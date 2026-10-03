#!/bin/sh
set -e

case "$1" in
  serve)
    python manage.py migrate --noinput
    python manage.py sync_site
    # Dos procesos para dos núcleos. Las conexiones SSE son asíncronas: no ocupan un
    # proceso cada una, así que 2 bastan para un aula completa conectada a la vez.
    exec uvicorn config.asgi:application \
      --host 0.0.0.0 --port 8000 \
      --workers "${POLICLASE_WORKERS:-2}" \
      --proxy-headers --forwarded-allow-ips="*" \
      --timeout-graceful-shutdown 5
    ;;
  *)
    exec "$@"
    ;;
esac
