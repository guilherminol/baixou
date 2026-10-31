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
# --no-editable instala a WHEEL construída a partir de backend/ (não uma
# montagem editable do repositório): o runtime da imagem roda exatamente o que
# a wheel contém — inclusive as migrações embutidas em baixou/migrations
# (WR-01 da 01-REVIEW). O COPY backend ./backend acima fica: é a fonte a
# partir da qual o uv constrói a wheel neste build.
RUN uv sync --frozen --no-dev --no-editable

# Usuário dedicado não-root; /data é o volume compartilhado por web e worker.
RUN useradd --create-home baixou \
    && mkdir -p /data \
    && chown -R baixou:baixou /app /data

USER baixou

# Na wheel --no-editable o default de _dist_frontend() calcula parents[3]
# DENTRO de .venv (/app/.venv/lib/python3.14/frontend/dist — inexistente) e
# toda a UI dá 404 — gap do UAT de 31/10. A env de override já existe no
# código (baixou/web/main.py:44-47) e aponta o dist copiado na linha 32 —
# mesma família do fix WR-01: o runtime embalado precisa dos caminhos
# absolutos da imagem.
ENV DATA_DIR=/data \
    FRONTEND_DIST=/app/frontend/dist
EXPOSE 8000

# Sem HEALTHCHECK no Dockerfile: o worker não atende HTTP. O healthcheck fica
# no compose, no serviço web, apontando para /healthz.
# ENTRYPOINT não fixado: o compose define o command por serviço
# (python -m baixou.web.main / python -m baixou.worker.main).
