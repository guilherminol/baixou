# Baixou

Ferramenta self-hosted e de código aberto de alerta de preço "abaixo de X" para
ações da B3 e criptomoedas, com avisos no celular por Telegram e ntfy.
Instalável com um único `docker compose up`, sem cron do host, sem Node em
produção e sem proxy específico.

> **Aviso:** o Baixou não é recomendação de investimento. Os dados de mercado
> têm atraso e podem errar.

## Instalar (produção)

Sem clonar o repositório — são só dois comandos (Docker instalado):

```bash
curl -O https://raw.githubusercontent.com/guilherminol/baixou/main/compose.install.yaml
docker compose -f compose.install.yaml up -d
```

Com isso o Baixou **sobe sem configurar nada**: nenhuma variável de ambiente é
obrigatória. A página abre em **http://127.0.0.1:8000** — o cadastro do
administrador é feito pelo próprio navegador no primeiro acesso. Sem canal
configurado, a tela de status mostra a faixa de **"nenhum canal configurado"**:
o sistema está no ar, a lista funciona — só não avisa.

### Para alertar de verdade

O aviso no celular precisa de um **`.env`** criado ao lado do
`compose.install.yaml` com **`BRAPI_TOKEN`** (dados da B3 — token gratuito em
[brapi.dev](https://brapi.dev)) **e um canal** de notificação. O compose lê o
`.env` automaticamente (ele é opcional justamente para a subida sem
configuração). Exemplo com valores-placeholders — descomente e troque pelos
seus:

```bash
# .env — valores de exemplo; nunca use tokens de terceiros
# BRAPI_TOKEN=seu-token-da-brapi
# Canal 1 — Telegram:
# TELEGRAM_TOKEN=123456:ABC-exemplo-de-token
# TELEGRAM_CHAT_ID=123456789
# Canal 2 — ntfy (alternativa ao Telegram):
# NTFY_TOPIC=um-topico-exemplo-so-seu
```

Sem canal o sistema sobe, mas não avisa; com o `.env` criado, recrie os
contêineres com o mesmo `docker compose -f compose.install.yaml up -d` de
cima para a configuração valer. Criptomoedas não precisam de token — os dados
da Binance são públicos.

### Atualizar

```bash
docker compose -f compose.install.yaml pull && docker compose -f compose.install.yaml up -d
```

A lista, o histórico e o banco vivem no volume nomeado `baixou-data` —
atualizar a imagem preserva tudo.

## Como funciona

Dois serviços rodam da mesma imagem e só se falam pelo banco SQLite (modo WAL)
em `/data`:

- **web** (FastAPI/uvicorn): API, login e o `/healthz`;
- **worker** (laço Python puro): rodadas a cada 15 minutos, sinal de vida em
  `run.last_run`.

Os dois aplicam as migrações ao subir; a corrida de subida é resolvida pelo
lock do banco, sem serviço extra.

## Como rodar em desenvolvimento

Requisitos: [Docker](https://docs.docker.com/get-docker/) (e, para desenvolver
sem Docker, [uv](https://docs.astral.sh/uv/)).

```bash
# Opção 1 — Docker (prova de ponta a ponta)
docker compose up --build
# Em outro terminal:
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/healthz

# Opção 2 — ambiente local com uv
uv sync
uv run python -m baixou.worker.main   # em um terminal
uv run python -m baixou.web.main      # em outro
uv run pytest                          # testes (sem rede)
```

Notas da Opção 1 (Docker):

- Os dados vivem no volume nomeado `baixou-data` e persistem entre subidas.
  `docker compose down` mantém o volume; `docker compose down -v` apaga o
  volume e o banco.
- Nenhum passo de preparação de pasta é necessário na máquina: o dono correto
  do `/data` vem da imagem no primeiro uso — é isso que torna a primeira
  subida verde em Linux.
- Já subiu com a montagem de diretório anterior? O volume novo nasce vazio e
  os dados antigos do diretório do host não migram sozinhos — antes da Fase 2
  não existe dado de valor no banco; recomeçar é seguro.
- Os serviços reiniciam sozinhos após um crash (política `unless-stopped`);
  uma parada explícita com `docker stop` continua sendo respeitada.

### Frontend (dev)

A interface vive em `frontend/` (React + TypeScript + Vite). Com o backend no
ar (`uv run python -m baixou.web.main`, porta 8000):

```bash
cd frontend
npm ci        # dependências EXATAS do package-lock.json (mesmo caminho do CI e da imagem)
npm run dev   # dev server com proxy de /api e /healthz para 127.0.0.1:8000 — sem CORS
```

Os demais comandos são a mesma malha exigida no CI (`.github/workflows/ci.yml`,
job `frontend`): `npm run lint` (eslint), `npm run typecheck`
(`tsc --noEmit`), `npm run test` (Vitest + Testing Library, sem rede) e
`npm run build` (o `dist/` que o FastAPI serve em produção). Os tipos
TypeScript da API são **gerados, nunca editados à mão**: `npm run
generate-types` regera `src/api/generated/schema.ts` a partir de
`backend/openapi.json` — o CI regenera e reprova o PR se o commitado divergir.

## Contrato do `/healthz`

`GET /healthz` mede a vida do **worker**, não do mercado:

- **200** com corpo vazio se o worker gravou `run.last_run` há menos de
  `2 × POLL_MINUTES`;
- **503** com corpo vazio caso contrário (worker parado, banco ausente ou
  batimento velho).

Sem login, sem dados no corpo, sem cabeçalhos de versão — serve ao healthcheck
do Docker e a monitores externos (Uptime Kuma, healthchecks.io, etc.). A
degradação de fontes/canais aparece na página, nunca derruba o `/healthz`.

## Redefinição de senha

Perdeu a senha (ou a sessão) do administrador? O comando abaixo roda **dentro
do container** e não precisa de acesso à API:

```bash
docker compose exec web python -m baixou.web.reset_password
# para um usuário diferente do padrão `admin`:
docker compose exec web python -m baixou.web.reset_password --username outro
```

O comando pede a senha nova duas vezes (sem eco no terminal — a senha nunca
passa por argumentos do shell, então não fica no histórico) e, num único
passo:

- troca a senha do usuário; e
- **encerra todas as sessões dele** — nenhum login antigo sobrevive ao reset;
  entre com a senha nova na página de login.

A senha nova precisa ter pelo menos 12 caracteres.

## Contrato da API

O contrato completo da API vive congelado em **`backend/openapi.json`** — é a
superfície `/api` da Fase 4 (setup, login/logout e gestão da lista), da qual a
interface (Fase 5) gera os tipos TypeScript com
[openapi-typescript](https://www.npmjs.com/package/openapi-typescript).

O arquivo é **congelado**: o teste `backend/tests/test_openapi_contract.py`
regenera o schema em memória e compara com o commitado — qualquer diferença
(regra a mais, a menos ou alterada) reprova a suíte. Mudou uma rota de
propósito? Regere o artefato e commite junto:

```bash
uv run python -c "import json,tempfile,pathlib;from baixou.core.config import Settings;from baixou.web.main import create_app;app=create_app(Settings(data_dir=pathlib.Path(tempfile.mkdtemp()),tz_market='America/Sao_Paulo',poll_minutes=15,log_level='info'));pathlib.Path('backend/openapi.json').write_text(json.dumps(app.openapi(),sort_keys=True,indent=2,ensure_ascii=False)+chr(10),encoding='utf-8')"
```

Detalhes do contrato:

- O `/healthz` fica **fora** do contrato (sinal de vida, não API) e a
  documentação automática do FastAPI permanece **desligada** — nenhum
  `/docs`, `/redoc` ou `/openapi.json` é servido; o JSON é artefato de
  build/CI, não rota.

## Variáveis de ambiente

Toda a configuração vem de variáveis de ambiente; **valor inválido impede a
subida, com a mensagem nomeando a variável** (o valor nunca aparece em logs ou
erros).

**Segredos aceitam o sufixo `_FILE`** (convenção dos secrets do Docker): com
`NOME_FILE=/caminho/do/arquivo`, o valor de `NOME` é lido do arquivo (uma
newline final é removida). A **variável de ambiente tem prioridade sobre o
arquivo** — se `NOME` e `NOME_FILE` existem, o valor de `NOME` vence e o
arquivo não é lido. Arquivo ausente ou ilegível também impede a subida,
nomeando a variável `NOME_FILE`. O valor lido de um `_FILE` nunca aparece em
log, mensagem de erro ou exceção. Qualquer nome aceita o sufixo (ex.:
`TELEGRAM_TOKEN_FILE`), inclusive os que chegam em fases futuras.

| Variável     | Padrão                | Função                          |
| ------------ | ------------------- | ------------------------------- |
| `DATA_DIR`   | `/data`             | Pasta do banco SQLite           |
| `TZ_MARKET`  | `America/Sao_Paulo` | Fuso do pregão da B3 (nome de zona IANA, nunca offset) |
| `POLL_MINUTES` | `15`              | Intervalo das rodadas do worker (inteiro > 0) |
| `LOG_LEVEL`  | `info`              | Nível do log (`debug`, `info`, `warning`, `error`) |
| `FRONTEND_DIST` | *(opcional)* `<repo>/frontend/dist` | Diretório do build do frontend servido pelo web. Ausente ou vazia usa o padrão — que, dentro da imagem, é `/app/frontend/dist` |

## Estrutura

```
backend/baixou/       pacote único: core (config/db/migrações/log), web, worker
backend/baixou/migrations/   migrações SQL numeradas, embutidas no pacote, aplicadas uma única vez
backend/tests/        testes do backend (sem rede)
frontend/             interface React + TypeScript + Vite (UI-01/UI-02, Fase 5)
docker/               Dockerfile da imagem única
docs/                 documentação
```

## Licença

MIT. Veja `docs/README.md` para onde a documentação completa vai morar.
