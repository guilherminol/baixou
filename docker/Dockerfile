# Imagem única do Baixou: os serviços web e worker diferem só no command do compose.
# O Node existe SÓ no estágio de build do frontend (o build nunca roda no servidor
# e o runtime de produção segue sem Node — decisão de arquitetura da Fase 5).

# Estágio 1 — build do frontend. A major do node casa com frontend/.nvmrc;
# npm ci instala EXATAMENTE o package-lock.json commitado (T-05-05/T-05-SC).
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# Estágio 2 — final (Python, sem Node): recebe o dist compilado do frontend.
FROM python:3.14-slim

# uv na imagem só para instalar dependências do lock (nunca re-resolve — T-01-SC).
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependências versionadas primeiro (cache de camadas). O lock é versionado e
# `--frozen` garante que as versões são exatamente as auditadas (T-01-SC).
COPY pyproject.toml uv.lock ./
COPY backend ./backend
# O dist do build Vite entra no estágio final — é o web (FastAPI) quem o serve,
# com fallback SPA (05-02); ANTES do chown, para o usuário dedicado o possuir.
COPY --from=frontend /app/frontend/dist /app/frontend/dist
RUN uv sync --frozen --no-dev

# Usuário dedicado não-root; /data é o volume compartilhado por web e worker.
RUN useradd --create-home baixou \
    && mkdir -p /data \
    && chown -R baixou:baixou /app /data

USER baixou

ENV DATA_DIR=/data
EXPOSE 8000

# Sem HEALTHCHECK no Dockerfile: o worker não atende HTTP. O healthcheck fica
# no compose, no serviço web, apontando para /healthz.
# ENTRYPOINT não fixado: o compose define o command por serviço
# (python -m baixou.web.main / python -m baixou.worker.main).
