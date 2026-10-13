"""Ajustes de allauth para policlase."""

from allauth.account.adapter import DefaultAccountAdapter

#: Por su ruta y no importada: allauth carga las etapas dentro de su flujo de inicio de sesión, y
#: importar allauth.account.stages al arrancar produce un ciclo de importación.
ROLE_EMAIL_VERIFICATION = "apps.accounts.stages.RoleEmailVerificationStage"


class AccountAdapter(DefaultAccountAdapter):
    def get_login_stages(self) -> list[str]:
        stock = "allauth.account.stages.EmailVerificationStage"
        return [ROLE_EMAIL_VERIFICATION if path == stock else path for path in super().get_login_stages()]
