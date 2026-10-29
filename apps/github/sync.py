"""Sincronización de presentaciones y actividades entre un curso y una carpeta de GitHub.

- `pull`: GitHub → policlase. Compara los blob sha del árbol con los guardados y solo descarga
  lo que cambió. Un archivo con errores no reemplaza la versión válida anterior: queda anotado en
  `CourseRepo.issues` para mostrarlo en el curso.
- `push` / `delete`: policlase → GitHub, con el sha conocido. Si alguien cambió el archivo en
  GitHub entretanto, GitHub responde conflicto y no se pisa nada.

Cada YAML se reconoce por su contenido (`policlase_gen.document`): `policlase/v1` según su `use`
(clase o actividad; una evaluación espera a su pestaña), y los formatos anteriores por su `schema`
(`policlase.deck/v1`, `policlase.activity/v1`). Los demás (ítems, configuración) se ignoran. De una
actividad llega solo su contenido: fechas, ponderación y publicación se deciden en policlase, y
una actividad que llega por primera vez nace como borrador.

Secciones: cada subcarpeta (primer nivel bajo la carpeta del curso) que contenga al menos una
presentación o una actividad —o el marcador `SECTION_MARKER`, para unidades aún vacías— es una
sección, y lo que está en ella le pertenece. Así una carpeta de generadores o de ítems no aparece
como unidad. Mover y renombrar se hacen en un solo commit (`GitHubClient.commit`).
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from policlase_gen import document as content_format

from apps.activities.models import WORK_USES, Activity
from apps.live.models import Deck, Section

from . import images
from .client import Blob, Conflict, GitHubClient, GitHubError  # noqa: F401  (reexportados)
from .models import CourseRepo

YAML_SUFFIXES = (".yaml", ".yml")
#: Archivo vacío que mantiene en git una sección sin presentaciones ni actividades todavía.
SECTION_MARKER = ".policlase-seccion"
#: Al abrir el curso se sincroniza si pasó al menos esto desde la última vez.
AUTO_PULL_AFTER = timedelta(seconds=60)


def client_for(user) -> GitHubClient:
    account = getattr(user, "github", None)
    token = account.token if account else ""
    if not token:
        raise GitHubError(_("Conecte su cuenta de GitHub en el menú de usuario → GitHub."))
    return GitHubClient(token)


def natural_key(text: str) -> list:
    """unidad-2 antes que unidad-10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def folder_title(folder: str) -> str:
    """'unidad-01' → 'Unidad 01'; un nombre con espacios se respeta tal cual."""
    if " " in folder:
        return folder
    text = re.sub(r"[-_]+", " ", folder).strip()
    return text[:1].upper() + text[1:] if text else folder


def clean_folder(title: str) -> str:
    """Nombre de carpeta a partir del título que escribió el docente (git admite tildes y espacios)."""
    return re.sub(r"\s+", " ", title.replace("/", "-").replace("\\", "-")).strip(" .") or "seccion"


@dataclass
class PullResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.deleted)


def _diagnostics(report) -> list[dict]:
    return [{"code": d.code, "severity": d.severity.value, "message": d.message, "line": d.line}
            for d in report]


def _folder_of(link: CourseRepo, path: str) -> str:
    """Sección (primer nivel bajo la carpeta del curso) de una ruta, o '' si está en la raíz."""
    relative = path[len(link.prefix):]
    return relative.split("/", 1)[0] if "/" in relative else ""


def pull(link: CourseRepo) -> PullResult:
    client = client_for(link.course.owner)
    result = PullResult()
    try:
        tree = client.tree(link.repo, link.branch)
    except GitHubError as exc:
        link.last_error = str(exc)
        link.save(update_fields=["last_error"])
        raise

    files = {path: sha for path, sha in tree.items()
             if path.startswith(link.prefix) and path.endswith(YAML_SUFFIXES)}
    # Las imágenes primero: lo que se compile en esta pasada ya las encuentra.
    found_images, image_problems = images.fetch(link, client, tree)
    changed_images = {p for p in set(found_images) | set(link.images)
                      if found_images.get(p) != link.images.get(p)}
    for path, problem in image_problems.items():
        result.issues.append({"path": path, "diagnostics": [], "note": problem})
    markers = {_folder_of(link, path) for path in tree
               if path.startswith(link.prefix) and path.endswith("/" + SECTION_MARKER)}
    decks = {d.github_path: d for d in Deck.objects.filter(course=link.course).exclude(github_path="")}
    activities = {a.github_path: a for a in Activity.objects.filter(course=link.course).exclude(github_path="")}

    previous_issues = {i["path"]: i for i in link.issues}
    skipped: dict[str, str] = {}
    for path, sha in sorted(files.items()):
        deck, activity = decks.get(path), activities.get(path)
        if (deck and deck.github_sha == sha) or (activity and activity.github_sha == sha):
            continue
        if link.skipped.get(path) == sha:              # ya visto y descartado: sin cambios
            skipped[path] = sha
            if path in previous_issues:
                result.issues.append(previous_issues[path])
            continue
        source = client.read(link.repo, sha)
        document, report, use = content_format.load_text(source)
        if document is not None and use is None and not content_format.is_document(document):
            skipped[path] = sha                        # otro YAML (ítems, configuración): no es nuestro
            continue
        if document is None or report.errors:
            skipped[path] = sha
            result.issues.append({"path": path, "diagnostics": _diagnostics(report)})
            continue
        if activity and activity.attempts.exists() and use in WORK_USES and Activity.kind_for(use) != activity.kind:
            skipped[path] = sha
            result.issues.append({"path": path, "diagnostics": [], "note": _(
                "cambió de tipo (actividad, evaluación o repaso) en GitHub y ya tiene entregas; no se actualizó. "
                "Para usar el otro tipo, cree un archivo nuevo.")})
            continue
        if (use in WORK_USES and deck) or (use == "class" and activity):
            # Cambió de tipo en GitHub: lo que ya existe (con sus clases o entregas) no se convierte.
            skipped[path] = sha
            result.issues.append({"path": path, "diagnostics": [], "note": _(
                "cambió de tipo (use) en GitHub y no se actualizó. Para usar el otro tipo, cree un archivo nuevo.")})
            continue
        compiled, missing = images.rewrite(content_format.compile_content(document), path, link.prefix, found_images)
        if missing:
            result.issues.append(_missing_images_issue(path, missing))
        if use in WORK_USES:
            if activity is None:
                activity = Activity(course=link.course, title=compiled["title"], source=source, compiled=compiled,
                                    github_path=path, github_sha=sha)
                activity.set_kind(use)
                activity.save()
                activities[path] = activity
                result.created.append(path)
            else:
                activity.title, activity.source, activity.compiled, activity.github_sha = \
                    compiled["title"], source, compiled, sha
                fields = ["title", "source", "compiled", "github_sha", "updated_at"]
                was = activity.kind
                activity.set_kind(use)
                if activity.kind != was:                 # sin entregas: el tipo puede cambiar
                    fields += ["kind", "max_attempts", "late_policy", "auto_submit", "require_code", "access_code",
                               "category", "review_mode"]
                activity.save(update_fields=fields)
                if activity.is_evaluation:
                    from apps.activities.engine import sync_correction
                    sync_correction(activity)            # la corrección sigue al contenido de la evaluación
                result.updated.append(path)
            continue
        if deck is None:
            decks[path] = Deck.objects.create(course=link.course, title=compiled["title"], source=source,
                                              compiled=compiled, github_path=path, github_sha=sha)
            result.created.append(path)
        else:
            deck.title, deck.source, deck.compiled, deck.github_sha = compiled["title"], source, compiled, sha
            deck.save()
            result.updated.append(path)

    for path, deck in list(decks.items()):
        if path not in files:
            # Las clases ya dictadas guardan su copia de las diapositivas: borrar no pierde resultados.
            deck.delete()
            del decks[path]
            result.deleted.append(path)
    for path, activity in list(activities.items()):
        if path not in files:
            if activity.attempts.exists():
                # Con entregas no se borra: se pierde el trabajo de los estudiantes. Queda en
                # policlase, ya sin vínculo con GitHub.
                Activity.objects.filter(pk=activity.pk).update(github_path="", github_sha="")
                result.issues.append({"path": path, "diagnostics": [], "note": _(
                    "se eliminó en GitHub, pero tiene entregas; se conserva en policlase, sin sincronización. "
                    "Si ya no la necesita, elimínela desde la página de la actividad.")})
            else:
                activity.delete()
            result.deleted.append(path)

    if changed_images:
        _refresh_images(link, changed_images, found_images, {*result.created, *result.updated},
                        [*decks.values(), *activities.values()], result)
    _sync_sections(link, decks, activities, markers)
    link.issues, link.skipped, link.last_error, link.last_synced_at = result.issues, skipped, "", timezone.now()
    link.images = found_images
    link.save(update_fields=["issues", "skipped", "last_error", "last_synced_at", "images"])
    return result


def _missing_images_issue(path: str, missing: list[str]) -> dict:
    return {"path": path, "diagnostics": [], "note": _(
        "se actualizó, pero estas imágenes no están en GitHub: %(images)s") % {"images": ", ".join(missing)}}


def _refresh_images(link: CourseRepo, changed: set[str], found: dict[str, str], done: set[str],
                    contents: list, result: PullResult) -> None:
    """Una imagen cambió (o apareció) en GitHub sin que cambiara el YAML que la cita: se vuelve a
    compilar ese contenido para que apunte a la imagen nueva. Las clases dictadas y los intentos
    enviados conservan la suya."""
    names = {posixpath.basename(p) for p in changed}
    for content in contents:
        if content.github_path in done or not any(name in content.source for name in names):
            continue
        document, report, _use = content_format.load_text(content.source)
        if document is None or report.errors:
            continue
        compiled, missing = images.rewrite(content_format.compile_content(document), content.github_path,
                                           link.prefix, found)
        if compiled != content.compiled:
            content.compiled = compiled
            content.save(update_fields=["compiled"])
            result.updated.append(content.github_path)
        if missing:
            result.issues.append(_missing_images_issue(content.github_path, missing))


def _sync_sections(link: CourseRepo, decks: dict[str, Deck], activities: dict[str, Activity],
                   markers: set[str]) -> None:
    """Una sección por carpeta con presentaciones o actividades (o marcador); el orden es el de los
    nombres, y cada archivo pertenece a la sección de su carpeta."""
    contents = {**decks, **activities}
    folders = {_folder_of(link, path) for path in contents} | markers
    folders.discard("")
    sections = {s.github_folder: s for s in Section.objects.filter(course=link.course).exclude(github_folder="")}
    for folder in folders - set(sections):
        sections[folder] = Section.objects.create(course=link.course, title=folder_title(folder),
                                                  github_folder=folder)
    for folder in set(sections) - folders:
        sections.pop(folder).delete()                  # la carpeta ya no existe en GitHub
    for position, folder in enumerate(sorted(sections, key=natural_key)):
        section = sections[folder]
        if section.position != position or section.title != folder_title(folder):
            section.position, section.title = position, folder_title(folder)
            section.save(update_fields=["position", "title"])
    for path, content in contents.items():
        section = sections.get(_folder_of(link, path))
        if content.section_id != (section.pk if section else None):
            content.section = section
            content.save(update_fields=["section"])


def maybe_pull(link: CourseRepo) -> PullResult | None:
    """Sincroniza al abrir el curso, sin repetir en cada recarga. Nunca rompe la página."""
    if link.last_synced_at and timezone.now() - link.last_synced_at < AUTO_PULL_AFTER:
        return None
    try:
        return pull(link)
    except GitHubError:
        return None


def _section_prefix(link: CourseRepo, section: Section | None) -> str:
    if section is None:
        return link.prefix
    if not section.github_folder:
        section.github_folder = clean_folder(section.title)
        section.save(update_fields=["github_folder"])
    return f"{link.prefix}{section.github_folder}/"


def _path_for(prefix: str, title: str, taken: set[str]) -> str:
    base = slugify(title) or "presentacion"
    path, n = f"{prefix}{base}.yaml", 2
    while path in taken:
        path, n = f"{prefix}{base}-{n}.yaml", n + 1
    return path


def _taken(link: CourseRepo, client: GitHubClient) -> set[str]:
    """Rutas ocupadas: las del repositorio y las que policlase ya asignó."""
    taken = set(Deck.objects.filter(course=link.course).values_list("github_path", flat=True))
    taken |= set(Activity.objects.filter(course=link.course).values_list("github_path", flat=True))
    return taken | set(client.tree(link.repo, link.branch))


def push(link: CourseRepo, content: Deck | Activity, source: str) -> tuple[str, str]:
    """Confirma la fuente de una presentación o una actividad en GitHub; devuelve (ruta, sha
    nuevo). No guarda nada en la base.

    Va a la carpeta de su sección. Si cambió de sección, el archivo se mueve a la carpeta nueva en
    el mismo commit (y solo si el original sigue como lo conocemos).
    """
    client = client_for(link.course.owner)
    prefix = _section_prefix(link, content.section)
    path = content.github_path
    folder = content.section.github_folder if content.section else ""
    if path and _folder_of(link, path) == folder:      # sigue en su sección: se actualiza en su sitio
        sha = client.write(link.repo, link.branch, path, source,
                           f"Actualiza «{content.title}» desde policlase", sha=content.github_sha)
        return path, sha

    taken = _taken(link, client) - {path}
    # Al mover se conserva el nombre del archivo; solo lo nuevo se nombra por su título.
    keep = f"{prefix}{path.rsplit('/', 1)[-1]}" if path else ""
    new_path = keep if keep and keep not in taken else _path_for(prefix, content.title, taken)
    if not path:
        sha = client.write(link.repo, link.branch, new_path, source, f"Crea «{content.title}» desde policlase")
        return new_path, sha
    shas = client.commit(link.repo, link.branch, {path: None, new_path: source},
                         f"Mueve «{content.title}» a {prefix or '/'} desde policlase",
                         expected={path: content.github_sha})
    return new_path, shas[new_path]


def delete(link: CourseRepo, content: Deck | Activity) -> None:
    if content.github_path and content.github_sha:
        client_for(link.course.owner).delete(link.repo, link.branch, content.github_path,
                                             f"Elimina «{content.title}» desde policlase", content.github_sha)


def create_section(link: CourseRepo, section: Section) -> None:
    """La carpeta de una sección nueva nace con el marcador: git no guarda carpetas vacías."""
    prefix = _section_prefix(link, section)
    client_for(link.course.owner).commit(link.repo, link.branch, {prefix + SECTION_MARKER: ""},
                                         f"Crea la sección «{section.title}» desde policlase")


def rename_section(link: CourseRepo, section: Section, title: str) -> None:
    """Renombra la carpeta: mueve todos sus archivos en un solo commit y actualiza las rutas."""
    client = client_for(link.course.owner)
    old = _section_prefix(link, section)
    new_folder = clean_folder(title)
    new = f"{link.prefix}{new_folder}/"
    if new == old:
        return
    tree = client.tree(link.repo, link.branch)
    if any(path.startswith(new) for path in tree):
        raise GitHubError(_("Ya existe una carpeta con ese nombre en GitHub."))
    moving = {path: sha for path, sha in tree.items() if path.startswith(old)}
    changes: dict[str, str | Blob | None] = {}
    for path, sha in moving.items():
        changes[path] = None
        changes[new + path[len(old):]] = Blob(sha)
    if not moving:
        changes[new + SECTION_MARKER] = ""
    shas = client.commit(link.repo, link.branch, changes,
                         f"Renombra la sección «{section.title}» a «{title}» desde policlase", expected=moving)
    with transaction.atomic():
        section.github_folder, section.title = new_folder, folder_title(new_folder)
        section.save(update_fields=["github_folder", "title"])
        for model in (Deck, Activity):
            for content in model.objects.filter(course=link.course, github_path__startswith=old):
                content.github_path = new + content.github_path[len(old):]
                content.github_sha = shas.get(content.github_path, content.github_sha)
                content.save(update_fields=["github_path", "github_sha"])


def delete_section(link: CourseRepo, section: Section) -> None:
    """Solo secciones vacías: borra el marcador (y con él la carpeta) en GitHub."""
    if not section.github_folder:
        return
    marker = f"{link.prefix}{section.github_folder}/{SECTION_MARKER}"
    client = client_for(link.course.owner)
    if marker in client.tree(link.repo, link.branch):
        client.commit(link.repo, link.branch, {marker: None},
                      f"Elimina la sección «{section.title}» desde policlase")


def export_local(link: CourseRepo) -> list[str]:
    """Al vincular: las presentaciones y actividades creadas antes en la web también pasan a
    GitHub, cada una en la carpeta de su sección."""
    exported = []
    for model in (Deck, Activity):
        # Una corrección no tiene archivo propio: es la misma evaluación (`Activity.correction_of`).
        local = model.objects.filter(course=link.course, github_path="")
        if model is Activity:
            local = local.filter(correction_of__isnull=True)
        for content in local.select_related("section"):
            path, sha = push(link, content, content.source)
            model.objects.filter(pk=content.pk).update(github_path=path, github_sha=sha)
            exported.append(path)
    for section in Section.objects.filter(course=link.course, decks__isnull=True, activities__isnull=True):
        create_section(link, section)
    return exported
