from django import template
from django.utils.safestring import mark_safe

from apps.core import markdown

register = template.Library()


@register.filter
def md(text):
    return mark_safe(markdown.render(str(text or "")))


@register.filter
def md_inline(text):
    return mark_safe(markdown.render_inline(str(text or "")))


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def percent(value, total):
    try:
        return round(100 * float(value) / float(total)) if float(total) else 0
    except (TypeError, ValueError):
        return 0


@register.filter
def letter(index):
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[int(index) % 26]


@register.filter
def slide_title(slide):
    """Una línea que identifica la diapositiva en el panel lateral."""
    from django.utils.translation import gettext as _

    kind = slide.get("kind")
    if kind == "feedback":
        return _("¿Cómo estuvo la clase? (anónima)")
    text = slide.get("markdown", "") if kind == "content" else (slide.get("question") or {}).get("prompt", "")
    for line in str(text).splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:80] + ("…" if len(line) > 80 else "")
    return "—"


#: Íconos tradicionales (trazos de Material Icons, Apache 2.0) en SVG: se ven igual en todas partes.
ICONS = {
    "gear": "M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.48.48 0 0 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.49.49 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z",
    "edit": "M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z",
    "trash": "M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z",
    "play": "M8 5v14l11-7z",
    "close": "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
    "grip": "M9 3h2v2H9zm4 0h2v2h-2zM9 8h2v2H9zm4 0h2v2h-2zm-4 5h2v2H9zm4 0h2v2h-2zm-4 5h2v2H9zm4 0h2v2h-2z",
    "folder_add": "M20 6h-8l-2-2H4c-1.11 0-2 .89-2 2v12c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V8c0-1.11-.89-2-2-2zm-1 8h-3v3h-2v-3h-3v-2h3V9h2v3h3v2z",
    "file_add": "M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm2 14h-3v3h-2v-3H8v-2h3v-3h2v3h3v2zm-3-7V3.5L18.5 9H13z",
    "results": "M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z",
    "check": "M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
}


@register.simple_tag
def icon(name, size=18):
    return mark_safe(f'<svg class="i" viewBox="0 0 24 24" width="{int(size)}" height="{int(size)}" '
                     f'aria-hidden="true" focusable="false"><path fill="currentColor" d="{ICONS[name]}"/></svg>')
