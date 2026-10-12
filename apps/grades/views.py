from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from apps.courses.access import can_view_course, owned_course, teacher_required
from apps.courses.models import Course, Enrollment

from . import gradebook, matching
from .forms import CategoryFormSet, ManualRecordForm, PeriodForm, RulesForm
from .models import Category, ManualRecord, ManualScore, Period


def _period_forms(course, data=None):
    out = []
    for period in Period.objects.filter(course=course):
        out.append({
            "period": period,
            "form": PeriodForm(data, instance=period, prefix=f"p{period.pk}"),
            "rows": CategoryFormSet(data, instance=period, prefix=f"c{period.pk}",
                                    queryset=period.categories.all()),
        })
    return out


def _sums(period) -> tuple[Decimal, Decimal]:
    rows = list(period.categories.all())
    return (sum((c.weight for c in rows if not c.optional), Decimal(0)),
            sum((c.weight for c in rows if c.optional), Decimal(0)))


@teacher_required
def grading_settings(request, pk):
    """Ponderaciones de cada parcial (obligatorias y opcionales) y reglas de aprobación."""
    course = owned_course(request, pk)
    rules = gradebook.ensure_defaults(course)
    data = request.POST if request.method == "POST" else None
    rules_form = RulesForm(data, instance=rules, prefix="rules")
    periods = _period_forms(course, data)

    if request.method == "POST":
        valid = rules_form.is_valid() and all(p["form"].is_valid() and p["rows"].is_valid() for p in periods)
        if valid:
            with transaction.atomic():
                rules_form.save()
                for p in periods:
                    p["form"].save()
                    rows = p["rows"].save(commit=False)
                    for row in p["rows"].deleted_objects:
                        row.delete()
                    for extra, row in enumerate(rows):
                        if row.pk is None:
                            row.position = 10_000 + extra       # lo nuevo, al final de la tabla
                        row.save()
                    # El orden de la tabla es el de la página: lo nuevo queda al final.
                    for position, row in enumerate(p["period"].categories.order_by("position", "pk")):
                        if row.position != position:
                            Category.objects.filter(pk=row.pk).update(position=position)
            messages.success(request, _("Ponderaciones guardadas."))
            moved = gradebook.rebind_all(course)
            if moved:
                messages.info(request, ngettext(
                    "Con las fechas nuevas, %(n)d actividad o registro pasó a la fila de su parcial.",
                    "Con las fechas nuevas, %(n)d actividades o registros pasaron a la fila de su parcial.", moved)
                    % {"n": moved})
            for period in Period.objects.filter(course=course):
                required, _extra = _sums(period)
                if required != 100:
                    messages.warning(request, _("En «%(period)s» las filas obligatorias suman %(sum)s %%, no 100 %%.")
                                     % {"period": period.name, "sum": f"{required.normalize():f}"})
            return redirect("grading_settings", pk=pk)
        messages.error(request, _("Revise los campos marcados."))

    for p in periods:
        p["required"], p["extra"] = _sums(p["period"])
    return render(request, "grades/settings.html", {
        "course": course, "rules_form": rules_form, "periods": periods,
    })


@teacher_required
@require_POST
def period_add(request, pk):
    course = owned_course(request, pk)
    gradebook.ensure_defaults(course)
    period = gradebook.add_period(course)
    messages.success(request, _("Se agregó «%(name)s» con la tabla de ponderaciones por omisión.")
                     % {"name": period.name})
    return redirect("grading_settings", pk=pk)


@teacher_required
@require_POST
def period_delete(request, pk, period_pk):
    course = owned_course(request, pk)
    period = get_object_or_404(Period, pk=period_pk, course=course)
    if Period.objects.filter(course=course).count() <= 1:
        messages.error(request, _("El curso necesita al menos un parcial."))
    else:
        name = period.name
        period.delete()
        messages.success(request, _("Se eliminó «%(name)s». Sus actividades quedaron sin ponderación.")
                         % {"name": name})
    return redirect("grading_settings", pk=pk)


@teacher_required
def course_gradebook(request, pk):
    course = owned_course(request, pk)
    students = [e.student for e in Enrollment.objects.filter(course=course, status=Enrollment.Status.APPROVED)
                .select_related("student")]
    grades = gradebook.course_grades(course, students)
    periods = list(Period.objects.filter(course=course))
    # Un parcial a la vez: con dos parciales de seis componentes, todo junto no cabe en pantalla.
    chosen = next((p for p in periods if str(p.pk) == request.GET.get("parcial")), None)
    index = periods.index(chosen) if chosen else None
    return render(request, "grades/gradebook.html", {
        "course": course, "grades": grades, "rules": course.grading_rules, "periods": periods,
        "chosen": chosen,
        "detail": [(g, g.periods[index]) for g in grades] if chosen else [],
        "header": grades[0].periods[index].cells if grades and chosen else [],
    })


@login_required
def my_grades(request, pk):
    """Lo que ve el estudiante: la ponderación de cada parcial y su acumulado."""
    course = get_object_or_404(Course.objects.select_related("owner"), pk=pk)
    if not can_view_course(request.user, course):
        raise Http404
    mine = gradebook.course_grades(course, [request.user])[0]
    return render(request, "grades/mine.html", {"course": course, "grades": mine, "rules": mine.rules})


# ------------------------------------------------------------------------ registros manuales


def _number(raw: str):
    """Lo que el docente escribió en una casilla de nota: vacío, un número (con coma o punto) o
    algo inválido (None)."""
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return ""
    try:
        return Decimal(raw)
    except ArithmeticError:
        return None


def _students(course):
    return [e.student for e in Enrollment.objects.filter(course=course, status=Enrollment.Status.APPROVED)
            .select_related("student").order_by("student__last_name", "student__first_name")]


@teacher_required
def manual_record(request, pk, record_pk=None):
    """Registrar notas de algo hecho fuera de policlase. Una sola página: los datos del registro,
    una casilla por estudiante y, para no escribir nota por nota, pegar una lista «nombre puntaje»
    que se empareja con los inscritos. Pegar solo rellena las casillas: nada se guarda hasta
    «Guardar», para que el docente revise.

    Cada nota recuerda de qué nombre de la lista salió (`sources`): «Quitar» la devuelve a «Sin
    asignar», y la que coincide solo por el apellido se guarda únicamente si el docente la confirma."""
    course = owned_course(request, pk)
    gradebook.ensure_defaults(course)
    record = get_object_or_404(ManualRecord, pk=record_pk, course=course) if record_pk else None
    students = _students(course)
    action = request.POST.get("action", "save") if request.method == "POST" else ""
    initial = None if record else {"on": timezone.localdate()}
    form = ManualRecordForm(request.POST or None, instance=record, course=course, initial=initial)
    values = {s.pk: "" for s in students}
    sources: dict[int, dict] = {}
    for score in record.scores.all() if record else []:
        values[score.student_id] = score.points
        if score.pasted_name:
            sources[score.student_id] = {"name": score.pasted_name}
    unmatched = list(record.unmatched) if record else []
    report = None

    if request.method == "POST":
        values = {s.pk: request.POST.get(f"s-{s.pk}", "").strip() for s in students}
        sources = _posted_sources(request, students)
        unmatched = _posted_unmatched(request)
    if action == "paste":
        rows, bad = matching.parse_lines(request.POST.get("pasted", ""))
        matches = matching.match_all(rows, students)
        for m in matches:
            if m.student:
                values[m.student.pk] = f"{m.score.normalize():f}"
                sources[m.student.pk] = {"name": m.name, "weak": m.strength == matching.WEAK}
        known = {(u["name"], u["points"]) for u in unmatched}
        for m in matches:
            if not m.student and (m.name, f"{m.score.normalize():f}") not in known:
                unmatched.append({"name": m.name, "points": f"{m.score.normalize():f}", "also": m.also})
        report = {"matched": sum(1 for m in matches if m.student),
                  "weak": sum(1 for m in matches if m.strength == matching.WEAK),
                  "unmatched": sum(1 for m in matches if not m.student), "bad": bad,
                  "top": max((m.score for m in matches), default=None)}
        limit = _number(request.POST.get("max_points", ""))
        report["over"] = bool(report["top"] and isinstance(limit, Decimal) and report["top"] > limit)
    elif action.startswith("quitar-"):
        # Deshacer una asignación: la línea vuelve a «Sin asignar», sin sugerir a la misma persona.
        target = action.removeprefix("quitar-")
        source = sources.pop(int(target), None) if target.isdigit() else None
        if source:
            points = _number(str(values[int(target)]))
            if isinstance(points, Decimal):
                unmatched.append({"name": source["name"], "points": f"{points.normalize():f}", "also": "",
                                  "suggest": ""})
            values[int(target)] = ""
    elif action == "save":
        response = _save_record(request, course, form, record, students, values, sources, unmatched)
        if response:
            return response

    # Sugerencias para lo que sigue sin asignar (estudiantes que se inscribieron después).
    assigned = {pk for pk, v in values.items() if v not in ("", None)}
    for u in unmatched:
        if "suggest" not in u:
            student, strength, _weight = matching.best(u["name"], [s for s in students if s.pk not in assigned])
            u["suggest"] = student.pk if student and strength == matching.STRONG else ""
    rows = []
    for s in students:
        value = values.get(s.pk, "")
        rows.append({"student": s, "value": value if not isinstance(value, Decimal) else f"{value.normalize():f}",
                     "source": sources.get(s.pk)})
    return render(request, "grades/record.html", {
        "course": course, "record": record, "form": form, "rows": rows, "unmatched": unmatched,
        "report": report, "students": students, "pending": action == "paste" or action.startswith("quitar-"),
    })


def _posted_sources(request, students) -> dict[int, dict]:
    """De qué nombre de la lista salió cada nota; viaja en el formulario mientras no se guarda."""
    out = {}
    for s in students:
        name = request.POST.get(f"p-{s.pk}", "")[:200]
        if name:
            out[s.pk] = {"name": name, "weak": bool(request.POST.get(f"p-{s.pk}-weak")),
                         "confirmed": bool(request.POST.get(f"c-{s.pk}"))}
    return out


def _posted_unmatched(request) -> list[dict]:
    """Los nombres sin asignar viajan en el formulario mientras no se guardan."""
    out = []
    for i in range(int(request.POST.get("unmatched-count") or 0)):
        name = request.POST.get(f"u-{i}-name", "")[:200]
        points = _number(request.POST.get(f"u-{i}-points", ""))
        if name and isinstance(points, Decimal):
            out.append({"name": name, "points": f"{points.normalize():f}", "also": request.POST.get(f"u-{i}-also", "")[:200],
                        "suggest": request.POST.get(f"u-{i}", ""),
                        "discard": bool(request.POST.get(f"u-{i}-discard"))})
    return out


def _save_record(request, course, form, record, students, values, sources, unmatched):
    by_pk = {s.pk: s for s in students}
    if not form.is_valid():
        messages.error(request, _("Revise los campos marcados."))
        return None
    top = form.cleaned_data["max_points"]
    scores, errors, keep, unconfirmed = {}, [], [], 0
    for pk, raw in values.items():
        value = raw if isinstance(raw, Decimal) else _number(str(raw))
        if value == "":
            continue
        if value is None or value < 0 or value > top:
            errors.append(by_pk[pk].display_name)
            continue
        source = sources.get(pk, {})
        if source.get("weak") and not source.get("confirmed"):
            # Coincidía solo el apellido y nadie lo confirmó: no se adivina una nota.
            keep.append({"name": source["name"], "points": f"{value.normalize():f}", "also": ""})
            unconfirmed += 1
            continue
        scores[pk] = (value, source.get("name", ""))
    for u in unmatched:
        target = int(u["suggest"]) if str(u.get("suggest", "")).isdigit() else None
        if u.get("discard"):
            continue
        if target in by_pk:
            points = Decimal(u["points"])
            if points < 0 or points > top:
                errors.append(by_pk[target].display_name)
            else:
                scores[target] = (points, u["name"])
            continue
        keep.append({"name": u["name"], "points": u["points"], "also": u.get("also", "")})
    if errors:
        messages.error(request, _("Hay notas fuera de rango (deben estar entre 0 y el puntaje máximo, %(top)s): "
                                  "%(names)s.") % {"top": f"{top.normalize():f}", "names": ", ".join(errors)})
        return None
    with transaction.atomic():
        record = form.save(commit=False)
        record.course = course
        record.unmatched = keep
        rebound = gradebook.rebind(record)
        record.save()
        record.scores.exclude(student_id__in=scores).delete()
        for pk, (points, name) in scores.items():
            ManualScore.objects.update_or_create(record=record, student_id=pk,
                                                 defaults={"points": points, "pasted_name": name})
    messages.success(request, ngettext("«%(title)s» guardado: %(n)d nota.", "«%(title)s» guardado: %(n)d notas.",
                                       len(scores)) % {"title": record.title, "n": len(scores)})
    if rebound:
        messages.info(request, _("La fecha corresponde a otro parcial; el registro cuenta en «%(row)s».")
                      % {"row": record.category})
    if unconfirmed:
        messages.warning(request, ngettext(
            "%(n)d asignación sin confirmar pasó a «Sin asignar».",
            "%(n)d asignaciones sin confirmar pasaron a «Sin asignar».", unconfirmed) % {"n": unconfirmed})
    elif keep:
        messages.warning(request, ngettext("Queda %(n)d nombre sin asignar.", "Quedan %(n)d nombres sin asignar.",
                                           len(keep)) % {"n": len(keep)})
    return redirect("manual_record", pk=course.pk, record_pk=record.pk)


@teacher_required
@require_POST
def manual_record_delete(request, pk, record_pk):
    course = owned_course(request, pk)
    record = get_object_or_404(ManualRecord, pk=record_pk, course=course)
    title = record.title
    record.delete()
    messages.success(request, _("«%(title)s» eliminado, con sus notas.") % {"title": title})
    return redirect("course_activities", pk=course.pk)
