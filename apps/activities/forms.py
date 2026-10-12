from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.grades.forms import PeriodRowMixin, TaggedSelect
from apps.grades.gradebook import day_of
from apps.grades.models import Category, Milestone

from .models import Activity

DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


class ActivitySettingsForm(PeriodRowMixin, forms.ModelForm):
    """Lo que la plataforma decide de una actividad: ponderación, fechas, atrasos e intentos."""

    DUE_NONE, DUE_FIXED, DUE_MILESTONE = "none", "fixed", "milestone"
    due_mode = forms.ChoiceField(
        label=_("Vencimiento"), widget=forms.RadioSelect,
        choices=[(DUE_MILESTONE, _("Relativo a una fecha clave")), (DUE_FIXED, _("Fecha fija")),
                 (DUE_NONE, _("Sin fecha de entrega"))])

    class Meta:
        model = Activity
        fields = ["category", "published", "opens_at", "due_milestone", "due_offset_days", "due_time",
                  "due_at", "late_policy", "late_penalty", "max_attempts", "block_paste"]
        widgets = {
            "opens_at": DATETIME, "due_at": DATETIME, "due_milestone": TaggedSelect(),
            "due_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "due_offset_days": forms.NumberInput(attrs={"min": "-60", "max": "60"}),
            "late_penalty": forms.NumberInput(attrs={"min": "0", "max": "100"}),
            "max_attempts": forms.NumberInput(attrs={"min": "1", "max": "20"}),
        }

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(period__course=course).select_related("period")
        self.fields["category"].empty_label = _("— Sin ponderación (no cuenta) —")
        milestones = Milestone.objects.filter(course=course)
        self.fields["due_milestone"].queryset = milestones
        self.fields["due_milestone"].empty_label = _("— Elija una fecha clave —")
        # La fecha de cada fecha clave, para que el navegador proponga el parcial de la entrega.
        self.fields["due_milestone"].widget.tags = {str(m.pk): {"data-day": m.on.isoformat()} for m in milestones}
        self.setup_period(course, day_of(self.instance) or timezone.localdate())
        self.fields["due_at"].label = _("Fecha y hora de entrega")
        instance = self.instance
        if not self.is_bound:
            self.initial["due_mode"] = (self.DUE_MILESTONE if instance.due_milestone_id else
                                        self.DUE_FIXED if instance.due_at else
                                        self.DUE_MILESTONE if self.fields["due_milestone"].queryset.exists()
                                        else self.DUE_NONE)

    def clean_max_attempts(self):
        value = self.cleaned_data["max_attempts"]
        if not 1 <= value <= 20:
            raise forms.ValidationError(_("Entre 1 y 20."))
        return value

    def clean_late_penalty(self):
        value = self.cleaned_data["late_penalty"]
        if not 0 <= value <= 100:
            raise forms.ValidationError(_("Entre 0 y 100."))
        return value

    def clean_due_offset_days(self):
        value = self.cleaned_data["due_offset_days"]
        if not -60 <= value <= 60:
            raise forms.ValidationError(_("Entre -60 y 60 días."))
        return value

    def clean(self):
        data = super().clean()
        mode = data.get("due_mode")
        if mode == self.DUE_MILESTONE:
            if not data.get("due_milestone"):
                self.add_error("due_milestone", _("Elija la fecha clave."))
            if not data.get("due_time"):
                self.add_error("due_time", _("Indique la hora."))
        elif mode == self.DUE_FIXED:
            data["due_milestone"] = None
            if not data.get("due_at"):
                self.add_error("due_at", _("Indique la fecha de entrega."))
        else:
            data["due_milestone"], data["due_at"] = None, None
        opens, due = data.get("opens_at"), data.get("due_at")
        if mode == self.DUE_FIXED and opens and due and due <= opens:
            self.add_error("due_at", _("Debe ser posterior a la apertura."))
        return self.clean_period_row(data)

    def save(self, commit=True):
        from apps.grades.gradebook import rebind

        activity = super().save(commit=False)
        if activity.due_milestone_id:
            activity.compute_due()
        # Vence en el otro parcial: cuenta en la fila del mismo nombre de ese parcial.
        self.rebound = rebind(activity)
        if commit:
            activity.save()
        return activity
