"""La configuración de la IA del docente y las propuestas de calificación (ver `grading.py`)."""

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from apps.activities import engine
from apps.activities.models import Answer
from apps.activities.views import _own_activity
from apps.courses.access import teacher_required

from . import deepseek, grading
from .models import AiAccount

#: Sin JavaScript, cada envío del formulario procesa unas pocas respuestas (cada una tarda segundos).
NOJS_BATCH = 3


def account_for(user) -> AiAccount | None:
    return AiAccount.objects.filter(user=user).first()


@teacher_required
def ai_account(request):
    """La clave de DeepSeek, el modelo y el interruptor de la integración."""
    account = account_for(request.user) or AiAccount(user=request.user)
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "remove":
            account.key, account.enabled = "", False
            account.save()
            messages.success(request, _("Se quitó la clave de DeepSeek y se deshabilitó la integración."))
            return redirect("ai_account")
        key = request.POST.get("key", "").strip()
        if key:
            account.key = key
        account.enabled = request.POST.get("enabled") == "on"
        if request.POST.get("model") in AiAccount.Model.values:
            account.model = request.POST["model"]
        if account.enabled and not account.key_encrypted:
            account.enabled = False
            messages.error(request, _("Para habilitar la integración, ingrese primero su clave de DeepSeek."))
        account.save()
        if action == "test":
            try:
                grading.test_connection(account)
            except deepseek.AiError as exc:
                messages.error(request, _("La prueba falló: %(error)s") % {"error": exc})
            else:
                messages.success(request, _("La conexión con DeepSeek funciona."))
        else:
            messages.success(request, _("Se guardó la configuración de la IA."))
        return redirect("ai_account")
    return render(request, "ai/account.html", {"account": account, "models": AiAccount.Model.choices})


def _ready_account(request) -> AiAccount:
    account = account_for(request.user)
    if not account or not account.ready:
        raise deepseek.AiError(_("La calificación asistida por IA no está habilitada. Configúrela en «Inteligencia artificial»."))
    return account


def pending_answers(activity, keys, regenerate=False) -> list[Answer]:
    """Las respuestas abiertas que esperan propuesta: del intento que cuenta, no calificadas por el
    docente, no en blanco y, salvo que se pida regenerar, sin propuesta todavía."""
    open_keys = {q["key"] for q in activity.questions if q["type"] == "open"} & set(keys)
    counted = engine.counted_attempts(activity)
    rows = [a for a in Answer.objects.filter(attempt__in=counted.values(), key__in=open_keys, graded_by__isnull=True)
            .order_by("key", "pk") if not engine.blank(a.value)]
    if not regenerate:
        done = grading.latest(rows)
        rows = [a for a in rows if a.pk not in done]
    return rows


@teacher_required
@require_POST
def ai_pending(request, course_pk, pk):
    """Con JavaScript: qué respuestas procesar (la página las pide una a una y muestra el avance).
    Sin JavaScript: procesa unas pocas y vuelve a la actividad."""
    course, activity = _own_activity(request, course_pk, pk)
    keys = request.POST.getlist("key")
    rows = pending_answers(activity, keys, regenerate=request.POST.get("regenerate") == "1")
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"answers": [a.pk for a in rows]})
    try:
        account = _ready_account(request)
    except deepseek.AiError as exc:
        messages.error(request, str(exc))
        return redirect(reverse("activity_detail", args=[course.pk, activity.pk]) + "#ai")
    done = [grading.propose(a, account, request.user) for a in rows[:NOJS_BATCH]]
    ok = sum(1 for p in done if not p.error)
    messages.info(request, ngettext("Se generó %(n)d propuesta.", "Se generaron %(n)d propuestas.", ok) % {"n": ok})
    if len(rows) > NOJS_BATCH:
        messages.info(request, _("Quedan %(n)d por generar. Pulse de nuevo el botón para continuar.") % {"n": len(rows) - NOJS_BATCH})
    return redirect(reverse("activity_detail", args=[course.pk, activity.pk]) + "#ai")


@teacher_required
@require_POST
def ai_propose(request, course_pk, pk, answer_pk):
    """La propuesta para una respuesta. Con JavaScript devuelve lo que llena el formulario de nota."""
    course, activity = _own_activity(request, course_pk, pk)
    answer = get_object_or_404(Answer.objects.select_related("attempt__activity__course", "attempt__activity__section"),
                               pk=answer_pk, attempt__activity=activity)
    item, question, _record = engine._records(answer.attempt.content)[answer.key]
    if question["type"] != "open":
        raise Http404
    fetched = request.headers.get("X-Requested-With") == "fetch"
    try:
        proposal = grading.propose(answer, _ready_account(request), request.user)
        if proposal.error:
            raise deepseek.AiError(proposal.error)
    except deepseek.AiError as exc:
        if fetched:
            return JsonResponse({"error": str(exc)}, status=409)
        messages.error(request, str(exc))
    else:
        if fetched:
            return JsonResponse({"points": proposal.points, "feedback": proposal.feedback,
                                 "criteria": proposal.criteria, "confidence": proposal.confidence})
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = reverse("question_review", args=[course.pk, activity.pk, answer.key])
    return redirect(f"{back}#r{answer.pk}")
