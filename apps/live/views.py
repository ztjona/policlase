import asyncio
import json

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from policlase_gen import deck as deckfmt
from policlase_gen import loader

from apps.courses.access import is_approved, owned_course, teacher_required
from apps.courses.models import Enrollment

from . import engine
from .guests import qr_svg
from .models import Deck, LiveSession, Response

Phase = LiveSession.Phase
Status = LiveSession.Status

DECK_TEMPLATE = """schema: policlase.deck/v1
title: "Nueva clase"
defaults: { time_limit_s: 30 }

slides:
  - markdown: |
      # Título de la clase
      Contenido con matemáticas: $f(x) = x^2 - 4$.

  - item:
      id: L01-ejemplo
      points: 1
      questions:
        - id: q1
          type: choice
          points: 1
          prompt: "¿Cuáles son las raíces de $x^2 - 4$?"
          options:
            - { text: "$x = \\\\pm 2$", correct: true }
            - { text: "$x = 4$" }
            - { text: "$x = \\\\pm 4$" }
            - { text: "No tiene raíces reales" }
"""


# -------------------------------------------------------------------- presentaciones


def _diagnostics(report) -> list[dict]:
    return [{"code": d.code, "severity": d.severity.value, "message": d.message,
             "line": d.line, "where": ".".join(p for p in (d.item_id, d.question_id) if p)}
            for d in report]


def _course_link(course):
    from apps.github.models import CourseRepo
    return CourseRepo.objects.filter(course=course).select_related("course__owner").first()


@teacher_required
def deck_edit(request, course_pk, pk=None):
    from apps.github import sync

    course = owned_course(request, course_pk)
    link = _course_link(course)
    if link and request.method != "POST":
        sync.maybe_pull(link)                    # abrir el editor sobre la última versión de GitHub
    deck = get_object_or_404(Deck, pk=pk, course=course) if pk else None
    source = deck.source if deck else DECK_TEMPLATE
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

        document, report = deckfmt.load_deck_text(source)
        diagnostics = _diagnostics(report)
        if document is not None and not report.errors:
            compiled = deckfmt.compile_deck(document)
            deck = deck or Deck(course=course)
            deck.title = compiled["title"]
            saved = True
            if link:
                # GitHub primero: si allá cambió el archivo, no se pisa nada y el texto sigue aquí.
                try:
                    deck.github_path, deck.github_sha = sync.push_deck(link, deck, source)
                except sync.Conflict:
                    saved = False
                    messages.error(request, _("La presentación cambió en GitHub después de que la abrió; "
                                              "no se guardó para no perder esos cambios. Copie su texto, "
                                              "recargue el editor y vuelva a aplicarlo."))
                except sync.GitHubError as exc:
                    saved = False
                    messages.error(request, _("No se guardó: %(error)s") % {"error": exc})
            if saved:
                deck.source, deck.compiled = source, compiled
                deck.save()
                messages.success(request, _("Presentación «%(title)s» guardada: %(slides)d diapositivas, "
                                            "%(questions)d preguntas.")
                                 % {"title": deck.title, "slides": len(deck.slides),
                                    "questions": deck.question_count})
                if link:
                    messages.info(request, _("Confirmada en GitHub: %(path)s") % {"path": deck.github_path})
                return redirect("deck_view", course_pk=course.pk, pk=deck.pk)
        else:
            messages.error(request, _("La presentación tiene errores; no se guardó."))

    return render(request, "live/deck_edit.html", {
        "course": course, "deck": deck, "source": source, "diagnostics": diagnostics, "link": link,
    })


@teacher_required
@require_POST
def deck_preview(request, course_pk):
    """Vista previa en vivo del editor: valida, compila y devuelve las diapositivas renderizadas.

    Las presentaciones no tienen generadores (policlase-gen las rechaza): compilarlas en el
    servidor no ejecuta código del docente.
    """
    owned_course(request, course_pk)
    source = request.POST.get("source", "")
    if len(source) > 512 * 1024:
        return JsonResponse({"diagnostics": [{"code": "", "severity": "error", "line": None, "where": "",
                                              "message": _("El archivo supera 512 KB.")}]})
    document, report = deckfmt.load_deck_text(source)
    payload = {"diagnostics": _diagnostics(report), "html": None, "lines": []}
    if document is not None and not report.errors:
        compiled = deckfmt.compile_deck(document)
        payload["html"] = render_to_string("live/_deck_slides.html", {"slides": compiled["slides"]}, request)
        payload["title"] = compiled["title"]
    if document is not None and isinstance(document.get("slides"), list):
        # Línea donde empieza cada diapositiva: el editor resalta la que tiene el cursor.
        payload["lines"] = [loader.line_of(document["slides"], i) for i in range(len(document["slides"]))]
    return JsonResponse(payload)


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
            sync.delete_deck(link, deck)
        except sync.GitHubError as exc:
            messages.error(request, _("No se eliminó: %(error)s") % {"error": exc})
            return redirect("deck_view", course_pk=course.pk, pk=deck.pk)
    deck.delete()
    messages.success(request, _("Presentación eliminada. Las clases ya dictadas conservan sus resultados."))
    return redirect("course_manage", pk=course.pk)


# ------------------------------------------------------------------ sesión: docente


@teacher_required
@require_POST
def session_start(request, course_pk, deck_pk):
    course = owned_course(request, course_pk)
    deck = get_object_or_404(Deck, pk=deck_pk, course=course)

    active = LiveSession.objects.active().filter(course=course).first()
    if active:
        messages.info(request, _("Ya hay una clase en vivo en este curso; se abrió esa."))
        return redirect("present", pk=active.pk)

    for _attempt in range(10):  # el PIN es aleatorio; en el raro caso de choque se reintenta
        try:
            with transaction.atomic():
                session = LiveSession.objects.create(
                    course=course, deck=deck, title=deck.title, slides=deck.slides,
                )
            break
        except IntegrityError:
            continue
    else:
        messages.error(request, _("No se pudo generar un PIN libre. Intente de nuevo."))
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
    return render(request, "live/_present_stage.html", context)


@teacher_required
@require_POST
def present_action(request, pk):
    session = _own_session(request, pk)
    try:
        engine.apply(session.pk, request.POST.get("action", ""))
    except engine.ActionError as exc:
        return HttpResponse(str(exc), status=409)
    return HttpResponse(status=204)


@teacher_required
def session_results(request, pk):
    session = _own_session(request, pk)
    questions = [(i, session.slides[i]) for i in session.question_indices]
    students = (Enrollment.objects.filter(course=session.course, status=Enrollment.Status.APPROVED)
                .select_related("student"))
    by_student: dict[int, dict[int, Response]] = {}
    for r in Response.objects.filter(session=session):
        by_student.setdefault(r.student_id, {})[r.slide_index] = r

    max_points = session.max_points
    rows = []
    for e in students:
        answers = by_student.get(e.student_id, {})
        total = sum(r.points for r in answers.values())
        rows.append({
            "student": e.student,
            "cells": [answers.get(i) for i, _q in questions],
            "total": total,
            "percent": round(100 * total / max_points) if max_points else 0,
            "joined": session.participants.filter(student=e.student).exists(),
        })
    rows.sort(key=lambda r: (-r["total"], r["student"].last_name))

    # Invitados: quienes entraron por el enlace sin estar inscritos en el curso.
    enrolled = {e.student_id for e in students}
    guests = []
    for p in session.participants.exclude(student_id__in=enrolled).select_related("student"):
        answers = by_student.get(p.student_id, {})
        total = sum(r.points for r in answers.values())
        guests.append({"student": p.student, "cells": [answers.get(i) for i, _q in questions],
                       "total": total, "percent": round(100 * total / max_points) if max_points else 0})
    guests.sort(key=lambda r: -r["total"])
    return render(request, "live/results.html", {
        "session": session, "course": session.course, "questions": questions,
        "rows": rows, "guests": guests, "max_points": max_points,
    })


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


def _student_context(session: LiveSession, user, error: str = "") -> dict:
    context = {"session": session, "slide": session.slide, "error": error, "now": timezone.now()}
    if session.is_question:
        mine = Response.objects.filter(session=session, slide_index=session.index, student=user).first()
        context["mine"] = mine
        if mine:
            context["mine_display"] = engine.answer_display(session.slide, mine.answer)
        if session.phase == Phase.RESULTS:
            context["correct_display"] = engine.correct_display(session.slide)
    if session.phase == Phase.RESULTS or session.status == Status.ENDED:
        board = engine.leaderboard(session, limit=None)
        context["leaderboard"] = board[:5]
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


# ------------------------------------------------------------------------------ SSE


@sync_to_async
def _authorize_stream(user, pk) -> bool:
    session = LiveSession.objects.filter(pk=pk).select_related("course").first()
    if session is None or not user.is_authenticated:
        return False
    return session.course.owner_id == user.pk or can_attend(user, session)


@sync_to_async
def _snapshot(pk) -> dict | None:
    engine.expire_if_due(pk)
    return (LiveSession.objects.filter(pk=pk)
            .values("state_version", "answers_version", "status", "phase", "index").first())


async def events(request, pk):
    """Flujo SSE: avisa cuándo cambió algo; el navegador pide el fragmento que le toca.

    Enviar solo versiones —no HTML— mantiene el flujo diminuto y deja la autorización y el
    renderizado en las vistas normales, que ya están probadas.
    """
    user = await request.auser()
    if not await _authorize_stream(user, pk):
        return HttpResponseForbidden()

    poll = settings.LIVE_POLL_SECONDS
    heartbeat_every = max(1, int(settings.LIVE_HEARTBEAT_SECONDS / poll))

    async def stream():
        last, idle = None, 0
        yield "retry: 2000\n\n"
        while True:
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
