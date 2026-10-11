"""Recepción de imágenes entregadas.

Solo JPEG, PNG y WebP, reconocidos por su contenido y no por la extensión. Cada imagen se
decodifica y se vuelve a codificar como JPEG: lo que se guarda es una imagen nueva, sin los
metadatos del original (las fotos del teléfono traen la ubicación GPS) y sin bytes que no sean
de la imagen. Nunca SVG: es XML y puede llevar scripts.
"""

from __future__ import annotations

import io

from django.core.files.base import ContentFile
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP"}
#: Lo que acepta el selector de archivos del navegador (el servidor igual lo verifica).
ACCEPT_ATTR = "image/jpeg,image/png,image/webp"
MAX_BYTES = 15 * 1024 * 1024
#: Más que cualquier foto de teléfono; frena las «bombas» de descompresión.
MAX_PIXELS = 50_000_000
#: Lado mayor de lo que se guarda: se lee bien un desarrollo a mano y pesa poco.
MAX_SIDE = 2000
QUALITY = 85


class ImageError(Exception):
    """El archivo no se acepta; el mensaje se le muestra al estudiante."""


def process(upload) -> tuple[ContentFile, int, int]:
    """Valida y re-codifica. Devuelve (archivo JPEG, ancho, alto)."""
    if upload.size > MAX_BYTES:
        raise ImageError(_("La imagen supera 15 MB."))
    data = upload.read()
    try:
        with Image.open(io.BytesIO(data)) as probe:
            kind = probe.format
            width, height = probe.size
            probe.verify()              # estructura íntegra, sin decodificar todavía
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise ImageError(_("El archivo no es una imagen válida. Use JPEG, PNG o WebP.")) from None
    if kind not in ACCEPTED_FORMATS:
        raise ImageError(_("Formato no admitido (%(kind)s). Use JPEG, PNG o WebP.") % {"kind": kind or "?"})
    if width * height > MAX_PIXELS:
        raise ImageError(_("La imagen es demasiado grande (%(w)d × %(h)d píxeles).") % {"w": width, "h": height})

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)        # respetar la orientación antes de soltar el EXIF
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, (255, 255, 255))
                flat.paste(image, mask=image.getchannel("A"))
                image = flat
            elif image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=QUALITY, optimize=True)
            size = image.size
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ImageError(_("No se pudo leer la imagen. Pruebe con otra foto o captura.")) from None
    return ContentFile(out.getvalue()), size[0], size[1]
