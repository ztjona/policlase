"""Vista de solo lectura: otra persona consulta la cuenta de un estudiante sin poder cambiar nada.

El estudiante crea el enlace (`ReadOnlyLink`) y lo revoca cuando quiera. Quien lo abre no inicia
sesión como el estudiante: su sesión guarda solo el token, y en cada petición se comprueba de nuevo
que el enlace exista y esté vigente, de modo que revocarlo corta el acceso de inmediato.

Tres barreras independientes impiden que algo se modifique:

1. Solo se abren las páginas de `ALLOWED`: los cursos, las actividades con su revisión, el repaso de
   las clases terminadas y las calificaciones. La cuenta, las clases en vivo, los intentos en curso y la administración responden
   con un aviso. Es una lista de lo permitido y no de lo prohibido: una página nueva queda fuera hasta
   que se decida incluirla.
2. Solo se aceptan GET y HEAD, salvo para salir de la vista y para cambiar el idioma o el tema, que en
   esta vista se guardan solo en el navegador de quien mira (`WRITABLE`).
3. Cada vista corre dentro de una transacción que se deshace al terminar: lo que una página escribe
   al consultarse (el envío automático de un borrador vencido, las posturas del debate) no se guarda
   y queda para cuando entre el estudiante o su docente. El resultado es el mismo, porque esas
   escrituras no dependen de quién ni de cuándo se consulte.

Además, quien mira no ve la cédula del estudiante (marca de agua) ni los nombres de sus compañeros
(debate, `debate.anonymize`), que no dieron su consentimiento.
"""

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone

from .models import ReadOnlyLink, User

#: Clave de la sesión de quien mira: el token del enlace, nunca la identidad del estudiante.
SESSION_KEY = "policlase_readonly"

#: Las páginas que se pueden abrir en la vista de solo lectura (nombres de URL).
ALLOWED = frozenset({
    "home", "course_detail", "activity_student", "attempt_group_wait", "course_classes", "class_review", "my_grades",
    "upload_file",
    "about", "favicon", "javascript-catalog", "set_language", "set_theme",
    "readonly_open", "readonly_exit",
})
#: Las únicas que aceptan POST: ninguna toca la cuenta del estudiante.
WRITABLE = frozenset({"set_language", "set_theme", "readonly_exit"})
SAFE_METHODS = ("GET", "HEAD")

TEMPLATE = "account/readonly.html"


def find(token: str) -> ReadOnlyLink | None:
    """El enlace vigente de ese token, si lo hay (y si su dueño sigue siendo un estudiante activo)."""
    return (ReadOnlyLink.objects.select_related("student")
            .filter(token=token, expires_at__gt=timezone.now(),
                    student__is_active=True, student__role=User.Role.STUDENT)
            .first())


def viewing(request) -> ReadOnlyLink | None:
    """El enlace con el que se está mirando, o None fuera de la vista de solo lectura."""
    return getattr(request, "readonly_view", None)


def _enter(request) -> None:
    """Con un enlace vigente en la sesión, la petición se atiende como el estudiante, en solo lectura."""
    token = request.session.get(SESSION_KEY)
    if token is None:
        return
    if request.user.is_authenticated:
        # Quien inició sesión con su propia cuenta ya no está mirando la de otro.
        request.session.pop(SESSION_KEY, None)
        return
    link = find(token)
    if link is None:                                   # venció o el estudiante lo revocó
        request.session.pop(SESSION_KEY, None)
        request.readonly_ended = True
        return
    student = link.student

    async def auser():
        return student

    request.user, request.auser, request.readonly_view = student, auser, link


class ReadOnlyViewMiddleware:
    """Va después de `AuthenticationMiddleware` y antes de las preferencias, que en esta vista son
    las del navegador de quien mira (`apps.accounts.middleware`)."""

    sync_capable = async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(self, request):
        if iscoroutinefunction(self):
            return self.__acall__(request)
        _enter(request)
        return self.get_response(request)

    async def __acall__(self, request):
        # Casi nunca hay enlace en la sesión: entonces no se pasa por un hilo (las clases en vivo).
        if await request.session.aget(SESSION_KEY) is not None:
            await sync_to_async(_enter)(request)
        return await self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        name = request.resolver_match.url_name
        if getattr(request, "readonly_ended", False) and name != "readonly_open":
            # Se avisa que la vista terminó, salvo a quien abre un enlace nuevo.
            return redirect("readonly_ended")
        if viewing(request) is None:
            return None
        allowed = name in ALLOWED and (request.method in SAFE_METHODS or name in WRITABLE)
        # Una vista asíncrona no correría dentro de la transacción que se deshace: no se abre.
        if not allowed or iscoroutinefunction(view_func):
            return render(request, TEMPLATE, {"state": "blocked"}, status=403)
        with transaction.atomic():
            response = view_func(request, *view_args, **view_kwargs)
            if callable(getattr(response, "render", None)):
                response = response.render()           # la plantilla también se lee aquí dentro
            transaction.set_rollback(True)
        return response
