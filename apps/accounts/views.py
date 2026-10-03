from allauth.account.views import SignupView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from .forms import PreferencesForm


class TeacherSignupView(SignupView):
    """Registro de docentes: el mismo flujo de allauth (verificación de correo incluida), sin
    cédula. Cualquiera puede abrir su espacio de docente; la separación entre docentes la hacen
    las reglas de `apps.courses.access`, no el registro.
    """

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.as_teacher = True
        form.fields.pop("student_id", None)
        return form

    def get_context_data(self, **kwargs):
        return super().get_context_data(teacher=True, **kwargs)


teacher_signup = TeacherSignupView.as_view()


def _remember_language(response, language: str):
    """La cookie mantiene el idioma también después de cerrar sesión."""
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME, language, max_age=settings.LANGUAGE_COOKIE_AGE,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE, secure=settings.LANGUAGE_COOKIE_SECURE,
    )
    return response


@require_POST
def set_language(request):
    """Cambio rápido de idioma (menú de usuario o barra sin sesión)."""
    language = request.POST.get("language", "")
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        target = "/"
    response = redirect(target)
    if language not in dict(settings.LANGUAGES):
        return response
    if request.user.is_authenticated:
        request.user.language = language
        request.user.save(update_fields=["language"])
    return _remember_language(response, language)


@login_required
def preferences(request):
    user = request.user
    initial = {"language": user.language or translation.get_language(),
               "timezone": user.timezone or settings.TIME_ZONE}
    form = PreferencesForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        user.language = form.cleaned_data["language"]
        user.timezone = form.cleaned_data["timezone"]
        user.save(update_fields=["language", "timezone"])
        translation.activate(user.language)
        messages.success(request, _("Preferencias guardadas."))
        return _remember_language(redirect("account_preferences"), user.language)
    return render(request, "account/preferences.html", {"form": form})
