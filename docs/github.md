# Sincronización con GitHub

Referencia de cómo policlase lee y escribe las presentaciones y actividades de un curso en un
repositorio de GitHub. El formato de cada archivo (`policlase.deck/v1`, `policlase.activity/v1`)
está en [`policlase-gen/docs/schema.md`](../../policlase-gen/docs/schema.md) §9 (formato único `policlase/v1`,
con segmentos) y §10; aquí se documenta lo que hace la plataforma con esos archivos. Implementación: `apps/github/` (`sync.py`, `client.py`,
`views.py`), con pruebas en `apps/github/tests.py`.

**Principio: GitHub es la fuente de verdad.** Lo que se empuja a GitHub aparece en policlase; lo
que se guarda en policlase se confirma primero en GitHub. Si las dos versiones divergen, policlase
no pisa nada.

## 1. Conectar

1. **Token (una vez por docente).** En GitHub: *Settings → Developer settings → Personal access
   tokens → Fine-grained tokens*. *Repository access*: solo el repositorio de los cursos.
   *Permissions → Contents*: **Read and write**. Nada más.
2. En policlase: menú de usuario → **GitHub** → pegar el token. Se verifica contra la API, se guarda
   **cifrado** (clave derivada de `DJANGO_SECRET_KEY`; si esa clave cambia, hay que volver a pegarlo)
   y solo se usa para los cursos de ese docente. «Desconectar» lo borra.
3. **Vincular un curso:** Configuración del curso → Sincronización con GitHub → repositorio
   (`usuario/repositorio`, también acepta la URL), rama (`main`) y carpeta del curso (relativa a la
   raíz; vacía = raíz). policlase comprueba que el token pueda escribir en esa rama.
4. Al vincular, las presentaciones y actividades que ya existían en la web **se suben** a la carpeta
   (cada una en la de su sección) y luego se trae todo lo que hay en GitHub.

Desvincular deja las presentaciones y actividades en policlase y deja de sincronizarlas.

## 2. Qué se lee

Dentro de la carpeta vinculada:

| En GitHub | En policlase |
|---|---|
| Un `.yaml`/`.yml` con `schema: policlase/v1` y `use: class` (o `schema: policlase.deck/v1`), sin errores | Una presentación (clase en vivo) |
| Un `.yaml` con `schema: policlase/v1` y `use: activity` (o `schema: policlase.activity/v1`), sin errores | Una actividad; la primera vez llega como **borrador** (falta decidir fechas y ponderación en policlase) |
| Un `.yaml` con `use: evaluation` | Un aviso: las evaluaciones llegan con su pestaña |
| Un archivo que cambió de `use` (de clase a actividad o al revés) | Un aviso; lo que ya existe no se convierte (tiene clases o entregas): cree un archivo nuevo |
| Un `.yaml` con errores | Un aviso en el curso (archivo, línea, código); **se conserva la última versión válida** |
| Otro YAML (ítems, configuración) | Se ignora |
| Un archivo borrado | La presentación se elimina (las clases ya dictadas conservan su copia y sus resultados). Una actividad **sin entregas** se elimina; **con entregas** se conserva en policlase, ya sin vínculo, y el curso lo avisa |

Para no descargar todo en cada sincronización, policlase compara el **sha de cada archivo** con el
que conoce: solo lee lo que cambió. Los archivos ignorados o con errores tampoco se vuelven a leer
hasta que cambien.

## 3. Secciones = carpetas

- Cada **subcarpeta de primer nivel** de la carpeta vinculada que contenga **al menos una
  presentación o una actividad** es una sección (unidad), y lo que está en ella le pertenece: sus
  presentaciones y sus deberes aparecen juntos en la pestaña Clases. Las subcarpetas más profundas
  pertenecen a la sección de primer nivel que las contiene.
- Una carpeta **sin presentaciones ni actividades no es una sección** (así `generators/` o una
  carpeta de ítems no aparecen como unidades), **salvo** que tenga el archivo vacío
  **`.policlase-seccion`**: es el marcador que policlase crea para una sección nueva todavía vacía
  (git no guarda carpetas vacías).
- Lo que está en la raíz de la carpeta vinculada queda en **«Sin sección»**.
- **Nombre:** el de la carpeta. Si no tiene espacios, los `-` y `_` se vuelven espacios y la
  primera letra va en mayúscula (`unidad-01` → «Unidad 01»); con espacios se respeta tal cual
  (`Unidad 3 - Interpolación`).
- **Orden:** por nombre, con orden natural (`unidad-2` antes que `unidad-10`). Lo mismo para las
  presentaciones dentro de una sección: use prefijos numéricos (`01-biseccion.yaml`) para fijarlo.
- La sección y el parcial son cosas distintas: una unidad puede repartirse entre dos parciales. El
  parcial de una actividad lo decide su fecha de entrega, que vive en policlase, no en GitHub.
- Los **registros manuales** (notas de algo hecho fuera de policlase) viven solo en policlase. Si la
  carpeta de su unidad desaparece de GitHub, quedan sin unidad.

Estructura recomendada (vincule el curso a `metodos-numericos/clases`):

```
repositorio-de-cursos/                  ← privado: los ítems llevan las respuestas
└── metodos-numericos/
    ├── clases/                         ← carpeta vinculada al curso
    │   ├── unidad-01/
    │   │   ├── 01-biseccion.yaml        ← presentación (schema: policlase.deck/v1)
    │   │   ├── 02-newton.yaml
    │   │   └── deber-01.yaml            ← actividad de la unidad (schema: policlase.activity/v1)
    │   ├── unidad-02/
    │   │   └── 01-interpolacion.yaml
    │   └── repaso-general.yaml         ← «Sin sección»
    ├── items/                          ← evaluaciones (próximamente)
    └── generators/
```

**Carpetas, no ramas.** Cada curso lee una sola rama. Organice cursos y unidades por carpetas en
`main`; use ramas solo para borradores (por ejemplo, vincule un curso de prueba a la rama
`borrador` y fusione a `main` con un pull request). Si la materia se repite otro semestre, cree un
curso nuevo vinculado a la misma carpeta: las presentaciones se reutilizan y los resultados de cada
semestre quedan separados en policlase.

## 4. Imágenes

Las presentaciones y actividades pueden mostrar imágenes guardadas en la carpeta del curso, con la
sintaxis de markdown de siempre:

```yaml
- markdown: |
    ## Error de redondeo
    ![Gráfica del error relativo](img/error-relativo.png)
```

- La ruta es **relativa al archivo YAML**, como en GitHub: `img/error.png` junto a
  `unidad-01/aritmetica.yaml` es `unidad-01/img/error.png`. Si empieza con `/`, es relativa a la
  carpeta del curso (`/comun/logo.png`), útil para imágenes que comparten varias unidades.
- Formatos: PNG, JPEG, GIF, WebP y SVG, hasta 5 MB cada una.
- policlase descarga cada imagen al sincronizar y la sirve desde una dirección que depende de su
  contenido: cambiar la imagen en GitHub actualiza las presentaciones que la usan, mientras que las
  clases ya dictadas y los intentos ya enviados conservan la imagen con la que se hicieron.
- Una imagen que no está (ruta mal escrita, archivo sin subir) aparece como aviso en la pestaña Clases
  y en la vista previa del editor.
- Las direcciones externas (`https://…`) funcionan en cualquier curso; las rutas relativas, solo en
  cursos vinculados a GitHub.

## 5. Cuándo se sincroniza

- **Al abrir** la pestaña Clases o Actividades del curso, o un editor, si pasó más de un minuto desde
  la última vez.
- **«Sincronizar ahora»** en la configuración del curso.
- **Al instante con el webhook** (opcional): en GitHub, *Settings → Webhooks → Add webhook*, con la
  URL y el secreto que muestra la configuración del curso, *Content type* `application/json` y solo
  el evento *push*. policlase verifica la firma (`X-Hub-Signature-256`) y solo reacciona a pushes a
  la rama vinculada.

El ícono de GitHub junto a la rueda del curso muestra el repositorio, la carpeta y la última
sincronización; se pone rojo si la última falló (token vencido, sin acceso…).

## 6. Qué escribe policlase

Solo en cursos vinculados, **un commit por acción**, con el token del docente (los commits quedan a
su nombre):

| Acción en policlase | Commit | Mensaje |
|---|---|---|
| «Guardar» una presentación nueva | crea `<sección>/<título>.yaml`, con el título sin tildes ni espacios («Método de Newton» → `metodo-de-newton.yaml`) | Crea «…» desde policlase |
| «Guardar» una existente | actualiza su archivo | Actualiza «…» desde policlase |
| Arrastrarla a otra sección (o cambiar «Sección» en el editor) | la mueve, **conservando el nombre del archivo** | Mueve «…» a … desde policlase |
| Eliminarla | borra su archivo | Elimina «…» desde policlase |
| Crear una sección | crea `<carpeta>/.policlase-seccion` | Crea la sección «…» desde policlase |
| Renombrar una sección (doble clic) | mueve **todos** los archivos de la carpeta | Renombra la sección «…» a «…» desde policlase |
| Eliminar una sección vacía | borra el marcador | Elimina la sección «…» desde policlase |
| «Guardar» una actividad nueva | crea `<sección>/<título>.yaml`, igual que una presentación | Crea «…» desde policlase |
| «Guardar» una actividad existente | actualiza su archivo | Actualiza «…» desde policlase |
| Arrastrar una actividad a otra sección (o cambiar «Sección» en el editor) | la mueve, conservando el nombre del archivo | Mueve «…» a … desde policlase |
| Eliminar una actividad | borra su archivo (y en policlase, sus entregas) | Elimina «…» desde policlase |

- La carpeta de una sección creada en la web lleva el nombre tal como se escribió (git admite
  tildes y espacios; las `/` se reemplazan).
- Mover y renombrar son **un solo commit** aunque toquen varios archivos (API de datos de git).
  Renombrar reutiliza los archivos de la carpeta por su sha: las imágenes u otros binarios que haya
  ahí no se alteran.
- **Solo «Guardar» (o Ctrl+S, como en VS Code) hace commit.** No generan commits: escribir en el
  editor, la vista previa, reordenar u ocultar diapositivas o ítems desde el panel (cambian el
  texto; se confirman al «Guardar»), ocultar diapositivas durante una clase (solo valen
  para esa clase), y todo lo que vive en la base de datos: clases dictadas, respuestas, notas,
  retroalimentación, inscripciones, y de las actividades sus fechas, ponderación, intentos,
  entregas e imágenes.

## 7. Conflictos

Cada escritura lleva el sha del archivo tal como policlase lo conoce. Si alguien lo cambió en GitHub
entretanto, GitHub rechaza el commit y **policlase no guarda nada**: el texto del editor sigue en
pantalla para copiarlo, recargar y volver a aplicarlo. En las operaciones de varios archivos, además,
el commit solo entra si la rama no avanzó mientras tanto.

## 8. Seguridad

- El repositorio de cursos debe ser **privado**: los ítems contienen las respuestas.
- El token solo necesita *Contents: read and write* sobre ese repositorio.
- La separación entre docentes no depende de GitHub: cada docente usa su propio token y solo ve y
  vincula sus cursos.
