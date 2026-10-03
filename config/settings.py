"""Configuración de policlase.

Todo lo que cambia entre el prototipo y producción —dominio, HTTPS, correo, secretos— llega
por variables de entorno (ver `.env.example`). El dominio definitivo aún no está comprado, así
que ningún nombre de host está escrito aquí.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, "1" if default else "0").strip().lower() in {"1", "true", "yes", "on"}


DEBUG = env_bool("DJANGO_DEBUG")
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "solo-para-desarrollo-no-usar-en-produccion"
    else:
        raise RuntimeError("Defina DJANGO_SECRET_KEY (ver .env.example).")

# --------------------------------------------------------------------------- dominio

DOMAIN = env("POLICLASE_DOMAIN").strip()
HTTPS = env_bool("POLICLASE_HTTPS")

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "web"]
CSRF_TRUSTED_ORIGINS = [f"http://localhost:{env('POLICLASE_PORT', '8100')}",
                        f"http://127.0.0.1:{env('POLICLASE_PORT', '8100')}"]
if DOMAIN:
    ALLOWED_HOSTS.append(DOMAIN)
    CSRF_TRUSTED_ORIGINS.append(f"{'https' if HTTPS else 'http'}://{DOMAIN}")

# ------------------------------------------------------------------------- aplicación

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "apps.core",
    "apps.accounts",
    "apps.courses",
    "apps.live",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.PreferencesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------- base de datos


def _database(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or 5432),
        # Las conexiones SSE viven lo que dura la clase: reutilizar la conexión evita abrir
        # una nueva en cada consulta de sondeo.
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


DATABASE_URL = env("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": _database(DATABASE_URL)}
else:  # solo para ejecutar manage.py fuera de Docker sin base de datos (p. ej. collectstatic)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "dev.sqlite3"}}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
SITE_ID = 1

# ------------------------------------------------------------------------- contraseñas

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# -------------------------------------------------------------------------- allauth
# Usuario y contraseña clásicos, con verificación obligatoria del correo y recuperación de
# contraseña. El correo se verifica una sola vez; iniciar sesión nunca requiere un correo.

ACCOUNT_LOGIN_METHODS = {"username", "email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "username*", "password1*", "password2*"]
ACCOUNT_SIGNUP_FORM_CLASS = "apps.accounts.forms.ProfileSignupForm"
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_CONFIRM_EMAIL_ON_GET = False
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = False
ACCOUNT_PASSWORD_RESET_BY_CODE_ENABLED = False
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[policlase] "
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https" if HTTPS else "http"
ACCOUNT_SESSION_REMEMBER = None           # el usuario elige "recordarme"
ACCOUNT_PREVENT_ENUMERATION = True        # no revela si un correo está registrado
# Límites de intentos por defecto de allauth (inicio de sesión fallido, registro,
# recuperación); se dejan explícitos para que sean visibles.
ACCOUNT_RATE_LIMITS = {
    "login_failed": "10/m/ip,5/5m/key",
    "signup": "20/m/ip",
    "reset_password": "20/m/ip,5/m/key",
    "confirm_email": "1/3m/key",
}

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "home"
ACCOUNT_LOGOUT_REDIRECT_URL = "account_login"

# ----------------------------------------------------------------------------- correo

_backend = env("POLICLASE_EMAIL_BACKEND", "console")
EMAIL_BACKEND = {
    "console": "django.core.mail.backends.console.EmailBackend",
    "smtp": "django.core.mail.backends.smtp.EmailBackend",
    "locmem": "django.core.mail.backends.locmem.EmailBackend",
}.get(_backend, _backend)
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "policlase <no-responder@localhost>")

# --------------------------------------------------------------------------- seguridad

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

if HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("POLICLASE_HSTS_SECONDS", "0"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False

# ---------------------------------------------------------------------- idioma y hora

# El español es el idioma fuente; las traducciones al inglés viven en locale/en. Cada usuario
# guarda su idioma y su zona horaria (apps.accounts.middleware); sin sesión, el idioma sale de
# la cookie o del navegador, y la zona horaria la detecta el navegador.
LANGUAGE_CODE = "es"
LANGUAGES = [("es", "Español"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
LANGUAGE_COOKIE_AGE = 365 * 24 * 3600
LANGUAGE_COOKIE_SAMESITE = "Lax"
LANGUAGE_COOKIE_SECURE = HTTPS
#: Respaldo cuando el navegador aún no informó su zona horaria.
TIME_ZONE = "America/Guayaquil"
TIMEZONE_COOKIE = "policlase_tz"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------- estáticos

STATIC_URL = "/static/"
# Fuera de /app: en desarrollo el código se monta encima de /app y ocultaría lo recolectado.
STATIC_ROOT = Path(env("POLICLASE_STATIC_ROOT", str(BASE_DIR / "staticfiles")))
STATICFILES_DIRS = [BASE_DIR / "static"]
if Path("/opt/vendor/static").is_dir():
    STATICFILES_DIRS.append(Path("/opt/vendor/static"))
TESTING = len(sys.argv) > 1 and sys.argv[1] == "test"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # En producción, nombres con hash para caché permanente; en desarrollo y pruebas, los
    # archivos tal cual, para que un cambio en app.css se vea sin recolectar de nuevo.
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
                    if DEBUG or TESTING else
                    "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
WHITENOISE_USE_FINDERS = DEBUG
MEDIA_URL = "/media/"
MEDIA_ROOT = env("POLICLASE_MEDIA_ROOT", str(BASE_DIR / "media"))

# ----------------------------------------------------------------------------- clases en vivo

#: Cada cuánto consulta el flujo SSE el estado de la sesión. 0.5 s con un aula de 30
#: estudiantes son ~60 consultas triviales por segundo: nada para Postgres, y la latencia
#: percibida al avanzar una diapositiva queda por debajo de un segundo.
LIVE_POLL_SECONDS = float(env("POLICLASE_LIVE_POLL_SECONDS", "0.5"))
LIVE_HEARTBEAT_SECONDS = 15

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.request": {"level": "WARNING"}},
}
