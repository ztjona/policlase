"""Cifrado de los tokens de GitHub en la base de datos.

La clave sale de DJANGO_SECRET_KEY: un respaldo de la base por sí solo no expone los tokens.
Cambiar la clave secreta los invalida y cada docente debe volver a pegar el suyo.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(b"policlase-github-token:" + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return ""
