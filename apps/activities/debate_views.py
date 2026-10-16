"""Las acciones del debate (la lógica y sus reglas están en `debate.py`): el estudiante publica su
respuesta revisada, comenta y da «me gusta» desde la página de la actividad; el docente lee el foro,
comenta, oculta lo que no corresponda y decide cuándo se cierra."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.courses.access import owned_course, teacher_required

from . import debate, engine
from .models import Activity, DebateEntry, DebateReply
from .views import _student_activity


def _fetched(request) -> bool:
    return request.headers.get("X-Requested-With") == "fetch"


def _back(activity, anchor: str = "") -> str:
    url = reverse("activity_student", args=[activity.course_id, activity.pk])
    return f"{url}#{anchor}" if anchor else url


def _entry(activity, pk) -> DebateEntry:
    return get_object_or_404(DebateEntry, pk=pk, activity=activity)


def _reply(activity, pk) -> DebateReply:
    return get_object_or_404(DebateReply.objects.select_related("entry"), pk=pk, entry__activity=activity)


# -------------------------------------------------------------------- estudiante


@login_required
@require_POST
def debate_revise(request, course_pk, pk):
    """Publicar la respuesta revisada (o la postura de quien no entregó). No cambia la nota."""
    activity = _student_activity(request, course_pk, pk)
    key = request.POST.get("key", "")
    found = debate.records(activity).get(key)
    if found is None:
        raise Http404
    _item, question, _record = found
    state = engine.state(activity, request.user)
    value = engine.parse_answers([question], request.POST).get(key)
    try:
        debate.revise(activity, request.user, state, key, value, request.POST.get(f"w-{key}", ""))
    except debate.DebateError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Se publicó su respuesta en el debate."))
    return redirect(_back(activity, f"debate-{key}"))


@login_required
@require_POST
def debate_reply(request, course_pk, pk):
    activity = _student_activity(request, course_pk, pk)
    entry = _entry(activity, request.POST.get("entry"))
    try:
        reply = debate.add_reply(activity, entry, request.user, request.POST.get("body", ""),
                                 engine.state(activity, request.user))
    except debate.DebateError as exc:
        messages.error(request, str(exc))
        return redirect(_back(activity, f"entry-{entry.pk}"))
    return redirect(_back(activity, f"reply-{reply.pk}"))


@login_required
@require_POST
def debate_reply_delete(request, course_pk, pk, reply_pk):
    activity = _student_activity(request, course_pk, pk)
    reply = _reply(activity, reply_pk)
    try:
        debate.delete_reply(activity, reply, request.user)
    except debate.DebateError as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, _("Se eliminó su comentario."))
    return redirect(_back(activity, f"entry-{reply.entry_id}"))


@login_required
@require_POST
def debate_like(request, course_pk, pk):
    """Da o quita un «me gusta». Con JavaScript devuelve el número al momento."""
    activity = _student_activity(request, course_pk, pk)
    entry = _entry(activity, request.POST["entry"]) if request.POST.get("entry") else None
    reply = _reply(activity, request.POST.get("reply")) if entry is None else None
    try:
        count, liked = debate.toggle_like(activity, request.user, engine.state(activity, request.user),
                                          entry=entry, reply=reply)
    except debate.DebateError as exc:
        if _fetched(request):
            return JsonResponse({"error": str(exc)}, status=409)
        messages.error(request, str(exc))
        count, liked = None, None
    if _fetched(request):
        return JsonResponse({"count": count, "liked": liked})
    return redirect(_back(activity, f"entry-{entry.pk}" if entry else f"reply-{reply.pk}"))


# -------------------------------------------------------------------- docente


def _own(request, course_pk, pk) -> Activity:
    course = owned_course(request, course_pk)
    activity = get_object_or_404(Activity.objects.select_related("course__owner"), pk=pk, course=course)
    if not activity.debate_keys:
        raise Http404
    return activity


@teacher_required
def debate_teacher(request, course_pk, pk):
    """El foro de cada pregunta de debate, con quién falta, y cuándo se cierra."""
    activity = _own(request, course_pk, pk)
    now = timezone.now()
    engine.finalize_overdue(activity)
    debate.sync(activity, now)
    names = debate.names(activity)
    threads = []
    for key in activity.debate_keys:
        thread = debate.thread(activity, key, request.user, teacher=True, names_map=names)
        posted = {e.student_id for e in thread.entries}
        thread.missing = sorted(name for sid, name in names.items() if sid not in posted)
        threads.append(thread)
    return render(request, "activities/debate_teacher.html", {
        "course": activity.course, "activity": activity, "threads": threads, "now": now,
        "is_open": activity.debate_open(now), "is_closed": activity.debate_closed(now),
    })


@teacher_required
@require_POST
def debate_teacher_action(request, course_pk, pk):
    """Cerrar o reabrir el debate, comentar y ocultar o mostrar lo publicado. Cuándo se cierra solo
    se configura con las fechas de la actividad."""
    activity = _own(request, course_pk, pk)
    back = reverse("debate_teacher", args=[activity.course_id, activity.pk])
    action = request.POST.get("action", "")
    if action == "close":
        debate.close(activity)
        messages.success(request, _("El debate se cerró. Los estudiantes ya ven la corrección y la explicación de estas preguntas."))
    elif action == "reopen":
        debate.reopen(activity)
        messages.success(request, _("El debate se reabrió hasta que usted lo cierre."))
    elif action in ("hide", "show"):
        target = (_entry(activity, request.POST["entry"]) if request.POST.get("entry")
                  else _reply(activity, request.POST.get("reply")))
        debate.set_hidden(target, action == "hide")
        anchor = f"entry-{target.pk}" if isinstance(target, DebateEntry) else f"reply-{target.pk}"
        return redirect(f"{back}#{anchor}")
    elif action == "reply":
        entry = _entry(activity, request.POST.get("entry"))
        try:
            reply = debate.add_reply(activity, entry, request.user, request.POST.get("body", ""), None)
        except debate.DebateError as exc:
            messages.error(request, str(exc))
            return redirect(f"{back}#entry-{entry.pk}")
        return redirect(f"{back}#reply-{reply.pk}")
    else:
        raise Http404
    return redirect(back)
