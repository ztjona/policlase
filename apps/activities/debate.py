"""El debate de las preguntas marcadas `debate: true` en una actividad.

- Se responden con una justificación (`Attempt.reasons`). Nadie ve las de otros antes de la fecha
  de entrega.
- Desde el vencimiento hasta `Activity.debate_close` (por omisión, el cierre de la revisión), cada una
  tiene su foro: la postura de cada estudiante (`DebateEntry`: su respuesta y su justificación, con
  su nombre corto), comentarios de un nivel (`DebateReply`) y «me gusta» públicos (`DebateLike`).
- **Primero se publica, después se lee:** ve el foro de una pregunta quien tiene su postura en ella,
  y solo si ya no puede entregar con nota (si todavía puede, sería responder con las respuestas de
  los compañeros a la vista). Quien no entregó, y ya no puede, se suma publicando su postura.
- Durante el debate, cada estudiante puede publicar una respuesta revisada, sin nota. La
  corrección de la pregunta y la explicación de su ítem no se muestran hasta que el debate se
  cierra: entonces aparecen, como síntesis del docente.
- El docente lee todo, comenta y oculta lo que no corresponda.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext as _
from policlase_gen.grading import grade

from apps.courses.models import Enrollment, short_name

from . import engine
from .models import Activity, Answer, DebateEntry, DebateLike, DebateReply

#: Tope de una justificación y de un comentario: un párrafo, no un documento.
MAX_REASON = 3000
MAX_REPLY = 2000
#: La vista previa de una justificación en el foro; el resto se abre con «Leer completa».
PREVIEW = 280


class DebateError(engine.ActionError):
    """La acción no se puede hacer ahora; el mensaje se le muestra a quien la intentó."""


def records(activity: Activity) -> dict[str, tuple[dict, dict, dict]]:
    """Clave → (ítem, pregunta, solución) de las preguntas de debate de la versión actual."""
    keys = set(activity.debate_keys)
    out = {}
    for item in activity.compiled.get("items", []):
        for q in item.get("questions", []):
            if q["key"] in keys:
                out[q["key"]] = (item, q, item.get("solutions", {}).get(q["id"], {}))
    return out


def in_debate(activity: Activity, key: str, now=None) -> bool:
    """La corrección de esta pregunta todavía no se muestra: el debate no se ha cerrado."""
    return bool(activity.due_at) and key in activity.debate_keys and not activity.debate_closed(now)


def hidden_items(activity: Activity, now=None) -> set[str]:
    """Ítems cuya explicación espera el cierre del debate."""
    if not activity.due_at or activity.debate_closed(now):
        return set()
    return {item["item_id"] for item, _q, _r in records(activity).values()}


# ------------------------------------------------------------------------ posturas


def sync(activity: Activity, now=None) -> None:
    """Las posturas salen del intento que cuenta de cada estudiante: se crean las que faltan y se
    actualizan si el estudiante envió después otro intento. Lo revisado durante el debate no se
    toca. Antes del vencimiento no hace nada: el foro todavía no existe."""
    keys = activity.debate_keys
    if not keys or not activity.is_past_due(now):
        return
    counted = engine.counted_attempts(activity)
    if not counted:
        return
    by_pk = {a.pk: a for a in counted.values()}
    current = {(e.student_id, e.key): e for e in DebateEntry.objects.filter(activity=activity, key__in=keys)}
    new = []
    for attempt_id, key, value in (Answer.objects.filter(attempt_id__in=by_pk, key__in=keys)
                                   .values_list("attempt_id", "key", "value")):
        attempt = by_pk[attempt_id]
        reason = str((attempt.reasons or {}).get(key, "")).strip()[:MAX_REASON]
        value = None if engine.blank(value) else value
        entry = current.get((attempt.student_id, key))
        if entry is None:
            if value is not None or reason:
                new.append(DebateEntry(activity=activity, key=key, student_id=attempt.student_id,
                                       value=value, reason=reason))
        elif entry.value != value or entry.reason != reason:
            entry.value, entry.reason = value, reason
            entry.save(update_fields=["value", "reason"])
    if new:
        DebateEntry.objects.bulk_create(new, ignore_conflicts=True)


@dataclass
class Access:
    """Qué puede hacer un estudiante con el foro de una pregunta."""

    status: str                      # soon · wait · join · open · closed · none
    entry: DebateEntry | None = None
    message: str = ""

    @property
    def can_read(self) -> bool:
        return self.status in ("open", "closed")

    @property
    def can_post(self) -> bool:
        return self.status in ("open", "join")


def access(activity: Activity, student, state: engine.State, key: str, now=None) -> Access:
    now = now or timezone.now()
    if key not in activity.debate_keys or not activity.due_at:
        return Access("none")
    entry = DebateEntry.objects.filter(activity=activity, key=key, student=student).first()
    if not activity.is_past_due(now):
        return Access("soon", entry, _("El debate se abre el %(when)s, al vencer la fecha de entrega.")
                      % {"when": engine._when(activity.due_at)})
    if state.draft is not None or state.can_start:
        # Todavía puede entregar con nota: leer a los compañeros sería responder con sus respuestas a la vista.
        return Access("wait", entry, _("Para participar en el debate, envíe primero su entrega."))
    if activity.debate_closed(now):
        return Access("closed", entry, _("El debate se cerró el %(when)s.") % {"when": engine._when(activity.debate_close)})
    if entry is None:
        return Access("join", None, _("Para leer las respuestas de sus compañeros, publique primero la suya."))
    return Access("open", entry)


# ------------------------------------------------------------------------ el foro


@dataclass
class Thread:
    """El foro de una pregunta, listo para mostrar."""

    key: str
    item: dict
    question: dict
    record: dict
    entries: list = field(default_factory=list)
    total: int = 0                   # posturas visibles (sin las ocultas)
    replies: int = 0
    missing: list = field(default_factory=list)

    @property
    def as_panel(self) -> dict:
        """Lo que espera `_debate.html` (para la vista del docente, sin acceso que evaluar)."""
        return {"key": self.key, "question": self.question, "record": self.record, "item": self.item,
                "access": None, "thread": self}


def names(activity: Activity) -> dict[int, str]:
    """Estudiante → nombre corto (el preferido, o el primer nombre, y el primer apellido)."""
    rows = (Enrollment.objects.filter(course_id=activity.course_id, status=Enrollment.Status.APPROVED)
            .select_related("student"))
    return {e.student_id: short_name(e.student, e.preferred_name) for e in rows}


def thread(activity: Activity, key: str, viewer, teacher: bool = False, names_map=None) -> Thread:
    """Las posturas de una pregunta con sus comentarios y sus «me gusta». Lo oculto lo ven el
    docente y su autor (con el aviso); los demás, no."""
    item, question, record = records(activity)[key]
    names_map = names_map if names_map is not None else names(activity)
    owner_id = activity.course.owner_id
    entries = list(DebateEntry.objects.filter(activity=activity, key=key).select_related("student")
                   .annotate(n_likes=Count("likes", distinct=True)))
    replies = list(DebateReply.objects.filter(entry__in=entries).select_related("author")
                   .annotate(n_likes=Count("likes", distinct=True)))
    liked_entries = set(DebateLike.objects.filter(user=viewer, entry__in=entries).values_list("entry_id", flat=True))
    liked_replies = set(DebateLike.objects.filter(user=viewer, reply__in=replies).values_list("reply_id", flat=True))
    by_entry: dict[int, list] = {}
    for r in replies:
        if r.hidden and not teacher and r.author_id != viewer.pk:
            continue
        r.mine, r.liked = r.author_id == viewer.pk, r.pk in liked_replies
        r.is_teacher = r.author_id == owner_id
        r.name = r.author.display_name if r.is_teacher else names_map.get(r.author_id) or short_name(r.author)
        by_entry.setdefault(r.entry_id, []).append(r)
    out = []
    for e in entries:
        if e.hidden and not teacher and e.student_id != viewer.pk:
            continue
        e.mine, e.liked = e.student_id == viewer.pk, e.pk in liked_entries
        e.name = names_map.get(e.student_id) or short_name(e.student)
        e.reply_list = by_entry.get(e.pk, [])
        e.preview = _preview(e.shown_reason)
        out.append(e)
    out.sort(key=lambda e: (not e.mine, e.created_at, e.pk))           # la propia, primero
    visible = [e for e in out if not e.hidden]
    return Thread(key=key, item=item, question=question, record=record, entries=out, total=len(visible),
                  replies=sum(1 for e in visible for r in e.reply_list if not r.hidden))


def _preview(text: str) -> str:
    """El comienzo de una justificación, cortado en una palabra; vacío si cabe entera."""
    text = (text or "").strip()
    if len(text) <= PREVIEW:
        return ""
    cut = text[:PREVIEW].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"


def verdict(record: dict, question: dict, value) -> bool | None:
    """¿Es correcta una respuesta (revisada, sin nota)? None si no se puede decir."""
    if engine.blank(value) or question.get("survey"):
        return None
    result = grade(question, value, solution=record)
    return result.correct if result.gradable else None


# ------------------------------------------------------------------------ acciones


def _open_for(activity: Activity, user, now=None) -> None:
    if not activity.debate_open(now):
        raise DebateError(_("El debate de esta actividad no está abierto."))


def revise(activity: Activity, student, state: engine.State, key: str, value, reason: str, now=None) -> DebateEntry:
    """Publicar (o cambiar) la respuesta revisada y su justificación. No cambia la nota."""
    now = now or timezone.now()
    _open_for(activity, student, now)
    sync(activity, now)                        # la respuesta original, antes de la revisada
    current = access(activity, student, state, key, now)
    if not current.can_post:
        raise DebateError(current.message)
    _item, question, record = records(activity)[key]
    reason = (reason or "").strip()[:MAX_REASON]
    if engine.blank(value) and not reason:
        raise DebateError(_("Escriba su respuesta o su justificación."))
    if not engine.blank(value):
        result = grade(question, value, solution=record)
        if not result.gradable:
            raise DebateError(" ".join(result.format_errors))
    try:
        with transaction.atomic():
            entry, _created = DebateEntry.objects.get_or_create(activity=activity, key=key, student=student)
    except IntegrityError:                     # doble clic
        entry = DebateEntry.objects.get(activity=activity, key=key, student=student)
    entry.revised_value = None if engine.blank(value) else value
    entry.revised_reason, entry.revised_at = reason, now
    entry.save(update_fields=["revised_value", "revised_reason", "revised_at"])
    return entry


def can_read(activity: Activity, user, entry: DebateEntry, state: engine.State | None) -> bool:
    if user.pk == activity.course.owner_id:
        return True
    return state is not None and access(activity, user, state, entry.key).can_read


def add_reply(activity: Activity, entry: DebateEntry, author, body: str, state: engine.State | None) -> DebateReply:
    _open_for(activity, author)
    if not can_read(activity, author, entry, state) or (entry.hidden and author.pk != activity.course.owner_id):
        raise DebateError(_("No puede comentar esta respuesta."))
    body = (body or "").strip()
    if not body:
        raise DebateError(_("Escriba su comentario."))
    return DebateReply.objects.create(entry=entry, author=author, body=body[:MAX_REPLY])


def delete_reply(activity: Activity, reply: DebateReply, user) -> None:
    """El autor retira su comentario mientras el debate sigue abierto."""
    if reply.author_id != user.pk:
        raise DebateError(_("Solo su autor puede eliminar un comentario."))
    _open_for(activity, user)
    reply.delete()


def toggle_like(activity: Activity, user, state: engine.State | None, entry: DebateEntry | None = None,
                reply: DebateReply | None = None) -> tuple[int, bool]:
    """Da o quita un «me gusta». Devuelve (cuántos tiene, si este usuario lo dio)."""
    _open_for(activity, user)
    target = entry or reply.entry
    if not can_read(activity, user, target, state):
        raise DebateError(_("No puede reaccionar a esta respuesta."))
    author = entry.student_id if entry else reply.author_id
    if author == user.pk:
        raise DebateError(_("No puede dar «me gusta» a lo que usted publicó."))
    lookup = {"entry": entry} if entry else {"reply": reply}
    found = DebateLike.objects.filter(user=user, **lookup)
    if found.exists():
        found.delete()
        liked = False
    else:
        try:
            with transaction.atomic():
                DebateLike.objects.create(user=user, **lookup)
        except IntegrityError:
            pass
        liked = True
    return DebateLike.objects.filter(**lookup).count(), liked


def set_hidden(target, hidden: bool) -> None:
    target.hidden = hidden
    target.save(update_fields=["hidden"])


def close(activity: Activity, now=None) -> None:
    activity.debate_mode, activity.debate_until = Activity.DebateClose.FIXED, now or timezone.now()
    activity.save(update_fields=["debate_mode", "debate_until"])


def reopen(activity: Activity) -> None:
    activity.debate_mode, activity.debate_until = Activity.DebateClose.MANUAL, None
    activity.save(update_fields=["debate_mode", "debate_until"])
