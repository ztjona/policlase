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
from policlase_gen import activity as activityfmt
from policlase_gen import loader

from apps.courses.access import is_approved, owned_course, teacher_required
from apps.courses.models import Course, Enrollment
from apps.grades import gradebook
from apps.grades.forms import MilestoneForm
from apps.grades.models import Milestone

from . import engine, images
from .forms import ActivitySettingsForm
from .models import Activity, Answer, Attempt, Upload

ACTIVITY_TEMPLATE = r"""schema: policlase.activity/v1
title: 'Deber 1'
instructions: |
  Resuelva cada ejercicio. Las fechas, el peso y los intentos se configuran en policlase,
  no en este archivo.

# El LaTeX va entre comillas simples: en las dobles, \t, \f, \b… son escapes de YAML.
items:
  - id: D01-ejemplo
    questions:
      - id: q1
        type: numeric
        prompt: '¿Cuánto vale $\sqrt{2}$ con cuatro decimales?'
        solution: 1.4142
        grading: { atol: 0.0001 }
      - id: q2
        type: open
        points: 2
        prompt: 'Explique con sus palabras qué es el error de truncamiento.'
        grading:
          mode: manual            # ai: marcada para calificación con IA (aún no activa)
          rubric:
            - { criterion: 'Define el error de truncamiento', points: 1 }
            - { criterion: 'Da un ejemplo', points: 1 }
      - id: q3
        type: image
        prompt: 'Suba la foto de su desarrollo a mano.'
        max_files: 3
"""


def _diagnostics(report) -> list[dict]:
    return [{"code": d.code, "severity": d.severity.value, "message": d.message,
             "line": d.line, "where": ".".join(p for p in (d.item_id, d.question_id) if p)}
            for d in report]


def _course_link(course):
    from apps.github.models import CourseRepo
    return CourseRepo.objects.filter(course=course).select_related("course__owner").first()


def _approved(course):
    return [e.student for e in Enrollment.objects.filter(course=course, status=Enrollment.Status.APPROVED)
            .select_related("student")]


# -------------------------------------------------------------------- docente: contenido


@teacher_required
def activity_edit(request, course_pk, pk=None):
    from apps.github import sync

    course = owned_course(request, course_pk)
    link = _course_link(course)
    if link and request.method != "POST":
        sync.maybe_pull(link)
    activity = get_object_or_404(Activity, pk=pk, course=course) if pk else None
    source = activity.source if activity else ACTIVITY_TEMPLATE
    diagnostics = []

    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload:
            if upload.size > 512 * 1024:
                messages.error(request, _("El archivo supera 512 KB."))
                return redirect(request.path)
            source = upload.read().decode("utf-8", errors="replace")
        else:
            source = request.POST.get("source", "")

        document, report = activityfmt.load_activity_text(source)
        diagnostics = _diagnostics(report)
        if document is not None and not report.errors:
            compiled = activityfmt.compile_activity(document)
            new = activity is None
            activity = activity or Activity(course=course)
            activity.title = compiled["title"]
            saved = True
            if link:
                try:
                    activity.github_path, activity.github_sha = sync.push_activity(link, activity, source)
                except sync.Conflict:
                    saved = False
                    messages.error(request, _("La actividad cambió en GitHub después de que la abrió; "
                                              "no se guardó para no perder esos cambios. Copie su texto, "
                                              "recargue el editor y vuelva a aplicarlo."))
                except sync.GitHubError as exc:
                    saved = False
                    messages.error(request, _("No se guardó: %(error)s") % {"error": exc})
            if saved:
                activity.source, activity.compiled = source, compiled
                activity.save()
                messages.success(request, _("Actividad «%(title)s» guardada: %(questions)d preguntas, "
                                            "%(points)s puntos.")
                                 % {"title": activity.title, "questions": len(activity.questions),
                                    "points": f"{activity.points:g}"})
                if link:
                    messages.info(request, _("Confirmada en GitHub: %(path)s") % {"path": activity.github_path})
                if new:
                    messages.info(request, _("Elija su ponderación y su fecha de entrega, y publíquela."))
                return redirect("activity_detail", course_pk=course.pk, pk=activity.pk)
        else:
            messages.error(request, _("La actividad tiene errores; no se guardó."))

    return render(request, "activities/edit.html", {
        "course": course, "activity": activity, "source": source, "diagnostics": diagnostics, "link": link,
    })


@teacher_required
@require_POST
def activity_preview(request, course_pk):
    """Vista previa del editor. Las actividades no admiten generadores todavía (E092): compilar
    aquí no ejecuta código del docente."""
    owned_course(request, course_pk)
    source = request.POST.get("source", "")
    if len(source) > 512 * 1024:
        return JsonResponse({"diagnostics": [{"code": "", "severity": "error", "line": None, "where": "",
                                              "message": _("El archivo supera 512 KB.")}]})
    document, report = activityfmt.load_activity_text(source)
    payload = {"diagnostics": _diagnostics(report), "html": None, "lines": []}
    if document is not None and not report.errors:
        compiled = activityfmt.compile_activity(document)
        payload["html"] = render_to_string("activities/_preview.html", {"content": compiled}, request)
        payload["title"] = compiled["title"]
    if document is not None and isinstance(document.get("items"), list):
        payload["lines"] = [loader.line_of(document["items"], i) for i in range(len(document["items"]))]
    return JsonResponse(payload)


@teacher_required
@require_POST
def activity_delete(request, course_pk, pk):
    course = owned_course(request, course_pk)
    activity = get_object_or_404(Activity, pk=pk, course=course)
    link = _course_link(course)
    if link and activity.github_path:
        from apps.github import sync
        try:
            sync.delete_activity(link, activity)
        except sync.GitHubError as exc:
            messages.error(request, _("No se eliminó: %(error)s") % {"error": exc})
            return redirect("activity_detail", course_pk=course.pk, pk=activity.pk)
    title = activity.title
    activity.delete()
    messages.success(request, _("Actividad «%(title)s» eliminada, con sus entregas.") % {"title": title})
    return redirect("course_activities", pk=course.pk)


# -------------------------------------------------------------------- docente: configuración y resultados


def _results(activity, students) -> list[dict]:
    counted = engine.counted_attempts(activity)
    tries: dict[int, int] = {}
    drafts = set()
    for student_id, submitted in Attempt.objects.filter(activity=activity).values_list("student_id", "submitted_at"):
        if submitted:
            tries[student_id] = tries.get(student_id, 0) + 1
        else:
            drafts.add(student_id)
    rows = []
    for student in students:
        attempt = counted.get(student.pk)
        rows.append({"student": student, "attempt": attempt, "tries": tries.get(student.pk, 0),
                     "working": student.pk in drafts,
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


@teacher_required
def activity_detail(request, course_pk, pk):
    course = owned_course(request, course_pk)
    activity = get_object_or_404(Activity.objects.select_related("category__period", "due_milestone"),
                                 pk=pk, course=course)
    gradebook.ensure_defaults(course)
    form = ActivitySettingsForm(request.POST or None, instance=activity, course=course)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, _("Configuración guardada."))
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
        "submitted": sum(1 for r in rows if r["attempt"]),
        "review": [{"question": q, "pending": pending.get(q["key"], 0)} for q in review],
        "milestones": Milestone.objects.filter(course=course),
    })


def _own_activity(request, course_pk, pk):
    course = owned_course(request, course_pk)
    return course, get_object_or_404(Activity, pk=pk, course=course)


@teacher_required
def attempt_review(request, course_pk, pk, attempt_pk):
    """Un intento de un estudiante, con lo que respondió, su nota y la solución."""
    course, activity = _own_activity(request, course_pk, pk)
    attempt = get_object_or_404(Attempt.objects.select_related("student"), pk=attempt_pk, activity=activity,
                                submitted_at__isnull=False)
    others = Attempt.objects.filter(activity=activity, student=attempt.student, submitted_at__isnull=False)
    return render(request, "activities/attempt_review.html", {
        "course": course, "activity": activity, "attempt": attempt, "others": others,
        "rows": engine.answer_rows(attempt), "flags": _flags(attempt.telemetry),
    })


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
                except ValueError:
                    offset = -1
                if milestone is None or abs(offset) > 60:
                    messages.error(request, _("Elija la fecha clave y un desfase entre -60 y 60 días."))
                else:
                    for activity in chosen:
                        activity.due_milestone, activity.due_offset_days = milestone, offset
                        activity.compute_due()
                        activity.save(update_fields=["due_milestone", "due_offset_days", "due_at"])
                    messages.success(request, ngettext(
                        "%(n)d actividad vence ahora en relación con «%(name)s».",
                        "%(n)d actividades vencen ahora en relación con «%(name)s».", len(chosen))
                        % {"n": len(chosen), "name": milestone.name})
            return redirect("activity_dates", course_pk=course.pk)

    milestones = [{"milestone": m, "form": MilestoneForm(instance=m, prefix=f"m{m.pk}"),
                   "count": m.activities.count()} for m in Milestone.objects.filter(course=course)]
    activities = Activity.objects.filter(course=course).select_related("due_milestone", "category__period")
    return render(request, "activities/dates.html", {
        "course": course, "milestones": milestones, "milestone_form": milestone_form,
        "activities": activities, "milestone_choices": Milestone.objects.filter(course=course),
    })


def _shift(activities, days: int) -> int:
    """Mueve el vencimiento (y la apertura) `days` días. Las relativas a una fecha clave cambian
    su desfase y siguen atadas a ella."""
    moved = 0
    delta = timedelta(days=days)
    for activity in activities:
        fields = []
        if activity.due_milestone_id:
            activity.due_offset_days += days
            activity.compute_due()
            fields += ["due_offset_days", "due_at"]
        elif activity.due_at:
            activity.due_at += delta
            fields.append("due_at")
        if activity.opens_at and activity.opens_at > timezone.now():
            activity.opens_at += delta
            fields.append("opens_at")
        if fields:
            activity.save(update_fields=fields)
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
    return render(request, "activities/student.html", {
        "course": activity.course, "activity": activity, "state": state,
        "rows": engine.answer_rows(counted) if counted else [],
    })


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


@login_required
def attempt_form(request, course_pk, pk):
    """El intento en curso. Guardar (también automático) y Enviar llegan aquí."""
    activity = _student_activity(request, course_pk, pk)
    draft = Attempt.objects.filter(activity=activity, student=request.user, submitted_at=None).first()
    if draft is None:
        return redirect("activity_student", course_pk=course_pk, pk=pk)
    errors: dict[str, str] = {}
    if request.method == "POST":
        action = request.POST.get("action", "save")
        _save_answers(request, draft)
        upload_errors = _receive_files(request, draft)
        if request.headers.get("X-Requested-With") == "fetch":
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
    return render(request, "activities/attempt.html", {
        "course": activity.course, "activity": activity, "attempt": draft,
        "rows": engine.answer_rows(draft), "errors": errors, "late": late, "blocked": reason,
        "accept": images.ACCEPT_ATTR,
    })


def _save_answers(request, draft) -> None:
    """Solo si llegó el formulario completo (`form=attempt`): una petición con solo una imagen
    no trae las respuestas y no debe dejarlas en blanco."""
    if request.POST.get("form") == "attempt":
        engine.save_draft(draft, engine.parse_answers(draft.questions, request.POST), _telemetry(request))


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
