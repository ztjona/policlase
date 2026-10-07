# Prueba de carga de una clase en vivo

Simula N teléfonos (invitados con su propio flujo SSE) que responden con demoras aleatorias,
mientras un docente avanza la presentación por HTTP. Mide cuánto tarda cada cambio en llegar a
todos los teléfonos, la latencia de las respuestas, el cierre automático y la retroalimentación.

```bash
pip install httpx
POLICLASE_URL=https://policlase.org POLICLASE_DOCENTE=profe POLICLASE_CLAVE=... \
POLICLASE_CURSO=1 POLICLASE_PRESENTACION=1 python carga.py 30
```

Úsela con un curso y una presentación de prueba, **fuera del horario de clases**. Al final,
borre lo que creó (`SESSION` se imprime al terminar):

```bash
docker compose exec web python manage.py shell -c "
from apps.accounts.models import User; from apps.live.models import LiveSession
User.objects.filter(role='guest', first_name__startswith='Carga ').delete()
LiveSession.objects.filter(pk=SESSION).delete()"
```

Resultados en el VPS de 2 núcleos (2026-10-07), con el simulador corriendo en la misma máquina:

| Estudiantes | Errores | Cambio de diapositiva en los teléfonos (mediana / peor) | Conexiones Postgres |
|---|---|---|---|
| 30 | 0 | 0,3 s / 0,7 s | 32 de 100 |
| 90 | 0 | 0,4 s / 1,5 s | 33 de 100 |
