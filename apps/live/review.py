"""Repaso de las clases dictadas: el estudiante recorre las diapositivas de una clase terminada y
consulta la respuesta de cada pregunta. Es material de estudio: no se califica ni se registra nada, y
no se muestran puntos, tiempos ni lo que respondió cada uno.

- **Lo que se proyectó y nada más** (`LiveSession.shown`): lo que el docente no alcanzó a mostrar, u
  ocultó en esa clase, puede ser parte de la próxima. La retroalimentación final no se repasa.
- **Hasta donde el docente diga** (`LiveSession.review_through`): si proyectó el final sin
  explicarlo, en los resultados de la clase elige la última diapositiva del repaso. Lo demás no se
  borra: ampliar el límite lo devuelve.
- Una clase que se inició por error y se terminó sin pasar de la primera diapositiva no aparece.
- **Preguntas en su versión vigente:** cada pregunta se toma de la presentación actual por su
  `item_id`, de modo que una clave corregida después de la clase llega al repaso. El contenido se
  muestra como se proyectó. Si la presentación se eliminó, todo sale de la copia de la clase.
- **Ninguna respuesta durante una clase:** mientras una clase del curso esté en la sala de espera, en
  curso o en pausa, las preguntas que contiene no muestran su respuesta en ningún repaso, porque podría
  estar abierta en ese momento.
- Ven el repaso los estudiantes aprobados del curso y su docente; los invitados, no. Las clases se
  listan en la pestaña Clases de la página del curso (`course_classes`).
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils.translation import pgettext

from apps.courses.access import can_view_course
from apps.courses.models import Course

from .models import LiveSession

Status = LiveSession.Status

#: Una clase que proyectó menos que esto se inició por error.
MIN_SHOWN = 2
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def counts(shown, through=None) -> bool:
    return len([i for i in shown or [] if through is None or i <= through]) >= MIN_SHOWN


def reviewable(session: LiveSession) -> bool:
    return session.status == Status.ENDED and counts(session.shown, session.review_through)


def projected(session: LiveSession) -> list[int]:
    """Los índices de las diapositivas proyectadas que se pueden repasar, en orden: sin la
    retroalimentación ni las ocultas."""
    out = []
    for index in sorted(set(session.shown or [])):
        if 0 <= index < len(session.slides):
            slide = session.slides[index]
            if slide["kind"] != "feedback" and not slide.get("hidden"):
                out.append(index)
    return out


def in_review(session: LiveSession) -> list[int]:
    """Lo proyectado hasta el límite que fijó el docente (`review_through`)."""
    through = session.review_through
    return [i for i in projected(session) if through is None or i <= through]


def withheld_items(course) -> set[str]:
    """Las preguntas de las clases que todavía no terminan: su respuesta no se muestra."""
    items = set()
    for slides in LiveSession.objects.active().filter(course=course).values_list("slides", flat=True):
        items.update(s.get("item_id") for s in slides if s.get("kind") == "question")
    return items


def answer_of(slide: dict) -> list[dict]:
    """La respuesta correcta en líneas para mostrar: la letra (o V/F) y el texto en markdown."""
    question, record = slide["question"], slide.get("solution") or {}
    kind = question["type"]
    if kind in ("choice", "multi_choice"):
        correct = set(record.get("correct") or [])
        return [{"tag": LETTERS[i % 26], "text": o["text"]}
                for i, o in enumerate(question.get("options") or []) if o["key"] in correct]
    if kind == "true_false":
        answers = record.get("answers") or {}
        return [{"tag": pgettext("inicial de verdadero", "V") if answers.get(s["key"])
                 else pgettext("inicial de falso", "F"), "text": s["text"]}
                for s in question.get("statements") or []]
    if kind == "text":
        accept = (record.get("grading") or {}).get("accept") or []
        return [{"tag": "", "text": str(accept[0])}] if accept else []
    value = record.get("solution")
    return [] if value is None else [{"tag": "", "text": str(value)}]


def slides_of(session: LiveSession, withheld: set[str]) -> list[dict]:
    """Las diapositivas del repaso, en el orden de la clase."""
    current = {}
    if session.deck is not None:
        current = {s["item_id"]: s for s in session.deck.slides if s.get("kind") == "question"}
    out = []
    for index in in_review(session):
        slide = session.slides[index]
        entry = {"slide": slide, "number": len(out) + 1, "correct_keys": set()}
        if slide["kind"] == "question":
            slide = entry["slide"] = current.get(slide["item_id"], slide)
            entry["survey"] = bool(slide["question"].get("survey"))
            entry["withheld"] = not entry["survey"] and slide["item_id"] in withheld
            if not entry["survey"] and not entry["withheld"]:
                entry["answer"] = answer_of(slide)
                entry["correct_keys"] = set((slide.get("solution") or {}).get("correct") or [])
        out.append(entry)
    return out


def class_groups(course, linked: bool) -> list[dict]:
    """Las clases que se pueden repasar, por unidad y en orden de fecha. Solo viene desplegada la
    unidad de la última clase, como en la pestaña Clases del docente."""
    from .sections import ordered

    sessions = [s for s in (LiveSession.objects.filter(course=course, status=Status.ENDED)
                            .order_by("started_at", "created_at")
                            .values("pk", "title", "started_at", "created_at", "shown", "review_through",
                                    "deck__section_id"))
                if counts(s["shown"], s["review_through"])]
    if not sessions:
        return []
    by_section: dict[int | None, list[dict]] = {}
    for s in sessions:
        by_section.setdefault(s["deck__section_id"], []).append(s)
    latest = sessions[-1]["deck__section_id"]
    groups = [{"section": section, "sessions": by_section[section.pk], "current": section.pk == latest}
              for section in ordered(course, linked) if section.pk in by_section]
    if None in by_section:
        groups.append({"section": None, "sessions": by_section[None], "current": latest is None})
    return groups


@login_required
def course_classes(request, pk):
    """La pestaña Clases de la página del curso: las clases terminadas, para repasarlas."""
    from apps.github.models import CourseRepo

    course = get_object_or_404(Course.objects.select_related("owner"), pk=pk)
    if not can_view_course(request.user, course):
        raise Http404
    return render(request, "live/classes.html", {
        "course": course, "tab": "clases",
        "live": LiveSession.objects.active().filter(course=course).first(),
        "class_groups": class_groups(course, linked=CourseRepo.objects.filter(course=course).exists()),
    })


@login_required
def class_review(request, course_pk, pk):
    course = get_object_or_404(Course.objects.select_related("owner"), pk=course_pk)
    if not can_view_course(request.user, course):
        raise Http404
    session = get_object_or_404(LiveSession.objects.select_related("deck__section"), pk=pk, course=course)
    if not reviewable(session):
        raise Http404
    slides = slides_of(session, withheld_items(course))
    return render(request, "live/review.html", {
        "course": course, "session": session, "slides": slides,
        "section": session.deck.section if session.deck else None,
        "questions": sum(1 for s in slides if s["slide"]["kind"] == "question"),
        "answers": any(s.get("answer") for s in slides),
    })
