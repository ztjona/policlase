# Publicar policlase con su dominio

La aplicación responde solo en `http://127.0.0.1:8100`. Publicarla es configuración, no código:
nada en la plataforma lleva el dominio escrito.

A lo largo de la guía, `aula.ejemplo.ec` es su dominio nuevo.

## 1. DNS

En el panel del registrador, cree un registro **A** de `aula.ejemplo.ec` hacia la IP pública del
VPS. Compruebe que resuelve antes de seguir:

```bash
dig +short aula.ejemplo.ec
```

## 2. Certificado

El nginx del servidor (`/home/ubuntu/automat`) ya sirve `/.well-known/acme-challenge/` para sus
otros dominios. Primero agregue en `/home/ubuntu/automat/nginx.conf` el bloque HTTP del dominio nuevo,
solo con el desafío:

```nginx
server {
    listen 80;
    server_name aula.ejemplo.ec;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}
```

```bash
docker exec n8n-nginx nginx -t && docker exec n8n-nginx nginx -s reload
docker run --rm \
  -v /home/ubuntu/automat/certbot-conf:/etc/letsencrypt \
  -v /home/ubuntu/automat/certbot-www:/var/www/certbot \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d aula.ejemplo.ec --email su-correo@ejemplo.ec --agree-tos --no-eff-email
```

> **Renovación.** Un cron diario del usuario `ubuntu` (03:17) corre `certbot renew` y recarga el
> nginx; el registro queda en `/home/ubuntu/automat/certbot-renew.log`. El contenedor
> `n8n-certbot` ya no se usa.
>
> **Ojo al editar `nginx.conf`.** Está montado como archivo suelto: si el editor lo reemplaza en
> vez de escribirlo en el sitio, el contenedor sigue viendo la versión vieja y `nginx -s reload` no
> aplica nada. Compruébelo con `docker exec n8n-nginx grep <algo-nuevo> /etc/nginx/nginx.conf`; si no
> aparece, `docker restart n8n-nginx` (corte de un segundo para todos los sitios).

## 3. Bloque HTTPS

Agregue al mismo `nginx.conf`:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name aula.ejemplo.ec;

    ssl_certificate     /etc/letsencrypt/live/aula.ejemplo.ec/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aula.ejemplo.ec/privkey.pem;

    client_max_body_size 30m;

    location / {
        proxy_pass http://policlase-web:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    # Clases en vivo: el flujo SSE no debe acumularse ni cortarse a los 60 s.
    location ~ ^/en-vivo/\d+/eventos/$ {
        proxy_pass http://policlase-web:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3h;
    }
}
```

## 4. Variables de entorno

En `policlase/.env`:

```bash
POLICLASE_DOMAIN=aula.ejemplo.ec
POLICLASE_HTTPS=1
POLICLASE_HSTS_SECONDS=0          # súbalo a 31536000 tras una semana sin problemas

POLICLASE_EMAIL_BACKEND=smtp      # confirmación del correo y recuperación de contraseña
EMAIL_HOST=smtp.su-proveedor.com
EMAIL_PORT=587
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=policlase <no-responder@aula.ejemplo.ec>
```

Para el correo, la opción más simple con dominio propio es un servicio transaccional (Brevo,
Mailgun, Amazon SES, Postmark…) con los registros SPF y DKIM que le pida en el DNS. Sin ellos, los
correos de verificación terminan en la carpeta de spam.

Aun con SPF, DKIM y DMARC correctos, algunos servidores institucionales (Microsoft 365) retienen en
cuarentena el correo de un dominio nuevo sin avisarle al destinatario. Por eso la confirmación del
correo es **obligatoria solo para docentes** (el registro de docentes es abierto) y **opcional para
estudiantes**, que entran de inmediato: el docente aprueba cada inscripción por nombre y cédula, y el
estudiante ve en «Mis cursos» un recordatorio para confirmar su correo, necesario para recuperar la
contraseña (`apps/accounts/stages.py`). Si una institución retiene los correos, lo más efectivo es
pedirle a su área de TI que agregue el dominio como remitente permitido.

Cada registro envía un correo a la dirección que se escriba en el formulario, sea de quien sea. Por eso
los dos registros llevan un campo trampa invisible (`ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD`): quien lo llena
recibe la respuesta normal, pero no se crea la cuenta ni se envía nada. Si en el panel del servicio de
correo aparecen confirmaciones dirigidas a desconocidos, compare el formato de las cuentas nuevas
(nombres y usuarios al azar) con los accesos a `/cuenta/signup/` en el registro de nginx.

## 5. Levantar detrás del nginx

```bash
cd /home/ubuntu/policlase/policlase
docker compose -f compose.yaml -f compose.proxy.yaml up -d --build
docker exec n8n-nginx nginx -t && docker exec n8n-nginx nginx -s reload
```

`sync_site` corre en cada arranque y pone el dominio en los enlaces de los correos.

## 6. Antes de la primera clase real

- Solo en una instalación **nueva**, antes de que entre nadie: borre los datos de demostración
  (`docker compose down && sudo rm -rf /mnt/mydrive/policlase/postgres`) y vuelva a levantar. Con
  estudiantes registrados, **nunca**: borra todas las cuentas y resultados. Cree su cuenta con
  `create_teacher --admin` (los colegas se registran en `/cuenta/registro-docente/`).
- Configure los respaldos (abajo) y una copia fuera del VPS.

## Respaldos

`ops/respaldar.sh` guarda la base (`pg_dump` en formato personalizado, comprobado con `pg_restore -l`)
y las imágenes entregadas en `/mnt/mydrive/policlase-respaldos/`, solo legibles por su dueño. Conserva
los diarios de 30 días y el del día 1 de cada mes durante un año. Solo lee: se puede correr en
cualquier momento, también con clases en curso.

```bash
ops/respaldar.sh                       # a mano; conviene antes de cada despliegue (corre migraciones)
# diario, a las 04:30 (crontab -e del usuario ubuntu):
30 4 * * * /home/ubuntu/policlase/policlase/ops/respaldar.sh >> /mnt/mydrive/policlase-respaldos/respaldo.log 2>&1
```

Restaurar (en una base vacía; **nunca** sobre la de producción en uso):

```bash
docker compose exec -T db pg_restore -U policlase -d policlase --no-owner < /mnt/mydrive/policlase-respaldos/policlase-AAAA-MM-DD_HHMM.dump
docker compose exec -T web tar -C /data -xzf - < /mnt/mydrive/policlase-respaldos/media-AAAA-MM-DD_HHMM.tar.gz
```

Para probar un respaldo sin tocar nada, restáurelo en un contenedor desechable
(`docker run --rm -d --name prueba -e POSTGRES_PASSWORD=x -e POSTGRES_USER=policlase postgres:16-alpine`)
y compare los conteos de tablas con producción.

**Fuera del VPS.** El disco `/mnt/mydrive` es un volumen aparte, pero un respaldo en el mismo
servidor no protege de perder la cuenta o el centro de datos. Copie `policlase-respaldos/` a otro
lugar: la política de respaldos del volumen en la consola de Oracle Cloud, o `rclone` a un
almacenamiento en la nube. El contenido de los cursos ya está en GitHub; las cuentas, inscripciones
y notas solo están en la base.

## Actualizar con clases en curso

**`ops/desplegar.sh` hace todo esto en orden:** se niega si hay alguien en una clase en vivo,
respalda, construye, corre `manage.py check` en la imagen nueva con la configuración de producción
(si falla, no toca lo que corre), levanta y verifica que responda con la versión de `VERSION`; si no
responde en 90 s, vuelve sola a la imagen anterior. La imagen
desplegada queda como `policlase-web:desplegada` y la anterior como `policlase-web:anterior`;
`ops/desplegar.sh --volver` regresa a ella (si una migración cambió la base, restaure también el
respaldo). Quien tenga un deber abierto no pierde nada: su borrador está en la base y el
autoguardado reintenta hasta que vuelve el servidor.

A mano: `docker compose ... up -d --build` reinicia la plataforma y aplica las migraciones; corra antes
`ops/respaldar.sh`. Los teléfonos se reconectan solos en unos segundos, pero es mejor no actualizar
en medio de una clase. Antes, revise que no haya ninguna en vivo con gente conectada:

```bash
docker compose exec web python manage.py shell -c "
from django.utils import timezone; from datetime import timedelta; from apps.live.models import Participant
print(Participant.objects.filter(last_seen__gte=timezone.now() - timedelta(minutes=2)).count(), 'conectados')"
```
