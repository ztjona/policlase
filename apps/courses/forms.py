from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Course


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["name", "code", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class JoinForm(forms.Form):
    code = forms.CharField(
        label=_("Código del curso"), max_length=20,
        widget=forms.TextInput(attrs={"placeholder": "ABCD-2345", "autocomplete": "off",
                                      "autocapitalize": "characters", "spellcheck": "false"}),
    )
