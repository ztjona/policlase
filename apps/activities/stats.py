"""Estadísticas de una actividad para el docente: la nota, cada pregunta, el tiempo y las señales.

Se calculan sobre los intentos que cuentan (el último enviado de cada estudiante). Con pocos
estudiantes, una correlación o una pregunta «difícil» son indicios que piden revisar, no
conclusiones: la página lo dice junto a cada cifra que se presta a sobreinterpretarse.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter

from django.utils.translation import gettext as _

from .engine import blank

#: Con menos entregas, la desviación, la discriminación y la correlación no se muestran.
MIN_FOR_SPREAD = 2
MIN_FOR_CORRELATION = 5
#: Grupos de la tabla de señales: (desde, hasta) salidas de la página, ambos incluidos.
BLUR_GROUPS = ((0, 0), (1, 5), (6, 20), (21, None))


def summary(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
        "sd": statistics.stdev(values) if len(values) >= MIN_FOR_SPREAD else None,
        "min": min(values), "max": max(values),
    }


def pearson(x: list[float], y: list[float]) -> float | None:
    """Correlación de Pearson; None si alguna de las dos series es constante o hay pocos datos."""
    if len(x) < MIN_FOR_CORRELATION:
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def ranks(values: list[float]) -> list[float]:
    """Rangos con empates promediados (0, 1, 2…)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2
        i = j + 1
    return out


def spearman(x: list[float], y: list[float]) -> float | None:
    """Correlación por rangos: no la dominan uno o dos valores extremos, como sí a la de Pearson."""
    return pearson(ranks(x), ranks(y))


def chance_bound(n: int) -> float:
    """Por debajo de este valor absoluto, una correlación de Spearman con `n` datos es compatible
    con el azar (aproximación normal, 95 %)."""
    return 1.96 / math.sqrt(n - 1) if n > 1 else 1.0


def duration_text(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    if seconds < 60:
        return _("%(s)d s") % {"s": seconds}
    minutes, seconds = divmod(seconds, 60)
    if minutes < 10:                   # en una salida breve, los segundos importan
        return _("%(m)d min %(s)02d s") % {"m": minutes, "s": seconds}
    if minutes < 60:
        return _("%(m)d min") % {"m": minutes}
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return _("%(h)d h %(m)02d min") % {"h": hours, "m": minutes}
    return _("%(d)d d %(h)d h") % {"d": hours // 24, "h": hours % 24}


def _records(attempts) -> tuple[dict, dict]:
    """De todas las versiones que vieron los estudiantes: la pregunta y su registro de solución por
    clave. Una pregunta corregida después de empezar conserva su clave, y sus opciones, la suya."""
    questions, solutions = {}, {}
    for attempt in attempts:
        for item in attempt.content.get("items", []):
            for q in item.get("questions", []):
                questions.setdefault(q["key"], {**q, "item_id": item.get("item_id", ""),
                                                 "item_title": item.get("title", "")})
                solutions.setdefault(q["key"], (item.get("solutions") or {}).get(q["id"], {}))
    return questions, solutions


def _shown(question: dict, value) -> str:
    """Una respuesta tal como conviene mostrarla en la lista de las más frecuentes."""
    options = {o["key"]: o["text"] for o in question.get("options") or []}
    if question["type"] == "choice":
        return options.get(value, str(value))
    if question["type"] == "multi_choice":
        return " · ".join(options.get(v, str(v)) for v in value or [])
    if isinstance(value, list):
        return " · ".join(str(v).strip() or "—" for v in value)
    return str(value).strip()


def _statements(question: dict, solution: dict, answers) -> list[dict]:
    """En verdadero o falso, cuántos acertaron cada afirmación."""
    truth = solution.get("answers") or {}
    out = []
    for s in question.get("statements") or []:
        given = [a.value.get(s["key"]) for a in answers if isinstance(a.value, dict) and s["key"] in a.value]
        right = sum(1 for v in given if v == truth.get(s["key"]))
        out.append({"text": s["text"], "answered": len(given), "right": right,
                    "percent": round(100 * right / len(given)) if given else None})
    return out


def _choice_bars(question: dict, solution: dict, answers) -> list[dict]:
    """En elección, cuántos marcaron cada opción, con la correcta señalada."""
    correct = set(solution.get("correct") or [])
    counts = Counter()
    for a in answers:
        for v in a.value if isinstance(a.value, list) else [a.value]:
            counts[v] += 1
    return [{"text": o["text"], "count": counts[o["key"]], "correct": o["key"] in correct}
            for o in question.get("options") or []]


def question_rows(activity, attempts) -> list[dict]:
    """Una fila por pregunta, en el orden de la actividad, sin las secciones deshabilitadas."""
    questions, solutions = _records(attempts)
    order = [q["key"] for q in activity.questions] + [k for k in questions if k not in
                                                       {q["key"] for q in activity.questions}]
    skip = activity.disabled
    totals = {a.pk: a.points or 0.0 for a in attempts}
    by_key: dict[str, list] = {}
    for attempt in attempts:
        for answer in attempt.answer_rows.all():
            by_key.setdefault(answer.key, []).append(answer)

    keys = [k for k in order if k in questions and questions[k]["item_id"] not in skip
            and not questions[k].get("survey")]
    rows = []
    for number, key in enumerate(keys, start=1):
        q = questions[key]
        answers = by_key.get(key, [])
        graded = [a for a in answers if not a.needs_review]
        points = [a.points for a in graded]
        full = float(q.get("points") or 0)
        spread = summary(points)
        answered = [a for a in answers if not blank(a.value)]
        wrong = Counter(_shown(q, a.value) for a in graded if not a.correct and not blank(a.value))
        rest = [totals[a.attempt_id] - a.points for a in graded]
        row = {
            "number": number, "key": key, "question": q, "points": full, "n": len(answers),
            # La primera pregunta de cada ejercicio abre su grupo en la tabla.
            "new_item": not rows or rows[-1]["question"]["item_id"] != q["item_id"],
            "pending": len(answers) - len(graded), "stats": spread,
            "percent": round(100 * spread["mean"] / full) if spread and full else None,
            "correct": sum(1 for a in graded if a.correct),
            "blank": len(answers) - len(answered),
            # Discriminación: correlación entre la pregunta y el resto de la nota. Negativa, quienes
            # la acertaron rindieron peor en lo demás: conviene revisar el enunciado o la solución.
            "discrimination": pearson(points, rest),
            "wrong": wrong.most_common(3) if q["type"] not in ("choice", "multi_choice", "true_false") else [],
            "choices": _choice_bars(q, solutions.get(key, {}), answered) if q["type"] in ("choice", "multi_choice") else [],
            "statements": _statements(q, solutions.get(key, {}), answered) if q["type"] == "true_false" else [],
        }
        row["flags"] = _question_flags(row)
        rows.append(row)
    return rows


def _question_flags(row: dict) -> list[tuple[str, str, str]]:
    """(texto, clase del chip, explicación) para lo que conviene mirar en una pregunta."""
    out = []
    if row["percent"] is not None and row["stats"]["n"] >= MIN_FOR_CORRELATION:
        if row["percent"] >= 90:
            out.append((_("muy fácil"), "", _("El promedio es de al menos el 90 % de sus puntos: casi no distingue entre estudiantes.")))
        elif row["percent"] <= 20:
            out.append((_("muy difícil"), "warn", _("El promedio no llega al 20 % de sus puntos. Conviene revisar si el tema se trabajó lo suficiente o si el enunciado se entendió.")))
    if row["discrimination"] is not None and row["discrimination"] < 0:
        out.append((_("discriminación negativa"), "warn", _("Quienes obtuvieron más puntos en esta pregunta obtuvieron, en promedio, menos en el resto. Conviene revisar el enunciado y la solución.")))
    return out


def histogram(fractions: list[float], bins: int = 10) -> list[dict]:
    """Cuántos estudiantes hay en cada tramo de la nota, en porcentaje del máximo."""
    counts = [0] * bins
    for f in fractions:
        counts[min(bins - 1, max(0, int(f * bins)))] += 1
    width = 100 // bins
    return [{"label": _("%(a)d–%(b)d %%") % {"a": i * width, "b": (i + 1) * width}, "count": c}
            for i, c in enumerate(counts)]


def signal_groups(attempts) -> list[dict]:
    out = []
    for low, high in BLUR_GROUPS:
        group = [a for a in attempts if (a.telemetry or {}).get("blurs", 0) >= low
                 and (high is None or (a.telemetry or {}).get("blurs", 0) <= high)]
        if high == 0:
            label = _("Nunca")
        elif high is None:
            label = _("Más de %(n)d veces") % {"n": low - 1}
        else:
            label = _("De %(a)d a %(b)d veces") % {"a": low, "b": high}
        fractions = [a.fraction for a in group if a.fraction is not None]
        out.append({"label": label, "count": len(group),
                    "mean": round(100 * statistics.fmean(fractions)) if fractions else None})
    return out


def report(activity, attempts, enrolled: int) -> dict:
    """Todo lo que muestra la página de estadísticas."""
    fractions = [a.fraction for a in attempts if a.fraction is not None]
    scores = [a.points or 0.0 for a in attempts]
    durations = [(a.submitted_at - a.started_at).total_seconds() for a in attempts
                 if a.submitted_at and a.started_at]
    blurs = [(a.telemetry or {}).get("blurs", 0) for a in attempts]
    # El tiempo fuera de la página se registra desde la versión 0.21: los intentos anteriores no lo tienen.
    away = [a for a in attempts if "away_s" in (a.telemetry or {})]
    time = summary(durations)
    return {
        "submitted": len(attempts), "enrolled": enrolled,
        "max_points": max((a.max_points for a in attempts), default=float(activity.points)),
        "score": summary(scores), "percent": summary([100 * f for f in fractions]),
        "histogram": histogram(fractions),
        "pending": sum(1 for a in attempts if a.pending),
        "duration": {k: duration_text(time[k]) for k in ("mean", "median", "min", "max")} if time else None,
        "auto": sum(1 for a in attempts if a.auto_submitted),
        "late": sum(1 for a in attempts if a.late),
        "questions": question_rows(activity, attempts),
        "signals": {
            "groups": signal_groups(attempts),
            "rho": spearman(blurs, [a.fraction or 0.0 for a in attempts]),
            "bound": chance_bound(len(attempts)),
            "pastes": sum((a.telemetry or {}).get("pastes", 0) for a in attempts),
            "blocked": sum((a.telemetry or {}).get("blocked_pastes", 0) for a in attempts),
            "away": len(away),
            "away_total": duration_text(statistics.median([a.telemetry["away_s"] for a in away])) if away else None,
            "away_longest": duration_text(statistics.median([a.telemetry.get("away_max_s", 0) for a in away])) if away else None,
        },
    }
