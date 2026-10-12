from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.live.models import Section

from .gradebook import period_for
from .models import Category, GradingRules, ManualRecord, Milestone, Period

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class TaggedSelect(forms.Select):
    """Un select cuyas opciones llevan atributos `data-*` (el parcial de cada fila, la fecha de cada
    fecha clave), para que el navegador filtre o calcule sin consultar al servidor."""

    def __init__(self, attrs=None, choices=(), tags=None):
        super().__init__(attrs, choices)
        self.tags = tags or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        option["attrs"].update(self.tags.get(str(getattr(value, "value", value)), {}))
        return option


class PeriodRowMixin:
    """El parcial aparte de la fila de la ponderación. Casi todo lo que se crea cuenta en el parcial en
    curso: el parcial viene propuesto por la fecha (de entrega o del registro) y la lista de filas
    muestra solo las de ese parcial (static/js/forms.js). Sin JavaScript, las filas van agrupadas por
    parcial; al guardar, una fila de otro parcial pasa a la del mismo nombre en el elegido."""

    def setup_period(self, course, day):
        periods = list(Period.objects.filter(course=course).prefetch_related("categories"))
        self.fields["period"] = forms.ModelChoiceField(
            queryset=Period.objects.filter(course=course), required=False, empty_label=None, label=_("Parcial"),
            widget=TaggedSelect(attrs={"data-period": ""},
                                tags={str(p.pk): {"data-ends": p.ends_on.isoformat()} for p in periods if p.ends_on}))
        rows = self.fields["category"]
        rows.widget = TaggedSelect(attrs={"data-rows": ""},
                                   tags={str(c.pk): {"data-period": str(p.pk)} for p in periods for c in p.categories.all()})
        rows.widget.is_required = rows.required
        rows.choices = [("", rows.empty_label)] + [(p.name, [(c.pk, c.name) for c in p.categories.all()])
                                                   for p in periods]
        if not self.is_bound:
            category = self.instance.category if self.instance.category_id else None
            chosen = category.period if category else period_for(periods, day)
            self.initial["period"] = chosen.pk if chosen else None

    def clean_period_row(self, data):
        period, category = data.get("period"), data.get("category")
        if period and category and category.period_id != period.pk:
            twin = Category.objects.filter(period=period, name__iexact=category.name).first()
            if twin is None:
                self.add_error("category", _("«%(period)s» no tiene la fila «%(row)s»; elija una de sus filas.")
                               % {"period": period.name, "row": category.name})
            else:
                data["category"] = twin
        return data


class RulesForm(forms.ModelForm):
    class Meta:
        model = GradingRules
        fields = ["period_max", "fail_below", "pass_from", "makeup_max", "makeup_total", "makeup_min"]
        widgets = {f: forms.NumberInput(attrs={"step": "0.01", "min": "0"}) for f in fields}

    def clean(self):
        data = super().clean()
        fail, ok = data.get("fail_below"), data.get("pass_from")
        if fail is not None and ok is not None and fail > ok:
            self.add_error("pass_from", _("Debe ser mayor o igual que el límite de reprobación."))
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


class ManualRecordForm(PeriodRowMixin, forms.ModelForm):
    class Meta:
        model = ManualRecord
        fields = ["title", "section", "category", "on", "max_points", "note"]
        widgets = {"on": DATE, "max_points": forms.NumberInput(attrs={"step": "any", "min": "0.01"}),
                   "note": forms.TextInput(attrs={"placeholder": _("p. ej. «Fuiz, código 20226»")})}

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(period__course=course).select_related("period")
        self.fields["category"].empty_label = _("— Sin ponderación (no cuenta) —")
        self.fields["category"].help_text = _("Si la fecha corresponde a otro parcial, el registro cuenta en la fila "
                                              "del mismo nombre en ese parcial.")
        self.setup_period(course, self.instance.on if self.instance.pk else timezone.localdate())
        self.fields["section"].queryset = Section.objects.filter(course=course)
        self.fields["section"].empty_label = _("— Sin unidad —")
        self.fields["section"].help_text = _("La unidad del curso a la que corresponde; se muestra junto al registro "
                                             "en la pestaña Actividades.")

    def clean(self):
        return self.clean_period_row(super().clean())

    def clean_max_points(self):
        value = self.cleaned_data["max_points"]
        if value is None or value <= 0:
            raise forms.ValidationError(_("Debe ser mayor que cero."))
        return value
