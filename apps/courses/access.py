"""Reglas de acceso. Todo control de permisos pasa por aquí para que sea auditable."""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from .models import Course, Enrollment


def teacher_required(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_teacher:
            raise PermissionDenied(_("Solo docentes."))
        return view(request, *args, **kwargs)

    return wrapper


def owned_course(request, pk) -> Course:
    """Un docente solo ve sus cursos: el curso de otro docente es un 404, no un 403."""
    return get_object_or_404(Course, pk=pk, owner=request.user)


def is_approved(user, course) -> bool:
    return Enrollment.objects.filter(
        course=course, student=user, status=Enrollment.Status.APPROVED
    ).exists()


def can_view_course(user, course) -> bool:
    return user.is_authenticated and (course.owner_id == user.pk or is_approved(user, course))
