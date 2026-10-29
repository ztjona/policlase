import io
import shutil
import tempfile
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from policlase_gen import activity as activityfmt

from apps.accounts.models import User
from apps.courses.models import Course, Enrollment
from apps.grades import gradebook
from apps.grades.models import Milestone
from apps.live.models import Section

from . import debate, engine, stats
from .models import Activity, Answer, Attempt, DebateEntry, DebateReply, Upload

SOURCE = r"""schema: policlase.activity/v1
title: 'Deber 1'
instructions: 'Resuelva.'
items:
  - id: D1-a
    questions:
      - id: q1
        type: numeric
        points: 2
        prompt: 'Raíz de 2'
        solution: 1.4142
        grading: { atol: 0.001 }
      - id: q2
        type: choice
        prompt: '¿Orden de Newton?'
        options:
          - { text: 'Cuadrático', correct: true }
          - { text: 'Lineal' }
      - id: q3
        type: open
        points: 2
        prompt: 'Explique'
        grading:
          mode: ai
          reference: 'Tangente horizontal'
          rubric:
            - { criterion: 'División entre cero', points: 1 }
            - { criterion: 'Geometría', points: 1 }
  - id: D1-foto
    questions:
      - { id: q1, type: image, prompt: 'Foto del desarrollo', max_files: 2 }
"""

MEDIA = tempfile.mkdtemp(prefix="policlase-test-media-")


def make_user(username, role=User.Role.STUDENT):
    return User.objects.create_user(username, f"{username}@example.ec", "clave-segura-2026", role=role)


def png_bytes(size=(40, 30), color=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def jpeg_with_gps() -> bytes:
    image = Image.new("RGB", (50, 50), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "Telefono"                       # Make
    exif[0x8825] = {1: "S", 2: (0.0, 13.0, 0.0)}    # GPSInfo
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


@override_settings(MEDIA_ROOT=MEDIA)
class ActivityBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.teacher = make_user("profe", User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="Métodos", code="MN-1")
        gradebook.ensure_defaults(self.course)
        self.deberes = self.course.periods.first().categories.get(name="Deberes")
        document, _ = activityfmt.load_activity_text(SOURCE)
        self.activity = Activity.objects.create(
            course=self.course, title="Deber 1", source=SOURCE, compiled=activityfmt.compile_activity(document),
            category=self.deberes, published=True, due_at=timezone.now() + timedelta(days=2))
        self.ana, self.bruno, self.outsider = make_user("ana"), make_user("bruno"), make_user("zoe")
        for s in (self.ana, self.bruno):
            Enrollment.objects.create(course=self.course, student=s, status=Enrollment.Status.APPROVED)
        self.right_choice = self.activity.compiled["items"][0]["solutions"]["q2"]["correct"][0]

    def answer_all(self, attempt, value="1.4142", choice=None):
        engine.save_draft(attempt, {"D1-a.q1": value, "D1-a.q2": choice or self.right_choice,
                                    "D1-a.q3": "Porque la derivada es cero"})


class EditorTests(ActivityBase):
    def test_crear_actividad_desde_el_editor(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("activity_new", args=[self.course.pk]), {"source": SOURCE})
        created = Activity.objects.exclude(pk=self.activity.pk).get()
        self.assertRedirects(response, reverse("activity_detail", args=[self.course.pk, created.pk]))
        self.assertFalse(created.published)              # nace como borrador
        self.assertEqual(created.points, 6)

    def test_errores_no_se_guardan(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("activity_new", args=[self.course.pk]),
                                    {"source": SOURCE.replace("title: 'Deber 1'\n", "")})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "E003")
        self.assertEqual(Activity.objects.count(), 1)

    def test_vista_previa_muestra_rubrica_y_referencia_al_docente(self):
        self.client.force_login(self.teacher)
        data = self.client.post(reverse("content_preview", args=[self.course.pk]), {"source": SOURCE}).json()
        self.assertIn("División entre cero", data["html"])
        self.assertIn("Tangente horizontal", data["html"])

    def test_configurar_fecha_relativa_a_una_fecha_clave(self):
        prueba = Milestone.objects.create(course=self.course, name="Prueba 1", on=date(2026, 11, 12))
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_detail", args=[self.course.pk, self.activity.pk]), {
            "category": self.deberes.pk, "published": "on", "due_mode": "milestone",
            "due_milestone": prueba.pk, "due_offset_days": "-1", "due_time": "23:59",
            "late_policy": "penalty", "late_penalty": "30", "max_attempts": "3"})
        self.activity.refresh_from_db()
        local = timezone.localtime(self.activity.due_at, timezone.get_default_timezone())
        self.assertEqual((local.date(), local.time()), (date(2026, 11, 11), time(23, 59)))


class IsolationTests(ActivityBase):
    def test_otro_docente_recibe_404_y_nada_cambia(self):
        other = make_user("otra", User.Role.TEACHER)
        self.client.force_login(other)
        for name, args in [("activity_detail", [self.course.pk, self.activity.pk]),
                           ("activity_edit", [self.course.pk, self.activity.pk]),
                           ("activity_dates", [self.course.pk]),
                           ("question_review", [self.course.pk, self.activity.pk, "D1-a.q3"]),
                           ("activity_stats", [self.course.pk, self.activity.pk]),
                           ("course_gradebook", [self.course.pk]),
                           ("grading_settings", [self.course.pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404, name)
        self.client.post(reverse("activity_delete", args=[self.course.pk, self.activity.pk]))
        self.client.post(reverse("activity_detail", args=[self.course.pk, self.activity.pk]),
                         {"due_mode": "none", "late_policy": "block", "late_penalty": "0", "max_attempts": "1"})
        self.activity.refresh_from_db()
        self.assertEqual((self.activity.late_policy, self.activity.max_attempts), ("penalty", 3))

    def test_estudiante_no_ve_borradores_ni_cursos_ajenos(self):
        self.client.force_login(self.outsider)
        url = reverse("activity_student", args=[self.course.pk, self.activity.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(url).status_code, 200)
        Activity.objects.filter(pk=self.activity.pk).update(published=False)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_estudiante_no_entra_a_paginas_del_docente(self):
        self.client.force_login(self.ana)
        response = self.client.get(reverse("activity_detail", args=[self.course.pk, self.activity.pk]))
        self.assertIn(response.status_code, (403, 404))


class AttemptTests(ActivityBase):
    def test_flujo_completo_por_la_web(self):
        self.client.force_login(self.ana)
        self.client.post(reverse("attempt_start", args=[self.course.pk, self.activity.pk]))
        attempt = Attempt.objects.get()
        url = reverse("attempt_form", args=[self.course.pk, self.activity.pk])
        page = self.client.get(url)
        self.assertContains(page, "ana")                  # marca de agua con su nombre
        self.assertNotContains(page, "Tangente horizontal")  # la referencia nunca llega al estudiante
        data = {"form": "attempt", "a-D1-a.q1": "1,4142", "a-D1-a.q2": self.right_choice,
                "a-D1-a.q3": "La derivada es cero", "telemetry": '{"pastes": 2, "pasted_chars": 40}'}
        saved = self.client.post(url, data, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(saved.status_code, 200)
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers["D1-a.q1"], "1,4142")
        self.assertEqual(attempt.telemetry["pastes"], 2)
        self.client.post(url, {**data, "action": "submit", "telemetry": "{}"})
        attempt.refresh_from_db()
        self.assertIsNotNone(attempt.submitted_at)
        self.assertEqual(attempt.points, 3)               # 2 + 1; la abierta espera al docente
        self.assertEqual(attempt.pending, 1)
        self.assertEqual(Answer.objects.filter(attempt=attempt).count(), 4)

    def test_respuesta_ilegible_no_se_envia(self):
        attempt = engine.start(self.activity, self.ana)
        self.answer_all(attempt, value="uno coma cuatro")
        with self.assertRaises(engine.ActionError):
            engine.submit(attempt)
        self.assertIn("D1-a.q1", engine.format_errors(attempt))
        attempt.refresh_from_db()
        self.assertIsNone(attempt.submitted_at)

    def test_en_blanco_vale_cero_sin_error(self):
        attempt = engine.submit(engine.start(self.activity, self.ana))
        self.assertEqual((attempt.points, attempt.pending), (0, 0))

    def test_cuenta_el_ultimo_intento_y_respeta_el_maximo(self):
        Activity.objects.filter(pk=self.activity.pk).update(max_attempts=2)
        self.activity.refresh_from_db()
        first = engine.start(self.activity, self.ana)
        self.answer_all(first)
        engine.submit(first)
        second = engine.start(self.activity, self.ana)
        self.answer_all(second, value="9")
        engine.submit(second)
        self.assertEqual(engine.counted_attempts(self.activity)[self.ana.pk].pk, second.pk)
        with self.assertRaises(engine.ActionError):
            engine.start(self.activity, self.ana)

    def test_un_solo_borrador_a_la_vez(self):
        self.assertEqual(engine.start(self.activity, self.ana).pk, engine.start(self.activity, self.ana).pk)

    def test_atrasada_con_penalizacion_solo_si_no_entrego_a_tiempo(self):
        on_time = engine.start(self.activity, self.ana)
        self.answer_all(on_time)
        engine.submit(on_time)
        Activity.objects.filter(pk=self.activity.pk).update(due_at=timezone.now() - timedelta(hours=1))
        self.activity.refresh_from_db()
        # Ana ya entregó: no puede reenviar con las soluciones a la vista.
        self.assertFalse(engine.state(self.activity, self.ana).can_start)
        self.assertTrue(engine.state(self.activity, self.ana).reveal)
        late = engine.start(self.activity, self.bruno)
        self.answer_all(late)
        late = engine.submit(late)
        self.assertTrue(late.late)
        self.assertEqual(late.penalty, 30)
        self.assertAlmostEqual(late.points, 3 * 0.7)

    def test_atrasos_bloqueados(self):
        Activity.objects.filter(pk=self.activity.pk).update(due_at=timezone.now() - timedelta(hours=1),
                                                            late_policy=Activity.Late.BLOCK)
        self.activity.refresh_from_db()
        with self.assertRaises(engine.ActionError):
            engine.start(self.activity, self.ana)

    def test_atrasos_hasta_el_cierre_del_parcial(self):
        period = self.deberes.period
        period.ends_on = timezone.localdate() - timedelta(days=1)
        period.save()
        Activity.objects.filter(pk=self.activity.pk).update(due_at=timezone.now() - timedelta(days=3))
        self.activity.refresh_from_db()
        state = engine.state(self.activity, self.ana)
        self.assertFalse(state.can_start)
        self.assertIn("atrasadas", state.reason)

    def test_borrador_vencido_con_entrega_previa_no_se_envia(self):
        first = engine.start(self.activity, self.ana)
        engine.submit(first)
        draft = engine.start(self.activity, self.ana)
        Activity.objects.filter(pk=self.activity.pk).update(due_at=timezone.now() - timedelta(minutes=1))
        draft.activity.refresh_from_db()
        with self.assertRaises(engine.ActionError):
            engine.submit(draft)

    def test_soluciones_solo_despues_del_vencimiento(self):
        attempt = engine.start(self.activity, self.ana)
        self.answer_all(attempt, value="3")
        engine.submit(attempt)
        self.client.force_login(self.ana)
        url = reverse("activity_student", args=[self.course.pk, self.activity.pk])
        self.assertNotContains(self.client.get(url), "1.4142")
        Activity.objects.filter(pk=self.activity.pk).update(due_at=timezone.now() - timedelta(minutes=1))
        self.assertContains(self.client.get(url), "1.4142")

    def test_la_copia_del_intento_no_cambia_si_se_edita_la_actividad(self):
        attempt = engine.start(self.activity, self.ana)
        changed = SOURCE.replace("solution: 1.4142", "solution: 2")
        document, _ = activityfmt.load_activity_text(changed)
        self.activity.compiled = activityfmt.compile_activity(document)
        self.activity.save()
        self.answer_all(attempt)
        self.assertEqual(engine.submit(attempt).answer_rows.get(key="D1-a.q1").points, 2)


class GradingTests(ActivityBase):
    def setUp(self):
        super().setUp()
        attempt = engine.start(self.activity, self.ana)
        self.answer_all(attempt, value="7")
        self.attempt = engine.submit(attempt)

    def test_nota_del_docente_en_la_abierta(self):
        answer = self.attempt.answer_rows.get(key="D1-a.q3")
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("activity_answer_grade", args=[self.course.pk, self.activity.pk, answer.pk]),
            {"points": "1,5", "comment": "Falta la geometría"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.json()["attempt_points"], 2.5)
        answer.refresh_from_db()
        self.assertEqual((answer.points, answer.needs_review, answer.comment), (1.5, False, "Falta la geometría"))

    def test_corregir_y_volver_a_la_automatica(self):
        answer = self.attempt.answer_rows.get(key="D1-a.q1")
        engine.set_grade(answer, 2, self.teacher)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.points, 3)
        engine.set_grade(answer, None, self.teacher)
        answer.refresh_from_db()
        self.assertEqual((answer.points, answer.auto_points), (0, 0))

    def test_nota_no_supera_el_valor_de_la_pregunta(self):
        answer = self.attempt.answer_rows.get(key="D1-a.q3")
        engine.set_grade(answer, 50, self.teacher)
        answer.refresh_from_db()
        self.assertEqual(answer.points, 2)

    def test_revisar_por_pregunta_lista_las_respuestas(self):
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("question_review", args=[self.course.pk, self.activity.pk, "D1-a.q3"]))
        self.assertContains(page, "Porque la derivada es cero")
        self.assertContains(page, "Tangente horizontal")

    def test_senales_en_la_tabla_de_entregas(self):
        Attempt.objects.filter(pk=self.attempt.pk).update(telemetry={"pastes": 3, "pasted_chars": 900, "blurs": 5})
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("activity_detail", args=[self.course.pk, self.activity.pk]))
        self.assertContains(page, "900 caracteres")
        self.assertContains(page, "salió de la página 5 veces")

    def test_telemetria_ignora_valores_absurdos(self):
        merged = engine.merge_telemetry({"pastes": 1}, {"pastes": "2", "blurs": -4, "otra": 9, "pasted_chars": "x"})
        self.assertEqual(merged, {"pastes": 3})


class StatsTests(ActivityBase):
    def setUp(self):
        super().setUp()
        wrong = next(o["key"] for o in self.activity.compiled["items"][0]["questions"][1]["options"]
                     if o["key"] != self.right_choice)
        for student, value, choice, telemetry in ((self.ana, "1.4142", self.right_choice, {"blurs": 3, "away_s": 40, "away_max_s": 25}),
                                                  (self.bruno, "7", wrong, {})):
            attempt = engine.start(self.activity, student)
            self.answer_all(attempt, value=value, choice=choice)
            engine.submit(attempt)
            Attempt.objects.filter(pk=attempt.pk).update(telemetry=telemetry)
        self.url = reverse("activity_stats", args=[self.course.pk, self.activity.pk])

    def test_pagina_con_nota_preguntas_y_senales(self):
        self.client.force_login(self.teacher)
        page = self.client.get(self.url)
        self.assertContains(page, "2 de 2 inscritos")
        self.assertContains(page, "Desviación estándar")
        self.assertContains(page, "Respuestas incorrectas más frecuentes")
        self.assertContains(page, '<span class="mono">7</span>', html=True)
        self.assertContains(page, "De 1 a 5 veces")
        self.assertContains(page, "por revisar")                     # la abierta aún sin nota
        detail = self.client.get(reverse("activity_detail", args=[self.course.pk, self.activity.pk]))
        self.assertContains(detail, self.url)
        self.assertContains(detail, "estuvo fuera 40 s en total; la salida más larga duró 25 s")

    def test_sin_entregas(self):
        Attempt.objects.all().delete()
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(self.url), "Las estadísticas aparecerán cuando haya entregas.")

    def test_cifras_por_pregunta(self):
        attempts = list(Attempt.objects.filter(activity=self.activity))
        for a in attempts:
            a.activity = self.activity
        rows = {r["key"]: r for r in stats.report(self.activity, attempts, 2)["questions"]}
        numeric = rows["D1-a.q1"]
        self.assertEqual((numeric["correct"], numeric["n"], numeric["blank"], numeric["percent"]), (1, 2, 0, 50))
        self.assertAlmostEqual(numeric["stats"]["sd"], 2 ** 0.5)       # puntos 2 y 0
        self.assertEqual(numeric["wrong"], [("7", 1)])
        self.assertEqual([b["count"] for b in rows["D1-a.q2"]["choices"]], [1, 1])
        self.assertEqual(rows["D1-a.q3"]["pending"], 2)                 # la abierta no entra en el promedio
        self.assertIsNone(rows["D1-a.q3"]["stats"])

    def test_estadisticos(self):
        self.assertEqual(stats.ranks([10, 20, 20, 5]), [1, 2.5, 2.5, 0])
        self.assertAlmostEqual(stats.spearman([1, 2, 3, 4, 5], [2, 4, 9, 16, 100]), 1.0)
        self.assertIsNone(stats.spearman([1, 2, 3], [3, 2, 1]))         # muy pocos datos
        self.assertIsNone(stats.pearson([1, 1, 1, 1, 1], [1, 2, 3, 4, 5]))
        self.assertAlmostEqual(stats.chance_bound(23), 0.418, places=3)
        self.assertEqual([stats.duration_text(s) for s in (45, 200, 1500, 6900, 200_000)],
                         ["45 s", "3 min 20 s", "25 min", "1 h 55 min", "2 d 7 h"])
        self.assertEqual([b["count"] for b in stats.histogram([0.0, 0.05, 0.55, 1.0])], [2, 0, 0, 0, 0, 1, 0, 0, 0, 1])
        row = {"percent": 50, "stats": {"n": 10}, "discrimination": -0.2}
        self.assertEqual([f[0] for f in stats._question_flags(row)], ["discriminación negativa"])

    def test_tiempo_fuera_se_suma_y_la_salida_mas_larga_es_un_maximo(self):
        merged = engine.merge_telemetry({"blurs": 2, "away_s": 30, "away_max_s": 20},
                                        {"blurs": 1, "away_s": 15, "away_max_s": 12})
        self.assertEqual(merged, {"blurs": 3, "away_s": 45, "away_max_s": 20})
        self.assertEqual(engine.merge_telemetry(merged, {"away_max_s": 90})["away_max_s"], 90)

    def test_estudiante_no_ve_las_estadisticas(self):
        self.client.force_login(self.ana)
        self.assertIn(self.client.get(self.url).status_code, (403, 404))


class UploadTests(ActivityBase):
    def setUp(self):
        super().setUp()
        self.attempt = engine.start(self.activity, self.ana)

    def upload(self, content, name="foto.png"):
        self.client.force_login(self.ana)
        return self.client.post(reverse("attempt_upload", args=[self.course.pk, self.activity.pk]),
                                {"key": "D1-foto.q1", "f-D1-foto.q1": SimpleUploadedFile(name, content)},
                                HTTP_X_REQUESTED_WITH="fetch")

    def test_imagen_se_recodifica_sin_metadatos(self):
        response = self.upload(jpeg_with_gps(), "foto.jpg")
        self.assertEqual(response.json()["error"], "")
        row = Upload.objects.get()
        with Image.open(row.file.path) as saved:
            self.assertEqual(saved.format, "JPEG")
            self.assertEqual(len(saved.getexif()), 0)

    def test_png_se_acepta(self):
        self.assertEqual(self.upload(png_bytes()).json()["error"], "")
        self.assertEqual(Upload.objects.get().width, 40)

    def test_svg_y_texto_se_rechazan(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertTrue(self.upload(svg, "x.svg").json()["error"])
        self.assertTrue(self.upload(b"%PDF-1.4 hola", "x.png").json()["error"])
        self.assertFalse(Upload.objects.exists())

    def test_maximo_de_imagenes(self):
        self.upload(png_bytes())
        self.upload(png_bytes())
        self.assertIn("hasta 2", self.upload(png_bytes()).json()["error"])
        self.assertEqual(Upload.objects.count(), 2)

    def test_quien_ve_la_imagen(self):
        self.upload(png_bytes())
        url = reverse("upload_file", args=[Upload.objects.get().pk])
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.teacher)
        response = self.client.get(url)
        self.assertEqual((response.status_code, response["X-Content-Type-Options"]), (200, "nosniff"))
        self.client.force_login(self.bruno)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(make_user("otra", User.Role.TEACHER))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_enviar_con_imagen_queda_por_revisar(self):
        self.upload(png_bytes())
        attempt = engine.submit(self.attempt)
        answer = attempt.answer_rows.get(key="D1-foto.q1")
        self.assertTrue(answer.needs_review)
        self.assertEqual(len(answer.value), 1)

    def test_subir_solo_imagen_no_borra_las_respuestas(self):
        engine.save_draft(self.attempt, {"D1-a.q1": "1.4142"})
        self.upload(png_bytes())
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.answers["D1-a.q1"], "1.4142")

    def test_borrar_el_intento_borra_los_archivos(self):
        self.upload(png_bytes())
        path = Upload.objects.get().file.path
        self.attempt.delete()
        import os
        self.assertFalse(os.path.exists(path))


class DatesTests(ActivityBase):
    def test_mover_la_fecha_clave_mueve_sus_actividades(self):
        prueba = Milestone.objects.create(course=self.course, name="Prueba 1", on=date(2026, 11, 12))
        self.activity.due_milestone = prueba
        self.activity.compute_due()
        self.activity.save()
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_dates", args=[self.course.pk]), {
            "action": "milestone_save", "milestone": prueba.pk,
            f"m{prueba.pk}-name": "Prueba 1", f"m{prueba.pk}-on": "2026-11-19"})
        self.activity.refresh_from_db()
        self.assertEqual(timezone.localtime(self.activity.due_at).date(), date(2026, 11, 18))

    def test_mover_varias_y_atarlas(self):
        before = self.activity.due_at
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_dates", args=[self.course.pk]),
                         {"action": "shift", "activity": [self.activity.pk], "days": "7"})
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.due_at - before, timedelta(days=7))
        prueba = Milestone.objects.create(course=self.course, name="Examen", on=date(2026, 12, 3))
        self.client.post(reverse("activity_dates", args=[self.course.pk]),
                         {"action": "link", "activity": [self.activity.pk], "milestone": prueba.pk, "offset": "-2"})
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.due_milestone, prueba)
        self.assertEqual(timezone.localtime(self.activity.due_at).date(), date(2026, 12, 1))

    def test_eliminar_la_fecha_clave_deja_la_fecha_fija(self):
        prueba = Milestone.objects.create(course=self.course, name="Prueba 1", on=date(2026, 11, 12))
        self.activity.due_milestone = prueba
        self.activity.compute_due()
        self.activity.save()
        due = self.activity.due_at
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_dates", args=[self.course.pk]),
                         {"action": "milestone_delete", "milestone": prueba.pk})
        self.activity.refresh_from_db()
        self.assertEqual((self.activity.due_milestone, self.activity.due_at), (None, due))


class CoursePagesTests(ActivityBase):
    def test_pestana_actividades_del_docente(self):
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("course_activities", args=[self.course.pk]))
        self.assertContains(page, "Deber 1")
        self.assertContains(page, "Parcial 1")

    def test_curso_del_estudiante_lista_lo_pendiente(self):
        self.client.force_login(self.ana)
        page = self.client.get(reverse("course_detail", args=[self.course.pk]))
        self.assertContains(page, "Deber 1")
        self.assertContains(page, "pendiente")
        Activity.objects.filter(pk=self.activity.pk).update(published=False)
        self.assertNotContains(self.client.get(reverse("course_detail", args=[self.course.pk])), "Deber 1")


class GitHubActivityTests(ActivityBase):
    """Las actividades viven en la carpeta de su unidad, como las presentaciones."""

    def setUp(self):
        super().setUp()
        from apps.github.models import CourseRepo, GitHubAccount
        from apps.github.tests import FakeGitHub

        account = GitHubAccount(user=self.teacher, login="profe-gh")
        account.token = "github_pat_secreto"
        account.save()
        self.gh = FakeGitHub({"mn/clases/unidad-01/deber-2.yaml": SOURCE.replace("Deber 1", "Deber 2")})
        patcher = mock.patch("apps.github.sync.client_for", return_value=self.gh)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.link = CourseRepo.objects.create(course=self.course, repo="profe/cursos", folder="mn/clases")

    def test_actividad_de_github_llega_como_borrador_en_su_unidad(self):
        from apps.github import sync

        sync.pull(self.link)
        pulled = Activity.objects.get(github_path="mn/clases/unidad-01/deber-2.yaml")
        self.assertEqual(pulled.title, "Deber 2")
        self.assertFalse(pulled.published)
        self.assertEqual(pulled.section.title, "Unidad 01")         # una carpeta con solo actividades es sección

    def test_formato_unico_en_github(self):
        from apps.github import sync
        from apps.live.models import Deck

        clase = TIMED.replace("use: activity", "use: class").replace(", block_paste: true", "") \
            .replace("type: text, prompt: 'Escriba la barra invertida', grading: { accept: ['\\'], normalize: [] }",
                     "type: numeric, prompt: '1 + 1', solution: 2, grading: { atol: 0 }")
        self.gh.files["mn/clases/unidad-01/clase-teclado.yaml"] = clase
        self.gh.files["mn/clases/unidad-01/deber-teclado.yaml"] = TIMED
        self.gh.files["mn/clases/unidad-01/prueba.yaml"] = TIMED.replace("use: activity", "use: evaluation")
        result = sync.pull(self.link)
        self.assertEqual(Deck.objects.get(github_path="mn/clases/unidad-01/clase-teclado.yaml").section.title,
                         "Unidad 01")
        self.assertTrue(Activity.objects.filter(github_path="mn/clases/unidad-01/deber-teclado.yaml").exists())
        self.assertEqual([i for i in result.issues if i["path"].endswith("prueba.yaml")], [])
        self.gh.files["mn/clases/unidad-01/repaso.yaml"] = TIMED.replace("use: activity", "use: practice")
        sync.pull(self.link)
        repaso = Activity.objects.get(github_path="mn/clases/unidad-01/repaso.yaml")
        self.assertTrue(repaso.is_practice)                                # llega como repaso
        self.assertEqual((repaso.category, repaso.auto_submit), (None, False))
        prueba = Activity.objects.get(github_path="mn/clases/unidad-01/prueba.yaml")
        self.assertTrue(prueba.is_evaluation)                              # llega como evaluación
        self.assertEqual((prueba.max_attempts, prueba.late_policy, prueba.published), (1, "block", False))
        self.assertEqual(len(prueba.access_code), 6)

    def test_borrada_en_github_con_entregas_se_conserva(self):
        from apps.github import sync

        sync.pull(self.link)
        pulled = Activity.objects.get(title="Deber 2")
        Activity.objects.filter(pk=pulled.pk).update(published=True)
        pulled.refresh_from_db()
        engine.submit(engine.start(pulled, self.ana))
        del self.gh.files["mn/clases/unidad-01/deber-2.yaml"]
        result = sync.pull(self.link)
        pulled.refresh_from_db()
        self.assertEqual(pulled.github_path, "")
        self.assertTrue(result.issues[0]["note"])

    def test_nueva_actividad_en_la_web_va_a_la_carpeta_de_su_seccion(self):
        from apps.github import sync

        sync.pull(self.link)
        unit = Section.objects.get(course=self.course)
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_new", args=[self.course.pk]),
                         {"source": SOURCE.replace("Deber 1", "Taller 3")})
        self.assertIn("mn/clases/taller-3.yaml", self.gh.files)          # sin sección: en la raíz
        self.client.post(reverse("activity_new", args=[self.course.pk]),
                         {"source": SOURCE.replace("Deber 1", "Taller 4"), "section": unit.pk})
        self.assertIn("mn/clases/unidad-01/taller-4.yaml", self.gh.files)

    def test_mover_y_renombrar_la_seccion_mueven_el_archivo(self):
        from apps.github import sync

        sync.pull(self.link)
        pulled = Activity.objects.get(title="Deber 2")
        self.client.force_login(self.teacher)
        self.client.post(reverse("section_create", args=[self.course.pk]), {"title": "Unidad 02"})
        unit2 = Section.objects.get(title="Unidad 02")
        self.assertEqual(self.client.post(reverse("activity_move", args=[self.course.pk, pulled.pk]),
                                          {"section": unit2.pk}).json(), {"ok": True})
        pulled.refresh_from_db()
        self.assertEqual(pulled.github_path, "mn/clases/Unidad 02/deber-2.yaml")
        self.assertNotIn("mn/clases/unidad-01/deber-2.yaml", self.gh.files)
        self.client.post(reverse("section_rename", args=[self.course.pk, unit2.pk]), {"title": "Unidad 2 - Raíces"})
        pulled.refresh_from_db()
        self.assertEqual(pulled.github_path, "mn/clases/Unidad 2 - Raíces/deber-2.yaml")
        self.assertIn(pulled.github_path, self.gh.files)
        self.client.force_login(make_user("otra", User.Role.TEACHER))
        self.assertEqual(self.client.post(reverse("activity_move", args=[self.course.pk, pulled.pk]),
                                          {"section": ""}).status_code, 404)


class PagesRenderTests(ActivityBase):
    """Cada página del docente y del estudiante se dibuja sin errores."""

    def test_paginas_del_docente(self):
        attempt = engine.start(self.activity, self.ana)
        self.answer_all(attempt)
        attempt = engine.submit(attempt)
        Milestone.objects.create(course=self.course, name="Prueba 1", on=date(2026, 11, 12))
        self.client.force_login(self.teacher)
        for name, args in [("activity_new", [self.course.pk]),
                           ("activity_edit", [self.course.pk, self.activity.pk]),
                           ("activity_detail", [self.course.pk, self.activity.pk]),
                           ("activity_dates", [self.course.pk]),
                           ("attempt_review", [self.course.pk, self.activity.pk, attempt.pk]),
                           ("question_review", [self.course.pk, self.activity.pk, "D1-foto.q1"]),
                           ("grading_settings", [self.course.pk]),
                           ("course_gradebook", [self.course.pk]),
                           ("course_settings", [self.course.pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_paginas_del_estudiante(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("my_grades", args=[self.course.pk])).status_code, 200)
        start = self.client.post(reverse("attempt_start", args=[self.course.pk, self.activity.pk]))
        self.assertRedirects(start, reverse("attempt_form", args=[self.course.pk, self.activity.pk]))
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.activity.pk]))
        self.assertContains(page, 'name="f-D1-foto.q1"')
        self.assertContains(page, 'accept="image/jpeg,image/png,image/webp"')

    def test_pegar_lo_decide_el_documento(self):
        # Sin `block_paste` en el documento, se puede pegar en las abiertas («Pegue su código»).
        engine.start(self.activity, self.ana)
        self.client.force_login(self.ana)
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.activity.pk]))
        self.assertNotContains(page, "data-no-paste")


class RedirectSafetyTests(ActivityBase):
    def test_volver_solo_a_este_sitio(self):
        answer = engine.submit(engine.start(self.activity, self.ana)).answer_rows.get(key="D1-a.q3")
        self.client.force_login(self.teacher)
        url = reverse("activity_answer_grade", args=[self.course.pk, self.activity.pk, answer.pk])
        response = self.client.post(url, {"points": "1", "next": "https://malicioso.example/robar"})
        self.assertEqual(response["Location"], reverse("activity_detail", args=[self.course.pk, self.activity.pk]))
        response = self.client.post(url, {"points": "1", "next": "/docente/cursos/1/"})
        self.assertEqual(response["Location"], "/docente/cursos/1/")


TIMED = r"""schema: policlase/v1
title: 'Teclado'
use: activity
slides:
  - markdown: '## Antes de empezar'
  - item:
      id: T-encuesta
      questions:
        - id: q1
          type: choice
          prompt: '¿Qué distribución tiene su teclado?'
          grading: { mode: survey }
          options: [{ text: 'Latinoamérica' }, { text: 'España' }]
  - segment: { title: 'Rápidas', time_limit_s: 15, block_paste: true }
  - item:
      id: T-barra
      questions:
        - { id: q1, type: text, prompt: 'Escriba la barra invertida', grading: { accept: ['\'], normalize: [] } }
  - segment: {}
  - item:
      id: T-suma
      questions:
        - { id: q1, type: numeric, prompt: '2 + 2', solution: 4, grading: { atol: 0 } }
"""


class TimedAndSurveyTests(ActivityBase):
    """Formato único en una actividad: contenido y segmentos entre los ítems, preguntas con tiempo y
    encuestas."""

    def setUp(self):
        super().setUp()
        from policlase_gen import document as content_format

        document, report, use = content_format.load_text(TIMED)
        self.assertEqual((report.errors, use), ([], "activity"))
        self.timed = Activity.objects.create(
            course=self.course, title="Teclado", source=TIMED, compiled=content_format.compile_content(document),
            category=self.deberes, published=True, due_at=timezone.now() + timedelta(days=2))
        self.survey_key = "T-encuesta.q1"
        self.latina = self.timed.compiled["items"][1]["questions"][0]["options"][0]["key"]

    def test_la_pregunta_con_tiempo_se_abre_a_pedido_y_se_cierra_sola(self):
        attempt = engine.start(self.timed, self.ana)
        question = next(q for q in attempt.questions if q["key"] == "T-barra.q1")
        self.assertEqual(engine.timer(attempt, question).state, "locked")
        engine.save_draft(attempt, {"T-barra.q1": "\\"})                 # sin abrir: no se guarda
        attempt.refresh_from_db()
        self.assertNotIn("T-barra.q1", attempt.answers)

        start = timezone.now()
        attempt = engine.open_timer(attempt, "T-barra.q1", now=start)
        engine.save_draft(attempt, {"T-barra.q1": "\\"}, now=start + timedelta(seconds=10))
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers["T-barra.q1"], "\\")
        late = start + timedelta(seconds=15 + engine.TIMER_GRACE_S + 1)
        self.assertTrue(engine.timer(attempt, question, now=late).overtime)  # sigue abierta, fuera de tiempo
        engine.save_draft(attempt, {"T-barra.q1": "\\"}, now=late)      # la misma respuesta: sin penalización
        attempt.refresh_from_db()
        self.assertNotIn("late", attempt.timers["T-barra.q1"])
        attempt = engine.open_timer(attempt, "T-barra.q1", now=late)      # abrir otra vez no reinicia
        self.assertEqual(attempt.timers["T-barra.q1"]["opened"], start.isoformat())

        engine.save_draft(attempt, {self.survey_key: self.latina, "T-suma.q1": "4"})
        attempt = engine.submit(attempt)
        answers = {a.key: a for a in attempt.answer_rows.all()}
        self.assertTrue(answers["T-barra.q1"].correct)
        self.assertFalse(answers["T-barra.q1"].late)
        self.assertEqual(answers[self.survey_key].points, 0)              # la encuesta no da puntos
        self.assertEqual((attempt.points, attempt.max_points), (2, 2))

    def test_fuera_de_tiempo_vale_con_penalizacion(self):
        attempt = engine.start(self.timed, self.ana)
        start = timezone.now() - timedelta(seconds=40)
        attempt = engine.open_timer(attempt, "T-barra.q1", now=start)
        engine.save_draft(attempt, {"T-barra.q1": "\\"})                # 40 s después de abrir (límite 15)
        attempt.refresh_from_db()
        self.assertTrue(attempt.timers["T-barra.q1"]["late"])
        attempt = engine.submit(attempt)
        answer = attempt.answer_rows.get(key="T-barra.q1")
        self.assertTrue(answer.late and answer.correct)
        self.assertAlmostEqual(answer.points, 0.7)

    def test_responder_cierra_la_pregunta_y_el_autoguardado_no_la_reabre(self):
        attempt = engine.open_timer(engine.start(self.timed, self.ana), "T-barra.q1")
        attempt = engine.close_timer(attempt, "T-barra.q1", {"T-barra.q1": "\\"})
        engine.save_draft(attempt, {"T-barra.q1": "otra"})
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers["T-barra.q1"], "\\")
        with self.assertRaises(engine.ActionError):
            engine.open_timer(attempt, "T-suma.q1")                       # no tiene tiempo

    def test_paginas_y_vistas_del_reloj(self):
        self.client.force_login(self.ana)
        self.client.post(reverse("attempt_start", args=[self.course.pk, self.timed.pk]))
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.timed.pk]))
        self.assertContains(page, "Antes de empezar")                     # contenido entre ítems
        self.assertContains(page, "Rápidas")                              # el segmento
        self.assertContains(page, "Abrir la pregunta")
        self.assertNotContains(page, "Escriba la barra invertida")        # sin abrir no se ve el enunciado
        self.assertContains(page, "Encuesta")

        open_url = reverse("attempt_timer", args=[self.course.pk, self.timed.pk, "T-barra.q1", "abrir"])
        data = self.client.post(open_url, {"form": "attempt"}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertIn("Escriba la barra invertida", data["html"])
        self.assertIn("data-no-paste", data["html"])
        self.assertGreater(data["remaining_ms"], 10_000)
        close_url = reverse("attempt_timer", args=[self.course.pk, self.timed.pk, "T-barra.q1", "responder"])
        data = self.client.post(close_url, {"form": "attempt", "a-T-barra.q1": "\\"},
                                HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertIn("Respondió", data["html"])
        self.assertEqual(Attempt.objects.get(student=self.ana, activity=self.timed).answers["T-barra.q1"], "\\")

        self.client.force_login(self.bruno)                               # sin intento propio: 404
        self.assertEqual(self.client.post(open_url).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(open_url).status_code, 404)

    def test_la_encuesta_no_se_marca_como_incorrecta_en_la_revision(self):
        attempt = engine.start(self.timed, self.ana)
        engine.save_draft(attempt, {self.survey_key: self.latina})
        attempt = engine.submit(attempt)
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("attempt_review", args=[self.course.pk, self.timed.pk, attempt.pk]))
        self.assertContains(page, 'class="picked"')
        self.assertNotContains(page, "picked wrong")

    def test_el_docente_ve_la_distribucion_de_la_encuesta(self):
        for student in (self.ana, self.bruno):
            attempt = engine.start(self.timed, student)
            engine.save_draft(attempt, {self.survey_key: self.latina})
            engine.submit(attempt)
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("activity_detail", args=[self.course.pk, self.timed.pk]))
        self.assertContains(page, "¿Qué distribución tiene su teclado?")
        self.assertContains(page, "2 respuestas")
        preview = self.client.post(reverse("content_preview", args=[self.course.pk]), {"source": TIMED}).json()
        self.assertEqual(preview["kind"], "activity")
        self.assertIn("15 s", preview["html"])
        self.assertIn("segment", preview["outline"])


WORKSHOP = TIMED.replace("  - segment: {}\n", """  - solution: |
      ## Solución de la parte rápida
      La barra invertida se escribe con AltGr.
  - segment: {}
""")


class WorkshopBase(ActivityBase):
    """Un taller en el formato único, con una diapositiva de solución."""

    def setUp(self):
        super().setUp()
        from policlase_gen import document as content_format

        document, report, _use = content_format.load_text(WORKSHOP)
        self.assertEqual(report.errors, [])
        self.workshop = Activity.objects.create(
            course=self.course, title="Taller", source=WORKSHOP, compiled=content_format.compile_content(document),
            category=self.deberes, published=True, opens_at=timezone.now() - timedelta(hours=2),
            due_at=timezone.now() + timedelta(days=2))

    def overdue(self, activity, hours=1):
        """La fecha de entrega ya pasó (y la gracia del envío automático también)."""
        Activity.objects.filter(pk=activity.pk).update(due_at=timezone.now() - timedelta(hours=hours))
        activity.refresh_from_db()
        Attempt.objects.filter(activity=activity).update(started_at=activity.due_at - timedelta(hours=1))


class AutoSubmitTests(WorkshopBase):
    def test_el_borrador_se_envia_solo_al_vencer(self):
        attempt = engine.start(self.activity, self.ana)
        engine.save_draft(attempt, {"D1-a.q1": "1.4142", "D1-a.q2": "dos coma cinco"})
        self.overdue(self.activity)
        state = engine.state(self.activity, self.ana)
        counted = state.counted
        self.assertTrue(counted.auto_submitted)
        self.assertEqual(counted.submitted_at, self.activity.due_at)       # a tiempo, sin penalización
        self.assertFalse(counted.late)
        self.assertEqual(counted.points, 2)                                # lo ilegible vale cero
        self.assertEqual(counted.answers["D1-a.q2"], "dos coma cinco")      # nada se pierde

    def test_dentro_de_la_gracia_todavia_se_guarda(self):
        attempt = engine.start(self.activity, self.ana)
        Activity.objects.filter(pk=self.activity.pk).update(due_at=timezone.now() - timedelta(seconds=30))
        self.activity.refresh_from_db()
        self.assertEqual(engine.finalize_overdue(self.activity), 0)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.submitted_at)

    def test_sin_envio_automatico_o_intento_atrasado_no_se_envia(self):
        Activity.objects.filter(pk=self.activity.pk).update(auto_submit=False)
        self.activity.refresh_from_db()
        engine.start(self.activity, self.ana)
        self.overdue(self.activity)
        self.assertEqual(engine.finalize_overdue(self.activity), 0)
        # Empezado después del vencimiento (entrega atrasada): lo envía el estudiante.
        Activity.objects.filter(pk=self.activity.pk).update(auto_submit=True)
        self.activity.refresh_from_db()
        Attempt.objects.filter(activity=self.activity).update(started_at=timezone.now())
        self.assertEqual(engine.finalize_overdue(self.activity), 0)

    def test_el_autoguardado_de_una_pagina_abierta_recibe_el_motivo(self):
        engine.start(self.activity, self.ana)
        self.overdue(self.activity)
        self.client.force_login(self.ana)
        response = self.client.post(reverse("attempt_form", args=[self.course.pk, self.activity.pk]),
                                    {"form": "attempt", "a-D1-a.q1": "1"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.status_code, 409)
        self.assertIn("se envió automáticamente", response.json()["error"])

    def test_mover_la_fecha_hacia_adelante_reabre_lo_enviado_solo(self):
        attempt = engine.start(self.activity, self.ana)
        engine.save_draft(attempt, {"D1-a.q1": "1.4142"})
        other = engine.start(self.activity, self.bruno)
        engine.save_draft(other, {"D1-a.q3": "Porque sí"})
        self.overdue(self.activity)
        engine.finalize_overdue(self.activity)
        graded = Answer.objects.get(attempt__student=self.bruno, key="D1-a.q3")
        engine.set_grade(graded, 1, self.teacher)                         # el docente ya la calificó
        Activity.objects.filter(pk=self.activity.pk).update(due_at=timezone.now() + timedelta(days=1))
        self.activity.refresh_from_db()
        self.assertEqual(engine.reopen_auto_submitted(self.activity), 1)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.submitted_at)
        self.assertEqual(attempt.answers["D1-a.q1"], "1.4142")
        self.assertFalse(attempt.answer_rows.exists())
        self.assertIsNotNone(Attempt.objects.get(student=self.bruno).submitted_at)

    def test_la_planilla_cuenta_lo_enviado_al_vencer(self):
        attempt = engine.start(self.activity, self.ana)
        engine.save_draft(attempt, {"D1-a.q1": "1.4142"})
        self.overdue(self.activity)
        grades = gradebook.course_grades(self.course, [self.ana])
        attempt.refresh_from_db()
        self.assertTrue(attempt.auto_submitted)
        self.assertTrue(grades)


class ReviewWindowTests(WorkshopBase):
    def submit_workshop(self, student=None):
        attempt = engine.start(self.workshop, student or self.ana)
        engine.save_draft(attempt, {"T-suma.q1": "4"})
        return engine.submit(attempt)

    def test_la_solucion_no_se_ve_durante_el_intento(self):
        engine.start(self.workshop, self.ana)
        self.client.force_login(self.ana)
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.workshop.pk]))
        self.assertNotContains(page, "AltGr")
        self.assertNotContains(page, "Solución de la parte rápida")       # tampoco en el índice

    def test_revision_con_soluciones_hasta_su_cierre(self):
        self.submit_workshop()
        self.client.force_login(self.ana)
        url = reverse("activity_student", args=[self.course.pk, self.workshop.pk])
        self.assertNotContains(self.client.get(url), "AltGr")              # antes del vencimiento
        self.overdue(self.workshop)
        Activity.objects.filter(pk=self.workshop.pk).update(review_mode=Activity.Review.FIXED,
                                                            review_until=timezone.now() + timedelta(days=1))
        page = self.client.get(url)
        self.assertContains(page, "AltGr")
        self.assertContains(page, "La revisión está disponible hasta")
        self.assertContains(page, 'class="watermark"')
        Activity.objects.filter(pk=self.workshop.pk).update(review_until=timezone.now() - timedelta(minutes=1))
        page = self.client.get(url)
        self.assertNotContains(page, "AltGr")
        self.assertContains(page, "ahora se muestra solo el puntaje")

    def test_sin_revision_solo_el_puntaje(self):
        self.submit_workshop()
        self.overdue(self.workshop)
        Activity.objects.filter(pk=self.workshop.pk).update(review_mode=Activity.Review.NONE)
        self.workshop.refresh_from_db()
        state = engine.state(self.workshop, self.ana)
        self.assertFalse(state.reveal or state.solutions)

    def test_quien_no_entrego_ve_las_soluciones_solo_si_ya_no_puede_entregar(self):
        self.overdue(self.workshop)
        self.assertFalse(engine.state(self.workshop, self.bruno).solutions)   # aún puede entregar atrasado
        Activity.objects.filter(pk=self.workshop.pk).update(late_policy=Activity.Late.BLOCK)
        self.workshop.refresh_from_db()
        self.assertTrue(engine.state(self.workshop, self.bruno).solutions)
        self.client.force_login(self.bruno)
        page = self.client.get(reverse("activity_student", args=[self.course.pk, self.workshop.pk]))
        self.assertContains(page, "No entregó esta actividad")
        self.assertContains(page, "AltGr")


    def test_revision_hasta_una_fecha_clave_que_se_mueve(self):
        prueba = Milestone.objects.create(course=self.course, name="Prueba 1", on=date(2026, 11, 12))
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_dates", args=[self.course.pk]), {
            "action": "link", "activity": [self.workshop.pk], "milestone": prueba.pk, "offset": "-3",
            "review_offset": "0"})
        self.workshop.refresh_from_db()
        tz = timezone.get_default_timezone()
        self.assertEqual(timezone.localtime(self.workshop.due_at, tz).date(), date(2026, 11, 9))
        self.assertEqual(self.workshop.review_mode, Activity.Review.MILESTONE)
        self.assertEqual(timezone.localtime(self.workshop.review_until, tz).replace(tzinfo=None),
                         datetime(2026, 11, 12, 0, 0))
        prueba.on = date(2026, 11, 19)
        prueba.save()
        gradebook.recompute_due_dates(prueba)
        self.workshop.refresh_from_db()
        self.assertEqual(timezone.localtime(self.workshop.review_until, tz).date(), date(2026, 11, 19))
        self.assertEqual(timezone.localtime(self.workshop.due_at, tz).date(), date(2026, 11, 16))

    def test_configurar_la_revision_en_la_pagina(self):
        prueba = Milestone.objects.create(course=self.course, name="Prueba 1", on=date(2026, 11, 12))
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_detail", args=[self.course.pk, self.workshop.pk]), {
            "category": self.deberes.pk, "published": "on", "due_mode": "milestone",
            "due_milestone": prueba.pk, "due_offset_days": "-3", "due_time": "21:59",
            "review_mode": "milestone", "review_milestone": prueba.pk, "review_offset_days": "0",
            "review_time": "07:00", "late_policy": "penalty", "late_penalty": "30", "max_attempts": "3",
            "auto_submit": "on"})
        self.workshop.refresh_from_db()
        local = timezone.localtime(self.workshop.review_until, timezone.get_default_timezone())
        self.assertEqual((local.date(), local.time()), (date(2026, 11, 12), time(7, 0)))


class DueFromOpeningTests(WorkshopBase):
    def test_siete_dias_despues_de_la_apertura(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_detail", args=[self.course.pk, self.workshop.pk]), {
            "category": self.deberes.pk, "published": "on", "opens_at": "2026-10-13T16:05",
            "due_mode": "opening", "opening_days": "7", "opening_time": "21:59", "review_mode": "always",
            "late_policy": "penalty", "late_penalty": "30", "max_attempts": "3"})
        self.workshop.refresh_from_db()
        self.assertTrue(self.workshop.due_from_opening)
        local = timezone.localtime(self.workshop.due_at, timezone.get_default_timezone())
        self.assertEqual((local.date(), local.time()), (date(2026, 10, 20), time(21, 59)))

    def test_sin_apertura_no_se_puede(self):
        self.client.force_login(self.teacher)
        page = self.client.post(reverse("activity_detail", args=[self.course.pk, self.workshop.pk]), {
            "category": self.deberes.pk, "published": "on", "opens_at": "", "due_mode": "opening",
            "opening_days": "7", "opening_time": "21:59", "review_mode": "always",
            "late_policy": "penalty", "late_penalty": "30", "max_attempts": "3"})
        self.assertContains(page, "Indique la fecha de apertura")


class DisabledSectionsTests(WorkshopBase):
    def test_una_seccion_deshabilitada_no_suma_ni_cuenta_en_el_maximo(self):
        attempt = engine.start(self.activity, self.ana)
        self.answer_all(attempt)
        attempt = engine.submit(attempt)
        self.assertEqual((attempt.points, attempt.max_points), (3, 6))
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_parts", args=[self.course.pk, self.activity.pk]), {"enabled": ["D1-a"]})
        attempt.refresh_from_db()
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.ungraded_items, ["D1-foto"])
        self.assertEqual(self.activity.points, 5)
        self.assertEqual((attempt.points, attempt.max_points), (3, 5))
        self.assertEqual(attempt.answer_rows.count(), 4)                  # ninguna respuesta se borra
        self.client.post(reverse("activity_parts", args=[self.course.pk, self.activity.pk]),
                         {"enabled": ["D1-a", "D1-foto"]})
        attempt.refresh_from_db()
        self.assertEqual(attempt.max_points, 6)

    def test_el_estudiante_no_la_ve_y_su_respuesta_se_conserva(self):
        attempt = engine.start(self.workshop, self.ana)
        engine.save_draft(attempt, {"T-suma.q1": "4"})
        engine.set_disabled(self.workshop, ["T-suma"])
        self.client.force_login(self.ana)
        url = reverse("attempt_form", args=[self.course.pk, self.workshop.pk])
        page = self.client.get(url)
        self.assertNotContains(page, 'name="a-T-suma.q1"')
        self.assertNotContains(page, 'data-qchip="T-suma.q1"')
        # El autoguardado de la página (sin esa pregunta) no la deja en blanco.
        self.client.post(url, {"form": "attempt", "a-T-barra.q1": ""}, HTTP_X_REQUESTED_WITH="fetch")
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers["T-suma.q1"], "4")
        engine.set_disabled(self.workshop, [])
        self.assertContains(self.client.get(url), 'name="a-T-suma.q1" value="4"')

    def test_toda_la_seccion_se_oculta_con_su_enunciado(self):
        from . import outline

        content = {"items": [
            {"kind": "content", "markdown": "## Ejercicio 1", "at": 0},
            {"kind": "item", "item_id": "E1", "questions": [], "at": 1},
            {"kind": "content", "markdown": "## Ejercicio 2\nEnunciado largo.", "at": 2},
            {"kind": "item", "item_id": "E2a", "questions": [], "at": 3},
            {"kind": "solution", "markdown": "Solución 2", "at": 4},
            {"kind": "item", "item_id": "E2b", "questions": [], "at": 5},
        ]}
        self.assertEqual(outline.hidden_blocks(content, ["E2a", "E2b"]), {2, 3, 4, 5})
        self.assertEqual(outline.hidden_blocks(content, ["E2a"]), {3})       # la sección sigue: su enunciado también
        self.assertEqual([g["title"] for g in outline.parts(content)], ["Ejercicio 1", "Ejercicio 2"])

    def test_una_respuesta_ilegible_en_lo_deshabilitado_no_impide_enviar(self):
        attempt = engine.start(self.workshop, self.ana)
        engine.save_draft(attempt, {"T-suma.q1": "cuatro"})
        engine.set_disabled(self.workshop, ["T-suma"])
        attempt.refresh_from_db()
        self.assertEqual(engine.format_errors(attempt), {})
        engine.submit(attempt)

    def test_otro_docente_no_cambia_las_secciones(self):
        other = make_user("otra", User.Role.TEACHER)
        self.client.force_login(other)
        response = self.client.post(reverse("activity_parts", args=[self.course.pk, self.activity.pk]), {"enabled": []})
        self.assertEqual(response.status_code, 404)
        self.activity.refresh_from_db()
        self.assertEqual(self.activity.ungraded_items, [])


class PresentTests(WorkshopBase):
    def test_presentar_y_seguir_al_docente(self):
        engine.start(self.workshop, self.ana)
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("activity_present", args=[self.course.pk, self.workshop.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Escriba la barra invertida")
        segment = next(b for b in self.workshop.compiled["items"] if b["kind"] == "segment" and b["title"])
        moved = self.client.post(reverse("activity_present_action", args=[self.course.pk, self.workshop.pk]),
                                 {"action": "move", "at": segment["at"]}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual(moved["working"], 1)
        self.client.force_login(self.ana)
        pointer = self.client.get(reverse("attempt_pointer", args=[self.course.pk, self.workshop.pk])).json()
        self.assertEqual(pointer, {"presenting": True, "at": segment["at"], "item": "", "heading": "Rápidas"})
        # Sin señal del proyector, se da por terminada.
        Activity.objects.filter(pk=self.workshop.pk).update(presenting_at=timezone.now() - timedelta(minutes=5))
        pointer = self.client.get(reverse("attempt_pointer", args=[self.course.pk, self.workshop.pk])).json()
        self.assertEqual(pointer, {"presenting": False})

    def test_el_proyector_no_revela_respuestas(self):
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("activity_present", args=[self.course.pk, self.workshop.pk]))
        self.assertNotContains(page, "AltGr")                              # la solución, antes de la revisión
        self.assertNotContains(page, "Se acepta")
        self.assertNotContains(page, 'class="right"')
        self.overdue(self.workshop)
        page = self.client.get(reverse("activity_present", args=[self.course.pk, self.workshop.pk]))
        self.assertContains(page, "AltGr")                                 # con la revisión abierta, sí

    def test_terminar_y_aislamiento(self):
        self.client.force_login(self.teacher)
        url = reverse("activity_present_action", args=[self.course.pk, self.workshop.pk])
        self.client.post(url, {"action": "move", "at": 0})
        self.client.post(url, {"action": "stop"})
        self.workshop.refresh_from_db()
        self.assertFalse(self.workshop.is_presenting())
        other = make_user("otra", User.Role.TEACHER)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("activity_present", args=[self.course.pk, self.workshop.pk])).status_code, 404)
        self.assertEqual(self.client.post(url, {"action": "move", "at": 0}).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("attempt_pointer", args=[self.course.pk, self.workshop.pk])).status_code, 404)


class PanelsAndReviewTests(WorkshopBase):
    def test_enter_en_un_campo_no_cierra_una_pregunta_con_tiempo(self):
        """El botón por omisión del formulario (el que pulsa Enter) es «Guardar», no el «Responder» de
        una pregunta con tiempo abierta."""
        import re

        attempt = engine.start(self.workshop, self.ana)
        timed = next(q["key"] for q in attempt.questions if q.get("time_limit_s"))
        engine.open_timer(attempt, timed)
        self.client.force_login(self.ana)
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.workshop.pk])).content.decode()
        form = page[page.index('id="attempt-form"'):]
        first = re.search(r'<button[^>]*type="submit"[^>]*>', form).group(0)
        self.assertIn('value="save"', first)
        self.assertIn("data-timer-close", form)

    def test_marcar_para_revisar_se_guarda(self):
        engine.start(self.activity, self.ana)
        self.client.force_login(self.ana)
        url = reverse("attempt_form", args=[self.course.pk, self.activity.pk])
        self.client.post(url, {"form": "attempt", "mark": ["D1-a.q3", "inventada"]}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(Attempt.objects.get().marked, ["D1-a.q3"])
        page = self.client.get(url)
        self.assertContains(page, 'name="mark" value="D1-a.q3" checked')
        self.assertContains(page, 'data-qchip="D1-a.q3"')                  # el panel de preguntas
        self.assertContains(page, "qchip marked")

    def test_el_docente_ve_el_avance_y_deja_un_comentario(self):
        attempt = engine.start(self.activity, self.ana)
        engine.save_draft(attempt, {"D1-a.q3": "Avance del estudiante"})
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("attempt_review", args=[self.course.pk, self.activity.pk, attempt.pk]))
        self.assertContains(page, "Borrador en curso")
        self.assertContains(page, "Avance del estudiante")
        self.assertNotContains(page, "data-grade-form")                    # un borrador no se califica
        attempt = engine.submit(attempt)
        self.client.post(reverse("attempt_feedback", args=[self.course.pk, self.activity.pk, attempt.pk]),
                         {"feedback": "Buen trabajo en la parte A."})
        self.client.force_login(self.ana)
        page = self.client.get(reverse("activity_student", args=[self.course.pk, self.activity.pk]))
        self.assertContains(page, "Buen trabajo en la parte A.")
        other = make_user("otra", User.Role.TEACHER)
        self.client.force_login(other)
        response = self.client.post(reverse("attempt_feedback", args=[self.course.pk, self.activity.pk, attempt.pk]),
                                    {"feedback": "x"})
        self.assertEqual(response.status_code, 404)
        attempt.refresh_from_db()
        self.assertEqual(attempt.feedback, "Buen trabajo en la parte A.")

    def test_la_revision_usa_las_soluciones_de_la_version_actual(self):
        # El intento empezó antes de que se escribiera la solución: la revisión la muestra igual.
        from policlase_gen import document as content_format

        old = TIMED.replace("use: activity", "use: activity")
        document, _report, _use = content_format.load_text(old)
        Activity.objects.filter(pk=self.workshop.pk).update(compiled=content_format.compile_content(document))
        self.workshop.refresh_from_db()
        attempt = engine.start(self.workshop, self.ana)
        engine.submit(attempt)
        document, _report, _use = content_format.load_text(WORKSHOP)
        Activity.objects.filter(pk=self.workshop.pk).update(compiled=content_format.compile_content(document))
        self.overdue(self.workshop)
        self.client.force_login(self.ana)
        page = self.client.get(reverse("activity_student", args=[self.course.pk, self.workshop.pk]))
        self.assertContains(page, "AltGr")

    def test_las_casillas_de_una_lista_llevan_su_numero(self):
        engine.start(self.workshop, self.ana)
        self.client.force_login(self.ana)
        from policlase_gen import document as content_format

        source = WORKSHOP.replace("  - segment: {}\n", """  - segment: {}
  - item:
      id: T-lista
      questions:
        - { id: q1, type: numeric_list, shape: [1, 6], prompt: 'Seis valores', solution: [1, 2, 3, 4, 5, 6], grading: { atol: 0 } }
""")
        document, _report, _use = content_format.load_text(source)
        Activity.objects.filter(pk=self.workshop.pk).update(compiled=content_format.compile_content(document))
        Attempt.objects.all().delete()
        self.workshop.refresh_from_db()
        engine.start(self.workshop, self.ana)
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.workshop.pk]))
        self.assertContains(page, 'class="cells cells-list many"')
        self.assertContains(page, '<span class="cell-n" aria-hidden="true">6</span>')

    def test_las_preguntas_de_seguimiento_se_numeran_con_letra(self):
        from .views import _labeled

        item = {"item_id": "I", "title": "Ítem", "questions": []}
        rows = [{"item": item, "question": {"follow_up": f}} for f in (False, False, True, True, False)]
        self.assertEqual([r["number"] for r in _labeled(rows, {})], ["1", "2", "2a", "2b", "3"])

    def test_indice_del_taller(self):
        from . import outline

        entries = outline.build(self.workshop.compiled)
        self.assertEqual([e["kind"] for e in entries], ["content", "item", "segment", "item", "item"])
        self.assertEqual(entries[2]["title"], "Rápidas")
        self.assertNotIn("Escriba la barra invertida", [e["title"] for e in entries])   # ítem con tiempo
        groups = outline.parts(self.workshop.compiled)
        self.assertEqual([g["title"] for g in groups], ["Antes de empezar", "Rápidas"])
        self.assertTrue(any(e["kind"] == "solution" for e in outline.build(self.workshop.compiled, solutions=True)))


EVALUATION = r"""schema: policlase/v1
title: 'Prueba 1'
use: evaluation
slides:
  - item:
      id: P1-a
      questions:
        - { id: q1, type: numeric, points: 2, prompt: 'Raíz de 2', solution: 1.4142, grading: { atol: 0.001 } }
        - id: q2
          type: open
          points: 2
          prompt: 'Explique su criterio de parada'
          grading: { mode: manual, rubric: [{ criterion: 'Idea', points: 2 }] }
  - solution: |
      La raíz de 2 vale 1.4142 (CLAVE-SOLUCION).
"""


class EvaluationTests(ActivityBase):
    def setUp(self):
        super().setUp()
        from policlase_gen import document as content_format

        document, report, _use = content_format.load_text(EVALUATION)
        self.assertEqual(report.errors, [])
        self.prueba = self.course.periods.first().categories.get(name="Prueba")
        self.exam = Activity(course=self.course, title="Prueba 1", source=EVALUATION,
                             compiled=content_format.compile_content(document), category=self.prueba,
                             published=True, opens_at=timezone.now() - timedelta(minutes=10),
                             due_at=timezone.now() + timedelta(hours=2), duration_min=60)
        self.exam.set_kind("evaluation")
        self.exam.save()
        self.code = self.exam.access_code

    def started(self, student=None, minutes_ago=0):
        attempt = engine.start(self.exam, student or self.ana, code=self.code)
        if minutes_ago:
            Attempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now() - timedelta(minutes=minutes_ago))
            attempt.refresh_from_db()
        return attempt

    def test_prorroga_con_duracion_propia(self):
        engine.grant_extension(self.exam, self.ana, self.exam.due_at + timedelta(hours=1), duration_min=90,
                               note="Adecuación")
        self.exam.refresh_from_db()
        attempt = self.started(minutes_ago=80)                            # 90 min propios, no 60
        engine.save_draft(attempt, {"P1-a.q1": "1.4142"})                 # todavía puede
        self.assertEqual(engine.remaining_ms(attempt) // 60_000, 9)
        # Bruno rinde otro día: su propio horario.
        tomorrow = timezone.now() + timedelta(days=1)
        engine.grant_extension(self.exam, self.bruno, tomorrow + timedelta(hours=2), opens_at=tomorrow)
        self.exam.refresh_from_db()
        state = engine.state(self.exam, self.bruno)
        self.assertFalse(state.can_start)
        self.assertIn("comienza", state.reason)

    def test_prorroga_de_una_actividad_reabre_lo_enviado_solo(self):
        attempt = engine.start(self.activity, self.ana)
        engine.save_draft(attempt, {"D1-a.q1": "1.4142"})
        Activity.objects.filter(pk=self.activity.pk).update(due_at=timezone.now() - timedelta(hours=1))
        Attempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now() - timedelta(hours=2))
        self.activity.refresh_from_db()
        engine.finalize_overdue(self.activity)
        self.assertTrue(Attempt.objects.get(pk=attempt.pk).auto_submitted)
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_extension", args=[self.course.pk, self.activity.pk]), {
            "student": self.ana.pk, "due_at": (timezone.localtime() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "note": "Certificado médico"})
        attempt.refresh_from_db()
        self.assertIsNone(attempt.submitted_at)                           # vuelve a quedar abierto
        self.activity.refresh_from_db()
        self.assertTrue(engine.state(self.activity, self.ana).draft)
        self.assertFalse(engine.past_due(self.activity, self.ana.pk))
        self.assertTrue(engine.past_due(self.activity, self.bruno.pk))
        other = make_user("otra", User.Role.TEACHER)
        self.client.force_login(other)
        response = self.client.post(reverse("activity_extension", args=[self.course.pk, self.activity.pk]),
                                    {"student": self.bruno.pk, "due_at": "2030-01-01T10:00"})
        self.assertEqual(response.status_code, 404)

    def test_valores_de_una_evaluacion_nueva(self):
        self.assertEqual((self.exam.max_attempts, self.exam.late_policy, self.exam.require_code), (1, "block", True))
        self.assertRegex(self.code, r"^\d{6}$")

    def test_horario_y_codigo(self):
        with self.assertRaises(engine.ActionError):
            engine.start(self.exam, self.ana, code="000000" if self.code != "000000" else "111111")
        with self.assertRaises(engine.ActionError):
            engine.start(self.exam, self.ana)                              # sin código
        self.assertIsNone(Attempt.objects.filter(student=self.ana).first())
        self.started()
        Activity.objects.filter(pk=self.exam.pk).update(opens_at=timezone.now() + timedelta(hours=1))
        self.exam.refresh_from_db()
        state = engine.state(self.exam, self.bruno)
        self.assertFalse(state.can_start)
        self.assertIn("comienza", state.reason)

    def test_demasiados_codigos_incorrectos(self):
        from django.core.cache import cache

        cache.clear()
        wrong = "999999" if self.code != "999999" else "888888"
        for _ in range(engine.CODE_TRIES):
            with self.assertRaises(engine.ActionError):
                engine.start(self.exam, self.ana, code=wrong)
        with self.assertRaisesMessage(engine.ActionError, "demasiadas veces"):
            engine.start(self.exam, self.ana, code=self.code)               # ni el correcto, por ahora

    def test_la_duracion_cierra_el_intento(self):
        attempt = self.started(minutes_ago=61)
        with self.assertRaises(engine.ActionError):
            engine.save_draft(attempt, {"P1-a.q1": "1.4142"})              # ya pasó su límite
        attempt = self.started(self.bruno, minutes_ago=30)
        engine.save_draft(attempt, {"P1-a.q1": "1.4142"})
        engine.finalize_overdue(self.exam)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.submitted_at)                            # aún tiene tiempo
        ana = Attempt.objects.get(student=self.ana)
        self.assertTrue(ana.auto_submitted)
        self.assertEqual(ana.submitted_at, ana.started_at + timedelta(minutes=60))
        self.assertEqual(engine.remaining_ms(attempt) // 60_000, 29)

    def test_al_llegar_a_cero_la_pagina_la_envia(self):
        self.started(minutes_ago=60)
        self.client.force_login(self.ana)
        url = reverse("attempt_form", args=[self.course.pk, self.exam.pk])
        response = self.client.post(url, {"form": "attempt", "a-P1-a.q1": "1.4142", "action": "timeout"})
        self.assertRedirects(response, reverse("activity_student", args=[self.course.pk, self.exam.pk]))
        attempt = Attempt.objects.get(student=self.ana)
        self.assertIsNotNone(attempt.submitted_at)
        self.assertEqual(attempt.answers["P1-a.q1"], "1.4142")            # lo último llegó dentro de la gracia

    def test_nada_se_ve_hasta_publicar(self):
        attempt = self.started()
        engine.save_draft(attempt, {"P1-a.q1": "1.4142"})
        engine.submit(attempt)
        self.client.force_login(self.ana)
        page = self.client.get(reverse("activity_student", args=[self.course.pk, self.exam.pk]))
        self.assertContains(page, "Su evaluación se recibió")
        self.assertNotContains(page, "2 / 4")
        self.assertNotContains(page, "CLAVE-SOLUCION")
        grades = self.client.get(reverse("my_grades", args=[self.course.pk]))
        self.assertNotContains(grades, "Prueba 1")
        self.assertFalse(engine.state(self.exam, self.ana).can_start)       # un solo intento
        with self.assertRaises(engine.ActionError):
            engine.publish(self.exam)                                       # todavía no se cierra
        Activity.objects.filter(pk=self.exam.pk).update(due_at=timezone.now() - timedelta(minutes=1))
        self.exam.refresh_from_db()
        engine.publish(self.exam)
        page = self.client.get(reverse("activity_student", args=[self.course.pk, self.exam.pk]))
        self.assertContains(page, "2 / 4")
        self.assertContains(page, "CLAVE-SOLUCION")
        self.assertContains(self.client.get(reverse("my_grades", args=[self.course.pk])), "Prueba 1")

    def test_paginas_del_docente_y_aislamiento(self):
        self.client.force_login(self.teacher)
        detail = self.client.get(reverse("activity_detail", args=[self.course.pk, self.exam.pk]))
        self.assertContains(detail, self.code)
        projector = self.client.get(reverse("evaluation_projector", args=[self.course.pk, self.exam.pk]))
        self.assertContains(projector, self.code)
        self.assertNotContains(projector, "Raíz de 2")
        self.assertEqual(self.client.get(reverse("activity_present", args=[self.course.pk, self.exam.pk])).status_code, 404)
        self.client.post(reverse("evaluation_code", args=[self.course.pk, self.exam.pk]))
        self.exam.refresh_from_db()
        self.assertNotEqual(self.exam.access_code, self.code)
        tab = self.client.get(reverse("activity_dates", args=[self.course.pk]))
        self.assertContains(tab, "Prueba 1")
        other = make_user("otra", User.Role.TEACHER)
        self.client.force_login(other)
        for name in ("evaluation_projector", "activity_detail"):
            self.assertEqual(self.client.get(reverse(name, args=[self.course.pk, self.exam.pk])).status_code, 404)
        for name in ("evaluation_code", "evaluation_publish"):
            self.assertEqual(self.client.post(reverse(name, args=[self.course.pk, self.exam.pk])).status_code, 404)

    def test_fecha_relativa_a_la_prueba_con_horario(self):
        prueba = Milestone.objects.create(course=self.course, name="Prueba 1", on=date(2026, 11, 12))
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_detail", args=[self.course.pk, self.exam.pk]), {
            "category": self.prueba.pk, "published": "on", "day_mode": "milestone", "due_milestone": prueba.pk,
            "due_offset_days": "0", "opens_time": "07:00", "due_time": "09:00", "duration_min": "90",
            "require_code": "on", "review_mode": "always"})
        self.exam.refresh_from_db()
        tz = timezone.get_default_timezone()
        self.assertEqual(timezone.localtime(self.exam.opens_at, tz).replace(tzinfo=None), datetime(2026, 11, 12, 7, 0))
        self.assertEqual(timezone.localtime(self.exam.due_at, tz).replace(tzinfo=None), datetime(2026, 11, 12, 9, 0))
        self.assertEqual(self.exam.duration_min, 90)
        prueba.on = date(2026, 11, 19)
        prueba.save()
        gradebook.recompute_due_dates(prueba)
        self.exam.refresh_from_db()
        self.assertEqual(timezone.localtime(self.exam.opens_at, tz).date(), date(2026, 11, 19))

    def test_el_estudiante_la_ve_en_su_curso_con_su_horario(self):
        self.client.force_login(self.ana)
        page = self.client.get(reverse("course_detail", args=[self.course.pk]))
        self.assertContains(page, "Evaluaciones")
        self.assertContains(page, "disponible")
        start = self.client.get(reverse("activity_student", args=[self.course.pk, self.exam.pk]))
        self.assertContains(start, 'name="code"')
        response = self.client.post(reverse("attempt_start", args=[self.course.pk, self.exam.pk]), {"code": self.code})
        self.assertRedirects(response, reverse("attempt_form", args=[self.course.pk, self.exam.pk]))
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.exam.pk]))
        self.assertContains(page, "data-remaining-ms")
        self.assertNotContains(page, "data-pointer-url")

    def test_crear_una_evaluacion_en_el_editor(self):
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("activity_new", args=[self.course.pk]) + "?tipo=evaluacion")
        self.assertContains(page, "use: evaluation")
        self.client.post(reverse("activity_new", args=[self.course.pk]),
                         {"source": EVALUATION.replace("Prueba 1", "Recuperación ZQ")})
        exam = Activity.objects.get(title="Recuperación ZQ")
        self.assertTrue(exam.is_evaluation)
        self.assertEqual(exam.max_attempts, 1)
        self.client.get(reverse("activity_detail", args=[self.course.pk, exam.pk]))   # muestra los avisos
        clases = self.client.get(reverse("course_manage", args=[self.course.pk]))
        self.assertNotContains(clases, "Recuperación ZQ")                 # no es material de una unidad


DEBATE = r"""schema: policlase/v1
title: 'Deber con debate'
use: activity
slides:
  - item:
      id: DB-newton
      questions:
        - id: q1
          type: choice
          debate: true
          prompt: '¿Qué método converge más rápido cerca de una raíz simple?'
          options:
            - { text: 'Bisección' }
            - { text: 'Newton', correct: true }
  - solution: |
      ## Por qué Newton
      Converge cuadráticamente.
  - item:
      id: DB-suma
      questions:
        - { id: q1, type: numeric, prompt: '2 + 2', solution: 4, grading: { atol: 0 } }
  - solution: 'La suma da cuatro.'
"""


class DebateTests(ActivityBase):
    KEY = "DB-newton.q1"

    def setUp(self):
        super().setUp()
        from policlase_gen import document as content_format

        document, report, _use = content_format.load_text(DEBATE)
        self.assertEqual(report.errors, [])
        self.deb = Activity.objects.create(
            course=self.course, title="Debate", source=DEBATE, compiled=content_format.compile_content(document),
            category=self.deberes, published=True, due_at=timezone.now() + timedelta(days=2))
        record = next(i for i in self.deb.compiled["items"] if i.get("item_id") == "DB-newton")
        self.right = record["solutions"]["q1"]["correct"][0]
        self.wrong = next(o["key"] for o in record["questions"][0]["options"] if o["key"] != self.right)
        Enrollment.objects.filter(student=self.ana).update(preferred_name="Anita")
        User.objects.filter(pk=self.ana.pk).update(first_name="Ana María", last_name="Pérez Gómez")
        User.objects.filter(pk=self.bruno.pk).update(first_name="Bruno", last_name="De la Torre Vega")

    def send(self, student, choice, reason=""):
        self.client.force_login(student)
        self.client.post(reverse("attempt_start", args=[self.course.pk, self.deb.pk]))
        url = reverse("attempt_form", args=[self.course.pk, self.deb.pk])
        self.client.post(url, {"form": "attempt", f"a-{self.KEY}": choice, "a-DB-suma.q1": "4",
                               f"w-{self.KEY}": reason, "action": "submit"})
        return Attempt.objects.get(activity=self.deb, student=student)

    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("activity_student", args=[self.course.pk, self.deb.pk]))

    def close_due(self):
        Activity.objects.filter(pk=self.deb.pk).update(due_at=timezone.now() - timedelta(hours=1))
        self.deb.refresh_from_db()

    def test_la_justificacion_va_con_la_respuesta(self):
        self.client.force_login(self.ana)
        self.client.post(reverse("attempt_start", args=[self.course.pk, self.deb.pk]))
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.deb.pk]))
        self.assertContains(page, f'name="w-{self.KEY}"')
        self.assertContains(page, "con su nombre")
        attempt = self.send(self.ana, self.right, "Por la convergencia cuadrática")
        self.assertEqual(attempt.reasons, {self.KEY: "Por la convergencia cuadrática"})
        self.assertEqual(attempt.points, 2)

    def test_el_foro_se_abre_al_vencer_y_oculta_la_correccion(self):
        self.send(self.ana, self.right, "Por la convergencia cuadrática")
        self.send(self.bruno, self.wrong, "La bisección siempre converge")
        page = self.page(self.ana)
        self.assertNotContains(page, "La bisección siempre converge")      # antes del vencimiento, nadie lee a nadie
        self.assertContains(page, "El debate se abre el")
        self.close_due()
        page = self.page(self.ana)
        self.assertContains(page, "Bruno De la Torre")                       # nombre corto: primer nombre y apellido
        self.assertContains(page, "La bisección siempre converge")
        self.assertContains(page, "en debate")
        self.assertNotContains(page, "Por qué Newton")                       # la explicación espera al cierre
        self.assertContains(page, "La suma da cuatro.")                      # la de otro ítem, sí
        self.assertContains(page, "Ver la explicación")
        self.client.force_login(self.bruno)
        self.assertContains(self.page(self.bruno), "Anita Pérez")            # su nombre preferido

    def test_primero_se_publica_y_quien_puede_entregar_no_lee(self):
        carla = make_user("carla")
        Enrollment.objects.create(course=self.course, student=carla, status=Enrollment.Status.APPROVED)
        self.send(self.ana, self.right, "Por la convergencia cuadrática")
        self.close_due()
        page = self.page(carla)                                              # todavía puede entregar, atrasada
        self.assertContains(page, "envíe primero su entrega")
        self.assertNotContains(page, "Por la convergencia cuadrática")
        Activity.objects.filter(pk=self.deb.pk).update(late_policy=Activity.Late.BLOCK)
        page = self.page(carla)
        self.assertContains(page, "publique primero la suya")
        self.assertNotContains(page, "Por la convergencia cuadrática")
        self.client.post(reverse("debate_revise", args=[self.course.pk, self.deb.pk]),
                         {"key": self.KEY, f"a-{self.KEY}": self.wrong, f"w-{self.KEY}": "Creo que bisección"})
        page = self.page(carla)
        self.assertContains(page, "Por la convergencia cuadrática")
        self.assertContains(page, "publicada en el debate")

    def test_respuesta_revisada_sin_nota(self):
        attempt = self.send(self.ana, self.right, "Newton")
        self.close_due()
        self.client.force_login(self.ana)
        self.client.post(reverse("debate_revise", args=[self.course.pk, self.deb.pk]),
                         {"key": self.KEY, f"a-{self.KEY}": self.wrong, f"w-{self.KEY}": "Me convenció Bruno"})
        entry = DebateEntry.objects.get(student=self.ana)
        self.assertEqual((entry.value, entry.revised_value), (self.right, self.wrong))
        attempt.refresh_from_db()
        self.assertEqual(attempt.points, 2)                                  # la nota no cambia
        self.assertContains(self.page(self.ana), "Respuesta original:")
        debate.close(self.deb)
        page = self.page(self.ana)
        self.assertContains(page, "Su respuesta revisada no es correcta")
        self.assertContains(page, "Por qué Newton")                          # la síntesis, al cerrar
        self.assertNotContains(page, "en debate")

    def test_comentarios_y_me_gusta(self):
        self.send(self.ana, self.right, "Newton")
        self.send(self.bruno, self.wrong, "Bisección")
        self.close_due()
        self.page(self.ana)                                                  # crea las posturas
        ana_entry = DebateEntry.objects.get(student=self.ana)
        self.client.force_login(self.bruno)
        self.client.post(reverse("debate_reply", args=[self.course.pk, self.deb.pk]),
                         {"entry": ana_entry.pk, "body": "¿Y si la derivada es cero?"})
        reply = DebateReply.objects.get()
        url = reverse("debate_like", args=[self.course.pk, self.deb.pk])
        liked = self.client.post(url, {"entry": ana_entry.pk}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual(liked, {"count": 1, "liked": True})
        self.assertEqual(self.client.post(url, {"entry": ana_entry.pk}, HTTP_X_REQUESTED_WITH="fetch").json(),
                         {"count": 0, "liked": False})
        self.assertEqual(self.client.post(url, {"reply": reply.pk}, HTTP_X_REQUESTED_WITH="fetch").status_code, 409)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.post(url, {"reply": reply.pk}, HTTP_X_REQUESTED_WITH="fetch").json()["count"], 1)
        self.assertContains(self.page(self.ana), "¿Y si la derivada es cero?")
        self.client.post(reverse("debate_reply_delete", args=[self.course.pk, self.deb.pk, reply.pk]))
        self.assertTrue(DebateReply.objects.exists())                        # solo su autor lo elimina
        self.client.force_login(self.bruno)
        self.client.post(reverse("debate_reply_delete", args=[self.course.pk, self.deb.pk, reply.pk]))
        self.assertFalse(DebateReply.objects.exists())

    def test_el_docente_modera_y_cierra(self):
        self.send(self.ana, self.right, "Newton")
        self.send(self.bruno, self.wrong, "Un comentario fuera de lugar")
        self.close_due()
        self.client.force_login(self.teacher)
        url = reverse("debate_teacher", args=[self.course.pk, self.deb.pk])
        action = reverse("debate_teacher_action", args=[self.course.pk, self.deb.pk])
        self.assertContains(self.client.get(url), "Un comentario fuera de lugar")
        bruno_entry = DebateEntry.objects.get(student=self.bruno)
        self.client.post(action, {"action": "hide", "entry": bruno_entry.pk})
        self.assertNotContains(self.page(self.ana), "Un comentario fuera de lugar")
        self.assertContains(self.page(self.bruno), "oculta por el docente")
        self.client.force_login(self.teacher)
        self.client.post(action, {"action": "reply", "entry": DebateEntry.objects.get(student=self.ana).pk,
                                  "body": "Buena observación"})
        self.assertTrue(DebateReply.objects.filter(author=self.teacher).exists())
        self.client.post(action, {"action": "close"})
        self.deb.refresh_from_db()
        self.assertTrue(self.deb.debate_closed())
        self.client.force_login(self.ana)
        response = self.client.post(reverse("debate_reply", args=[self.course.pk, self.deb.pk]),
                                    {"entry": bruno_entry.pk, "body": "tarde"})
        self.assertEqual(DebateReply.objects.filter(author=self.ana).count(), 0)
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.teacher)
        self.client.post(action, {"action": "reopen"})
        self.deb.refresh_from_db()
        self.assertTrue(self.deb.debate_open())

    def test_el_debate_se_cierra_con_la_revision_y_se_configura_con_las_fechas(self):
        prueba = Milestone.objects.create(course=self.course, name="Prueba 1", on=timezone.localdate() + timedelta(days=9))
        self.client.force_login(self.teacher)
        url = reverse("activity_detail", args=[self.course.pk, self.deb.pk])
        self.assertContains(self.client.get(url), 'name="debate_mode"')     # hay preguntas de debate
        due = timezone.localtime() + timedelta(days=2)
        data = {"category": self.deberes.pk, "published": "on", "due_mode": "fixed",
                "due_at": due.strftime("%Y-%m-%dT%H:%M"), "review_mode": "milestone", "review_milestone": prueba.pk,
                "review_offset_days": "0", "review_time": "07:00", "late_policy": "penalty", "late_penalty": "30",
                "max_attempts": "3", "auto_submit": "on", "debate_mode": "review"}
        self.client.post(url, data)
        self.deb.refresh_from_db()
        self.assertEqual(self.deb.debate_close, self.deb.review_until)       # por omisión, con la revisión
        early = (due - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(url, {**data, "debate_mode": "fixed", "debate_until": early})
        self.assertContains(response, "posterior a la fecha de entrega")
        later = due + timedelta(days=3)
        self.client.post(url, {**data, "debate_mode": "fixed", "debate_until": later.strftime("%Y-%m-%dT%H:%M")})
        self.deb.refresh_from_db()
        self.assertEqual(timezone.localtime(self.deb.debate_close).date(), later.date())
        self.assertNotContains(self.client.get(reverse("activity_detail", args=[self.course.pk, self.activity.pk])),
                               'name="debate_mode"')                           # sin preguntas de debate, no aparece

    def test_aislamiento(self):
        self.send(self.ana, self.right, "Newton")
        self.close_due()
        other = make_user("otra", User.Role.TEACHER)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("debate_teacher", args=[self.course.pk, self.deb.pk])).status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.page(self.outsider).status_code, 404)
        self.assertEqual(self.client.post(reverse("debate_like", args=[self.course.pk, self.deb.pk]),
                                          {"entry": 1}).status_code, 404)
        self.client.force_login(self.teacher)                                # sin debate, no hay página
        self.assertEqual(self.client.get(reverse("debate_teacher", args=[self.course.pk, self.activity.pk])).status_code, 404)



PRACTICE = r"""schema: policlase/v1
title: 'Repaso para la Prueba 1'
use: practice
slides:
  - item:
      id: R-raiz
      questions:
        - { id: q1, type: numeric, prompt: 'Raíz de 2', solution: 1.4142, grading: { atol: 0.001 } }
        - id: q2
          type: choice
          prompt: '¿Orden de Newton?'
          options: [{ text: 'Cuadrático', correct: true }, { text: 'Lineal' }]
  - solution: 'Newton converge cuadráticamente.'
  - item:
      id: R-suma
      questions:
        - { id: q1, type: numeric, prompt: '2 + 2', solution: 4, grading: { atol: 0 } }
"""


class PracticeTests(ActivityBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_new", args=[self.course.pk]), {"source": PRACTICE})
        self.rep = Activity.objects.get(title="Repaso para la Prueba 1")
        Activity.objects.filter(pk=self.rep.pk).update(published=True)
        self.rep.refresh_from_db()
        item = next(i for i in self.rep.compiled["items"] if i.get("item_id") == "R-raiz")
        self.right = item["solutions"]["q2"]["correct"][0]

    def check(self, key, value, student=None):
        self.client.force_login(student or self.ana)
        url = reverse("attempt_check", args=[self.course.pk, self.rep.pk, key])
        return self.client.post(url, {"form": "attempt", f"a-{key}": value}, HTTP_X_REQUESTED_WITH="fetch")

    def test_nace_sin_nota_y_con_intentos_ilimitados(self):
        self.assertTrue(self.rep.is_practice)
        self.assertIsNone(self.rep.category)
        self.assertGreaterEqual(self.rep.max_attempts, 100)
        page = self.client.get(reverse("activity_new", args=[self.course.pk]) + "?tipo=repaso")
        self.assertContains(page, "use: practice")

    def test_comprobar_corrige_al_momento_y_abre_la_explicacion(self):
        engine.start(self.rep, self.ana)
        res = self.check("R-raiz.q1", "1.41")
        self.assertEqual(res.status_code, 200)
        self.assertIn("incorrecta", res.json()["html"])
        self.assertEqual(res.json()["explanation"], "")                     # falta la otra pregunta del ítem
        attempt = Attempt.objects.get()
        self.assertFalse(attempt.checked["R-raiz.q1"]["correct"])
        engine.save_draft(attempt, {"R-raiz.q1": "1.4142"})                  # lo comprobado no cambia
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers["R-raiz.q1"], "1.41")
        res = self.check("R-raiz.q2", self.right)
        self.assertIn("correcta", res.json()["html"])
        self.assertIn("Newton converge cuadráticamente.", res.json()["explanation"])
        self.assertEqual(self.check("R-suma.q1", "cuatro").status_code, 409)  # ilegible: se corrige antes
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.rep.pk]))
        self.assertContains(page, "Ver la explicación")
        self.assertContains(page, "Terminar el repaso")

    def test_terminar_y_repetir_sin_nota(self):
        attempt = engine.start(self.rep, self.ana)
        engine.save_draft(attempt, {"R-raiz.q1": "1.4142", "R-raiz.q2": self.right, "R-suma.q1": "5"})
        engine.submit(attempt)
        state = engine.state(self.rep, self.ana)
        self.assertTrue(state.reveal and state.solutions and state.can_start)
        self.client.force_login(self.ana)
        page = self.client.get(reverse("activity_student", args=[self.course.pk, self.rep.pk]))
        self.assertContains(page, "Repasar de nuevo")
        self.assertContains(page, "Newton converge cuadráticamente.")
        self.assertContains(page, "(sin nota)")
        course_page = self.client.get(reverse("course_detail", args=[self.course.pk]))
        self.assertContains(course_page, "Repaso")
        engine.start(self.rep, self.ana)                                      # otro intento
        self.assertEqual(Attempt.objects.filter(student=self.ana).count(), 2)
        self.assertIsNone(Activity.objects.get(pk=self.rep.pk).category)    # no cuenta en ninguna fila

    def test_abre_unos_dias_antes_de_su_fecha_clave(self):
        prueba = Milestone.objects.create(course=self.course, name="Prueba 1", on=timezone.localdate() + timedelta(days=10))
        self.client.force_login(self.teacher)
        self.client.post(reverse("activity_detail", args=[self.course.pk, self.rep.pk]), {
            "published": "on", "open_mode": "milestone", "due_milestone": prueba.pk, "days_before": 7})
        self.rep.refresh_from_db()
        self.assertEqual(timezone.localtime(self.rep.opens_at).date(), prueba.on - timedelta(days=7))
        self.assertIsNone(self.rep.due_at)
        Milestone.objects.filter(pk=prueba.pk).update(on=prueba.on + timedelta(days=2))
        prueba.refresh_from_db()
        gradebook.recompute_due_dates(prueba)
        self.rep.refresh_from_db()
        self.assertEqual(timezone.localtime(self.rep.opens_at).date(), prueba.on - timedelta(days=7))
        self.client.force_login(self.ana)                                     # todavía no abre
        self.assertEqual(self.client.get(reverse("activity_student", args=[self.course.pk, self.rep.pk])).status_code, 404)
        dates = self.client.get(reverse("activity_dates", args=[self.course.pk]))
        self.assertEqual(dates.status_code, 403)                               # el panel es del docente
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse("activity_dates", args=[self.course.pk])), "Repaso para la Prueba 1")

    def test_solo_en_un_repaso_se_comprueba(self):
        engine.start(self.activity, self.ana)
        self.client.force_login(self.ana)
        url = reverse("attempt_check", args=[self.course.pk, self.activity.pk, "D1-a.q1"])
        self.assertEqual(self.client.post(url, {"form": "attempt", "a-D1-a.q1": "1.4142"},
                                          HTTP_X_REQUESTED_WITH="fetch").status_code, 409)
        engine.start(self.rep, self.ana)
        self.client.force_login(self.outsider)
        url = reverse("attempt_check", args=[self.course.pk, self.rep.pk, "R-raiz.q1"])
        self.assertEqual(self.client.post(url, {"a-R-raiz.q1": "1"}).status_code, 404)


TIMED_LIST = r"""schema: policlase/v1
title: 'Python rápido'
use: activity
slides:
  - segment: { title: 'Rápidas', time_limit_s: 120 }
  - item:
      id: TL-numpy
      questions:
        - { id: q1, type: numeric_list, shape: [1, 2], prompt: 'A[1, 2] y A[:, 1].sum()', solution: [6, 15], grading: { atol: 0 } }
  - segment: {}
  - item:
      id: TL-exp
      questions:
        - { id: q1, type: numeric, prompt: 'Épsilon de float32', solution: 1.1920929e-7, grading: { rtol: 1.0e-3 } }
"""


class StudentReportsTests(ActivityBase):
    """Lo que reportaron los estudiantes en octubre de 2026: una pregunta con tiempo cerrada con una
    respuesta ilegible impedía enviar, y la notación científica solo se aceptaba como `1.5e-4`."""

    def setUp(self):
        super().setUp()
        from policlase_gen import document as content_format

        document, report, _use = content_format.load_text(TIMED_LIST)
        self.assertEqual(report.errors, [])
        self.quiz = Activity.objects.create(
            course=self.course, title="Python rápido", source=TIMED_LIST, compiled=content_format.compile_content(document),
            category=self.deberes, published=True, due_at=timezone.now() + timedelta(days=2))
        self.attempt = engine.start(self.quiz, self.ana)
        self.attempt = engine.open_timer(self.attempt, "TL-numpy.q1")

    def test_una_respuesta_ilegible_no_cierra_la_pregunta_con_tiempo(self):
        with self.assertRaisesMessage(engine.ActionError, "no es un número"):
            engine.close_timer(self.attempt, "TL-numpy.q1", {"TL-numpy.q1": ["[3, 5, 6, 7]", "[1, 5, 9]"]})
        self.attempt.refresh_from_db()
        self.assertNotIn("closed", self.attempt.timers["TL-numpy.q1"])      # sigue abierta para corregirla
        attempt = engine.close_timer(self.attempt, "TL-numpy.q1", {"TL-numpy.q1": ["6", "15"]})
        self.assertIn("closed", attempt.timers["TL-numpy.q1"])

    def test_una_pregunta_cerrada_e_ilegible_no_impide_enviar(self):
        Attempt.objects.filter(pk=self.attempt.pk).update(
            answers={"TL-numpy.q1": ["[3, 5]", "[1, 5]"], "TL-exp.q1": "1.19*10**-7"},
            timers={"TL-numpy.q1": {**self.attempt.timers["TL-numpy.q1"], "closed": timezone.now().isoformat()}})
        self.attempt.refresh_from_db()
        self.assertEqual(engine.format_errors(self.attempt), {})
        attempt = engine.submit(self.attempt)
        answers = {a.key: a for a in attempt.answer_rows.all()}
        self.assertEqual(answers["TL-numpy.q1"].points, 0)                 # ilegible: vale cero
        self.assertTrue(answers["TL-exp.q1"].correct)                       # 1.19*10**-7 es un número

    def test_avance_y_tiempo_por_pregunta(self):
        engine.save_draft(self.attempt, {"TL-exp.q1": "1.19e-7"},
                          telemetry={"q_s": {"TL-exp.q1": 40, "otra.q9": 7}, "blurs": 1})
        engine.save_draft(self.attempt, {}, telemetry={"q_s": {"TL-exp.q1": 5, "TL-numpy.q1": "x"}})
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.telemetry["q_s"], {"TL-exp.q1": 45})  # sin claves ajenas ni basura
        self.assertEqual(engine.progress_of(self.attempt), (1, 2))
        self.client.force_login(self.teacher)
        detail = self.client.get(reverse("activity_detail", args=[self.course.pk, self.quiz.pk]))
        self.assertContains(detail, "1 de 2 preguntas respondidas")
        engine.submit(self.attempt)
        page = self.client.get(reverse("activity_stats", args=[self.course.pk, self.quiz.pk]))
        self.assertContains(page, "Mediana del tiempo con la pregunta en pantalla")
        self.assertContains(page, "45 s")
        self.assertContains(page, "Con preguntas sin responder")
        self.assertContains(page, "Notas más altas")


class ReviewAfterPublishingTests(ActivityBase):
    def setUp(self):
        super().setUp()
        from policlase_gen import document as content_format

        document, _report, _use = content_format.load_text(EVALUATION)
        self.exam = Activity(course=self.course, title="Prueba 1", source=EVALUATION,
                             compiled=content_format.compile_content(document),
                             category=self.course.periods.first().categories.get(name="Prueba"), published=True,
                             opens_at=timezone.now() - timedelta(hours=3), due_at=timezone.now() - timedelta(hours=1))
        self.exam.set_kind("evaluation")
        self.exam.save()

    def test_la_revision_se_cuenta_desde_la_publicacion(self):
        tz = timezone.get_default_timezone()
        day = timezone.localtime(self.exam.due_at, tz).date()
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("activity_detail", args=[self.course.pk, self.exam.pk]), {
            "category": self.exam.category_id, "published": "on", "day_mode": "fixed", "day": day.isoformat(),
            "opens_time": "07:00", "due_time": "09:00", "require_code": "on",
            "review_mode": "published", "review_offset_days": "3", "review_time": "23:59"})
        self.assertEqual(response.status_code, 302)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.review_mode, Activity.Review.PUBLISHED)
        self.assertIsNone(self.exam.review_until)
        self.assertEqual(str(self.exam.review_rule), "hasta 3 días después de publicar las notas")
        Activity.objects.filter(pk=self.exam.pk).update(due_at=timezone.now() - timedelta(minutes=5))
        self.exam.refresh_from_db()
        published = timezone.now()
        engine.publish(self.exam, now=published)
        self.exam.refresh_from_db()
        expected = datetime.combine(timezone.localtime(published, tz).date() + timedelta(days=3), time(23, 59))
        self.assertEqual(timezone.localtime(self.exam.review_until, tz).replace(tzinfo=None), expected)
        self.assertTrue(self.exam.review_open(published + timedelta(days=1)))
        self.assertFalse(self.exam.review_open(published + timedelta(days=5)))
        engine.unpublish(self.exam)
        self.exam.refresh_from_db()
        self.assertIsNone(self.exam.review_until)

    def test_una_actividad_no_ofrece_la_opcion(self):
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("activity_detail", args=[self.course.pk, self.activity.pk]))
        self.assertNotContains(page, 'value="published"')
        exam_page = self.client.get(reverse("activity_detail", args=[self.course.pk, self.exam.pk]))
        self.assertContains(exam_page, 'value="published"')
