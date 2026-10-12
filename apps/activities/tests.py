import io
import shutil
import tempfile
from datetime import date, time, timedelta
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

from . import engine
from .models import Activity, Answer, Attempt, Upload

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
        notes = [i for i in result.issues if i["path"].endswith("prueba.yaml")]
        self.assertIn("evaluación", notes[0]["note"])

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

    def test_bloquear_pegar_marca_las_abiertas(self):
        Activity.objects.filter(pk=self.activity.pk).update(block_paste=True)
        engine.start(self.activity, self.ana)
        self.client.force_login(self.ana)
        page = self.client.get(reverse("attempt_form", args=[self.course.pk, self.activity.pk]))
        self.assertContains(page, "data-no-paste")
        self.assertNotContains(page, 'name="a-D1-a.q1" value="" autocomplete="off" data-no-paste')


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
        engine.save_draft(attempt, {"T-barra.q1": "/"}, now=late)         # se acabó: no cambia
        attempt.refresh_from_db()
        self.assertEqual(attempt.answers["T-barra.q1"], "\\")
        self.assertEqual(engine.timer(attempt, question, now=late).state, "closed")
        attempt = engine.open_timer(attempt, "T-barra.q1", now=late)      # abrir otra vez no reinicia
        self.assertEqual(attempt.timers["T-barra.q1"]["opened"], start.isoformat())

        engine.save_draft(attempt, {self.survey_key: self.latina, "T-suma.q1": "4"})
        attempt = engine.submit(attempt)
        answers = {a.key: a for a in attempt.answer_rows.all()}
        self.assertTrue(answers["T-barra.q1"].correct)
        self.assertEqual(answers[self.survey_key].points, 0)              # la encuesta no da puntos
        self.assertEqual((attempt.points, attempt.max_points), (2, 2))

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
