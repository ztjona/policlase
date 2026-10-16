import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from policlase_gen import activity as activityfmt

from apps.accounts.models import User
from apps.activities import engine
from apps.activities.models import Activity
from apps.courses.models import Course, Enrollment

from . import deepseek, grading
from .models import AiAccount, AiProposal

SOURCE = r"""schema: policlase.activity/v1
title: 'Deber IA'
items:
  - id: IA-a
    stem: 'Sea $f(x) = x^2 - 2$.'
    questions:
      - id: q1
        type: open
        points: 2
        prompt: 'Explique por qué Newton converge rápido.'
        grading:
          mode: ai
          reference: 'Convergencia cuadrática cerca de una raíz simple.'
          rubric:
            - { criterion: 'Menciona la convergencia cuadrática', points: 1 }
            - { criterion: 'Lo relaciona con la derivada', points: 1 }
"""

REPLY = json.dumps({"criterios": [{"criterio": "Menciona la convergencia cuadrática", "puntos": 1, "motivo": "Lo dice."},
                                  {"criterio": "Lo relaciona con la derivada", "puntos": 7, "motivo": "Excede."}],
                    "retroalimentacion": "Usted explica bien la convergencia.", "confianza": "alta"})


def make_user(username, role=User.Role.STUDENT):
    return User.objects.create_user(username, f"{username}@example.ec", "clave-segura-2026", role=role,
                                    first_name="Ana", last_name="Pérez Gómez")


class AiBase(TestCase):
    def setUp(self):
        self.teacher = make_user("profe", User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="Métodos Numéricos", code="MN-1")
        document, _ = activityfmt.load_activity_text(SOURCE)
        self.activity = Activity.objects.create(course=self.course, title="Deber IA", source=SOURCE, published=True,
                                                compiled=activityfmt.compile_activity(document),
                                                due_at=timezone.now() + timedelta(days=1))
        self.ana = make_user("ana")
        self.ana.student_id = "1712345678"
        self.ana.save()
        Enrollment.objects.create(course=self.course, student=self.ana, status=Enrollment.Status.APPROVED)
        attempt = engine.start(self.activity, self.ana)
        engine.save_draft(attempt, {"IA-a.q1": "Porque el error se eleva al cuadrado."})
        self.attempt = engine.submit(attempt)
        self.answer = self.attempt.answer_rows.get(key="IA-a.q1")
        self.account = AiAccount(user=self.teacher, enabled=True)
        self.account.key = "sk-prueba-1234567890"
        self.account.save()


class AccountTests(AiBase):
    def test_la_clave_se_guarda_cifrada_y_no_se_muestra(self):
        self.assertNotIn("sk-prueba", self.account.key_encrypted)
        self.assertEqual(AiAccount.objects.get().key, "sk-prueba-1234567890")
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("ai_account"))
        self.assertNotContains(page, "sk-prueba-1234567890")
        self.assertContains(page, "…7890")
        self.client.post(reverse("ai_account"), {"action": "save", "key": "", "model": "deepseek-chat"})
        self.assertEqual(AiAccount.objects.get().key, "sk-prueba-1234567890")      # vacío: se conserva
        self.assertFalse(AiAccount.objects.get().enabled)                           # casilla sin marcar
        self.client.post(reverse("ai_account"), {"action": "remove"})
        self.assertEqual(AiAccount.objects.get().key_encrypted, "")
        student = self.client
        student.force_login(self.ana)
        self.assertNotEqual(student.get(reverse("ai_account")).status_code, 200)


class ProposalTests(AiBase):
    def test_el_prompt_lleva_el_contexto_y_no_identifica_al_estudiante(self):
        item, question, record = engine._records(self.attempt.content)["IA-a.q1"]
        sent = grading.messages(self.activity, item, question, record, self.answer.value)
        text = json.dumps(sent, ensure_ascii=False)
        for expected in ("Métodos Numéricos", "Deber IA", "Menciona la convergencia cuadrática",
                         "Convergencia cuadrática cerca", "el error se eleva al cuadrado", "<<<RESPUESTA"):
            self.assertIn(expected, text)
        for private in ("Pérez", "1712345678", "ana@example.ec"):
            self.assertNotIn(private, text)
        # Todas las respuestas de la pregunta comparten el comienzo (lo que DeepSeek guarda en caché).
        other = grading.messages(self.activity, item, question, record, "Otra respuesta")
        self.assertEqual(sent[0], other[0])
        self.assertTrue(other[1]["content"].startswith(sent[1]["content"].split("Respuesta del estudiante")[0]))

    def test_la_propuesta_respeta_los_maximos_y_llena_el_formulario(self):
        usage = {"prompt_tokens": 900, "prompt_cache_hit_tokens": 800, "completion_tokens": 120}
        with mock.patch.object(deepseek, "complete", return_value=(REPLY, usage)) as call:
            self.client.force_login(self.teacher)
            response = self.client.post(reverse("ai_propose", args=[self.course.pk, self.activity.pk, self.answer.pk]),
                                        HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["points"], 2.0)                 # 1 + min(7, 1)
        self.assertEqual(call.call_args.args[1], "deepseek-chat")
        proposal = AiProposal.objects.get()
        self.assertEqual((proposal.tokens_cached, proposal.prompt_version), (800, grading.PROMPT_VERSION))
        self.answer.refresh_from_db()
        self.assertTrue(self.answer.needs_review)                         # nada cambia hasta que el docente guarda
        page = self.client.get(reverse("question_review", args=[self.course.pk, self.activity.pk, "IA-a.q1"]))
        self.assertContains(page, "Propuesta de la IA")
        self.assertContains(page, "Usted explica bien la convergencia.")
        self.client.post(reverse("activity_answer_grade", args=[self.course.pk, self.activity.pk, self.answer.pk]),
                         {"points": "1.5", "comment": "Usted explica bien la convergencia."})
        proposal.refresh_from_db()
        self.assertIsNotNone(proposal.applied_at)
        self.answer.refresh_from_db()
        self.assertEqual((self.answer.points, self.answer.needs_review), (1.5, False))

    def test_lote_y_errores(self):
        self.client.force_login(self.teacher)
        url = reverse("ai_pending", args=[self.course.pk, self.activity.pk])
        listed = self.client.post(url, {"key": ["IA-a.q1"]}, HTTP_X_REQUESTED_WITH="fetch").json()
        self.assertEqual(listed["answers"], [self.answer.pk])
        with mock.patch.object(deepseek, "complete", side_effect=deepseek.AiError("Sin saldo")):
            response = self.client.post(reverse("ai_propose", args=[self.course.pk, self.activity.pk, self.answer.pk]),
                                        HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual((response.status_code, response.json()["error"]), (409, "Sin saldo"))
        with mock.patch.object(deepseek, "complete", return_value=(REPLY, {})):
            self.client.post(url, {"key": ["IA-a.q1"]})                    # sin JavaScript: procesa unas pocas
        self.assertEqual(self.client.post(url, {"key": ["IA-a.q1"]}, HTTP_X_REQUESTED_WITH="fetch").json()["answers"], [])
        detail = self.client.get(reverse("activity_detail", args=[self.course.pk, self.activity.pk]))
        self.assertContains(detail, "Generar propuestas con IA")

    def test_sin_clave_no_se_consulta_y_es_del_docente(self):
        AiAccount.objects.update(enabled=False)
        self.client.force_login(self.teacher)
        with mock.patch.object(deepseek, "complete") as call:
            response = self.client.post(reverse("ai_propose", args=[self.course.pk, self.activity.pk, self.answer.pk]),
                                        HTTP_X_REQUESTED_WITH="fetch")
        call.assert_not_called()
        self.assertEqual(response.status_code, 409)
        other = make_user("otra", User.Role.TEACHER)
        self.client.force_login(other)
        response = self.client.post(reverse("ai_propose", args=[self.course.pk, self.activity.pk, self.answer.pk]))
        self.assertEqual(response.status_code, 404)

    def test_respuesta_con_texto_alrededor(self):
        _item, question, _record = engine._records(self.attempt.content)["IA-a.q1"]
        points, feedback, criteria, _conf = grading.parse("Aquí está:\n" + REPLY + "\nFin.", question)
        self.assertEqual((points, len(criteria)), (2.0, 2))
