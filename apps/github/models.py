import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.courses.models import Course

from . import crypto


class GitHubAccount(models.Model):
    """El token de GitHub de un docente (cifrado). Uno por docente, para todos sus cursos."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="github")
    token_encrypted = models.TextField()
    #: Usuario de GitHub dueño del token, verificado al guardarlo.
    login = models.CharField(max_length=100)
    connected_at = models.DateTimeField(auto_now=True)

    @property
    def token(self) -> str:
        return crypto.decrypt(self.token_encrypted)

    @token.setter
    def token(self, value: str) -> None:
        self.token_encrypted = crypto.encrypt(value)


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(24)


class CourseRepo(models.Model):
    """Carpeta de un repositorio de GitHub con las presentaciones de un curso.

    GitHub es la fuente de verdad: lo que se empuja allá se trae aquí, y lo que se guarda en el
    editor web se confirma allá antes de guardarse aquí.
    """

    course = models.OneToOneField(Course, on_delete=models.CASCADE, related_name="github_repo")
    repo = models.CharField(_("repositorio"), max_length=200, help_text=_("usuario/repositorio"))
    branch = models.CharField(_("rama"), max_length=100, default="main")
    folder = models.CharField(_("carpeta"), max_length=300, blank=True,
                              help_text=_("p. ej. metodos-numericos/clases; vacío = raíz del repositorio"))
    webhook_secret = models.CharField(max_length=64, default=new_webhook_secret)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    #: Archivos con errores en la última sincronización: [{path, diagnostics: [...]}].
    issues = models.JSONField(default=list, blank=True)
    #: {ruta: sha} de los YAML que no son presentaciones o tienen errores: no se descargan de
    #: nuevo hasta que cambien.
    skipped = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"{self.repo}@{self.branch}/{self.folder}"

    @property
    def prefix(self) -> str:
        return f"{self.folder.strip('/')}/" if self.folder.strip("/") else ""
