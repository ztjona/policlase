import hashlib
import hmac
import json

from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.courses.access import owned_course, teacher_required

from . import sync
from .client import GitHubClient, GitHubError
from .forms import CourseRepoForm, TokenForm
from .models import CourseRepo, GitHubAccount


@teacher_required
def account(request):
    current = GitHubAccount.objects.filter(user=request.user).first()
    form = TokenForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("action") == "disconnect":
            GitHubAccount.objects.filter(user=request.user).delete()
            messages.success(request, _("Cuenta de GitHub desconectada. El token se borró de policlase."))
            return redirect("github_account")
        if form.is_valid():
            token = form.cleaned_data["token"]
            try:
                login = GitHubClient(token).login()
            except GitHubError as exc:
                form.add_error("token", str(exc))
            else:
                account = current or GitHubAccount(user=request.user)
                account.token, account.login = token, login
                account.save()
                messages.success(request, _("Conectado a GitHub como @%(login)s.") % {"login": login})
                return redirect("github_account")
    return render(request, "github/account.html", {"current": current, "form": form})


def _summary(result: sync.PullResult) -> str:
    return _("Sincronizado: %(c)d nuevas, %(u)d actualizadas, %(d)d eliminadas.") % {
        "c": len(result.created), "u": len(result.updated), "d": len(result.deleted)}


@teacher_required
@require_POST
def course_link(request, pk):
    """Vincular, sincronizar o desvincular la carpeta de GitHub de un curso."""
    course = owned_course(request, pk)
    link = CourseRepo.objects.filter(course=course).first()
    action = request.POST.get("action")

    if action == "unlink" and link:
        link.delete()
        messages.success(request, _("Curso desvinculado de GitHub. Las presentaciones quedan en policlase."))
    elif action == "sync" and link:
        try:
            messages.success(request, _summary(sync.pull(link)))
        except GitHubError as exc:
            messages.error(request, str(exc))
    elif action == "link":
        form = CourseRepoForm(request.POST, instance=link or CourseRepo(course=course))
        if not form.is_valid():
            for errors in form.errors.values():
                for e in errors:
                    messages.error(request, e)
            return redirect("course_settings", pk=pk)
        candidate = form.save(commit=False)
        try:
            client = sync.client_for(request.user)
            client.check_repo(candidate.repo, candidate.branch)
        except GitHubError as exc:
            messages.error(request, str(exc))
            return redirect("course_settings", pk=pk)
        candidate.last_synced_at = None
        candidate.save()
        try:
            exported = sync.export_local_decks(candidate)
            result = sync.pull(candidate)
        except GitHubError as exc:
            messages.error(request, str(exc))
        else:
            text = _summary(result)
            if exported:
                text += " " + _("%(n)d presentaciones de policlase se subieron a GitHub.") % {"n": len(exported)}
            messages.success(request, text)
    return redirect("course_settings", pk=pk)


@csrf_exempt
@require_POST
def webhook(request, pk):
    """GitHub avisa de cada push; se sincroniza al instante. Firma HMAC con el secreto del curso."""
    link = get_object_or_404(CourseRepo, course_id=pk)
    expected = "sha256=" + hmac.new(link.webhook_secret.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
        return HttpResponseForbidden("firma inválida")
    if request.headers.get("X-GitHub-Event") == "push":
        try:
            ref = json.loads(request.body or b"{}").get("ref", "")
        except ValueError:
            ref = ""
        if ref == f"refs/heads/{link.branch}":
            try:
                sync.pull(link)
            except GitHubError as exc:
                return HttpResponse(str(exc), status=502)
    return HttpResponse(status=204)
