# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS frontend-build
WORKDIR /frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim-bookworm AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/media /app/staticfiles \
    && chown app:app /app/media /app/staticfiles
COPY backend/ ./
COPY demo/ /app/demo/
COPY deployment/backend-entrypoint.sh /usr/local/bin/backend-entrypoint
RUN sed -i 's/\r$//' /usr/local/bin/backend-entrypoint \
    && chmod 755 /usr/local/bin/backend-entrypoint
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["backend-entrypoint"]
CMD ["gunicorn", "config.wsgi:application", "--no-control-socket", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]

FROM nginx:1.28-alpine AS web
COPY deployment/nginx.conf /etc/nginx/conf.d/default.conf
COPY deployment/security-headers.conf /etc/nginx/security-headers.conf
COPY --from=frontend-build /frontend/dist/ /usr/share/nginx/html/
EXPOSE 80
