"""Markdown con matemáticas, saneado.

`dollarmath` separa `$…$` y `$$…$$` *antes* de que markdown interprete `_` o `*`, que es lo
que rompe el LaTeX en los convertidores ingenuos. Las fórmulas salen como
`<span class="math inline">` / `<div class="math block">` y las dibuja KaTeX en el navegador.

Todo pasa por nh3: una presentación la escribe un profesor, pero un enunciado con HTML
arbitrario llegaría a los teléfonos de todo el curso.
"""

from __future__ import annotations

from functools import lru_cache

import nh3
from markdown_it import MarkdownIt
from mdit_py_plugins.dollarmath import dollarmath_plugin

_ALLOWED_TAGS = {
    "p", "br", "hr", "strong", "em", "code", "pre", "blockquote", "a", "img",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "table", "thead", "tbody", "tr", "th", "td",
    "span", "div", "del", "sup", "sub",
}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "span": {"class"},
    "div": {"class"},
    "th": {"style"},
    "td": {"style"},
}


@lru_cache(maxsize=1)
def _parser() -> MarkdownIt:
    return (
        MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
        .enable("table")
        .enable("strikethrough")
        .use(dollarmath_plugin, allow_space=True, double_inline=True)
    )


def _clean(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )


@lru_cache(maxsize=2048)
def render(text: str) -> str:
    return _clean(_parser().render(text or ""))


@lru_cache(maxsize=2048)
def render_inline(text: str) -> str:
    """Para opciones y afirmaciones: sin el `<p>` que envuelve un párrafo."""
    return _clean(_parser().renderInline(text or ""))
