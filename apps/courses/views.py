from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from .access import can_view_course, owned_course, teacher_required
from .forms import CourseForm, JoinForm
from .models import Course, Enrollment, normalize_code

Status = Enrollment.Status


@login_required
def home(request):
    from apps.live.models import LiveSession

    if request.user.is_teacher:
        courses = (Course.objects.filter(owner=request.user)
                   .prefetch_related("enrollments"))
        rows = []
        for course in courses:
            by_status = {s: 0 for s in Status.values}
            for e in course.enrollments.all():
                by_status[e.status] += 1
            rows.append({"course": course, "pending": by_status[Status.PENDING],
                         "approved": by_status[Status.APPROVED]})
        return render(request, "courses/teacher_home.html", {"rows": rows})

    enrollments = list(Enrollment.objects.filter(student=request.user)
                       .select_related("course", "course__owner")
                       .order_by("status", "course__name"))
    # Lo aprobado es lo normal y no se anuncia; un rechazo se avisa hasta que el estudiante lo cierra.
    active = [e for e in enrollments if e.status != Status.REJECTED]
    rejected = [e for e in enrollments if e.status == Status.REJECTED and not e.dismissed]
    live_ids = set(LiveSession.objects.active()
                   .filter(course__in=[e.course for e in active if e.status == Status.APPROVED])
                   .values_list("course_id", flat=True))
    return render(request, "courses/student_home.html", {
        "enrollments": active, "rejected": rejected, "live_ids": live_ids,
        # Inscribirse se hace una vez: solo está al frente mientras no tenga ningún curso.
        "join_form": JoinForm() if not active else None,
    })


@login_required
def join(request):
    if request.user.is_teacher:
        raise PermissionDenied(_("Las cuentas de docente no se inscriben en cursos."))
    if request.method != "POST":
        return render(request, "courses/join.html", {"join_form": JoinForm()})

    form = JoinForm(request.POST)
    code = normalize_code(form.data.get("code", ""))
    course = Course.objects.filter(join_code=code).first() if code else None

    # Mismo mensaje para "no existe" y "cerrado": no se confirma qué códigos son válidos.
    if course is None or not course.accepting:
        messages.error(request, _("Ese código no corresponde a ningún curso abierto. Revíselo con su docente."))
        return redirect("course_join")

    try:
        with transaction.atomic():
            enrollment, created = Enrollment.objects.get_or_create(course=course, student=request.user)
    except IntegrityError:
        enrollment, created = Enrollment.objects.get(course=course, student=request.user), False

    if created:
        messages.success(request, _("Solicitud enviada a %(course)s. Su docente debe aprobarla.")
                         % {"course": course.name})
    elif enrollment.status == Status.APPROVED:
        messages.info(request, _("Ya está inscrito en %(course)s.") % {"course": course.name})
    elif enrollment.status == Status.PENDING:
        messages.info(request, _("Su solicitud a %(course)s sigue pendiente de aprobación.")
                      % {"course": course.name})
    else:
        messages.warning(request, _("Su solicitud a %(course)s fue rechazada. Hable con su docente.")
                         % {"course": course.name})
    return redirect("home")


@login_required
@require_POST
def enrollment_dismiss(request, pk):
    """El estudiante cierra el aviso de una solicitud rechazada."""
    Enrollment.objects.filter(pk=pk, student=request.user, status=Status.REJECTED).update(dismissed=True)
    return redirect("home")


@login_required
def course_detail(request, pk):
    """Página del curso para estudiantes aprobados (y para su docente)."""
    from apps.live.models import LiveSession

    course = get_object_or_404(Course.objects.select_related("owner"), pk=pk)
    if not can_view_course(request.user, course):
        # Pendiente o rechazado ven lo mismo que un curso ajeno: nada.
        raise Http404
    return render(request, "courses/course_detail.html", {
        "course": course,
        "live": LiveSession.objects.active().filter(course=course).first(),
    })


# ---------------------------------------------------------------------------- docente


@teacher_required
def course_create(request):
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        course = form.save(commit=False)
        course.owner = request.user
        try:
            course.save()
        except IntegrityError:
            form.add_error("code", _("Ya tiene un curso con ese código."))
        else:
            messages.success(request, _("Curso creado. Comparta el código de inscripción %(code)s con sus "
                                        "estudiantes; también está en la configuración del curso.")
                             % {"code": course.join_code_display})
            return redirect("course_manage", pk=course.pk)
    return render(request, "courses/course_form.html", {"form": form})


TABS = ("clases", "evaluaciones", "actividades")


@teacher_required
def course_manage(request, pk, tab="clases"):
    """El frente del curso: la clase en vivo (o en pausa), solicitudes por aprobar (si las hay) y
    las pestañas Clases | Evaluaciones | Actividades. Lo que se configura una vez —código, datos
    del curso, inscritos— vive en `course_settings`."""
    from apps.github import sync
    from apps.github.models import CourseRepo
    from apps.live.models import Deck, LiveSession, Section

    course = owned_course(request, pk)
    link = CourseRepo.objects.filter(course=course).first()
    if link and tab == "clases":
        result = sync.maybe_pull(link)          # GitHub es la fuente: al abrir el curso, al día
        if result and result.changed:
            messages.info(request, _("Presentaciones actualizadas desde GitHub."))
    pending = (course.enrollments.filter(status=Status.PENDING)
               .select_related("student").order_by("requested_at"))
    context = {
        "course": course, "tab": tab, "pending": pending, "link": link,
        "approved_count": course.enrollments.filter(status=Status.APPROVED).count(),
        "live": LiveSession.objects.active().filter(course=course).first(),
    }
    if tab == "clases":
        decks = list(Deck.objects.filter(course=course))
        key = (lambda d: sync.natural_key(d.github_path)) if link else (lambda d: d.pk)
        sections = list(Section.objects.filter(course=course))
        if link:
            sections.sort(key=lambda s: (not s.github_folder, sync.natural_key(s.github_folder), s.pk))
        groups = [{"section": None, "decks": sorted([d for d in decks if d.section_id is None], key=key)}]
        groups += [{"section": s, "decks": sorted([d for d in decks if d.section_id == s.pk], key=key)}
                   for s in sections]
        context.update({
            "groups": groups, "has_decks": bool(decks),
            "past_sessions": LiveSession.objects.filter(course=course, status=LiveSession.Status.ENDED)[:10],
        })
    return render(request, "courses/course_manage.html", context)


@teacher_required
def course_settings(request, pk):
    course = owned_course(request, pk)
    form = CourseForm(request.POST or None, instance=course)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error("code", _("Ya tiene un curso con ese código."))
        else:
            messages.success(request, _("Datos del curso guardados."))
            return redirect("course_settings", pk=pk)
    from apps.github.forms import CourseRepoForm
    from apps.github.models import CourseRepo, GitHubAccount

    link = CourseRepo.objects.filter(course=course).first()
    enrollments = course.enrollments.select_related("student", "decided_by")
    return render(request, "courses/course_settings.html", {
        "course": course,
        "form": form,
        "link": link,
        "repo_form": CourseRepoForm(instance=link, initial=None if link else {"branch": "main"}),
        "github_account": GitHubAccount.objects.filter(user=request.user).first(),
        "webhook_url": request.build_absolute_uri(reverse("github_webhook", args=[course.pk])),
        "github_url": reverse("github_account"),
        "approved": [e for e in enrollments if e.status == Status.APPROVED],
        "rejected": [e for e in enrollments if e.status == Status.REJECTED],
    })


def _back(request, pk):
    """Las solicitudes se deciden desde el frente o desde la configuración: se vuelve a la misma."""
    return redirect("course_settings" if request.POST.get("back") == "settings" else "course_manage", pk=pk)


@teacher_required
@require_POST
def enrollment_decide(request, pk):
    """Aprobar o rechazar solicitudes: las marcadas, o todas las pendientes de una vez."""
    course = owned_course(request, pk)
    action = request.POST.get("action")
    target = {"approve": Status.APPROVED, "reject": Status.REJECTED,
              "approve_all": Status.APPROVED}.get(action)
    if target is None:
        messages.error(request, _("Acción desconocida."))
        return _back(request, pk)

    if action == "approve_all":
        queryset = course.enrollments.filter(status=Status.PENDING)
    else:
        ids = [int(i) for i in request.POST.getlist("enrollment") if i.isdigit()]
        # Filtrar por curso impide decidir sobre matrículas de cursos ajenos con ids forjados.
        queryset = course.enrollments.filter(pk__in=ids)

    count = queryset.update(status=target, decided_at=timezone.now(), decided_by=request.user,
                            dismissed=False)
    if count == 0:
        messages.info(request, _("No había solicitudes seleccionadas."))
    elif target == Status.APPROVED:
        messages.success(request, ngettext("%(count)d solicitud aprobada.", "%(count)d solicitudes aprobadas.",
                                           count) % {"count": count})
    else:
        messages.success(request, ngettext("%(count)d solicitud rechazada.", "%(count)d solicitudes rechazadas.",
                                           count) % {"count": count})
    return _back(request, pk)


@teacher_required
@require_POST
def enrollment_remove(request, pk, enrollment_pk):
    course = owned_course(request, pk)
    enrollment = get_object_or_404(Enrollment, pk=enrollment_pk, course=course)
    name = enrollment.student.display_name
    enrollment.delete()
    messages.success(request, _("%(name)s fue retirado del curso. Puede volver a solicitar con el código.")
                     % {"name": name})
    return redirect("course_settings", pk=pk)


@teacher_required
@require_POST
def join_code_action(request, pk):
    course = owned_course(request, pk)
    action = request.POST.get("action")
    if action == "regenerate":
        course.regenerate_join_code()
        messages.success(request, _("Nuevo código: %(code)s. El anterior dejó de funcionar.")
                         % {"code": course.join_code_display})
    elif action == "toggle":
        course.accepting = not course.accepting
        course.save(update_fields=["accepting"])
        messages.success(request, _("El curso acepta solicitudes.") if course.accepting
                         else _("El curso ya no acepta solicitudes."))
    return redirect("course_settings", pk=pk)
