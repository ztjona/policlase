"""Nombres y apellidos en mayúscula inicial, aunque se escriban todo en mayúsculas o en minúsculas:
«CAIZA OÑA» → «Caiza Oña», «MARÍA DEL CISNE» → «María del Cisne», «DE LA TORRE» → «De la Torre».

Solo se tocan las palabras escritas todas en mayúsculas o todas en minúsculas: una mezcla
deliberada («McDonald», «DiCaprio») se respeta. Las partículas van en minúscula salvo al comienzo.
"""

import re

PARTICLES = {"de", "del", "la", "las", "los", "y", "e", "da", "das", "do", "dos", "di", "van", "von", "der", "den"}


def _cap(part: str) -> str:
    # «O'BRIEN» → «O'Brien»; «MARÍA-JOSÉ» → «María-José».
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), part)


def name_case(text: str) -> str:
    words = str(text or "").split()
    out = []
    for i, word in enumerate(words):
        letters = [c for c in word if c.isalpha()]
        uniform = letters and (all(c.isupper() for c in letters) or all(c.islower() for c in letters))
        if not uniform or (len(letters) == 1 and word.isupper()):
            out.append(word)                                  # mezcla deliberada, o una inicial («A.»)
        elif i > 0 and word.lower() in PARTICLES:
            out.append(word.lower())
        else:
            out.append(_cap(word))
    return " ".join(out)
