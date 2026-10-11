"""El editor de contenido: presentaciones y actividades en un mismo estudio.

Panel lateral (tipo, sección e índice de diapositivas o ítems) | YAML en Monaco | vista previa.
El tipo lo dice el `schema` del texto: en un documento nuevo, elegir «Actividad» en el panel pone
su plantilla, y pegar una actividad convierte el editor en uno de actividad. Un documento ya
guardado conserva su tipo.

La vista previa y el panel trabajan sobre el texto y no guardan nada: solo «Guardar» (o Ctrl+S)
guarda, y en un curso vinculado eso es un commit en GitHub. Ni las presentaciones ni las
actividades admiten generadores, así que compilar aquí no ejecuta código del docente.
"""

import re

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from policlase_gen import activity as activityfmt
from policlase_gen import deck as deckfmt
from policlase_gen import deck_text, loader

from apps.courses.access import owned_course, teacher_required

from .models import Deck, Section

DECK, ACTIVITY = "deck", "activity"
MAX_SOURCE = 512 * 1024

DECK_TEMPLATE = r"""schema: policlase.deck/v1
title: 'Nueva clase'
defaults: { time_limit_s: 30 }

slides:
  - markdown: |
      # Título de la clase
      Contenido con matemáticas: $f(x) = x^2 - 4$.

  # El LaTeX va entre comillas simples: en las dobles, \t, \f, \b… son escapes de YAML.
  - item:
      id: L01-ejemplo
      questions:
        - id: q1
          type: choice
          prompt: '¿Cuáles son las raíces de $x^2 - 4$?'
          options:
            - { text: '$x = \pm 2$', correct: true }
            - { text: '$x = 4$' }
            - { text: '$x = \pm 4$' }
            - { text: 'No tiene raíces reales' }
"""

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

TEMPLATES = {DECK: DECK_TEMPLATE, ACTIVITY: ACTIVITY_TEMPLATE}
SCHEMA_LINE = re.compile(r"^schema\s*:\s*['\"]?policlase\.(deck|activity)/", re.MULTILINE)


def kind_of(source: str, default: str) -> str:
    """El tipo que declara el texto en su línea `schema`; sin ella, `default`."""
    match = SCHEMA_LINE.search(source or "")
    return match.group(1) if match else default


def _diagnostics(report) -> list[dict]:
    return [{"code": d.code, "severity": d.severity.value, "message": d.message,
             "line": d.line, "where": ".".join(p for p in (d.item_id, d.question_id) if p)}
            for d in report]


def _course_link(course):
    from apps.github.models import CourseRepo
    return CourseRepo.objects.filter(course=course).select_related("course__owner").first()


def _load(kind: str, source: str):
    return (activityfmt.load_activity_text if kind == ACTIVITY else deckfmt.load_deck_text)(source)


def _compile(kind: str, document) -> dict:
    return (activityfmt.compile_activity if kind == ACTIVITY else deckfmt.compile_deck)(document)


@teacher_required
def editor(request, course_pk, kind=DECK, pk=None):
    from apps.activities.models import Activity
    from apps.github import sync

    course = owned_course(request, course_pk)
    link = _course_link(course)
    if link and request.method != "POST":
        sync.maybe_pull(link)                    # abrir el editor sobre la última versión de GitHub
    model = Activity if kind == ACTIVITY else Deck
    content = get_object_or_404(model, pk=pk, course=course) if pk else None
    if content is None and request.GET.get("tipo") in ("actividad", ACTIVITY):
        kind = ACTIVITY
    source = content.source if content else TEMPLATES[kind]
    diagnostics = []
    sections = list(Section.objects.filter(course=course))
    wanted = request.POST.get("section") if request.method == "POST" else request.GET.get("seccion")
    if wanted is None:
        section = content.section if content else None
    else:
        section = next((x for x in sections if str(x.pk) == wanted), None)

    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload:
            if upload.size > MAX_SOURCE:
                messages.error(request, _("El archivo supera 512 KB."))
                return redirect(request.get_full_path())
            source = upload.read().decode("utf-8", errors="replace")
        else:
            source = request.POST.get("source", "")
        found = kind_of(source, kind)
        if content is None:
            kind = found
        elif found != kind:
            messages.error(request, _("Este documento es una presentación y el texto es de una actividad (o al "
                                      "revés); no se guardó. Para crear el otro tipo, use «Nuevo» en la sección."))
            found = None
        if found:
            response = _save(request, course, link, kind, content, source, section)
            if response:
                return response
        document, report = _load(kind, source)
        diagnostics = _diagnostics(report)

    return render(request, "live/studio.html", {
        "course": course, "kind": kind, "content": content, "source": source, "diagnostics": diagnostics,
        "link": link, "sections": sections, "section": section, "templates": TEMPLATES,
        "back": _back_url(course, kind, content),
    })


def _back_url(course, kind, content) -> str:
    from django.urls import reverse
    if content is None:
        return reverse("course_manage", args=[course.pk])
    if kind == ACTIVITY:
        return reverse("activity_detail", args=[course.pk, content.pk])
    return reverse("deck_view", args=[course.pk, content.pk])


def _save(request, course, link, kind, content, source, section):
    """Valida, confirma en GitHub (si hay vínculo) y guarda. Devuelve la redirección, o None si no
    se guardó (los mensajes ya dicen por qué y el texto sigue en el editor)."""
    from apps.activities.models import Activity
    from apps.github import sync

    document, report = _load(kind, source)
    if document is None or report.errors:
        messages.error(request, _("La actividad tiene errores; no se guardó.") if kind == ACTIVITY
                       else _("La presentación tiene errores; no se guardó."))
        return None
    compiled = _compile(kind, document)
    new = content is None
    content = content or (Activity if kind == ACTIVITY else Deck)(course=course)
    content.title = compiled["title"]
    content.section = section
    if link:
        # GitHub primero: si allá cambió el archivo, no se pisa nada y el texto sigue aquí.
        try:
            content.github_path, content.github_sha = sync.push(link, content, source)
        except sync.Conflict:
            messages.error(request, _("El archivo cambió en GitHub después de que lo abrió; no se guardó para "
                                      "no perder esos cambios. Copie su texto, recargue el editor y vuelva a "
                                      "aplicarlo."))
            return None
        except sync.GitHubError as exc:
            messages.error(request, _("No se guardó: %(error)s") % {"error": exc})
            return None
    content.source, content.compiled = source, compiled
    content.save()
    if kind == ACTIVITY:
        messages.success(request, _("Actividad «%(title)s» guardada: %(questions)d preguntas, %(points)s puntos.")
                         % {"title": content.title, "questions": len(content.questions),
                            "points": f"{content.points:g}"})
    else:
        messages.success(request, _("Presentación «%(title)s» guardada: %(slides)d diapositivas, "
                                    "%(questions)d preguntas.")
                         % {"title": content.title, "slides": len(content.slides),
                            "questions": content.question_count})
    if link:
        messages.info(request, _("Confirmado en GitHub: %(path)s") % {"path": content.github_path})
    if kind == ACTIVITY:
        if new:
            messages.info(request, _("Elija su ponderación y su fecha de entrega, y publíquela."))
        return redirect("activity_detail", course_pk=course.pk, pk=content.pk)
    return redirect("deck_view", course_pk=course.pk, pk=content.pk)


@teacher_required
@require_POST
def preview(request, course_pk):
    """Vista previa en vivo: valida, compila y devuelve el contenido renderizado y su índice."""
    owned_course(request, course_pk)
    source = request.POST.get("source", "")
    posted = request.POST.get("kind") if request.POST.get("kind") in TEMPLATES else DECK
    # Un documento guardado conserva su tipo: se valida como tal aunque el texto diga otra cosa.
    kind = posted if request.POST.get("fixed") else kind_of(source, posted)
    if len(source) > MAX_SOURCE:
        return JsonResponse({"kind": kind, "html": None, "lines": [], "diagnostics": [{
            "code": "", "severity": "error", "line": None, "where": "", "message": _("El archivo supera 512 KB.")}]})
    document, report = _load(kind, source)
    payload = {"kind": kind, "diagnostics": _diagnostics(report), "html": None, "lines": []}
    key = "items" if kind == ACTIVITY else "slides"
    if document is not None and not report.errors:
        compiled = _compile(kind, document)
        if kind == ACTIVITY:
            payload["html"] = render_to_string("activities/_preview.html", {"content": compiled}, request)
            payload["outline"] = render_to_string("activities/_item_outline.html", {"items": compiled["items"]},
                                                  request)
        else:
            payload["html"] = render_to_string("live/_deck_slides.html", {"slides": compiled["slides"]}, request)
            payload["outline"] = render_to_string("live/_deck_outline.html", {"slides": compiled["slides"]},
                                                  request)
        payload["title"] = compiled["title"]
    if document is not None and isinstance(document.get(key), list):
        # Línea donde empieza cada diapositiva o ítem: el editor resalta la que tiene el cursor.
        payload["lines"] = [loader.line_of(document[key], i) for i in range(len(document[key]))]
    return JsonResponse(payload)


@teacher_required
@require_POST
def ops(request, course_pk):
    """Panel lateral: reordenar, ocultar/mostrar diapositivas, activar la retroalimentación;
    reordenar los ítems de una actividad.

    Edita el texto (policlase_gen.deck_text) y lo devuelve; el editor lo aplica como una edición
    más, deshacible con Ctrl+Z. Nada se guarda hasta «Guardar».
    """
    owned_course(request, course_pk)
    source, op = request.POST.get("source", ""), request.POST.get("op", "")
    activity = kind_of(source, DECK) == ACTIVITY
    try:
        index = int(request.POST.get("index", "-1"))
        if op == "move":
            target = int(request.POST.get("target", "-1"))
            source = (deck_text.move_item if activity else deck_text.move_slide)(source, index, target)
        elif op in ("hide", "show") and not activity:
            source = deck_text.set_hidden(source, index, op == "hide")
        elif op in ("feedback_on", "feedback_off") and not activity:
            source = deck_text.set_feedback(source, op == "feedback_on")
        else:
            return JsonResponse({"error": _("Operación desconocida.")}, status=400)
    except (ValueError, deck_text.EditError) as exc:
        return JsonResponse({"error": _("Corrija los errores antes de usar el panel: %(error)s") % {"error": exc}},
                            status=400)
    return JsonResponse({"source": source})
