"""Alinea el `Site` de Django con POLICLASE_DOMAIN.

allauth usa el dominio del sitio para construir los enlaces de verificación y de
recuperación de contraseña. Se ejecuta en cada arranque, así que cambiar el dominio es
solo cambiar la variable de entorno.
"""

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sincroniza el dominio del sitio con POLICLASE_DOMAIN."

    def handle(self, *args, **options):
        domain = settings.DOMAIN or f"localhost:{settings.CSRF_TRUSTED_ORIGINS[0].rsplit(':', 1)[-1]}"
        site, _ = Site.objects.update_or_create(
            pk=settings.SITE_ID, defaults={"domain": domain, "name": "policlase"}
        )
        self.stdout.write(f"Sitio: {site.domain}")
