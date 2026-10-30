import re
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.courses.models import Course, Enrollment

from .models import TeacherInvitation, User

PASSWORD = "clave-segura-2026"


def signup_data(**over):
    data = {"first_name": "Ana", "last_name": "Pérez", "student_id": "1700000001",
            "email": "ana@example.ec", "username": "ana",
            "password1": PASSWORD, "password2": PASSWORD}
    data.update(over)
    return data


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SignupTests(TestCase):
    def setUp(self):
        cache.clear()           # allauth limita un correo de confirmación por dirección cada 3 minutos
        self.teacher = User.objects.create_user("docente", "docente@example.ec", PASSWORD, role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, name="Métodos", code="MN-1")

    def data(self, **over):
        return signup_data(course_code=self.course.join_code, **over)

    def test_registro_crea_estudiante_que_entra_sin_confirmar_el_correo(self):
        # La confirmación es opcional para estudiantes: entra de inmediato y el correo se envía igual.
        response = self.client.post(reverse("account_signup"), self.data())
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertIn("/cuenta/confirm-email/", mail.outbox[-1].body)

        user = User.objects.get(username="ana")
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertEqual((user.first_name, user.last_name, user.student_id), ("Ana", "Pérez", "1700000001"))
        self.assertFalse(EmailAddress.objects.get(user=user).verified)
        self.assertContains(self.client.get(reverse("home")), reverse("account_email"))   # el recordatorio

        self.client.logout()
        response = self.client.post(reverse("account_login"), {"login": "ana", "password": PASSWORD})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_el_enlace_de_verificacion_activa_la_cuenta(self):
        self.client.post(reverse("account_signup"), self.data())
        link = re.search(r"https?://\S+/cuenta/confirm-email/\S+/", mail.outbox[-1].body).group(0)
        path = link.split("://", 1)[1].split("/", 1)[1]
        self.client.post("/" + path)
        self.assertTrue(EmailAddress.objects.get(email="ana@example.ec").verified)

        self.client.logout()
        response = self.client.post(reverse("account_login"), {"login": "ana@example.ec", "password": PASSWORD})
        self.assertRedirects(response, reverse("home"))

    def test_contrasena_debil_rechazada(self):
        response = self.client.post(reverse("account_signup"), self.data(password1="12345678", password2="12345678"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="ana").exists())

    def test_cedula_invalida_rechazada(self):
        self.client.post(reverse("account_signup"), self.data(student_id="12 34"))
        self.assertFalse(User.objects.filter(username="ana").exists())

    def test_el_registro_de_estudiante_no_crea_docentes(self):
        self.client.post(reverse("account_signup"), self.data(role="teacher", as_teacher="1"))
        self.assertEqual(User.objects.get(username="ana").role, User.Role.STUDENT)

    # ------------------------------------------------------------ código del curso

    def test_el_registro_crea_la_solicitud_al_curso(self):
        response = self.client.post(reverse("account_signup"), signup_data(
            course_code=self.course.join_code_display.lower()), follow=True)   # con guion y minúsculas
        enrollment = Enrollment.objects.get(student__username="ana")
        self.assertEqual((enrollment.course, enrollment.status), (self.course, Enrollment.Status.PENDING))
        self.assertContains(response, "Solicitud enviada")

    def test_sin_codigo_valido_no_hay_cuenta_ni_correo(self):
        closed = Course.objects.create(owner=self.teacher, name="Cerrado", code="C-1", accepting=False)
        expired = Course.objects.create(owner=self.teacher, name="Vencido", code="C-2",
                                        accepting_until=timezone.now() - timedelta(minutes=1))
        for code in ("", "ZZZZ-ZZZZ", "xqzwvplmkj", closed.join_code, expired.join_code):
            with self.subTest(code=code):
                response = self.client.post(reverse("account_signup"), signup_data(course_code=code))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'name="course_code"')
                self.assertFalse(User.objects.filter(username="ana").exists())
                self.assertEqual(mail.outbox, [])

    def test_codigo_invalido_con_correo_ya_registrado_tampoco_envia_correo(self):
        # Con un código válido, allauth avisa por correo a la dirección ya registrada; sin él, nada.
        User.objects.create_user("otra", "ana@example.ec", PASSWORD)
        self.client.post(reverse("account_signup"), signup_data(course_code="ZZZZ-ZZZZ"))
        self.assertEqual(mail.outbox, [])

    def test_el_enlace_de_inscripcion_completa_el_codigo(self):
        url = reverse("course_join_link", args=[self.course.join_code])
        response = self.client.get(url, follow=True)
        self.assertEqual(response.redirect_chain[-1][0],
                         f"{reverse('account_signup')}?codigo={self.course.join_code}")
        self.assertContains(response, f'value="{self.course.join_code}"')
        self.assertContains(response, "Métodos")
        # Quien ya tiene cuenta inicia sesión y llega a inscribirse con el código completado.
        self.assertContains(response, f"next=%2Funirse%2F%3Fcodigo%3D{self.course.join_code}")

        self.client.force_login(User.objects.create_user("bruno", "bruno@example.ec", PASSWORD))
        response = self.client.get(url, follow=True)
        self.assertContains(response, f'value="{self.course.join_code}"')
        self.assertContains(response, "Solicitar inscripción")

    def test_el_enlace_de_un_curso_cerrado_lo_avisa_antes_de_llenar_el_formulario(self):
        self.course.accepting = False
        self.course.save()
        response = self.client.get(reverse("course_join_link", args=[self.course.join_code]), follow=True)
        self.assertContains(response, "ningún curso abierto")
        self.assertNotContains(response, "Métodos")       # no se confirma qué curso tiene ese código

    # ------------------------------------------------------------ invitación de docentes

    def invitation(self, **over):
        return TeacherInvitation.objects.create(created_by=self.teacher, **over)

    def invited_url(self, invitation):
        return reverse("teacher_signup", args=[invitation.token])

    def teacher_data(self, **over):
        return signup_data(username="nueva", email="nueva@example.ec", student_id="", **over)

    def test_registro_de_docente_con_invitacion_sin_cedula_y_con_verificacion(self):
        invitation = self.invitation()
        response = self.client.post(self.invited_url(invitation), self.teacher_data())
        self.assertRedirects(response, reverse("account_email_verification_sent"))

        user = User.objects.get(username="nueva")
        self.assertTrue(user.is_teacher)
        self.assertEqual(user.student_id, "")
        self.assertFalse(Enrollment.objects.filter(student=user).exists())
        self.assertFalse(EmailAddress.objects.get(user=user).verified)
        self.assertIn("/cuenta/confirm-email/", mail.outbox[-1].body)
        invitation.refresh_from_db()
        self.assertEqual(invitation.used_by, user)

        # Para docentes sigue siendo obligatoria: sin confirmar, iniciar sesión no da acceso.
        self.client.logout()
        response = self.client.post(reverse("account_login"), {"login": "nueva", "password": PASSWORD})
        self.assertRedirects(response, reverse("account_email_verification_sent"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_sin_invitacion_no_hay_formulario_de_docente(self):
        page = self.client.get(reverse("teacher_signup_closed"))
        self.assertContains(page, "por invitación")
        self.assertNotContains(page, 'name="username"')
        self.assertEqual(self.client.post(reverse("teacher_signup_closed"), self.teacher_data()).status_code, 405)
        self.assertFalse(User.objects.filter(username="nueva").exists())
        self.assertEqual(mail.outbox, [])

    def test_invitacion_invalida_usada_o_vencida_no_crea_cuenta(self):
        used = self.invitation(used_at=timezone.now())
        expired = self.invitation(expires_at=timezone.now() - timedelta(minutes=1))
        for url in (reverse("teacher_signup", args=["no-existe"]), self.invited_url(used), self.invited_url(expired)):
            with self.subTest(url=url):
                self.assertNotContains(self.client.get(url), 'name="username"', status_code=404)
                self.assertEqual(self.client.post(url, self.teacher_data()).status_code, 404)
                self.assertFalse(User.objects.filter(username="nueva").exists())
                self.assertEqual(mail.outbox, [])

    def test_una_invitacion_sirve_una_sola_vez(self):
        url = self.invited_url(self.invitation())
        self.client.post(url, self.teacher_data())
        self.client.logout()
        response = self.client.post(url, signup_data(username="tercera", email="tercera@example.ec", student_id=""))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(User.objects.filter(username="tercera").exists())

    def test_correo_ya_registrado_no_gasta_la_invitacion(self):
        User.objects.create_user("otra", "nueva@example.ec", PASSWORD)
        invitation = self.invitation()
        self.client.post(self.invited_url(invitation), self.teacher_data())
        invitation.refresh_from_db()
        self.assertTrue(invitation.is_usable)
        self.assertFalse(User.objects.filter(username="nueva").exists())

    # ------------------------------------------------------------ campo trampa

    def test_el_campo_trampa_no_crea_la_cuenta_ni_envia_correo(self):
        invitation = self.invitation()
        for url, data in ((reverse("account_signup"), self.data(comentario="xqzwvplmkj")),
                          (self.invited_url(invitation), self.teacher_data(comentario="xqzwvplmkj"))):
            with self.subTest(url):
                self.assertContains(self.client.get(url), 'name="comentario"')
                response = self.client.post(url, data)
                # Misma respuesta que un registro con confirmación pendiente: el bot no se entera.
                self.assertRedirects(response, reverse("account_email_verification_sent"))
                self.assertEqual(User.objects.filter(pk__gt=self.teacher.pk).count(), 0)
                self.assertEqual(mail.outbox, [])
                self.assertNotIn("_auth_user_id", self.client.session)
        invitation.refresh_from_db()
        self.assertTrue(invitation.is_usable)

    def test_el_campo_trampa_vacio_no_afecta_el_registro(self):
        self.client.post(reverse("account_signup"), self.data(comentario=""))
        self.assertTrue(User.objects.filter(username="ana").exists())

    def test_recuperar_contrasena_envia_correo(self):
        User.objects.create_user("ana", "ana@example.ec", PASSWORD)
        self.client.post(reverse("account_reset_password"), {"email": "ana@example.ec"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/cuenta/password/reset/key/", mail.outbox[0].body)

    def test_contrasenas_con_argon2(self):
        user = User.objects.create_user("ana", "ana@example.ec", PASSWORD)
        self.assertTrue(user.password.startswith("argon2"))


class TeacherInvitationPageTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user("docente", "docente@example.ec", PASSWORD, role=User.Role.TEACHER)
        self.other = User.objects.create_user("otra", "otra@example.ec", PASSWORD, role=User.Role.TEACHER)
        self.student = User.objects.create_user("ana", "ana@example.ec", PASSWORD)
        self.url = reverse("teacher_invitations")

    def test_el_docente_crea_y_ve_su_enlace(self):
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(reverse("home")), self.url)     # en el menú de usuario
        response = self.client.post(self.url, follow=True)
        invitation = TeacherInvitation.objects.get(created_by=self.teacher)
        self.assertTrue(invitation.is_usable)
        self.assertContains(response, reverse("teacher_signup", args=[invitation.token]))

    def test_el_estudiante_no_puede_invitar(self):
        self.client.force_login(self.student)
        self.assertNotContains(self.client.get(reverse("home")), self.url)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertFalse(TeacherInvitation.objects.exists())

    def test_sin_sesion_no_se_puede_invitar(self):
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertFalse(TeacherInvitation.objects.exists())

    def test_aislamiento_otro_docente_no_ve_ni_revoca(self):
        invitation = TeacherInvitation.objects.create(created_by=self.teacher)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.url), invitation.token)
        response = self.client.post(reverse("teacher_invitation_revoke", args=[invitation.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(TeacherInvitation.objects.filter(pk=invitation.pk).exists())

    def test_revocar_deja_el_enlace_sin_efecto(self):
        invitation = TeacherInvitation.objects.create(created_by=self.teacher)
        self.client.force_login(self.teacher)
        self.client.post(reverse("teacher_invitation_revoke", args=[invitation.pk]))
        self.client.logout()
        self.assertEqual(self.client.get(reverse("teacher_signup", args=[invitation.token])).status_code, 404)

    def test_la_invitacion_usada_no_se_revoca_y_dice_a_quien_se_invito(self):
        invitation = TeacherInvitation.objects.create(created_by=self.teacher, used_by=self.other, used_at=timezone.now())
        self.client.force_login(self.teacher)
        self.assertContains(self.client.get(self.url), self.other.display_name)
        response = self.client.post(reverse("teacher_invitation_revoke", args=[invitation.pk]))
        self.assertEqual(response.status_code, 404)

    def test_el_enlace_vencido_no_aparece(self):
        TeacherInvitation.objects.create(created_by=self.teacher, expires_at=timezone.now() - timedelta(minutes=1))
        self.client.force_login(self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.context["pending"], [])


class CreateTeacherTests(TestCase):
    def test_crea_docente_con_correo_verificado(self):
        call_command("create_teacher", username="profe", email="profe@example.ec", password=PASSWORD)
        user = User.objects.get(username="profe")
        self.assertTrue(user.is_teacher)
        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        self.assertTrue(self.client.login(username="profe", password=PASSWORD))


class PreferencesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ana", "ana@example.ec", PASSWORD)
        self.client.force_login(self.user)

    def test_menu_de_usuario_con_cuenta_y_sesion(self):
        page = self.client.get(reverse("home"))
        self.assertContains(page, 'class="usermenu"')
        for name in ("account_preferences", "account_change_password", "account_logout", "live_join", "course_join"):
            self.assertContains(page, reverse(name))

    def test_cambiar_idioma_persiste_en_la_cuenta_y_la_cookie(self):
        response = self.client.post(reverse("set_language"), {"language": "en", "next": "/"})
        self.assertEqual(response.cookies["django_language"].value, "en")
        self.user.refresh_from_db()
        self.assertEqual(self.user.language, "en")
        self.assertContains(self.client.get(reverse("home")), "My courses")
        # aun si la cookie dice otra cosa, manda la preferencia guardada
        self.client.cookies["django_language"] = "es"
        self.assertContains(self.client.get(reverse("home")), "My courses")

    def test_next_externo_no_redirige_fuera(self):
        response = self.client.post(reverse("set_language"), {"language": "en", "next": "https://malo.example/"})
        self.assertEqual(response["Location"], "/")

    def test_zona_horaria_detectada_se_guarda_una_vez(self):
        self.client.cookies["policlase_tz"] = "Europe/Madrid"
        self.client.get(reverse("home"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Europe/Madrid")
        self.client.cookies["policlase_tz"] = "Asia/Tokyo"          # viajar no la cambia
        self.client.get(reverse("home"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "Europe/Madrid")

    def test_zona_horaria_invalida_se_ignora(self):
        self.client.cookies["policlase_tz"] = "Marte/Olimpo"
        self.client.get(reverse("home"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.timezone, "")

    def test_pagina_de_preferencias(self):
        self.client.post(reverse("account_preferences"), {"language": "en", "timezone": "America/New_York"})
        self.user.refresh_from_db()
        self.assertEqual((self.user.language, self.user.timezone), ("en", "America/New_York"))


class AnonymousLanguageTests(TestCase):
    def test_sin_sesion_el_idioma_queda_en_la_cookie_y_pasa_al_registro(self):
        self.client.post(reverse("set_language"), {"language": "en", "next": "/cuenta/login/"})
        self.assertContains(self.client.get(reverse("account_signup")), "Create a student account")
        self.client.cookies["policlase_tz"] = "America/Bogota"
        teacher = User.objects.create_user("docente", "docente@example.ec", PASSWORD, role=User.Role.TEACHER)
        course = Course.objects.create(owner=teacher, name="Métodos", code="MN-1")
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            # otro correo: allauth limita a un correo de confirmación por dirección cada 3 minutos
            self.client.post(reverse("account_signup"), signup_data(
                username="eva", email="eva@example.ec", course_code=course.join_code))
        user = User.objects.get(username="eva")
        self.assertEqual((user.language, user.timezone), ("en", "America/Bogota"))


class ThemePreferenceTests(TestCase):
    def test_automatico_por_defecto_y_persistente(self):
        self.assertNotContains(self.client.get(reverse("account_login")), "data-theme")
        self.client.post(reverse("set_theme"), {"theme": "light", "next": "/"})
        self.assertContains(self.client.get(reverse("account_login")), 'data-theme="light"')
        # El docente lo elige en Preferencias.
        teacher = User.objects.create_user("profe", "profe@example.ec", PASSWORD, role=User.Role.TEACHER)
        self.client.force_login(teacher)
        self.client.post(reverse("account_preferences"),
                         {"language": "es", "timezone": "America/Guayaquil", "theme": "dark"})
        teacher.refresh_from_db()
        self.assertEqual(teacher.theme, "dark")

    def test_el_boton_de_tema_esta_en_todas_las_paginas(self):
        user = User.objects.create_user("ana", "ana@example.ec", PASSWORD)
        self.client.force_login(user)
        for name in ("home", "account_preferences", "course_join"):
            self.assertContains(self.client.get(reverse(name)), 'class="themeswitch auto"')
        self.client.post(reverse("set_theme"), {"theme": "dark", "next": "/"})
        user.refresh_from_db()
        self.assertEqual(user.theme, "dark")
        home = self.client.get(reverse("home"))                   # en oscuro, el botón lleva al claro
        self.assertContains(home, 'name="theme" value="light"')
        self.assertNotContains(home, 'name="theme" value="dark"')
        self.client.logout()
        self.assertContains(self.client.get(reverse("account_login")), 'name="theme"')   # también sin sesión

    def test_tema_invalido_se_ignora(self):
        response = self.client.post(reverse("set_theme"), {"theme": "<script>", "next": "/"})
        self.assertNotIn("policlase_theme", response.cookies)


class NameCaseTests(TestCase):
    def test_los_nombres_se_guardan_con_mayuscula_inicial(self):
        from .names import name_case

        user = User.objects.create_user("danny", "danny@example.ec", "clave-segura-2026",
                                        first_name="DANNY JAVIER", last_name="CAIZA OÑA")
        user.refresh_from_db()
        self.assertEqual((user.first_name, user.last_name), ("Danny Javier", "Caiza Oña"))
        self.assertEqual(name_case("MARÍA DEL CISNE"), "María del Cisne")
        self.assertEqual(name_case("DE LA TORRE VEGA"), "De la Torre Vega")
        self.assertEqual(name_case("McDonald"), "McDonald")                # una mezcla deliberada se respeta
        self.assertEqual(name_case("Oña A."), "Oña A.")
