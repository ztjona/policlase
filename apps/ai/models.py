"""Calificación asistida por IA de las respuestas abiertas (DeepSeek).

La IA solo **propone**: una propuesta (`AiProposal`) llena el formulario de nota del docente, que la
revisa, la corrige si hace falta y la guarda. Nada llega al estudiante sin ese paso. Se guarda lo
necesario para defender una calificación: el modelo, la versión del prompt y la salida cruda.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.github import crypto

KEY_PURPOSE = "ai-key"


class AiAccount(models.Model):
    """La integración de un docente: su clave de DeepSeek (cifrada), el modelo y si está habilitada."""

    class Model(models.TextChoices):
        CHAT = "deepseek-chat", _("DeepSeek Chat (rápido y económico)")
        REASONER = "deepseek-reasoner", _("DeepSeek Reasoner (razona antes de calificar; más lento y costoso)")

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_account")
    enabled = models.BooleanField(_("habilitar la calificación asistida por IA"), default=False)
    key_encrypted = models.TextField(blank=True, default="")
    model = models.CharField(_("modelo"), max_length=40, choices=Model.choices, default=Model.CHAT)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def key(self) -> str:
        return crypto.decrypt(self.key_encrypted, KEY_PURPOSE) if self.key_encrypted else ""

    @key.setter
    def key(self, value: str) -> None:
        self.key_encrypted = crypto.encrypt(value, KEY_PURPOSE) if value else ""

    @property
    def key_hint(self) -> str:
        """Los últimos cuatro caracteres, para reconocerla sin mostrarla."""
        key = self.key
        return key[-4:] if len(key) > 8 else ""

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.key_encrypted)


class AiProposal(models.Model):
    """Una calificación propuesta para una respuesta abierta: puntos por criterio, total y una
    retroalimentación para el estudiante. El docente la revisa y la aplica como su nota."""

    answer = models.ForeignKey("activities.Answer", on_delete=models.CASCADE, related_name="ai_proposals")
    points = models.FloatField(null=True, blank=True)
    feedback = models.TextField(blank=True, default="")
    #: [{"criterion", "max", "points", "reason"}], en el orden de la rúbrica.
    criteria = models.JSONField(default=list, blank=True)
    confidence = models.CharField(max_length=10, blank=True, default="")
    model = models.CharField(max_length=40)
    prompt_version = models.CharField(max_length=20)
    raw = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_cached = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    #: Cuándo la guardó el docente como su nota (con o sin cambios).
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
