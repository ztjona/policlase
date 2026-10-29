"""Las fechas y plazos de una actividad, como franjas sobre un mismo eje de tiempo.

Una fila por plazo (entregar, atrasadas, revisión, debate, notas, corrección), cada una con su
inicio y su fin (o sin fin), y una marca en «hoy». Lo dibuja `activities/_timeline.html` con
porcentajes, sin JavaScript. Lo que todavía no tiene fecha (la revisión de una evaluación antes de
publicar las notas) va como nota debajo, no como franja.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Activity, course_tz

#: Una franja nunca es más angosta que esto (en % del eje), para que se vea aunque dure minutos.
MIN_WIDTH = 1.5
#: Lo que se agrega a la derecha del último fin, para dibujar las franjas sin fin y la marca de hoy.
TAIL = 0.12


@dataclass
class Lane:
    label: str
    start: datetime
    end: datetime | None                 # None: sin fin
    tone: str                            # main · late · review · debate · correction · point
    point: bool = False                  # un instante (la publicación de las notas), no un intervalo
    left: float = 0.0
    width: float = 0.0


def _end_of(day, tz) -> datetime:
    return datetime.combine(day, time(23, 59), tzinfo=tz)


def lanes(activity: Activity, now=None, student=None) -> dict | None:
    """{"lanes", "now_at", "notes"} o None si la actividad no tiene ninguna fecha que dibujar. Con
    `student`, su propio plazo si tiene una prórroga."""
    from . import engine

    now = now or timezone.now()
    tz = course_tz(activity.course)
    opens, due = (engine.window(activity, student.pk) if student is not None
                  else (activity.opens_at, activity.due_at))
    out: list[Lane] = []
    notes: list[str] = []

    if activity.is_practice:
        if opens:
            out.append(Lane(_("Repaso disponible"), opens, due, "main"))
    elif activity.is_evaluation:
        if opens and due:
            out.append(Lane(_("Evaluación"), opens, due, "main"))
        if activity.published_at:
            out.append(Lane(_("Notas publicadas"), activity.published_at, None, "point", point=True))
            if activity.review_mode != Activity.Review.NONE:
                out.append(Lane(_("Revisión"), activity.published_at, activity.review_until, "review"))
        else:
            notes.append(_("La revisión empieza cuando el docente publique las notas."))
        correction = activity.correction_activity
        if correction is not None and correction.published and correction.opens_at:
            out.append(Lane(_("Corrección"), correction.opens_at, correction.due_at, "correction"))
            notes.append(_("Mientras pueda hacer la corrección, la revisión de la evaluación espera a que la envíe o la omita."))
    else:
        start = opens or (due - timedelta(days=7) if due else None)
        if start:
            label = _("Corrección") if activity.correction_of_id else _("Entrega")
            out.append(Lane(label, start, due, "correction" if activity.correction_of_id else "main"))
        period = activity.category.period if activity.category_id else None
        if (due and activity.late_policy == Activity.Late.PENALTY and period is not None and period.ends_on):
            close = _end_of(period.ends_on, tz)
            if close > due:
                out.append(Lane(_("Atrasadas (−%(p)d %%)") % {"p": activity.late_penalty}, due, close, "late"))
        if activity.correction_of_id:
            notes.append(_("Al enviar la corrección se abren su revisión y la de la evaluación."))
        elif due and activity.review_mode != Activity.Review.NONE:
            out.append(Lane(_("Revisión"), due, activity.review_until, "review"))
        if due and activity.debate_keys:
            out.append(Lane(_("Debate"), due, activity.debate_close, "debate"))
    if not out:
        return None

    first = min(lane.start for lane in out)
    last = max([lane.end or lane.start for lane in out] + [now if now > first else first])
    span = max((last - first).total_seconds(), 3600.0)
    edge = span * (1 + TAIL)                     # sitio para lo que no termina y para «hoy»

    def at(moment) -> float:
        return round(100 * (moment - first).total_seconds() / edge, 2)

    for lane in out:
        lane.left = min(at(lane.start), 100 - MIN_WIDTH)
        end = at(lane.end) if lane.end else 100.0
        lane.width = max(MIN_WIDTH, round(end - lane.left, 2))
    now_at = at(now) if first <= now <= first + timedelta(seconds=edge) else None
    return {"lanes": out, "now_at": now_at, "notes": notes}
