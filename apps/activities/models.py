import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from apps.courses.models import Course
from apps.grades.models import Category, Milestone
from apps.live.models import Section


#: Los `use` del formato que son una `Activity` (lo demás, `class`, es una presentación).
WORK_USES = ("activity", "evaluation", "practice")


def course_tz(course) -> ZoneInfo:
    """Las horas de entrega («21:59») se entienden en la zona del docente del curso."""
    name = getattr(course.owner, "timezone", "") or settings.TIME_ZONE
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError):
        return ZoneInfo(settings.TIME_ZONE)


class Activity(models.Model):
    """Un deber, taller o evidencia: la fuente YAML (contenido) más lo que decide la plataforma
    (cuándo abre y vence, a qué fila de la ponderación pertenece, qué pasa con lo atrasado).

    El vencimiento es fijo (`due_at`), relativo a una fecha clave (`due_milestone` +
    `due_offset_days` a las `due_time`) o relativo a la apertura (`due_from_opening`: «7 días
    después del taller»); en los dos últimos casos `due_at` se recalcula cada vez que la fecha de
    referencia cambia, así que siempre se puede consultar directamente. El fin de la revisión
    (`review_until`) sigue las mismas reglas.
    """

    class Late(models.TextChoices):
        PENALTY = "penalty", _("Se aceptan con penalización")
        BLOCK = "block", _("No se aceptan")

    class DebateClose(models.TextChoices):
        REVIEW = "review", _("Al cerrar la revisión")
        FIXED = "fixed", _("En una fecha fija")
        MANUAL = "manual", _("Hasta que usted lo cierre")

    class Kind(models.TextChoices):
        ACTIVITY = "activity", _("Actividad")
        EVALUATION = "evaluation", _("Evaluación")
        #: Un repaso: sin nota, sin tiempo y con intentos ilimitados; cada pregunta se comprueba al
        #: momento, con su corrección y su explicación. Abre unos días antes de una fecha clave.
        PRACTICE = "practice", _("Repaso")

    class Review(models.TextChoices):
        ALWAYS = "always", _("Sin límite")
        MILESTONE = "milestone", _("Hasta una fecha relativa a una fecha clave")
        FIXED = "fixed", _("Hasta una fecha fija")
        NONE = "none", _("Sin revisión (solo se muestra el puntaje)")

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="activities")
    #: Lo dice el documento (`use: activity` o `use: evaluation`). Una evaluación (prueba, examen) se
    #: rinde una sola vez en un horario, con duración por estudiante, código de acceso y notas que el
    #: docente publica cuando termina de calificar.
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.ACTIVITY, db_default=Kind.ACTIVITY)
    #: La unidad a la que pertenece (pestaña Clases); con GitHub, la carpeta donde está su archivo.
    section = models.ForeignKey(Section, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="activities", verbose_name=_("sección"))
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
                                    help_text=_("Si se deja vacío, la actividad está disponible desde su publicación."))
    due_milestone = models.ForeignKey(Milestone, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name="activities", verbose_name=_("fecha clave"))
    due_offset_days = models.SmallIntegerField(_("días respecto a la fecha clave"), default=-1)
    due_time = models.TimeField(_("hora de entrega"), default=time(21, 59))
    due_at = models.DateTimeField(_("fecha de entrega"), null=True, blank=True)
    #: Vence `due_offset_days` días después de la apertura (`opens_at`), a las `due_time`.
    due_from_opening = models.BooleanField(default=False, db_default=False)
    #: Revisión: desde que vence, el estudiante ve la corrección detallada y las diapositivas de
    #: solución, hasta `review_until` (sin límite si está vacía).
    review_mode = models.CharField(_("revisión"), max_length=10, choices=Review.choices,
                                   default=Review.ALWAYS, db_default=Review.ALWAYS)
    review_milestone = models.ForeignKey(Milestone, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name="review_activities", verbose_name=_("fecha clave de la revisión"))
    review_offset_days = models.SmallIntegerField(_("días respecto a la fecha clave"), default=0, db_default=0)
    review_time = models.TimeField(_("hora de cierre de la revisión"), default=time(0, 0), db_default=time(0, 0))
    review_until = models.DateTimeField(_("revisión hasta"), null=True, blank=True)
    #: Evaluación: hora de inicio el día de su fecha clave (el cierre es `due_time`).
    opens_time = models.TimeField(_("hora de inicio"), null=True, blank=True)
    #: Evaluación: minutos desde que el estudiante empieza, sin pasar del cierre. Vacío: hasta el cierre.
    duration_min = models.PositiveSmallIntegerField(_("duración (minutos)"), null=True, blank=True)
    #: Evaluación: el código que el docente proyecta en el aula; sin él no se puede empezar.
    require_code = models.BooleanField(_("pedir un código de acceso para empezar"), default=True, db_default=True)
    access_code = models.CharField(max_length=8, blank=True, default="", db_default="")
    #: Evaluación: cuándo publicó el docente las notas. Antes, el estudiante no ve ni su puntaje.
    published_at = models.DateTimeField(_("notas publicadas"), null=True, blank=True)
    late_policy = models.CharField(_("entregas atrasadas"), max_length=8, choices=Late.choices,
                                   default=Late.PENALTY)
    late_penalty = models.PositiveSmallIntegerField(_("penalización (%)"), default=30)
    max_attempts = models.PositiveSmallIntegerField(_("intentos"), default=3)
    #: Al vencer, los borradores empezados a tiempo se envían con lo que tengan: un borrador sin
    #: enviar no cuenta, y nadie pierde lo que respondió por no pulsar «Enviar».
    auto_submit = models.BooleanField(_("enviar automáticamente los borradores al vencer la fecha"),
                                      default=True, db_default=True)
    #: Ítems deshabilitados (por `item_id`): para la sección de un taller a la que no se llegó en
    #: clase. El estudiante no los ve y no cuentan para la nota ni para el máximo; sus respuestas,
    #: si las había, se conservan y reaparecen al habilitarlos. Vale para todos los intentos, también
    #: los ya enviados. (El nombre de la columna es de cuando solo dejaban de calificarse.)
    ungraded_items = models.JSONField(default=list, db_default=[], blank=True)
    #: Preguntas de debate (`debate: true`): el foro abre al vencer la fecha de entrega y se cierra
    #: según `debate_mode`: con la revisión (por omisión), en `debate_until` o cuando el docente lo
    #: cierra («Cerrar el debate ahora» lo fija en ese momento). Al cerrarse se muestran la
    #: corrección y la explicación de esas preguntas. `debate_close` dice cuándo.
    debate_mode = models.CharField(_("cierre del debate"), max_length=8, choices=DebateClose.choices,
                                   default=DebateClose.REVIEW, db_default=DebateClose.REVIEW)
    debate_until = models.DateTimeField(_("debate hasta"), null=True, blank=True)
    #: Dónde está el docente al presentar la actividad en clase: el bloque (`at` del contenido) y,
    #: si es un ítem, su `item_id`. `presenting_at` es la última señal de la página del proyector:
    #: sin señal reciente, no se está presentando.
    presenting_at = models.DateTimeField(null=True, blank=True)
    pointer_at = models.SmallIntegerField(null=True, blank=True)
    pointer_item = models.CharField(max_length=120, blank=True, default="", db_default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [models.F("due_at").asc(nulls_last=True), "title"]
        verbose_name = _("actividad")

    def __str__(self) -> str:
        return self.title

    @property
    def points(self) -> float:
        """Lo que vale la actividad: sin los ítems deshabilitados."""
        return graded_points(self.compiled, self.ungraded_items)

    @property
    def disabled(self) -> set[str]:
        """Los `item_id` deshabilitados."""
        return set(self.ungraded_items or [])

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

    @property
    def due_relative(self) -> bool:
        return bool(self.due_milestone_id or self.due_from_opening)

    def compute_due(self) -> None:
        """Recalcula `due_at` (y el fin de la revisión) desde su fecha de referencia. No guarda. Una
        evaluación relativa a una fecha clave abre ese día a las `opens_time`."""
        tz = course_tz(self.course)
        if self.is_practice:
            # Un repaso abre unos días antes de su fecha clave (`due_offset_days`, negativo) y no vence.
            if self.due_milestone_id:
                day = self.due_milestone.on + timedelta(days=self.due_offset_days)
                self.opens_at = datetime.combine(day, time(0, 0), tzinfo=tz)
            return
        if self.due_milestone_id:
            day = self.due_milestone.on + timedelta(days=self.due_offset_days)
            self.due_at = datetime.combine(day, self.due_time, tzinfo=tz)
            if self.is_evaluation and self.opens_time:
                self.opens_at = datetime.combine(day, self.opens_time, tzinfo=tz)
        elif self.due_from_opening and self.opens_at:
            day = self.opens_at.astimezone(tz).date() + timedelta(days=self.due_offset_days)
            self.due_at = datetime.combine(day, self.due_time, tzinfo=tz)
        self.compute_review()

    def compute_review(self) -> None:
        """Recalcula `review_until` según el modo de revisión. No guarda."""
        if self.review_mode == self.Review.MILESTONE and self.review_milestone_id:
            day = self.review_milestone.on + timedelta(days=self.review_offset_days)
            self.review_until = datetime.combine(day, self.review_time, tzinfo=course_tz(self.course))
        elif self.review_mode in (self.Review.ALWAYS, self.Review.NONE):
            self.review_until = None

    @property
    def review_rule(self) -> str:
        """«hasta el mismo día de «Prueba 1», 00:00», «hasta el 12 nov, 07:00», «sin límite»…"""
        if self.review_mode == self.Review.NONE:
            return _("sin revisión")
        if self.review_mode == self.Review.ALWAYS or not self.review_until:
            return _("sin límite")
        return _("hasta el %(when)s") % {"when": date_format(timezone.localtime(self.review_until, course_tz(self.course)),
                                                           "D d M, H:i")}

    @property
    def debate_keys(self) -> list[str]:
        """Las preguntas de debate de la versión actual, sin las de ítems deshabilitados."""
        if self.kind != self.Kind.ACTIVITY:
            return []
        skip = self.disabled
        return [q["key"] for item in self.compiled.get("items", []) if item.get("kind", "item") == "item"
                and item.get("item_id") not in skip for q in item["questions"] if q.get("debate")]

    @property
    def debate_close(self):
        """Cuándo se cierra el debate; None si sigue abierto hasta que el docente lo cierre (también
        con «Al cerrar la revisión» si la revisión no tiene fin)."""
        if self.debate_mode == self.DebateClose.REVIEW:
            return self.review_until if self.review_mode in (self.Review.MILESTONE, self.Review.FIXED) else None
        return self.debate_until

    def debate_open(self, now=None) -> bool:
        """El foro está abierto: desde el vencimiento hasta su cierre."""
        now = now or timezone.now()
        close = self.debate_close
        return bool(self.debate_keys) and self.is_past_due(now) and (close is None or now <= close)

    def debate_closed(self, now=None) -> bool:
        close = self.debate_close
        return bool(close) and (now or timezone.now()) > close

    def review_open(self, now=None) -> bool:
        """¿Está abierta la revisión? Desde que vence la fecha hasta `review_until`; en una evaluación,
        además, solo con las notas publicadas."""
        now = now or timezone.now()
        if self.review_mode == self.Review.NONE or not self.is_past_due(now):
            return False
        if self.is_evaluation and not self.published_at:
            return False
        return self.review_until is None or now <= self.review_until

    @property
    def is_evaluation(self) -> bool:
        return self.kind == self.Kind.EVALUATION

    @property
    def is_practice(self) -> bool:
        return self.kind == self.Kind.PRACTICE

    @property
    def days_before(self) -> int:
        """Repaso: cuántos días antes de su fecha clave abre."""
        return -self.due_offset_days if self.due_milestone_id else 0

    @classmethod
    def kind_for(cls, use: str) -> str:
        """El tipo según el `use` del documento."""
        return {"evaluation": cls.Kind.EVALUATION, "practice": cls.Kind.PRACTICE}.get(use, cls.Kind.ACTIVITY)

    def refresh_from_db(self, *args, **kwargs):
        super().refresh_from_db(*args, **kwargs)
        # Las prórrogas que el motor guardó en el objeto se vuelven a leer.
        self.__dict__.pop("_extensions", None)

    def set_kind(self, use: str) -> None:
        """El tipo según el `use` del documento. Una evaluación nueva parte de lo habitual: un solo
        intento, sin atrasos, envío automático y código de acceso. No guarda."""
        kind = self.kind_for(use)
        if kind == self.kind and self.pk:
            return
        self.kind = kind
        if kind == self.Kind.EVALUATION:
            self.max_attempts, self.late_policy, self.auto_submit = 1, self.Late.BLOCK, True
            self.require_code = True
            self.access_code = self.access_code or new_access_code()
        elif kind == self.Kind.PRACTICE:
            # Sin nota: no cuenta en ninguna fila, no vence y se repite cuantas veces se quiera.
            self.max_attempts, self.late_policy, self.auto_submit = PRACTICE_ATTEMPTS, self.Late.BLOCK, False
            self.category, self.review_mode = None, self.Review.ALWAYS

    def window_open(self, now=None) -> bool:
        """Evaluación: dentro del horario (de la apertura al cierre)."""
        now = now or timezone.now()
        return bool(self.opens_at and self.due_at) and self.opens_at <= now <= self.due_at

    def is_presenting(self, now=None) -> bool:
        return bool(self.presenting_at) and (now or timezone.now()) - self.presenting_at < PRESENTING_TTL

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
        """Una actividad publicada se ve desde que abre; una evaluación publicada, desde antes, para
        que el estudiante sepa cuándo es (pero solo se empieza en su horario)."""
        now = now or timezone.now()
        if self.is_evaluation:
            return self.published
        return self.published and (self.opens_at is None or self.opens_at <= now)

    def is_past_due(self, now=None) -> bool:
        return bool(self.due_at) and (now or timezone.now()) > self.due_at


#: Un repaso no limita los intentos; este tope solo evita un número sin fin de filas.
PRACTICE_ATTEMPTS = 200

#: Sin señal de la página del proyector durante este tiempo, la presentación se da por terminada.
PRESENTING_TTL = timedelta(seconds=90)


def graded_points(content: dict, disabled) -> float:
    """Los puntos de un contenido compilado sin los ítems deshabilitados."""
    skip = set(disabled or [])
    return float(sum(float(item.get("points") or 0) for item in content.get("items", [])
                     if item.get("kind", "item") == "item" and item.get("item_id") not in skip))


def new_token() -> str:
    return uuid.uuid4().hex


def new_access_code() -> str:
    """Seis cifras, fáciles de leer en el proyector y de escribir en un teléfono."""
    import secrets

    return f"{secrets.randbelow(10**6):06d}"


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
    #: Preguntas con tiempo: clave → {"opened": ISO, "closed": ISO}. El reloj es el del servidor
    #: (`engine.timer`): se abre a pedido del estudiante, corre aunque cierre la página y no se reinicia.
    timers = models.JSONField(default=dict)
    #: Preguntas que el estudiante marcó para revisar antes de enviar (claves de pregunta).
    marked = models.JSONField(default=list, db_default=[], blank=True)
    #: Se envió solo, al vencer la fecha (`Activity.auto_submit`).
    auto_submitted = models.BooleanField(default=False, db_default=False)
    #: Comentario general del docente sobre la entrega (los de cada pregunta van en `Answer.comment`).
    feedback = models.TextField(blank=True, default="", db_default="")
    #: Justificaciones de las preguntas de debate, {clave: texto}; las ven los compañeros en el foro.
    reasons = models.JSONField(default=dict, db_default={}, blank=True)
    #: Repaso: las preguntas ya comprobadas, {clave: {"fraction", "correct", "review"}}. Una pregunta
    #: comprobada ya no cambia en ese intento; para volver a intentarla, se empieza otro.
    checked = models.JSONField(default=dict, db_default={}, blank=True)

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
        """Nota del intento: suma de sus respuestas menos la penalización por atraso, sin los ítems
        deshabilitados (que tampoco cuentan en el máximo). Guarda."""
        skip = self.activity.disabled
        rows = [a for a in self.answer_rows.all() if a.item_id not in skip]
        raw = sum(a.points for a in rows)
        self.points = round(raw * (100 - self.penalty) / 100, 4)
        self.max_points = graded_points(self.content, skip)
        self.pending = sum(1 for a in rows if a.needs_review)
        self.save(update_fields=["points", "max_points", "pending"])


class Extension(models.Model):
    """Una prórroga para un estudiante: su propia fecha de entrega en una actividad, o su propio
    horario en una evaluación (por ejemplo, la rinde otro día por enfermedad) y, si hace falta, su
    propia duración (una adecuación: más minutos que los demás)."""

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="extensions")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    opens_at = models.DateTimeField(_("disponible desde"), null=True, blank=True)
    due_at = models.DateTimeField(_("nueva fecha"))
    note = models.CharField(_("motivo"), max_length=200, blank=True, default="", db_default="")
    #: Evaluación: minutos para este estudiante, en lugar de `Activity.duration_min`.
    duration_min = models.PositiveSmallIntegerField(_("duración propia (minutos)"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_at"]
        constraints = [models.UniqueConstraint(fields=["activity", "student"], name="one_extension_per_student")]


class DebateEntry(models.Model):
    """La postura de un estudiante en una pregunta de debate: su respuesta y su justificación, que
    sus compañeros ven desde la fecha de entrega. `value` y `reason` son los del intento que cuenta
    (`debate.sync` los mantiene al día); `revised_*`, lo que publicó durante el debate, sin nota.
    Quien no entregó y se suma al debate solo tiene lo revisado."""

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="debate_entries")
    key = models.CharField(max_length=250)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    value = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True, default="")
    revised_value = models.JSONField(null=True, blank=True)
    revised_reason = models.TextField(blank=True, default="")
    revised_at = models.DateTimeField(null=True, blank=True)
    #: El docente la ocultó: sus compañeros no la ven; el autor, sí, con el aviso.
    hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["activity", "key", "student"], name="one_debate_entry")]

    @property
    def shown_value(self):
        return self.revised_value if self.revised_at else self.value

    @property
    def shown_reason(self) -> str:
        return self.revised_reason if self.revised_at else self.reason


class DebateReply(models.Model):
    """Un comentario sobre una postura (un solo nivel: no hay respuestas a comentarios)."""

    entry = models.ForeignKey(DebateEntry, on_delete=models.CASCADE, related_name="replies")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    body = models.TextField()
    hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]


class DebateLike(models.Model):
    """Un «me gusta», público, en una postura o en un comentario."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    entry = models.ForeignKey(DebateEntry, on_delete=models.CASCADE, null=True, blank=True, related_name="likes")
    reply = models.ForeignKey(DebateReply, on_delete=models.CASCADE, null=True, blank=True, related_name="likes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "entry"], condition=models.Q(entry__isnull=False), name="one_like_per_entry"),
            models.UniqueConstraint(fields=["user", "reply"], condition=models.Q(reply__isnull=False), name="one_like_per_reply"),
            models.CheckConstraint(condition=models.Q(entry__isnull=False, reply__isnull=True)
                                   | models.Q(entry__isnull=True, reply__isnull=False), name="like_one_target"),
        ]


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
    #: Pregunta con tiempo respondida después de su límite: vale con la penalización por atraso.
    late = models.BooleanField(default=False, db_default=False)
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
