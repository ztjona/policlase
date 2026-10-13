import asyncio
import json
import time

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db import connection as db_connection
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from apps.courses.access import is_approved, owned_course, teacher_required
from apps.courses.models import Enrollment

from . import engine
from .guests import qr_svg
from .models import Deck, LiveSession, Response, Section, SessionGrade

Phase = LiveSession.Phase
Status = LiveSession.Status

# -------------------------------------------------------------------- presentaciones


def _course_link(course):
    from apps.github.models import CourseRepo
    return CourseRepo.objects.filter(course=course).select_related("course__owner").first()


@teacher_required
def deck_view(request, course_pk, pk):
    course = owned_course(request, course_pk)
    deck = get_object_or_404(Deck, pk=pk, course=course)
    return render(request, "live/deck_view.html", {"course": course, "deck": deck})


@teacher_required
@require_POST
def deck_delete(request, course_pk, pk):
    course = owned_course(request, course_pk)
    deck = get_object_or_404(Deck, pk=pk, course=course)
    link = _course_link(course)
    if link and deck.github_path:
        from apps.github import sync
        try:
            sync.delete(link, deck)
        except sync.GitHubError as exc:
            messages.error(request, _("No se eliminó: %(error)s") % {"error": exc})
            return redirect("deck_view", course_pk=course.pk, pk=deck.pk)
    deck.delete()
    if link and deck.github_path:
        messages.success(request, _("Presentación eliminada y su archivo borrado de GitHub. Las clases ya dictadas "
                                    "conservan sus resultados."))
    else:
        messages.success(request, _("Presentación eliminada. Las clases ya dictadas conservan sus resultados."))
    return redirect("course_manage", pk=course.pk)


@teacher_required
@require_POST
def deck_move(request, course_pk, pk):
    """Arrastrar una presentación a otra sección. Con GitHub, el archivo cambia de carpeta en un
    solo commit (y solo si allá sigue como lo conocemos)."""
    course = owned_course(request, course_pk)
    deck = get_object_or_404(Deck, pk=pk, course=course)
    wanted = request.POST.get("section", "")
    section = get_object_or_404(Section, pk=wanted, course=course) if wanted else None
    if section == deck.section:
        return JsonResponse({"ok": True})
    deck.section = section
    link = _course_link(course)
    if link:
        from apps.github import sync
        try:
            deck.github_path, deck.github_sha = sync.push(link, deck, deck.source)
        except sync.GitHubError as exc:
            return JsonResponse({"error": _("No se movió: %(error)s") % {"error": exc}}, status=409)
    deck.save(update_fields=["section", "github_path", "github_sha"])
    return JsonResponse({"ok": True})


@teacher_required
@require_POST
def session_delete(request, pk):
    """Eliminar una clase iniciada por error (o una terminada): se borran sus respuestas."""
    session = _own_session(request, pk)
    if session.status == Status.LIVE:
        messages.error(request, _("Termine o pause la clase antes de eliminarla."))
        return redirect("present", pk=session.pk)
    course_pk = session.course_id
    session.delete()
    messages.success(request, _("Clase eliminada."))
    return redirect("course_activities", pk=course_pk)         # donde se listan las clases dictadas


# ------------------------------------------------------------------ sesión: docente


@teacher_required
@require_POST
def session_start(request, course_pk, deck_pk):
    course = owned_course(request, course_pk)
    deck = get_object_or_404(Deck, pk=deck_pk, course=course)

    active = LiveSession.objects.active().filter(course=course).first()
    if active:
        if active.status == Status.PAUSED:
            messages.info(request, _("Hay una clase en pausa en este curso: reanúdela o termínela antes de iniciar otra."))
        else:
            messages.info(request, _("Ya hay una clase en vivo en este curso; se abrió esa clase."))
        return redirect("present", pk=active.pk)

    for _attempt in range(10):  # el PIN es aleatorio; en el raro caso de choque se reintenta
        try:
            with transaction.atomic():
                # Las ocultas viajan marcadas: «Siguiente» las salta, y el panel del proyector
                # permite mostrarlas sobre la marcha.
                session = LiveSession.objects.create(
                    course=course, deck=deck, title=deck.title, slides=deck.slides,
                    speed_bonus=deck.compiled.get("speed_bonus", True),
                )
            break
        except IntegrityError:
            continue
    else:
        messages.error(request, _("No se pudo generar un PIN disponible. Intente de nuevo."))
        return redirect("course_manage", pk=course.pk)
    return redirect("present", pk=session.pk)


def _own_session(request, pk) -> LiveSession:
    return get_object_or_404(LiveSession.objects.select_related("course"),
                             pk=pk, course__owner=request.user)


@teacher_required
def present(request, pk):
    session = _own_session(request, pk)
    return render(request, "live/present.html", {"session": session, "course": session.course,
                                                 "join_host": request.get_host()})


def guest_url(request, session: LiveSession) -> str:
    return request.build_absolute_uri(reverse("live_guest", args=[session.guest_token]))


@teacher_required
def present_fragment(request, pk):
    session = _own_session(request, pk)
    engine.expire_if_due(session.pk)
    session.refresh_from_db()
    context = {
        "session": session,
        "slide": session.slide,
        "participants": session.participants.count(),
        "join_host": request.get_host(),
        "now": timezone.now(),
    }
    if session.allow_guests:
        context["guest_url"] = url = guest_url(request, session)
        context["guest_qr"] = qr_svg(url)
    if session.is_question and session.phase in (Phase.OPEN, Phase.CLOSED, Phase.RESULTS):
        context["stats"] = engine.question_stats(session, session.index)
        context["answered"] = context["stats"]["total"]
    if session.is_question and session.phase == Phase.RESULTS:
        context["correct_display"] = engine.correct_display(session.slide)
    if session.phase == Phase.RESULTS or session.status == Status.ENDED:
        context["leaderboard"] = engine.leaderboard(session)
    if session.is_question and session.phase in (Phase.OPEN, Phase.CLOSED):
        context["connected"] = engine.connected(session.pk).count()
    if session.is_feedback or (session.status == Status.ENDED and session.has_feedback):
        context["feedback_count"] = session.feedback.count()
    if session.status == Status.PAUSED and session.paused_remaining_ms is not None:
        context["paused_left_s"] = round(session.paused_remaining_ms / 1000)
    context.update(_progress(session))
    return render(request, "live/_present_stage.html", context)


def _progress(session: LiveSession) -> dict:
    """Posición entre las diapositivas visibles y el panel lateral del proyector."""
    visible = [i for i, s in enumerate(session.slides) if not s.get("hidden")]
    return {
        "position": visible.index(session.index) + 1 if session.index in visible else "–",
        "visible_total": len(visible),
        "has_prev": any(i < session.index for i in visible),
        "has_next": any(i > session.index for i in visible),
        "outline": [{"index": i, "slide": s, "asked": i in session.opened, "current": i == session.index}
                    for i, s in enumerate(session.slides)],
        # Para saltar a la retroalimentación sin pasar por las diapositivas que quedan.
        "feedback_index": next((i for i in visible if session.slides[i].get("kind") == "feedback"), None),
    }


@teacher_required
@require_POST
def present_action(request, pk):
    session = _own_session(request, pk)
    try:
        raw = request.POST.get("index", "")
        engine.apply(session.pk, request.POST.get("action", ""), index=int(raw) if raw.isdigit() else None)
    except engine.ActionError as exc:
        return HttpResponse(str(exc), status=409)
    return HttpResponse(status=204)


@teacher_required
def session_results(request, pk):
    session = _own_session(request, pk)
    questions = [(i, session.slides[i]) for i in session.asked_indices]
    students = (Enrollment.objects.filter(course=session.course, status=Enrollment.Status.APPROVED)
                .select_related("student"))
    by_student: dict[int, dict[int, Response]] = {}
    for r in Response.objects.filter(session=session):
        by_student.setdefault(r.student_id, {})[r.slide_index] = r
    manual = {g.student_id: g for g in SessionGrade.objects.filter(session=session)}
    joined = set(session.participants.values_list("student_id", flat=True))

    max_points = session.max_points
    rows = []
    for e in students:
        answers = by_student.get(e.student_id, {})
        computed = sum(r.points for r in answers.values())
        grade = manual.get(e.student_id)
        total = grade.points if grade else computed
        rows.append({
            "student": e.student,
            "cells": [(answers.get(i), q["points"], q["question"].get("survey")) for i, q in questions],
            "total": total, "computed": computed, "manual": grade,
            "percent": round(100 * total / max_points) if max_points else 0,
            "joined": e.student_id in joined,
        })
    rows.sort(key=lambda r: (-r["total"], r["student"].last_name))

    # Invitados: quienes entraron por el enlace sin estar inscritos en el curso. No cuentan para
    # ninguna nota; si alguno era un estudiante que se inscribió tarde, se le asigna su cuenta.
    enrolled = {e.student_id for e in students}
    guests = []
    for p in session.participants.exclude(student_id__in=enrolled).select_related("student"):
        answers = by_student.get(p.student_id, {})
        total = sum(r.points for r in answers.values())
        guests.append({"student": p.student, "cells": [(answers.get(i), q["points"], q["question"].get("survey")) for i, q in questions],
                       "total": total, "percent": round(100 * total / max_points) if max_points else 0})
    guests.sort(key=lambda r: -r["total"])
    absent = [r["student"] for r in rows if not r["joined"]]
    absent.sort(key=lambda s: (s.last_name, s.first_name))
    review = [{"index": i, "number": n, "slide": slide, "groups": engine.answer_groups(session, i)}
              for n, (i, slide) in enumerate(questions, start=1)]
    return render(request, "live/results.html", {
        "session": session, "course": session.course, "questions": questions, "review": review,
        "rows": rows, "guests": guests, "max_points": max_points, "absent": absent,
        "has_survey": any(q["question"].get("survey") for _, q in questions),
        "feedback": engine.feedback_summary(session) if session.has_feedback else None,
    })


@teacher_required
@require_POST
def session_grade(request, pk, student_pk):
    """Nota manual de un estudiante inscrito en toda la clase, o volver a la suma de sus respuestas."""
    session = _own_session(request, pk)
    enrollment = get_object_or_404(Enrollment, course=session.course, student_id=student_pk,
                                   status=Enrollment.Status.APPROVED)
    if request.POST.get("restore"):
        engine.set_session_grade(session, enrollment.student, None, request.user)
    else:
        try:
            engine.set_session_grade(session, enrollment.student,
                                     float(request.POST.get("points", "").replace(",", ".")), request.user)
        except ValueError:
            return JsonResponse({"error": _("Escriba un número.")}, status=400)
    return JsonResponse({"ok": True})


@teacher_required
@require_POST
def guest_link(request, pk, guest_pk):
    """Asignar un invitado a un estudiante inscrito: sus respuestas pasan a la cuenta del estudiante."""
    session = _own_session(request, pk)
    guest = get_object_or_404(session.participants.select_related("student"), student_id=guest_pk).student
    enrollment = Enrollment.objects.filter(course=session.course, status=Enrollment.Status.APPROVED,
                                           student_id=request.POST.get("student") or 0).select_related("student").first()
    if enrollment is None:
        messages.error(request, _("Elija un estudiante inscrito."))
        return redirect(f"{reverse('session_results', args=[session.pk])}#invitados")
    name = guest.first_name
    try:
        moved = engine.link_guest(session, guest, enrollment.student)
    except engine.LinkError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, ngettext(
            "«%(guest)s» quedó asignado a %(name)s: %(n)d respuesta pasó a su cuenta.",
            "«%(guest)s» quedó asignado a %(name)s: %(n)d respuestas pasaron a su cuenta.", moved)
            % {"guest": name, "name": enrollment.student.display_name, "n": moved})
    return redirect(f"{reverse('session_results', args=[session.pk])}#notas")


@teacher_required
@require_POST
def response_grade(request, pk, response_pk):
    """Corregir a mano la nota de un estudiante en una pregunta (o volver a la automática)."""
    session = _own_session(request, pk)
    response = get_object_or_404(Response, pk=response_pk, session=session)
    if request.POST.get("restore"):
        engine.set_grade(response, None, request.user)
    else:
        try:
            engine.set_grade(response, float(request.POST.get("points", "").replace(",", ".")), request.user)
        except ValueError:
            return JsonResponse({"error": _("Escriba un número.")}, status=400)
    return JsonResponse({"points": response.points, "correct": response.correct, "edited": response.edited})


@teacher_required
@require_POST
def answer_grade(request, pk, index):
    """Dar por correcta (o incorrecta) una respuesta para todos los que la dieron."""
    session = _own_session(request, pk)
    if index not in session.asked_indices:
        raise Http404
    key, action = request.POST.get("key", ""), request.POST.get("action", "")
    full = float(session.slides[index]["points"])
    points = {"correct": full, "wrong": 0.0, "auto": None}.get(action, "x")
    if points == "x":
        return redirect("session_results", pk=session.pk)
    changed = 0
    for response in Response.objects.filter(session=session, slide_index=index):
        if engine.answer_key(response.answer) == key:
            engine.set_grade(response, points, request.user)
            changed += 1
    messages.success(request, ngettext("%(n)d respuesta actualizada.", "%(n)d respuestas actualizadas.", changed)
                     % {"n": changed})
    return redirect(f"{reverse('session_results', args=[session.pk])}#p{index}")


# ---------------------------------------------------------------- sesión: estudiante


def can_attend(user, session: LiveSession) -> bool:
    """Estudiantes aprobados del curso, o quien entró por el enlace de una clase abierta.

    Entrar por el enlace deja un `Participant`; si el docente cierra la clase a invitados,
    quienes entraron así pierden el acceso de inmediato.
    """
    if not user.is_authenticated:
        return False
    if is_approved(user, session.course):
        return True
    return session.allow_guests and session.participants.filter(student=user).exists()


def _student_session(request, pk) -> LiveSession:
    session = get_object_or_404(LiveSession.objects.select_related("course"), pk=pk)
    if not can_attend(request.user, session):
        raise Http404
    return session


@login_required
def join_by_pin(request):
    if request.method == "POST":
        pin = "".join(ch for ch in request.POST.get("pin", "") if ch.isdigit())
        session = LiveSession.objects.active().filter(pin=pin).select_related("course").first()
        if session and is_approved(request.user, session.course):
            return redirect("live_student", pk=session.pk)
        # Un PIN válido de un curso en el que no está aprobado se trata igual que uno falso.
        messages.error(request, _("No hay una clase en vivo con ese PIN en sus cursos."))
    return render(request, "live/join.html")


@login_required
def live_student(request, pk):
    session = _student_session(request, pk)
    engine.join(session, request.user)
    return render(request, "live/student.html", {"session": session, "course": session.course})


@login_required
def student_fragment(request, pk):
    session = _student_session(request, pk)
    engine.expire_if_due(session.pk)
    session.refresh_from_db()
    engine.join(session, request.user)
    return render(request, "live/_student_stage.html", _student_context(session, request.user))


def rating_choices():
    return [(1, _("Mal")), (2, _("Regular")), (3, _("Bien")), (4, _("Muy bien")), (5, _("Excelente"))]


def _student_context(session: LiveSession, user, error: str = "") -> dict:
    context = {"session": session, "slide": session.slide, "error": error, "now": timezone.now(),
               "rating_choices": rating_choices()}
    if session.is_question:
        mine = Response.objects.filter(session=session, slide_index=session.index, student=user).first()
        context["mine"] = mine
        if mine:
            context["mine_display"] = engine.answer_display(session.slide, mine.answer)
        if session.phase == Phase.RESULTS:
            context["correct_display"] = engine.correct_display(session.slide)
    if session.is_feedback or session.status == Status.ENDED:
        context["feedback_open"] = engine.can_give_feedback(session, user)
        context["feedback_given"] = session.participants.filter(student=user, feedback_given=True).exists()
    if session.phase == Phase.RESULTS or session.status == Status.ENDED:
        board = engine.leaderboard(session, limit=None)
        context["leaderboard"] = board[:5]
        context["my_points"] = sum(Response.objects.filter(session=session, student=user)
                                   .values_list("points", flat=True))
        context["my_total"] = next((r["total"] for r in board if r["student"] == user.pk), 0)
        context["my_rank"] = next((i + 1 for i, r in enumerate(board) if r["student"] == user.pk), None)
    return context


def _parse_answer(slide: dict, post) -> object:
    kind = slide["question"]["type"]
    if kind == "choice":
        return post.get("choice", "")
    if kind == "multi_choice":
        return post.getlist("choice")
    if kind == "true_false":
        marks = {}
        for statement in slide["question"]["statements"]:
            value = post.get(f"tf_{statement['key']}")
            if value in ("1", "0"):
                marks[statement["key"]] = value == "1"
        return marks
    return post.get("value", "").strip()


@login_required
@require_POST
def student_answer(request, pk):
    session = _student_session(request, pk)
    error = ""
    if session.is_question:
        try:
            engine.submit(session.pk, request.user, _parse_answer(session.slide, request.POST))
        except engine.ActionError as exc:
            error = str(exc)
    session.refresh_from_db()
    return render(request, "live/_student_stage.html", _student_context(session, request.user, error))


@login_required
@require_POST
def student_feedback(request, pk):
    session = _student_session(request, pk)
    error = ""
    try:
        rating = int(request.POST.get("rating", "0"))
    except ValueError:
        rating = 0
    try:
        engine.submit_feedback(session.pk, request.user, rating, request.POST.get("comment", ""))
    except engine.ActionError as exc:
        error = str(exc)
    session.refresh_from_db()
    return render(request, "live/_student_stage.html", _student_context(session, request.user, error))


# ------------------------------------------------------------------------------ SSE


def _stream_access(user, pk) -> tuple[bool, bool]:
    """(¿puede ver el flujo?, ¿es el docente?)."""
    session = LiveSession.objects.filter(pk=pk).select_related("course").first()
    if session is None or not user.is_authenticated:
        return False, False
    is_teacher = session.course.owner_id == user.pk
    return is_teacher or can_attend(user, session), is_teacher


def _released(func):
    """Las consultas del flujo SSE devuelven la conexión al pool apenas terminan: la petición
    dura toda la clase y, si no, cada teléfono retendría una conexión todo ese tiempo."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            if not db_connection.in_atomic_block:    # (en las pruebas todo va dentro de una transacción)
                db_connection.close()
    return sync_to_async(wrapper)


_touch = _released(engine.touch)
# También suelta la conexión que abrió la autenticación de la petición (mismo hilo).
_authorize_stream = _released(_stream_access)


def _snapshot_db(pk) -> dict | None:
    engine.expire_if_due(pk)
    return (LiveSession.objects.filter(pk=pk)
            .values("state_version", "answers_version", "status", "phase", "index").first())


_snapshot_from_db = _released(_snapshot_db)
#: {sesión: (instante, estado)}: todos los flujos de una clase en este proceso comparten un sondeo.
_shared: dict[int, tuple[float, dict | None]] = {}
_shared_locks: dict[int, asyncio.Lock] = {}


async def _snapshot(pk) -> dict | None:
    """Estado de la sesión, consultado como mucho una vez por intervalo de sondeo y proceso.

    Sin esto, cada teléfono consultaba por su cuenta: 30 estudiantes eran 60 consultas idénticas
    por segundo. Ahora son 2 por proceso, haya 5 o 100 estudiantes.
    """
    fresh = settings.LIVE_POLL_SECONDS * 0.5
    hit = _shared.get(pk)
    if hit and time.monotonic() - hit[0] < fresh:
        return hit[1]
    async with _shared_locks.setdefault(pk, asyncio.Lock()):
        hit = _shared.get(pk)
        if hit and time.monotonic() - hit[0] < fresh:
            return hit[1]
        value = await _snapshot_from_db(pk)
        _shared[pk] = (time.monotonic(), value)
        if len(_shared) > 200:                         # olvidar sesiones viejas
            for old in [k for k, (t, _v) in _shared.items() if time.monotonic() - t > 60]:
                _shared.pop(old, None)
                _shared_locks.pop(old, None)
        return value


async def events(request, pk):
    """Flujo SSE: avisa cuándo cambió algo; el navegador pide el fragmento que le toca.

    Enviar solo versiones —no HTML— mantiene el flujo diminuto y deja la autorización y el
    renderizado en las vistas normales, que ya están probadas.
    """
    user = await request.auser()
    allowed, is_teacher = await _authorize_stream(user, pk)
    if not allowed:
        return HttpResponseForbidden()

    poll = settings.LIVE_POLL_SECONDS
    heartbeat_every = max(1, int(settings.LIVE_HEARTBEAT_SECONDS / poll))
    touch_every = max(1, int(engine.TOUCH_EVERY_S / poll))

    async def stream():
        last, idle, ticks = None, 0, 0
        yield "retry: 2000\n\n"
        while True:
            if not is_teacher and ticks % touch_every == 0:
                await _touch(pk, user)         # «sigue conectado»: cuenta para el cierre automático
            ticks += 1
            snapshot = await _snapshot(pk)
            if snapshot is None:
                return
            if snapshot != last:
                yield f"data: {json.dumps(snapshot)}\n\n"
                last, idle = snapshot, 0
                if snapshot["status"] == Status.ENDED:
                    return
            else:
                idle += 1
                if idle >= heartbeat_every:  # mantiene viva la conexión a través de proxies
                    yield ": ping\n\n"
                    idle = 0
            await asyncio.sleep(poll)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # nginx: no acumular el flujo
    return response
