from allauth.account.views import SignupView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.courses.access import teacher_required
from apps.courses.models import Course

from .context_processors import THEMES
from .forms import PreferencesForm
from .models import INVITATION_DAYS, TeacherInvitation

CLOSED_TEMPLATE = "account/teacher_signup_closed.html"


class StudentSignupView(SignupView):
    """Registro de estudiantes: el formulario exige el código de un curso abierto
    (`ProfileSignupForm`). Aquí solo se aprovecha el del enlace de inscripción."""

    def get_initial(self):
        initial = super().get_initial()
        if code := self.request.GET.get("codigo"):
            initial["course_code"] = code
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if code := self.request.GET.get("codigo"):
            # Se avisa antes de que llene el formulario si el enlace ya no sirve.
            context["link_given"] = True
            context["link_course"] = Course.open_for_code(code)
            # Quien ya tiene cuenta inicia sesión y llega a inscribirse con el código completado.
            join = f"{reverse('course_join')}?{urlencode({'codigo': code})}"
            context["login_url"] = f"{reverse('account_login')}?{urlencode({'next': join})}"
        return context


student_signup = StudentSignupView.as_view()


class TeacherSignupView(SignupView):
    """Registro de docentes, solo con una invitación vigente: el mismo flujo de allauth (verificación
    de correo incluida), sin cédula ni código de curso. La separación entre docentes la hacen las
    reglas de `apps.courses.access`, no el registro.
    """

    def dispatch(self, request, *args, **kwargs):
        self.invitation = TeacherInvitation.objects.filter(token=kwargs["token"]).first()
        if self.invitation is None or not self.invitation.is_usable:
            return render(request, CLOSED_TEMPLATE, {"invalid": True}, status=404)
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.as_teacher = True
        form.fields.pop("student_id", None)
        form.fields.pop("course_code", None)
        return form

    def get_context_data(self, **kwargs):
        return super().get_context_data(teacher=True, invitation=self.invitation, **kwargs)

    def form_valid(self, form):
        # Con dos personas usando el mismo enlace a la vez, solo la primera crea la cuenta. La
        # invitación se gasta únicamente si se creó: un correo ya registrado o el campo trampa
        # responden como si nada, sin crear cuenta.
        with transaction.atomic():
            invitation = TeacherInvitation.objects.select_for_update().filter(pk=self.invitation.pk).first()
            if invitation is None or not invitation.is_usable:
                return render(self.request, CLOSED_TEMPLATE, {"invalid": True}, status=404)
            response = super().form_valid(form)
            if self.user is not None:
                invitation.used_by, invitation.used_at = self.user, timezone.now()
                invitation.save(update_fields=["used_by", "used_at"])
        return response


teacher_signup = TeacherSignupView.as_view()
teacher_signup_closed = TemplateView.as_view(template_name=CLOSED_TEMPLATE)


@teacher_required
def teacher_invitations(request):
    if request.method == "POST":
        TeacherInvitation.objects.create(created_by=request.user)
        messages.success(request, _("Enlace de invitación creado. Cópielo y entrégueselo a su colega."))
        return redirect("teacher_invitations")
    pending, used = [], []
    for invitation in request.user.teacher_invitations.select_related("used_by"):
        if invitation.used_at:
            used.append(invitation)
        elif invitation.is_usable:
            invitation.link = request.build_absolute_uri(reverse("teacher_signup", args=[invitation.token]))
            pending.append(invitation)
    return render(request, "account/invitations.html",
                  {"pending": pending, "used": used, "days": INVITATION_DAYS})


@teacher_required
@require_POST
def teacher_invitation_revoke(request, pk):
    # La de otro docente es un 404, y la usada no se revoca: ya cumplió su función.
    get_object_or_404(TeacherInvitation, pk=pk, created_by=request.user, used_at__isnull=True).delete()
    messages.success(request, _("El enlace de invitación dejó de funcionar."))
    return redirect("teacher_invitations")


def _cookie(response, name: str, value: str):
    """Las cookies mantienen idioma y tema también después de cerrar sesión."""
    response.set_cookie(name, value, max_age=settings.LANGUAGE_COOKIE_AGE,
                        samesite=settings.LANGUAGE_COOKIE_SAMESITE, secure=settings.LANGUAGE_COOKIE_SECURE)
    return response


def _remember_language(response, language: str):
    return _cookie(response, settings.LANGUAGE_COOKIE_NAME, language)


def _back(request):
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        target = "/"
    return redirect(target)


@require_POST
def set_language(request):
    """Cambio rápido de idioma (menú de usuario o barra sin sesión)."""
    language = request.POST.get("language", "")
    response = _back(request)
    if language not in dict(settings.LANGUAGES):
        return response
    if request.user.is_authenticated:
        request.user.language = language
        request.user.save(update_fields=["language"])
    return _remember_language(response, language)


@require_POST
def set_theme(request):
    """Cambio rápido de tema (p. ej. el botón ☀/☾ del proyector)."""
    theme = request.POST.get("theme", "")
    response = _back(request)
    if theme not in ("", *THEMES):
        return response
    if request.user.is_authenticated:
        request.user.theme = theme
        request.user.save(update_fields=["theme"])
    return _cookie(response, settings.THEME_COOKIE, theme)


@login_required
def preferences(request):
    user = request.user
    initial = {"language": user.language or translation.get_language(),
               "timezone": user.timezone or settings.TIME_ZONE, "theme": user.theme}
    form = PreferencesForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        user.language = form.cleaned_data["language"]
        user.timezone = form.cleaned_data["timezone"]
        user.theme = form.cleaned_data["theme"]
        user.save(update_fields=["language", "timezone", "theme"])
        translation.activate(user.language)
        messages.success(request, _("Preferencias guardadas."))
        response = _remember_language(redirect("account_preferences"), user.language)
        return _cookie(response, settings.THEME_COOKIE, user.theme)
    return render(request, "account/preferences.html", {"form": form})
