"""Sincronización de presentaciones entre un curso y una carpeta de GitHub.

- `pull`: GitHub → policlase. Compara los blob sha del árbol con los guardados y solo descarga
  lo que cambió. Un archivo con errores no reemplaza la versión válida anterior: queda anotado en
  `CourseRepo.issues` para mostrarlo en el curso.
- `push_deck` / `delete_deck`: policlase → GitHub, con el sha conocido. Si alguien cambió el
  archivo en GitHub entretanto, GitHub responde conflicto y no se pisa nada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _
from policlase_gen import deck as deckfmt

from apps.live.models import Deck

from .client import Conflict, GitHubClient, GitHubError  # noqa: F401  (reexportados)
from .models import CourseRepo

YAML_SUFFIXES = (".yaml", ".yml")
#: Al abrir el curso se sincroniza si pasó al menos esto desde la última vez.
AUTO_PULL_AFTER = timedelta(seconds=60)


def client_for(user) -> GitHubClient:
    account = getattr(user, "github", None)
    token = account.token if account else ""
    if not token:
        raise GitHubError(_("Conecte su cuenta de GitHub en el menú de usuario → GitHub."))
    return GitHubClient(token)


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
            Deck.objects.create(course=link.course, title=compiled["title"], source=source,
                                compiled=compiled, github_path=path, github_sha=sha)
            result.created.append(path)
        else:
            deck.title, deck.source, deck.compiled, deck.github_sha = compiled["title"], source, compiled, sha
            deck.save()
            result.updated.append(path)

    for path, deck in decks.items():
        if path not in files:
            # Las clases ya dictadas guardan su copia de las diapositivas: borrar no pierde resultados.
            deck.delete()
            result.deleted.append(path)

    link.issues, link.skipped, link.last_error, link.last_synced_at = result.issues, skipped, "", timezone.now()
    link.save(update_fields=["issues", "skipped", "last_error", "last_synced_at"])
    return result


def maybe_pull(link: CourseRepo) -> PullResult | None:
    """Sincroniza al abrir el curso, sin repetir en cada recarga. Nunca rompe la página."""
    if link.last_synced_at and timezone.now() - link.last_synced_at < AUTO_PULL_AFTER:
        return None
    try:
        return pull(link)
    except GitHubError:
        return None


def _path_for(link: CourseRepo, title: str, taken: set[str]) -> str:
    base = slugify(title) or "presentacion"
    path, n = f"{link.prefix}{base}.yaml", 2
    while path in taken:
        path, n = f"{link.prefix}{base}-{n}.yaml", n + 1
    return path


def push_deck(link: CourseRepo, deck: Deck, source: str) -> tuple[str, str]:
    """Confirma la fuente en GitHub; devuelve (ruta, sha nuevo). No guarda el Deck."""
    client = client_for(link.course.owner)
    path = deck.github_path
    if not path:
        taken = set(Deck.objects.filter(course=link.course).values_list("github_path", flat=True))
        taken |= set(client.tree(link.repo, link.branch))
        path = _path_for(link, deck.title, taken)
    verb = "Actualiza" if deck.github_sha else "Crea"
    sha = client.write(link.repo, link.branch, path, source,
                       f"{verb} «{deck.title}» desde policlase", sha=deck.github_sha)
    return path, sha


def delete_deck(link: CourseRepo, deck: Deck) -> None:
    if deck.github_path and deck.github_sha:
        client_for(link.course.owner).delete(link.repo, link.branch, deck.github_path,
                                             f"Elimina «{deck.title}» desde policlase", deck.github_sha)


def export_local_decks(link: CourseRepo) -> list[str]:
    """Al vincular: las presentaciones creadas antes en la web también pasan a GitHub."""
    exported = []
    for deck in Deck.objects.filter(course=link.course, github_path=""):
        path, sha = push_deck(link, deck, deck.source)
        with transaction.atomic():
            Deck.objects.filter(pk=deck.pk).update(github_path=path, github_sha=sha)
        exported.append(path)
    return exported
