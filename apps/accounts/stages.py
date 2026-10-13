"""Etapas de inicio de sesión propias. allauth las importa por su ruta, ya dentro de su flujo de
inicio de sesión: este módulo no debe importarse antes (allauth.account.stages se importa con un
ciclo si se carga primero; ver AccountAdapter.get_login_stages)."""

from allauth.account.app_settings import EmailVerificationMethod
from allauth.account.stages import EmailVerificationStage


class RoleEmailVerificationStage(EmailVerificationStage):
    """Confirmar el correo es obligatorio para docentes y opcional para estudiantes.

    El docente aprueba cada inscripción por nombre y cédula, así que el correo del estudiante no
    protege el acceso a nada; solo sirve para recuperar la contraseña. Y algunos servidores
    institucionales (Microsoft 365) retienen el correo de confirmación sin avisarle a nadie: con la
    confirmación obligatoria, el estudiante quedaba fuera. El registro de docentes es abierto: ahí el
    correo confirmado sí importa.
    """

    def handle(self):
        if getattr(self.login.user, "is_teacher", False):
            self.login.email_verification = EmailVerificationMethod.MANDATORY
        return super().handle()
