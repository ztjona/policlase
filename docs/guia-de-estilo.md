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
   revela respuestas correctas (`_slide_card.html` con `reveal=False`). Las notas del docente de cada
   diapositiva no están en el escenario: aparecen solo al pasar el cursor por su botón de los
   controles, o al enfocarlo con el teclado (decisión del autor, 0.22).
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
  `fullscreen`, `github`, `calendar`, `image`, `task` (actividad), `record` (registro manual), `eye` / `eye_off` (mostrar u ocultar), `info` (explicación en un tooltip), `flag` / `flag_outline` (revisar después), `chevron_left` / `chevron_right` (plegar un panel), `light_mode` / `dark_mode` (cambiar el tema, `core/_theme_toggle.html`). Para uno nuevo, agregar su trazo a `ICONS`.
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
| `.tip[data-tip]` | tooltip propio, varias líneas, aparece al instante; `forms.js` lo ubica con posición fija para que no lo corte una tabla con desplazamiento propio, y `.up` lo abre hacia arriba |
| `.notice` | aviso importante que requiere acción; **no** se esconde en un tooltip |
| `.messages` | confirmación tras una acción |
| `details.unit` | sección minimizable; solo la unidad (o el parcial, en Actividades) en curso viene desplegada |
| `.weight-chip.cN` | la fila de la ponderación de una actividad, con un color por nombre (`courses.views.row_colors`) |
| `form[data-dirty]` | «Guardar» y «Cancelar» (`data-dirty-enable`) solo con cambios sin guardar (`static/js/forms.js`) |
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
  calificación y la nota del docente. Cuenta el **último intento enviado**; lo atrasado se acepta, si
  se acepta, hasta el cierre del parcial y solo de quien no entregó a tiempo.
- **Revisión:** la corrección detallada y las diapositivas `solution` se muestran desde que vence la
  fecha hasta el cierre de la revisión (`Activity.review_until`: sin límite, relativo a una fecha
  clave o fijo); fuera de ella, solo el puntaje. Quien no entregó ve las soluciones solo cuando ya no
  puede entregar. Las soluciones nunca aparecen en el intento ni en el proyector antes de la revisión.
- **Envío automático:** al vencer, el borrador empezado a tiempo se envía solo (`finalize_overdue`).
  No hay tareas programadas: se aplica al consultar la actividad, la página del estudiante o la
  planilla, con el vencimiento como hora de envío, así que el resultado no depende de cuándo se mire.
  Mover la fecha hacia adelante lo reabre (`reopen_auto_submitted`), salvo lo calificado a mano.
- **Secciones deshabilitadas** (`Activity.ungraded_items`): para la sección de un taller a la que no
  se llegó. Se decide en la plataforma, no en el YAML, porque los intentos ya empezados conservan su
  copia: así vale también para ellos. El estudiante no las ve (si toda una sección queda
  deshabilitada, tampoco su enunciado: `outline.hidden_blocks`) y no cuentan para la nota ni para el
  máximo. Sus respuestas se conservan: el formulario solo lee las preguntas visibles
  (`engine.visible_questions`), para que el autoguardado no las deje en blanco.
- **Soluciones de la versión vigente:** la revisión usa las diapositivas `solution` de la actividad
  actual, ubicadas tras el ítem que las precede (`outline.for_review`), porque las soluciones suelen
  escribirse después de que los estudiantes empezaron.
- **Presentar una actividad en clase:** el proyector (`activities/present.html`) avisa en qué bloque
  está el docente (`Activity.pointer_*`, con una señal cada 20 s; sin señal en 90 s, se da por
  terminada). El estudiante decide si va a esa diapositiva, vuelve a donde estaba o sigue al docente.
  La posición viaja con el `item_id` o el título del bloque, porque el intento puede ser de una
  versión anterior de la actividad. Muestra cuántos respondieron, nunca qué.
- **Evaluaciones** (`Activity.kind = evaluation`, desde `use: evaluation`): se editan, sincronizan y
  califican como una actividad, con otras reglas. Un intento, sin atrasos, en un horario (de
  `opens_at` a `due_at`, el día de su fecha clave o en una fecha fija) y, si hay `duration_min`, con
  un límite propio desde que se empieza (`Activity.deadline`). Se empieza con el código que el
  docente proyecta (`evaluation_projector`), y diez códigos equivocados obligan a esperar. Pasado el
  límite (con una gracia de 20 s), nada se guarda y el intento se envía solo. El estudiante no ve su
  puntaje, su corrección ni su nota en «Calificaciones» hasta que el docente publica las notas
  (`published_at`); la revisión empieza entonces y puede cerrarse unos días después de la
  publicación (`Activity.Review.PUBLISHED`, que `engine.publish` recalcula). Con «partes en orden
  aleatorio» (`Activity.shuffle_parts`), cada estudiante recibe los segmentos en su propio orden al
  empezar (`outline.shuffle_parts`, con la semilla de la evaluación y el estudiante): se mezclan
  partes enteras, nunca preguntas sueltas, para que ningún enunciado pierda su contexto. Una
  evaluación nunca se proyecta con sus preguntas y lo enviado no se reabre al mover la fecha. Las
  variantes por estudiante (§7 del formato) quedan para una segunda etapa.
- **Pegar:** lo decide solo el documento (`block_paste` en la raíz o en un segmento). La antigua
  casilla de la plataforma bloqueaba también las preguntas que piden pegar código; se eliminó en 0.15.1.
- Cada intento copia la actividad compilada (`Attempt.content`): editarla después no cambia lo que
  ya se respondió. La nota del docente no borra la automática (`auto_points`).
- Las fechas y la ponderación viven en la plataforma, nunca en el YAML. Un vencimiento relativo a
  una fecha clave se recalcula al moverla (`gradebook.recompute_due_dates`).
- **Pestañas del curso:** Clases | Evaluaciones | Actividades | Calificaciones
  (`courses/_course_tabs.html`). Evaluaciones reúne las fechas clave (pruebas y exámenes) y el
  vencimiento de las actividades; Calificaciones es la planilla. Las dos son páginas propias con el
  mismo encabezado (`courses/_course_head.html`).
- **Corrección de una evaluación** (`Activity.allow_correction`, `correction_of`,
  `engine.sync_correction`): una actividad aparte con el contenido de la evaluación, un intento,
  abierta desde la publicación de las notas hasta el cierre de la revisión y en la fila que elija el
  docente. `sync_correction` la mantiene al día al guardar la configuración, al publicar o retirar
  las notas, al editar o sincronizar el contenido y al mover una fecha clave. Mientras el estudiante
  pueda enviarla, la revisión de la evaluación espera (`engine.correction_pending`); antes de
  empezarla puede omitirla y ver la revisión (`engine.skip_correction`, `SkippedCorrection`, sin
  vuelta atrás). Su configuración y su contenido se editan en la evaluación.
- **Fechas y plazos** (`activities/timeline.py`, `_timeline.html`): en la página del estudiante y en
  la configuración, una franja por plazo (entrega, atrasadas, revisión, debate, notas, corrección)
  sobre un eje común y una línea en «hoy», sin JavaScript. Lo que aún no tiene fecha va como nota.
- **Entregas y configuración, separadas.** Cada actividad tiene dos pestañas
  (`activities/_activity_head.html`): Entregas (`activity_detail`: en una evaluación su aplicación,
  con el código y la publicación de las notas; la calificación pendiente y la tabla de entregas,
  ordenable por apellido, nota o avance) y Configuración (`activity_settings`: fechas, ponderación,
  revisión, secciones, contenido y prórrogas). Así no se cambia algo cuando solo se quiere mirar los
  resultados. La revisión de un intento sigue el orden elegido en la tabla.
- **Material y notas, separados.** La pestaña Clases es el material, por unidad: presentaciones
  (con las fechas en que se dictaron, cada una enlazada a sus resultados) y actividades. La pestaña Actividades es todo lo que se
  califica, por parcial y en orden de fecha: clases dictadas, actividades y registros manuales,
  filtrable por fila de la ponderación. El estudiante ve en «Calificaciones» la nota de cada uno.
- **Unidad y parcial son ejes distintos.** La unidad (sección) organiza el contenido; el parcial
  lo decide la fecha: si una actividad vence en otro parcial que su fila, pasa a la fila del mismo
  nombre de ese parcial (`gradebook.rebind`); si no la hay, se avisa. En los formularios, el parcial
  se elige aparte de la fila y viene propuesto por la fecha.
- **Preguntas con tiempo:** el reloj es del servidor (`Attempt.timers`, `engine.timer`). La pregunta
  no se ve hasta que el estudiante la abre. **No hay cuenta regresiva a la vista ni aviso al
  acabarse**, porque la presión del tiempo genera ansiedad y el aviso desanima: el límite se dice
  antes de abrirla y 10 s antes del final el marco cambia. Al acabarse sigue abierta, y lo que se
  responda después vale con la penalización por atraso de la actividad (`Answer.late`). Se cierra
  al pulsar «Responder» (en blanco, pide confirmación) o al enviar el intento. Enter en un campo de
  texto nunca pulsa un botón de la página (el botón por omisión del formulario es «Guardar»). **Encuestas**
  (`grading.mode: survey`): se registran, no tienen correcta ni puntos, y en ninguna pantalla se
  muestran como correctas o incorrectas.
- **En vivo**, lo mismo: ni el proyector ni los teléfonos muestran la cuenta regresiva (el docente la
  ve pequeña en sus controles), y al acabarse no se avisa nada al estudiante. La pregunta sigue
  abierta hasta que el docente la cierra o responden todos; lo que llega después vale con
  `live.engine.LATE_PENALTY` (`Response.late`). Con bono por rapidez, una sola mención al empezar
  invita a responder pronto.
- **Evaluaciones:** el reloj del intento está oculto; se consulta con «Ver tiempo restante» y avisa a
  los 10 y a los 2 minutos del final.
- **Prórrogas:** una prórroga (`Extension`) da a un estudiante su propia fecha en una actividad, o
  su propio horario y, si hace falta, su propia duración en una evaluación (una adecuación). No hay un
  tiempo adicional general por estudiante: cada caso se decide en su actividad. El motor la consulta
  con `engine.window` y `engine.deadline`; nada lee `due_at` directamente para un estudiante.
- **Nombres:** se guardan con mayúscula inicial (`accounts.names.name_case`, en `User.save`): una
  palabra escrita toda en mayúsculas o en minúsculas se corrige; una mezcla deliberada («McDonald»)
  se respeta, y las partículas van en minúscula salvo al comienzo («De la Torre»).
- **Nombres entre compañeros:** el nombre preferido (`Enrollment.preferred_name`, por omisión el
  primer nombre) y el primer apellido (`courses.models.short_name`), en el marcador y en lo que ven
  los compañeros. El docente lo ajusta en la lista de inscritos. La marca de agua conserva el nombre
  completo y la cédula.
- **Explicaciones:** una diapositiva `solution` explica el ítem que la precede
  (`outline.with_explanations`). En la revisión no se muestra de entrada: el estudiante ve primero su
  corrección y la abre con «Ver la explicación» (`_explanation.html`).
- **Debate** (`apps/activities/debate.py`, `debate: true` en una pregunta): la justificación va con la
  respuesta (`Attempt.reasons`), con markdown y LaTeX (`student_md`, vista previa del servidor); el
  foro abre al vencer la fecha y se cierra según `Activity.debate_close` (por omisión, con la
  revisión; se configura junto a las fechas de la actividad). Reglas que no se negocian: primero se
  publica y después se lee;
  no lee quien todavía puede entregar con nota; la respuesta revisada no cambia la nota; la corrección
  y la explicación de esas preguntas esperan al cierre (`debate.in_debate`, `debate.hidden_items`).
  El foro se muestra junto a su pregunta en la revisión (`_debate.html`), funciona sin JavaScript y
  `debate.js` solo hace que el «me gusta» muestre el número al momento. Lo oculto por el docente lo
  ve su autor, con el aviso.
- **Calificación asistida por IA** (`apps/ai`, DeepSeek): la clave de cada docente, cifrada
  (`AiAccount`, con `github.crypto`); una consulta por respuesta abierta (`ai.grading.propose`), con
  el contexto común primero (para la caché de DeepSeek) y la respuesta al final, sin datos que
  identifiquen al estudiante. La propuesta (`AiProposal`) solo llena el formulario de nota: la nota
  la guarda siempre el docente (`answer_grade` marca la propuesta como aplicada).
- **Repasos** (`Activity.Kind.PRACTICE`, `use: practice`): sin nota (nunca entran en
  `gradebook`), sin tiempo y con intentos ilimitados. «Comprobar» (`engine.check`) califica una
  pregunta al momento y la deja cerrada en ese intento (`Attempt.checked`); la explicación del ítem
  aparece cuando se comprobaron todas sus preguntas. Abren unos días antes de una fecha clave
  (`due_offset_days` negativo sobre `opens_at`) y se listan en la pestaña Evaluaciones.
- **Estadísticas** (`activities/stats.py`, página propia enlazada desde la actividad): sobre el
  intento que cuenta de cada estudiante, la nota (promedio, desviación estándar, mediana,
  distribución), el tiempo entre empezar y enviar, cada pregunta (promedio, correctas, en blanco,
  discriminación y respuestas incorrectas frecuentes) y las señales agrupadas, con la correlación
  por rangos y el umbral bajo el cual puede deberse al azar; desde 0.22, el tiempo por pregunta y
  los estudiantes (notas más altas y más bajas, a quienes se les acabó el tiempo, quienes dejaron
  preguntas sin responder y quienes tienen más señales). Junto a cada cifra que se presta a
  sobreinterpretarse, la página dice qué no permite concluir. «Con más señales», no «sospechosos».
- **Notas fuera de policlase:** registros manuales (`ManualRecord`) que cuentan como una actividad
  (o una clase) más de su fila. Pegar una lista solo rellena: nada se guarda sin que el docente lo
  revise, y lo que no coincide queda «sin asignar» para cuando el estudiante se inscriba. Cada nota
  recuerda el nombre de la lista del que salió (`ManualScore.pasted_name`) para poder quitar la
  asignación después; la que coincide solo por el apellido se guarda únicamente si el docente la
  confirma. La unidad de un registro solo lo rotula.
- **Invitados:** nunca cuentan para una nota. Si uno era un estudiante que aún no estaba inscrito,
  se le asigna su cuenta (`engine.link_guest`, con la clase terminada). Para todo lo demás, la nota
  manual de la clase (`SessionGrade`), que reemplaza la suma sin tocar las respuestas.
- **Imágenes:** solo JPEG, PNG y WebP, verificadas por contenido y **re-codificadas** a JPEG (sin
  EXIF ni GPS); se sirven solo por `upload_file`, con permisos y `nosniff`, nunca desde `/media/`.
- **Señales, no sanciones:** pegar texto y salir de la página (cuántas veces y, desde 0.21, cuánto
  tiempo en total y la salida más larga: `away_s`, `away_max_s`) se registran y se muestran al
  docente, y el estudiante lo sabe. Desde 0.22 también el tiempo en cada pregunta (`q_s`: cada
  segundo con la página visible cuenta para la pregunta con el foco o, si ninguna lo tiene, la que
  cruza el centro de la pantalla) y, en la tabla de entregas, el avance (`engine.progress_of`). Una
  salida incluye pasar a otra ventana, por ejemplo a la calculadora: por sí sola no distingue una
  herramienta permitida de una que no lo es. No se bloquean capturas (en la web no se puede) ni se
  castiga automáticamente. La marca de agua (nombre y cédula) es fija sobre la ventana, muy tenue,
  en el intento y en la revisión (`activities/_watermark.html`).
- **El intento:** el índice (partes, títulos del contenido e ítems, con cuántas preguntas tienen
  respuesta) en el borde izquierdo de la pantalla y el estado de cada pregunta en el derecho (un
  recuadro por ítem, un número por pregunta, las de seguimiento con letra: respondida, sin responder
  o para revisar), con el contenido al centro. Los dos paneles se pliegan y el navegador lo recuerda
  (`_side_outline.html`, `_side_questions.html` y `static/js/side-panels.js`). La revisión del
  docente de un intento usa los mismos paneles; a la derecha, cada pregunta correcta, incorrecta o
  por calificar, con la marca de «revisar después» del estudiante. «Revisar después» es una bandera
  discreta en la esquina de cada pregunta, visible al pasar el puntero o al responder (en pantallas
  táctiles, tenue). El autoguardado es la única forma de guardar (con JavaScript, «Guardar borrador»
  se oculta; Ctrl+S guarda al instante). Enviar con preguntas en blanco es posible y la confirmación
  dice cuántas son.
  Las preguntas con tiempo explican su regla una vez, al comienzo del segmento; en cada una, solo el
  botón y un tooltip. Ningún índice ni panel muestra el enunciado de una pregunta con tiempo.

### Datos

- Nunca se guarda lo que no hace falta (anonimato) y nunca se pierde lo que se corrigió (`auto_*`).
- Migraciones con relleno de datos en la misma migración; un campo único nuevo, en tres pasos
  (nulo → rellenar → único). Si cambia el formato compilado, recompilar las presentaciones desde su
  fuente (`0004_recompile_decks.py`).

### Formato (`policlase-gen`)

- El formato, su validación y su compilación viven en `policlase-gen`; la plataforma lo importa, no
  lo reimplementa.
- Toda clave nueva: validación con código de diagnóstico, entrada en el catálogo (`schema/07-validacion.md`, que una prueba compara con `errors.py`) y en la parte del formato que la explica, prueba.
- Las ediciones automáticas del YAML (panel del editor) tocan solo las líneas necesarias
  (`deck_text.py`), para que el diff en git sea legible.
- LaTeX entre **comillas simples**; las plantillas del editor ya lo hacen.

### Interfaz

- JavaScript sin dependencias ni paso de compilación: un archivo por página (`static/js/`), en
  `(function () { … })()`, con atributos `data-*` como contrato con la plantilla.
- **No usar `form.action` ni `form.method` en JavaScript:** si el formulario tiene un control con ese
  nombre (los botones `name="action"`), el navegador devuelve el control. Use
  `form.getAttribute("action")`. Por eso el autoguardado de los intentos no funcionó hasta 0.13.1.
- `[hidden]` oculta siempre (regla global en `app.css`), aunque la clase del elemento le dé otro
  `display`.
- Un panel con desplazamiento propio (`overflow: auto`) lleva `position: relative`: lo que va con
  posición absoluta dentro (el MathML oculto de KaTeX, `.sr-only`) se ubica respecto de la página y
  la alarga si no.
- **Un solo formato y un solo editor** (`studio.html` + `studio.js`): clases, actividades y
  evaluaciones se escriben en `policlase/v1` ([schema, parte 5](../../policlase-gen/docs/schema/05-documentos.md)), con `use` y **segmentos** que cambian el
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
