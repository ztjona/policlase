import secrets

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.courses.models import Course


class Section(models.Model):
    """Una unidad dentro de «Clases» (Unidad 1, Unidad 2…) que agrupa presentaciones.

    En un curso vinculado a GitHub, cada sección es una carpeta (`github_folder`) dentro de la
    carpeta del curso: crear, renombrar o mover en la web hace el commit correspondiente.
    """

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField(_("título"), max_length=200)
    position = models.PositiveIntegerField(default=0)
    #: Minimizada en la vista del docente (preferencia de interfaz, no va a GitHub).
    collapsed = models.BooleanField(default=False)
    github_folder = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return self.title


class Deck(models.Model):
    """Una presentación: la fuente YAML tal como la escribió el docente y su forma compilada."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="decks")
    section = models.ForeignKey(Section, on_delete=models.SET_NULL, null=True, blank=True, related_name="decks")
    title = models.CharField(_("título"), max_length=200)
    #: Archivo de origen en GitHub y su blob sha, si el curso está vinculado (apps.github).
    github_path = models.CharField(max_length=400, blank=True)
    github_sha = models.CharField(max_length=40, blank=True)
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
    def visible_slides(self) -> list[dict]:
        """Lo que se presentará: sin ocultas ni la retroalimentación final."""
        return [s for s in self.slides if not s.get("hidden") and s["kind"] != "feedback"]

    @property
    def question_count(self) -> int:
        return sum(1 for s in self.visible_slides if s["kind"] == "question")

    @property
    def has_feedback(self) -> bool:
        return any(s["kind"] == "feedback" and not s.get("hidden") for s in self.slides)


class LiveSessionQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(status=LiveSession.Status.ENDED)


def new_pin() -> str:
    return f"{secrets.randbelow(900000) + 100000}"


def new_guest_token() -> str:
    return secrets.token_urlsafe(12)


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
        #: La clase se acabó a mitad de la presentación: se retoma otro día donde quedó.
        PAUSED = "paused", _("En pausa")
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
    #: Clase abierta: cualquiera con el enlace de invitado entra sin cuenta (charlas, visitas).
    #: El PIN sigue siendo solo para estudiantes aprobados: el enlace es largo e inadivinable.
    allow_guests = models.BooleanField(default=False)
    #: Bono por rapidez en el marcador (no en la nota). Lo trae la presentación (`speed_bonus`)
    #: y el docente puede cambiarlo en la sala de espera.
    speed_bonus = models.BooleanField(default=True)
    guest_token = models.CharField(max_length=24, unique=True, default=new_guest_token)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.LOBBY)
    index = models.PositiveIntegerField(default=0)
    phase = models.CharField(max_length=10, choices=Phase.choices, default=Phase.CONTENT)
    #: Índices de las preguntas que ya se abrieron; volver a una muestra sus resultados.
    opened = models.JSONField(default=list)
    opened_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    #: Tiempo que le quedaba a la pregunta abierta al pausar; se devuelve al reanudar.
    paused_remaining_ms = models.PositiveIntegerField(null=True, blank=True)

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
    def is_feedback(self) -> bool:
        return bool(self.slide) and self.slide["kind"] == "feedback"

    @property
    def has_feedback(self) -> bool:
        return any(s["kind"] == "feedback" and not s.get("hidden") for s in self.slides)

    @property
    def question_indices(self) -> list[int]:
        return [i for i, s in enumerate(self.slides) if s["kind"] == "question"]

    @property
    def asked_indices(self) -> list[int]:
        """Preguntas que de verdad se abrieron: una clase pausada o terminada a la mitad no
        castiga a nadie por las que no alcanzaron a hacerse."""
        return [i for i in self.question_indices if i in self.opened]

    @property
    def max_points(self) -> float:
        return sum(self.slides[i]["points"] for i in self.asked_indices)


class Participant(models.Model):
    session = models.ForeignKey(LiveSession, on_delete=models.CASCADE, related_name="participants")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    joined_at = models.DateTimeField(auto_now_add=True)
    #: Lo actualizan la carga de cada diapositiva y el flujo SSE cada pocos segundos: «conectado»
    #: es haberse visto hace menos de `engine.CONNECTED_WINDOW`.
    last_seen = models.DateTimeField(auto_now=True)
    #: Si ya dejó su retroalimentación. Qué respondió no se guarda junto a quién (ver Feedback).
    feedback_given = models.BooleanField(default=False)

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
    #: Nota que cuenta. La pone el calificador y el docente puede corregirla a mano (respuestas
    #: correctas que el ítem no previó); `auto_*` guardan lo que dijo el calificador.
    points = models.FloatField()
    auto_points = models.FloatField(null=True, blank=True)
    auto_correct = models.BooleanField(null=True, blank=True)
    edited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                                  blank=True, related_name="+")
    edited_at = models.DateTimeField(null=True, blank=True)
    #: Puntos de juego para el marcador: 1000 × puntos, y con bono por rapidez entre el 50 %
    #: (al final del tiempo) y el 100 % (al instante). La nota usa `points`, nunca esto.
    score = models.PositiveIntegerField(default=0)
    elapsed_ms = models.PositiveIntegerField()
    answered_at = models.DateTimeField(auto_now_add=True)

    @property
    def edited(self) -> bool:
        return self.edited_at is not None

    class Meta:
        constraints = [
            # Una respuesta por pregunta y estudiante: la primera es la que cuenta.
            models.UniqueConstraint(fields=["session", "slide_index", "student"],
                                    name="one_answer_per_question"),
        ]


class Feedback(models.Model):
    """Retroalimentación anónima de una clase.

    A propósito sin estudiante y sin fecha: ni la base de datos permite saber quién escribió qué.
    `Participant.feedback_given` solo evita que alguien responda dos veces.
    """

    session = models.ForeignKey(LiveSession, on_delete=models.CASCADE, related_name="feedback")
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
