from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from policlase_gen import activity as activityfmt
from policlase_gen import deck as deckfmt

from apps.accounts.models import User
from apps.activities import engine
from apps.activities.models import Activity
from apps.courses.models import Course, Enrollment
from apps.live.models import LiveSession, Participant, Response

from . import gradebook, matching
from .models import Category, ManualRecord, Period

ONE = """schema: policlase.activity/v1
title: '{title}'
items:
  - id: A-{title}
    questions:
      - { id: q1, type: numeric, solution: 1, grading: { atol: 0.01 } }
"""


def make_user(username, role=User.Role.STUDENT):
    return User.objects.create_user(username, f"{username}@example.ec", "clave-segura-2026", role=role)


class GradesBase(TestCase):
    def setUp(self):
        self.teacher = make_user("profe", User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="Métodos", code="MN-1")
        self.rules = gradebook.ensure_defaults(self.course)
        self.p1, self.p2 = list(Period.objects.filter(course=self.course))
        self.ana, self.bruno = make_user("ana"), make_user("bruno")
        for s in (self.ana, self.bruno):
            Enrollment.objects.create(course=self.course, student=s, status=Enrollment.Status.APPROVED)

    def row(self, period, name):
        return period.categories.get(name=name)

    def activity(self, title, category, due_in_days=-1):
        document, _ = activityfmt.load_activity_text(ONE.replace("{title}", title))
        return Activity.objects.create(course=self.course, title=title, source="", category=category,
                                       compiled=activityfmt.compile_activity(document), published=True,
                                       due_at=timezone.now() + timedelta(days=due_in_days))

    def deliver(self, activity, student, value="1"):
        # Se entrega antes de vencer: se adelanta el vencimiento solo para enviar.
        due = activity.due_at
        Activity.objects.filter(pk=activity.pk).update(due_at=timezone.now() + timedelta(days=1))
        activity.refresh_from_db()
        attempt = engine.start(activity, student)
        engine.save_draft(attempt, {f"A-{activity.title}.q1": value})
        engine.submit(attempt)
        Activity.objects.filter(pk=activity.pk).update(due_at=due)

    def grades_of(self, student):
        return next(g for g in gradebook.course_grades(self.course, [self.ana, self.bruno]) if g.student == student)


class DefaultsTests(GradesBase):
    def test_dos_parciales_con_la_tabla_por_omision(self):
        self.assertEqual(Period.objects.filter(course=self.course).count(), 2)
        rows = list(self.p1.categories.all())
        self.assertEqual([r.name for r in rows], ["Prueba", "Examen", "Deberes", "Proyecto",
                                                  "Participación en clase", "Actividades extracurriculares"])
        self.assertEqual(sum(r.weight for r in rows if not r.optional), 100)
        self.assertEqual(sum(r.weight for r in rows if r.optional), 10)
        self.assertEqual(self.row(self.p1, "Participación en clase").source, Category.Source.LIVE)

    def test_no_se_duplican(self):
        gradebook.ensure_defaults(self.course)
        self.assertEqual(Category.objects.filter(period__course=self.course).count(), 12)


class ComputeTests(GradesBase):
    def test_actividad_vencida_sin_entregar_vale_cero_y_la_futura_no_cuenta(self):
        deberes = self.row(self.p1, "Deberes")
        done = self.activity("d1", deberes)
        self.activity("d2", deberes)                     # vencida: Bruno y Ana sin entregar
        self.activity("d3", deberes, due_in_days=5)       # aún no vence: no cuenta
        self.deliver(done, self.ana)
        ana = self.grades_of(self.ana).periods[0]
        cell = next(c for c in ana.cells if c.category == deberes)
        self.assertEqual((cell.fraction, cell.counted), (0.5, 2))
        self.assertEqual(ana.total, round(0.5 * 5 / 100 * 20, 2))
        bruno = next(c for c in self.grades_of(self.bruno).periods[0].cells if c.category == deberes)
        self.assertEqual(bruno.fraction, 0)

    def test_participacion_faltar_vale_cero(self):
        document, _ = deckfmt.load_deck_text("""schema: policlase.deck/v1
title: 'Clase'
slides:
  - item:
      id: L1
      questions:
        - { id: q1, type: numeric, solution: 1, grading: { atol: 0.1 } }
""")
        slides = deckfmt.compile_deck(document)["slides"]
        session = LiveSession.objects.create(course=self.course, title="Clase", slides=slides, opened=[0],
                                             status=LiveSession.Status.ENDED, started_at=timezone.now())
        Participant.objects.create(session=session, student=self.ana)
        Response.objects.create(session=session, slide_index=0, student=self.ana, item_id="L1", item_version="x",
                                answer="1", correct=True, fraction=1, points=1, elapsed_ms=10)
        live = self.row(self.p1, "Participación en clase")
        ana = next(c for c in self.grades_of(self.ana).periods[0].cells if c.category == live)
        bruno = next(c for c in self.grades_of(self.bruno).periods[0].cells if c.category == live)
        self.assertEqual((ana.fraction, bruno.fraction), (1.0, 0.0))

    def test_el_parcial_se_topa_en_veinte(self):
        # Todo perfecto en filas que suman 110 %: el parcial queda en 20, no en 22.
        for name in ("Prueba", "Examen", "Deberes", "Proyecto", "Actividades extracurriculares"):
            self.deliver(self.activity(name, self.row(self.p1, name)), self.ana)
        Category.objects.filter(pk=self.row(self.p1, "Participación en clase").pk).update(source=Category.Source.WORK)
        self.deliver(self.activity("Part", self.row(self.p1, "Participación en clase")), self.ana)
        period = self.grades_of(self.ana).periods[0]
        self.assertEqual((period.raw, period.total, period.capped), (22.0, 20.0, True))

    def test_los_extras_compensan_lo_que_falta(self):
        for name in ("Prueba", "Examen", "Proyecto", "Actividades extracurriculares"):
            self.deliver(self.activity(name, self.row(self.p1, name)), self.ana)
        self.activity("Deberes", self.row(self.p1, "Deberes"))      # vencido, sin entregar
        self.assertEqual(self.grades_of(self.ana).periods[0].total, 20.0)

    def test_estado_y_supletorio_al_cerrar_los_parciales(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        Period.objects.filter(course=self.course).update(ends_on=yesterday)
        g = self.grades_of(self.ana)
        g.periods[0].cells[0].fraction = 1.0               # Prueba del parcial 1: 7 puntos
        g.periods[1].cells[0].fraction = 1.0               # Prueba del parcial 2: 7 puntos
        g.periods[0].cells[1].fraction = 1.0               # Examen del parcial 1: 7 puntos
        self.assertEqual(g.total, 21.0)
        self.assertEqual(g.status, "makeup")
        self.assertEqual(g.makeup_needed, 27.0)            # 48 − 21; nunca menos de 24
        g.periods[1].cells[1].fraction = 1.0
        self.assertEqual((g.total, g.makeup_needed), (28.0, 24.0))
        self.assertEqual(g.status, "approved")
        g.periods[0].cells[1].fraction = 0
        g.periods[1].cells[1].fraction = 0
        g.periods[1].cells[0].fraction = 0.5
        self.assertEqual(g.status, "failed")               # 10.5 < 18

    def test_sin_estado_mientras_el_semestre_sigue(self):
        self.assertIsNone(self.grades_of(self.ana).status)


class SettingsTests(GradesBase):
    def form_data(self, **overrides):
        data = {"rules-period_max": "20", "rules-fail_below": "18", "rules-pass_from": "28",
                "rules-makeup_max": "40", "rules-makeup_total": "48", "rules-makeup_min": "24"}
        for period in (self.p1, self.p2):
            rows = list(period.categories.all())
            data.update({f"p{period.pk}-name": period.name, f"p{period.pk}-starts_on": "", f"p{period.pk}-ends_on": "",
                         f"c{period.pk}-TOTAL_FORMS": str(len(rows) + 1), f"c{period.pk}-INITIAL_FORMS": str(len(rows)),
                         f"c{period.pk}-MIN_NUM_FORMS": "0", f"c{period.pk}-MAX_NUM_FORMS": "1000"})
            for i, row in enumerate(rows):
                data.update({f"c{period.pk}-{i}-id": str(row.pk), f"c{period.pk}-{i}-name": row.name,
                             f"c{period.pk}-{i}-weight": str(row.weight), f"c{period.pk}-{i}-source": row.source})
                if row.optional:
                    data[f"c{period.pk}-{i}-optional"] = "on"
            # La fila vacía para agregar: el navegador envía el valor por omisión del selector.
            data[f"c{period.pk}-{len(rows)}-source"] = "work"
        data.update(overrides)
        return data

    def test_editar_agregar_y_quitar_filas(self):
        self.client.force_login(self.teacher)
        p = self.p1.pk
        data = self.form_data(**{f"c{p}-0-weight": "30", f"c{p}-6-name": "Lecturas", f"c{p}-6-weight": "5",
                                 f"c{p}-6-source": "work", f"c{p}-6-optional": "on", f"c{p}-2-DELETE": "on"})
        response = self.client.post(reverse("grading_settings", args=[self.course.pk]), data, follow=True)
        names = list(self.p1.categories.values_list("name", flat=True))
        self.assertNotIn("Deberes", names)
        self.assertEqual(names[-1], "Lecturas")
        self.assertEqual(self.row(self.p1, "Prueba").weight, Decimal("30"))
        self.assertContains(response, "suman 90")          # aviso: las obligatorias ya no suman 100

    def test_agregar_y_eliminar_parcial(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("period_add", args=[self.course.pk]))
        third = Period.objects.filter(course=self.course).last()
        self.assertEqual((third.name, third.categories.count()), ("Parcial 3", 6))
        self.client.post(reverse("period_delete", args=[self.course.pk, third.pk]))
        self.assertEqual(Period.objects.filter(course=self.course).count(), 2)

    def test_estudiante_ve_la_ponderacion(self):
        self.client.force_login(self.ana)
        page = self.client.get(reverse("my_grades", args=[self.course.pk]))
        self.assertContains(page, "Participación en clase")
        self.assertContains(page, "opcional")
        self.client.force_login(make_user("zoe"))
        self.assertEqual(self.client.get(reverse("my_grades", args=[self.course.pk])).status_code, 404)

    def test_libro_de_calificaciones(self):
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("course_gradebook", args=[self.course.pk]))
        self.assertContains(page, "ana")
        self.assertContains(page, "Parcial 2")


class GradebookViewTests(GradesBase):
    def test_resumen_y_detalle_por_parcial(self):
        self.deliver(self.activity("d1", self.row(self.p1, "Deberes")), self.ana)
        self.client.force_login(self.teacher)
        summary = self.client.get(reverse("course_gradebook", args=[self.course.pk]))
        self.assertContains(summary, "Parcial 2")
        detail = self.client.get(reverse("course_gradebook", args=[self.course.pk]) + f"?parcial={self.p1.pk}")
        self.assertContains(detail, "Deberes")
        self.assertContains(detail, "(100 %)")


class MatchingTests(TestCase):
    """Los nombres pegados de otra plataforma se emparejan con los inscritos por apellido y nombre."""

    def setUp(self):
        def student(username, last, first):
            user = make_user(username)
            user.last_name, user.first_name = last, first
            user.save()
            return user
        self.students = [
            student("castro", "Castro Paredes", "Leandro David"),
            student("ogonaga", "Ogonaga", "Jair"),
            student("ona", "Oña", "Sebastian"),
            student("pineda", "Pineda Oña", "Danny Jofre"),
            student("avila", "Avila", "Cesar"),
            student("morillo", "Morillo Tenorio", "Matías Alejandro"),
        ]

    def test_lineas_con_y_sin_posicion(self):
        rows, bad = matching.parse_lines("1. Castro P Leandro\t20117\nRobalino.T.Valeria 20277\n\nsin número\n"
                                         "Pineda O. Danny J.;8,5")
        self.assertEqual(rows, [("Castro P Leandro", Decimal("20117")), ("Robalino.T.Valeria", Decimal("20277")),
                                ("Pineda O. Danny J.", Decimal("8.5"))])
        self.assertEqual(bad, ["sin número"])

    def test_emparejar(self):
        rows, _ = matching.parse_lines(
            "Castro P Leandro 20117\nOgonaga M Jair 8220\nOgonaga M Jairr 1499\nOgonaga M Edison 611\n"
            "Ona A. Sebastian G 7336\nPineda O. Danny J. 14992\nAvila V Ismael 14614\nReyes O Sebastián 12654\n"
            "Morillo T. Matías 15700")
        found = {m.name: m for m in matching.match_all(rows, self.students)}
        by = lambda name: found[name].student.username if found[name].student else None
        self.assertEqual(by("Castro P Leandro"), "castro")
        self.assertEqual(by("Ogonaga M Jair"), "ogonaga")
        self.assertIsNone(by("Ogonaga M Jairr"))                     # el mismo estudiante dos veces
        self.assertEqual(found["Ogonaga M Jairr"].also, "Ogonaga M Jair")
        self.assertIsNone(by("Ogonaga M Edison"))
        self.assertEqual(by("Ona A. Sebastian G"), "ona")              # sin tilde
        self.assertEqual(found["Ona A. Sebastian G"].strength, matching.STRONG)
        self.assertEqual(by("Pineda O. Danny J."), "pineda")
        self.assertEqual((by("Avila V Ismael"), found["Avila V Ismael"].strength), ("avila", matching.WEAK))
        self.assertIsNone(by("Reyes O Sebastián"))                     # sin apellido en común: no se adivina
        self.assertEqual(by("Morillo T. Matías"), "morillo")


class ManualRecordTests(GradesBase):
    def setUp(self):
        super().setUp()
        self.ana.last_name, self.ana.first_name = "Andrade", "Ana"
        self.ana.save()
        self.bruno.last_name, self.bruno.first_name = "Bravo", "Bruno"
        self.bruno.save()
        self.client.force_login(self.teacher)
        self.url = reverse("manual_record_new", args=[self.course.pk])
        self.row_live = self.row(self.p1, "Participación en clase")

    def data(self, **extra):
        return {"title": "Quiz 1 (Fuiz)", "category": self.row_live.pk, "on": str(timezone.localdate()),
                "max_points": "20000", "note": "", **extra}

    def test_pegar_rellena_sin_guardar_y_guardar_cuenta_en_la_fila(self):
        page = self.client.post(self.url, self.data(action="paste", pasted="Andrade A 15000\nZapata Z 9000\n"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'value="15000"')
        self.assertContains(page, "Zapata Z")
        self.assertFalse(ManualRecord.objects.exists())                  # pegar no guarda

        response = self.client.post(self.url, self.data(**{
            "action": "save", f"s-{self.ana.pk}": "15000", f"s-{self.bruno.pk}": "",
            "unmatched-count": "1", "u-0-name": "Zapata Z", "u-0-points": "9000", "u-0": ""}))
        record = ManualRecord.objects.get()
        self.assertRedirects(response, reverse("manual_record", args=[self.course.pk, record.pk]))
        self.assertEqual(record.unmatched, [{"name": "Zapata Z", "points": "9000", "also": ""}])
        live = lambda g: next(c for c in g.periods[0].cells if c.category == self.row_live)
        self.assertAlmostEqual(live(self.grades_of(self.ana)).fraction, 0.75)
        self.assertEqual(live(self.grades_of(self.bruno)).fraction, 0.0)  # sin nota: cero

        # Bruno era «Zapata Z» en la otra plataforma: se le asigna lo que quedó pendiente.
        url = reverse("manual_record", args=[self.course.pk, record.pk])
        self.client.post(url, self.data(**{"action": "save", f"s-{self.ana.pk}": "15000",
                                           "unmatched-count": "1", "u-0-name": "Zapata Z", "u-0-points": "9000",
                                           "u-0": str(self.bruno.pk)}))
        record.refresh_from_db()
        self.assertEqual(record.unmatched, [])
        self.assertAlmostEqual(live(self.grades_of(self.bruno)).fraction, 0.45)
        self.assertContains(self.client.get(reverse("course_activities", args=[self.course.pk])), "Quiz 1 (Fuiz)")

    def test_quitar_una_asignacion_y_confirmar_la_dudosa(self):
        # «Bravo Z» coincide con Bravo, Bruno solo por el apellido: queda por confirmar.
        page = self.client.post(self.url, self.data(action="paste", pasted="Andrade A 15000\nBravo Z 9000\n"))
        self.assertContains(page, f'name="p-{self.bruno.pk}-weak"')
        self.assertContains(page, f'name="c-{self.bruno.pk}"')
        self.assertContains(page, f'value="quitar-{self.ana.pk}"')
        pasted = {"action": "save", f"s-{self.ana.pk}": "15000", f"p-{self.ana.pk}": "Andrade A",
                  f"s-{self.bruno.pk}": "9000", f"p-{self.bruno.pk}": "Bravo Z", f"p-{self.bruno.pk}-weak": "1"}

        # «Quitar» devuelve la línea a «Sin asignar» y vacía la casilla; nada se guarda todavía.
        page = self.client.post(self.url, self.data(**{**pasted, "action": f"quitar-{self.ana.pk}"}))
        self.assertContains(page, 'name="u-0-name" value="Andrade A"')
        self.assertContains(page, f'name="s-{self.ana.pk}" value=""')
        self.assertFalse(ManualRecord.objects.exists())

        # Sin confirmar, la dudosa no se guarda: pasa a «Sin asignar».
        self.client.post(self.url, self.data(**pasted))
        record = ManualRecord.objects.get()
        self.assertEqual({m.student_id: m.pasted_name for m in record.scores.all()}, {self.ana.pk: "Andrade A"})
        self.assertEqual(record.unmatched, [{"name": "Bravo Z", "points": "9000", "also": ""}])

        # Confirmada, sí; y el nombre de la lista queda junto a la nota para poder quitarla después.
        url = reverse("manual_record", args=[self.course.pk, record.pk])
        self.client.post(url, self.data(**{**pasted, f"c-{self.bruno.pk}": "on"}))
        self.assertEqual({m.student_id: m.pasted_name for m in record.scores.all()},
                         {self.ana.pk: "Andrade A", self.bruno.pk: "Bravo Z"})
        self.assertContains(self.client.get(url), f'value="quitar-{self.bruno.pk}"')

    def test_el_registro_va_en_su_unidad(self):
        from apps.live.models import Section

        unit = Section.objects.create(course=self.course, title="Unidad 01")
        other = Section.objects.create(course=self.course, title="Unidad 02")
        self.assertContains(self.client.get(self.url + f"?seccion={unit.pk}"), f'<option value="{unit.pk}" selected>')
        self.client.post(self.url, self.data(action="save", section=unit.pk, category=""))
        record = ManualRecord.objects.get()
        self.assertEqual(record.section, unit)
        classes = self.client.get(reverse("course_manage", args=[self.course.pk]))
        self.assertContains(classes, reverse("manual_record_move", args=[self.course.pk, record.pk]))
        # Sin ponderación también se ve en Actividades, en su grupo.
        self.assertContains(self.client.get(reverse("course_activities", args=[self.course.pk])), "Quiz 1 (Fuiz)")
        # Una unidad con registros no se elimina; se arrastra a otra.
        self.client.post(reverse("section_delete", args=[self.course.pk, unit.pk]))
        self.assertTrue(Section.objects.filter(pk=unit.pk).exists())
        self.client.post(reverse("manual_record_move", args=[self.course.pk, record.pk]), {"section": other.pk})
        record.refresh_from_db()
        self.assertEqual(record.section, other)

    def test_solo_el_parcial_en_curso_viene_desplegado(self):
        page = self.client.get(reverse("course_activities", args=[self.course.pk])).content.decode()
        self.assertIn(f'id="parcial-{self.p1.pk}" open', page)        # sin fechas: el primero
        self.assertIn(f'id="parcial-{self.p2.pk}">', page)

    def test_notas_fuera_de_rango_no_se_guardan(self):
        page = self.client.post(self.url, self.data(**{"action": "save", f"s-{self.ana.pk}": "25000"}))
        self.assertContains(page, "fuera de rango")
        self.assertFalse(ManualRecord.objects.exists())

    def test_otro_docente_no(self):
        record = ManualRecord.objects.create(course=self.course, title="Q", on=timezone.localdate())
        self.client.force_login(make_user("otra", User.Role.TEACHER))
        for name, args in [("manual_record", [self.course.pk, record.pk]), ("manual_record_new", [self.course.pk]),
                           ("manual_record_delete", [self.course.pk, record.pk])]:
            self.assertEqual(self.client.post(reverse(name, args=args), self.data(action="save")).status_code, 404)
        self.assertTrue(ManualRecord.objects.filter(pk=record.pk).exists())


class RebindTests(GradesBase):
    """El parcial lo decide la fecha: una unidad puede repartirse entre dos parciales."""

    def test_la_actividad_sigue_a_su_fecha(self):
        today = timezone.localdate()
        Period.objects.filter(pk=self.p1.pk).update(ends_on=today + timedelta(days=10))
        homework = self.activity("d1", self.row(self.p1, "Deberes"), due_in_days=20)
        self.assertTrue(gradebook.rebind(homework))
        self.assertEqual(homework.category, self.row(self.p2, "Deberes"))
        homework.save()
        self.row(self.p2, "Deberes").delete()                      # sin fila igual en el otro parcial
        homework = self.activity("d2", self.row(self.p1, "Deberes"), due_in_days=20)
        self.assertFalse(gradebook.rebind(homework))
        self.assertEqual(gradebook.mismatch(homework, list(Period.objects.filter(course=self.course))), self.p2)

    def test_sin_fechas_de_cierre_no_se_mueve_nada(self):
        homework = self.activity("d1", self.row(self.p2, "Deberes"), due_in_days=20)
        self.assertFalse(gradebook.rebind(homework))
        self.assertIsNone(gradebook.mismatch(homework, list(Period.objects.filter(course=self.course))))
