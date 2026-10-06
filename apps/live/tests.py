import json
from datetime import timedelta
from pathlib import Path

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from policlase_gen import deck as deckfmt

from apps.accounts.models import User
from apps.courses.models import Course, Enrollment

from . import engine
from .models import Deck, LiveSession, Response

DEMO = (Path(__file__).resolve().parent / "demo" / "biseccion-clase-1.yaml").read_text(encoding="utf-8")
Phase, Status = LiveSession.Phase, LiveSession.Status


def make_user(username, role=User.Role.STUDENT):
    return User.objects.create_user(username, f"{username}@example.ec", "clave-segura-2026", role=role)


class LiveBase(TestCase):
    def setUp(self):
        self.teacher = make_user("profe", User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="Métodos", code="MN-1")
        document, _ = deckfmt.load_deck_text(DEMO)
        compiled = deckfmt.compile_deck(document)
        self.deck = Deck.objects.create(course=self.course, title=compiled["title"],
                                        source=DEMO, compiled=compiled)
        self.ana, self.bruno, self.outsider = make_user("ana"), make_user("bruno"), make_user("zoe")
        for s in (self.ana, self.bruno):
            Enrollment.objects.create(course=self.course, student=s, status=Enrollment.Status.APPROVED)
        Enrollment.objects.create(course=self.course, student=self.outsider)  # pendiente

    def start(self):
        self.client.force_login(self.teacher)
        self.client.post(reverse("session_start", args=[self.course.pk, self.deck.pk]))
        return LiveSession.objects.get()

    def goto_first_question(self, session):
        engine.apply(session.pk, "start")
        return engine.apply(session.pk, "next")  # diapositiva 2: pregunta choice

    def right_key(self, session):
        return session.slide["solution"]["correct"][0]


class DeckTests(LiveBase):
    def test_guardar_presentacion_valida(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("deck_new", args=[self.course.pk]), {"source": DEMO})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Deck.objects.count(), 2)

    def test_errores_se_muestran_y_no_se_guarda(self):
        self.client.force_login(self.teacher)
        broken = DEMO.replace("type: choice", "type: open", 1)
        response = self.client.post(reverse("deck_new", args=[self.course.pk]), {"source": broken})
        self.assertContains(response, "E082")
        self.assertEqual(Deck.objects.count(), 1)

    def test_yaml_invalido_no_revienta(self):
        self.client.force_login(self.teacher)
        response = self.client.post(reverse("deck_new", args=[self.course.pk]), {"source": "slides: [ :"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "E060")

    def test_estudiante_no_edita_presentaciones(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("deck_new", args=[self.course.pk])).status_code, 403)


class TeacherIsolationTests(LiveBase):
    """Un docente no ve ni modifica nada de otro: presentaciones, sesiones ni resultados."""

    def test_otro_docente_no_toca_presentaciones_ni_sesiones(self):
        session = self.start()
        self.client.force_login(make_user("otra", User.Role.TEACHER))
        c, d, s = self.course.pk, self.deck.pk, session.pk
        for name, args in [("deck_view", [c, d]), ("deck_edit", [c, d]), ("deck_new", [c]),
                           ("present", [s]), ("present_fragment", [s]), ("session_results", [s])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404, name)
        for name, args in [("deck_edit", [c, d]), ("deck_delete", [c, d]),
                           ("session_start", [c, d]), ("present_action", [s])]:
            self.assertEqual(self.client.post(reverse(name, args=args),
                                              {"source": "x", "action": "end"}).status_code, 404, name)
        self.deck.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(self.deck.source, DEMO)
        self.assertNotEqual(session.status, Status.ENDED)


class StateMachineTests(LiveBase):
    def test_sesion_copia_las_diapositivas(self):
        session = self.start()
        self.deck.compiled = {"title": "x", "slides": []}
        self.deck.save()
        session.refresh_from_db()
        self.assertEqual(len(session.slides), 7)

    def test_una_sesion_activa_por_curso(self):
        first = self.start()
        self.client.post(reverse("session_start", args=[self.course.pk, self.deck.pk]))
        self.assertEqual(LiveSession.objects.active().count(), 1)
        self.assertEqual(LiveSession.objects.get(), first)

    def test_recorrido_completo_de_una_pregunta(self):
        session = self.goto_first_question(self.start())
        self.assertEqual(session.phase, Phase.READY)
        with self.assertRaises(engine.ActionError):
            engine.apply(session.pk, "reveal")
        session = engine.apply(session.pk, "open")
        self.assertEqual(session.phase, Phase.OPEN)
        with self.assertRaises(engine.ActionError):
            engine.apply(session.pk, "next")          # no se avanza con la pregunta abierta
        session = engine.apply(session.pk, "close")
        session = engine.apply(session.pk, "reveal")
        self.assertEqual(session.phase, Phase.RESULTS)
        session = engine.apply(session.pk, "next")
        session = engine.apply(session.pk, "prev")
        self.assertEqual(session.phase, Phase.RESULTS)  # volver a una pregunta ya abierta

    def test_cada_transicion_sube_la_version(self):
        session = self.start()
        before = session.state_version
        session = engine.apply(session.pk, "start")
        self.assertEqual(session.state_version, before + 1)

    def test_cierre_automatico_al_vencer(self):
        session = engine.apply(self.goto_first_question(self.start()).pk, "open")
        LiveSession.objects.filter(pk=session.pk).update(closes_at=timezone.now() - timedelta(seconds=1))
        self.assertTrue(engine.expire_if_due(session.pk))
        self.assertFalse(engine.expire_if_due(session.pk))  # idempotente
        session.refresh_from_db()
        self.assertEqual(session.phase, Phase.CLOSED)

    def test_accion_principal_la_decide_el_servidor(self):
        """Dos pulsaciones rápidas de espacio deben avanzar dos pasos, no repetir el primero."""
        session = self.start()
        steps = [engine.apply(session.pk, "primary") for _ in range(5)]
        self.assertEqual([(s.status, s.phase) for s in steps], [
            (Status.LIVE, Phase.CONTENT),      # iniciar
            (Status.LIVE, Phase.READY),        # siguiente → pregunta
            (Status.LIVE, Phase.OPEN),         # abrir
            (Status.LIVE, Phase.CLOSED),       # cerrar
            (Status.LIVE, Phase.RESULTS),      # revelar
        ])

    def test_llegar_a_la_sala_avisa_al_proyector(self):
        """Sin esto el proyector se quedaba en «0 conectados» mientras entraban estudiantes."""
        session = self.start()
        before = session.answers_version
        engine.join(session, self.ana)
        engine.join(session, self.ana)                 # reconectar no cuenta dos veces
        session.refresh_from_db()
        self.assertEqual(session.answers_version, before + 1)

    def test_terminar(self):
        session = engine.apply(self.start().pk, "end")
        self.assertEqual(session.status, Status.ENDED)
        with self.assertRaises(engine.ActionError):
            engine.apply(session.pk, "next")


class AnswerTests(LiveBase):
    def open_question(self):
        session = engine.apply(self.goto_first_question(self.start()).pk, "open")
        return session

    def answer(self, user, session, **data):
        self.client.force_login(user)
        return self.client.post(reverse("student_answer", args=[session.pk]), data)

    def test_respuesta_correcta_suma_puntos(self):
        session = self.open_question()
        self.answer(self.ana, session, choice=self.right_key(session))
        r = Response.objects.get(student=self.ana)
        self.assertTrue(r.correct)
        self.assertEqual(r.points, 1)
        self.assertEqual(r.item_id, "L01-bolzano")

    def test_solo_cuenta_la_primera_respuesta(self):
        session = self.open_question()
        wrong = [o["key"] for o in session.slide["question"]["options"] if o["key"] != self.right_key(session)][0]
        self.answer(self.ana, session, choice=wrong)
        response = self.answer(self.ana, session, choice=self.right_key(session))
        self.assertContains(response, "Ya respondió")
        self.assertEqual(Response.objects.get(student=self.ana).points, 0)

    def test_no_se_responde_fuera_de_tiempo(self):
        session = self.open_question()
        LiveSession.objects.filter(pk=session.pk).update(closes_at=timezone.now() - timedelta(seconds=1))
        self.answer(self.ana, session, choice=self.right_key(session))
        self.assertFalse(Response.objects.exists())

    def test_no_se_responde_si_la_pregunta_no_esta_abierta(self):
        session = self.goto_first_question(self.start())
        self.answer(self.ana, session, choice=self.right_key(session))
        self.assertFalse(Response.objects.exists())

    def test_pendiente_o_ajeno_no_entra(self):
        session = self.open_question()
        self.assertEqual(self.answer(self.outsider, session, choice="x").status_code, 404)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("live_student", args=[session.pk])).status_code, 404)

    def test_tipos_numeric_multi_y_verdadero_falso(self):
        session = self.goto_first_question(self.start())
        engine.apply(session.pk, "next")                         # contenido
        session = engine.apply(session.pk, "next")               # numeric
        session = engine.apply(session.pk, "open")
        self.answer(self.ana, session, value="10")
        self.assertEqual(Response.objects.get(student=self.ana, slide_index=session.index).points, 2)

        engine.apply(session.pk, "close")
        session = engine.apply(session.pk, "next")               # multi_choice
        session = engine.apply(session.pk, "open")
        keys = session.slide["solution"]["correct"]
        self.answer(self.ana, session, choice=keys)
        self.answer(self.bruno, session, choice=[o["key"] for o in session.slide["question"]["options"]])
        by = {r.student_id: r.points for r in Response.objects.filter(slide_index=session.index)}
        self.assertEqual(by[self.ana.pk], 2)
        self.assertLess(by[self.bruno.pk], 2)                    # marcar todo penaliza

        engine.apply(session.pk, "close")
        session = engine.apply(session.pk, "next")               # true_false
        session = engine.apply(session.pk, "open")
        answers = session.slide["solution"]["answers"]
        self.answer(self.ana, session, **{f"tf_{k}": "1" if v else "0" for k, v in answers.items()})
        self.assertEqual(Response.objects.get(student=self.ana, slide_index=session.index).points, 2)

    def test_marcador_y_resultados(self):
        session = self.open_question()
        self.answer(self.ana, session, choice=self.right_key(session))
        board = engine.leaderboard(session)
        self.assertEqual(board[0]["student"], self.ana.pk)
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("session_results", args=[session.pk]))
        self.assertContains(response, "ausente")                 # bruno no se conectó


class FragmentTests(LiveBase):
    """Cada fase se renderiza sin errores en ambas vistas."""

    def test_todas_las_fases(self):
        session = self.start()
        seen = set()

        def render_both():
            self.client.force_login(self.teacher)
            teacher = self.client.get(reverse("present_fragment", args=[session.pk]))
            self.client.force_login(self.ana)
            student = self.client.get(reverse("student_fragment", args=[session.pk]))
            self.assertEqual((teacher.status_code, student.status_code), (200, 200))
            s = LiveSession.objects.get(pk=session.pk)
            seen.add((s.status, s.phase))
            return teacher, student

        render_both()
        engine.apply(session.pk, "start")
        for _ in range(len(session.slides) - 1):
            s = LiveSession.objects.get(pk=session.pk)
            if s.phase == Phase.READY:
                engine.apply(session.pk, "open"); render_both()
                self.client.force_login(self.ana)
                engine.submit(session.pk, self.ana, _any_answer(s.slide))
                render_both()
                engine.apply(session.pk, "close"); render_both()
                engine.apply(session.pk, "reveal")
            render_both()
            engine.apply(session.pk, "next")
        render_both()
        engine.apply(session.pk, "end")
        teacher, student = render_both()
        self.assertContains(student, "Clase terminada")
        self.assertTrue({(Status.LOBBY, Phase.CONTENT), (Status.LIVE, Phase.OPEN),
                         (Status.LIVE, Phase.RESULTS), (Status.ENDED, Phase.CONTENT)} <= seen)


def _any_answer(slide):
    q = slide["question"]
    if q["type"] == "choice":
        return q["options"][0]["key"]
    if q["type"] == "multi_choice":
        return [q["options"][0]["key"]]
    if q["type"] == "true_false":
        return {s["key"]: True for s in q["statements"]}
    return "1"


class SSETests(LiveBase):
    async def read_first_event(self, user, session_pk):
        await self.async_client.aforce_login(user)
        response = await self.async_client.get(reverse("live_events", args=[session_pk]))
        if response.status_code != 200:
            return response.status_code, None
        chunks = []
        async for chunk in response.streaming_content:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
            if any(c.startswith("data:") for c in chunks):
                break
        return 200, json.loads(next(c for c in chunks if c.startswith("data:"))[5:])

    async def test_el_flujo_envia_el_estado(self):
        from asgiref.sync import sync_to_async
        session = await sync_to_async(self.start)()
        status, data = await self.read_first_event(self.ana, session.pk)
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "lobby")

    async def test_el_flujo_rechaza_a_quien_no_esta_aprobado(self):
        from asgiref.sync import sync_to_async
        session = await sync_to_async(self.start)()
        status, _ = await self.read_first_event(self.outsider, session.pk)
        self.assertEqual(status, 403)


class GuestTests(LiveBase):
    """Clase abierta: entrar sin cuenta con el enlace, sin acceder a nada más."""

    def open_session(self, allow=True):
        session = self.start()
        if allow:
            engine.apply(session.pk, "guests_on")
        self.client.logout()
        session.refresh_from_db()
        return session

    def enter(self, session, name="Visitante"):
        return self.client.post(reverse("live_guest", args=[session.guest_token]), {"name": name})

    def test_invitado_entra_responde_y_aparece_en_resultados(self):
        session = self.open_session()
        response = self.enter(session, "María")
        self.assertRedirects(response, reverse("live_student", args=[session.pk]))
        guest = User.objects.get(role=User.Role.GUEST)
        self.assertEqual(guest.first_name, "María")
        self.assertFalse(guest.has_usable_password())

        session = self.goto_first_question(session)
        engine.apply(session.pk, "open")
        session.refresh_from_db()
        self.client.post(reverse("student_answer", args=[session.pk]), {"choice": self.right_key(session)})
        self.assertEqual(Response.objects.get().student, guest)

        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse("session_results", args=[session.pk])), "María")

    def test_clase_cerrada_a_invitados(self):
        session = self.open_session(allow=False)
        self.assertContains(self.client.get(reverse("live_guest", args=[session.guest_token])),
                            "no está abierta a invitados")
        self.enter(session)
        self.assertFalse(User.objects.filter(role=User.Role.GUEST).exists())

    def test_cerrar_a_invitados_les_quita_el_acceso(self):
        session = self.open_session()
        self.enter(session)
        engine.apply(session.pk, "guests_off")
        self.assertEqual(self.client.get(reverse("student_fragment", args=[session.pk])).status_code, 404)

    def test_el_pin_no_basta_para_invitados(self):
        session = self.open_session()
        self.enter(session)
        # el token es el permiso; la clase de otro curso con el mismo invitado no se abre
        other = Course.objects.create(owner=self.teacher, name="Otro", code="X-1")
        other_session = LiveSession.objects.create(course=other, title="x", slides=session.slides,
                                                   allow_guests=True)
        self.assertEqual(self.client.get(reverse("live_student", args=[other_session.pk])).status_code, 404)

    def test_invitado_queda_confinado_a_la_clase(self):
        session = self.open_session()
        self.enter(session)
        for url in (reverse("home"), reverse("course_detail", args=[self.course.pk]),
                    reverse("account_preferences"), reverse("course_join")):
            self.assertRedirects(self.client.get(url), reverse("live_student", args=[session.pk]),
                                 fetch_redirect_response=False)

    def test_estudiante_inscrito_con_el_enlace_entra_como_si_mismo(self):
        session = self.open_session()
        self.client.force_login(self.ana)
        self.client.get(reverse("live_guest", args=[session.guest_token]))
        self.assertFalse(User.objects.filter(role=User.Role.GUEST).exists())

    def test_proyector_muestra_enlace_y_qr(self):
        session = self.open_session()
        self.client.force_login(self.teacher)
        page = self.client.get(reverse("present_fragment", args=[session.pk]))
        self.assertContains(page, session.guest_token)
        self.assertContains(page, "<svg")

    def test_clase_terminada(self):
        session = self.open_session()
        engine.apply(session.pk, "end")
        self.assertContains(self.client.get(reverse("live_guest", args=[session.guest_token])), "ya terminó")


class PreviewTests(LiveBase):
    def preview(self, source, user=None):
        self.client.force_login(user or self.teacher)
        return self.client.post(reverse("deck_preview", args=[self.course.pk]), {"source": source})

    def test_vista_previa_renderiza_y_ubica_diapositivas(self):
        data = self.preview(DEMO).json()
        self.assertEqual(data["diagnostics"], [])
        self.assertIn("slide-card", data["html"])
        self.assertEqual(len(data["lines"]), len(self.deck.slides))
        self.assertTrue(all(isinstance(n, int) for n in data["lines"]))

    def test_vista_previa_con_errores(self):
        data = self.preview(DEMO.replace("type: choice", "type: open", 1)).json()
        self.assertIsNone(data["html"])
        self.assertIn("E082", [d["code"] for d in data["diagnostics"]])

    def test_otro_docente_no_usa_la_vista_previa_de_cursos_ajenos(self):
        self.assertEqual(self.preview(DEMO, make_user("otra", User.Role.TEACHER)).status_code, 404)


class ThemeTests(LiveBase):
    def test_proyector_claro_por_defecto_y_oscuro_si_se_elige(self):
        session = self.start()
        page = self.client.get(reverse("present", args=[session.pk]))
        self.assertContains(page, 'data-theme="light"')
        self.client.post(reverse("set_theme"), {"theme": "dark", "next": "/"})
        self.assertContains(self.client.get(reverse("present", args=[session.pk])), 'data-theme="dark"')
        self.assertContains(self.client.get(reverse("home")), 'data-theme="dark"')
