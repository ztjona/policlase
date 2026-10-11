"""Intentos de una actividad: empezar, guardar, subir imágenes, enviar y calificar.

Toda regla sobre cuándo se puede responder vive aquí, para que la página, las pruebas y una
futura API digan lo mismo:

- Se responde mientras la actividad está publicada y abierta, y quedan intentos.
- Vencida la fecha, una actividad que acepta atrasos recibe la entrega con penalización hasta el
  cierre del parcial, **solo de quien no entregó a tiempo**: quien ya entregó ve su corrección y no
  puede reenviar con las respuestas a la vista.
- Cuenta el último intento enviado.
- La corrección detallada se muestra cuando vence la fecha (o se acabaron los intentos); antes,
  solo el puntaje, para que no se acierte por descarte.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from policlase_gen.grading import grade

from . import images
from .models import Activity, Answer, Attempt, Upload

REVIEW_TYPES = {"open", "image"}
#: Tope de texto de una respuesta abierta: un párrafo largo, no un documento.
MAX_TEXT = 10_000


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

    @property
    def used(self) -> int:
        return sum(1 for a in self.attempts if a.submitted_at)


def state(activity: Activity, student, now=None) -> State:
    now = now or timezone.now()
    attempts = list(Attempt.objects.filter(activity=activity, student=student).order_by("number"))
    submitted = [a for a in attempts if a.submitted_at]
    draft = next((a for a in attempts if not a.submitted_at), None)
    counted = submitted[-1] if submitted else None
    ok, reason, late = can_answer(activity, len(submitted), now)
    exhausted = len(submitted) >= activity.max_attempts
    reveal = bool(counted) and (activity.is_past_due(now) or exhausted)
    return State(attempts=attempts, draft=draft, counted=counted, can_start=ok and draft is None,
                 reason=reason, late=late, reveal=reveal)


def can_answer(activity: Activity, submitted: int, now) -> tuple[bool, str, bool]:
    """(¿puede responder?, motivo si no, ¿sería atrasado?)"""
    if not activity.is_visible(now):
        return False, _("La actividad todavía no está disponible."), False
    if submitted >= activity.max_attempts:
        return False, _("Ya usó todos sus intentos."), False
    if not activity.is_past_due(now):
        return True, "", False
    if activity.late_policy == Activity.Late.BLOCK:
        return False, _("La fecha de entrega ya pasó."), False
    if submitted:
        return False, _("La fecha de entrega ya pasó; cuenta su entrega anterior."), False
    until = activity.late_until
    if until and now > until:
        return False, _("Se cerró el plazo para entregas atrasadas."), False
    return True, "", True


def start(activity: Activity, student) -> Attempt:
    current = state(activity, student)
    if current.draft:
        return current.draft
    if not current.can_start:
        raise ActionError(current.reason)
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


def save_draft(attempt: Attempt, answers: dict, telemetry: dict | None = None) -> Attempt:
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    attempt.answers = {**attempt.answers, **answers}
    attempt.saved_at = timezone.now()
    if telemetry:
        attempt.telemetry = merge_telemetry(attempt.telemetry, telemetry)
    attempt.save(update_fields=["answers", "saved_at", "telemetry"])
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
    question = _question(attempt, key)
    if question["type"] != "image":
        raise ActionError(_("Esa pregunta no recibe imágenes."))
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
    row = attempt.uploads.filter(pk=upload_pk).first()
    if row:
        row.delete()


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
    for key, (_item, q, record) in _records(attempt.content).items():
        value = attempt.answers.get(key)
        if q["type"] in REVIEW_TYPES or blank(value):
            continue
        result = grade(q, value, solution=record)
        if not result.gradable:
            errors[key] = " ".join(result.format_errors)
    return errors


@transaction.atomic
def submit(attempt: Attempt, now=None) -> Attempt:
    now = now or timezone.now()
    attempt = Attempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.submitted_at:
        raise ActionError(_("Este intento ya se envió."))
    activity = attempt.activity
    previous = Attempt.objects.filter(activity=activity, student=attempt.student,
                                      submitted_at__isnull=False).count()
    ok, reason, late = can_answer(activity, previous, now)
    if not ok:
        raise ActionError(reason)
    errors = format_errors(attempt)
    if errors:
        raise ActionError(_("Revise las respuestas marcadas: no se pudieron leer."))

    files: dict[str, list[int]] = {}
    for row in attempt.uploads.all():
        files.setdefault(row.key, []).append(row.pk)

    for key, (item, q, record) in _records(attempt.content).items():
        value = files.get(key, []) if q["type"] == "image" else attempt.answers.get(key)
        if blank(value):
            points, fraction, correct, review = 0.0, 0.0, False, False
        else:
            result = grade(q, value, solution=record)
            points, fraction, correct, review = result.points, result.fraction, result.correct, result.needs_review
        Answer.objects.create(
            attempt=attempt, key=key, item_id=item["item_id"], item_version=item["item_version"],
            value=value, fraction=fraction, points=points, correct=correct,
            auto_points=None if review else points, auto_correct=None if review else correct,
            needs_review=review,
        )

    attempt.submitted_at = now
    attempt.late = late
    attempt.penalty = activity.late_penalty if late else 0
    attempt.save(update_fields=["submitted_at", "late", "penalty"])
    attempt.recompute()
    return attempt


def set_grade(answer: Answer, points: float | None, by, comment: str | None = None) -> Answer:
    """La nota del docente en una pregunta. `None` vuelve a la automática (si la hubo)."""
    question = _records(answer.attempt.content)[answer.key][1]
    full = float(question.get("points") or 0)
    if points is None:
        if answer.auto_points is None:
            raise ActionError(_("Esta pregunta no tiene nota automática: escriba una."))
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


def answer_rows(attempt: Attempt) -> list[dict]:
    """Cada pregunta del intento con lo respondido, la nota y (si corresponde) la solución."""
    rows = {a.key: a for a in attempt.answer_rows.all()} if attempt.submitted_at else {}
    uploads: dict[str, list[Upload]] = {}
    for u in attempt.uploads.all():
        uploads.setdefault(u.key, []).append(u)
    out = []
    for key, (item, q, record) in _records(attempt.content).items():
        row = {"key": key, "item": item, "question": q, "record": record,
               "answer": rows.get(key), "draft": attempt.answers.get(key), "uploads": uploads.get(key, [])}
        if q["type"] == "numeric_list":
            given = row["draft"] if isinstance(row["draft"], list) else []
            row["cells"] = [given[i] if i < len(given) else "" for i in range(_cell_count(q))]
        out.append(row)
    return out
