"""Secciones (unidades) de la pestaña Clases: crear, renombrar, eliminar y minimizar.

En un curso vinculado a GitHub, cada operación hace primero el commit (carpeta nueva, carpeta
renombrada con todos sus archivos, marcador borrado) y solo después cambia la base de datos.
"""

from django.contrib import messages
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.courses.access import owned_course, teacher_required

from .models import Section


def _link(course):
    from apps.github.models import CourseRepo
    return CourseRepo.objects.filter(course=course).select_related("course__owner").first()


def _title(request) -> str:
    return " ".join(request.POST.get("title", "").split())[:200]


@teacher_required
@require_POST
def section_create(request, course_pk):
    from apps.github import sync

    course = owned_course(request, course_pk)
    title = _title(request)
    if not title:
        messages.error(request, _("Escriba un nombre para la sección."))
        return redirect("course_manage", pk=course.pk)
    position = (Section.objects.filter(course=course).aggregate(m=Max("position"))["m"] or 0) + 1
    section = Section(course=course, title=title, position=position)
    link = _link(course)
    if link:
        section.github_folder = sync.clean_folder(title)
        if Section.objects.filter(course=course, github_folder=section.github_folder).exists():
            messages.error(request, _("Ya existe una sección con ese nombre."))
            return redirect("course_manage", pk=course.pk)
        try:
            sync.create_section(link, section)
        except sync.GitHubError as exc:
            messages.error(request, _("No se creó: %(error)s") % {"error": exc})
            return redirect("course_manage", pk=course.pk)
        section.title = sync.folder_title(section.github_folder)
    section.save()
    messages.success(request, _("Sección «%(title)s» creada.") % {"title": section.title})
    return redirect(f"{reverse('course_manage', args=[course.pk])}#seccion-{section.pk}")


@teacher_required
@require_POST
def section_rename(request, course_pk, pk):
    from apps.github import sync

    course = owned_course(request, course_pk)
    section = get_object_or_404(Section, pk=pk, course=course)
    title = _title(request)
    if not title or title == section.title:
        return redirect("course_manage", pk=course.pk)
    link = _link(course)
    if link:
        try:
            sync.rename_section(link, section, title)
        except sync.GitHubError as exc:
            messages.error(request, _("No se renombró: %(error)s") % {"error": exc})
            return redirect("course_manage", pk=course.pk)
    else:
        section.title = title
        section.save(update_fields=["title"])
    messages.success(request, _("Sección renombrada."))
    return redirect("course_manage", pk=course.pk)


@teacher_required
@require_POST
def section_delete(request, course_pk, pk):
    from apps.github import sync

    course = owned_course(request, course_pk)
    section = get_object_or_404(Section, pk=pk, course=course)
    if section.decks.exists():
        messages.error(request, _("Solo se pueden eliminar secciones vacías: mueva o elimine antes sus presentaciones."))
        return redirect("course_manage", pk=course.pk)
    link = _link(course)
    if link:
        try:
            sync.delete_section(link, section)
        except sync.GitHubError as exc:
            messages.error(request, _("No se eliminó: %(error)s") % {"error": exc})
            return redirect("course_manage", pk=course.pk)
    section.delete()
    messages.success(request, _("Sección eliminada."))
    return redirect("course_manage", pk=course.pk)


@teacher_required
@require_POST
def section_toggle(request, course_pk, pk):
    """Minimizar o desplegar: preferencia de la vista del docente, se guarda sin recargar."""
    course = owned_course(request, course_pk)
    collapsed = request.POST.get("collapsed") == "1"
    Section.objects.filter(pk=pk, course=course).update(collapsed=collapsed)
    return JsonResponse({"collapsed": collapsed})
