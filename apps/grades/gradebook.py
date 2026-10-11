"""Ponderaciones por omisión y cálculo de notas.

La nota de un parcial es la suma, sobre las filas de su tabla, de

    fracción obtenida en la fila × peso de la fila × nota máxima del parcial

topada en la nota máxima: las filas opcionales suman encima de las obligatorias y compensan lo que
falte, pero un parcial no pasa de 20. La fracción de una fila es el promedio de sus actividades
(o de las clases en vivo del parcial, para la participación):

- una actividad cuenta desde que vence, o desde que el estudiante la entregó; vencida y sin
  entregar, vale cero. La que cuenta es la última entrega.
- una clase en vivo terminada cuenta para todos los inscritos: faltar vale cero.

Una fila sin nada que contar todavía no aporta. Por eso lo que se muestra durante el semestre es
el **acumulado**: lo ganado hasta hoy y lo que estaba en juego hasta hoy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Category, GradingRules, Period

Source = Category.Source


def default_rows() -> list[tuple[str, int, bool, str]]:
    """(nombre, peso, opcional, origen). Las obligatorias suman 100; los extras van encima."""
    return [
        (_("Prueba"), 35, False, Source.WORK),
        (_("Examen"), 35, False, Source.WORK),
        (_("Deberes"), 5, False, Source.WORK),
        (_("Proyecto"), 25, False, Source.WORK),
        (_("Participación en clase"), 5, True, Source.LIVE),
        (_("Actividades extracurriculares"), 5, True, Source.WORK),
    ]


@transaction.atomic
def ensure_defaults(course) -> GradingRules:
    """La primera vez: reglas, dos parciales y su tabla de ponderaciones por omisión."""
    rules, _created = GradingRules.objects.get_or_create(course=course)
    if not Period.objects.filter(course=course).exists():
        for position in range(2):
            add_period(course, position)
    return rules


def add_period(course, position: int | None = None) -> Period:
    if position is None:
        position = Period.objects.filter(course=course).count()
    period = Period.objects.create(course=course, position=position,
                                   name=_("Parcial %(n)d") % {"n": position + 1})
    for index, (name, weight, optional, source) in enumerate(default_rows()):
        Category.objects.create(period=period, name=name, weight=Decimal(weight), optional=optional,
                                source=source, position=index)
    return period


def period_for(periods: list[Period], day) -> Period | None:
    """El parcial de una fecha: el primero que no había cerrado ese día. Sin fechas de cierre,
    todo cae en el primero; después del último cierre, en el último."""
    for period in periods:
        if period.ends_on is None or day <= period.ends_on:
            return period
    return periods[-1] if periods else None


def recompute_due_dates(milestone) -> int:
    """Una fecha clave cambió: sus actividades vencen de nuevo en relación con ella."""
    count = 0
    for activity in milestone.activities.select_related("course__owner", "due_milestone"):
        activity.compute_due()
        activity.save(update_fields=["due_at"])
        count += 1
    return count


# ------------------------------------------------------------------------ cálculo


@dataclass
class Cell:
    category: Category
    fraction: float | None = None        # promedio de lo que cuenta; None si aún no hay nada
    counted: int = 0
    pending: int = 0                     # entregas con preguntas por revisar
    scale: float = 20.0

    @property
    def weight(self) -> float:
        return float(self.category.weight)

    @property
    def points(self) -> float:
        """Lo que aporta al parcial, sobre su nota máxima."""
        return round((self.fraction or 0.0) * self.weight / 100 * self.scale, 2)

    @property
    def max_points(self) -> float:
        return round(self.weight / 100 * self.scale, 2)


@dataclass
class PeriodGrade:
    period: Period
    cells: list[Cell] = field(default_factory=list)
    scale: float = 20.0

    def _sum(self, cells) -> float:
        return sum((c.fraction or 0.0) * c.weight / 100 * self.scale for c in cells)

    @property
    def raw(self) -> float:
        return round(self._sum(self.cells), 2)

    @property
    def total(self) -> float:
        """Lo acumulado, topado en la nota máxima del parcial."""
        return round(min(self.scale, self._sum(self.cells)), 2)

    @property
    def at_stake(self) -> float:
        """Lo que estuvo en juego hasta hoy en las filas obligatorias."""
        return round(sum(c.weight / 100 * self.scale for c in self.cells
                         if c.fraction is not None and not c.category.optional), 2)

    @property
    def capped(self) -> bool:
        return self.raw > self.scale


@dataclass
class StudentGrades:
    student: object
    periods: list[PeriodGrade]
    rules: GradingRules
    finished: bool                        # todos los parciales cerraron

    @property
    def total(self) -> float:
        return round(sum(p.total for p in self.periods), 2)

    @property
    def status(self) -> str | None:
        """approved | makeup | failed, solo cuando cerraron todos los parciales."""
        if not self.finished:
            return None
        if self.total < float(self.rules.fail_below):
            return "failed"
        if self.total >= float(self.rules.pass_from):
            return "approved"
        return "makeup"

    @property
    def makeup_needed(self) -> float:
        """Nota de supletorio necesaria: lo que falta para la suma mínima, y nunca menos del mínimo."""
        return round(max(float(self.rules.makeup_min), float(self.rules.makeup_total) - self.total), 2)


def course_grades(course, students, now=None) -> list[StudentGrades]:
    """Notas de cada estudiante en cada parcial. Pocas consultas: todo se arma en memoria."""
    from apps.activities.models import Activity, Attempt
    from apps.live.models import LiveSession, Response

    now = now or timezone.now()
    rules = ensure_defaults(course)
    periods = list(Period.objects.filter(course=course).prefetch_related("categories"))
    student_ids = [s.pk for s in students]
    today = timezone.localdate(now)
    finished = bool(periods) and all(p.ends_on and p.ends_on < today for p in periods)

    # Actividades: la fracción de la última entrega de cada estudiante.
    activities = list(Activity.objects.filter(course=course, published=True, category__isnull=False))
    last: dict[tuple[int, int], Attempt] = {}
    for a in (Attempt.objects.filter(activity__in=activities, student_id__in=student_ids,
                                     submitted_at__isnull=False).order_by("number")):
        last[(a.activity_id, a.student_id)] = a
    by_category: dict[int, list] = {}
    for activity in activities:
        by_category.setdefault(activity.category_id, []).append(activity)

    # Clases en vivo terminadas, por parcial: puntos de cada estudiante sobre lo preguntado.
    sessions = [s for s in LiveSession.objects.filter(course=course, status=LiveSession.Status.ENDED)
                if s.max_points > 0]
    asked = {s.pk: set(s.asked_indices) for s in sessions}
    live_points: dict[tuple[int, int], float] = {}
    for r in Response.objects.filter(session__in=sessions, student_id__in=student_ids):
        if r.slide_index in asked[r.session_id]:
            live_points[(r.session_id, r.student_id)] = live_points.get((r.session_id, r.student_id), 0) + r.points
    sessions_in: dict[int, list] = {}
    for s in sessions:
        when = timezone.localdate(s.started_at or s.created_at)
        period = period_for(periods, when)
        if period:
            sessions_in.setdefault(period.pk, []).append(s)

    out = []
    for student in students:
        grades = []
        for period in periods:
            pg = PeriodGrade(period=period, scale=float(rules.period_max))
            for category in period.categories.all():
                cell = Cell(category=category, scale=pg.scale)
                values = []
                if category.source == Source.LIVE:
                    for s in sessions_in.get(period.pk, []):
                        values.append(live_points.get((s.pk, student.pk), 0.0) / s.max_points)
                else:
                    for activity in by_category.get(category.pk, []):
                        attempt = last.get((activity.pk, student.pk))
                        if attempt is not None:
                            values.append(attempt.fraction or 0.0)
                            cell.pending += 1 if attempt.pending else 0
                        elif activity.is_past_due(now):
                            values.append(0.0)
                if values:
                    cell.fraction, cell.counted = sum(values) / len(values), len(values)
                pg.cells.append(cell)
            grades.append(pg)
        out.append(StudentGrades(student=student, periods=grades, rules=rules, finished=finished))
    return out

