from django import forms
from django.forms import inlineformset_factory
from django.utils.translation import gettext_lazy as _

from .models import Category, GradingRules, ManualRecord, Milestone, Period

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class RulesForm(forms.ModelForm):
    class Meta:
        model = GradingRules
        fields = ["period_max", "fail_below", "pass_from", "makeup_max", "makeup_total", "makeup_min"]
        widgets = {f: forms.NumberInput(attrs={"step": "0.01", "min": "0"}) for f in fields}

    def clean(self):
        data = super().clean()
        fail, ok = data.get("fail_below"), data.get("pass_from")
        if fail is not None and ok is not None and fail > ok:
            self.add_error("pass_from", _("Debe ser mayor o igual que el límite para perder."))
        return data


class PeriodForm(forms.ModelForm):
    class Meta:
        model = Period
        fields = ["name", "starts_on", "ends_on"]
        widgets = {"starts_on": DATE, "ends_on": DATE}

    def clean(self):
        data = super().clean()
        start, end = data.get("starts_on"), data.get("ends_on")
        if start and end and end < start:
            self.add_error("ends_on", _("El cierre no puede ser antes del inicio."))
        return data


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "weight", "optional", "source"]
        widgets = {"weight": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
                   "source": forms.Select(attrs={"class": "compact"})}

    def clean_weight(self):
        weight = self.cleaned_data["weight"]
        if weight is not None and not 0 <= weight <= 100:
            raise forms.ValidationError(_("Entre 0 y 100."))
        return weight


CategoryFormSet = inlineformset_factory(Period, Category, form=CategoryForm, extra=1, can_delete=True)


class MilestoneForm(forms.ModelForm):
    class Meta:
        model = Milestone
        fields = ["name", "on"]
        widgets = {"on": DATE, "name": forms.TextInput(attrs={"placeholder": _("p. ej. Prueba 1")})}


class ManualRecordForm(forms.ModelForm):
    class Meta:
        model = ManualRecord
        fields = ["title", "category", "on", "max_points", "note"]
        widgets = {"on": DATE, "max_points": forms.NumberInput(attrs={"step": "any", "min": "0.01"}),
                   "note": forms.TextInput(attrs={"placeholder": _("p. ej. «Fuiz, código 20226»")})}

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(period__course=course).select_related("period")
        self.fields["category"].empty_label = _("— Sin ponderación (no cuenta) —")
        self.fields["category"].help_text = _("Si la fecha cae en otro parcial, cuenta en la fila del mismo nombre "
                                              "de ese parcial.")

    def clean_max_points(self):
        value = self.cleaned_data["max_points"]
        if value is None or value <= 0:
            raise forms.ValidationError(_("Debe ser mayor que cero."))
        return value
