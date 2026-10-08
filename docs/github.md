# Sincronización con GitHub

Referencia de cómo policlase lee y escribe las presentaciones de un curso en un repositorio de
GitHub. El formato de cada archivo (`policlase.deck/v1`) está en
[`policlase-gen/docs/schema.md`](../../policlase-gen/docs/schema.md) §9; aquí se documenta lo que
hace la plataforma con esos archivos. Implementación: `apps/github/` (`sync.py`, `client.py`,
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
4. Al vincular, las presentaciones que ya existían en la web **se suben** a la carpeta (cada una en
   la de su sección) y luego se trae todo lo que hay en GitHub.

Desvincular deja las presentaciones en policlase y deja de sincronizarlas.

## 2. Qué se lee

Dentro de la carpeta vinculada:

| En GitHub | En policlase |
|---|---|
| Un `.yaml`/`.yml` con `schema: policlase.deck/v1` y sin errores | Una presentación |
| Un `.yaml` con errores | Un aviso en el curso (archivo, línea, código); **se conserva la última versión válida** |
| Otro YAML (ítems, configuración) | Se ignora |
| Un archivo borrado | La presentación se elimina (las clases ya dictadas conservan su copia y sus resultados) |

Para no descargar todo en cada sincronización, policlase compara el **sha de cada archivo** con el
que conoce: solo lee lo que cambió. Los archivos ignorados o con errores tampoco se vuelven a leer
hasta que cambien.

## 3. Secciones = carpetas

- Cada **subcarpeta de primer nivel** de la carpeta vinculada que contenga **al menos una
  presentación** es una sección (unidad). Las subcarpetas más profundas pertenecen a la sección de
  primer nivel que las contiene.
- Una carpeta **sin presentaciones no es una sección** (así `generators/` o una carpeta de ítems no
  aparecen como unidades), **salvo** que tenga el archivo vacío **`.policlase-seccion`**: es el
  marcador que policlase crea para una sección nueva todavía vacía (git no guarda carpetas vacías).
- Las presentaciones en la raíz de la carpeta vinculada quedan en **«Sin sección»**.
- **Nombre:** el de la carpeta. Si no tiene espacios, los `-` y `_` se vuelven espacios y la
  primera letra va en mayúscula (`unidad-01` → «Unidad 01»); con espacios se respeta tal cual
  (`Unidad 3 - Interpolación`).
- **Orden:** por nombre, con orden natural (`unidad-2` antes que `unidad-10`). Lo mismo para las
  presentaciones dentro de una sección: use prefijos numéricos (`01-biseccion.yaml`) para fijarlo.
- Minimizar una sección es una preferencia de la vista del docente: no va a GitHub.

Estructura recomendada (vincule el curso a `metodos-numericos/clases`):

```
repositorio-de-cursos/                  ← privado: los ítems llevan las respuestas
└── metodos-numericos/
    ├── clases/                         ← carpeta vinculada al curso
    │   ├── unidad-01/
    │   │   ├── 01-biseccion.yaml
    │   │   └── 02-newton.yaml
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

## 4. Cuándo se sincroniza

- **Al abrir** la pestaña Clases del curso o el editor, si pasó más de un minuto desde la última vez.
- **«Sincronizar ahora»** en la configuración del curso.
- **Al instante con el webhook** (opcional): en GitHub, *Settings → Webhooks → Add webhook*, con la
  URL y el secreto que muestra la configuración del curso, *Content type* `application/json` y solo
  el evento *push*. policlase verifica la firma (`X-Hub-Signature-256`) y solo reacciona a pushes a
  la rama vinculada.

El ícono de GitHub junto a la rueda del curso muestra el repositorio, la carpeta y la última
sincronización; se pone rojo si la última falló (token vencido, sin acceso…).

## 5. Qué escribe policlase

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

- La carpeta de una sección creada en la web lleva el nombre tal como se escribió (git admite
  tildes y espacios; las `/` se reemplazan).
- Mover y renombrar son **un solo commit** aunque toquen varios archivos (API de datos de git).
  Renombrar reutiliza los archivos de la carpeta por su sha: las imágenes u otros binarios que haya
  ahí no se alteran.
- **No generan commits:** escribir en el editor, reordenar u ocultar diapositivas desde el panel
  (cambian el texto; se confirman al «Guardar»), ocultar diapositivas durante una clase (solo valen
  para esa clase), y todo lo que vive en la base de datos: clases dictadas, respuestas, notas,
  retroalimentación, inscripciones.

## 6. Conflictos

Cada escritura lleva el sha del archivo tal como policlase lo conoce. Si alguien lo cambió en GitHub
entretanto, GitHub rechaza el commit y **policlase no guarda nada**: el texto del editor sigue en
pantalla para copiarlo, recargar y volver a aplicarlo. En las operaciones de varios archivos, además,
el commit solo entra si la rama no avanzó mientras tanto.

## 7. Seguridad

- El repositorio de cursos debe ser **privado**: los ítems contienen las respuestas.
- El token solo necesita *Contents: read and write* sobre ese repositorio.
- La separación entre docentes no depende de GitHub: cada docente usa su propio token y solo ve y
  vincula sus cursos.
