import re

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import CourseRepo

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class TokenForm(forms.Form):
    token = forms.CharField(
        label=_("Token de acceso personal"), strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "off", "placeholder": "github_pat_…"}),
    )


class CourseRepoForm(forms.ModelForm):
    class Meta:
        model = CourseRepo
        fields = ["repo", "branch", "folder"]

    def clean_repo(self):
        value = self.cleaned_data["repo"].strip()
        value = re.sub(r"^(https?://)?github\.com/", "", value).removesuffix(".git").strip("/")
        if not REPO.match(value):
            raise forms.ValidationError(_("Escríbalo como usuario/repositorio."))
        return value

    def clean_folder(self):
        return self.cleaned_data["folder"].strip().strip("/")
