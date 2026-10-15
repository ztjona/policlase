import json
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from apps.courses.access import is_approved, owned_course, teacher_required
from apps.courses.models import Course, Enrollment
from apps.grades import gradebook
from apps.grades.forms import MilestoneForm
from apps.grades.models import Milestone, Period

from . import engine, images, outline
from .forms import ActivitySettingsForm
from .models import Activity, Answer, Attempt, Upload

def _course_link(course):
    from apps.github.models import CourseRepo
    return CourseRepo.objects.filter(course=course).select_related("course__owner").first()


def _approved(course):
    return [e.student for e in Enrollment.objects.filter(course=course, status=Enrollment.Status.APPROVED)
            .select_related("student")]


# -------------------------------------------------------------------- docente: contenido


@teacher_required
@require_POST
def activity_delete(request, course_pk, pk):
    course = owned_course(request, course_pk)
    activity = get_object_or_404(Activity, pk=pk, course=course)
    link = _course_link(course)
    if link and activity.github_path:
        from apps.github import sync
        try:
            sync.delete(link, activity)
        except sync.GitHubError as exc:
            messages.error(request, _("No se eliminó: %(error)s") % {"error": exc})
            return redirect("activity_detail", course_pk=course.pk, pk=activity.pk)
    title = activity.title
    activity.delete()
    if link and activity.github_path:
        messages.success(request, _("Actividad «%(title)s» eliminada, con sus entregas; su archivo se borró de "
                                    "GitHub.") % {"title": title})
    else:
        messages.success(request, _("Actividad «%(title)s» eliminada, con sus entregas.") % {"title": title})
    return redirect("course_activities", pk=course.pk)


@teacher_required
@require_POST
def activity_move(request, course_pk, pk):
    """Arrastrarla a otra sección en la pestaña Clases. Con GitHub, el archivo cambia de carpeta en
    un solo commit (y solo si allá sigue como lo conocemos)."""
    from apps.live.models import Section

    course = owned_course(request, course_pk)
    activity = get_object_or_404(Activity, pk=pk, course=course)
    wanted = request.POST.get("section", "")
    section = get_object_or_404(Section, pk=wanted, course=course) if wanted else None
    if section == activity.section:
        return JsonResponse({"ok": True})
    activity.section = section
    link = _course_link(course)
    if link and activity.github_path:
        from apps.github import sync
        try:
            activity.github_path, activity.github_sha = sync.push(link, activity, activity.source)
        except sync.GitHubError as exc:
            return JsonResponse({"error": _("No se movió: %(error)s") % {"error": exc}}, status=409)
    activity.save(update_fields=["section", "github_path", "github_sha"])
    return JsonResponse({"ok": True})


# -------------------------------------------------------------------- docente: configuración y resultados


def _results(activity, students) -> list[dict]:
    counted = engine.counted_attempts(activity)
    tries: dict[int, int] = {}
    drafts: dict[int, Attempt] = {}
    for a in Attempt.objects.filter(activity=activity).only("pk", "student_id", "submitted_at", "telemetry"):
        if a.submitted_at:
            tries[a.student_id] = tries.get(a.student_id, 0) + 1
        else:
            drafts[a.student_id] = a
    rows = []
    for student in students:
        attempt = counted.get(student.pk)
        rows.append({"student": student, "attempt": attempt, "tries": tries.get(student.pk, 0),
                     "working": student.pk in drafts, "draft": drafts.get(student.pk),
                     "flags": _flags(attempt.telemetry) if attempt else []})
    rows.sort(key=lambda r: (r["attempt"] is None, r["student"].last_name, r["student"].first_name))
    return rows


def _flags(telemetry: dict) -> list[str]:
    """Señales para el docente. Ninguna es prueba de nada: piden una conversación, no una sanción."""
    out = []
    if telemetry.get("pastes"):
        out.append(ngettext("pegó texto %(n)d vez (%(chars)d caracteres)",
                            "pegó texto %(n)d veces (%(chars)d caracteres)", telemetry["pastes"])
                   % {"n": telemetry["pastes"], "chars": telemetry.get("pasted_chars", 0)})
    if telemetry.get("blocked_pastes"):
        out.append(ngettext("intentó pegar %(n)d vez", "intentó pegar %(n)d veces", telemetry["blocked_pastes"])
                   % {"n": telemetry["blocked_pastes"]})
    if telemetry.get("blurs"):
        out.append(ngettext("salió de la página %(n)d vez", "salió de la página %(n)d veces", telemetry["blurs"])
                   % {"n": telemetry["blurs"]})
    return out


def _titles(activity) -> dict[str, str]:
    """Título de cada ítem en la versión actual: los intentos empezados antes de que el título se
    compilara lo toman de aquí."""
    return {i["item_id"]: i.get("title", "") for i in activity.compiled.get("items", []) if i.get("item_id")}


@teacher_required
def activity_detail(request, course_pk, pk):
    course = owned_course(request, course_pk)
    activity = get_object_or_404(Activity.objects.select_related("category__period", "due_milestone", "course__owner"),
                                 pk=pk, course=course)
    gradebook.ensure_defaults(course)
    engine.finalize_overdue(activity)
    form = ActivitySettingsForm(request.POST or None, instance=activity, course=course)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, _("Configuración guardada."))
            if form.rebound:
                messages.info(request, _("La fecha de entrega corresponde a otro parcial; la actividad cuenta ahora en «%(row)s».") % {"row": activity.category})
            if form.review_before_due:
                messages.info(request, _("La revisión termina antes de la fecha de entrega, así que los estudiantes no "
                                         "la verán. Elija un cierre posterior al vencimiento o «Sin revisión»."))
            reopened = engine.reopen_auto_submitted(activity)
            if reopened:
                messages.info(request, ngettext(
                    "Como la nueva fecha de entrega es posterior, %(n)d intento enviado automáticamente volvió a quedar abierto.",
                    "Como la nueva fecha de entrega es posterior, %(n)d intentos enviados automáticamente volvieron a quedar abiertos.",
                    reopened) % {"n": reopened})
            return redirect("activity_detail", course_pk=course.pk, pk=activity.pk)
        messages.error(request, _("Revise los campos marcados."))
    students = _approved(course)
    rows = _results(activity, students)
    review = [q for q in activity.questions if q["type"] in engine.REVIEW_TYPES]
    pending = {}
    for key in Answer.objects.filter(attempt__activity=activity, needs_review=True,
                                     attempt__submitted_at__isnull=False).values_list("key", flat=True):
        pending[key] = pending.get(key, 0) + 1
    return render(request, "activities/detail.html", {
        "course": course, "activity": activity, "form": form, "rows": rows,
        "surveys": _surveys(activity, rows),
        "mismatch": gradebook.mismatch(activity, list(Period.objects.filter(course=course))),
        "submitted": sum(1 for r in rows if r["attempt"]),
        "review": [{"question": q, "pending": pending.get(q["key"], 0)} for q in review],
        "milestones": Milestone.objects.filter(course=course),
        "parts": outline.parts(activity.compiled), "disabled": activity.disabled,
        "full_points": float(activity.compiled.get("points") or 0),
    })


@teacher_required
@require_POST
def activity_parts(request, course_pk, pk):
    """Qué secciones están habilitadas. Una deshabilitada no se muestra al estudiante ni cuenta para
    la nota; las entregas ya enviadas se recalculan y las respuestas no se tocan."""
    course, activity = _own_activity(request, course_pk, pk)
    enabled = set(request.POST.getlist("enabled"))
    every = {i["item_id"] for i in activity.compiled.get("items", []) if i.get("kind", "item") == "item"}
    count = engine.set_disabled(activity, every - enabled)
    if activity.ungraded_items:
        messages.success(request, ngettext(
            "Se guardaron las secciones. La actividad vale ahora %(p)s puntos y se recalculó %(n)d entrega.",
            "Se guardaron las secciones. La actividad vale ahora %(p)s puntos y se recalcularon %(n)d entregas.", count)
            % {"p": f"{activity.points:g}", "n": count})
    else:
        messages.success(request, _("Todas las secciones están habilitadas."))
    return redirect(reverse("activity_detail", args=[course.pk, activity.pk]) + "#parts")


def _surveys(activity, rows) -> list[dict]:
    """Cuántos eligieron cada opción de cada encuesta, en las entregas que cuentan."""
    surveys = [q for q in activity.questions if q.get("survey")]
    if not surveys:
        return []
    counted = [r["attempt"].pk for r in rows if r["attempt"]]
    values: dict[str, list] = {}
    for key, value in Answer.objects.filter(attempt_id__in=counted).values_list("key", "value"):
        values.setdefault(key, []).append(value)
    out = []
    for q in surveys:
        given = values.get(q["key"], [])
        counts = {o["key"]: 0 for o in q.get("options") or []}
        answered = 0
        for value in given:
            picked = value if isinstance(value, list) else [value]
            picked = [p for p in picked if p in counts]
            answered += bool(picked)
            for p in picked:
                counts[p] += 1
        out.append({"question": q, "answered": answered,
                    "bars": [{"text": o["text"], "count": counts[o["key"]]} for o in q.get("options") or []]})
    return out


def _own_activity(request, course_pk, pk):
    course = owned_course(request, course_pk)
    return course, get_object_or_404(Activity, pk=pk, course=course)


@teacher_required
def attempt_review(request, course_pk, pk, attempt_pk):
    """Un intento de un estudiante, con lo que respondió, su nota y la solución. Un borrador en
    curso también se puede ver (sin calificar): sirve para seguir el avance durante un taller."""
    course, activity = _own_activity(request, course_pk, pk)
    attempt = get_object_or_404(Attempt.objects.select_related("student"), pk=attempt_pk, activity=activity)
    others = Attempt.objects.filter(activity=activity, student=attempt.student).order_by("number")
    # Anterior y siguiente, en el orden de la tabla de entregas: calificar una tras otra.
    order = [r["attempt"].pk for r in _results(activity, _approved(course)) if r["attempt"]]
    here = order.index(attempt.pk) if attempt.pk in order else None
    return render(request, "activities/attempt_review.html", {
        "course": course, "activity": activity, "attempt": attempt, "others": others,
        "rows": engine.answer_rows(attempt), "flags": _flags(attempt.telemetry),
        "blocks": outline.for_review(attempt.content, activity.compiled),
        "previous": order[here - 1] if here else None,
        "next": order[here + 1] if here is not None and here + 1 < len(order) else None,
        "position": (here + 1) if here is not None else None, "total": len(order),
    })


@teacher_required
@require_POST
def attempt_feedback(request, course_pk, pk, attempt_pk):
    """Comentario general del docente sobre una entrega; el estudiante lo ve con su nota."""
    course, activity = _own_activity(request, course_pk, pk)
    attempt = get_object_or_404(Attempt, pk=attempt_pk, activity=activity, submitted_at__isnull=False)
    attempt.feedback = request.POST.get("feedback", "").strip()[:4000]
    attempt.save(update_fields=["feedback"])
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"ok": True})
    messages.success(request, _("Comentario guardado."))
    return redirect("attempt_review", course_pk=course.pk, pk=activity.pk, attempt_pk=attempt.pk)


# -------------------------------------------------------------------- docente: presentar en clase


def _slides(activity, now=None) -> list[dict]:
    """Lo que se proyecta: el contenido y los ítems, sin respuestas (nada en el proyector revela
    una respuesta correcta). Las diapositivas de solución, solo si la revisión ya está abierta para
    los estudiantes."""
    review = activity.review_open(now)
    hidden = outline.hidden_blocks(activity.compiled, activity.disabled)
    out = []
    for block in activity.compiled.get("items", []):
        kind = block.get("kind", "item")
        if kind == "segment" and not (block.get("title") or block.get("instructions")):
            continue
        if (kind == "solution" and not review) or block.get("at") in hidden:
            continue
        out.append(block)
    return out


@teacher_required
def activity_present(request, course_pk, pk):
    """Proyectar la actividad mientras los estudiantes la resuelven: el docente avanza y cada
    estudiante decide si lo sigue o continúa donde estaba (static/js/activity.js)."""
    course, activity = _own_activity(request, course_pk, pk)
    slides = _slides(activity)
    if not slides:
        raise Http404
    return render(request, "activities/present.html", {
        "course": course, "activity": activity, "slides": slides,
        "entries": outline.build({"items": slides}, solutions=True),
    })


@teacher_required
@require_POST
def activity_present_action(request, course_pk, pk):
    """`move` (el docente cambió de diapositiva), `beat` (la página sigue abierta) o `stop`.
    Devuelve el avance de la clase en el ítem actual: cuántos lo respondieron, no qué."""
    course, activity = _own_activity(request, course_pk, pk)
    action = request.POST.get("action")
    if action == "stop":
        engine.stop_presenting(activity)
        return JsonResponse({"ok": True})
    try:
        at = int(request.POST.get("at", ""))
    except ValueError:
        return JsonResponse({"error": _("Diapositiva desconocida.")}, status=400)
    block = next((b for b in _slides(activity) if b.get("at") == at), None)
    if block is None:
        return JsonResponse({"error": _("Diapositiva desconocida.")}, status=400)
    engine.set_pointer(activity, block)
    keys = [q["key"] for q in block.get("questions", [])]
    return JsonResponse({"ok": True, **engine.progress(activity, keys)})


@teacher_required
def question_review(request, course_pk, pk, key):
    """Calificar por pregunta, no por estudiante: todas las respuestas a la misma pregunta
    seguidas, con la rúbrica y la referencia a la vista (docs/schema.md §8)."""
    course, activity = _own_activity(request, course_pk, pk)
    counted = engine.counted_attempts(activity)
    answers = {a.attempt_id: a for a in Answer.objects.filter(attempt__in=counted.values(), key=key)}
    uploads: dict[int, list] = {}
    for u in Upload.objects.filter(attempt__in=counted.values(), key=key):
        uploads.setdefault(u.attempt_id, []).append(u)
    question, record = None, {}
    for item in activity.compiled.get("items", []):
        for q in item["questions"]:
            if q["key"] == key:
                question, record = q, item.get("solutions", {}).get(q["id"], {})
    if question is None:
        raise Http404
    rows = []
    for student in _approved(course):
        attempt = counted.get(student.pk)
        if attempt and attempt.pk in answers:
            rows.append({"student": student, "attempt": attempt, "answer": answers[attempt.pk],
                         "uploads": uploads.get(attempt.pk, [])})
    rows.sort(key=lambda r: (not r["answer"].needs_review, r["student"].last_name))
    return render(request, "activities/question_review.html", {
        "course": course, "activity": activity, "question": question, "record": record, "rows": rows,
        "pending": sum(1 for r in rows if r["answer"].needs_review),
    })


@teacher_required
@require_POST
def answer_grade(request, course_pk, pk, answer_pk):
    course, activity = _own_activity(request, course_pk, pk)
    answer = get_object_or_404(Answer.objects.select_related("attempt"), pk=answer_pk, attempt__activity=activity)
    comment = request.POST.get("comment")
    try:
        if request.POST.get("restore"):
            engine.set_grade(answer, None, request.user, comment)
        else:
            raw = request.POST.get("points", "").replace(",", ".").strip()
            engine.set_grade(answer, float(raw), request.user, comment)
    except ValueError:
        error = _("Escriba un número.")
    except engine.ActionError as exc:
        error = str(exc)
    else:
        error = ""
    if request.headers.get("X-Requested-With") == "fetch":
        if error:
            return JsonResponse({"error": error}, status=400)
        answer.attempt.refresh_from_db()
        return JsonResponse({"points": answer.points, "needs_review": answer.needs_review,
                             "attempt_points": answer.attempt.points, "pending": answer.attempt.pending})
    if error:
        messages.error(request, error)
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = reverse("activity_detail", args=[course.pk, activity.pk])
    return redirect(back)


# -------------------------------------------------------------------- docente: fechas


@teacher_required
def dates_panel(request, course_pk):
    """Panel global de fechas: las fechas clave del curso y el vencimiento de cada actividad.

    Mover una fecha clave mueve todas las actividades que vencen en relación con ella; las de
    fecha fija se pueden mover varias a la vez.
    """
    course = owned_course(request, course_pk)
    gradebook.ensure_defaults(course)
    milestone_form = MilestoneForm(prefix="new")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "milestone_add":
            milestone_form = MilestoneForm(request.POST, prefix="new")
            if milestone_form.is_valid():
                milestone = milestone_form.save(commit=False)
                milestone.course = course
                milestone.save()
                messages.success(request, _("Fecha clave «%(name)s» agregada.") % {"name": milestone.name})
                return redirect("activity_dates", course_pk=course.pk)
        elif action == "milestone_save":
            milestone = get_object_or_404(Milestone, pk=request.POST.get("milestone"), course=course)
            form = MilestoneForm(request.POST, instance=milestone, prefix=f"m{milestone.pk}")
            if form.is_valid():
                with transaction.atomic():
                    form.save()
                    moved = gradebook.recompute_due_dates(milestone)
                messages.success(request, ngettext(
                    "«%(name)s» guardada; %(n)d actividad vence en relación con ella.",
                    "«%(name)s» guardada; %(n)d actividades vencen en relación con ella.", moved)
                    % {"name": milestone.name, "n": moved})
            else:
                messages.error(request, _("Revise la fecha clave: %(errors)s")
                               % {"errors": " ".join(e for errs in form.errors.values() for e in errs)})
            return redirect("activity_dates", course_pk=course.pk)
        elif action == "milestone_delete":
            milestone = get_object_or_404(Milestone, pk=request.POST.get("milestone"), course=course)
            linked = milestone.activities.count()
            name = milestone.name
            milestone.delete()      # sus actividades conservan la fecha calculada, ahora fija
            messages.success(request, _("Fecha clave «%(name)s» eliminada; %(n)d actividades quedaron con fecha fija.")
                             % {"name": name, "n": linked})
            return redirect("activity_dates", course_pk=course.pk)
        elif action in ("shift", "link"):
            ids = [int(i) for i in request.POST.getlist("activity") if i.isdigit()]
            chosen = list(Activity.objects.filter(course=course, pk__in=ids).select_related("due_milestone", "course__owner"))
            if not chosen:
                messages.info(request, _("No había actividades seleccionadas."))
            elif action == "shift":
                try:
                    days = int(request.POST.get("days", "0"))
                except ValueError:
                    days = 0
                if not days or abs(days) > 365:
                    messages.error(request, _("Indique cuántos días mover (positivo: más tarde)."))
                else:
                    moved = _shift(chosen, days)
                    messages.success(request, ngettext("Se movió %(n)d actividad %(days)+d días.",
                                                       "Se movieron %(n)d actividades %(days)+d días.", moved)
                                     % {"n": moved, "days": days})
            else:
                milestone = Milestone.objects.filter(course=course, pk=request.POST.get("milestone")).first()
                try:
                    offset = int(request.POST.get("offset", "-1"))
                    review = request.POST.get("review_offset", "").strip()
                    review = int(review) if review else None
                except ValueError:
                    offset, review = -1, 99
                if milestone is None or abs(offset) > 60 or (review is not None and abs(review) > 60):
                    messages.error(request, _("Elija la fecha clave y un desfase entre -60 y 60 días."))
                elif review is not None and review < offset:
                    messages.error(request, _("La revisión debe terminar después de la fecha de entrega."))
                else:
                    periods = list(Period.objects.filter(course=course))
                    for activity in chosen:
                        activity.due_milestone, activity.due_offset_days = milestone, offset
                        activity.due_from_opening = False
                        if review is not None:
                            # «Hasta el día de la prueba»: cierra al comenzar ese día, salvo otra hora.
                            activity.review_mode = Activity.Review.MILESTONE
                            activity.review_milestone, activity.review_offset_days = milestone, review
                        activity.compute_due()
                        gradebook.rebind(activity, periods)
                        activity.save(update_fields=["due_milestone", "due_offset_days", "due_from_opening", "due_at",
                                                     "review_mode", "review_milestone", "review_offset_days",
                                                     "review_until", "category"])
                        engine.reopen_auto_submitted(activity)
                    if review is not None:
                        messages.success(request, ngettext(
                            "%(n)d actividad vence ahora en relación con «%(name)s», con revisión hasta esa fecha clave.",
                            "%(n)d actividades vencen ahora en relación con «%(name)s», con revisión hasta esa fecha clave.",
                            len(chosen)) % {"n": len(chosen), "name": milestone.name})
                    else:
                        messages.success(request, ngettext(
                            "%(n)d actividad vence ahora en relación con «%(name)s».",
                            "%(n)d actividades vencen ahora en relación con «%(name)s».", len(chosen))
                            % {"n": len(chosen), "name": milestone.name})
            return redirect("activity_dates", course_pk=course.pk)

    milestones = [{"milestone": m, "form": MilestoneForm(instance=m, prefix=f"m{m.pk}"),
                   "count": m.activities.count()} for m in Milestone.objects.filter(course=course)]
    activities = Activity.objects.filter(course=course).select_related("due_milestone", "review_milestone",
                                                                       "category__period", "course__owner")
    return render(request, "activities/dates.html", {
        "course": course, "milestones": milestones, "milestone_form": milestone_form,
        "activities": activities, "milestone_choices": Milestone.objects.filter(course=course),
    })


def _shift(activities, days: int) -> int:
    """Mueve el vencimiento (y la apertura) `days` días. Las relativas a una fecha clave cambian
    su desfase y siguen atadas a ella."""
    moved = 0
    delta = timedelta(days=days)
    periods = None
    for activity in activities:
        fields = []
        if activity.opens_at and activity.opens_at > timezone.now():
            activity.opens_at += delta
            fields.append("opens_at")
        if activity.due_milestone_id:
            activity.due_offset_days += days
            activity.compute_due()
            fields += ["due_offset_days", "due_at", "review_until"]
        elif activity.due_from_opening and "opens_at" in fields:
            activity.compute_due()                  # sigue a la apertura, que ya se movió
            fields += ["due_at", "review_until"]
        elif activity.due_from_opening:
            activity.due_offset_days += days        # la apertura ya pasó: cambia el desfase
            activity.compute_due()
            fields += ["due_offset_days", "due_at", "review_until"]
        elif activity.due_at:
            activity.due_at += delta
            fields.append("due_at")
        if activity.review_mode == Activity.Review.FIXED and activity.review_until:
            activity.review_until += delta
            fields.append("review_until")
        if periods is None:
            periods = list(Period.objects.filter(course_id=activity.course_id))
        if fields and gradebook.rebind(activity, periods):
            fields.append("category")
        if fields:
            activity.save(update_fields=sorted(set(fields)))
            engine.reopen_auto_submitted(activity)
            moved += 1
    return moved


# -------------------------------------------------------------------- estudiante


def _student_activity(request, course_pk, pk) -> Activity:
    course = get_object_or_404(Course, pk=course_pk)
    activity = get_object_or_404(Activity.objects.select_related("course__owner", "category__period"),
                                 pk=pk, course=course)
    # Un borrador o una actividad aún cerrada no existen para el estudiante (ni para quien no
    # está inscrito): 404, igual que lo ajeno.
    if not is_approved(request.user, course) or not activity.is_visible():
        raise Http404
    return activity


@login_required
def activity_student(request, course_pk, pk):
    activity = _student_activity(request, course_pk, pk)
    state = engine.state(activity, request.user)
    counted = state.counted
    if counted:
        blocks = outline.for_review(counted.content, activity.compiled if state.solutions else {}, activity.disabled)
    else:
        blocks = outline.for_review(activity.compiled, activity.compiled, activity.disabled)
    return render(request, "activities/student.html", {
        "course": activity.course, "activity": activity, "state": state,
        "rows": engine.answer_rows(counted) if counted else [], "blocks": blocks,
    })


@login_required
def attempt_pointer(request, course_pk, pk):
    """Dónde está el docente si está presentando la actividad (la página del intento lo consulta)."""
    activity = _student_activity(request, course_pk, pk)
    return JsonResponse(engine.pointer(activity))


@login_required
@require_POST
def attempt_start(request, course_pk, pk):
    activity = _student_activity(request, course_pk, pk)
    try:
        engine.start(activity, request.user)
    except engine.ActionError as exc:
        messages.error(request, str(exc))
        return redirect("activity_student", course_pk=course_pk, pk=pk)
    return redirect("attempt_form", course_pk=course_pk, pk=pk)


def _draft(request, activity) -> Attempt:
    draft = Attempt.objects.filter(activity=activity, student=request.user, submitted_at=None).first()
    if draft is None:
        raise Http404
    return draft


def _telemetry(request) -> dict:
    try:
        data = json.loads(request.POST.get("telemetry") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _closed_message(activity, student) -> str:
    """Por qué ya no hay un borrador abierto (para el autoguardado de una página que sigue abierta)."""
    last = Attempt.objects.filter(activity=activity, student=student).order_by("-number").first()
    if last and last.auto_submitted:
        return _("La fecha de entrega venció y su intento se envió automáticamente con lo que tenía guardado.")
    return _("Este intento ya se envió. Recargue la página.")


@login_required
def attempt_form(request, course_pk, pk):
    """El intento en curso. Guardar (también automático) y Enviar llegan aquí."""
    activity = _student_activity(request, course_pk, pk)
    fetched = request.headers.get("X-Requested-With") == "fetch"
    engine.finalize_overdue(activity, student=request.user)
    draft = Attempt.objects.filter(activity=activity, student=request.user, submitted_at=None).first()
    if draft is None:
        if fetched:
            return JsonResponse({"error": _closed_message(activity, request.user)}, status=409)
        return redirect("activity_student", course_pk=course_pk, pk=pk)
    errors: dict[str, str] = {}
    if request.method == "POST":
        action = request.POST.get("action", "save")
        _save_answers(request, draft)
        upload_errors = _receive_files(request, draft)
        if fetched:
            return JsonResponse({"saved_at": timezone.localtime(draft.saved_at).strftime("%H:%M:%S"),
                                 "errors": upload_errors})
        for message in upload_errors.values():
            messages.error(request, message)
        if action == "submit":
            try:
                engine.submit(draft)
            except engine.ActionError as exc:
                messages.error(request, str(exc))
                errors = engine.format_errors(draft)
            else:
                draft.refresh_from_db()
                messages.success(request, _("Entrega recibida."))
                return redirect("activity_student", course_pk=course_pk, pk=pk)
        else:
            messages.info(request, _("Borrador guardado."))
            return redirect("attempt_form", course_pk=course_pk, pk=pk)
    state = engine.state(activity, request.user)
    _ok, reason, late = engine.can_answer(activity, state.used, timezone.now())
    titles = _titles(activity)
    hidden = outline.hidden_blocks(draft.content, activity.disabled)
    rows = _labeled([r for r in engine.answer_rows(draft) if not r["disabled"]], titles)
    entries = [e for e in outline.build(draft.content, titles) if e["at"] not in hidden]
    return render(request, "activities/attempt.html", {
        "course": activity.course, "activity": activity, "attempt": draft,
        "rows": rows, "errors": errors, "late": late, "blocked": reason,
        "accept": images.ACCEPT_ATTR, "entries": _progress(entries, rows), "hidden": hidden,
        "answered": sum(1 for r in rows if not r["blank"]), "unanswered": sum(1 for r in rows if r["blank"]),
        "marked": sum(1 for r in rows if r["marked"]),
    })


def _labeled(rows: list[dict], titles: dict[str, str]) -> list[dict]:
    """Un nombre corto para cada pregunta en el panel de pendientes: el título del ítem y, si tiene
    varias, el número de la pregunta."""
    per_item: dict[str, int] = {}
    for row in rows:
        per_item[row["item"]["item_id"]] = per_item.get(row["item"]["item_id"], 0) + 1
    numbers = {item_id: n for n, item_id in enumerate(per_item, start=1)}
    # Las preguntas se numeran 1, 2, 3…; las de seguimiento llevan el número de la suya y una letra (2a, 2b).
    seen: dict[str, int] = {}
    letters: dict[str, int] = {}
    for row in rows:
        item_id = row["item"]["item_id"]
        if row["question"].get("follow_up") and seen.get(item_id):
            letters[item_id] = letters.get(item_id, 0) + 1
            number = f"{seen[item_id]}{'abcdefghijklmnopqrstuvwxyz'[(letters[item_id] - 1) % 26]}"
        else:
            seen[item_id] = seen.get(item_id, 0) + 1
            letters[item_id] = 0
            number = str(seen[item_id])
        title = outline.item_title(row["item"], titles, numbers[item_id])
        row["item_title"], row["number"], row["single"] = title, number, per_item[item_id] == 1
        row["label"] = title if row["single"] else _("%(title)s · %(n)s") % {"title": title, "n": number}
    return rows


def _progress(entries: list[dict], rows: list[dict]) -> list[dict]:
    """Cuántas preguntas de cada ítem del índice tienen respuesta (para el panel del intento)."""
    done = {r["key"] for r in rows if not r["blank"]}
    for entry in entries:
        if entry["kind"] == "item":
            entry["done"] = sum(1 for k in entry["keys"] if k in done)
            entry["total"] = len(entry["keys"])
    return entries


@login_required
@require_POST
def attempt_timer(request, course_pk, pk, key, action):
    """Abrir una pregunta con tiempo, o responderla («Responder» o se acabó el tiempo). Con
    JavaScript llega por fetch y se devuelve la pregunta redibujada; sin él, se vuelve al intento."""
    activity = _student_activity(request, course_pk, pk)
    draft = get_object_or_404(Attempt, activity=activity, student=request.user, submitted_at=None)
    fetched = request.headers.get("X-Requested-With") == "fetch"
    try:
        if action == "abrir":
            _save_answers(request, draft)            # sin JavaScript llega el formulario entero
            draft = engine.open_timer(draft, key)
        elif action == "responder":
            question = [q for q in draft.questions if q["key"] == key]
            draft = engine.close_timer(draft, key, engine.parse_answers(question, request.POST))
            _save_answers(request, draft)
        else:
            raise Http404
    except engine.ActionError as exc:
        if fetched:
            return JsonResponse({"error": str(exc)}, status=409)
        messages.error(request, str(exc))
        return redirect("attempt_form", course_pk=course_pk, pk=pk)
    if not fetched:
        return redirect(f"{reverse('attempt_form', args=[course_pk, pk])}#q-{key}")
    row = next(r for r in engine.answer_rows(draft) if r["key"] == key)
    html = render_to_string("activities/_question_input.html", {
        "row": row, "activity": activity, "course": activity.course, "accept": images.ACCEPT_ATTR}, request)
    return JsonResponse({"html": html, "remaining_ms": row["timer"].remaining_ms if row["timer"] else 0})


def _save_answers(request, draft) -> None:
    """Solo si llegó el formulario completo (`form=attempt`): una petición con solo una imagen
    no trae las respuestas y no debe dejarlas en blanco."""
    if request.POST.get("form") == "attempt":
        engine.save_draft(draft, engine.parse_answers(engine.visible_questions(draft), request.POST),
                          _telemetry(request), marked=request.POST.getlist("mark"))


def _receive_files(request, draft) -> dict[str, str]:
    """Imágenes enviadas con el formulario (sin JavaScript) o una a una (con JavaScript)."""
    errors = {}
    for field, files in request.FILES.lists():
        if not field.startswith("f-"):
            continue
        key = field[2:]
        for upload in files:
            try:
                engine.add_upload(draft, key, upload)
            except engine.ActionError as exc:
                errors[key] = str(exc)
                break
    return errors


@login_required
@require_POST
def attempt_upload(request, course_pk, pk):
    """Con JavaScript, cada imagen se sube apenas se elige; devuelve la lista actualizada."""
    activity = _student_activity(request, course_pk, pk)
    draft = _draft(request, activity)
    key = request.POST.get("key", "")
    errors = _receive_files(request, draft)
    return JsonResponse({"error": errors.get(key, ""), "html": _uploads_html(request, draft, key)})


@login_required
@require_POST
def attempt_upload_remove(request, course_pk, pk, upload_pk):
    activity = _student_activity(request, course_pk, pk)
    draft = _draft(request, activity)
    row = get_object_or_404(Upload, pk=upload_pk, attempt=draft)
    key = row.key
    _save_answers(request, draft)           # sin JavaScript, el botón envía todo el formulario
    try:
        engine.remove_upload(draft, upload_pk)
    except engine.ActionError as exc:
        messages.error(request, str(exc))
    if request.headers.get("X-Requested-With") == "fetch":
        return JsonResponse({"html": _uploads_html(request, draft, key)})
    return redirect("attempt_form", course_pk=course_pk, pk=pk)


def _uploads_html(request, draft, key) -> str:
    question = next((q for q in draft.questions if q["key"] == key), None)
    if question is None:
        return ""
    return render_to_string("activities/_uploads.html", {
        "question": question, "uploads": draft.uploads.filter(key=key), "attempt": draft,
        "course": draft.activity.course, "activity": draft.activity, "editable": True,
    }, request)


@login_required
@require_POST
def attempt_discard(request, course_pk, pk):
    activity = _student_activity(request, course_pk, pk)
    draft = _draft(request, activity)
    draft.delete()
    messages.info(request, _("Se descartó el borrador."))
    return redirect("activity_student", course_pk=course_pk, pk=pk)


# -------------------------------------------------------------------- imágenes


@login_required
def upload_file(request, upload_pk):
    """Una imagen entregada: la ve su autor y el docente del curso. Para cualquier otro, 404."""
    row = get_object_or_404(Upload.objects.select_related("attempt__activity__course"), pk=upload_pk)
    attempt = row.attempt
    if not (attempt.student_id == request.user.pk or attempt.activity.course.owner_id == request.user.pk):
        raise Http404
    try:
        handle = row.file.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404 from None
    response = FileResponse(handle, content_type="image/jpeg")
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
    response["Content-Disposition"] = "inline"
    return response
