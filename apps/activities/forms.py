from datetime import datetime

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.grades.forms import PeriodRowMixin, TaggedSelect
from apps.grades.gradebook import day_of
from apps.grades.models import Category, Milestone

from .models import Activity, course_tz, new_access_code

DATETIME = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")


TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")
REVIEW_FIELDS = ["review_mode", "review_milestone", "review_offset_days", "review_time", "review_until"]
REVIEW_WIDGETS = {
    "review_until": DATETIME, "review_milestone": forms.Select(attrs={"class": "compact"}),
    "review_mode": forms.RadioSelect, "review_time": TIME,
    "review_offset_days": forms.NumberInput(attrs={"min": "-60", "max": "60"}),
}


class ReviewMixin:
    """La revisión (cuándo se ve la corrección y las soluciones), igual en actividades y evaluaciones."""

    def setup_review(self, milestones):
        self.fields["review_milestone"].queryset = milestones
        self.fields["review_milestone"].empty_label = _("— Elija una fecha clave —")
        self.fields["review_until"].label = _("Fecha y hora de cierre de la revisión")
        # Si no llegan (un formulario anterior, una petición parcial), se conservan los de la actividad.
        for name in ("review_mode", "review_offset_days", "review_time"):
            self.fields[name].required = False

    def clean_review_offset_days(self):
        value = self.cleaned_data["review_offset_days"]
        if value is not None and not -60 <= value <= 60:
            raise forms.ValidationError(_("Entre -60 y 60 días."))
        return value

    def clean_review(self, data) -> str:
        for name in ("review_mode", "review_offset_days", "review_time"):
            if name not in self.data:
                data[name] = getattr(self.instance, name)
        if not data.get("review_mode"):
            data["review_mode"] = Activity.Review.ALWAYS
        if data.get("review_offset_days") is None:
            data["review_offset_days"] = 0
        review = data.get("review_mode")
        if review == Activity.Review.MILESTONE:
            if not data.get("review_milestone"):
                self.add_error("review_milestone", _("Elija la fecha clave."))
            if data.get("review_time") is None:
                self.add_error("review_time", _("Indique la hora."))
            data["review_until"] = None
        elif review == Activity.Review.FIXED:
            data["review_milestone"] = None
            if not data.get("review_until"):
                self.add_error("review_until", _("Indique hasta cuándo dura la revisión."))
        else:
            data["review_milestone"], data["review_until"] = None, None
        return review

    def review_warning(self, activity) -> bool:
        """Cerrar la revisión antes de que empiece es lo mismo que no tenerla; se avisa en la página."""
        return bool(activity.review_until and activity.due_at and activity.review_until <= activity.due_at
                    and activity.review_mode != Activity.Review.ALWAYS)


class ActivitySettingsForm(ReviewMixin, PeriodRowMixin, forms.ModelForm):
    """Lo que la plataforma decide de una actividad: ponderación, fechas, revisión, atrasos e intentos."""

    DUE_NONE, DUE_FIXED, DUE_MILESTONE, DUE_OPENING = "none", "fixed", "milestone", "opening"
    due_mode = forms.ChoiceField(
        label=_("Vencimiento"), widget=forms.RadioSelect,
        choices=[(DUE_MILESTONE, _("Relativo a una fecha clave")), (DUE_OPENING, _("Relativo a la apertura")),
                 (DUE_FIXED, _("Fecha fija")), (DUE_NONE, _("Sin fecha de entrega"))])
    # «7 días después de la apertura, a las 21:59»: campos propios, para no compartir los de la fecha clave.
    opening_days = forms.IntegerField(label=_("días después de la apertura"), min_value=0, max_value=60,
                                      initial=7, required=False, widget=forms.NumberInput(attrs={"min": "0", "max": "60"}))
    opening_time = forms.TimeField(label=_("hora de entrega"), required=False, widget=TIME)

    class Meta:
        model = Activity
        fields = ["category", "published", "opens_at", "due_milestone", "due_offset_days", "due_time",
                  "due_at", *REVIEW_FIELDS, "debate_mode", "debate_until",
                  "late_policy", "late_penalty", "max_attempts", "auto_submit"]
        widgets = {
            "opens_at": DATETIME, "due_at": DATETIME, "due_milestone": TaggedSelect(), "due_time": TIME,
            "debate_mode": forms.RadioSelect, "debate_until": DATETIME,
            "due_offset_days": forms.NumberInput(attrs={"min": "-60", "max": "60"}),
            "late_penalty": forms.NumberInput(attrs={"min": "0", "max": "100"}),
            "max_attempts": forms.NumberInput(attrs={"min": "1", "max": "20"}),
            **REVIEW_WIDGETS,
        }

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(period__course=course).select_related("period")
        self.fields["category"].empty_label = _("— Sin ponderación (no cuenta) —")
        milestones = Milestone.objects.filter(course=course)
        self.fields["due_milestone"].queryset = milestones
        self.fields["due_milestone"].empty_label = _("— Elija una fecha clave —")
        self.setup_review(milestones)
        for name in ("due_offset_days", "due_time"):
            self.fields[name].required = False
        # La fecha de cada fecha clave, para que el navegador proponga el parcial de la entrega.
        self.fields["due_milestone"].widget.tags = {str(m.pk): {"data-day": m.on.isoformat()} for m in milestones}
        self.setup_period(course, day_of(self.instance) or timezone.localdate())
        self.fields["due_at"].label = _("Fecha y hora de entrega")
        self.fields["debate_until"].label = _("Fecha y hora de cierre del debate")
        self.fields["debate_mode"].required = False             # solo llega si hay preguntas de debate
        instance = self.instance
        if not self.is_bound:
            self.initial["due_mode"] = (self.DUE_MILESTONE if instance.due_milestone_id else
                                        self.DUE_OPENING if instance.due_from_opening else
                                        self.DUE_FIXED if instance.due_at else
                                        self.DUE_MILESTONE if self.fields["due_milestone"].queryset.exists()
                                        else self.DUE_NONE)
            self.initial["opening_days"] = instance.due_offset_days if instance.due_from_opening else 7
            self.initial["opening_time"] = instance.due_time
            if not instance.review_milestone_id and instance.due_milestone_id:
                # Lo habitual: la revisión dura hasta la prueba de la que depende la entrega.
                self.initial["review_milestone"] = instance.due_milestone_id

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
        if value is not None and not -60 <= value <= 60:
            raise forms.ValidationError(_("Entre -60 y 60 días."))
        return value

    def clean(self):
        data = super().clean()
        mode = data.get("due_mode")
        self.from_opening = mode == self.DUE_OPENING
        # Los campos de la fecha clave solo se exigen en ese modo; si no llegan, quedan los de antes.
        for name in ("due_offset_days", "due_time"):
            if data.get(name) is None and mode != self.DUE_MILESTONE:
                data[name] = getattr(self.instance, name)
        if mode == self.DUE_MILESTONE:
            if data.get("due_offset_days") is None:
                self.add_error("due_offset_days", _("Indique cuántos días antes o después."))
            if not data.get("due_milestone"):
                self.add_error("due_milestone", _("Elija la fecha clave."))
            if not data.get("due_time"):
                self.add_error("due_time", _("Indique la hora."))
        elif mode == self.DUE_OPENING:
            data["due_milestone"] = None
            if not data.get("opens_at"):
                self.add_error("opens_at", _("Indique la fecha de apertura, desde la cual se cuenta la fecha de entrega."))
            if data.get("opening_days") is None:
                self.add_error("opening_days", _("Indique cuántos días después de la apertura."))
            if not data.get("opening_time"):
                self.add_error("opening_time", _("Indique la hora."))
            data["due_offset_days"] = data.get("opening_days") or 0
            data["due_time"] = data.get("opening_time") or data.get("due_time")
        elif mode == self.DUE_FIXED:
            data["due_milestone"] = None
            if not data.get("due_at"):
                self.add_error("due_at", _("Indique la fecha de entrega."))
        else:
            data["due_milestone"], data["due_at"] = None, None
        opens, due = data.get("opens_at"), data.get("due_at")
        if mode == self.DUE_FIXED and opens and due and due <= opens:
            self.add_error("due_at", _("Debe ser posterior a la apertura."))

        review = self.clean_review(data)
        self.clean_debate(data)
        if mode == self.DUE_NONE and review in (Activity.Review.MILESTONE, Activity.Review.FIXED):
            self.add_error("review_mode", _("La revisión comienza al vencer la fecha de entrega. Si la actividad no "
                                            "tiene fecha de entrega, elija «Sin límite» o «Sin revisión»."))
        return self.clean_period_row(data)

    def clean_debate(self, data) -> None:
        """El cierre del debate: con la revisión, en una fecha fija (posterior al vencimiento, que es
        cuando se abre) o cuando el docente lo cierre. Sin preguntas de debate, queda como estaba."""
        if "debate_mode" not in self.data:
            data["debate_mode"], data["debate_until"] = self.instance.debate_mode, self.instance.debate_until
            return
        if data.get("debate_mode") == Activity.DebateClose.FIXED:
            until, due = data.get("debate_until"), data.get("due_at")
            if not until:
                self.add_error("debate_until", _("Indique la fecha y hora de cierre del debate."))
            elif due and until <= due:
                self.add_error("debate_until", _("El cierre del debate debe ser posterior a la fecha de entrega, que es cuando se abre."))
        else:
            data["debate_until"] = None
            data["debate_mode"] = data.get("debate_mode") or Activity.DebateClose.REVIEW

    def save(self, commit=True):
        from apps.grades.gradebook import rebind

        activity = super().save(commit=False)
        self.review_before_due = False
        activity.due_from_opening = getattr(self, "from_opening", False)
        if activity.due_relative:
            activity.compute_due()
        else:
            activity.compute_review()
        self.review_before_due = self.review_warning(activity)
        # Vence en el otro parcial: cuenta en la fila del mismo nombre de ese parcial.
        self.rebound = rebind(activity)
        if commit:
            activity.save()
        return activity


class EvaluationSettingsForm(ReviewMixin, PeriodRowMixin, forms.ModelForm):
    """Una evaluación: el día (el de una fecha clave, o uno fijo), su horario, la duración por
    estudiante, el código de acceso y la revisión, que empieza al publicar las notas."""

    DAY_MILESTONE, DAY_FIXED = "milestone", "fixed"
    day_mode = forms.ChoiceField(label=_("Fecha"), widget=forms.RadioSelect,
                                 choices=[(DAY_MILESTONE, _("El día de una fecha clave")), (DAY_FIXED, _("Una fecha fija"))])
    day = forms.DateField(label=_("Fecha"), required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    class Meta:
        model = Activity
        fields = ["category", "published", "due_milestone", "due_offset_days", "opens_time", "due_time",
                  "duration_min", "require_code", *REVIEW_FIELDS]
        widgets = {
            "due_milestone": TaggedSelect(), "opens_time": TIME, "due_time": TIME,
            "due_offset_days": forms.NumberInput(attrs={"min": "-60", "max": "60"}),
            "duration_min": forms.NumberInput(attrs={"min": "5", "max": "600", "placeholder": "90"}),
            **REVIEW_WIDGETS,
        }
        labels = {"due_time": _("Hora de cierre")}

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.filter(period__course=course).select_related("period")
        self.fields["category"].empty_label = _("— Sin ponderación (no cuenta) —")
        milestones = Milestone.objects.filter(course=course)
        self.fields["due_milestone"].queryset = milestones
        self.fields["due_milestone"].empty_label = _("— Elija una fecha clave —")
        self.fields["due_milestone"].widget.tags = {str(m.pk): {"data-day": m.on.isoformat()} for m in milestones}
        self.fields["due_offset_days"].required = False
        self.fields["opens_time"].required = True
        self.setup_review(milestones)
        self.setup_period(course, day_of(self.instance) or timezone.localdate())
        instance = self.instance
        if not self.is_bound:
            self.initial["day_mode"] = (self.DAY_FIXED if instance.due_at and not instance.due_milestone_id
                                        else self.DAY_MILESTONE)
            if instance.due_at:
                tz = course_tz(course)
                self.initial["day"] = timezone.localtime(instance.due_at, tz).date()
                self.initial["due_time"] = timezone.localtime(instance.due_at, tz).time()
                if instance.opens_at:
                    self.initial["opens_time"] = timezone.localtime(instance.opens_at, tz).time()

    def clean_duration_min(self):
        value = self.cleaned_data["duration_min"]
        if value is not None and not 5 <= value <= 600:
            raise forms.ValidationError(_("Entre 5 y 600 minutos."))
        return value

    def clean(self):
        data = super().clean()
        if data.get("day_mode") == self.DAY_MILESTONE:
            if not data.get("due_milestone"):
                self.add_error("due_milestone", _("Elija la fecha clave."))
            if data.get("due_offset_days") is None:
                data["due_offset_days"] = 0
        else:
            data["due_milestone"] = None
            if not data.get("day"):
                self.add_error("day", _("Indique la fecha de la evaluación."))
        opens, closes = data.get("opens_time"), data.get("due_time")
        if opens and closes and closes <= opens:
            self.add_error("due_time", _("Debe ser posterior a la hora de inicio."))
        self.clean_review(data)
        return self.clean_period_row(data)

    def save(self, commit=True):
        from apps.grades.gradebook import rebind

        activity = super().save(commit=False)
        activity.kind = Activity.Kind.EVALUATION
        activity.due_from_opening = False
        if activity.due_milestone_id:
            activity.compute_due()
        else:
            tz = course_tz(activity.course)
            day = self.cleaned_data["day"]
            activity.opens_at = datetime.combine(day, activity.opens_time, tzinfo=tz)
            activity.due_at = datetime.combine(day, activity.due_time, tzinfo=tz)
            activity.compute_review()
        if activity.require_code and not activity.access_code:
            activity.access_code = new_access_code()
        self.review_before_due = self.review_warning(activity)
        self.rebound = rebind(activity)
        if commit:
            activity.save()
        return activity


class PracticeSettingsForm(forms.ModelForm):
    """Un repaso: cuándo abre (unos días antes de una fecha clave, o desde una fecha fija) y, si se
    quiere, cuándo se cierra. No tiene ponderación, intentos ni atrasos: no cuenta para la nota."""

    OPEN_MILESTONE, OPEN_FIXED = "milestone", "fixed"
    open_mode = forms.ChoiceField(label=_("Disponible"), widget=forms.RadioSelect,
                                  choices=[(OPEN_MILESTONE, _("Unos días antes de una fecha clave")),
                                           (OPEN_FIXED, _("Desde una fecha fija"))])
    days_before = forms.IntegerField(label=_("días antes de"), min_value=0, max_value=60, initial=7, required=False,
                                     widget=forms.NumberInput(attrs={"min": "0", "max": "60"}))

    class Meta:
        model = Activity
        fields = ["published", "due_milestone", "opens_at", "due_at"]
        widgets = {"opens_at": DATETIME, "due_at": DATETIME, "due_milestone": forms.Select(attrs={"class": "compact"})}
        labels = {"due_at": _("Se cierra (opcional)")}

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["due_milestone"].queryset = Milestone.objects.filter(course=course)
        self.fields["due_milestone"].empty_label = _("— Elija una fecha clave —")
        self.fields["due_milestone"].label = _("Fecha clave")
        self.fields["opens_at"].label = _("Disponible desde")
        self.fields["due_at"].help_text = _("Si se deja vacío, el repaso sigue disponible.")
        instance = self.instance
        if not self.is_bound:
            self.initial["open_mode"] = self.OPEN_FIXED if instance.opens_at and not instance.due_milestone_id \
                else self.OPEN_MILESTONE
            if instance.due_milestone_id:
                self.initial["days_before"] = -instance.due_offset_days

    def clean(self):
        data = super().clean()
        if data.get("open_mode") == self.OPEN_MILESTONE:
            if not data.get("due_milestone"):
                self.add_error("due_milestone", _("Elija la fecha clave."))
            if data.get("days_before") is None:
                self.add_error("days_before", _("Indique cuántos días antes."))
        else:
            data["due_milestone"] = None
            if not data.get("opens_at"):
                self.add_error("opens_at", _("Indique desde cuándo está disponible."))
        return data

    def save(self, commit=True):
        activity = super().save(commit=False)
        activity.kind, activity.category, activity.due_from_opening = Activity.Kind.PRACTICE, None, False
        if activity.due_milestone_id:
            activity.due_offset_days = -int(self.cleaned_data.get("days_before") or 0)
            activity.compute_due()
        opens, closes = activity.opens_at, activity.due_at
        self.closes_before = bool(opens and closes and closes <= opens)
        if commit:
            activity.save()
        return activity
