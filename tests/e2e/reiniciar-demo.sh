#!/bin/sh
# ADVERTENCIA: borra la base de datos de POLICLASE_DATA. Solo para una instalación de prueba.
# Base limpia + datos de demostración, para una corrida determinista de e2e.py
set -e
cd "$(dirname "$0")/../.."

# Salvaguarda: con un dominio configurado esto es producción, con cuentas reales.
if grep -Eq '^POLICLASE_DOMAIN=.+' .env 2>/dev/null; then
  echo "Hay un dominio en .env (POLICLASE_DOMAIN): esto es producción. No se borra nada." >&2
  echo "Corra e2e.py contra una instalación de prueba, con su propio POLICLASE_DATA." >&2
  exit 1
fi
DATA="${POLICLASE_DATA:-$(sed -n 's/^POLICLASE_DATA=//p' .env 2>/dev/null)}"
DATA="${DATA:-/mnt/mydrive/policlase}"
printf 'Se borrará %s/postgres. Escriba BORRAR para continuar: ' "$DATA"
read answer
[ "$answer" = "BORRAR" ] || { echo "Cancelado."; exit 1; }

docker compose down >/dev/null 2>&1
sudo rm -rf "$DATA/postgres"
docker compose up -d >/dev/null 2>&1
for i in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:8100/cuenta/login/ && break; sleep 1; done
docker compose exec -T web python manage.py seed_demo | head -1
