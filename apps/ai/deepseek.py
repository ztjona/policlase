"""Cliente mínimo de la API de DeepSeek (compatible con la de OpenAI), sin dependencias."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.utils.translation import gettext as _

API_URL = "https://api.deepseek.com/chat/completions"
#: Una respuesta tarda de 5 a 30 s; el modelo que razona, más.
TIMEOUT_S = 120


class AiError(Exception):
    """La llamada falló; el mensaje se le muestra al docente."""


def complete(key: str, model: str, messages: list[dict], max_tokens: int = 1500) -> tuple[str, dict]:
    """(texto de la respuesta, uso de tokens). Pide JSON y temperatura 0: la misma respuesta,
    la misma propuesta, en lo posible."""
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
    if model == "deepseek-reasoner":
        body["max_tokens"] = max(max_tokens, 4000)      # el razonamiento consume tokens antes de responder
    else:                                               # el que razona no admite temperatura ni modo JSON
        body["temperature"] = 0
        body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise AiError({
            401: _("DeepSeek rechazó la clave. Revísela en «Inteligencia artificial»."),
            402: _("La cuenta de DeepSeek no tiene saldo suficiente."),
            429: _("DeepSeek está limitando las solicitudes. Inténtelo de nuevo en un momento."),
        }.get(exc.code, _("DeepSeek respondió con un error (%(code)s).") % {"code": exc.code})) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AiError(_("No se pudo conectar con DeepSeek.")) from None
    except ValueError:
        raise AiError(_("DeepSeek devolvió una respuesta que no se pudo leer.")) from None
    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AiError(_("DeepSeek devolvió una respuesta que no se pudo leer.")) from None
    return content, data.get("usage") or {}
