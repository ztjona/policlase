import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

#: Días que dura un enlace de invitación sin usar.
INVITATION_DAYS = 7


def new_invitation_token() -> str:
    return secrets.token_urlsafe(32)


def invitation_expiry():
    return timezone.now() + timedelta(days=INVITATION_DAYS)


class User(AbstractUser):
    """Una identidad por persona.

    Un estudiante matriculado con dos profesores tiene una sola cuenta; cada profesor ve solo
    sus propios cursos y matrículas. La separación entre espacios de trabajo es de autorización,
    no de identidad.
    """

    class Role(models.TextChoices):
        STUDENT = "student", _("Estudiante")
        TEACHER = "teacher", _("Docente")
        #: Asistente sin registro a una clase en vivo abierta (apps.live.guests).
        GUEST = "guest", _("Invitado")

    class Theme(models.TextChoices):
        AUTO = "", _("Automático (como el sistema)")
        LIGHT = "light", _("Claro")
        DARK = "dark", _("Oscuro")

    email = models.EmailField(_("correo electrónico"), unique=True)
    role = models.CharField(_("rol"), max_length=10, choices=Role.choices, default=Role.STUDENT)
    #: Cédula o pasaporte. Entra en la derivación de la semilla de cada estudiante y en la
    #: exportación a Moodle; no es un secreto, pero tampoco se muestra a otros estudiantes.
    student_id = models.CharField(_("cédula o pasaporte"), max_length=20, blank=True)
    #: Vacíos hasta que el usuario elige; mientras tanto mandan el navegador y la cookie.
    language = models.CharField(_("idioma"), max_length=8, blank=True, choices=settings.LANGUAGES)
    timezone = models.CharField(_("zona horaria"), max_length=64, blank=True)
    theme = models.CharField(_("tema"), max_length=8, blank=True, choices=Theme.choices, default=Theme.AUTO)

    REQUIRED_FIELDS = ["email"]

    @property
    def is_teacher(self) -> bool:
        return self.role == self.Role.TEACHER

    @property
    def is_guest(self) -> bool:
        return self.role == self.Role.GUEST

    @property
    def display_name(self) -> str:
        return self.get_full_name() or self.username

    def __str__(self) -> str:
        return self.display_name

    def save(self, *args, **kwargs):
        from .names import name_case

        # «CAIZA OÑA, DANNY JAVIER» se guarda como «Caiza Oña, Danny Javier», venga de donde venga.
        self.first_name, self.last_name = name_case(self.first_name), name_case(self.last_name)
        super().save(*args, **kwargs)


class TeacherInvitation(models.Model):
    """Enlace de un solo uso para crear una cuenta de docente.

    Lo genera un docente y lo entrega por su cuenta: la plataforma no envía correos a direcciones
    escritas en un formulario, para no servir de vía de envío a terceros. La invitación usada se
    conserva porque dice quién invitó a quién.
    """

    token = models.CharField(max_length=43, unique=True, default=new_invitation_token, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name="teacher_invitations")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=invitation_expiry)
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.created_by} · {self.created_at:%Y-%m-%d}"

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and timezone.now() < self.expires_at
