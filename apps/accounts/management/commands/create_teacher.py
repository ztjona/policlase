"""Crea una cuenta de docente con el correo ya verificado.

    docker compose exec web python manage.py create_teacher --username profe --email profe@x.ec

Los docentes normalmente se registran solos en /cuenta/registro-docente/. Este comando sirve para
crear la primera cuenta desde el servidor, dar acceso al admin (--admin) o promover a docente una
cuenta existente.
"""

import getpass

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Crea (o promueve) una cuenta de docente."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--password", help="se pide de forma interactiva si se omite")
        parser.add_argument("--admin", action="store_true", help="acceso también al admin de Django")

    @transaction.atomic
    def handle(self, *args, **opts):
        User = get_user_model()
        user = User.objects.filter(username=opts["username"]).first()
        created = user is None
        if created:
            user = User(username=opts["username"], email=opts["email"].lower())
        elif user.email.lower() != opts["email"].lower():
            raise CommandError("Ese usuario ya existe con otro correo.")

        user.first_name = opts["first_name"] or user.first_name
        user.last_name = opts["last_name"] or user.last_name
        user.role = User.Role.TEACHER
        if opts["admin"]:
            user.is_staff = user.is_superuser = True

        password = opts["password"]
        if created and not password:
            password = getpass.getpass("Contraseña: ")
            if password != getpass.getpass("Repita la contraseña: "):
                raise CommandError("Las contraseñas no coinciden.")
        if password:
            try:
                validate_password(password, user)
            except ValidationError as exc:
                raise CommandError(" ".join(exc.messages)) from exc
            user.set_password(password)
        user.save()

        EmailAddress.objects.update_or_create(
            user=user, email=user.email, defaults={"verified": True, "primary": True}
        )
        verb = "Creado" if created else "Actualizado"
        self.stdout.write(self.style.SUCCESS(f"{verb} docente {user.username} <{user.email}>"))
