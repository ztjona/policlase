"""Los grupos de estudiantes en las actividades: a quién llega una actividad, qué partes recibe cada
grupo y cómo se reparten los ítems de una parte entre los integrantes de un grupo.

- **Destinatarios** (`Activity.groups`): sin grupos, todos los estudiantes. Quien no es destinatario
  no ve la actividad ni recibe un cero. Quien ya la había empezado (por ejemplo, antes de cambiar de
  grupo) la conserva y, si la entregó, su nota cuenta.
- **Partes de un grupo** (`Activity.item_groups`): esos ítems solo los reciben los integrantes de esos
  grupos. Una parte que se queda sin ítems para el estudiante desaparece entera, con su enunciado.
- **Reparto** (`Activity.deal`): cada estudiante recibe N ítems de la parte, distintos de los de sus
  compañeros de grupo mientras alcancen (después, los menos repetidos), y fijos desde que empieza: un
  nuevo intento, o la corrección de una evaluación, conserva los mismos.

Los archivos de contenido no nombran grupos: todo se configura en la plataforma, por `item_id`, y se
decide al empezar, en la copia del contenido que guarda el intento (`Attempt.content`).
"""

from __future__ import annotations

import random
from collections import Counter

from apps.courses.models import StudentGroup

from . import outline
from .models import Attempt


def member_groups(course_id, student_id) -> set[int]:
    """Los grupos del curso a los que pertenece el estudiante."""
    return set(StudentGroup.objects.filter(course_id=course_id, members=student_id).values_list("pk", flat=True))


def group_ids(activity) -> set[int]:
    """Los grupos destinatarios (vacío: todos los estudiantes). Usa la precarga de `groups`."""
    return {g.pk for g in activity.groups.all()}


def audience_ids(activity) -> set[int] | None:
    """Los estudiantes destinatarios, o None si son todos. Usa la precarga de `groups__members`."""
    groups = list(activity.groups.all())
    if not groups:
        return None
    return {m.pk for g in groups for m in g.members.all()}


def reaches(activity, student, mine: set[int] | None = None) -> bool:
    """¿Le llega la actividad a este estudiante? `mine`: sus grupos, si ya se conocen."""
    wanted = group_ids(activity)
    if not wanted:
        return True
    if mine is None:
        mine = member_groups(activity.course_id, student.pk)
    return bool(wanted & mine) or Attempt.objects.filter(activity=activity, student=student).exists()


def audience(activity, students) -> list:
    """De `students`, a quiénes llega la actividad: sus destinatarios y quienes ya la empezaron."""
    ids = audience_ids(activity)
    if ids is None:
        return list(students)
    started = set(Attempt.objects.filter(activity=activity).values_list("student_id", flat=True))
    return [s for s in students if s.pk in ids or s.pk in started]


def personalized(activity) -> bool:
    """¿Cambia el contenido según el estudiante (partes de un grupo o reparto)?"""
    return bool(activity.item_groups or activity.deal)


def part_rules(activity, content: dict) -> list[dict]:
    """Cada parte con ítems (como en `outline.parts`) con sus grupos y su reparto:
    [{"items", "groups", "deal"}]. Los valores se guardan por ítem; una parte toma los de sus ítems."""
    item_groups, deal = activity.item_groups or {}, activity.deal or {}
    out = []
    for part in outline.groups(content):
        if not part["items"]:
            continue
        groups = set()
        for item_id in part["items"]:
            groups.update(int(g) for g in item_groups.get(item_id) or [])
        out.append({"items": part["items"], "groups": groups,
                    "deal": max((int(deal.get(i) or 0) for i in part["items"]), default=0)})
    return out


def personal_content(activity, student, content: dict, previous: list[dict] | None = None,
                     deal: bool = True) -> dict:
    """La copia de `content` para este estudiante: sin los ítems de otros grupos y, en cada parte que
    se reparte, solo con los que le tocan. `activity` pone las reglas (en la corrección de una
    evaluación, la evaluación). `previous`: el contenido de sus intentos anteriores, cuyos ítems
    repartidos conserva. Sin `deal`, no reparte (lo que puede ver sin haber empezado)."""
    if not personalized(activity):
        return content
    mine = member_groups(activity.course_id, student.pk)
    wanted = group_ids(activity)
    rules = part_rules(activity, content)
    drop = {i for rule in rules if rule["groups"] and not rule["groups"] & mine for i in rule["items"]}
    dealt = [r for r in rules if deal and r["deal"] and len([i for i in r["items"] if i not in drop]) > r["deal"]]
    if dealt:
        had = {b.get("item_id") for c in previous or [] for b in c.get("items", []) if b.get("kind", "item") == "item"}
        # Con quiénes no repetir: los integrantes de los grupos del estudiante que cuentan en cada parte.
        relevant = {id(r): (mine & r["groups"]) if r["groups"] else (mine & wanted if wanted else mine) for r in dealt}
        everyone = set().union(*relevant.values())
        members: dict[int, set[int]] = {}
        for group_id, student_id in StudentGroup.members.through.objects.filter(
                studentgroup_id__in=everyone).values_list("studentgroup_id", "user_id"):
            members.setdefault(group_id, set()).add(student_id)
        peers_all = set().union(*members.values()) - {student.pk} if members else set()
        taken_by: dict[int, set[str]] = {}
        for student_id, other in Attempt.objects.filter(activity=activity, student_id__in=peers_all) \
                .values_list("student_id", "content"):
            taken_by.setdefault(student_id, set()).update(
                b.get("item_id") for b in (other or {}).get("items", []) if b.get("kind", "item") == "item")
        for number, rule in enumerate(dealt):
            visible = [i for i in rule["items"] if i not in drop]
            peers = set().union(*(members.get(g, set()) for g in relevant[id(rule)])) - {student.pk}
            taken = Counter(i for p in peers for i in taken_by.get(p, set()) if i in visible)
            chosen = _pick(visible, rule["deal"], had, taken, f"{activity.pk}:{student.pk}:{number}")
            drop |= set(visible) - chosen
    return outline.without_items(content, drop)


def _pick(visible: list[str], n: int, had: set, taken: Counter, seed: str) -> set[str]:
    """Los `n` ítems de una parte para un estudiante: los que ya tenía (si en su intento anterior se
    repartieron) y, para completar, los menos tomados por sus compañeros, al azar entre iguales."""
    kept = [i for i in visible if i in had]
    if len(kept) >= len(visible):                  # su intento anterior no repartía: se reparte ahora
        kept = []
    kept = kept[:n]
    pool = [i for i in visible if i not in kept]
    random.Random(seed).shuffle(pool)
    pool.sort(key=lambda i: taken[i])              # estable: el azar decide entre los igual de tomados
    return set(kept) | set(pool[:n - len(kept)])


def opening_groups(activity) -> list:
    """Los grupos para los que se abre una evaluación grupo por grupo: sus destinatarios; si no tiene,
    los grupos de sus secciones; si tampoco, todos los del curso. Con sus integrantes precargados."""
    groups = StudentGroup.objects.filter(course_id=activity.course_id).prefetch_related("members")
    wanted = group_ids(activity) or {int(g) for ids in (activity.item_groups or {}).values() for g in ids}
    return list(groups.filter(pk__in=wanted) if wanted else groups)


def visible_content(activity, student) -> dict:
    """Lo que el estudiante puede ver de la actividad sin un intento (por ejemplo, en la revisión de
    quien no entregó): sin las partes de otros grupos."""
    return personal_content(activity, student, activity.compiled, deal=False)
