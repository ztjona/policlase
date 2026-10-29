"""El editor de contenido: clases, actividades, evaluaciones y repasos en un mismo estudio y un
mismo formato (`policlase/v1`, con segmentos; los formatos anteriores siguen valiendo).

Panel lateral (tipo, sección e índice) | YAML en Monaco | vista previa. El tipo lo dice el texto
(`use:`, o el `schema` de los formatos anteriores): en un documento nuevo, elegir «Actividad» en
el panel cambia `use:` (o pone la plantilla si aún no escribió nada), y pegar una actividad
convierte el editor en uno de actividad. Un documento ya guardado conserva su tipo.

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
from policlase_gen import deck_text, loader
from policlase_gen import document as content_format

from apps.activities.models import WORK_USES
from apps.courses.access import owned_course, teacher_required
from apps.github import images

from .models import Deck, Section

DECK, ACTIVITY = "deck", "activity"
MAX_SOURCE = 512 * 1024

DECK_TEMPLATE = r"""schema: policlase/v1
title: 'Nueva clase'
use: class                  # class: clase en vivo · activity: actividad
time_limit_s: 30            # segundos por pregunta (null: sin límite)

# El LaTeX va entre comillas simples: en las dobles, \t, \f, \b… son escapes de YAML.
slides:
  - markdown: |
      # Título de la clase
      Contenido con matemáticas: $f(x) = x^2 - 4$.

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

ACTIVITY_TEMPLATE = r"""schema: policlase/v1
title: 'Deber 1'
use: activity               # class: clase en vivo · activity: actividad
instructions: |
  Resuelva cada ejercicio. Las fechas, el peso y los intentos se configuran en policlase,
  no en este archivo.

# El LaTeX va entre comillas simples: en las dobles, \t, \f, \b… son escapes de YAML.
slides:
  - item:
      id: D01-ejemplo
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
            mode: manual          # ai: marcada para calificación con IA (aún no activa)
            rubric:
              - { criterion: 'Define el error de truncamiento', points: 1 }
              - { criterion: 'Da un ejemplo', points: 1 }

  # Un segmento cambia el comportamiento de lo que sigue: aquí, 60 s por pregunta y sin pegar.
  - segment: { title: 'Con tiempo', time_limit_s: 60, block_paste: true }

  - item:
      id: D01-rapida
      questions:
        - { id: q1, type: numeric, prompt: '¿Cuánto es $2^{10}$?', solution: 1024, grading: { atol: 0 } }

  - segment: {}             # vuelve a lo de todo el documento

  - item:
      id: D01-foto
      questions:
        - { id: q1, type: image, prompt: 'Suba la foto de su desarrollo a mano.', max_files: 3 }
"""

EVALUATION_TEMPLATE = r"""schema: policlase/v1
title: 'Prueba 1'
use: evaluation             # una sola vez, en un horario; pegar viene bloqueado
instructions: |
  Resuelva de forma individual. La fecha, el horario, la duración y el código de acceso se
  configuran en policlase, no en este archivo.

# El LaTeX va entre comillas simples: en las dobles, \t, \f, \b… son escapes de YAML.
slides:
  - item:
      id: P01-ejemplo
      questions:
        - id: q1
          type: numeric
          points: 2
          prompt: 'Calcule $\sqrt{2}$ con cuatro decimales.'
          solution: 1.4142
          grading: { atol: 0.0001 }
        - id: q2
          type: open
          points: 3
          prompt: 'Justifique el criterio de parada que usó.'
          grading:
            mode: manual
            rubric:
              - { criterion: 'Enuncia el criterio', points: 1 }
              - { criterion: 'Lo justifica con el error', points: 2 }

  - solution: |             # se muestra en la revisión, cuando usted publique las notas
      ## Solución
      $\sqrt{2} \approx 1.4142$.
"""

PRACTICE_TEMPLATE = r"""schema: policlase/v1
title: 'Repaso para la Prueba 1'
use: practice               # sin nota ni tiempo; cada pregunta se comprueba al momento
instructions: |
  Practique antes de la prueba. Compruebe cada pregunta para ver si acertó y lea la explicación.
  Puede repetir el repaso cuantas veces quiera; no cuenta para la nota.

# El LaTeX va entre comillas simples: en las dobles, \t, \f, \b… son escapes de YAML.
slides:
  - item:
      id: R01-ejemplo
      questions:
        - id: q1
          type: numeric
          prompt: 'Calcule $\sqrt{2}$ con cuatro decimales.'
          solution: 1.4142
          grading: { atol: 0.0001 }

  - solution: |             # la explicación del ítem anterior: se abre al comprobarlo
      ## Explicación
      $\sqrt{2} \approx 1.4142$: basta aplicar dos iteraciones de Newton desde $x_0 = 1{,}5$.
"""

TEMPLATES = {DECK: DECK_TEMPLATE, ACTIVITY: ACTIVITY_TEMPLATE}
#: `use` del formato → tipo de la plataforma. Una evaluación se edita y se guarda como una actividad
#: (`Activity.kind` la distingue).
KINDS = {"class": DECK, "activity": ACTIVITY, "evaluation": ACTIVITY, "practice": ACTIVITY}


def _diagnostics(report) -> list[dict]:
    return [{"code": d.code, "severity": d.severity.value, "message": d.message,
             "line": d.line, "where": ".".join(p for p in (d.item_id, d.question_id) if p)}
            for d in report]


def _course_link(course):
    from apps.github.models import CourseRepo
    return CourseRepo.objects.filter(course=course).select_related("course__owner").first()


def _load(source: str):
    """(documento, reporte, tipo): el tipo según el texto, o None si no lo dice."""
    document, report, use = content_format.load_text(source)
    return document, report, (KINDS.get(use, use) if use else None)


def _lines(document, compiled_list) -> list:
    """Línea de cada diapositiva o bloque compilado (por su `at`): el editor resalta la del cursor."""
    found = content_format.entries(document)
    if found is None:
        return []
    return [loader.line_of(found, entry["at"]) if isinstance(entry.get("at"), int) and entry["at"] < len(found)
            else None for entry in compiled_list]


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
    if kind == ACTIVITY and content is not None and content.correction_of_id:
        # La corrección no tiene contenido propio: se edita la evaluación.
        return redirect("activity_edit", course_pk=course.pk, pk=content.correction_of_id)
    evaluation = content is None and request.GET.get("tipo") == "evaluacion"
    practice = content is None and request.GET.get("tipo") == "repaso"
    if content is None and (evaluation or practice or request.GET.get("tipo") in ("actividad", ACTIVITY)):
        kind = ACTIVITY
    source = (content.source if content else EVALUATION_TEMPLATE if evaluation else PRACTICE_TEMPLATE if practice
              else TEMPLATES[kind])
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
        document, report, found = _load(source)
        found = found or kind
        use = content_format.kind_of(document) if document is not None else None
        if content is not None and kind == ACTIVITY and use in WORK_USES \
                and content.kind_for(use) != content.kind and content.attempts.exists():
            messages.error(request, _("No se guardó: el documento cambió de tipo (actividad, evaluación o repaso) y "
                                      "ya tiene entregas. Para usar el otro tipo, cree un documento nuevo."))
        elif content is not None and found != kind:
            messages.error(request, _("No se guardó: el documento es una presentación y el texto corresponde a una "
                                      "actividad, o viceversa. Para crear un documento del otro tipo, use los botones "
                                      "de creación de la sección."))
        else:
            kind = found
            response = _save(request, course, link, kind, content, source, section, document, report)
            if response:
                return response
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


def _save(request, course, link, kind, content, source, section, document, report):
    """Confirma en GitHub (si hay vínculo) y guarda un documento ya validado. Devuelve la
    redirección, o None si no se guardó (los mensajes dicen por qué y el texto sigue en el editor)."""
    from apps.activities.models import Activity
    from apps.github import sync

    if document is None or report.errors:
        messages.error(request, _("La actividad tiene errores; no se guardó.") if kind == ACTIVITY
                       else _("La presentación tiene errores; no se guardó."))
        return None
    compiled = content_format.compile_content(document)
    new = content is None
    content = content or (Activity if kind == ACTIVITY else Deck)(course=course)
    if kind == ACTIVITY:
        content.set_kind(content_format.kind_of(document))
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
        compiled, missing = images.rewrite(compiled, content.github_path, link.prefix, link.images)
        if missing:
            messages.warning(request, _("Estas imágenes no están en GitHub (o aún no se sincronizan): %(images)s")
                             % {"images": ", ".join(missing)})
    content.source, content.compiled = source, compiled
    content.save()
    if kind == ACTIVITY and content.is_evaluation:
        from apps.activities.engine import sync_correction
        sync_correction(content)                         # la corrección sigue al contenido de la evaluación
        messages.success(request, _("Evaluación «%(title)s» guardada: %(questions)d preguntas, %(points)s puntos.")
                         % {"title": content.title, "questions": len(content.questions),
                            "points": f"{content.points:g}"})
    elif kind == ACTIVITY:
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
        if new and content.is_evaluation:
            messages.info(request, _("Asigne la fecha, el horario y la ponderación, y publique la evaluación."))
        elif new:
            messages.info(request, _("Asigne la ponderación y la fecha de entrega, y publique la actividad."))
        return redirect("activity_detail", course_pk=course.pk, pk=content.pk)
    return redirect("deck_view", course_pk=course.pk, pk=content.pk)


@teacher_required
@require_POST
def preview(request, course_pk):
    """Vista previa en vivo: valida, compila y devuelve el contenido renderizado y su índice."""
    course = owned_course(request, course_pk)
    source = request.POST.get("source", "")
    posted = request.POST.get("kind") if request.POST.get("kind") in TEMPLATES else DECK
    if len(source) > MAX_SOURCE:
        return JsonResponse({"kind": posted, "html": None, "lines": [], "diagnostics": [{
            "code": "", "severity": "error", "line": None, "where": "", "message": _("El archivo supera 512 KB.")}]})
    document, report, found = _load(source)
    # Un documento guardado conserva su tipo: si el texto dice otro, se avisa en vez de cambiar.
    kind = found or posted
    payload = {"kind": kind, "diagnostics": _diagnostics(report), "html": None, "lines": []}
    if request.POST.get("fixed") and found and found != posted:
        payload["kind"] = posted
        payload["diagnostics"].insert(0, {"code": "", "severity": "error", "line": None, "where": "",
                                          "message": _("Este documento se guardó con otro tipo, que no puede "
                                                       "cambiarse desde el texto.")})
        return JsonResponse(payload)
    if document is not None and not report.errors and found in (DECK, ACTIVITY):
        compiled = content_format.compile_content(document)
        compiled = _preview_images(request, course, compiled, payload["diagnostics"])
        if found == DECK:
            payload["html"] = render_to_string("live/_deck_slides.html", {"slides": compiled["slides"]}, request)
            outline = _class_outline(compiled)
            payload["lines"] = _lines(document, compiled["slides"])
        else:
            payload["html"] = render_to_string("activities/_preview.html", {"content": compiled}, request)
            outline = [{**block, "index": index} for index, block in enumerate(compiled["items"])]
            payload["lines"] = _lines(document, compiled["items"])
        found_list = content_format.entries(document) or []
        for entry in outline:
            at = entry.get("at")
            entry["line"] = loader.line_of(found_list, at) if isinstance(at, int) and at < len(found_list) else None
        payload["outline"] = render_to_string("live/_outline.html", {"entries": outline, "kind": found}, request)
        payload["title"] = compiled["title"]
    return JsonResponse(payload)


def _preview_images(request, course, compiled, diagnostics: list) -> dict:
    """Las imágenes del repositorio en la vista previa: se resuelven desde la ruta del archivo (o la
    carpeta de la sección, si aún no se guarda) y se avisa de las que no están."""
    cited = images.relative_images(compiled)
    if not cited:
        return compiled
    link = _course_link(course)
    if link is None:
        diagnostics.append({"code": "", "severity": "aviso", "line": None, "where": "", "message": _(
            "Las imágenes con ruta relativa (%(images)s) solo se muestran en cursos vinculados a GitHub; "
            "fuera de ellos, use una dirección https://.") % {"images": ", ".join(cited)}})
        return compiled
    path = request.POST.get("path", "")
    if not path.startswith(link.prefix):
        section = Section.objects.filter(course=course, pk=request.POST.get("section") or 0).first()
        folder = f"{section.github_folder}/" if section and section.github_folder else ""
        path = f"{link.prefix}{folder}nuevo.yaml"
    compiled, missing = images.rewrite(compiled, path, link.prefix, link.images)
    for target in missing:
        diagnostics.append({"code": "", "severity": "aviso", "line": None, "where": "", "message": _(
            "La imagen %(path)s no está en GitHub (o aún no se sincroniza).") % {"path": target}})
    return compiled


def _class_outline(compiled: dict) -> list[dict]:
    """Las diapositivas de una clase con los segmentos intercalados donde empiezan."""
    out, segments = [], list(compiled.get("segments") or [])
    for index, slide in enumerate(compiled["slides"]):
        while segments and isinstance(slide.get("at"), int) and segments[0]["at"] < slide["at"]:
            out.append({"kind": "segment", "index": None, **segments.pop(0)})
        out.append({**slide, "index": index})
    out[-1:-1] = [{"kind": "segment", "index": None, **s} for s in segments]     # segmentos al final, antes de la retroalimentación
    return out


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
    # Las posiciones son las de la lista del archivo: `items` en el formato anterior de actividad,
    # `slides` en los demás (con los segmentos contados como una entrada más).
    legacy_activity = bool(re.search(r"^schema\s*:\s*['\"]?policlase\.activity/", source, re.MULTILINE))
    try:
        index = int(request.POST.get("index", "-1"))
        if op == "move":
            target = int(request.POST.get("target", "-1"))
            source = (deck_text.move_item if legacy_activity else deck_text.move_slide)(source, index, target)
        elif op in ("hide", "show") and not legacy_activity:
            source = deck_text.set_hidden(source, index, op == "hide")
        elif op in ("feedback_on", "feedback_off"):
            source = deck_text.set_feedback(source, op == "feedback_on")
        else:
            return JsonResponse({"error": _("Operación desconocida.")}, status=400)
    except (ValueError, deck_text.EditError) as exc:
        return JsonResponse({"error": _("Corrija los errores antes de usar el panel: %(error)s") % {"error": exc}},
                            status=400)
    return JsonResponse({"source": source})
