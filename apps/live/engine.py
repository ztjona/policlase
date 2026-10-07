"""Máquina de estados de una clase en vivo.

Cada transición es una actualización atómica condicionada al estado esperado, así que dos
clics del docente o un cierre automático que coincide con un clic manual no pueden dejar la
sesión en un estado imposible.

    lobby ──start──▶ live ──end──▶ ended
                      ▲  │
              resume  │  ▼ pause
                     paused ──end──▶ ended

    en una diapositiva de contenido:  content
    en una pregunta:                   ready ──open──▶ open ──close/tiempo/todos──▶ closed ──reveal──▶ results
                                                       ▲ extend                     │ extend (reabre)
                                                       └────────────────────────────┘
    retroalimentación final:           content (se responde mientras está en pantalla o tras terminar)
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import pgettext
from policlase_gen.grading import grade

from .models import Feedback, LiveSession, Participant, Response

Phase = LiveSession.Phase
Status = LiveSession.Status

#: Un participante cuenta como conectado si se lo vio hace menos que esto (el flujo SSE lo
#: renueva cada `TOUCH_EVERY_S`). Quien cerró la pestaña deja de frenar el cierre automático.
CONNECTED_WINDOW = timedelta(seconds=40)
TOUCH_EVERY_S = 10
EXTEND_S = 15
FEEDBACK_MIN_SHOWN = 3


def _tf(value) -> str:
    return pgettext("inicial de verdadero", "V") if value else pgettext("inicial de falso", "F")


class ActionError(Exception):
    """La acción no aplica al estado actual; el mensaje se le muestra al docente."""


def _phase_for(session: LiveSession, index: int) -> str:
    slide = session.slides[index]
    if slide["kind"] != "question":            # contenido y retroalimentación
        return Phase.CONTENT
    return Phase.RESULTS if index in session.opened else Phase.READY


def speed_factor(elapsed_ms: int, limit_s: float) -> float:
    """1.0 al instante, 0.5 al acabarse el tiempo (y después, si el docente lo extendió)."""
    if limit_s <= 0:
        return 1.0
    return 1.0 - 0.5 * min(1.0, max(0, elapsed_ms) / (limit_s * 1000))


def game_score(points: float, elapsed_ms: int, limit_s: float, speed_bonus: bool) -> int:
    factor = speed_factor(elapsed_ms, limit_s) if speed_bonus else 1.0
    return round(1000 * points * factor)


def _step(session: LiveSession, start: int, direction: int) -> int | None:
    """Siguiente diapositiva visible en esa dirección (las ocultas se saltan)."""
    i = start + direction
    while 0 <= i < len(session.slides):
        if not session.slides[i].get("hidden"):
            return i
        i += direction
    return None


@transaction.atomic
def apply(session_id: int, action: str, index: int | None = None) -> LiveSession:
    session = LiveSession.objects.select_for_update().get(pk=session_id)
    now = timezone.now()
    last = len(session.slides) - 1

    if session.status == Status.ENDED:
        raise ActionError(_("La clase ya terminó."))
    if session.status == Status.PAUSED and action == "primary":
        action = "resume"
    if session.status == Status.PAUSED and action not in ("resume", "end", "guests_on", "guests_off", "hide", "show"):
        raise ActionError(_("La clase está en pausa: reanúdela o termínela."))

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
        first = 0 if not session.slides[0].get("hidden") else _step(session, 0, 1)
        session.status, session.started_at, session.index = Status.LIVE, now, first or 0
        session.phase = _phase_for(session, session.index)

    elif action in ("next", "prev"):
        if session.status != Status.LIVE:
            raise ActionError(_("Inicie la clase primero."))
        if session.phase == Phase.OPEN:
            raise ActionError(_("Cierre la pregunta antes de cambiar de diapositiva."))
        target = _step(session, session.index, 1 if action == "next" else -1)
        if target is None:
            raise ActionError(_("No hay más diapositivas en esa dirección."))
        session.index = target
        session.phase = _phase_for(session, target)
        session.opened_at = session.closes_at = None

    elif action == "goto":
        # Panel lateral del proyector: saltar a cualquier diapositiva (una oculta se muestra).
        if session.status != Status.LIVE:
            raise ActionError(_("Inicie la clase primero."))
        if session.phase == Phase.OPEN:
            raise ActionError(_("Cierre la pregunta antes de cambiar de diapositiva."))
        if index is None or not 0 <= index <= last:
            raise ActionError(_("Esa diapositiva no existe."))
        session.slides[index]["hidden"] = False
        session.index = index
        session.phase = _phase_for(session, index)
        session.opened_at = session.closes_at = None

    elif action in ("hide", "show"):
        # Ocultar o mostrar sobre la marcha: solo en esta clase, la presentación no cambia.
        if index is None or not 0 <= index <= last:
            raise ActionError(_("Esa diapositiva no existe."))
        if action == "hide" and index == session.index and session.status != Status.LOBBY:
            raise ActionError(_("No se puede ocultar la diapositiva que está en pantalla."))
        session.slides[index]["hidden"] = action == "hide"

    elif action in ("speed_on", "speed_off"):
        if session.status != Status.LOBBY:
            raise ActionError(_("El bono por rapidez se elige antes de iniciar la clase."))
        session.speed_bonus = action == "speed_on"

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

    elif action == "pause":
        if session.status != Status.LIVE:
            raise ActionError(_("Solo se puede pausar una clase en curso."))
        session.status, session.paused_at = Status.PAUSED, now
        if session.phase == Phase.OPEN and session.closes_at:
            # El reloj de la pregunta abierta se congela y se devuelve al reanudar.
            left = max(0, int((session.closes_at - now).total_seconds() * 1000))
            session.paused_remaining_ms, session.closes_at = left, None

    elif action == "resume":
        if session.status != Status.PAUSED:
            raise ActionError(_("La clase no está en pausa."))
        if session.phase == Phase.OPEN:
            left = max(session.paused_remaining_ms or 0, 5000)      # al menos 5 s para releer
            session.closes_at = now + timedelta(milliseconds=left)
            if session.opened_at and session.paused_at:
                # La pausa no cuenta como tiempo de respuesta (bono por rapidez).
                session.opened_at += now - session.paused_at
        session.status, session.paused_at = Status.LIVE, None
        session.paused_remaining_ms = None

    elif action == "extend":
        if session.phase == Phase.OPEN:
            session.closes_at = max(session.closes_at or now, now) + timedelta(seconds=EXTEND_S)
        elif session.phase == Phase.CLOSED:
            # Reabrir: quienes no alcanzaron pueden responder; las respuestas ya dadas se quedan.
            session.phase, session.closes_at = Phase.OPEN, now + timedelta(seconds=EXTEND_S)
        else:
            raise ActionError(_("No hay una pregunta abierta o cerrada a la que dar más tiempo."))

    elif action in ("guests_on", "guests_off"):
        session.allow_guests = action == "guests_on"

    elif action == "reveal":
        if session.phase == Phase.OPEN:
            session.closes_at = now
        elif session.phase != Phase.CLOSED:
            raise ActionError(_("Cierre la pregunta antes de mostrar resultados."))
        session.phase = Phase.RESULTS

    elif action == "end":
        session.status, session.ended_at = Status.ENDED, now
        session.paused_at = session.paused_remaining_ms = None
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

    if session.status == Status.PAUSED:
        raise ActionError(_("La clase está en pausa."))
    if session.status != Status.LIVE or session.phase != Phase.OPEN or not session.is_question:
        raise ActionError(_("La pregunta ya no recibe respuestas."))
    if session.closes_at and now > session.closes_at:
        raise ActionError(_("Se acabó el tiempo."))

    join(session, student)          # quien responde está conectado, aunque su SSE aún no lo dijera
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
                score=game_score(result.points, elapsed, float(slide["time_limit_s"]), session.speed_bonus),
            )
    except IntegrityError:
        raise ActionError(_("Ya respondió esta pregunta.")) from None

    LiveSession.objects.filter(pk=session_id).update(answers_version=F("answers_version") + 1)
    close_if_all_answered(session_id, session.index)
    return response


def connected(session_id: int):
    return Participant.objects.filter(session_id=session_id,
                                      last_seen__gte=timezone.now() - CONNECTED_WINDOW)


def touch(session_id: int, user) -> None:
    """El flujo SSE de cada estudiante lo llama cada pocos segundos: sigue conectado."""
    Participant.objects.filter(session_id=session_id, student=user).update(last_seen=timezone.now())


def close_if_all_answered(session_id: int, index: int) -> bool:
    """Cierra la pregunta en cuanto respondieron todos los conectados.

    Condicionado a que siga abierta y en la misma diapositiva: si el docente ya avanzó o el
    tiempo la cerró, no hace nada.
    """
    answered = set(Response.objects.filter(session_id=session_id, slide_index=index)
                   .values_list("student_id", flat=True))
    waiting = set(connected(session_id).values_list("student_id", flat=True)) - answered
    if not answered or waiting:
        return False
    return bool(
        LiveSession.objects.filter(pk=session_id, phase=Phase.OPEN, index=index, status=Status.LIVE)
        .update(phase=Phase.CLOSED, closes_at=timezone.now(), state_version=F("state_version") + 1)
    )


def can_give_feedback(session: LiveSession, student) -> bool:
    if not session.has_feedback:
        return False
    if not (session.is_feedback and session.status == Status.LIVE) and session.status != Status.ENDED:
        return False
    return Participant.objects.filter(session=session, student=student, feedback_given=False).exists()


@transaction.atomic
def submit_feedback(session_id: int, student, rating: int, comment: str) -> None:
    """Guarda la retroalimentación sin vínculo con quien la dio."""
    session = LiveSession.objects.get(pk=session_id)
    if not session.has_feedback or (
            not (session.is_feedback and session.status == Status.LIVE) and session.status != Status.ENDED):
        raise ActionError(_("La retroalimentación no está abierta."))
    if not 1 <= rating <= 5:
        raise ActionError(_("Elija una valoración de 1 a 5."))
    marked = Participant.objects.filter(session=session, student=student, feedback_given=False) \
        .update(feedback_given=True)
    if not marked:
        raise ActionError(_("Ya dejó su retroalimentación. ¡Gracias!"))
    Feedback.objects.create(session=session, rating=rating, comment=comment.strip()[:1000])
    LiveSession.objects.filter(pk=session_id).update(answers_version=F("answers_version") + 1)


def feedback_summary(session: LiveSession) -> dict:
    """Resumen para el docente. Con menos de FEEDBACK_MIN_SHOWN respuestas no se muestra nada:
    en un grupo pequeño, el detalle permitiría adivinar quién respondió qué."""
    import random

    rows = list(Feedback.objects.filter(session=session).values_list("rating", "comment"))
    summary = {"count": len(rows), "shown": len(rows) >= FEEDBACK_MIN_SHOWN, "minimum": FEEDBACK_MIN_SHOWN}
    if summary["shown"]:
        counts = {n: 0 for n in range(1, 6)}
        for rating, _comment in rows:
            counts[rating] += 1
        summary["average"] = sum(r for r, _c in rows) / len(rows)
        summary["bars"] = [{"rating": n, "count": counts[n]} for n in range(5, 0, -1)]
        comments = [c for _r, c in rows if c]
        random.shuffle(comments)                # el orden de llegada también podría delatar
        summary["comments"] = comments
    return summary


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
            .annotate(total=Sum("score"), time=Sum("elapsed_ms"))
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
