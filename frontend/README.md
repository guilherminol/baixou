# frontend — Baixou (interface web)

SPA React + TypeScript construída com Vite e React Router (modo biblioteca,
importado do pacote `react-router`). O backend FastAPI é quem serve o build:
em produção os estáticos de `dist/` saem pelo próprio serviço web (sem Node em
produção); em dev, o proxy do `vite.config.ts` encaminha `/api` e `/healthz`
para `http://127.0.0.1:8000` — o mesmo código de cliente funciona nos dois
modos, sem CORS.

## Comandos

| Comando                | O que faz                                                          |
| ---------------------- | ------------------------------------------------------------------ |
| `npm run dev`          | Servidor de desenvolvimento com proxy para o backend local          |
| `npm run build`        | `tsc -b` + `vite build` — gera `dist/` servido pelo FastAPI         |
| `npm run test`         | Suíte de componentes com Vitest + Testing Library                   |
| `npm run typecheck`    | `tsc --noEmit` sobre o tsconfig estrito (linha de base do CI)       |
| `npm run generate-types` | Regenera `src/api/generated/schema.ts` a partir de `backend/openapi.json` |

## Tipos da API — GERADOS, nunca à mão

`src/api/generated/schema.ts` é GERADO pelo `openapi-typescript` do contrato
congelado `backend/openapi.json` (o mesmo artefato guardado pelo teste de drift
do backend). Nenhum tipo de API é escrito à mão: tipo manual divergente do
gerado é bug. O cheque de desatualização entra no CI no plano 05-05.

## Convenções

- Visual: CSS próprio com variáveis em `src/styles/global.css` — o design
  system nasce do mockup da fase (paleta clara/escura automática, serif nos
  títulos, números monoespaçados com `tabular-nums`, alvos de 44 px).
- Estado de sessão/credenciais: SÓ em memória React — nada de
  localStorage/sessionStorage nem token em URL (proibição de arquitetura).
- Node fixado em `.nvmrc` (a mesma major do estágio de build do
  `docker/Dockerfile`); `package-lock.json` é commitado e o CI usa `npm ci`.
