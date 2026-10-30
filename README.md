# policlase

Aula virtual autoalojada para cursos universitarios con carga matemática.

**Estado: en uso** en [policlase.org](https://policlase.org) con un curso real (versión en el archivo
`VERSION`, visible en el pie de página). Funciona de punta a punta:

- **Cuentas** con usuario y contraseña (django-allauth): estudiantes con el código de un curso abierto
  y docentes por invitación de un colega, confirmación del correo (obligatoria para docentes),
  recuperación de contraseña, límites de intentos y Argon2. Cada docente tiene su espacio: nadie ve ni
  modifica lo de otro (lo ajeno responde 404).
- **Cursos** con código y enlace de inscripción, fecha de cierre opcional y aprobación del docente;
  nombre preferido de cada estudiante.
- **Un solo formato y un solo editor** (`policlase/v1`, validado por [`policlase-gen`](../policlase-gen)):
  `use` dice si es una clase en vivo, una actividad, una evaluación o un repaso, y los **segmentos**
  cambian el comportamiento de lo que sigue (tiempo por pregunta, pegar). Monaco con autocompletado,
  vista previa en vivo, panel para reordenar y ocultar, y **sincronización con GitHub** por curso
  (cada carpeta es una unidad; lo que se guarda se confirma en GitHub y nunca se pisa un cambio ajeno).
- **Clases en vivo**: el docente proyecta y los estudiantes responden desde el teléfono, sin cuenta
  regresiva a la vista; pausar y retomar, reabrir, bono por rapidez solo en el marcador, invitados por
  enlace o QR y retroalimentación anónima. Las notas del docente se ven solo en un tooltip de sus controles.
- **Actividades** (deberes y talleres): fechas fijas o relativas a una fecha clave, intentos, atrasos
  bloqueados o penalizados con envío automático al vencer, preguntas con tiempo, entregas de imágenes,
  **debate** entre compañeros, revisión con explicaciones y prórrogas por estudiante.
- **Evaluaciones**: horario, duración por estudiante, código de acceso proyectado, partes en un orden
  distinto para cada estudiante, notas que el docente publica, revisión hasta unos días después y
  **corrección** opcional (volver a resolverla completa, en su propia fila de la ponderación).
- **Repasos** sin nota, con «Comprobar» por pregunta y explicaciones.
- **Calificación**: automática por tipo de pregunta, manual por pregunta o por estudiante, y
  **asistida por IA** (DeepSeek) en las respuestas abiertas: la IA propone y el docente decide.
- **Entregas y estadísticas**: tabla ordenable con avance y señales (pegar, salir de la página y por
  cuánto tiempo), revisión de cada intento con índice y estado de las preguntas, estadísticas por
  pregunta y por estudiante, y un diagrama de fechas y plazos en cada actividad.
- **Ponderaciones por parcial** con filas obligatorias y opcionales, reglas de aprobación y
  supletorio, planilla ordenable, **registros manuales** pegados desde una hoja de cálculo y
  exportación a Moodle.
- **Español e inglés**, tema claro, oscuro o automático por usuario.

## Arranque

```bash
cp .env.example .env                  # complete DJANGO_SECRET_KEY y POSTGRES_PASSWORD
docker compose up -d --build          # http://127.0.0.1:8100
# primer docente, desde el servidor; los demás, con su invitación («Invitar docentes», en el menú de usuario):
docker compose exec web python manage.py create_teacher --username profe --email profe@ejemplo.ec
docker compose exec web python manage.py seed_demo       # opcional: datos de demostración
```

Los datos viven en `POLICLASE_DATA` (por defecto `/mnt/mydrive/policlase`).

## Desarrollo

```bash
DEV="docker compose -f compose.yaml -f compose.dev.yaml"
$DEV up                                # recarga en caliente, correos en los logs
$DEV run --rm web python manage.py test apps
```

## Idiomas

Español (idioma fuente) e inglés. Cada usuario elige idioma y zona horaria en el menú de usuario;
la zona horaria se detecta del navegador. Tras cambiar textos:

```bash
$DEV run --rm --no-deps -u "$(id -u):$(id -g)" web python manage.py makemessages -l en --ignore "staticfiles/*" --ignore "tests/*" --no-obsolete
$DEV run --rm --no-deps -u "$(id -u):$(id -g)" web python manage.py makemessages -d djangojs -l en --ignore "staticfiles/*" --no-obsolete
# traduzca lo nuevo en locale/en/LC_MESSAGES/*.po; la imagen compila los .mo al construirse
```

## Publicación con dominio propio

Ver [`docs/deploy.md`](docs/deploy.md).

## Más documentación

- [`docs/github.md`](docs/github.md) — sincronización con GitHub.
- [`docs/guia-de-estilo.md`](docs/guia-de-estilo.md) — guía de estilo y patrones de diseño.
- [`docs/deploy.md`](docs/deploy.md) — publicación, respaldos y operación.
- [`policlase-gen/docs/schema.md`](../policlase-gen/docs/schema.md) — el formato (normativo).

## Licencia

MIT.
