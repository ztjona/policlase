from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """Una identidad por persona.

    Un estudiante matriculado con dos profesores tiene una sola cuenta; cada profesor ve solo
    sus propios cursos y matrículas. La separación entre espacios de trabajo es de autorización,
    no de identidad.
    """

    class Role(models.TextChoices):
        STUDENT = "student", _("Estudiante")
        TEACHER = "teacher", _("Docente")

    email = models.EmailField(_("correo electrónico"), unique=True)
    role = models.CharField(_("rol"), max_length=10, choices=Role.choices, default=Role.STUDENT)
    #: Cédula o pasaporte. Entra en la derivación de la semilla de cada estudiante y en la
    #: exportación a Moodle; no es un secreto, pero tampoco se muestra a otros estudiantes.
    student_id = models.CharField(_("cédula o pasaporte"), max_length=20, blank=True)
    #: Vacíos hasta que el usuario elige; mientras tanto mandan el navegador y la cookie.
    language = models.CharField(_("idioma"), max_length=8, blank=True, choices=settings.LANGUAGES)
    timezone = models.CharField(_("zona horaria"), max_length=64, blank=True)

    REQUIRED_FIELDS = ["email"]

    @property
    def is_teacher(self) -> bool:
        return self.role == self.Role.TEACHER

    @property
    def display_name(self) -> str:
        return self.get_full_name() or self.username

    def __str__(self) -> str:
        return self.display_name
