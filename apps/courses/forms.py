from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Course


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["name", "code", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class AcceptingUntilForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["accepting_until"]
        widgets = {"accepting_until": forms.DateTimeInput(attrs={"type": "datetime-local"},
                                                          format="%Y-%m-%dT%H:%M")}

    def clean_accepting_until(self):
        value = self.cleaned_data["accepting_until"]
        if value and value <= timezone.now():
            raise forms.ValidationError(_("La fecha de cierre debe ser posterior a la fecha actual."))
        return value


class JoinForm(forms.Form):
    code = forms.CharField(
        label=_("Código del curso"), max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "ABCD-2345", "autocomplete": "off",
                                      "autocapitalize": "characters", "spellcheck": "false"}),
    )
