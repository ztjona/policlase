"""Sincronización de presentaciones entre un curso y una carpeta de GitHub.

- `pull`: GitHub → policlase. Compara los blob sha del árbol con los guardados y solo descarga
  lo que cambió. Un archivo con errores no reemplaza la versión válida anterior: queda anotado en
  `CourseRepo.issues` para mostrarlo en el curso.
- `push_deck` / `delete_deck`: policlase → GitHub, con el sha conocido. Si alguien cambió el
  archivo en GitHub entretanto, GitHub responde conflicto y no se pisa nada.

Secciones: cada subcarpeta (primer nivel bajo la carpeta del curso) que contenga al menos una
presentación —o el marcador `SECTION_MARKER`, para unidades aún vacías— es una sección. Así una
carpeta de generadores o de ítems no aparece como unidad. Mover y renombrar se hacen en un solo
commit (`GitHubClient.commit`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from policlase_gen import deck as deckfmt

from apps.live.models import Deck, Section

from .client import Blob, Conflict, GitHubClient, GitHubError  # noqa: F401  (reexportados)
from .models import CourseRepo

YAML_SUFFIXES = (".yaml", ".yml")
#: Archivo vacío que mantiene en git una sección sin presentaciones todavía.
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
    markers = {_folder_of(link, path) for path in tree
               if path.startswith(link.prefix) and path.endswith("/" + SECTION_MARKER)}
    decks = {d.github_path: d for d in Deck.objects.filter(course=link.course).exclude(github_path="")}

    previous_issues = {i["path"]: i for i in link.issues}
    skipped: dict[str, str] = {}
    for path, sha in sorted(files.items()):
        deck = decks.get(path)
        if deck and deck.github_sha == sha:
            continue
        if link.skipped.get(path) == sha:              # ya visto y descartado: sin cambios
            skipped[path] = sha
            if path in previous_issues:
                result.issues.append(previous_issues[path])
            continue
        source = client.read(link.repo, sha)
        document, report = deckfmt.load_deck_text(source)
        if document is not None and not deckfmt.is_deck(document):
            skipped[path] = sha                        # otro YAML (ítems, configuración): no es nuestro
            continue
        if document is None or report.errors:
            skipped[path] = sha
            result.issues.append({"path": path, "diagnostics": _diagnostics(report)})
            continue
        compiled = deckfmt.compile_deck(document)
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

    _sync_sections(link, decks, markers)
    link.issues, link.skipped, link.last_error, link.last_synced_at = result.issues, skipped, "", timezone.now()
    link.save(update_fields=["issues", "skipped", "last_error", "last_synced_at"])
    return result


def _sync_sections(link: CourseRepo, decks: dict[str, Deck], markers: set[str]) -> None:
    """Una sección por carpeta con presentaciones (o marcador); el orden es el de los nombres."""
    folders = {_folder_of(link, path) for path in decks} | markers
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
    for path, deck in decks.items():
        section = sections.get(_folder_of(link, path))
        if deck.section_id != (section.pk if section else None):
            deck.section = section
            deck.save(update_fields=["section"])


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


def push_deck(link: CourseRepo, deck: Deck, source: str) -> tuple[str, str]:
    """Confirma la fuente en GitHub; devuelve (ruta, sha nuevo). No guarda el Deck.

    Si la presentación cambió de sección, el archivo se mueve a la carpeta nueva en el mismo
    commit (y solo si el original sigue como lo conocemos).
    """
    client = client_for(link.course.owner)
    prefix = _section_prefix(link, deck.section)
    path = deck.github_path
    folder = deck.section.github_folder if deck.section else ""
    if path and _folder_of(link, path) == folder:      # sigue en su sección: se actualiza en su sitio
        sha = client.write(link.repo, link.branch, path, source,
                           f"Actualiza «{deck.title}» desde policlase", sha=deck.github_sha)
        return path, sha

    taken = set(Deck.objects.filter(course=link.course).values_list("github_path", flat=True))
    taken |= set(client.tree(link.repo, link.branch))
    new_path = _path_for(prefix, deck.title, taken - {path})
    if not path:
        sha = client.write(link.repo, link.branch, new_path, source, f"Crea «{deck.title}» desde policlase")
        return new_path, sha
    shas = client.commit(link.repo, link.branch, {path: None, new_path: source},
                         f"Mueve «{deck.title}» a {prefix or '/'} desde policlase",
                         expected={path: deck.github_sha})
    return new_path, shas[new_path]


def delete_deck(link: CourseRepo, deck: Deck) -> None:
    if deck.github_path and deck.github_sha:
        client_for(link.course.owner).delete(link.repo, link.branch, deck.github_path,
                                             f"Elimina «{deck.title}» desde policlase", deck.github_sha)


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
        for deck in Deck.objects.filter(course=link.course, github_path__startswith=old):
            deck.github_path = new + deck.github_path[len(old):]
            deck.github_sha = shas.get(deck.github_path, deck.github_sha)
            deck.save(update_fields=["github_path", "github_sha"])


def delete_section(link: CourseRepo, section: Section) -> None:
    """Solo secciones vacías: borra el marcador (y con él la carpeta) en GitHub."""
    if not section.github_folder:
        return
    marker = f"{link.prefix}{section.github_folder}/{SECTION_MARKER}"
    client = client_for(link.course.owner)
    if marker in client.tree(link.repo, link.branch):
        client.commit(link.repo, link.branch, {marker: None},
                      f"Elimina la sección «{section.title}» desde policlase")


def export_local_decks(link: CourseRepo) -> list[str]:
    """Al vincular: las presentaciones creadas antes en la web también pasan a GitHub, cada una
    en la carpeta de su sección."""
    exported = []
    for deck in Deck.objects.filter(course=link.course, github_path="").select_related("section"):
        path, sha = push_deck(link, deck, deck.source)
        with transaction.atomic():
            Deck.objects.filter(pk=deck.pk).update(github_path=path, github_sha=sha)
        exported.append(path)
    for section in Section.objects.filter(course=link.course, decks__isnull=True):
        create_section(link, section)
    return exported
