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


def groups(content: dict) -> list[dict]:
    """Las secciones de la actividad: cada una empieza en un segmento con título o en un título
    `#`/`##` del contenido, y reúne los bloques hasta la siguiente. [{"title", "ats", "items"}],
    con las posiciones de todos sus bloques y los `item_id` de sus ítems. Lo anterior al primer
    título va en una sección sin título."""
    out: list[dict] = [{"title": "", "ats": [], "items": []}]
    for block in content.get("items", []):
        kind = block.get("kind", "item")
        found = heading(block.get("markdown", "")) if kind == "content" else None
        if (kind == "segment" and block.get("title")) or (found and found[0] <= 2):
            out.append({"title": block.get("title") or found[1], "ats": [], "items": []})
        out[-1]["ats"].append(block.get("at"))
        if kind == "item":
            out[-1]["items"].append(block.get("item_id", ""))
    return [g for g in out if g["ats"]]


def hidden_blocks(content: dict, disabled) -> set:
    """Las posiciones que el estudiante no ve porque el docente deshabilitó esos ítems: los ítems y,
    si toda una sección quedó deshabilitada, también su contenido (el enunciado del ejercicio)."""
    disabled = set(disabled or [])
    if not disabled:
        return set()
    out = set()
    for group in groups(content):
        if group["items"] and all(i in disabled for i in group["items"]):
            out.update(group["ats"])
    for block in content.get("items", []):
        if block.get("kind", "item") == "item" and block.get("item_id") in disabled:
            out.add(block.get("at"))
    return out


def parts(content: dict, titles: dict[str, str] | None = None) -> list[dict]:
    """Los ítems agrupados por sección, para habilitar o deshabilitar secciones:
    [{"title", "items": [entrada de ítem, …]}]."""
    entries = {e["item_id"]: e for e in build(content, titles) if e["kind"] == "item"}
    return [{"title": g["title"], "items": [entries[i] for i in g["items"] if i in entries]}
            for g in groups(content) if g["items"]]


def for_review(content: dict, current: dict, disabled=None) -> list[dict]:
    """Los bloques de un intento para la revisión, con las diapositivas de solución de la versión
    actual de la actividad: el intento copia la actividad al empezar, y las soluciones suelen
    escribirse después. Cada solución va tras el ítem que la precede en la versión actual (al
    final, si ese ítem no está en el intento). Con `disabled`, sin lo que el estudiante no vio."""
    disabled = set(disabled or [])
    hidden = hidden_blocks(content, disabled)
    after: dict[str, list] = {}
    anchor = ""
    for block in current.get("items", []):
        kind = block.get("kind", "item")
        if kind == "item":
            anchor = block.get("item_id", "")
        elif kind == "solution" and anchor not in disabled:
            after.setdefault(anchor, []).append(block)
    out = list(after.pop("", []))
    for block in content.get("items", []):
        kind = block.get("kind", "item")
        if kind == "solution":
            continue                                   # se usan las de la versión actual
        if block.get("at") not in hidden:
            out.append(block)
        if kind == "item":
            out.extend(after.pop(block.get("item_id", ""), []))
    for rest in after.values():
        out.extend(rest)
    return out


def with_explanations(blocks: list[dict], waiting=()) -> list[dict]:
    """Cada diapositiva de solución pasa a su ítem (el que la precede) como su explicación, que el
    estudiante abre con «Ver la explicación» después de ver su corrección. Las de los ítems en
    `waiting` (con una pregunta en debate) se retienen hasta que se cierre: el ítem queda marcado
    con `explanation_waits`. Una solución antes de todo ítem queda como bloque."""
    waiting = set(waiting)
    out: list[dict] = []
    last = None
    for block in blocks:
        kind = block.get("kind", "item")
        if kind == "solution" and last is not None:
            if last.get("item_id") in waiting:
                last["explanation_waits"] = True
            else:
                last.setdefault("explanations", []).append(block)
            continue
        if kind == "item":
            block = {**block}
            last = block
        out.append(block)
    return out


def explanations(content: dict, current: dict, disabled=None) -> dict[str, dict]:
    """`item_id` → {"explanations": [diapositivas de solución]} de cada ítem que tiene explicación,
    con las soluciones de la versión actual (como en la revisión)."""
    blocks = with_explanations(for_review(content, current, disabled))
    return {b["item_id"]: {"explanations": b["explanations"]} for b in blocks
            if b.get("kind", "item") == "item" and b.get("explanations")}
