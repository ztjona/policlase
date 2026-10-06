"""Clases abiertas: asistir sin cuenta con un enlace que el docente comparte.

Un invitado es un `User` con rol `guest`, sin contraseña ni correo real, creado al entrar por el
enlace. Así el motor, el marcador y los resultados lo tratan como a cualquier participante sin
caminos especiales. `apps.accounts.middleware` lo confina a las páginas de la clase en vivo.
"""

import secrets

import segno
from django import forms
from django.contrib.auth import get_user_model, login, logout
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _

from apps.courses.access import is_approved

from . import engine
from .models import LiveSession

GUEST_EMAIL_DOMAIN = "invitado.invalid"   # .invalid: reservado, nunca entrega correo


def qr_svg(url: str) -> str:
    """Código QR del enlace, en SVG, para proyectarlo en la sala de espera."""
    return segno.make(url, error="m").svg_inline(border=2, omitsize=True, dark="#14222B", light="#FFFFFF")


class GuestForm(forms.Form):
    name = forms.CharField(label=_("Su nombre"), max_length=40,
                           widget=forms.TextInput(attrs={"autocomplete": "nickname", "autofocus": True}))

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if not name:
            raise forms.ValidationError(_("Escriba un nombre."))
        return name


def create_guest(name: str):
    User = get_user_model()
    username = f"invitado-{secrets.token_hex(5)}"
    user = User(username=username, email=f"{username}@{GUEST_EMAIL_DOMAIN}",
                first_name=name, role=User.Role.GUEST)
    user.set_unusable_password()
    user.save()
    return user


def guest_entry(request, token):
    session = get_object_or_404(LiveSession.objects.select_related("course"), guest_token=token)
    user = request.user

    if session.status == LiveSession.Status.ENDED:
        return render(request, "live/guest_closed.html", {"session": session, "ended": True})
    if not session.allow_guests:
        return render(request, "live/guest_closed.html", {"session": session, "ended": False})

    if user.is_authenticated:
        if session.course.owner_id == user.pk:
            return redirect("present", pk=session.pk)
        if user.is_guest and not session.participants.filter(student=user).exists():
            logout(request)          # invitado de otra clase: entra como invitado nuevo
        else:
            # Estudiantes aprobados entran normalmente; otras cuentas, con su propio nombre.
            if not is_approved(user, session.course):
                engine.join(session, user)
            return redirect("live_student", pk=session.pk)

    form = GuestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        guest = create_guest(form.cleaned_data["name"])
        login(request, guest, backend="django.contrib.auth.backends.ModelBackend")
        engine.join(session, guest)
        return redirect("live_student", pk=session.pk)
    return render(request, "live/guest_join.html", {"session": session, "form": form})
