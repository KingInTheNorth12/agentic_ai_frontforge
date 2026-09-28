# Private single-owner deployment image. Read docs/DEPLOYMENT.md before hosting.
FROM node:22-bookworm-slim AS ui
WORKDIR /build/web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM node:22-bookworm-slim AS node
FROM python:3.13-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && useradd --create-home --uid 1000 studio \
    && mkdir -p /data && chown studio:studio /data
WORKDIR /app
COPY . .
COPY --from=ui /build/studio/static ./studio/static
RUN pip install --no-cache-dir .
ENV FRONTEND_AGENT_DATA_DIR=/data \
    FRONTEND_AGENT_WORKSPACE=/data \
    FRONTEND_AGENT_SANDBOX_BACKEND=mock \
    FRONTEND_AGENT_PREVIEW_BIND=0.0.0.0 \
    FRONTEND_AGENT_PREVIEW_PORT=8788 \
    PYTHONUNBUFFERED=1
USER studio
EXPOSE 8787 8788
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=3)"
CMD ["python", "-m", "studio", "--host", "0.0.0.0", "--port", "8787"]
