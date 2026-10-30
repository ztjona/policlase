from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from django.views.i18n import JavaScriptCatalog

from apps.accounts.views import (
    preferences,
    set_language,
    set_theme,
    student_signup,
    teacher_invitation_revoke,
    teacher_invitations,
    teacher_signup,
    teacher_signup_closed,
)
from apps.core.views import about

urlpatterns = [
    # Los navegadores piden /favicon.ico por su cuenta, aunque la página declare otro ícono.
    path("favicon.ico", RedirectView.as_view(url=settings.STATIC_URL + "img/favicon.ico", permanent=False)),
    # Antes que allauth.urls: esta ruta reemplaza su registro abierto.
    path("cuenta/signup/", student_signup, name="account_signup"),
    path("cuenta/registro-docente/", teacher_signup_closed, name="teacher_signup_closed"),
    path("cuenta/registro-docente/<str:token>/", teacher_signup, name="teacher_signup"),
    path("cuenta/invitaciones/", teacher_invitations, name="teacher_invitations"),
    path("cuenta/invitaciones/<int:pk>/revocar/", teacher_invitation_revoke, name="teacher_invitation_revoke"),
    path("cuenta/preferencias/", preferences, name="account_preferences"),
    path("idioma/", set_language, name="set_language"),
    path("tema/", set_theme, name="set_theme"),
    path("acerca/", about, name="about"),
    path("i18n/js/", JavaScriptCatalog.as_view(), name="javascript-catalog"),
    path("", include("apps.github.urls")),
    path("", include("apps.ai.urls")),
    path("cuenta/", include("allauth.urls")),
    path("admin/", admin.site.urls),
    path("", include("apps.courses.urls")),
    path("", include("apps.live.urls")),
    path("", include("apps.grades.urls")),
    path("", include("apps.activities.urls")),
]
