import re

from django import forms
from django.conf import settings
from django.contrib import messages
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from apps.courses.models import Course, Enrollment

from .middleware import timezone_names, valid_timezone

STUDENT_ID = re.compile(r"^[A-Za-z0-9-]{5,20}$")


class ProfileSignupForm(forms.Form):
    """Campos que allauth añade a su formulario de registro.

    El registro de estudiantes (`account_signup`) pide la cédula y el código de un curso abierto;
    el de docentes (`teacher_signup`) quita esos campos y marca el formulario con `as_teacher`.
    El código es lo que protege a terceros: cada cuenta nueva envía un correo a la dirección escrita
    en el formulario, y sin un código válido el formulario no pasa de aquí.
    """

    as_teacher = False

    course_code = forms.CharField(
        label=_("Código del curso"), max_length=20,
        help_text=_("Su docente le entrega este código; si recibió un enlace de inscripción, ya aparece completado."),
        widget=forms.TextInput(attrs={"placeholder": "ABCD-2345", "autocomplete": "off",
                                      "autocapitalize": "characters", "spellcheck": "false"}),
    )
    first_name = forms.CharField(label=_("Nombres"), max_length=150)
    last_name = forms.CharField(label=_("Apellidos"), max_length=150)
    student_id = forms.CharField(
        label=_("Cédula o pasaporte"), max_length=20,
        help_text=_("Se utiliza para identificarle ante sus docentes y en las actas de calificaciones."),
    )

    field_order = ["course_code", "first_name", "last_name", "student_id", "email", "username",
                   "password1", "password2"]

    def clean_course_code(self):
        self.course = Course.open_for_code(self.cleaned_data["course_code"])
        if self.course is None:
            raise forms.ValidationError(_("Ese código no corresponde a ningún curso abierto. Revíselo con su docente."))
        return self.cleaned_data["course_code"]

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
        if not self.as_teacher:
            Enrollment.objects.get_or_create(course=self.course, student=user)
            messages.success(request, _("Solicitud enviada a %(course)s. Su docente debe aprobarla.")
                             % {"course": self.course.name})


class PreferencesForm(forms.Form):
    language = forms.ChoiceField(label=_("Idioma"), choices=settings.LANGUAGES)
    timezone = forms.ChoiceField(label=_("Zona horaria"))
    theme = forms.ChoiceField(label=_("Tema"), required=False,
                              help_text=_("En el proyector se utiliza el tema claro, salvo que se elija el oscuro."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["timezone"].choices = [(z, z.replace("_", " ")) for z in sorted(timezone_names())]
        from .models import User
        self.fields["theme"].choices = User.Theme.choices
