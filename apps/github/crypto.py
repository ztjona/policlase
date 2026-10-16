"""Cifrado de los tokens de GitHub y de las claves de IA en la base de datos.

La clave sale de DJANGO_SECRET_KEY: un respaldo de la base por sí solo no expone los tokens.
Cambiar la clave secreta los invalida y cada docente debe volver a pegar el suyo.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet(purpose: str) -> Fernet:
    digest = hashlib.sha256(f"policlase-{purpose}:".encode() + settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(token: str, purpose: str = "github-token") -> str:
    """`purpose` separa las claves: un token de GitHub no se descifra como una clave de IA."""
    return _fernet(purpose).encrypt(token.encode()).decode()


def decrypt(value: str, purpose: str = "github-token") -> str:
    try:
        return _fernet(purpose).decrypt(value.encode()).decode()
    except InvalidToken:
        return ""
