#!/bin/sh
# ADVERTENCIA: borra la base de datos de POLICLASE_DATA. Solo para el prototipo.
# Base limpia + datos de demostración, para una corrida determinista de e2e.py
set -e
cd "$(dirname "$0")/../.."
docker compose down >/dev/null 2>&1
sudo rm -rf /mnt/mydrive/policlase/postgres
docker compose up -d >/dev/null 2>&1
for i in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:8100/cuenta/login/ && break; sleep 1; done
docker compose exec -T web python manage.py seed_demo | head -1
