import re

from allauth.account.models import EmailAddress
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User

PASSWORD = "clave-segura-2026"


def signup_data(**over):
    data = {"first_name": "Ana", "last_name": "Pérez", "student_id": "1700000001",
            "email": "ana@example.ec", "username": "ana",
            "password1": PASSWORD, "password2": PASSWORD}
    data.update(over)
    return data


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SignupTests(TestCase):
    def test_registro_crea_estudiante_con_perfil_y_exige_verificar_el_correo(self):
        response = self.client.post(reverse("account_signup"), signup_data())
        self.assertRedirects(response, reverse("account_email_verification_sent"))

        user = User.objects.get(username="ana")
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertEqual((user.first_name, user.last_name, user.student_id), ("Ana", "Pérez", "1700000001"))
        self.assertFalse(EmailAddress.objects.get(user=user).verified)

        # Sin verificar, iniciar sesión no da acceso: allauth reenvía a la verificación.
        self.client.logout()
        response = self.client.post(reverse("account_login"), {"login": "ana", "password": PASSWORD})
        self.assertRedirects(response, reverse("account_email_verification_sent"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_el_enlace_de_verificacion_activa_la_cuenta(self):
        self.client.post(reverse("account_signup"), signup_data())
        link = re.search(r"https?://\S+/cuenta/confirm-email/\S+/", mail.outbox[-1].body).group(0)
        path = link.split("://", 1)[1].split("/", 1)[1]
        self.client.post("/" + path)
        self.assertTrue(EmailAddress.objects.get(email="ana@example.ec").verified)

        self.client.logout()
        response = self.client.post(reverse("account_login"), {"login": "ana@example.ec", "password": PASSWORD})
        self.assertRedirects(response, reverse("home"))

    def test_contrasena_debil_rechazada(self):
        response = self.client.post(reverse("account_signup"), signup_data(password1="12345678", password2="12345678"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="ana").exists())

    def test_cedula_invalida_rechazada(self):
        self.client.post(reverse("account_signup"), signup_data(student_id="12 34"))
        self.assertFalse(User.objects.filter(username="ana").exists())

    def test_el_registro_de_estudiante_no_crea_docentes(self):
        self.client.post(reverse("account_signup"), signup_data(role="teacher", as_teacher="1"))
        self.assertEqual(User.objects.get(username="ana").role, User.Role.STUDENT)

    def test_registro_de_docente_sin_cedula_y_con_verificacion(self):
        response = self.client.post(reverse("teacher_signup"), signup_data(
            username="profe", email="profe@example.ec", student_id=""))
        self.assertRedirects(response, reverse("account_email_verification_sent"))

        user = User.objects.get(username="profe")
        self.assertTrue(user.is_teacher)
        self.assertEqual(user.student_id, "")
        self.assertFalse(EmailAddress.objects.get(user=user).verified)
        self.assertIn("/cuenta/confirm-email/", mail.outbox[-1].body)

    def test_cada_pagina_enlaza_a_la_otra(self):
        self.assertContains(self.client.get(reverse("account_signup")), reverse("teacher_signup"))
        page = self.client.get(reverse("teacher_signup"))
        self.assertContains(page, reverse("account_signup"))
        self.assertNotContains(page, 'name="student_id"')

    def test_recuperar_contrasena_envia_correo(self):
        User.objects.create_user("ana", "ana@example.ec", PASSWORD)
        self.client.post(reverse("account_reset_password"), {"email": "ana@example.ec"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/cuenta/password/reset/key/", mail.outbox[0].body)

    def test_contrasenas_con_argon2(self):
        user = User.objects.create_user("ana", "ana@example.ec", PASSWORD)
        self.assertTrue(user.password.startswith("argon2"))


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
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            # otro correo: allauth limita a un correo de confirmación por dirección cada 3 minutos
            self.client.post(reverse("account_signup"), signup_data(username="eva", email="eva@example.ec"))
        user = User.objects.get(username="eva")
        self.assertEqual((user.language, user.timezone), ("en", "America/Bogota"))
