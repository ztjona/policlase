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
