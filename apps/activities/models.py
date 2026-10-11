import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from apps.courses.models import Course
from apps.grades.models import Category, Milestone


def course_tz(course) -> ZoneInfo:
    """Las horas de entrega («23:59») se entienden en la zona del docente del curso."""
    name = getattr(course.owner, "timezone", "") or settings.TIME_ZONE
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError):
        return ZoneInfo(settings.TIME_ZONE)


class Activity(models.Model):
    """Un deber, taller o evidencia: la fuente YAML (contenido) más lo que decide la plataforma
    (cuándo abre y vence, a qué fila de la ponderación pertenece, qué pasa con lo atrasado).

    El vencimiento es fijo (`due_at`) o relativo a una fecha clave (`due_milestone` +
    `due_offset_days` a las `due_time`); en ese caso `due_at` se recalcula cada vez que la fecha
    clave cambia, así que siempre se puede consultar directamente.
    """

    class Late(models.TextChoices):
        PENALTY = "penalty", _("Se aceptan con penalización")
        BLOCK = "block", _("No se aceptan")

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="activities")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name="activities", verbose_name=_("ponderación"))
    title = models.CharField(_("título"), max_length=200)
    source = models.TextField("fuente YAML")
    compiled = models.JSONField(default=dict)
    github_path = models.CharField(max_length=400, blank=True)
    github_sha = models.CharField(max_length=40, blank=True)

    #: Un borrador no lo ve ningún estudiante. Lo que llega de GitHub nace como borrador: falta
    #: decidir sus fechas y su ponderación.
    published = models.BooleanField(_("publicada"), default=False)
    opens_at = models.DateTimeField(_("disponible desde"), null=True, blank=True,
                                    help_text=_("Vacío: apenas se publica."))
    due_milestone = models.ForeignKey(Milestone, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="activities", verbose_name=_("fecha clave"))
    due_offset_days = models.SmallIntegerField(_("días respecto a la fecha clave"), default=-1)
    due_time = models.TimeField(_("hora de entrega"), default=time(23, 59))
    due_at = models.DateTimeField(_("fecha de entrega"), null=True, blank=True)
    late_policy = models.CharField(_("entregas atrasadas"), max_length=8, choices=Late.choices,
                                   default=Late.PENALTY)
    late_penalty = models.PositiveSmallIntegerField(_("penalización (%)"), default=30)
    max_attempts = models.PositiveSmallIntegerField(_("intentos"), default=3)
    #: Impide pegar en las respuestas abiertas: obliga a escribir, nada más. No se ofrece en las
    #: numéricas: los estudiantes calculan en Python o Excel y pegan el número.
    block_paste = models.BooleanField(_("bloquear pegar en respuestas abiertas"), default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [models.F("due_at").asc(nulls_last=True), "title"]
        verbose_name = _("actividad")

    def __str__(self) -> str:
        return self.title

    @property
    def points(self) -> float:
        return float(self.compiled.get("points") or 0)

    @property
    def questions(self) -> list[dict]:
        return [q for item in self.compiled.get("items", []) for q in item["questions"]]

    @property
    def needs_review(self) -> bool:
        return bool(self.compiled.get("review"))

    @property
    def uses_ai(self) -> bool:
        return bool(self.compiled.get("ai"))

    @property
    def only_images(self) -> bool:
        questions = self.questions
        return bool(questions) and all(q["type"] == "image" for q in questions)

    @property
    def due_rule(self) -> str:
        """«1 día antes de Prueba 1», «el mismo día de Examen», «2 días después de…»."""
        if not self.due_milestone_id:
            return ""
        name, n = self.due_milestone.name, self.due_offset_days
        if n == 0:
            return _("el mismo día de «%(name)s»") % {"name": name}
        if n < 0:
            return ngettext("%(n)d día antes de «%(name)s»", "%(n)d días antes de «%(name)s»", -n) % {"n": -n, "name": name}
        return ngettext("%(n)d día después de «%(name)s»", "%(n)d días después de «%(name)s»", n) % {"n": n, "name": name}

    def compute_due(self) -> None:
        """Recalcula `due_at` desde la fecha clave (si la tiene). No guarda."""
        if self.due_milestone_id:
            day = self.due_milestone.on + timedelta(days=self.due_offset_days)
            self.due_at = datetime.combine(day, self.due_time, tzinfo=course_tz(self.course))

    @property
    def late_until(self):
        """Hasta cuándo se acepta lo atrasado: el cierre del parcial (sin cierre, sin límite)."""
        if self.late_policy == self.Late.BLOCK:
            return self.due_at
        period = self.category.period if self.category_id else None
        if period and period.ends_on:
            return datetime.combine(period.ends_on, time(23, 59, 59), tzinfo=course_tz(self.course))
        return None

    def is_visible(self, now=None) -> bool:
        now = now or timezone.now()
        return self.published and (self.opens_at is None or self.opens_at <= now)

    def is_past_due(self, now=None) -> bool:
        return bool(self.due_at) and (now or timezone.now()) > self.due_at


def new_token() -> str:
    return uuid.uuid4().hex


class Attempt(models.Model):
    """Un intento de un estudiante: borrador mientras `submitted_at` está vacío.

    `content` es una copia de la actividad compilada al empezar (capa 2): corregir la actividad
    después no cambia lo que este estudiante vio ni cómo se le calificó. Cuenta el último intento
    enviado.
    """

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    number = models.PositiveSmallIntegerField()
    content = models.JSONField()
    #: Respuestas del borrador, {clave de pregunta: valor}. Al enviar se copian a `Answer`.
    answers = models.JSONField(default=dict)
    started_at = models.DateTimeField(auto_now_add=True)
    saved_at = models.DateTimeField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    late = models.BooleanField(default=False)
    #: Porcentaje que se descontó por atraso.
    penalty = models.PositiveSmallIntegerField(default=0)
    points = models.FloatField(null=True, blank=True)
    max_points = models.FloatField(default=0)
    #: Preguntas que esperan la nota del docente (abiertas, imágenes).
    pending = models.PositiveSmallIntegerField(default=0)
    #: Señales para el docente, no castigos: veces que pegó texto y cuánto, veces que salió de
    #: la página. El estudiante sabe que se registran.
    telemetry = models.JSONField(default=dict)

    class Meta:
        ordering = ["activity", "student", "number"]
        constraints = [
            models.UniqueConstraint(fields=["activity", "student", "number"], name="one_attempt_number"),
            # Un solo borrador abierto por estudiante y actividad.
            models.UniqueConstraint(fields=["activity", "student"], condition=models.Q(submitted_at=None),
                                    name="one_open_draft"),
        ]

    @property
    def fraction(self) -> float | None:
        if self.points is None:
            return None
        return self.points / self.max_points if self.max_points else 0.0

    @property
    def questions(self) -> list[dict]:
        return [q for item in self.content.get("items", []) for q in item["questions"]]

    def recompute(self) -> None:
        """Nota del intento: suma de sus respuestas menos la penalización por atraso. Guarda."""
        rows = list(self.answer_rows.all())
        raw = sum(a.points for a in rows)
        self.points = round(raw * (100 - self.penalty) / 100, 4)
        self.pending = sum(1 for a in rows if a.needs_review)
        self.save(update_fields=["points", "pending"])


class Answer(models.Model):
    """Una respuesta calificada (capa 3): una fila por pregunta de un intento enviado."""

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="answer_rows")
    key = models.CharField(max_length=250)
    item_id = models.CharField(max_length=120)
    item_version = models.CharField(max_length=32)
    value = models.JSONField(null=True)
    fraction = models.FloatField(default=0)
    points = models.FloatField(default=0)
    correct = models.BooleanField(default=False)
    #: Lo que dijo el calificador automático; la nota del docente no lo borra.
    auto_points = models.FloatField(null=True, blank=True)
    auto_correct = models.BooleanField(null=True, blank=True)
    needs_review = models.BooleanField(default=False)
    comment = models.TextField(blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                  blank=True, related_name="+")
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["attempt", "key"], name="one_answer_per_key")]


def upload_to(instance, filename) -> str:
    attempt = instance.attempt
    return f"entregas/{attempt.activity.course_id}/{attempt.activity_id}/{attempt.student_id}/{new_token()}.jpg"


class Upload(models.Model):
    """Una imagen entregada. Se guarda re-codificada (sin metadatos: las fotos del teléfono
    traen la ubicación GPS) y solo se sirve por `activities.views.upload_file`, con permisos."""

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="uploads")
    key = models.CharField(max_length=250)
    file = models.FileField(upload_to=upload_to, max_length=300)
    original_name = models.CharField(max_length=200, blank=True)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


@models.signals.post_delete.connect
def _delete_upload_file(sender, instance, **kwargs):
    """Borrar la fila borra la imagen del disco (también en cascada, al eliminar un intento)."""
    if sender is Upload and instance.file:
        instance.file.delete(save=False)
