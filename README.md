# policlase

Aula virtual autoalojada para cursos universitarios con carga matemática.

**Estado: prototipo.** Funciona de punta a punta:

- **Cuentas** con usuario y contraseña (django-allauth): registro, verificación obligatoria del
  correo, recuperación y cambio de contraseña, límites de intentos, contraseñas con Argon2.
- **Docentes** se registran solos en `/cuenta/registro-docente/`. Cada uno tiene su espacio: ningún
  docente ve ni modifica cursos, estudiantes, presentaciones ni resultados de otro.
- **Cursos** del docente con **código de inscripción**. Cualquiera crea su cuenta; para entrar a un
  curso ingresa el código y el docente **aprueba** (todas de una vez, o selecciona y rechaza).
  Solo los aprobados ven el curso.
- **Clases en vivo** al estilo Kahoot: el docente proyecta y avanza, los estudiantes responden desde
  el teléfono, con cuenta regresiva, cierre automático, resultados y marcador. Las presentaciones
  se escriben en YAML (`policlase.deck/v1`) y se validan con [`policlase-gen`](../policlase-gen).
  El docente puede **abrir la clase a invitados**: un enlace (y su QR en el proyector) deja entrar
  sin cuenta, para charlas o visitas.
- **Un solo formato y un solo editor** para clases, actividades (y luego evaluaciones):
  `policlase/v1`, donde `use` dice para qué es y los **segmentos** cambian el comportamiento de lo
  que sigue (por ejemplo, 15 s por pregunta y sin pegar). **Encuestas** sin respuesta correcta.
  Monaco (el editor de VS Code, mismos atajos) con autocompletado según el esquema; pegar una
  actividad convierte el editor. Panel lateral con el tipo, la sección y las diapositivas o ítems
  para reordenar (y ocultar diapositivas, como en PowerPoint), vista previa en vivo, LaTeX de la línea
  del cursor renderizado y plantillas para cada tipo de pregunta.
- **Clases en vivo**: pausar y retomar otro día donde quedó, +15 s / reabrir una pregunta, cierre
  automático cuando respondieron todos los conectados, panel de diapositivas en el proyector (saltar,
  ocultar o mostrar sobre la marcha), **bono por rapidez** en el marcador (100 % al instante, 50 % al
  final del tiempo; la nota de participación solo cuenta acertar) y una pregunta final de
  **retroalimentación anónima** (sin vínculo con el estudiante; el docente ve el resumen desde 3 respuestas).
- **Curso en pestañas** Clases | Evaluaciones | Actividades. En Clases, **secciones** (unidades) con
  sus presentaciones y sus actividades; solo la unidad en curso viene desplegada. Actividades reúne
  todas, por parcial y filtrables por componente de la ponderación. Con GitHub, cada carpeta con
  presentaciones o actividades es una sección.
- **Actividades** (`policlase.activity/v1`): deberes que cada estudiante llena en línea a su paso,
  entregas de **imágenes** (evidencia, certificados, el desarrollo a mano; JPEG, PNG o WebP
  re-codificados sin metadatos) o ambas cosas. Fechas fijas o **relativas a una fecha clave** (mover
  la prueba mueve sus deberes), intentos (cuenta el último), atrasos bloqueados o con penalización,
  rúbrica visible, revisión **por pregunta**, preguntas marcadas para IA (aún sin activar), marca de
  agua con el nombre y señales para el docente (pegar texto, salir de la página). En las respuestas
  abiertas, el LaTeX entre `$…$` se ve renderizado.
- **Ponderaciones por parcial** con componentes obligatorios (100 %) y opcionales que suman encima,
  nota del parcial topada en su máximo, reglas de aprobación y supletorio configurables, libro de
  calificaciones del docente y vista de calificaciones del estudiante. **Registros manuales** para lo
  hecho fuera de policlase (pegar una lista «nombre puntaje» la empareja con los inscritos), **nota
  manual** de un estudiante en una clase e invitados que se asignan a su cuenta al inscribirse.
- **Sincronización con GitHub** por curso: cada docente conecta un token *fine-grained* (Contents:
  lectura y escritura) y vincula una carpeta de su repositorio. Lo que se empuja a GitHub aparece en
  la plataforma (al abrir el curso, o al instante con el webhook opcional); lo que se guarda en el
  editor se confirma en GitHub, y si el archivo cambió allá entretanto no se pisa nada.
- **Tema claro/oscuro/automático** por usuario; el proyector usa el claro salvo que se elija oscuro.

## Arranque

```bash
cp .env.example .env                  # complete DJANGO_SECRET_KEY y POSTGRES_PASSWORD
docker compose up -d --build          # http://127.0.0.1:8100
# docentes: http://127.0.0.1:8100/cuenta/registro-docente/  (o desde el servidor:)
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
$DEV run --rm web sh -c "python manage.py makemessages -l en --ignore 'staticfiles/*' --ignore 'tests/*' \
  && python manage.py makemessages -d djangojs -l en --ignore 'staticfiles/*'"
# traduzca lo nuevo en locale/en/LC_MESSAGES/*.po; la imagen compila los .mo al construirse
```

## Publicación con dominio propio

Ver [`docs/deploy.md`](docs/deploy.md).

## Más documentación

- [`docs/github.md`](docs/github.md) — sincronización de presentaciones con GitHub.
- [`docs/guia-de-estilo.md`](docs/guia-de-estilo.md) — guía de estilo y patrones de diseño.

## Licencia

MIT.
