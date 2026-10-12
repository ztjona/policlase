#!/bin/sh
# Desplegar policlase con las comprobaciones de siempre (docs/deploy.md, «Actualizar con clases en curso»):
#   1. nadie en una clase en vivo (si hay alguien, no se despliega);
#   2. respaldo de la base y las imágenes;
#   3. la imagen en uso queda como policlase-web:anterior, para volver atrás;
#   4. construir, levantar (corre las migraciones) y verificar que responde con la versión nueva.
#
#   ops/desplegar.sh            despliega el árbol de trabajo actual
#   ops/desplegar.sh --volver   vuelve a la imagen anterior (si una migración cambió la base,
#                               restaure también el respaldo: ver «Respaldos» en docs/deploy.md)
set -eu
cd "$(dirname "$0")/.."
COMPOSE="docker compose -f compose.yaml -f compose.proxy.yaml"
LOCAL="http://127.0.0.1:${POLICLASE_PORT:-8100}"

wait_ready() {
  for _ in $(seq 1 90); do
    curl -sf -o /dev/null "$LOCAL/cuenta/login/" && return 0
    sleep 1
  done
  echo "La plataforma no respondió en 90 s: revise «docker compose logs web»." >&2
  return 1
}

if [ "${1:-}" = "--volver" ]; then
  docker image inspect policlase-web:anterior > /dev/null
  docker tag policlase-web:anterior policlase-web:latest
  docker tag policlase-web:anterior policlase-web:desplegada
  $COMPOSE up -d --no-build web
  wait_ready
  echo "De vuelta en la imagen anterior: $(curl -s "$LOCAL/cuenta/login/" | grep -o 'policlase [0-9.]*' | head -n 1)"
  exit 0
fi

# 1. Una clase en vivo con gente conectada no se interrumpe. Quien tiene una actividad abierta no
#    pierde nada: su borrador está en la base y el autoguardado reintenta hasta que vuelve el servidor.
BUSY="$(docker compose exec -T web python manage.py shell -c "
from datetime import timedelta
from django.utils import timezone
from apps.activities.models import Attempt
from apps.live.models import Participant
ago = timezone.now() - timedelta(minutes=2)
print(Participant.objects.filter(last_seen__gte=ago).count(),
      Attempt.objects.filter(submitted_at__isnull=True, saved_at__gte=ago).count())" 2>/dev/null | tail -n 1)"
LIVE="${BUSY%% *}"
DRAFTS="${BUSY##* }"
if [ "${LIVE:-x}" != "0" ]; then
  echo "Hay ${LIVE:-?} personas en una clase en vivo (o no se pudo comprobar): no se despliega." >&2
  exit 1
fi
[ "${DRAFTS:-0}" = "0" ] || echo "Aviso: ${DRAFTS} estudiantes trabajando en una actividad; sus borradores se conservan."

# 2. Respaldo: el despliegue corre migraciones.
ops/respaldar.sh

# 3. Lo que corre ahora queda a mano para volver atrás. La imagen desplegada lleva su propia
#    etiqueta (`desplegada`): `latest` cambia cada vez que se construye para correr las pruebas.
docker tag policlase-web:desplegada policlase-web:anterior 2>/dev/null \
  || echo "Aviso: no hay imagen desplegada etiquetada; esta vez no habrá --volver (el respaldo sí)."

# 4. Construir y levantar. Si la construcción falla, sigue corriendo la versión de antes.
$COMPOSE up -d --build
wait_ready
VERSION="$(cat VERSION)"
if curl -s "$LOCAL/cuenta/login/" | grep -q "policlase $VERSION"; then
  docker tag policlase-web:latest policlase-web:desplegada
  echo "Desplegada policlase $VERSION."
else
  echo "La plataforma responde, pero no muestra la versión $VERSION: revise." >&2
  exit 1
fi
# Las imágenes viejas sin nombre (las anteriores a «anterior») solo ocupan disco.
docker image prune -f > /dev/null
