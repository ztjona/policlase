from allauth.account.models import EmailAddress
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.decorators.http import require_POST

from .access import can_view_course, owned_course, teacher_required
from .forms import AcceptingUntilForm, CourseForm, JoinForm
from .models import Course, Enrollment

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
        # El correo sin confirmar no impide entrar (la confirmación es opcional para estudiantes), pero
        # sin él no se recupera la contraseña: se recuerda aquí.
        "unconfirmed": not EmailAddress.objects.filter(user=request.user, verified=True).exists(),
        # Inscribirse se hace una vez: solo está al frente mientras no tenga ningún curso.
        "join_form": JoinForm() if not active else None,
    })


def join_link(request, code):
    """El enlace que comparte el docente, con el código ya completado: quien tiene sesión va a
    inscribirse y quien no, a crear su cuenta (esa página ofrece también iniciar sesión)."""
    target = "course_join" if request.user.is_authenticated else "account_signup"
    return redirect(f"{reverse(target)}?{urlencode({'codigo': code})}")


@login_required
def join(request):
    if request.user.is_teacher:
        raise PermissionDenied(_("Las cuentas de docente no se inscriben en cursos."))
    if request.method != "POST":
        form = JoinForm(initial={"code": request.GET.get("codigo", "")})
        return render(request, "courses/join.html", {"join_form": form})

    form = JoinForm(request.POST)
    course = Course.open_for_code(form.data.get("code", ""))

    # Mismo mensaje para "no existe" y "cerrado": no se confirma qué códigos son válidos.
    if course is None:
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
    rows = _student_activities(course, request.user)
    return render(request, "courses/course_detail.html", {
        "course": course,
        "live": LiveSession.objects.active().filter(course=course).first(),
        "activities": [r for r in rows if not r["activity"].is_evaluation],
        "evaluations": sorted([r for r in rows if r["activity"].is_evaluation],
                              key=lambda r: r["activity"].opens_at or timezone.now()),
        "now": timezone.now(),
    })


def _student_activities(course, user) -> list[dict]:
    """Actividades visibles, con lo que le toca al estudiante: lo pendiente primero."""
    from apps.activities import engine
    from apps.activities.models import Activity

    now = timezone.now()
    rows = []
    for activity in Activity.objects.filter(course=course, published=True).select_related("category__period"):
        if not activity.is_visible(now):
            continue
        state = engine.state(activity, user, now)
        # Un repaso no es una tarea pendiente: no tiene nota ni vence.
        todo = not activity.is_practice and (state.draft is not None or (state.can_start and state.counted is None))
        rows.append({"activity": activity, "state": state, "todo": todo})
    # Lo que falta entregar arriba, por fecha; lo entregado o cerrado después.
    rows.sort(key=lambda r: (not r["todo"], r["activity"].due_at is None, r["activity"].due_at or now))
    return rows


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


TABS = ("clases", "actividades")      # Evaluaciones y Calificaciones son páginas propias (_course_tabs.html)


@teacher_required
def course_manage(request, pk, tab="clases"):
    """El frente del curso: la clase en vivo (o en pausa), solicitudes por aprobar (si las hay) y
    las pestañas Clases y Actividades (Evaluaciones y Calificaciones son páginas propias con las mismas
    pestañas). Lo que se configura una vez —código, datos del curso, inscritos— vive en
    `course_settings`."""
    from apps.github import sync
    from apps.github.models import CourseRepo
    from apps.live.models import Deck, LiveSession, Section

    course = owned_course(request, pk)
    link = CourseRepo.objects.filter(course=course).first()
    if link and tab in ("clases", "actividades"):
        result = sync.maybe_pull(link)          # GitHub es la fuente: al abrir el curso, al día
        if result and result.changed:
            messages.info(request, _("Presentaciones y actividades actualizadas desde GitHub."))
    pending = (course.enrollments.filter(status=Status.PENDING)
               .select_related("student").order_by("requested_at"))
    context = {
        "course": course, "tab": tab, "pending": pending, "link": link,
        "approved_count": course.enrollments.filter(status=Status.APPROVED).count(),
        "live": LiveSession.objects.active().filter(course=course).first(),
    }
    if tab == "clases":
        # El material del curso, por unidad: presentaciones y actividades. Las notas (clases dictadas,
        # entregas, registros manuales) están en la pestaña Actividades.
        from apps.activities.models import Activity

        decks = list(Deck.objects.filter(course=course))
        # Las evaluaciones no son material de una unidad: viven en la pestaña Evaluaciones.
        activities = list(Activity.objects.filter(course=course).exclude(kind=Activity.Kind.EVALUATION)
                          .select_related("category"))
        taught: dict[int, list] = {}
        for session in (LiveSession.objects.filter(course=course, status=LiveSession.Status.ENDED, deck__isnull=False)
                        .order_by("started_at", "created_at")):
            taught.setdefault(session.deck_id, []).append(session)
        for d in decks:
            d.taught = taught.get(d.pk, [])
        key = (lambda d: sync.natural_key(d.github_path)) if link else (lambda d: d.pk)
        sections = list(Section.objects.filter(course=course))
        if link:
            sections.sort(key=lambda s: (not s.github_folder, sync.natural_key(s.github_folder), s.pk))
        # Las secciones primero y lo que no tiene sección al final: la unidad en curso no queda
        # enterrada bajo una lista creciente de presentaciones sin sección.
        groups = [{"section": s,
                   "decks": sorted([d for d in decks if d.section_id == s.pk], key=key),
                   "activities": sorted([a for a in activities if a.section_id == s.pk], key=key)}
                  for s in sections]
        groups.append({"section": None,
                       "decks": sorted([d for d in decks if d.section_id is None], key=key),
                       "activities": sorted([a for a in activities if a.section_id is None], key=key)})
        for group in groups:
            group["count"] = len(group["decks"]) + len(group["activities"])
        context.update({
            "groups": groups, "has_content": bool(decks or activities),
            "current_section": current_section(course, sections), "row_colors": row_colors(course),
        })
    elif tab == "actividades":
        context.update(_activities_tab(course, context["approved_count"], request.GET.get("fila", "")))
    return render(request, "courses/course_manage.html", context)


def row_colors(course) -> dict[str, int]:
    """Un color (0–5) por nombre de fila de la ponderación, el mismo en todos los parciales y pestañas:
    «Deberes» se reconoce de un vistazo junto a cada actividad."""
    from apps.grades.models import Category

    names = []
    for name in Category.objects.filter(period__course=course).order_by("period__position", "position", "pk") \
            .values_list("name", flat=True):
        if name not in names:
            names.append(name)
    return {name: i % 6 for i, name in enumerate(names)}


def current_section(course, sections) -> int | None:
    """La unidad en curso: la de la última clase dictada o, si ya se dictaron todas sus
    presentaciones, la siguiente que tenga alguna. Sin clases todavía, la primera. Es la única que
    la pestaña Clases muestra desplegada."""
    from apps.live.models import Deck, LiveSession

    if not sections:
        return None
    last = (LiveSession.objects.filter(course=course, deck__section__in=sections)
            .order_by("-started_at", "-created_at").select_related("deck").first())
    if last is None:
        return sections[0].pk
    taught = set(LiveSession.objects.filter(course=course, deck__isnull=False).values_list("deck_id", flat=True))
    order = [s.pk for s in sections]
    start = order.index(last.deck.section_id)
    pending = set(Deck.objects.filter(course=course, section__in=sections).exclude(pk__in=taught)
                  .values_list("section_id", flat=True))
    if last.deck.section_id in pending:
        return last.deck.section_id
    return next((pk for pk in order[start + 1:] if pk in pending), last.deck.section_id)


def _activities_tab(course, enrolled: int, wanted: str = "") -> dict:
    """Todo lo que se califica, por parcial: las clases dictadas, las actividades y los registros
    manuales, en orden de fecha, con su unidad, entregas (o asistencia) y lo pendiente de revisar.
    Se filtra por fila de la ponderación. El material (presentaciones y contenido de las
    actividades) vive en la pestaña Clases."""
    from apps.accounts.models import User
    from apps.activities.models import Activity
    from apps.grades.gradebook import day_of, ensure_defaults, mismatch, period_for
    from apps.grades.models import Category, ManualRecord, Period
    from apps.live.models import LiveSession

    ensure_defaults(course)
    periods = list(Period.objects.filter(course=course).prefetch_related("categories"))
    names = []
    for period in periods:
        for category in period.categories.all():
            if category.name not in names:
                names.append(category.name)
    wanted = wanted if wanted in names else ""
    keep = (lambda category: category is not None and category.name == wanted) if wanted else (lambda category: True)

    rows: dict[int | None, list[dict]] = {p.pk: [] for p in periods}
    rows[None] = []
    activities = (Activity.objects.filter(course=course)
                  .select_related("category__period", "due_milestone", "section", "course__owner")
                  .annotate(submitted=Count("attempts__student", filter=Q(attempts__submitted_at__isnull=False),
                                            distinct=True),
                            to_review=Count("attempts__answer_rows",
                                            filter=Q(attempts__answer_rows__needs_review=True))))
    for a in activities:
        if keep(a.category):
            a.mismatch = mismatch(a, periods)
            rows[a.category.period_id if a.category_id else None].append(
                {"kind": "activity", "item": a, "day": day_of(a)})
    for r in ManualRecord.objects.filter(course=course).select_related("category__period", "section"):
        if keep(r.category):
            rows[r.category.period_id if r.category_id else None].append({"kind": "record", "item": r, "day": r.on})
    # Cada clase dictada cuenta en la fila de participación del parcial de su fecha (como en las notas).
    sessions = (LiveSession.objects.filter(course=course, status=LiveSession.Status.ENDED)
                .select_related("deck__section")
                .annotate(present=Count("participants", filter=Q(participants__student__role=User.Role.STUDENT))))
    for session in sessions:
        day = timezone.localdate(session.started_at or session.created_at)
        period = period_for(periods, day)
        row = next((c for c in period.categories.all() if c.source == Category.Source.LIVE), None) if period else None
        session.row = row if session.max_points > 0 else None
        if keep(session.row):
            rows[period.pk if period else None].append({"kind": "live", "item": session, "day": day})
    order = {"live": 0, "activity": 1, "record": 2}
    groups = [{"period": p, "rows": rows[p.pk]} for p in periods]
    if rows[None]:
        groups.append({"period": None, "rows": rows[None]})
    # Como las unidades: solo el parcial en curso viene desplegado. «Sin ponderación» también, porque
    # pide atención; con un filtro, todo parcial que tenga algo.
    current = period_for(periods, timezone.localdate())
    for group in groups:
        group["rows"].sort(key=lambda r: (r["day"] is None, r["day"] or timezone.localdate(), order[r["kind"]]))
        group["count"] = len(group["rows"])
        group["open"] = group["period"] is None or group["period"] == current or bool(wanted and group["count"])
    return {"activity_groups": groups, "has_activities": any(g["count"] for g in groups), "enrolled": enrolled,
            "filters": names, "wanted": wanted, "row_colors": row_colors(course)}


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
        "until_form": AcceptingUntilForm(instance=course),
        "join_link": request.build_absolute_uri(reverse("course_join_link", args=[course.join_code])),
        "link": link,
        "repo_form": CourseRepoForm(instance=link, initial=None if link else {"branch": "main"}),
        "github_account": GitHubAccount.objects.filter(user=request.user).first(),
        "webhook_url": request.build_absolute_uri(reverse("github_webhook", args=[course.pk])),
        "github_url": reverse("github_account"),
        "approved": [e for e in enrollments if e.status == Status.APPROVED],
        "rejected": [e for e in enrollments if e.status == Status.REJECTED],
    })


@teacher_required
@require_POST
def enrollment_details(request, pk):
    """El nombre preferido de los inscritos (la tabla de la configuración del curso)."""
    from apps.accounts.names import name_case

    course = owned_course(request, pk)
    changed = 0
    for e in course.enrollments.filter(status=Status.APPROVED):
        name = name_case(" ".join(request.POST.get(f"name-{e.pk}", e.preferred_name).split())[:60])
        if name != e.preferred_name:
            e.preferred_name = name
            e.save(update_fields=["preferred_name"])
            changed += 1
    messages.success(request, ngettext("Se actualizó %(n)d inscrito.", "Se actualizaron %(n)d inscritos.", changed)
                     % {"n": changed})
    return redirect("course_settings", pk=pk)


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
    messages.success(request, _("%(name)s fue retirado del curso. Puede volver a solicitar la inscripción con el código del curso.")
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
        course.accepting = not course.is_accepting
        # Una fecha ya vencida volvería a cerrarla de inmediato.
        if course.accepting and course.accepting_until and course.accepting_until <= timezone.now():
            course.accepting_until = None
        course.save(update_fields=["accepting", "accepting_until"])
        messages.success(request, _("El curso acepta solicitudes.") if course.accepting
                         else _("El curso ya no acepta solicitudes."))
    elif action == "until":
        form = AcceptingUntilForm(request.POST, instance=course)
        if form.is_valid():
            form.save()
            messages.success(request, _("Fecha de cierre de la inscripción guardada."))
        else:
            messages.error(request, " ".join(form.errors["accepting_until"]))
    return redirect("course_settings", pk=pk)
