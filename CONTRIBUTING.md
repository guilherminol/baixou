# Contribuir com o Baixou

Obrigado por considerar contribuir. Este documento é curto de propósito — o
essencial é respeitar as regras não-negociáveis abaixo.

## Pré-requisitos

- **Python 3.14+ com [uv](https://docs.astral.sh/uv/)** — backend e testes;
- **Docker** (opcional) — para provar de ponta a ponta com `docker compose up`;
- **Node 24** — só para o frontend (`frontend/`); produção nunca roda Node.

## Desenvolvimento

Os comandos são os mesmos do README (seção "Como rodar em desenvolvimento"):

```bash
# Backend
uv sync                       # dependências do lock
uv run pytest                 # testes — SEM rede (respostas gravadas)

# Frontend (em frontend/)
npm ci                        # dependências EXATAS do lock
npm run lint                  # eslint
npm run typecheck             # tsc --noEmit
npm run test                  # Vitest + Testing Library, sem rede
npm run build                 # o dist/ que o FastAPI serve
```

O CI (`.github/workflows/ci.yml`) exige a mesma malha: ruff check/format,
pytest sem rede, lint/typecheck/test/build do frontend, tipos TypeScript
regenerados do `backend/openapi.json` e `npm audit`.

## Regras não-negociáveis

1. **Documentação e interface em português do Brasil** — a v0.1 é só PT-BR
   por decisão; inglês vem em versão futura.
2. **O worker nunca importa a camada web** — nem `fastapi` nem `pydantic`
   fora de `baixou.web` e dos testes: o `ruff` reprova com **TID251** e o CI
   barra. É a fronteira que mantém o worker em 25–40 MB em vez de 50–70 MB.
3. **Migrações SQL numeradas nunca são editadas depois de publicadas** — a
   migração já rodou em bancos reais; correção de esquema é migração nova,
   com número seguinte.
4. **httpx é o único cliente HTTP** — sem `requests`, sem SDK de terceiros;
   Telegram e ntfy são dois POSTs, não duas bibliotecas.
5. **Sem CDN, fonte externa ou serviço de terceiros no navegador** — CSP
   `'self'`, CSS próprio com variáveis de tema.
6. **Testes sem rede** — respostas gravadas em fixtures e `httpx.MockTransport`;
   a suíte inteira roda offline, no seu computador e no CI.

## Escrevendo uma fonte de cotação nova

O contrato é pequeno (arquitetura, seção "Interfaces de fontes e
notificadores"):

- declare o **mercado** que atende, se consulta **em lote** ou **um símbolo
  por chamada**, e o endereço (fixo no código — nunca configurável, evita SSRF);
- receba os símbolos e devolva, **por símbolo**, o preço, a hora da cotação
  (UTC) e, quando existir, a variação do dia;
- **falhas têm tipos próprios** (limite de uso, símbolo não encontrado,
  autenticação, indisponível, resposta inesperada) — o worker decide pela
  mensagem e pela repetição a partir do tipo, nunca pelo texto do erro;
- não repita a chamada em laço ao receber limite de uso — espere a rodada
  seguinte;
- inclua **respostas gravadas** dos testes mostrando o formato esperado,
  para o CI não depender de rede.

Exemplo mínimo de esqueleto:

```python
class FonteNova:
    mercado = "cripto"          # ou "b3"
    em_lote = True              # False = um símbolo por chamada
    # fetch(simbolos) -> cotações por símbolo; falha por símbolo vira erro
    # tipado, falha de nível (rede, limite) é comunicada pelo tipo — o
    # worker decide o que fazer com ela.
```

## Escrevendo um notificador novo

Também é pequeno:

- receba uma mensagem com **título (sempre)**, texto, prioridade, etiqueta e
  endereço para abrir;
- **devolva sucesso ou falha sem lançar exceção** — o resultado tipado é
  quem comunica o que aconteceu;
- não guarde estado de entrega: a regra "o aviso conta como enviado se pelo
  menos um canal aceitar" fica no worker, não no notificador.

A mídia social do título: em canais públicos (ntfy.sh), o nome do tópico
funciona como senha e **não pode aparecer** nem no título nem no corpo.

## Antes de abrir o PR

- todos os testes verdes sem rede, dos dois lados;
- `ruff check` e `ruff format --check` limpos;
- tipos regenerados se você mexeu em rota da API (`npm run generate-types` —
  o CI reprova o PR se o commitado divergir);
- nenhum token, IP, domínio pessoal ou identificador seu no diff — o
  repositório não contém dados pessoais do autor nem deve conter os seus
  (a varredura de segredos roda no CI).
