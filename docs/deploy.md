# Publicar policlase con su dominio

El prototipo responde solo en `http://127.0.0.1:8100`. Publicarlo es configuración, no código:
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

POLICLASE_EMAIL_BACKEND=smtp      # los estudiantes deben recibir la verificación y la recuperación
EMAIL_HOST=smtp.su-proveedor.com
EMAIL_PORT=587
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=policlase <no-responder@aula.ejemplo.ec>
```

Para el correo, la opción más simple con dominio propio es un servicio transaccional (Brevo,
Mailgun, Amazon SES, Postmark…) con los registros SPF y DKIM que le pida en el DNS. Sin ellos, los
correos de verificación terminan en la carpeta de spam.

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
- Respalde `/mnt/mydrive/policlase` fuera del VPS.

## Actualizar con clases en curso

`docker compose ... up -d --build` reinicia la plataforma: los teléfonos se reconectan solos en
unos segundos, pero es mejor no actualizar en medio de una clase. Antes, revise que no haya ninguna
en vivo con gente conectada:

```bash
docker compose exec web python manage.py shell -c "
from django.utils import timezone; from datetime import timedelta; from apps.live.models import Participant
print(Participant.objects.filter(last_seen__gte=timezone.now() - timedelta(minutes=2)).count(), 'conectados')"
```
