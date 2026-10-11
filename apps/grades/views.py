from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.courses.access import can_view_course, owned_course, teacher_required
from apps.courses.models import Course, Enrollment

from . import gradebook
from .forms import CategoryFormSet, PeriodForm, RulesForm
from .models import Category, Period


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
