"""Imágenes del repositorio.

En un curso vinculado a GitHub, las presentaciones y actividades muestran imágenes guardadas en la
carpeta del curso con la sintaxis de siempre, `![Gráfica del error](img/error.png)`. La ruta es
relativa al archivo YAML, como en GitHub; si empieza con `/`, es relativa a la carpeta del curso.

Al sincronizar, cada imagen se descarga una sola vez, nombrada por su sha de git, a
`MEDIA_ROOT/repo/`. Al compilar, la ruta se reescribe a `/imagenes/<sha>.<ext>`: una dirección que
depende del contenido, así que no cambia nunca (el navegador la guarda en caché para siempre), las
clases ya dictadas y los intentos ya enviados siguen mostrando la imagen con la que se hicieron, y no
se puede adivinar sin conocer la imagen (el repositorio puede ser privado).
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from urllib.parse import unquote

from django.conf import settings
from django.urls import reverse

#: Extensión → tipo. SVG se sirve con una política que impide ejecutar sus scripts (views.repo_image).
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
         ".webp": "image/webp", ".svg": "image/svg+xml"}
MAX_BYTES = 5 * 1024 * 1024
#: La sincronización corre al abrir el curso: un repositorio con muchas imágenes nuevas no la demora
#: más de la cuenta. Lo que falte llega en la siguiente (y los contenidos que las citan se actualizan).
MAX_NEW_PER_PULL = 40
NAME = re.compile(r"^[0-9a-f]{40}\.(png|jpe?g|gif|webp|svg)$")

# ![alt](ruta "título") o ![alt](<ruta con espacios>)
_IMAGE = re.compile(r'!\[(?P<alt>[^\]]*)\]\(\s*(?:<(?P<angled>[^>]+)>|(?P<src>[^)\s]+))(?P<title>\s+"[^"]*")?\s*\)')


def folder() -> Path:
    return Path(settings.MEDIA_ROOT) / "repo"


def is_image(path: str) -> bool:
    return posixpath.splitext(path.lower())[1] in TYPES


def _looks_right(data: bytes, ext: str) -> bool:
    """Que el contenido sea de verdad del tipo que dice la extensión."""
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if ext == ".webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return b"<svg" in data[:4096].lower()


def fetch(link, client, tree: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Descarga las imágenes nuevas de la carpeta del curso. Devuelve ({ruta: archivo}, {ruta: problema})."""
    from django.utils.translation import gettext as _

    found, problems, downloaded = {}, {}, 0
    folder().mkdir(parents=True, exist_ok=True)
    for path, sha in tree.items():
        if not (path.startswith(link.prefix) and is_image(path)):
            continue
        ext = posixpath.splitext(path.lower())[1]
        name = f"{sha}{'.jpg' if ext == '.jpeg' else ext}"
        target = folder() / name
        if not target.exists():
            if downloaded >= MAX_NEW_PER_PULL:
                continue
            downloaded += 1
            data = client.read_bytes(link.repo, sha)
            if len(data) > MAX_BYTES:
                problems[path] = _("la imagen supera 5 MB y no se descargó.")
                continue
            if not _looks_right(data, ext):
                problems[path] = _("el archivo no es una imagen %(ext)s válida y no se descargó.") % {"ext": ext}
                continue
            partial = target.with_suffix(target.suffix + ".parcial")
            partial.write_bytes(data)
            partial.replace(target)                 # nunca queda a medias con el nombre final
        found[path] = name
    return found, problems


def resolve(path: str, document_path: str, prefix: str) -> str:
    """La ruta en el repositorio de una imagen citada desde `document_path`."""
    path = unquote(path)
    if path.startswith("/"):
        return posixpath.normpath(prefix + path.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(document_path), path))


def _external(src: str) -> bool:
    """Una dirección externa, o una imagen del repositorio ya reescrita: no se tocan."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I) or src.startswith(("//", "#")):
        return True
    served = reverse("repo_image", args=["x.png"]).rsplit("x.png", 1)[0]
    return src.startswith(served) and bool(NAME.match(src[len(served):]))


def rewrite(compiled, document_path: str, prefix: str, images: dict[str, str]):
    """Reescribe las imágenes del repositorio en todo el contenido compilado (enunciados, opciones,
    soluciones…). Devuelve (contenido, [rutas no encontradas]). Las direcciones externas no se tocan."""
    missing: list[str] = []

    def one(match):
        src = match.group("angled") or match.group("src")
        if _external(src):
            return match.group(0)
        target = resolve(src, document_path, prefix)
        name = images.get(target)
        if name is None:
            if target not in missing:
                missing.append(target)
            return match.group(0)
        url = reverse("repo_image", args=[name])
        return f"![{match.group('alt')}]({url}{match.group('title') or ''})"

    def walk(value):
        if isinstance(value, str):
            return _IMAGE.sub(one, value) if "![" in value else value
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    return walk(compiled), missing


def relative_images(compiled) -> list[str]:
    """Las imágenes con ruta relativa (del repositorio) que cita un contenido compilado."""
    found: list[str] = []

    def walk(value):
        if isinstance(value, str) and "![" in value:
            for m in _IMAGE.finditer(value):
                src = m.group("angled") or m.group("src")
                if not _external(src) and src not in found:
                    found.append(src)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)

    walk(compiled)
    return found
