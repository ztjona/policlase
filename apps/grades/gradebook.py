"""Ponderaciones por omisión y cálculo de notas.

La nota de un parcial es la suma, sobre las filas de su tabla, de

    fracción obtenida en la fila × peso de la fila × nota máxima del parcial

topada en la nota máxima: las filas opcionales suman encima de las obligatorias y compensan lo que
falte, pero un parcial no pasa de 20. La fracción de una fila es el promedio de sus actividades
(o de las clases en vivo del parcial, para la participación) y de sus registros manuales:

- una actividad cuenta desde que vence, o desde que el estudiante la entregó; vencida y sin
  entregar, vale cero. La que cuenta es la última entrega.
- una clase en vivo terminada cuenta para todos los inscritos: faltar vale cero. Si el docente le
  puso una nota manual a un estudiante en esa clase (`SessionGrade`), cuenta esa.
- un registro manual (`ManualRecord`) cuenta desde que existe: quien no tiene nota registrada,
  cero.

Las unidades (secciones) y los parciales son ejes distintos: una unidad puede repartirse entre dos
parciales. El parcial de una actividad o de un registro lo decide su fecha (`rebind`).

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


def dates_known(periods: list[Period]) -> bool:
    """¿Se puede saber el parcial de una fecha? Hace falta el cierre de todos menos el último."""
    return bool(periods) and all(p.ends_on for p in periods[:-1])


def day_of(content):
    """La fecha que ubica en un parcial a una actividad (su vencimiento, en la zona del curso) o a
    un registro manual (su fecha)."""
    if hasattr(content, "due_at"):
        if not content.due_at:
            return None
        from apps.activities.models import course_tz
        return content.due_at.astimezone(course_tz(content.course)).date()
    return content.on


def rebind(content, periods: list[Period] | None = None) -> bool:
    """La ponderación sigue a la fecha: si la actividad (o el registro) cae en otro parcial que su
    fila, pasa a la fila del mismo nombre en ese parcial, si existe. No guarda; dice si cambió."""
    category, day = content.category, day_of(content)
    if category is None or day is None:
        return False
    if periods is None:
        periods = list(Period.objects.filter(course_id=content.course_id))
    if not dates_known(periods):
        return False
    period = period_for(periods, day)
    if period is None or period.pk == category.period_id:
        return False
    twin = Category.objects.filter(period=period, name__iexact=category.name).first()
    if twin is None:
        return False
    content.category = twin
    return True


def mismatch(content, periods: list[Period]) -> Period | None:
    """El parcial de su fecha cuando no es el de su fila de ponderación (no había fila del mismo
    nombre a dónde pasarla): para avisarle al docente."""
    day = day_of(content)
    if content.category_id is None or day is None or not dates_known(periods):
        return None
    period = period_for(periods, day)
    return period if period and period.pk != content.category.period_id else None


def rebind_all(course) -> int:
    """Tras cambiar las fechas de los parciales: cada actividad y registro, a su parcial."""
    from apps.activities.models import Activity

    from .models import ManualRecord

    periods = list(Period.objects.filter(course=course))
    count = 0
    for model in (Activity, ManualRecord):
        for content in model.objects.filter(course=course, category__isnull=False).select_related(
                "category", "course__owner"):
            if rebind(content, periods):
                content.save(update_fields=["category"])
                count += 1
    return count


def recompute_due_dates(milestone) -> int:
    """Una fecha clave cambió: sus actividades vencen de nuevo en relación con ella (y, si cruzan
    al otro parcial, cuentan en él)."""
    count = 0
    periods = list(Period.objects.filter(course_id=milestone.course_id))
    for activity in milestone.activities.select_related("course__owner", "due_milestone", "category"):
        activity.compute_due()
        rebind(activity, periods)
        activity.save(update_fields=["due_at", "category"])
        count += 1
    return count


# ------------------------------------------------------------------------ cálculo


@dataclass
class Item:
    """Una clase, actividad o registro que cuenta en una fila: el detalle que ve el estudiante."""

    kind: str                            # "live" | "activity" | "record"
    title: str
    day: object                          # date o None
    got: float                           # puntos obtenidos (cero si faltó o no entregó)
    top: float                           # puntos posibles
    note: str = ""                       # «sin entrega», «ausente», «por revisar»…

    @property
    def percent(self) -> int:
        return round(100 * min(1.0, self.got / self.top)) if self.top else 0


@dataclass
class Cell:
    category: Category
    fraction: float | None = None        # promedio de lo que cuenta; None si aún no hay nada
    counted: int = 0
    pending: int = 0                     # entregas con preguntas por revisar
    scale: float = 20.0
    items: list[Item] = field(default_factory=list)

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
    from apps.live.models import LiveSession, Participant, Response, SessionGrade

    from .models import ManualRecord, ManualScore

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
    present = set(Participant.objects.filter(session__in=sessions, student_id__in=student_ids)
                  .values_list("session_id", "student_id"))
    for r in Response.objects.filter(session__in=sessions, student_id__in=student_ids):
        if r.slide_index in asked[r.session_id]:
            live_points[(r.session_id, r.student_id)] = live_points.get((r.session_id, r.student_id), 0) + r.points
    for g in SessionGrade.objects.filter(session__in=sessions, student_id__in=student_ids):
        live_points[(g.session_id, g.student_id)] = g.points          # la nota manual reemplaza la suma
    sessions_in: dict[int, list] = {}
    for s in sessions:
        when = timezone.localdate(s.started_at or s.created_at)
        period = period_for(periods, when)
        if period:
            sessions_in.setdefault(period.pk, []).append(s)

    # Registros manuales: cada uno cuenta como una actividad (o una clase) más de su fila.
    records = list(ManualRecord.objects.filter(course=course, category__isnull=False))
    scores = {(m.record_id, m.student_id): float(m.points)
              for m in ManualScore.objects.filter(record__in=records, student_id__in=student_ids)}
    records_in: dict[int, list] = {}
    for record in records:
        records_in.setdefault(record.category_id, []).append(record)

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
                        got = live_points.get((s.pk, student.pk), 0.0)
                        values.append(got / s.max_points)
                        absent = (s.pk, student.pk) not in present and (s.pk, student.pk) not in live_points
                        cell.items.append(Item("live", s.title, timezone.localdate(s.started_at or s.created_at),
                                               got, s.max_points, _("ausente") if absent else ""))
                else:
                    for activity in by_category.get(category.pk, []):
                        attempt = last.get((activity.pk, student.pk))
                        day = day_of(activity)
                        if attempt is not None:
                            values.append(attempt.fraction or 0.0)
                            cell.pending += 1 if attempt.pending else 0
                            cell.items.append(Item("activity", activity.title, day, attempt.points or 0.0,
                                                   attempt.max_points, _("por revisar") if attempt.pending else ""))
                        elif activity.is_past_due(now):
                            values.append(0.0)
                            cell.items.append(Item("activity", activity.title, day, 0.0, activity.points,
                                                   _("sin entrega")))
                for record in records_in.get(category.pk, []):
                    top = float(record.max_points)
                    got = scores.get((record.pk, student.pk))
                    values.append(min(1.0, (got or 0.0) / top) if top > 0 else 0.0)
                    cell.items.append(Item("record", record.title, record.on, got or 0.0, top,
                                           _("sin nota registrada") if got is None else ""))
                cell.items.sort(key=lambda i: (i.day is None, i.day or today))
                if values:
                    cell.fraction, cell.counted = sum(values) / len(values), len(values)
                pg.cells.append(cell)
            grades.append(pg)
        out.append(StudentGrades(student=student, periods=grades, rules=rules, finished=finished))
    return out

