"""Índice de una actividad: sus partes (segmentos con título), los títulos del contenido y los ítems.

Lo usan el panel izquierdo del intento (para desplazarse y ver qué falta), las partes que se
califican en la página del docente y el proyector. Se arma desde el contenido compilado del intento
(`Attempt.content`), que es lo que el estudiante tiene delante.
"""

from __future__ import annotations

import re

from django.utils.translation import gettext as _

HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def heading(markdown: str) -> tuple[int, str] | None:
    """El primer título (`#`, `##` o `###`) de un bloque de contenido, con su nivel."""
    for line in str(markdown or "").splitlines():
        m = HEADING.match(line.strip())
        if m:
            return len(m.group(1)), m.group(2)
    return None


def item_title(item: dict, titles: dict[str, str] | None = None, number: int | None = None) -> str:
    """El título del ítem; si no lo tiene (o el intento es anterior a que se compilara), el de la
    actividad actual o la primera línea de su enunciado. Nunca la pregunta de un ítem con tiempo:
    no se ve hasta que el estudiante la abre."""
    title = item.get("title") or (titles or {}).get(item.get("item_id", ""), "")
    if title:
        return title
    questions = item.get("questions") or [{}]
    timed = any(q.get("time_limit_s") for q in questions)
    text = item.get("stem") or ("" if timed else questions[0].get("prompt", ""))
    for line in str(text).splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:70] + ("…" if len(line) > 70 else "")
    return _("Ítem %(n)d") % {"n": number} if number else _("Ítem")


def build(content: dict, titles: dict[str, str] | None = None, solutions: bool = False) -> list[dict]:
    """Entradas en orden de lectura: {"kind", "level", "title", "at", "item_id", "keys"}.

    Nivel 1: una parte (segmento con título). Nivel 2: un título `#`/`##` del contenido. Nivel 3:
    un `###` o un ítem. Las diapositivas de solución solo con `solutions`."""
    out, number = [], 0
    for block in content.get("items", []):
        kind = block.get("kind", "item")
        if kind == "segment":
            if block.get("title"):
                out.append({"kind": "segment", "level": 1, "title": block["title"], "at": block.get("at"),
                            "item_id": "", "keys": []})
        elif kind in ("content", "solution"):
            if kind == "solution" and not solutions:
                continue
            found = heading(block.get("markdown", ""))
            if found:
                level, text = found
                if kind == "solution":
                    text = _("Solución: %(title)s") % {"title": text}
                out.append({"kind": kind, "level": 3 if level == 3 else 2, "title": text, "at": block.get("at"),
                            "item_id": "", "keys": []})
            elif kind == "solution":
                out.append({"kind": kind, "level": 3, "title": _("Solución"), "at": block.get("at"),
                            "item_id": "", "keys": []})
        else:
            number += 1
            out.append({"kind": "item", "level": 3, "title": item_title(block, titles, number), "at": block.get("at"),
                        "item_id": block.get("item_id", ""), "keys": [q["key"] for q in block.get("questions", [])],
                        "points": float(block.get("points") or 0)})
    return out


def parts(content: dict, titles: dict[str, str] | None = None) -> list[dict]:
    """Los ítems agrupados bajo su parte o título más cercano, para elegir qué se califica:
    [{"title", "items": [entrada de ítem, …]}]. Los ítems antes de cualquier título van en un grupo
    sin título."""
    groups: list[dict] = [{"title": "", "items": []}]
    for entry in build(content, titles):
        if entry["kind"] == "item":
            groups[-1]["items"].append(entry)
        elif entry["level"] <= 2:
            groups.append({"title": entry["title"], "items": []})
    return [g for g in groups if g["items"]]
