"""Idioma y zona horaria de cada usuario; confinamiento de los invitados a su clase.

`LocaleMiddleware` ya eligió un idioma con la cookie o el navegador; aquí manda la preferencia
guardada del usuario. La zona horaria la detecta el navegador (base.html la deja en una cookie):
la primera vez que llega con sesión iniciada se guarda en la cuenta, y desde entonces solo cambia
en Preferencias.
"""

from functools import cache
from zoneinfo import ZoneInfo, available_timezones

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.shortcuts import redirect
from django.utils import timezone, translation

#: Lo único que ve un invitado: la clase en vivo y lo necesario para mostrarla.
GUEST_PATHS = ("/en-vivo/", "/static/", "/i18n/", "/idioma/", "/tema/", "/cuenta/logout/")


@cache
def timezone_names() -> frozenset[str]:
    return frozenset(available_timezones())  # recorre el disco: una sola vez por proceso


def valid_timezone(name: str | None) -> str:
    return name if name and name in timezone_names() else ""


def _apply(request, user) -> bool:
    """Activa idioma y zona; devuelve True si hay que guardar la zona detectada en la cuenta."""
    detected = valid_timezone(request.COOKIES.get(settings.TIMEZONE_COOKIE))
    save = False
    tz_name = detected
    if user.is_authenticated:
        if user.language:
            translation.activate(user.language)
            request.LANGUAGE_CODE = user.language
        if user.timezone:
            tz_name = user.timezone
        elif detected:
            user.timezone, save = detected, True
    if tz_name:
        timezone.activate(ZoneInfo(tz_name))
    else:
        timezone.deactivate()
    return save


def _guest_redirect(request, user):
    """Un invitado fuera de la clase vuelve a su clase (o sale, si ya no tiene ninguna)."""
    if not (user.is_authenticated and user.is_guest) or request.path.startswith(GUEST_PATHS):
        return None
    from apps.live.models import Participant

    last = Participant.objects.filter(student=user).order_by("-joined_at").first()
    return redirect("live_student", pk=last.session_id) if last else redirect("account_logout")


class PreferencesMiddleware:
    sync_capable = async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(self, request):
        if iscoroutinefunction(self):
            return self.__acall__(request)
        if _apply(request, request.user):
            request.user.save(update_fields=["timezone"])
        return _guest_redirect(request, request.user) or self.get_response(request)

    async def __acall__(self, request):
        user = await request.auser()
        if _apply(request, user):
            await sync_to_async(user.save)(update_fields=["timezone"])
        if user.is_authenticated and user.is_guest:
            response = await sync_to_async(_guest_redirect)(request, user)
            if response:
                return response
        return await self.get_response(request)
