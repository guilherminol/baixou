# Baixou

## What This Is

Ferramenta self-hosted e de código aberto de alerta de preço "abaixo de X" para ações da B3 e criptomoedas, com avisos no celular por Telegram e ntfy. Instalável por qualquer pessoa com `docker compose up`, sem cron do host, sem Node em produção e sem proxy específico. A v0.1 transforma a versão que já roda no homelab do autor (script Python com cron e página em HTML único) em um projeto que outras pessoas conseguem instalar e manter.

## Core Value

Avisar de forma confiável quando o preço de um ativo cruza para baixo do alvo — um aviso por cruzamento, repetido só depois do rearme, e nunca marcado como enviado se nenhum canal o aceitou.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. Building toward these. Requirements carry the FR-IDs of the PRD (.planning/docs/prd-v0.1.md). -->

- [ ] Alerta "abaixo de" com um aviso por cruzamento, rearme por margem acima do alvo e reavaliação na troca de alvo (FR-03)
- [ ] Cadastrar, editar alvo e remover tickers da B3 e de cripto, com validação de símbolo, alvo > 0, recusa de duplicados e "Desfazer" por 5 s (FR-01)
- [ ] Rodadas a cada 15 min alinhadas ao relógio; B3 só em pregão (seg–sex, 10h–18h, fuso configurável), cripto 24h (FR-02)
- [ ] Fontes brapi (um ticker por chamada) e Binance (lote, espelho em 451, repetição 1-a-1 em 400) atrás de interface comum de módulo (FR-05)
- [ ] Canais Telegram e ntfy, sempre com título; item só fica "alertado" se ao menos um canal aceitar (FR-04)
- [ ] Página de alertas (lista por mercado, preço, distância, estado, editar, remover com desfazer, salvar) e tela de status (fontes, canais, cota, rodadas) (FR-09, FR-10)
- [ ] Estados da cotação (ok/stale/closed/none), variação suspeita, cota mensal da brapi (80%/100%), aviso de falha prolongada e volta ao normal (FR-06, FR-07, FR-08)
- [ ] Login de usuário único: setup com token do log, sessão no servidor, "lembrar de mim", limite de tentativas por usuário e IP, reset de senha por comando no container (FR-11, FR-12, FR-13)
- [ ] `/healthz` medindo sinal de vida do worker, sem dados e sem login (FR-14)
- [ ] Configuração só por variáveis de ambiente e `_FILE`; valor inválido impede a subida (FR-15)
- [ ] Moeda do item derivada do sufixo do símbolo (BRL, USDT, USDC, USD) (FR-16)
- [ ] Comando de importação da lista de tickers/alvos do homelab atual, sem o estado "alertado" (FR-17)
- [ ] Botão de teste dos canais (v0.2, desejável) (FR-18)

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Alerta "acima de", variação percentual e regras compostas — v0.2+; v0.1 congela em "abaixo de"
- Página de configurações completa — v0.2; na v0.1 a configuração vem só de variáveis de ambiente
- Carteira, preço médio, relatórios semanal/mensal e histórico de preços — v0.3
- Vários usuários e login externo (OIDC) — v0.3+; v0.1 é de usuário único
- Idiomas além do português — decidido: só PT-BR na v0.1, inglês depois
- Integração direta com ferramentas de monitoramento — o projeto só expõe `/healthz`; quem hospeda escolhe o monitor
- Calendário de feriados da B3 — fora da v0.1; o worker consulta de qualquer forma e a cotação aparece parada
- Testes E2E (Playwright) — depois da v0.1
- Exposição direta na internet — o projeto é para rede doméstica atrás de proxy HTTPS; compose de exemplo publica só em 127.0.0.1
- URL de fonte/webhook definida pelo usuário — endereços fixos no código, evita SSRF

## Context

- **Documentos-fonte** (autoritativos, preservados neste repositório):
  - [arquitetura-v0.1.md](docs/arquitetura-v0.1.md) — o **como**: componentes, modelo de dados, contrato da API, máquina de estados, segurança, env vars, interfaces de fonte/notificador, frontend, testes/CI, orçamento de memória, plano de migração do homelab.
  - [prd-v0.1.md](docs/prd-v0.1.md) — o **quê e em que ordem**: 18 requisitos funcionais (10 essenciais, 7 importantes, 1 desejável), requisitos não funcionais, 6 marcos M0–M5 com gates, critérios de aceite, riscos.
- **Estado atual**: existe uma versão provisória rodando no homelab do autor (check.py + página HTML único + cron + Caddy). Serve de referência de comportamento e aparência (temas claro/escuro, tabela fina, números monoespaçados) e roda em paralelo até a versão nova provar que se comporta igual (M5).
- **Arquitetura v0.1**: dois serviços da mesma imagem — `web` (FastAPI/uvicorn: React estático, API, login, /healthz; grava watch/users/sessions) e `worker` (laço Python puro sem importar FastAPI: rodadas, máquina de estados, avisos; grava quote/source_state/quota/run) — que só se falam por SQLite em modo WAL com migrações SQL numeradas e transacionais.
- **Dependências externas gratuitas**: brapi (B3, token próprio de cada pessoa, 15.000 chamadas/mês), Binance dados públicos (sem chave), Telegram Bot API, ntfy (público ou próprio). Distribuição via Docker + GHCR, assumindo GitHub Actions.
- **Servidor de referência**: notebook Core i3-2350M, 4 GB RAM, HDD 5400 rpm, Debian 12 — já roda outros serviços; medição antes/depois é critério de aceite.
- **Ferramentas de qualidade**: ruff, pytest-style testes sem rede (respostas gravadas), Vitest + Testing Library, tipos TS gerados do OpenAPI (CI falha se desatualizado), `npm audit`, varredura de segredos no CI.

## Constraints

- **Tech stack**: Python com FastAPI, SQLite, React com TypeScript + Vite + React Router (modo biblioteca) — escolhas fechadas no documento de arquitetura.
- **Performance/Memória**: web ≤ 80 MB e worker ≤ 50 MB em repouso (limites de container 160/96 MB); memória disponível do servidor de referência > 1,5 GiB; swap < 100 MiB. Worker não pode importar a camada web. Build nunca no servidor.
- **Segurança**: instância única em rede doméstica atrás de proxy HTTPS; scrypt (16 MiB), sessão no servidor, cookie HttpOnly/SameSite=Strict/Secure configurável; CSP 'self'; segredos nunca em logs, erros ou respostas da API; cada pessoa usa as próprias chaves.
- **Compatibilidade**: imagem amd64 e arm64 publicada no GHCR; sem Node em produção; sem CDN, fonte externa ou serviço de terceiros no navegador.
- **Idioma**: documentação e interface em português do Brasil na v0.1 (decidido em 27/10/2026).
- **Licença**: MIT (decidido em 27/10/2026); aviso de que o projeto não é recomendação de investimento; termos de uso da brapi/Binance a conferir antes de publicar.
- **Privacidade do repositório**: nenhum domínio, IP, chat ID, tópico ntfy ou token do autor entra no repositório nem no histórico; tokens já expostos devem ser revogados antes de publicar.

## Key Decisions

<!-- Decisions that constrain future work. Add throughout project lifecycle. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Nome do projeto: **Baixou** | Curto, memorável, diz exatamente o que o aviso comunica; verificado em 27/10/2026 — único repositório homônimo no GitHub está abandonado desde 2018, sem estrelas | — Pending |
| Licença **MIT** | Simples e permissiva, recomendação do PRD; máxima adoção | — Pending |
| Documentação **só em PT-BR** na v0.1 | Alcance inicial brasileiro; inglês numa versão futura | — Pending |
| Stack fechada: FastAPI + SQLite + React/TS/Vite | Decisão pré-existente dos documentos de origem | — Pending |
| Dois serviços (web + worker) na mesma imagem, comunicação só via SQLite | Queda ou atualização da página não para os alertas; isolamento de responsabilidades e de memória | — Pending |
| Worker sem importar FastAPI/pydantic | Controle de memória: worker fica em 25–40 MB em vez de 50–70 MB | — Pending |
| Migrações SQL numeradas à mão (recomendação) | Esquema pequeno, poupa dependências; Alembic descartado por enquanto | — Pending |
| Sem histórico de preços na v0.1 | Relatórios são v0.3; se entrar, será tabela nova de fechamentos diários | — Pending |
| Configuração 100% por env na v0.1 | Página de configurações fica para v0.2; env tem prioridade sobre banco | — Pending |

**Decisões ainda em aberto** (carregadas dos documentos): tempo de dedicação do autor (antes de publicar), quem faz o teste de instalação de fora (antes do M5), valores de REARM_PCT/SUSPECT_PCT (padrão 1%/20% configurável), imagem base Python (slim recomendado), ciframento de segredos no banco (v0.2), feriados da B3 (fora da v0.1).

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-10-27 after initialization*
