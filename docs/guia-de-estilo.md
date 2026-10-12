# Guía de estilo y patrones de diseño

Cómo se construye policlase: los principios de producto, el diseño visual, los textos y los patrones
de código que ya están en uso. Sirve para mantener la coherencia entre sesiones de trabajo y entre
personas. Cuando una decisión nueva contradiga esta guía, se actualiza la guía en el mismo cambio.

Repositorios: `policlase` (plataforma) y `policlase-gen` (formato y validador, ver
[`schema.md`](../../policlase-gen/docs/schema.md)). Sincronización con GitHub: [`github.md`](github.md).

---

## 1. Principios de producto

1. **El frente de cada página muestra solo lo de hoy.** Lo que se configura una vez vive detrás de
   la rueda (configuración del curso) o en el menú de usuario: inscribirse en un curso, entrar con
   PIN, contraseña, correo, preferencias, GitHub, «Acerca de». Las solicitudes pendientes aparecen
   en el frente solo cuando las hay.
2. **No se anuncia lo normal.** Una inscripción aprobada no lleva etiqueta; un rechazo es un aviso
   que el estudiante cierra.
3. **Cada acción aparece en un solo lugar.** Nada de botones duplicados arriba y abajo.
4. **Separación estricta entre docentes.** Un docente nunca ve ni modifica nada de otro: todo acceso
   pasa por `apps/courses/access.py` y lo ajeno responde **404** (no 403: no se confirma que exista).
5. **El proyector lo ve la clase.** Nada que se muestre en el proyector —tampoco al pasar el mouse—
   revela respuestas correctas ni notas del docente (`_slide_card.html` con `reveal=False`).
6. **Anonimato de verdad.** La retroalimentación se guarda sin estudiante y sin hora, y el resumen
   solo aparece con 3 respuestas o más.
7. **La nota y el juego son cosas distintas.** El bono por rapidez y los puntos de juego (×1000) son
   del marcador; la nota de participación solo cuenta acertar. Una corrección manual cambia la nota,
   nunca el marcador, y la calificación automática se conserva (`auto_points`, `auto_correct`).
8. **GitHub es la fuente de verdad** del contenido: se confirma allá antes de guardar aquí, y ante un
   conflicto no se pisa nada (ver [`github.md`](github.md)).
9. **El código del docente no corre en el servidor.** Los generadores de `policlase-gen` se ejecutan
   en la máquina del autor; el registro de docentes es abierto y ejecutarlos aquí sería entregar el
   servidor.
10. **Identidad propia.** El logo y la paleta son de policlase, no de una institución. Una identidad
    institucional, si se autoriza, sería una edición configurable, no el logo base.

## 2. Diseño visual

### Color

Todo color sale de las variables de `static/css/app.css`; no se escriben colores sueltos en las
plantillas. Hay dos juegos (claro y oscuro) y el tema lo elige cada usuario (`data-theme` en
`<html>`; «automático» sigue al sistema). El proyector usa el tema claro salvo que el docente elija
oscuro: los proyectores lavan los fondos oscuros.

| Variable | Uso |
|---|---|
| `--ground`, `--surface`, `--surface-alt` | fondo de página, tarjetas, zonas secundarias (paneles) |
| `--ink`, `--ink-soft`, `--muted` | texto principal, secundario, terciario |
| `--rule` | bordes y separadores |
| `--accent`, `--accent-ink`, `--accent-soft` | acción principal (índigo; en oscuro, índigo claro), su texto, su fondo suave |
| `--brand` | solo la palabra «clase» del logotipo (violeta) |
| `--ok`, `--warn`, `--danger` (+ `-soft`) | correcto, pendiente, error o acción destructiva |
| `--opt-0` … `--opt-5` | opciones de respuesta en vivo, distinguibles también con daltonismo |

### Forma

- Fuentes del sistema (`--font`) y monoespaciada para código, PIN y cédulas (`--mono`). Sin fuentes
  externas.
- Radio `--radius` (8 px), sombra `--shadow`, tarjetas `.card`.
- Sin dependencias de CDN: KaTeX y Monaco se sirven desde la propia plataforma.

### Íconos

- Íconos tradicionales en SVG con `{% icon "nombre" tamaño %}` (`apps/core/templatetags/policlase.py`):
  `gear`, `edit`, `trash`, `play`, `close`, `check`, `grip`, `folder_add`, `file_add`, `results`,
  `fullscreen`, `github`, `calendar`, `image`, `task` (actividad), `record` (registro manual), `eye` / `eye_off` (mostrar u ocultar). Para uno nuevo, agregar su trazo a `ICONS`.
- **Nada de emojis ni de caracteres poco comunes como ícono** (⛶, 🔒, 🙂…): no todos los sistemas los
  tienen y aparecen como cajas vacías. Si hace falta un dibujo, SVG (`_face.html`, `_lock.html`).
- Todo botón de solo ícono lleva `title` (tooltip) y `aria-label`.

### Componentes

| Clase | Para qué |
|---|---|
| `button`, `.btn` | acción principal; `.secondary` acción secundaria; `.danger` destructiva; `.big`, `.small` tamaños |
| `.icon-btn` | acción con ícono (editar, iniciar, eliminar); `.primary`, `.danger` |
| `.ghost` | acción discreta (crear sección, presentación, actividad) |
| `.check-toggle` | **ajuste binario** (bono por rapidez, abierta a invitados) |
| `.chip` (`.ok`, `.warn`, `.danger`, `.live`) | estado o conteo breve |
| `.status-pill` (`.ok`, `.bad`) | estado vivo de un proceso (validación del editor) |
| `.tip[data-tip]` | tooltip propio, varias líneas, aparece al instante |
| `.notice` | aviso importante que requiere acción; **no** se esconde en un tooltip |
| `.messages` | confirmación tras una acción |
| `details.unit` | sección minimizable; solo la unidad (o el parcial, en Actividades) en curso viene desplegada |
| `fieldset.segmented` | elegir entre dos o tres opciones excluyentes (tipo de documento en el editor) |

Reglas de interacción:

- **Ajuste binario → casilla. Acción → botón.** Un botón que alterna «sí/no» no deja claro el estado.
- Acciones destructivas: confirmación (`confirm()` o `data-confirm`) que dice qué se pierde y qué no.
- Renombrar en el lugar con doble clic; Enter guarda, Esc cancela.
- Arrastrar y soltar siempre con una alternativa de teclado o botones (↑ ↓ en el panel del editor).
- Estados vacíos con una indicación de qué hacer («Sin presentaciones todavía: arrastre una aquí…»).
- Tablas: números de conteo centrados bajo su encabezado; la columna de acciones a la derecha, sin
  `display: flex` en la celda (rompe la tabla: el flex va en un `div` interno).

## 3. Textos

- **Idioma fuente: español**, con traducción al inglés en `locale/en`. Todo texto visible pasa por
  `{% translate %}`, `{% blocktranslate %}` o `gettext()` (en Python y en JavaScript).
- **Trato:** *usted* en la plataforma («Ingrese el código que le dio su docente»); *tú* solo en las
  pantallas del juego en el teléfono («Ya estás dentro», «Tu puntaje»).
- **Registro sobrio y formal.** Nada coloquial ni efectista («Así se verá» → «Vista previa»; «aún
  nada» → «Sin calificaciones»; «se pierde la materia» → «se reprueba la asignatura»). Frases
  completas e impersonales en lo que tiene peso institucional (notas, aprobación). Botones con verbo
  en infinitivo o sustantivo breve: «Guardar», «Iniciar clase», «Registrar notas».
- **Ni telegráfico ni ingenioso.** Las frases cortadas o con aire de eslogan suenan a texto generado
  y restan seriedad. Se escribe como en un documento institucional:

  | Evitar | Preferir |
  |---|---|
  | Nada se guarda hasta «Guardar». | Revise las notas y pulse «Guardar» para registrarlas. |
  | Sirve lo que se copie de una hoja de cálculo. | Puede pegar el contenido copiado de una hoja de cálculo. |
  | Se guardan solas / se mueven solos | Se guardan automáticamente / se actualizan automáticamente |
  | Clic para cambiarla. | Haga clic para cambiarla. |
  | Lo atrasado se acepta… | Las entregas atrasadas se aceptan… |
  | vacío = raíz del repositorio | si se deja vacío, se usa la raíz del repositorio |
  | aún no activa: la califica usted | función aún no disponible; la califica usted |
  | Cómo crear el token (2 minutos) · Nada más. | Cómo crear el token · No se requieren otros permisos. |

  Señales de alerta: dos puntos que encadenan ideas sueltas, sujetos implícitos («Vence en otro
  parcial: cuenta en…»), verbos coloquiales («sirve», «trae», «llega»), personificaciones («el cierre
  decide»), atar/soltar por vincular/desvincular, y elementos que parecen botones sin serlo (un
  `.chip` que pide «confirmar» debe ser una casilla o un botón de verdad).
- Los mensajes de error dicen **qué pasó y qué hacer**: «La presentación cambió en GitHub después de
  que la abrió; no se guardó para no perder esos cambios. Copie su texto, recargue el editor…».
- Comillas latinas «…» para nombres y títulos en mensajes.

## 4. Patrones de código

### Organización

| App | Contenido |
|---|---|
| `accounts` | usuarios (estudiante, docente, invitado), registro, preferencias (idioma, zona horaria, tema) |
| `courses` | cursos, inscripción con código y aprobación, **reglas de acceso** (`access.py`) |
| `live` | secciones, presentaciones, el **editor único** de presentaciones y actividades (`studio.py`), clases en vivo (`engine.py`), resultados, nota manual de una clase, invitados, retroalimentación |
| `github` | cuentas (token cifrado), vínculo curso-carpeta, sincronización |
| `grades` | parciales, tabla de ponderaciones, reglas de aprobación, fechas clave, registros manuales (`matching.py` empareja listas pegadas), cálculo de notas (`gradebook.py`) |
| `activities` | actividades, intentos (`engine.py`), respuestas calificadas, imágenes entregadas (`images.py`) |
| `core` | markdown y matemáticas, íconos, «Acerca de» |

- Las vistas son funciones; las parciales de plantilla empiezan con `_` (`_slide_card.html`).
- Los comentarios explican **por qué**, en español, como el resto del código.

### Estado de una clase en vivo

- `engine.py` es la única puerta para cambiar una sesión: `apply(session_id, action, index)` con
  `select_for_update`, y actualizaciones condicionadas al estado esperado (cierre por tiempo, cierre
  cuando respondieron todos). Dos clics simultáneos no dejan un estado imposible.
- `state_version` sube con lo que deben ver los teléfonos; `answers_version`, con lo que solo le
  interesa al proyector.
- El flujo SSE envía solo versiones; cada página pide su fragmento HTML. Todos los flujos de una clase
  comparten un sondeo por proceso y devuelven la conexión al pool tras cada consulta.

### Actividades y notas

- `activities/engine.py` es la única puerta para responder: cuándo se puede empezar o enviar, la
  calificación y la nota del docente. Cuenta el **último intento enviado**; la corrección detallada
  se muestra al vencer la fecha (antes, solo el puntaje); lo atrasado se acepta, si se acepta, hasta
  el cierre del parcial y solo de quien no entregó a tiempo.
- Cada intento copia la actividad compilada (`Attempt.content`): editarla después no cambia lo que
  ya se respondió. La nota del docente no borra la automática (`auto_points`).
- Las fechas y la ponderación viven en la plataforma, nunca en el YAML. Un vencimiento relativo a
  una fecha clave se recalcula al moverla (`gradebook.recompute_due_dates`).
- **Unidad y parcial son ejes distintos.** La unidad (sección) organiza el contenido: sus
  presentaciones y sus actividades van juntas en la pestaña Clases, y la pestaña Actividades es una
  vista de todas, por parcial y filtrable por fila de la ponderación. El parcial lo decide la fecha:
  si una actividad vence en otro parcial que su fila, pasa a la fila del mismo nombre de ese parcial
  (`gradebook.rebind`); si no la hay, se avisa.
- **Preguntas con tiempo:** el reloj es del servidor (`Attempt.timers`, `engine.timer`). La pregunta
  no se ve hasta que el estudiante la abre, se guarda solo mientras corre (más una gracia corta) y se
  cierra para el resto del intento; el navegador solo muestra la cuenta regresiva y envía al llegar a
  cero. **Encuestas** (`grading.mode: survey`): se registran, no tienen correcta ni puntos, y en
  ninguna pantalla se muestran como correctas o incorrectas.
- **Notas fuera de policlase:** registros manuales (`ManualRecord`) que cuentan como una actividad
  (o una clase) más de su fila. Pegar una lista solo rellena: nada se guarda sin que el docente lo
  revise, y lo que no coincide queda «sin asignar» para cuando el estudiante se inscriba. Cada nota
  recuerda el nombre de la lista del que salió (`ManualScore.pasted_name`) para poder quitar la
  asignación después; la que coincide solo por el apellido se guarda únicamente si el docente la
  confirma. Un registro puede ir en una unidad (pestaña Clases), junto a la presentación de la clase
  en que se hizo, que muestra las fechas en que se dictó; en un curso con GitHub, una unidad con
  registros no se borra aunque desaparezca su carpeta.
- **Invitados:** nunca cuentan para una nota. Si uno era un estudiante que aún no estaba inscrito,
  se le asigna su cuenta (`engine.link_guest`, con la clase terminada). Para todo lo demás, la nota
  manual de la clase (`SessionGrade`), que reemplaza la suma sin tocar las respuestas.
- **Imágenes:** solo JPEG, PNG y WebP, verificadas por contenido y **re-codificadas** a JPEG (sin
  EXIF ni GPS); se sirven solo por `upload_file`, con permisos y `nosniff`, nunca desde `/media/`.
- **Señales, no sanciones:** pegar texto y salir de la página se registran y se muestran al
  docente, y el estudiante lo sabe. No se bloquean capturas (en la web no se puede) ni se castiga
  automáticamente. La marca de agua (nombre y cédula) va encima del intento, tenue.

### Datos

- Nunca se guarda lo que no hace falta (anonimato) y nunca se pierde lo que se corrigió (`auto_*`).
- Migraciones con relleno de datos en la misma migración; un campo único nuevo, en tres pasos
  (nulo → rellenar → único). Si cambia el formato compilado, recompilar las presentaciones desde su
  fuente (`0004_recompile_decks.py`).

### Formato (`policlase-gen`)

- El formato, su validación y su compilación viven en `policlase-gen`; la plataforma lo importa, no
  lo reimplementa.
- Toda clave nueva: validación con código de diagnóstico, catálogo en `schema.md`, prueba.
- Las ediciones automáticas del YAML (panel del editor) tocan solo las líneas necesarias
  (`deck_text.py`), para que el diff en git sea legible.
- LaTeX entre **comillas simples**; las plantillas del editor ya lo hacen.

### Interfaz

- JavaScript sin dependencias ni paso de compilación: un archivo por página (`static/js/`), en
  `(function () { … })()`, con atributos `data-*` como contrato con la plantilla.
- **Un solo formato y un solo editor** (`studio.html` + `studio.js`): clases, actividades y
  evaluaciones se escriben en `policlase/v1` (schema.md §9), con `use` y **segmentos** que cambian el
  comportamiento de lo que les sigue (tiempo por pregunta, pegar). El tipo lo dice el texto (`use:`;
  el `schema` en los formatos anteriores, que siguen valiendo); lo que es de un solo tipo lleva
  `data-kinds="deck"` o `"activity"`. Las posiciones del panel son las del archivo (`at`), con los
  segmentos contados. Guardar es la única acción que confirma en GitHub, y su botón lo dice.
- Mejora progresiva: sin JavaScript, los formularios siguen funcionando (`<details>`, formularios
  normales); `fetch` con `X-CSRFToken`.
- Monaco se carga **después** de KaTeX (su cargador AMD haría que KaTeX no se registre).

### Pruebas

- Cada funcionalidad trae pruebas, y además:
  - una **prueba de aislamiento** (otro docente recibe 404 y nada cambia),
  - cuando aplica, una prueba de **no revelar** (proyector) y de **anonimato** (retroalimentación).
- GitHub se prueba con `FakeGitHub` (repositorio en memoria), nunca contra la red.
- Correr: `docker compose -f compose.yaml -f compose.dev.yaml run --rm web python manage.py test apps`
  y `python -m pytest -q` en `policlase-gen`. Tras cambiar `policlase-gen`, reconstruir la imagen
  (`docker compose build web`): las pruebas de la plataforma usan la versión instalada en ella.

### Traducciones

1. `makemessages -l en` (y `-d djangojs`) en el contenedor de desarrollo.
2. Traducir lo nuevo en `locale/en/LC_MESSAGES/*.po`; no dejar entradas `fuzzy`.
3. La imagen compila los `.mo` al construirse.

Un `gettext()` de JavaScript no debe compartir línea con un texto que contenga `${…}`: el extractor
lo pierde.

## 5. Publicar y operar

- **Nadie conectado antes de desplegar**: ver «Actualizar con clases en curso» en
  [`deploy.md`](deploy.md). Hay clases reales con estudiantes.
- **Respaldo antes de desplegar** (`ops/respaldar.sh`): un despliegue corre migraciones.
- **Nunca borrar la base** de producción. `tests/e2e/reiniciar-demo.sh` se niega si hay dominio.
- Pruebas de carga (`tests/carga/`) solo en el curso de demostración, fuera de horario, y borrando
  después los invitados «Carga NN» y la sesión.
- **Los commits los hace el autor del proyecto** tras revisar; quien colabora deja el árbol listo y
  propone el mensaje: título en español que describe el cambio (gitmoji opcional) y una lista breve
  de lo que cambió.
- Versión en el archivo `VERSION` (se ve en el pie de página y en «Acerca de»). **Todo conjunto de
  cambios en el código sube la versión en el mismo cambio**: el parche (0.5.0 → 0.5.1) para
  correcciones y ajustes, la menor (0.5 → 0.6) para una funcionalidad nueva. Una subida por conjunto
  de cambios, no por cada edición. Un cambio en `policlase-gen` sube además su `version` en
  `pyproject.toml`. La plataforma lee `VERSION` al arrancar: la versión nueva se ve tras desplegar.
