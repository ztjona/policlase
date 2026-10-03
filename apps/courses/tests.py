from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User

from .models import Course, Enrollment, normalize_code

Status = Enrollment.Status


def make_user(username, role=User.Role.STUDENT):
    return User.objects.create_user(username, f"{username}@example.ec", "clave-segura-2026",
                                    role=role, first_name=username.title(), last_name="Test")


class EnrollmentFlowTests(TestCase):
    def setUp(self):
        self.teacher = make_user("profe", User.Role.TEACHER)
        self.other_teacher = make_user("otra", User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="Métodos", code="MN-1")
        self.ana, self.bruno = make_user("ana"), make_user("bruno")

    def join(self, user, code):
        self.client.force_login(user)
        return self.client.post(reverse("course_join"), {"code": code})

    def test_el_codigo_crea_una_solicitud_pendiente(self):
        code = self.course.join_code_display.lower()      # con guion y minúsculas
        self.join(self.ana, code)
        e = Enrollment.objects.get(student=self.ana)
        self.assertEqual(e.status, Status.PENDING)

    def test_pendiente_no_ve_el_curso(self):
        self.join(self.ana, self.course.join_code)
        self.assertEqual(self.client.get(reverse("course_detail", args=[self.course.pk])).status_code, 404)

    def test_aprobado_ve_el_curso(self):
        Enrollment.objects.create(course=self.course, student=self.ana, status=Status.APPROVED)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("course_detail", args=[self.course.pk])).status_code, 200)

    def test_codigo_invalido_o_curso_cerrado(self):
        self.join(self.ana, "ZZZZ-ZZZZ")
        self.course.accepting = False
        self.course.save()
        self.join(self.ana, self.course.join_code)
        self.assertFalse(Enrollment.objects.exists())

    def test_docente_no_puede_inscribirse(self):
        self.assertEqual(self.join(self.other_teacher, self.course.join_code).status_code, 403)

    def test_solicitud_repetida_no_duplica(self):
        self.join(self.ana, self.course.join_code)
        self.join(self.ana, self.course.join_code)
        self.assertEqual(Enrollment.objects.count(), 1)

    def test_aprobar_todas(self):
        for u in (self.ana, self.bruno):
            Enrollment.objects.create(course=self.course, student=u)
        self.client.force_login(self.teacher)
        self.client.post(reverse("enrollment_decide", args=[self.course.pk]), {"action": "approve_all"})
        self.assertEqual(set(Enrollment.objects.values_list("status", flat=True)), {Status.APPROVED})

    def test_aprobar_y_rechazar_seleccionadas(self):
        ea = Enrollment.objects.create(course=self.course, student=self.ana)
        eb = Enrollment.objects.create(course=self.course, student=self.bruno)
        self.client.force_login(self.teacher)
        url = reverse("enrollment_decide", args=[self.course.pk])
        self.client.post(url, {"action": "approve", "enrollment": [ea.pk]})
        self.client.post(url, {"action": "reject", "enrollment": [eb.pk]})
        ea.refresh_from_db(); eb.refresh_from_db()
        self.assertEqual((ea.status, eb.status), (Status.APPROVED, Status.REJECTED))
        self.assertEqual(ea.decided_by, self.teacher)

    def test_otro_docente_no_ve_ni_decide_en_cursos_ajenos(self):
        e = Enrollment.objects.create(course=self.course, student=self.ana)
        self.client.force_login(self.other_teacher)
        self.assertEqual(self.client.get(reverse("course_manage", args=[self.course.pk])).status_code, 404)
        # ids forjados sobre su propio curso tampoco tocan matrículas ajenas
        own = Course.objects.create(owner=self.other_teacher, name="Otro", code="X-1")
        self.client.post(reverse("enrollment_decide", args=[own.pk]), {"action": "approve", "enrollment": [e.pk]})
        e.refresh_from_db()
        self.assertEqual(e.status, Status.PENDING)

    def test_estudiante_no_accede_al_panel_docente(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("course_manage", args=[self.course.pk])).status_code, 403)

    def test_regenerar_codigo_invalida_el_anterior(self):
        old = self.course.join_code
        self.client.force_login(self.teacher)
        self.client.post(reverse("join_code_action", args=[self.course.pk]), {"action": "regenerate"})
        self.join(self.ana, old)
        self.assertFalse(Enrollment.objects.exists())

    def test_retirar_estudiante(self):
        e = Enrollment.objects.create(course=self.course, student=self.ana, status=Status.APPROVED)
        self.client.force_login(self.teacher)
        self.client.post(reverse("enrollment_remove", args=[self.course.pk, e.pk]))
        self.assertFalse(Enrollment.objects.exists())

    def test_panel_muestra_pendientes(self):
        Enrollment.objects.create(course=self.course, student=self.ana)
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("course_manage", args=[self.course.pk]))
        self.assertContains(response, "Aprobar todas (1)")

    def test_frente_del_curso_sin_pendientes_no_muestra_inscripciones(self):
        Enrollment.objects.create(course=self.course, student=self.ana, status=Status.APPROVED)
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("course_manage", args=[self.course.pk]))
        self.assertNotContains(response, "Solicitudes pendientes")
        self.assertNotContains(response, self.course.join_code_display)   # el código vive en Configuración
        self.assertContains(response, reverse("course_settings", args=[self.course.pk]))

    def test_configuracion_del_curso(self):
        Enrollment.objects.create(course=self.course, student=self.ana, status=Status.APPROVED)
        self.client.force_login(self.teacher)
        url = reverse("course_settings", args=[self.course.pk])
        response = self.client.get(url)
        self.assertContains(response, self.course.join_code_display)
        self.assertContains(response, "Ana")
        self.client.post(url, {"name": "Métodos Numéricos", "code": "MN-2", "description": ""})
        self.course.refresh_from_db()
        self.assertEqual((self.course.name, self.course.code), ("Métodos Numéricos", "MN-2"))

    def test_otro_docente_no_ve_ni_edita_la_configuracion(self):
        self.client.force_login(self.other_teacher)
        url = reverse("course_settings", args=[self.course.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"name": "X", "code": "X"}).status_code, 404)
        self.course.refresh_from_db()
        self.assertEqual(self.course.name, "Métodos")

    def test_normalizar_codigo(self):
        self.assertEqual(normalize_code(" abcd-2345 "), "ABCD2345")


class StudentHomeTests(TestCase):
    def setUp(self):
        self.teacher = make_user("profe", User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="Métodos", code="MN-1")
        self.other = Course.objects.create(owner=self.teacher, name="Álgebra", code="AL-1")
        self.ana = make_user("ana")
        self.client.force_login(self.ana)

    def test_sin_cursos_la_inscripcion_esta_al_frente(self):
        self.assertContains(self.client.get(reverse("home")), 'name="code"')

    def test_con_cursos_la_inscripcion_pasa_al_menu(self):
        Enrollment.objects.create(course=self.course, student=self.ana, status=Status.APPROVED)
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, 'name="code"')
        self.assertContains(response, reverse("course_join"))           # en el menú de usuario
        self.assertEqual(self.client.get(reverse("course_join")).status_code, 200)

    def test_aprobada_no_se_anuncia_pendiente_si(self):
        Enrollment.objects.create(course=self.course, student=self.ana, status=Status.APPROVED)
        Enrollment.objects.create(course=self.other, student=self.ana)
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "Aprobada")
        self.assertContains(response, "Pendiente de aprobación")

    def test_rechazo_es_un_aviso_que_se_cierra(self):
        e = Enrollment.objects.create(course=self.course, student=self.ana, status=Status.REJECTED)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "fue rechazada")
        self.client.post(reverse("enrollment_dismiss", args=[e.pk]))
        self.assertNotContains(self.client.get(reverse("home")), "fue rechazada")
        # si el docente lo aprueba después, deja de ser un rechazo
        self.client.force_login(self.teacher)
        self.client.post(reverse("enrollment_decide", args=[self.course.pk]),
                         {"action": "approve", "enrollment": [e.pk], "back": "settings"})
        e.refresh_from_db()
        self.assertEqual((e.status, e.dismissed), (Status.APPROVED, False))

    def test_no_se_ocultan_avisos_ajenos(self):
        bruno = make_user("bruno")
        e = Enrollment.objects.create(course=self.course, student=bruno, status=Status.REJECTED)
        self.client.post(reverse("enrollment_dismiss", args=[e.pk]))
        e.refresh_from_db()
        self.assertFalse(e.dismissed)
