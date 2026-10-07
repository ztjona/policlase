# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POLICLASE_STATIC_ROOT=/opt/staticfiles \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gettext \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 policlase

WORKDIR /app

# KaTeX se sirve desde la propia plataforma: los teléfonos de los estudiantes no dependen
# de ningún CDN externo, y las fuentes matemáticas llegan con el resto de los estáticos.
ARG KATEX_VERSION=0.16.22
RUN mkdir -p /opt/vendor/static/vendor \
 && curl -fsSL "https://registry.npmjs.org/katex/-/katex-${KATEX_VERSION}.tgz" \
    | tar -xz -C /opt/vendor/static/vendor \
 && mv /opt/vendor/static/vendor/package/dist /opt/vendor/static/vendor/katex \
 && rm -rf /opt/vendor/static/vendor/package

# Monaco: el editor de VS Code (mismos atajos de teclado) para las presentaciones. Solo el
# núcleo, el resaltado de YAML/Markdown y los mensajes en español; los servicios de
# TypeScript/JSON/CSS/HTML (7 MB) nunca se usan. Sin los comentarios de sourcemaps: los .map
# no vienen en el paquete y whitenoise falla al no encontrarlos.
ARG MONACO_VERSION=0.52.2
RUN mkdir -p /tmp/monaco /opt/vendor/static/vendor/monaco \
 && curl -fsSL "https://registry.npmjs.org/monaco-editor/-/monaco-editor-${MONACO_VERSION}.tgz" \
    | tar -xz -C /tmp/monaco \
 && mv /tmp/monaco/package/min/vs /opt/vendor/static/vendor/monaco/vs \
 && cd /opt/vendor/static/vendor/monaco/vs \
 && rm -rf language \
 && find basic-languages -mindepth 1 -maxdepth 1 -type d ! -name yaml ! -name markdown -exec rm -rf {} + \
 && find . -maxdepth 1 -name 'nls.messages.*.js' ! -name 'nls.messages.es.js' -delete \
 && find . -name '*.js' -exec sed -i 's#//[#@] sourceMappingURL=[^ ]*$##' {} + \
 && find . -name '*.css' -exec sed -i 's#/\*[#@] sourceMappingURL=[^*]*\*/##' {} + \
 && rm -rf /tmp/monaco

COPY requirements.txt .
RUN pip install -r requirements.txt

# policlase-gen llega como contexto de construcción aparte (compose: additional_contexts),
# así la plataforma y la CLI instalan exactamente el mismo esquema y los mismos calificadores.
COPY --from=gen pyproject.toml README.md /opt/policlase-gen/
COPY --from=gen src /opt/policlase-gen/src
RUN pip install /opt/policlase-gen

COPY . .
RUN DJANGO_SECRET_KEY=build-only python manage.py compilemessages --ignore "*/site-packages/*" \
 && DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
 && chmod +x docker/entrypoint.sh \
 && mkdir -p /data/media && chown -R policlase /data

USER policlase
EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["serve"]
