from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.courses.models import Course


class GradingRules(models.Model):
    """Escala y reglas de aprobación del curso.

    Por omisión, las de una universidad con dos parciales sobre 20: con menos de 18 entre ambos se
    pierde, con 28 o más se aprueba, y en medio hay un supletorio sobre 40 que se aprueba si la suma
    de parciales y supletorio llega a 48 con al menos 24 en el supletorio.
    """

    course = models.OneToOneField(Course, on_delete=models.CASCADE, related_name="grading_rules")
    #: Tope de cada parcial: los extras compensan lo que falte, pero un parcial no pasa de aquí.
    period_max = models.DecimalField(_("nota máxima de cada parcial"), max_digits=6, decimal_places=2,
                                     default=Decimal("20"))
    fail_below = models.DecimalField(_("pierde con menos de"), max_digits=6, decimal_places=2,
                                     default=Decimal("18"),
                                     help_text=_("Suma de los parciales."))
    pass_from = models.DecimalField(_("aprueba desde"), max_digits=6, decimal_places=2, default=Decimal("28"),
                                    help_text=_("Suma de los parciales."))
    makeup_max = models.DecimalField(_("supletorio sobre"), max_digits=6, decimal_places=2,
                                     default=Decimal("40"))
    makeup_total = models.DecimalField(_("suma mínima con supletorio"), max_digits=6, decimal_places=2,
                                       default=Decimal("48"),
                                       help_text=_("Parciales más supletorio."))
    makeup_min = models.DecimalField(_("nota mínima del supletorio"), max_digits=6, decimal_places=2,
                                     default=Decimal("24"))

    def __str__(self) -> str:
        return f"{self.course} · {self.period_max}"


class Period(models.Model):
    """Un parcial: su tabla de ponderaciones y sus fechas.

    Las fechas deciden qué clases en vivo cuentan para la participación de cada parcial, y el
    cierre es el último día en que se aceptan entregas atrasadas.
    """

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="periods")
    name = models.CharField(_("nombre"), max_length=80)
    position = models.PositiveIntegerField(default=0)
    starts_on = models.DateField(_("inicio"), null=True, blank=True)
    ends_on = models.DateField(_("cierre"), null=True, blank=True,
                               help_text=_("Último día para entregas atrasadas."))

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return self.name


class Category(models.Model):
    """Una fila de la tabla de ponderaciones de un parcial.

    Las obligatorias suman 100 %; las opcionales se suman encima y le dan al estudiante cómo
    compensar lo que le falte. La nota del parcial se topa en `GradingRules.period_max`.
    """

    class Source(models.TextChoices):
        WORK = "work", _("Actividades")
        LIVE = "live", _("Clases en vivo")

    period = models.ForeignKey(Period, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(_("nombre"), max_length=120)
    weight = models.DecimalField(_("peso (%)"), max_digits=5, decimal_places=2)
    optional = models.BooleanField(_("opcional"), default=False)
    #: De dónde salen las notas: las actividades asignadas a esta fila, o los resultados de las
    #: clases en vivo del parcial (faltar a una cuenta cero).
    source = models.CharField(_("origen"), max_length=8, choices=Source.choices, default=Source.WORK)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]

    def __str__(self) -> str:
        return f"{self.period.name} · {self.name}"


class Milestone(models.Model):
    """Una fecha clave del curso (Prueba 1, Examen…). Los deberes pueden vencer en relación con
    ella: si la prueba se reprograma, se cambia aquí y todos sus deberes la siguen."""

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="milestones")
    name = models.CharField(_("nombre"), max_length=120)
    on = models.DateField(_("fecha"))

    class Meta:
        ordering = ["on", "pk"]

    def __str__(self) -> str:
        return f"{self.name} ({self.on:%Y-%m-%d})"
