"""Emparejar los nombres de una lista pegada (otra plataforma, una hoja de cálculo) con los
estudiantes inscritos.

Los nombres de afuera vienen como cada estudiante los escribió: «Robalino.T.Valeria», «Pineda O.
Danny J.», «Ogonaga M Jairr». Se comparan por palabras, sin tildes ni mayúsculas: hace falta que
coincida un apellido; si además coincide un nombre, la coincidencia es segura, y si no, queda
«por confirmar». Las iniciales suman poco y las palabras de cuatro letras o más aceptan un error
al final («Jairr»). Nunca se asigna a ciegas: el docente revisa antes de guardar.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

LINE = re.compile(r"^(?P<name>.*?)[\s;,:|\t]*(?P<score>[-+]?\d+(?:[.,]\d+)?)\s*$")
RANK = re.compile(r"^\s*\d+\s*[.)º°-]?\s+")

STRONG, WEAK = "strong", "weak"


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn").lower()
    return [w for w in re.split(r"[^a-z0-9]+", text) if w]


def parse_lines(text: str) -> tuple[list[tuple[str, Decimal]], list[str]]:
    """«Nombre  puntaje» por línea (el puntaje es el último número). Devuelve las filas leídas y
    las líneas que no se entendieron."""
    rows, bad = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = LINE.match(line)
        name = RANK.sub("", match.group("name")).strip(" \t;,:|-") if match else ""
        try:
            score = Decimal(match.group("score").replace(",", ".")) if match else None
        except InvalidOperation:
            score = None
        if not name or score is None or not words(name):
            bad.append(line)
            continue
        rows.append((name, score))
    return rows, bad


@dataclass
class Match:
    name: str
    score: Decimal
    student: object | None = None
    strength: str = ""
    weight: float = 0.0
    also: str = ""                    # otro nombre pegado que coincidía con el mismo estudiante


def _weigh(pasted: list[str], student) -> tuple[float, bool, bool]:
    last, first = words(student.last_name), words(student.first_name)
    every = last + first
    weight, surname, given = 0.0, False, False
    for w in pasted:
        if len(w) == 1:
            weight += 0.5 if any(x.startswith(w) for x in every) else 0
        elif w in last:
            weight, surname = weight + 3, True
        elif w in first:
            weight, given = weight + 2, True
        elif len(w) >= 4:
            near = [x for x in every if len(x) >= 4 and (x.startswith(w[:-1]) or w.startswith(x))]
            if near:
                weight += 1.5
                surname = surname or near[0] in last
                given = given or near[0] in first
    return weight, surname, given


def best(name: str, students) -> tuple[object | None, str, float]:
    """El inscrito que mejor coincide con `name`, la seguridad y el peso; (None, '', 0) si ninguno
    comparte un apellido o si dos empatan."""
    pasted = words(name)
    ranked = []
    for student in students:
        weight, surname, given = _weigh(pasted, student)
        if surname:
            ranked.append((weight, STRONG if given else WEAK, student))
    if not ranked:
        return None, "", 0.0
    ranked.sort(key=lambda r: -r[0])
    if len(ranked) > 1 and ranked[1][0] == ranked[0][0]:
        return None, "", 0.0
    weight, strength, student = ranked[0]
    return student, strength, weight


def match_all(rows: list[tuple[str, Decimal]], students) -> list[Match]:
    """Empareja cada fila. Si dos filas caen en el mismo estudiante (entró dos veces, un nombre
    parecido), se queda la más segura y la otra queda sin asignar con el aviso."""
    matches = []
    for name, score in rows:
        student, strength, weight = best(name, students)
        matches.append(Match(name=name, score=score, student=student, strength=strength, weight=weight))
    taken: dict[int, Match] = {}
    rank = {STRONG: 2, WEAK: 1}
    for m in sorted((m for m in matches if m.student),
                    key=lambda m: (-rank[m.strength], -m.weight, -m.score)):
        if m.student.pk in taken:
            m.also = taken[m.student.pk].name
            m.student, m.strength = None, ""
        else:
            taken[m.student.pk] = m
    return matches
