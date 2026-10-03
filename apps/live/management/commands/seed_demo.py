"""Datos de demostración para revisar el prototipo.

    docker compose exec web python manage.py seed_demo

Crea un docente, un curso con una presentación y varios estudiantes con la solicitud de
inscripción PENDIENTE, para probar el flujo de aprobación desde el panel. No usar en una base
con datos reales: las contraseñas de demostración son conocidas.
"""

from pathlib import Path

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from policlase_gen import deck as deckfmt

from apps.courses.models import Course, Enrollment
from apps.live.models import Deck

DEMO_DECK = Path(__file__).resolve().parents[2] / "demo" / "biseccion-clase-1.yaml"
STUDENTS = [
    ("ana", "Ana", "Pérez", "1700000001"),
    ("bruno", "Bruno", "Salazar", "1700000002"),
    ("carla", "Carla", "Mendoza", "1700000003"),
    ("diego", "Diego", "Torres", "1700000004"),
]


class Command(BaseCommand):
    help = "Crea un docente, un curso, una presentación y estudiantes de prueba."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="demo-policlase-2026")

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()
        password = opts["password"]

        def account(username, first, last, role, student_id=""):
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@demo.policlase", "first_name": first,
                          "last_name": last, "role": role, "student_id": student_id},
            )
            user.set_password(password)
            user.save()
            EmailAddress.objects.update_or_create(
                user=user, email=user.email, defaults={"verified": True, "primary": True})
            return user

        teacher = account("profe", "Docente", "Demo", User.Role.TEACHER)
        course, _ = Course.objects.get_or_create(
            owner=teacher, code="MN-2026-2",
            defaults={"name": "Métodos Numéricos",
                      "description": "Curso de demostración de policlase."},
        )

        document, report = deckfmt.load_deck_text(DEMO_DECK.read_text(encoding="utf-8"))
        compiled = deckfmt.compile_deck(document)
        Deck.objects.update_or_create(
            course=course, title=compiled["title"],
            defaults={"source": DEMO_DECK.read_text(encoding="utf-8"), "compiled": compiled},
        )

        for username, first, last, sid in STUDENTS:
            student = account(username, first, last, User.Role.STUDENT, sid)
            Enrollment.objects.get_or_create(course=course, student=student)

        self.stdout.write(self.style.SUCCESS("Datos de demostración listos."))
        self.stdout.write(f"  docente:      profe / {password}")
        self.stdout.write(f"  estudiantes:  {', '.join(s[0] for s in STUDENTS)} / {password}"
                          "  (solicitudes pendientes de aprobación)")
        self.stdout.write(f"  curso:        {course.name} · código {course.join_code_display}")
        self.stdout.write(self.style.WARNING("  No use estos datos en producción."))
