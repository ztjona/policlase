import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.courses.models import Course


class Deck(models.Model):
    """Una presentación: la fuente YAML tal como la escribió el docente y su forma compilada."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="decks")
    title = models.CharField(_("título"), max_length=200)
    source = models.TextField("fuente YAML")
    compiled = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title

    @property
    def slides(self) -> list:
        return self.compiled.get("slides", [])

    @property
    def question_count(self) -> int:
        return sum(1 for s in self.slides if s["kind"] == "question")


class LiveSessionQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status=LiveSession.Status.ENDED)


def new_pin() -> str:
    return f"{secrets.randbelow(900000) + 100000}"


class LiveSession(models.Model):
    """Una clase en vivo en curso o terminada.

    Las diapositivas se copian del `Deck` al iniciar: editar la presentación después no altera
    lo que se vio ni cómo se calificó en una clase ya dictada.

    `state_version` sube con cada cambio que los estudiantes deben ver (avanzar, abrir,
    cerrar, mostrar resultados); `answers_version` sube con cada llegada a la sala y cada
    respuesta, y solo le interesa a la vista del docente. Separarlas evita que 30 respuestas
    provoquen 30 × 30 recargas en los teléfonos.
    """

    class Status(models.TextChoices):
        LOBBY = "lobby", _("Sala de espera")
        LIVE = "live", _("En curso")
        ENDED = "ended", _("Terminada")

    class Phase(models.TextChoices):
        CONTENT = "content", _("Contenido")
        READY = "ready", _("Pregunta lista")
        OPEN = "open", _("Respondiendo")
        CLOSED = "closed", _("Cerrada")
        RESULTS = "results", _("Resultados")

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="live_sessions")
    deck = models.ForeignKey(Deck, on_delete=models.SET_NULL, null=True, related_name="sessions")
    title = models.CharField(max_length=200)
    slides = models.JSONField()
    pin = models.CharField(max_length=6, default=new_pin)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.LOBBY)
    index = models.PositiveIntegerField(default=0)
    phase = models.CharField(max_length=10, choices=Phase.choices, default=Phase.CONTENT)
    #: Índices de las preguntas que ya se abrieron; volver a una muestra sus resultados.
    opened = models.JSONField(default=list)
    opened_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)

    state_version = models.PositiveIntegerField(default=1)
    answers_version = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    objects = LiveSessionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # El PIN solo necesita ser único entre las sesiones vivas.
            models.UniqueConstraint(fields=["pin"], condition=~Q(status="ended"),
                                    name="unique_pin_among_active"),
            models.UniqueConstraint(fields=["course"], condition=~Q(status="ended"),
                                    name="one_active_session_per_course"),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_status_display()})"

    @property
    def slide(self) -> dict | None:
        if 0 <= self.index < len(self.slides):
            return self.slides[self.index]
        return None

    @property
    def is_question(self) -> bool:
        return bool(self.slide) and self.slide["kind"] == "question"

    @property
    def question_indices(self) -> list[int]:
        return [i for i, s in enumerate(self.slides) if s["kind"] == "question"]

    @property
    def max_points(self) -> float:
        return sum(s["points"] for s in self.slides if s["kind"] == "question")


class Participant(models.Model):
    session = models.ForeignKey(LiveSession, on_delete=models.CASCADE, related_name="participants")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    joined_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "student"], name="one_participant_row"),
        ]


class Response(models.Model):
    """Una respuesta a una pregunta en vivo: capa 3 del modelo de ítems.

    Se guarda la respuesta cruda, el resultado del calificador y el ítem de origen, para que
    la analítica de ítems cuente también las preguntas de clase.
    """

    session = models.ForeignKey(LiveSession, on_delete=models.CASCADE, related_name="responses")
    slide_index = models.PositiveIntegerField()
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    item_id = models.CharField(max_length=120)
    item_version = models.CharField(max_length=32)
    answer = models.JSONField()
    correct = models.BooleanField()
    fraction = models.FloatField()
    points = models.FloatField()
    elapsed_ms = models.PositiveIntegerField()
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Una respuesta por pregunta y estudiante: la primera es la que cuenta.
            models.UniqueConstraint(fields=["session", "slide_index", "student"],
                                    name="one_answer_per_question"),
        ]
