# Imagem única do Baixou: os serviços web e worker diferem só no command do compose.
# Sem estágio Node — o frontend é placeholder até a Fase 5 (D-06); sem Node em produção.
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
