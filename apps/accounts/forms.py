import re

from django import forms
from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from .middleware import timezone_names, valid_timezone

STUDENT_ID = re.compile(r"^[A-Za-z0-9-]{5,20}$")


class ProfileSignupForm(forms.Form):
    """Campos que allauth añade a su formulario de registro.

    El registro de estudiantes (`account_signup`) pide la cédula; el de docentes
    (`teacher_signup`) quita ese campo y marca el formulario con `as_teacher`.
    """

    as_teacher = False

    first_name = forms.CharField(label=_("Nombres"), max_length=150)
    last_name = forms.CharField(label=_("Apellidos"), max_length=150)
    student_id = forms.CharField(
        label=_("Cédula o pasaporte"), max_length=20,
        help_text=_("La usamos para identificarle ante sus docentes y en las actas de notas."),
    )

    field_order = ["first_name", "last_name", "student_id", "email", "username",
                   "password1", "password2"]

    def clean_student_id(self):
        value = self.cleaned_data["student_id"].strip().upper()
        if not STUDENT_ID.match(value):
            raise forms.ValidationError(_("Use solo letras, números o guiones (5 a 20 caracteres)."))
        return value

    def signup(self, request, user):
        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        if self.as_teacher:
            user.role = user.Role.TEACHER
        else:
            user.student_id = self.cleaned_data["student_id"]
            user.role = user.Role.STUDENT
        # Lo que la persona ya veía al registrarse queda como su preferencia.
        user.language = translation.get_language() if translation.get_language() in dict(settings.LANGUAGES) else ""
        user.timezone = valid_timezone(request.COOKIES.get(settings.TIMEZONE_COOKIE))
        user.save()


class PreferencesForm(forms.Form):
    language = forms.ChoiceField(label=_("Idioma"), choices=settings.LANGUAGES)
    timezone = forms.ChoiceField(label=_("Zona horaria"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = [(z, z.replace("_", " ")) for z in sorted(timezone_names())]
