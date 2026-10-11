#!/bin/sh
# Respaldo de policlase: la base de datos (pg_dump) y las imágenes entregadas, con rotación.
# Lo corre un cron diario y conviene correrlo antes de cada despliegue (docs/deploy.md, «Respaldos»).
# Solo lee: no detiene nada ni toca los datos en uso.
set -eu
cd "$(dirname "$0")/.."

DATA="${POLICLASE_DATA:-$(sed -n 's/^POLICLASE_DATA=//p' .env 2>/dev/null)}"
DATA="${DATA:-/mnt/mydrive/policlase}"
DEST="${POLICLASE_RESPALDOS:-$DATA-respaldos}"
STAMP="$(date +%Y-%m-%d_%H%M)"
umask 077                                  # los respaldos llevan cuentas y notas: solo el dueño
mkdir -p "$DEST"

# Formato personalizado de pg_dump: comprimido y restaurable tabla por tabla con pg_restore.
docker compose exec -T db pg_dump -U policlase -d policlase -Fc > "$DEST/policlase-$STAMP.dump.part"
# Un respaldo que no se puede leer no es un respaldo: se comprueba antes de darlo por bueno.
docker compose exec -T db pg_restore -l < "$DEST/policlase-$STAMP.dump.part" > /dev/null
mv "$DEST/policlase-$STAMP.dump.part" "$DEST/policlase-$STAMP.dump"

# Las imágenes son del usuario del contenedor: se empaquetan desde adentro.
docker compose exec -T web tar -C /data -czf - media > "$DEST/media-$STAMP.tar.gz.part"
mv "$DEST/media-$STAMP.tar.gz.part" "$DEST/media-$STAMP.tar.gz"

# Rotación: los diarios de los últimos 30 días y el del día 1 de cada mes durante un año.
find "$DEST" -maxdepth 1 \( -name 'policlase-*.dump' -o -name 'media-*.tar.gz' \) -mtime +30 \
  ! -name '*-01_*' -delete
find "$DEST" -maxdepth 1 \( -name 'policlase-*.dump' -o -name 'media-*.tar.gz' \) -mtime +400 -delete
find "$DEST" -maxdepth 1 -name '*.part' -mtime +1 -delete

echo "$(date '+%F %T') respaldo $STAMP: base $(du -h "$DEST/policlase-$STAMP.dump" | cut -f1)," \
     "imágenes $(du -h "$DEST/media-$STAMP.tar.gz" | cut -f1)"
