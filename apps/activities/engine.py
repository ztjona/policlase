"""Intentos de una actividad: empezar, guardar, subir imágenes, enviar y calificar.

Toda regla sobre cuándo se puede responder vive aquí, para que la página, las pruebas y una
futura API digan lo mismo:

- Se responde mientras la actividad está publicada y abierta, y quedan intentos.
- Vencida la fecha, una actividad que acepta atrasos recibe la entrega con penalización hasta el
  cierre del parcial, **solo de quien no entregó a tiempo**: quien ya entregó ve su corrección y no
  puede reenviar con las respuestas a la vista.
- Cuenta el último intento enviado. Al vencer la fecha, los borradores empezados a tiempo se
  envían solos con lo que tengan (`Activity.auto_submit`): un borrador sin enviar no cuenta.
- La corrección detallada se muestra en la revisión, desde que vence la fecha hasta su cierre
  (`Activity.review_until`), o antes si se acabaron los intentos; fuera de ella, solo el puntaje,
  para que no se acierte por descarte. Las diapositivas de solución, solo en la revisión.
- Los ítems que el docente deshabilita (`Activity.ungraded_items`) no se muestran ni se califican;
  lo que ya se había respondido en ellos se conserva.
- Una pregunta con tiempo (`time_limit_s`, de un segmento) no se ve hasta que el estudiante la
  abre; desde ahí corre el reloj del servidor. Al acabarse el tiempo (más `TIMER_GRACE_S` por la
  conexión) sigue abierta, pero lo que se responda después vale con la penalización por atraso.
  No se reinicia: cerrar la página no da más tiempo. El tiempo adicional del estudiante
  (`Enrollment.extra_time_pct`) alarga el de cada pregunta.
- **Repasos** (`Activity.kind`): sin nota, sin tiempo y con intentos ilimitados. Cada pregunta se
  comprueba al momento (`check`): queda cerrada en ese intento, con su corrección y, cuando se
  comprobó todo el ítem, su explicación.
- **Evaluaciones** (`Activity.kind`): un intento, dentro del horario (de `opens_at` a `due_at`), con
  el código que el docente proyecta y, si hay duración, un límite propio desde que se empieza. Al
  acabarse el tiempo se envía sola. El estudiante no ve su puntaje ni la corrección hasta que el
  docente publica las notas (`Activity.published_at`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import secrets

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from policlase_gen.grading import grade

from . import images
from .models import Activity, Answer, Attempt, Extension, Upload

REVIEW_TYPES = {"open", "image"}
#: Tope de texto de una respuesta abierta: un párrafo largo, no un documento.
MAX_TEXT = 10_000
#: Gracia de una pregunta con tiempo: lo que tarda en llegar la respuesta enviada al terminar.
TIMER_GRACE_S = 3
#: Gracia del envío automático: lo que tarda en llegar el último autoguardado al vencer la fecha.
AUTO_SUBMIT_GRACE = timedelta(minutes=2)
#: En una evaluación, la gracia es corta: solo lo que tarda el último guardado al acabarse el tiempo.
EVALUATION_GRACE = timedelta(seconds=20)
#: Intentos con un código de acceso equivocado antes de esperar. El contador vive en la caché de
#: cada proceso: es un freno a probar códigos al azar, no un registro.
CODE_TRIES, CODE_WAIT_S = 10, 600


class ActionError(Exception):
    """La acción no se puede hacer ahora; el mensaje se le muestra al estudiante."""


@dataclass
class State:
    """Dónde está un estudiante frente a una actividad."""

    attempts: list[Attempt]
    draft: Attempt | None
    counted: Attempt | None          # el último enviado: el que cuenta
    can_start: bool
    reason: str                      # por qué no puede empezar (vacío si puede)
    late: bool                       # empezar ahora sería una entrega atrasada
    reveal: bool                     # puede ver la corrección detallada del intento que cuenta
    solutions: bool = False          # puede ver las diapositivas de solución (y, sin entrega, las respuestas)
    review_closed: bool = False      # la revisión ya terminó
    show_score: bool = True          # en una evaluación, solo con las notas publicadas

    @property
    def used(self) -> int:
        return sum(1 for a in self.attempts if a.submitted_at)


def state(activity: Activity, student, now=None) -> State:
    now = now or timezone.now()
    finalize_overdue(activity, now, student=student)
    attempts = list(Attempt.objects.filter(activity=activity, student=student).order_by("number"))
    submitted = [a for a in attempts if a.submitted_at]
    draft = next((a for a in attempts if not a.submitted_at), None)
    counted = submitted[-1] if submitted else None
    ok, reason, late = can_answer(activity, len(submitted), now, student)
    if activity.is_practice:
        # Un repaso no tiene nota ni revisión que esperar: lo enviado se ve corregido, con las explicaciones.
        return State(attempts=attempts, draft=draft, counted=counted, can_start=ok and draft is None,
                     reason=reason, late=False, reveal=bool(counted), solutions=bool(counted))
    exhausted = len(submitted) >= activity.max_attempts
    # Con una prórroga, la revisión del estudiante empieza con su propio vencimiento.
    is_past = past_due(activity, student.pk, now)
    in_review = activity.review_open(now) and is_past
    if activity.review_mode == Activity.Review.NONE:
        reveal = False
    elif is_past or activity.is_evaluation:
        reveal = bool(counted) and in_review
    else:
        reveal = bool(counted) and exhausted
    # Las soluciones, en la revisión: para quien entregó y para quien ya no puede entregar. A quien
    # todavía puede entregar (atrasado), verlas sería responder con las soluciones a la vista.
    solutions = in_review and (bool(counted) or (not ok and draft is None))
    closed = is_past and not in_review and activity.review_mode != Activity.Review.NONE
    if activity.is_evaluation and not activity.published_at:
        closed = False                                 # la revisión ni siquiera empezó
    return State(attempts=attempts, draft=draft, counted=counted, can_start=ok and draft is None,
                 reason=reason, late=late, reveal=reveal, solutions=solutions, review_closed=closed,
                 show_score=not activity.is_evaluation or bool(activity.published_at))


def _when(moment) -> str:
    return date_format(timezone.localtime(moment), "D d M, H:i")


# ------------------------------------------------------------------------ prórrogas y tiempo adicional


def window(activity: Activity, student_id) -> tuple:
    """(apertura, vencimiento) para este estudiante: los de la actividad o los de su prórroga.
    Las prórrogas de la actividad se leen una vez y quedan en el objeto."""
    if not hasattr(activity, "_extensions"):
        activity._extensions = {e.student_id: e for e in Extension.objects.filter(activity=activity)}
    ext = activity._extensions.get(getattr(student_id, "pk", student_id))
    if ext is None:
        return activity.opens_at, activity.due_at
    return (ext.opens_at or activity.opens_at), ext.due_at


def extra_pct(activity: Activity, student_id) -> int:
    """El tiempo adicional (en %) del estudiante en el curso de la actividad (adecuación)."""
    if not hasattr(activity, "_extra"):
        from apps.courses.models import Enrollment

        activity._extra = dict(Enrollment.objects.filter(course_id=activity.course_id, extra_time_pct__gt=0)
                               .values_list("student_id", "extra_time_pct"))
    return activity._extra.get(getattr(student_id, "pk", student_id), 0)


def past_due(activity: Activity, student_id, now=None) -> bool:
    _opens, due = window(activity, student_id)
    return bool(due) and (now or timezone.now()) > due


def deadline(attempt: Attempt):
    """Hasta cuándo puede responder este intento. En una actividad, su vencimiento (o el de su
    prórroga). En una evaluación, lo primero entre el cierre y la duración contada desde que
    empezó; con tiempo adicional, la duración y el cierre se alargan en esa proporción."""
    activity = attempt.activity
    opens, due = window(activity, attempt.student_id)
    if not activity.is_evaluation:
        return due
    factor = extra_pct(activity, attempt.student_id) / 100
    if due and opens and factor:
        due = due + (timedelta(minutes=activity.duration_min) if activity.duration_min else due - opens) * factor
    if activity.duration_min and attempt.started_at:
        own = attempt.started_at + timedelta(minutes=activity.duration_min * (1 + factor))
        return min(own, due) if due else own
    return due


def can_answer(activity: Activity, submitted: int, now, student=None) -> tuple[bool, str, bool]:
    """(¿puede responder?, motivo si no, ¿sería atrasado?). Con `student`, según su prórroga."""
    opens, due = window(activity, student.pk) if student is not None else (activity.opens_at, activity.due_at)
    if activity.is_practice:
        if not activity.is_visible(now):
            return False, _("El repaso todavía no está disponible."), False
        if due and now > due:
            return False, _("El repaso se cerró el %(when)s.") % {"when": _when(due)}, False
        return True, "", False
    if activity.is_evaluation:
        # Una evaluación se rinde en su horario y una sola vez; no hay entregas atrasadas.
        if not activity.published:
            return False, _("La evaluación todavía no está disponible."), False
        if submitted >= activity.max_attempts:
            return False, _("Ya rindió esta evaluación."), False
        if not (opens and due):
            return False, _("La evaluación todavía no tiene horario."), False
        if now < opens:
            return False, _("La evaluación comienza el %(when)s.") % {"when": _when(opens)}, False
        if now > due:
            return False, _("La evaluación se cerró el %(when)s.") % {"when": _when(due)}, False
        return True, "", False
    if not activity.is_visible(now):
        return False, _("La actividad todavía no está disponible."), False
    if submitted >= activity.max_attempts:
        return False, _("Ya usó todos sus intentos."), False
    if not due or now <= due:
        return True, "", False
    if activity.late_policy == Activity.Late.BLOCK:
        return False, _("La fecha de entrega ya pasó."), False
    if submitted:
        return False, _("La fecha de entrega ya pasó; cuenta su entrega anterior."), False
    until = activity.late_until
    if until and now > until:
        return False, _("Se cerró el plazo para entregas atrasadas."), False
    return True, "", True


def check_code(activity: Activity, student, code: str | None) -> None:
    """El código que el docente proyecta en el aula. Tras varios intentos fallidos, hay que esperar."""
    key = f"policlase:codigo:{activity.pk}:{student.pk}"
    tries = cache.get(key, 0)
    if tries >= CODE_TRIES:
        raise ActionError(_("Se ingresó un código incorrecto demasiadas veces. Espere unos minutos e "
                            "inténtelo de nuevo."))
    given = "".join(str(code or "").split())
    if not activity.access_code or not secrets.compare_digest(given, activity.access_code):
        cache.set(key, tries + 1, CODE_WAIT_S)
        raise ActionError(_("El código de acceso no es correcto. Solicítelo a su docente."))
    cache.delete(key)


def start(activity: Activity, student, code: str | None = None) -> Attempt:
    current = state(activity, student)
    if current.draft:
        return current.draft
    if not current.can_start:
        raise ActionError(current.reason)
    if activity.is_evaluation and activity.require_code:
        check_code(activity, student, code)
    try:
        with transaction.atomic():
            return Attempt.objects.create(
                activity=activity, student=student, number=len(current.attempts) + 1,
                content=activity.compiled, max_points=activity.points,
            )
    except IntegrityError:          # doble clic: ya hay un borrador
        return Attempt.objects.get(activity=activity, student=student, submitted_at=None)


# ------------------------------------------------------------------------ respuestas


def parse_answers(questions: list[dict], post) -> dict:
    """Lee del formulario lo que respondió el estudiante (las imágenes van aparte)."""
    answers = {}
    for q in questions:
        name = f"a-{q['key']}"
        kind = q["type"]
        if kind == "image":
            continue
        if kind == "multi_choice":
            answers[q["key"]] = post.getlist(name)
        elif kind == "true_false":
            marks = {}
            for s in q.get("statements") or []:
                value = post.get(f"{name}-{s['key']}")
                if value in ("1", "0"):
                    marks[s["key"]] = value == "1"
            answers[q["key"]] = marks
        elif kind == "numeric_list":
            cells = _cell_count(q)
            answers[q["key"]] = [post.get(f"{name}-{i}", "").strip() for i in range(cells)]
        else:
            answers[q["key"]] = post.get(name, "").strip()[:MAX_TEXT]
    return answers


def parse_reasons(questions: list[dict], post) -> dict:
    """Las justificaciones de las preguntas de debate que llegaron en el formulario. Una que no
    llegó (la pregunta con tiempo ya cerrada, por ejemplo) no se deja en blanco."""
    from .debate import MAX_REASON

    return {q["key"]: post.get(f"w-{q['key']}", "").strip()[:MAX_REASON]
            for q in questions if q.get("debate") and f"w-{q['key']}" in post}


def _cell_count(question: dict) -> int:
    count = 1
    for dim in question.get("shape") or [1]:
        count *= int(dim)
    return count


def blank(value) -> bool:
    if value in (None, "", [], {}):
        return True
    if isinstance(value, list):
        return all(str(v).strip() == "" for v in value)
    return False


# ------------------------------------------------------------------------ preguntas con tiempo


@dataclass
class Timer:
    state: str                 # "locked" (sin abrir) · "open" · "closed"
    remaining_ms: int = 0
    limit_s: int = 0           # el del estudiante, con su tiempo adicional

    @property
    def remaining_s(self) -> int:
        return max(0, -(-self.remaining_ms // 1000))

    @property
    def overtime(self) -> bool:
        """Se acabó su tiempo: la pregunta sigue abierta, pero lo que responda vale menos."""
        return self.state == "open" and self.remaining_ms < -TIMER_GRACE_S * 1000


def limit_s(attempt: Attempt, question: dict) -> int | None:
    """El tiempo de una pregunta para este estudiante: el del segmento, más su tiempo adicional."""
    limit = question.get("time_limit_s")
    if not limit:
        return None
    return round(limit * (1 + extra_pct(attempt.activity, attempt.student_id) / 100))


def timer(attempt: Attempt, question: dict, now=None) -> Timer | None:
    """Dónde está el reloj de una pregunta con tiempo; None si la pregunta no tiene tiempo. Abierta,
    no se cierra sola al acabarse el tiempo: se cierra al responderla o al enviar el intento."""
    limit = limit_s(attempt, question)
    if not limit:
        return None
    entry = (attempt.timers or {}).get(question["key"])
    if not entry:
        return Timer("locked", limit_s=limit)
    if entry.get("closed") or attempt.submitted_at:
        return Timer("closed", limit_s=limit)
    left = datetime.fromisoformat(entry["opened"]) + timedelta(seconds=limit) - (now or timezone.now())
    return Timer("open", int(left.total_seconds() * 1000), limit_s=limit)


def check_time(attempt: Attempt, now=None) -> None:
    """En una evaluación, lo que llega después del límite del intento (más una gracia corta por la
    conexión) ya no se guarda."""
    activity = attempt.activity
    if not activity.is_evaluation:
        return
    limit = deadline(attempt)
    if limit and (now or timezone.now()) > limit + EVALUATION_GRACE:
        raise ActionError(_("Se acabó el tiempo de la evaluación; se conserva lo que se guardó hasta ese momento."))


def remaining_ms(attempt: Attempt, now=None) -> int | None:
    """Milisegundos que le quedan a un intento de evaluación (None si no hay límite)."""
    if not attempt.activity.is_evaluation:
        return None
    limit = deadline(attempt)
    if not limit:
        return None
    return max(0, int((limit - (now or timezone.now())).total_seconds() * 1000))


def _accepts(attempt: Attempt, question: dict, now) -> bool:
    """¿Se guarda ahora lo que llegue para esta pregunta? Sin tiempo, siempre; con tiempo, mientras
    está abierta (también pasado su tiempo, con penalización)."""
    current = timer(attempt, question, now)
    return current is None or current.state == "open"


def _mark_late(attempt: Attempt, key: str, value, now) -> None:
    """Una pregunta con tiempo que cambia de respuesta pasado su límite queda marcada: al enviar,
    vale con la penalización por atraso."""
    question = next((q for q in attempt.questions if q["key"] == key), None)
    current = timer(attempt, question, now) if question else None
    if current and current.overtime and value != attempt.answers.get(key):
        attempt.timers = {**attempt.timers, key: {**attempt.timers[key], "late": True}}


@transaction.atomic
def open_timer(attempt: Attempt, key: str, now=None) -> Attempt:
    """El estudiante abre una pregunta con tiempo: desde ahora corre su reloj. Abrirla de nuevo no
    lo reinicia."""
    attempt = Attempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    check_time(attempt, now)
    question = _question(attempt, key)
    if not question.get("time_limit_s"):
        raise ActionError(_("Esa pregunta no tiene límite de tiempo."))
    if key not in (attempt.timers or {}):
        attempt.timers = {**(attempt.timers or {}), key: {"opened": (now or timezone.now()).isoformat()}}
        attempt.save(update_fields=["timers"])
    return attempt


@transaction.atomic
def close_timer(attempt: Attempt, key: str, answers: dict | None = None, now=None) -> Attempt:
    """«Responder» o se acabó el tiempo: se guarda lo que llegó (si llegó a tiempo) y la pregunta
    queda cerrada para el resto del intento."""
    now = now or timezone.now()
    attempt = Attempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    check_time(attempt, now)
    question = _question(attempt, key)
    if not question.get("time_limit_s") or key not in (attempt.timers or {}):
        raise ActionError(_("Esa pregunta no está abierta."))
    if answers and key in answers and _accepts(attempt, question, now):
        _mark_late(attempt, key, answers[key], now)
        attempt.answers = {**attempt.answers, key: answers[key]}
    entry = dict(attempt.timers[key])
    entry.setdefault("closed", now.isoformat())
    attempt.timers = {**attempt.timers, key: entry}
    attempt.saved_at = now
    attempt.save(update_fields=["answers", "timers", "saved_at"])
    return attempt


def save_draft(attempt: Attempt, answers: dict, telemetry: dict | None = None, now=None,
               marked: list[str] | None = None, reasons: dict | None = None) -> Attempt:
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    now = now or timezone.now()
    check_time(attempt, now)
    if marked is not None:
        known = {q["key"] for q in attempt.questions}
        attempt.marked = [k for k in dict.fromkeys(marked) if k in known]
    # Las preguntas con tiempo solo se guardan mientras corre su reloj: el autoguardado de la página
    # entera no las abre ni les da más tiempo.
    timed = {q["key"]: q for q in attempt.questions if q.get("time_limit_s")}
    answers = {k: v for k, v in answers.items() if k not in timed or _accepts(attempt, timed[k], now)}
    # En un repaso, lo ya comprobado no cambia en este intento.
    answers = {k: v for k, v in answers.items() if k not in (attempt.checked or {})}
    for key in timed.keys() & answers.keys():
        _mark_late(attempt, key, answers[key], now)
    attempt.answers = {**attempt.answers, **answers}
    if reasons:
        attempt.reasons = {**(attempt.reasons or {}), **reasons}
    if telemetry:
        attempt.telemetry = merge_telemetry(attempt.telemetry, telemetry)
    attempt.saved_at = now
    attempt.save(update_fields=["answers", "saved_at", "telemetry", "marked", "timers", "reasons"])
    return attempt


TELEMETRY_KEYS = ("pastes", "pasted_chars", "blurs", "blocked_pastes")


def merge_telemetry(current: dict, delta: dict) -> dict:
    """El navegador manda lo ocurrido desde el último guardado; aquí se acumula."""
    out = dict(current or {})
    for key in TELEMETRY_KEYS:
        try:
            value = int(delta.get(key, 0))
        except (TypeError, ValueError):
            continue
        if 0 < value < 1_000_000:
            out[key] = out.get(key, 0) + value
    return out


def _question(attempt: Attempt, key: str) -> dict:
    for q in attempt.questions:
        if q["key"] == key:
            return q
    raise ActionError(_("Esa pregunta no existe."))


def add_upload(attempt: Attempt, key: str, upload) -> Upload:
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    check_time(attempt)
    question = _question(attempt, key)
    if question["type"] != "image":
        raise ActionError(_("Esa pregunta no admite imágenes."))
    if key in (attempt.checked or {}):
        raise ActionError(_("Esa pregunta ya se comprobó en este intento."))
    if not _accepts(attempt, question, timezone.now()):
        raise ActionError(_("Esa pregunta tiene límite de tiempo y no está abierta."))
    limit = int(question.get("max_files") or 1)
    if attempt.uploads.filter(key=key).count() >= limit:
        raise ActionError(_("Esta pregunta admite hasta %(n)d imágenes; quite una antes de subir otra.")
                          % {"n": limit})
    try:
        content, width, height = images.process(upload)
    except images.ImageError as exc:
        raise ActionError(str(exc)) from None
    row = Upload(attempt=attempt, key=key, original_name=str(upload.name or "")[:200],
                 width=width, height=height, size=content.size)
    row.file.save("imagen.jpg", content, save=False)
    row.save()
    Attempt.objects.filter(pk=attempt.pk).update(saved_at=timezone.now())
    return row


def remove_upload(attempt: Attempt, upload_pk: int) -> None:
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    check_time(attempt)
    row = attempt.uploads.filter(pk=upload_pk).first()
    if row and row.key not in (attempt.checked or {}):
        row.delete()


# ------------------------------------------------------------------------ repaso


@transaction.atomic
def check(attempt: Attempt, key: str, answers: dict, now=None) -> Attempt:
    """Repaso: comprobar una pregunta. Se guarda lo respondido y se califica al momento; la pregunta
    queda cerrada en este intento, con su corrección a la vista. Comprobarla de nuevo no hace nada."""
    attempt = Attempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    if not attempt.activity.is_practice:
        raise ActionError(_("Las preguntas se comprueban una a una solo en un repaso."))
    found = _records(attempt.content).get(key)
    if found is None:
        raise ActionError(_("Esa pregunta no existe."))
    if key in (attempt.checked or {}):
        return attempt
    _item, question, record = found
    if question["type"] == "image":
        value = list(attempt.uploads.filter(key=key).values_list("pk", flat=True))
    else:
        value = answers.get(key, attempt.answers.get(key))
    if blank(value):
        raise ActionError(_("Responda la pregunta antes de comprobarla."))
    result = grade(question, value, solution=record)
    if not result.gradable:
        raise ActionError(" ".join(result.format_errors))
    if question["type"] != "image":
        attempt.answers = {**attempt.answers, key: value}
    attempt.checked = {**(attempt.checked or {}), key: {
        "fraction": result.fraction, "correct": result.correct, "review": result.needs_review}}
    attempt.saved_at = now or timezone.now()
    attempt.save(update_fields=["answers", "checked", "saved_at"])
    return attempt


# ------------------------------------------------------------------------ enviar


def _records(content: dict) -> dict[str, tuple[dict, dict, dict]]:
    """clave de pregunta → (ítem, pregunta, registro de solución)."""
    out = {}
    for item in content.get("items", []):
        for q in item["questions"]:
            out[q["key"]] = (item, q, item.get("solutions", {}).get(q["id"], {}))
    return out


def format_errors(attempt: Attempt) -> dict[str, str]:
    """Respuestas que no se pueden leer («2,5,3» en una numérica): se le piden corregir antes de
    enviar. Una pregunta en blanco no es un error: vale cero."""
    errors = {}
    skip = attempt.activity.disabled
    for key, (item, q, record) in _records(attempt.content).items():
        value = attempt.answers.get(key)
        if q["type"] in REVIEW_TYPES or blank(value) or item["item_id"] in skip:
            continue                       # lo deshabilitado no se ve: no se le puede pedir corregirlo
        result = grade(q, value, solution=record)
        if not result.gradable:
            errors[key] = " ".join(result.format_errors)
    return errors


@transaction.atomic
def submit(attempt: Attempt, now=None, auto: bool = False) -> Attempt:
    """Envía el intento. `auto`: el envío automático al vencer la fecha (`now` es el vencimiento);
    una respuesta ilegible no lo detiene y vale cero, como una en blanco."""
    now = now or timezone.now()
    attempt = Attempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    activity = attempt.activity
    previous = Attempt.objects.filter(activity=activity, student=attempt.student,
                                      submitted_at__isnull=False).count()
    if activity.is_evaluation:
        # Pasado su límite, el intento cuenta como enviado en ese momento (nada se guardó después).
        limit = deadline(attempt)
        if limit and now > limit:
            now = limit
    ok, reason, late = can_answer(activity, previous, now, attempt.student)
    if not ok:
        raise ActionError(reason)
    if not auto and format_errors(attempt):
        raise ActionError(_("Revise las respuestas marcadas: no se pudieron leer."))

    files: dict[str, list[int]] = {}
    for row in attempt.uploads.all():
        files.setdefault(row.key, []).append(row.pk)

    keep = (100 - activity.late_penalty) / 100
    for key, (item, q, record) in _records(attempt.content).items():
        value = files.get(key, []) if q["type"] == "image" else attempt.answers.get(key)
        overtime = bool((attempt.timers or {}).get(key, {}).get("late")) and not blank(value)
        if blank(value):
            points, fraction, correct, review = 0.0, 0.0, False, False
        else:
            result = grade(q, value, solution=record)
            points, fraction, correct, review = result.points, result.fraction, result.correct, result.needs_review
            if overtime:                # respondida pasado el tiempo de la pregunta
                points, fraction = round(points * keep, 6), fraction * keep
        Answer.objects.create(
            attempt=attempt, key=key, item_id=item["item_id"], item_version=item["item_version"],
            value=value, fraction=fraction, points=points, correct=correct, late=overtime,
            auto_points=None if review else points, auto_correct=None if review else correct,
            needs_review=review,
        )

    attempt.submitted_at = now
    attempt.late = late
    attempt.penalty = activity.late_penalty if late else 0
    attempt.auto_submitted = auto
    attempt.save(update_fields=["submitted_at", "late", "penalty", "auto_submitted"])
    attempt.recompute()
    return attempt


def finalize_overdue(activity: Activity, now=None, student=None) -> int:
    """Envía los borradores que quedaron abiertos al vencer la fecha (`Activity.auto_submit`), con lo
    que tengan, como entregas a tiempo. Solo los empezados antes del vencimiento: un intento
    atrasado se envía a mano. En una evaluación (siempre se envía sola), cada intento vence en su
    propio límite (`Activity.deadline`). Se llama al consultar la actividad (no hay tareas
    programadas), así que da igual cuándo se mire: el resultado es el mismo."""
    now = now or timezone.now()
    if not activity.due_at or not (activity.auto_submit or activity.is_evaluation):
        return 0
    grace = EVALUATION_GRACE if activity.is_evaluation else AUTO_SUBMIT_GRACE
    timed = activity.is_evaluation and activity.duration_min
    if not timed and now <= activity.due_at + grace and not Extension.objects.filter(activity=activity).exists():
        return 0                                       # nadie venció todavía
    drafts = Attempt.objects.filter(activity=activity, submitted_at=None)
    if student is not None:
        drafts = drafts.filter(student=student)
    sent = 0
    for draft in drafts:
        draft.activity = activity
        limit = deadline(draft)
        _opens, due = window(activity, draft.student_id)
        # Solo lo empezado a tiempo; un intento atrasado lo envía el estudiante.
        if not limit or not due or draft.started_at > due or now <= limit + grace:
            continue
        try:
            submit(draft, now=limit, auto=True)
        except ActionError:
            continue
        sent += 1
    return sent


def finish_on_time(attempt: Attempt, now=None) -> Attempt:
    """Se acabó el tiempo de una evaluación (la página lo pide al llegar a cero): se envía tal como
    está, con la hora del límite."""
    now = now or timezone.now()
    limit = deadline(attempt)
    if not attempt.activity.is_evaluation or not limit or now < limit - timedelta(seconds=5):
        raise ActionError(_("Todavía queda tiempo."))
    return submit(attempt, now=min(now, limit), auto=True)


def publish(activity: Activity, now=None) -> None:
    """Publica las notas de una evaluación cerrada: desde ahora, cada estudiante ve su nota y, durante
    la revisión, su corrección."""
    now = now or timezone.now()
    if not activity.is_past_due(now):
        raise ActionError(_("Las notas se publican cuando la evaluación ya se cerró."))
    finalize_overdue(activity, now)
    activity.published_at = now
    activity.save(update_fields=["published_at"])


def unpublish(activity: Activity) -> None:
    activity.published_at = None
    activity.save(update_fields=["published_at"])


def reopen_auto_submitted(activity: Activity, now=None) -> int:
    """La fecha de entrega se movió hacia adelante: los intentos que se enviaron solos vuelven a ser
    borradores, para que el estudiante siga donde quedó. No se toca un intento que el docente ya
    calificó a mano ni uno tras el cual el estudiante empezó otro. Las respuestas no se pierden:
    siguen en el borrador (`Attempt.answers`)."""
    now = now or timezone.now()
    if not activity.due_at or activity.due_at <= now or activity.is_evaluation:
        return 0                              # una evaluación se rinde una vez: lo enviado no se reabre
    reopened = 0
    for attempt in Attempt.objects.filter(activity=activity, auto_submitted=True, submitted_at__isnull=False):
        later = Attempt.objects.filter(activity=activity, student=attempt.student, number__gt=attempt.number).exists()
        if later or attempt.answer_rows.filter(graded_by__isnull=False).exists():
            continue
        with transaction.atomic():
            attempt.answer_rows.all().delete()
            attempt.submitted_at, attempt.auto_submitted = None, False
            attempt.points, attempt.pending, attempt.late, attempt.penalty = None, 0, False, 0
            attempt.save(update_fields=["submitted_at", "auto_submitted", "points", "pending", "late", "penalty"])
        reopened += 1
    return reopened


def grant_extension(activity: Activity, student, due_at, opens_at=None, note: str = "") -> Extension:
    """Una prórroga para un estudiante. En una actividad, si su intento se envió solo al vencer la
    fecha general, vuelve a quedar abierto (salvo que el docente ya lo haya calificado a mano)."""
    ext, _created = Extension.objects.update_or_create(
        activity=activity, student=student, defaults={"due_at": due_at, "opens_at": opens_at, "note": note[:200]})
    activity.__dict__.pop("_extensions", None)
    if not activity.is_evaluation and due_at > timezone.now():
        for attempt in Attempt.objects.filter(activity=activity, student=student, auto_submitted=True):
            later = Attempt.objects.filter(activity=activity, student=student, number__gt=attempt.number).exists()
            if later or attempt.answer_rows.filter(graded_by__isnull=False).exists():
                continue
            with transaction.atomic():
                attempt.answer_rows.all().delete()
                attempt.submitted_at, attempt.auto_submitted = None, False
                attempt.points, attempt.pending, attempt.late, attempt.penalty = None, 0, False, 0
                attempt.save(update_fields=["submitted_at", "auto_submitted", "points", "pending", "late", "penalty"])
    return ext


def visible_questions(attempt: Attempt) -> list[dict]:
    """Las preguntas que el estudiante tiene a la vista: sin las de los ítems deshabilitados. Solo
    estas se leen del formulario, para que el autoguardado no deje en blanco lo que ya había
    respondido en una sección que luego se deshabilitó."""
    skip = attempt.activity.disabled
    return [q for item in attempt.content.get("items", []) if item.get("item_id") not in skip
            for q in item["questions"]]


def set_disabled(activity: Activity, item_ids) -> int:
    """Qué ítems están deshabilitados. Recalcula las entregas ya enviadas; devuelve cuántas."""
    known = {item["item_id"] for item in activity.compiled.get("items", []) if item.get("kind", "item") == "item"}
    activity.ungraded_items = sorted(set(item_ids) & known)
    activity.save(update_fields=["ungraded_items"])
    attempts = list(Attempt.objects.filter(activity=activity, submitted_at__isnull=False))
    for attempt in attempts:
        attempt.activity = activity
        attempt.recompute()
    return len(attempts)


def set_grade(answer: Answer, points: float | None, by, comment: str | None = None) -> Answer:
    """La nota del docente en una pregunta. `None` vuelve a la automática (si la hubo)."""
    question = _records(answer.attempt.content)[answer.key][1]
    full = float(question.get("points") or 0)
    if points is None:
        if answer.auto_points is None:
            raise ActionError(_("Esta pregunta no tiene calificación automática; ingrese una nota."))
        answer.points, answer.correct = answer.auto_points, bool(answer.auto_correct)
        answer.graded_by, answer.graded_at = None, None
    else:
        answer.points = min(max(0.0, float(points)), full)
        answer.correct = full > 0 and answer.points >= full - 1e-9
        answer.graded_by, answer.graded_at = by, timezone.now()
        answer.needs_review = False
    if comment is not None:
        answer.comment = comment.strip()[:2000]
    answer.fraction = answer.points / full if full else 0.0
    answer.save()
    answer.attempt.recompute()
    return answer


# ------------------------------------------------------------------------ resultados


def counted_attempts(activity: Activity) -> dict[int, Attempt]:
    """Estudiante → el intento que cuenta (el último enviado)."""
    out: dict[int, Attempt] = {}
    for a in (Attempt.objects.filter(activity=activity, submitted_at__isnull=False)
              .order_by("student_id", "number")):
        out[a.student_id] = a
    return out


# ------------------------------------------------------------------------ presentar en clase


def set_pointer(activity: Activity, block: dict, now=None) -> None:
    """El docente presenta la actividad y está en `block`. También sirve de señal de vida.

    Además de la posición (`at`), se guarda con qué reconocer el bloque si el intento del estudiante
    es de una versión anterior de la actividad (las posiciones pueden diferir): el `item_id` de un
    ítem, o «#» y el título de una diapositiva de contenido o de un segmento."""
    from .outline import heading

    if block.get("item_id"):
        key = block["item_id"]
    else:
        found = block.get("title") or (heading(block.get("markdown", "")) or (0, ""))[1]
        key = f"#{found}" if found else ""
    Activity.objects.filter(pk=activity.pk).update(
        presenting_at=now or timezone.now(), pointer_at=block.get("at"), pointer_item=key[:120])


def stop_presenting(activity: Activity) -> None:
    Activity.objects.filter(pk=activity.pk).update(presenting_at=None, pointer_at=None, pointer_item="")


def pointer(activity: Activity, now=None) -> dict:
    """Lo que necesita la página del estudiante para seguir al docente."""
    if not activity.is_presenting(now) or activity.pointer_at is None:
        return {"presenting": False}
    key = activity.pointer_item or ""
    if key.startswith("#"):
        return {"presenting": True, "at": activity.pointer_at, "item": "", "heading": key[1:]}
    return {"presenting": True, "at": activity.pointer_at, "item": key, "heading": ""}


def progress(activity: Activity, keys: list[str]) -> dict:
    """Cuántos borradores en curso hay y cuántos respondieron ya cada pregunta (sin decir qué)."""
    drafts = list(Attempt.objects.filter(activity=activity, submitted_at=None).values_list("answers", flat=True))
    answered = {key: sum(1 for answers in drafts if not blank((answers or {}).get(key))) for key in keys}
    images = set(Upload.objects.filter(attempt__activity=activity, attempt__submitted_at=None, key__in=keys)
                 .values_list("attempt_id", "key"))
    for _attempt, key in images:                             # las imágenes no están en `answers`
        answered[key] += 1
    return {"working": len(drafts), "answered": answered}


def answer_rows(attempt: Attempt) -> list[dict]:
    """Cada pregunta del intento con lo respondido, la nota y (si corresponde) la solución."""
    rows = {a.key: a for a in attempt.answer_rows.all()} if attempt.submitted_at else {}
    uploads: dict[str, list[Upload]] = {}
    for u in attempt.uploads.all():
        uploads.setdefault(u.key, []).append(u)
    skip = attempt.activity.disabled
    out = []
    for key, (item, q, record) in _records(attempt.content).items():
        given = uploads.get(key, []) if q["type"] == "image" else attempt.answers.get(key)
        row = {"key": key, "item": item, "question": q, "record": record,
               "answer": rows.get(key), "draft": attempt.answers.get(key), "uploads": uploads.get(key, []),
               "timer": timer(attempt, q), "blank": blank(given), "marked": key in (attempt.marked or []),
               "disabled": item["item_id"] in skip, "reason": (attempt.reasons or {}).get(key, ""),
               "check": (attempt.checked or {}).get(key),
               "value": rows[key].value if key in rows else attempt.answers.get(key)}
        if q["type"] == "numeric_list":
            given = row["draft"] if isinstance(row["draft"], list) else []
            row["cells"] = [given[i] if i < len(given) else "" for i in range(_cell_count(q))]
        out.append(row)
    return out
