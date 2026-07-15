# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS python-builder
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv
WORKDIR /build
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
COPY pyproject.toml README.md LICENSE ./
COPY backend/app/ backend/app/
RUN pip install --upgrade pip && pip install .

FROM python:3.12-slim AS runtime
ARG APP_UID=10001
ARG APP_GID=10001
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    PATH=/opt/venv/bin:$PATH

RUN groupadd --gid "$APP_GID" notifyhub \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --create-home --shell /usr/sbin/nologin notifyhub \
    && mkdir -p /app /data /logs /media /run/secrets \
    && chown -R notifyhub:notifyhub /app /data /logs /media

WORKDIR /app
COPY --from=python-builder /opt/venv /opt/venv
COPY --chown=notifyhub:notifyhub backend/ backend/
COPY --chown=notifyhub:notifyhub plugins/ plugins/
COPY --from=frontend-builder --chown=notifyhub:notifyhub /build/frontend/dist frontend/dist
COPY --chown=notifyhub:notifyhub deploy/entrypoint.sh /usr/local/bin/notify-hub-entrypoint
RUN chmod 0555 /usr/local/bin/notify-hub-entrypoint

USER notifyhub
EXPOSE 8000
VOLUME ["/data", "/logs", "/media"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"]
ENTRYPOINT ["notify-hub-entrypoint"]
