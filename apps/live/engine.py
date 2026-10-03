"""Máquina de estados de una clase en vivo.

Cada transición es una actualización atómica condicionada al estado esperado, así que dos
clics del docente o un cierre automático que coincide con un clic manual no pueden dejar la
sesión en un estado imposible.

    lobby ──start──▶ live ──end──▶ ended

    en una diapositiva de contenido:  content
    en una pregunta:                   ready ──open──▶ open ──close/tiempo──▶ closed ──reveal──▶ results
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import pgettext
from policlase_gen.grading import grade

from .models import LiveSession, Participant, Response

Phase = LiveSession.Phase
Status = LiveSession.Status


def _tf(value) -> str:
    return pgettext("inicial de verdadero", "V") if value else pgettext("inicial de falso", "F")


class ActionError(Exception):
    """La acción no aplica al estado actual; el mensaje se le muestra al docente."""


def _phase_for(session: LiveSession, index: int) -> str:
    slide = session.slides[index]
    if slide["kind"] != "question":
        return Phase.CONTENT
    return Phase.RESULTS if index in session.opened else Phase.READY


@transaction.atomic
def apply(session_id: int, action: str) -> LiveSession:
    session = LiveSession.objects.select_for_update().get(pk=session_id)
    now = timezone.now()
    last = len(session.slides) - 1

    if session.status == Status.ENDED:
        raise ActionError(_("La clase ya terminó."))

    if action == "primary":
        # Barra espaciadora / botón del presentador: el paso siguiente lo decide el estado del
        # servidor, bloqueado, y no el HTML que tenga el proyector en ese instante. Dos
        # pulsaciones rápidas avanzan dos pasos en vez de repetir el primero.
        action = {Phase.READY: "open", Phase.OPEN: "close", Phase.CLOSED: "reveal"}.get(
            session.phase, "next")
        if session.status == Status.LOBBY:
            action = "start"

    if action == "start":
        if session.status != Status.LOBBY:
            raise ActionError(_("La clase ya empezó."))
        session.status, session.started_at, session.index = Status.LIVE, now, 0
        session.phase = _phase_for(session, 0)

    elif action in ("next", "prev"):
        if session.status != Status.LIVE:
            raise ActionError(_("Inicie la clase primero."))
        if session.phase == Phase.OPEN:
            raise ActionError(_("Cierre la pregunta antes de cambiar de diapositiva."))
        target = session.index + (1 if action == "next" else -1)
        if not 0 <= target <= last:
            raise ActionError(_("No hay más diapositivas en esa dirección."))
        session.index = target
        session.phase = _phase_for(session, target)
        session.opened_at = session.closes_at = None

    elif action == "open":
        if session.phase != Phase.READY:
            raise ActionError(_("Esta diapositiva no tiene una pregunta lista para abrir."))
        seconds = int(session.slide["time_limit_s"])
        session.phase, session.opened_at = Phase.OPEN, now
        session.closes_at = now + timedelta(seconds=seconds)
        session.opened = sorted(set(session.opened) | {session.index})

    elif action == "close":
        if session.phase != Phase.OPEN:
            raise ActionError(_("La pregunta no está abierta."))
        session.phase, session.closes_at = Phase.CLOSED, now

    elif action == "reveal":
        if session.phase == Phase.OPEN:
            session.closes_at = now
        elif session.phase != Phase.CLOSED:
            raise ActionError(_("Cierre la pregunta antes de mostrar resultados."))
        session.phase = Phase.RESULTS

    elif action == "end":
        session.status, session.ended_at = Status.ENDED, now
        if session.phase == Phase.OPEN:
            session.phase, session.closes_at = Phase.CLOSED, now

    else:
        raise ActionError(_("Acción desconocida: %(action)s") % {"action": action})

    session.state_version = F("state_version") + 1
    session.save()
    session.refresh_from_db()
    return session


def expire_if_due(session_id: int) -> bool:
    """Cierra la pregunta si se acabó el tiempo. Idempotente y sin carreras.

    Lo llaman el flujo SSE y el envío de respuestas: no hace falta un proceso aparte que
    vigile los relojes, y el cierre ocurre aunque el docente no toque nada.
    """
    return bool(
        LiveSession.objects.filter(pk=session_id, phase=Phase.OPEN, closes_at__lte=timezone.now())
        .update(phase=Phase.CLOSED, state_version=F("state_version") + 1)
    )


def join(session: LiveSession, student) -> None:
    _, created = Participant.objects.get_or_create(session=session, student=student)
    if created:
        # El proyector muestra quién va entrando a la sala: una llegada es actividad que el
        # docente debe ver, igual que una respuesta.
        LiveSession.objects.filter(pk=session.pk).update(answers_version=F("answers_version") + 1)
    else:
        Participant.objects.filter(session=session, student=student).update(last_seen=timezone.now())


def submit(session_id: int, student, answer) -> Response:
    """Registra y califica una respuesta. La primera respuesta es la que cuenta."""
    expire_if_due(session_id)
    session = LiveSession.objects.get(pk=session_id)
    now = timezone.now()

    if session.status != Status.LIVE or session.phase != Phase.OPEN or not session.is_question:
        raise ActionError(_("La pregunta ya no recibe respuestas."))
    if session.closes_at and now > session.closes_at:
        raise ActionError(_("Se acabó el tiempo."))

    slide = session.slide
    result = grade(slide["question"], answer, solution=slide["solution"])
    if not result.gradable:
        raise ActionError(" ".join(result.format_errors))

    elapsed = int((now - session.opened_at).total_seconds() * 1000) if session.opened_at else 0
    try:
        with transaction.atomic():
            response = Response.objects.create(
                session=session, slide_index=session.index, student=student,
                item_id=slide["item_id"], item_version=slide["item_version"],
                answer=answer, correct=result.correct, fraction=result.fraction,
                points=result.points, elapsed_ms=max(elapsed, 0),
            )
    except IntegrityError:
        raise ActionError(_("Ya respondió esta pregunta.")) from None

    LiveSession.objects.filter(pk=session_id).update(answers_version=F("answers_version") + 1)
    return response


# ------------------------------------------------------------------------- estadísticas


def question_stats(session: LiveSession, index: int) -> dict:
    """Distribución de respuestas de una pregunta, para el proyector."""
    slide = session.slides[index]
    question = slide["question"]
    responses = list(Response.objects.filter(session=session, slide_index=index))
    total = len(responses)
    stats = {"total": total, "correct": sum(r.correct for r in responses),
             "kind": question["type"], "bars": []}

    if question["type"] in ("choice", "multi_choice"):
        correct = set(slide["solution"].get("correct") or [])
        counts = {o["key"]: 0 for o in question["options"]}
        for r in responses:
            for key in (r.answer if isinstance(r.answer, list) else [r.answer]):
                if key in counts:
                    counts[key] += 1
        stats["bars"] = [
            {"key": o["key"], "text": o["text"], "count": counts[o["key"]],
             "correct": o["key"] in correct}
            for o in question["options"]
        ]
    elif question["type"] == "true_false":
        answers = slide["solution"].get("answers") or {}
        for s in question["statements"]:
            right = sum(1 for r in responses
                        if isinstance(r.answer, dict) and r.answer.get(s["key"]) == answers.get(s["key"]))
            stats["bars"].append({"key": s["key"], "text": s["text"], "count": right,
                                  "answer": answers.get(s["key"])})
    else:
        tally: dict[str, list] = {}
        for r in responses:
            text = str(r.answer).strip()
            tally.setdefault(text, [0, r.correct])[0] += 1
        stats["bars"] = [{"text": t, "count": c, "correct": ok}
                         for t, (c, ok) in sorted(tally.items(), key=lambda kv: -kv[1][0])[:6]]
        stats["expected"] = slide["solution"].get("solution")
    return stats


def leaderboard(session: LiveSession, limit: int | None = 5) -> list[dict]:
    rows = (Response.objects.filter(session=session)
            .values("student", "student__first_name", "student__last_name", "student__username")
            .annotate(total=Sum("points"), time=Sum("elapsed_ms"))
            .order_by("-total", "time"))
    if limit:
        rows = rows[:limit]
    return [{"name": (f"{r['student__first_name']} {r['student__last_name']}".strip()
                      or r["student__username"]),
             "total": r["total"] or 0, "student": r["student"]} for r in rows]


# ---------------------------------------------------------------------- presentación


def _option_texts(question: dict) -> dict[str, str]:
    return {o["key"]: o["text"] for o in question.get("options") or []}


def correct_display(slide: dict) -> list[str]:
    """La respuesta correcta, en markdown, para mostrarla al revelar resultados."""
    question, record = slide["question"], slide["solution"]
    kind = question["type"]
    if kind in ("choice", "multi_choice"):
        texts = _option_texts(question)
        return [texts[k] for k in record.get("correct") or [] if k in texts]
    if kind == "true_false":
        answers = record.get("answers") or {}
        return [f"{_tf(answers.get(s['key']))} — {s['text']}" for s in question["statements"]]
    if kind == "text":
        accept = (record.get("grading") or {}).get("accept") or []
        return [str(accept[0])] if accept else []
    value = record.get("solution")
    return [] if value is None else [str(value)]


def answer_display(slide: dict, answer) -> list[str]:
    """Lo que respondió un estudiante, en markdown."""
    question = slide["question"]
    kind = question["type"]
    if kind in ("choice", "multi_choice"):
        texts = _option_texts(question)
        keys = answer if isinstance(answer, list) else [answer]
        return [texts.get(k, "?") for k in keys]
    if kind == "true_false" and isinstance(answer, dict):
        return [f"{_tf(answer.get(s['key']))} — {s['text']}"
                for s in question["statements"] if s["key"] in answer]
    return [str(answer)]
