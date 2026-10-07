from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from django.views.i18n import JavaScriptCatalog

from apps.accounts.views import preferences, set_language, set_theme, teacher_signup

urlpatterns = [
    # Los navegadores piden /favicon.ico por su cuenta, aunque la página declare otro ícono.
    path("favicon.ico", RedirectView.as_view(url=settings.STATIC_URL + "img/favicon.ico", permanent=False)),
    path("cuenta/registro-docente/", teacher_signup, name="teacher_signup"),
    path("cuenta/preferencias/", preferences, name="account_preferences"),
    path("idioma/", set_language, name="set_language"),
    path("tema/", set_theme, name="set_theme"),
    path("i18n/js/", JavaScriptCatalog.as_view(), name="javascript-catalog"),
    path("", include("apps.github.urls")),
    path("cuenta/", include("allauth.urls")),
    path("admin/", admin.site.urls),
    path("", include("apps.courses.urls")),
    path("", include("apps.live.urls")),
]
