import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

#: Sin 0/O ni 1/I/L: el código se dicta en voz alta o se copia de la pizarra.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def new_join_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(raw: str) -> str:
    return "".join(ch for ch in (raw or "").upper() if ch in CODE_ALPHABET)


def new_seed_secret() -> str:
    return secrets.token_hex(32)


class Course(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name="owned_courses", verbose_name=_("docente"))
    name = models.CharField(_("nombre"), max_length=200)
    code = models.CharField(_("código"), max_length=40, help_text=_("p. ej. MN-2026-2"))
    description = models.TextField(_("descripción"), blank=True)

    join_code = models.CharField(_("código de inscripción"), max_length=CODE_LENGTH,
                                 unique=True, default=new_join_code)
    accepting = models.BooleanField(_("acepta solicitudes"), default=True)
    #: Secreto para derivar la semilla de cada estudiante (policlase-gen `assign_seed`).
    seed_secret = models.CharField(max_length=64, default=new_seed_secret, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "code"], name="unique_course_code_per_teacher"),
        ]

    def __str__(self) -> str:
        return f"{self.code} · {self.name}"

    @property
    def join_code_display(self) -> str:
        return f"{self.join_code[:4]}-{self.join_code[4:]}"

    def regenerate_join_code(self) -> None:
        self.join_code = new_join_code()
        self.save(update_fields=["join_code"])


class Enrollment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pendiente")
        APPROVED = "approved", _("Aprobada")
        REJECTED = "rejected", _("Rechazada")

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="enrollments")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="+")
    #: El estudiante cerró el aviso de rechazo; vuelve a False si el docente decide de nuevo.
    dismissed = models.BooleanField(default=False)

    class Meta:
        ordering = ["student__last_name", "student__first_name"]
        constraints = [
            models.UniqueConstraint(fields=["course", "student"], name="one_enrollment_per_course"),
        ]

    def decide(self, status: str, by) -> None:
        self.status = status
        self.decided_at = timezone.now()
        self.decided_by = by
        self.dismissed = False
        self.save(update_fields=["status", "decided_at", "decided_by", "dismissed"])
