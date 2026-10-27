# Arquitetura — Alertas de preço (v0.1)

> Documento-fonte fornecido pelo autor em 27/10/2026. Preservado integralmente como referência técnica autoritativa para as fases de planejamento e execução.

27 de out. de 2026 · @Guilhermino

## Objetivo e escopo

A v0.1 avisa quando um ativo da B3 ou uma cripto chega ao preço-alvo "abaixo de", e se instala com um `docker compose up`, sem depender do homelab do autor.

### Dentro da v0.1

- Alerta apenas de "abaixo de X", uma vez por cruzamento.
- Fontes: brapi (B3, uma chamada por ticker) e Binance (cripto, em lote).
- Notificadores: Telegram e ntfy.
- Login de usuário único, com "lembrar de mim".
- Página com a lista de tickers, preço, estado e status das consultas, e o endereço `/healthz`.
- Agendador dentro da imagem, num serviço worker separado do web.

### Fora da v0.1

- Alerta de "acima de", variação percentual e regras compostas.
- Carteira, preço médio, relatórios semanal e mensal e histórico de preços.
- Vários usuários e login externo (OIDC).
- Página de configurações completa (v0.2). Na v0.1, a configuração vem de variáveis de ambiente.
- Idiomas além do português e integração direta com ferramentas de monitoramento. O projeto só expõe o `/healthz`.

## Premissas

- Cada pessoa usa as próprias chaves das fontes. O projeto não embute nenhuma.
- Instância única, para uma pessoa, na rede doméstica e atrás de um proxy com HTTPS. Não é feito para ficar exposto direto na internet.
- Os dados de mercado têm atraso e podem errar. O projeto não é recomendação de investimento.

## Componentes

O projeto roda como dois serviços da mesma imagem, web e worker, que só se falam pelo banco SQLite. Uma queda ou uma atualização da página não para os alertas.

Os dois serviços compartilham o volume `/data`. O web nunca consulta fontes de cotação, e o worker nunca atende requisições.

### Responsabilidades

- **web**: entrega os arquivos do React, a API, o login e o `/healthz`. Grava a lista de tickers, as contas e as sessões.
- **worker**: faz as rodadas, consulta as fontes, aplica a máquina de estados e envia os avisos. Grava cotações, o estado do alerta, a cota e o sinal de vida.
- **SQLite**: um arquivo só, em modo WAL, com migrações versionadas. É a única ligação entre os serviços.
- **Fora do projeto**: o proxy com HTTPS e o monitor que consulta o `/healthz`, escolhidos por quem hospeda.

## Modelo de dados e migrações

Um arquivo SQLite em modo WAL guarda tudo. Só o web altera a lista e a conta, e só o worker grava cotações e o estado do alerta.

| Tabela | Guarda | Quem grava |
|--------|--------|------------|
| `watch` | símbolo, mercado (b3 ou crypto), alvo "abaixo de" | web |
| `quote` | último preço, hora da cotação (UTC), variação do dia, alerted, alvo vigente no aviso, resultado da última tentativa por canal | worker |
| `source_state` | por fonte: última consulta, último sucesso, erro, falhas seguidas | worker |
| `quota` | chamadas por fonte e por mês, aviso de 80% já enviado | worker |
| `run` | última rodada, falhas seguidas do conjunto, trava de instância única | worker |
| `users` | usuário, hash da senha (scrypt), criado em | web |
| `sessions` | hash do token, usuário, criada em, expira em, último uso, "lembrar de mim" | web |
| `login_attempt` | contagem de falhas por usuário e por IP, bloqueado até | web |
| `setup` | token do primeiro acesso, enquanto não houver usuário | web |
| `schema_migrations` | versão aplicada de cada migração | ambos |

### Regras

- Datas e horas em UTC (ISO 8601). A conversão para o fuso configurado só acontece na saída.
- A chave de watch e de quote é (símbolo, mercado). Remover da lista apaga a cotação.
- Migrações são arquivos SQL numerados, aplicados na subida dentro de uma transação, e nunca editados depois de publicados. O banco aberto por uma versão mais antiga do que a gravada é recusado.
- Segredos configurados pela página (v0.2) ficam em tabela própria, só de escrita, e a variável de ambiente sempre tem prioridade sobre o banco.
- Sem histórico de preços na v0.1. Se entrar (relatórios), será uma tabela nova de fechamentos diários.

## Contrato da API

Todas as rotas ficam sob `/api`, em JSON, e exigem sessão, exceto `/healthz`, `/api/auth/login` e `/api/setup`. Todo método que altera algo exige o cabeçalho `Content-Type: application/json` e confere o Origin. A documentação automática do FastAPI fica desligada por padrão.

| Método e rota | Função | Observação |
|---------------|--------|------------|
| `GET /healthz` | Saúde do worker | Sem sessão. Devolve só ok ou erro, sem dados |
| `GET /api/setup` | Diz se falta criar o administrador | Sem sessão |
| `POST /api/setup` | Cria o administrador | Exige o token impresso no log. Só funciona sem usuário |
| `POST /api/auth/login` | Abre a sessão | Campo remember. Limite de tentativas |
| `POST /api/auth/logout` | Encerra a sessão | |
| `GET /api/auth/sessions` | Lista os aparelhos logados | v0.2 |
| `GET /api/status` | Última rodada, próxima, cota do mês, estado de cada fonte e de cada canal de aviso | |
| `GET /api/watch` | Lista com cotação, distância, estado e frescor | Mesmo formato dos dados do design atual, com os campos novos abaixo |
| `PUT /api/watch` | Substitui a lista inteira | Validação por item. Resposta com o erro e o ticker que falhou |
| `POST /api/notifiers/{canal}/test` | Envia um aviso de teste | Só envia, não grava |
| `GET /api/config` e `PUT /api/config` | Configuração e segredos | v0.2. Segredo nunca volta, só o estado "configurado" |

### Campos de cada item de `/api/watch`

- `symbol`, `market`, `target_below`, `currency` (derivada do sufixo do símbolo).
- `price`, `price_at`, `change_pct` e `fresh`, que vale `ok`, `stale`, `closed` ou `none`.
- `alerted` e `suspect` (variação do dia acima do limite).
- `notify_state`, com o resultado da última tentativa de aviso por canal.

### Erros

- Corpo `{"error": "<código>", "message": "<texto>"}` com os códigos HTTP 400, 401, 403, 409, 422 e 429.
- 401 leva o front para o login. 429 traz `Retry-After`.
- O front gera os tipos TypeScript a partir do OpenAPI do FastAPI, e o CI falha se o arquivo gerado estiver desatualizado.

## Máquina de estados do alerta

Um item só passa a "alertado" depois que pelo menos um canal aceita o aviso, e só volta a "aguardando" quando o preço sobe uma margem acima do alvo ou o alvo muda.

### Regras

- O worker avalia cada item a cada rodada, e só com cotação fresca (veja a tabela abaixo).
- Preço ≤ alvo com o item aguardando: envia o aviso por todos os canais configurados. Se ao menos um aceitar, o item fica alertado. Se nenhum aceitar, continua aguardando e tenta de novo na rodada seguinte.
- Preço ≤ alvo com o item já alertado: não repete o aviso.
- Preço ≥ alvo × (1 + REARM_PCT ÷ 100) com o item alertado: volta a aguardando. Com o padrão de 1%, um alvo de R$ 15,00 só rearma a partir de R$ 15,15.
- Alvo alterado: o worker compara o alvo atual com o alvo gravado no aviso. Se diferirem, o estado é zerado e a rodada seguinte reavalia. Assim o web não precisa gravar nas cotações.
- Variação suspeita: se a variação do dia passar de SUSPECT_PCT, o aviso pede para conferir evento corporativo (desdobramento, bonificação). O alerta não é bloqueado.
- Quando um canal aceita e outro falha, o item conta como alertado e o resultado por canal fica registrado. Não há reenvio só para o canal que falhou.

### Frescor da cotação (o que a página mostra)

| Estado | Quando | Efeito |
|--------|--------|--------|
| `ok` | Idade da cotação até o limite (B3: 75 min, cripto: 30 min) | Alerta avaliado |
| `stale` | Idade acima do limite durante o horário de consulta | Alerta não avaliado. Página mostra "desatualizado" |
| `closed` | B3 fora do pregão | Mantém a última cotação, sem reavaliar. Página mostra "mercado fechado" |
| `none` | Ainda sem cotação | Página mostra "sem cotação" |

### Estado das fontes, dos canais e da cota

| Situação | Condição | Efeito |
|----------|----------|--------|
| Fonte com erro | A última consulta falhou | Contador de falhas seguidas por fonte. A outra fonte segue normal |
| Falha prolongada | 3 rodadas seguidas com erro (FAIL_NOTIFY_AFTER) | Um aviso, e outro quando normalizar |
| Cota em 80% | Chamadas do mês na brapi chegam a 80% do limite | Um aviso por mês |
| Cota esgotada | 100% do limite | A B3 não é consultada até virar o mês. Página mostra o estado |
| Nenhum canal configurado | Sem Telegram nem ntfy | Avisos desligados. Faixa fixa na página |

## Agendador (worker)

O worker é um laço de um processo só, sem biblioteca de agendamento, que roda uma rodada a cada 15 minutos alinhada ao relógio (:00, :15, :30, :45) e sobrevive a qualquer erro de uma fonte.

### Uma rodada

1. Calcula o próximo horário alinhado e dorme até lá. Sem CPU entre as rodadas.
2. Grava `run.last_run` (sinal de vida), mesmo quando não há nada a consultar.
3. B3: só consulta de segunda a sexta, das 10h às 18h no fuso configurado (padrão Brasília), uma chamada por ticker, em sequência e com uma pausa curta entre elas.
4. Cripto: consulta o dia todo, num lote só por fonte.
5. Atualiza quote, aplica a máquina de estados e envia os avisos devidos.
6. Atualiza source_state e quota, e grava `run.last_ok` se nenhuma fonte falhou.

### Regras

- **Instância única**: uma trava no banco impede dois worker ao mesmo tempo. O segundo sai com mensagem clara.
- **Sem sobreposição**: se uma rodada passar de 15 minutos, a seguinte espera, e a rodada perdida não é recuperada.
- **Ao subir**: roda uma rodada na hora, e só depois entra no ritmo.
- **Cota**: conta as chamadas à brapi por mês. Ao chegar em 80% do limite configurado, avisa uma vez e continua. Ao chegar em 100%, para de consultar a B3 até virar o mês e mostra o estado na página.
- **Falhas**: uma fonte com erro não impede a outra. Três rodadas seguidas com erro geram um aviso, e a volta ao normal gera outro.
- **Feriados da B3**: na v0.1 o worker consulta de qualquer forma e a cotação aparece como parada. Um calendário de feriados configurável fica para depois.
- **Parada**: ao receber SIGTERM, termina a rodada em andamento e sai, sem deixar a gravação pela metade.
- **Relógio**: usa o relógio monotônico para dormir, e o relógio civil só para alinhar o horário.

## Segurança e login

A v0.1 tem um usuário administrador, sessão guardada no servidor e nada exposto sem login. O serviço foi pensado para rodar na rede doméstica, atrás de um proxy com HTTPS.

### Primeiro acesso

- Sem nenhum usuário, o web gera um token de configuração e o imprime no log do container. A tela de criar o administrador exige esse token.
- Alternativa para quem automatiza: senha inicial por variável de ambiente ou arquivo (`*_FILE`).
- Esquecer a senha se resolve por um comando dentro do container (`reset-password`), não por e-mail.

### Senha e sessão

- Hash com scrypt (biblioteca padrão), sal por usuário, parâmetros gravados junto, comparação em tempo constante. Senha com no mínimo 12 caracteres.
- A sessão é um identificador aleatório e longo. O banco guarda só o hash dele, com validade. Sair ou trocar a senha encerra as sessões.
- Sem "lembrar de mim": cookie de sessão, com expiração por inatividade de algumas horas. Com ele: cookie persistente de cerca de 30 dias, renovado pelo uso. A validade vale no servidor também.
- O cookie é HttpOnly e SameSite=Strict. O atributo Secure é uma opção com três valores (auto, sim, não), porque em `http://IP:porta` um cookie Secure é descartado e o login falha sem explicação. O README explica isso.

### Força bruta

- Limite de tentativas por usuário e por IP, com espera crescente, e a mesma resposta quando o usuário não existe.
- O scrypt de 16 MiB por tentativa limita as tentativas simultâneas a uma ou duas, para não esgotar a memória do servidor.
- O IP vem de X-Forwarded-For só quando o proxy está na lista de proxies confiáveis. Sem a lista, vale o endereço da conexão.

### Requisições forjadas e cabeçalhos

- Proteção por SameSite=Strict, `Content-Type: application/json` obrigatório e conferência do Origin.
- Content-Security-Policy restrita a 'self' (o build do Vite dispensa script e estilo embutidos), X-Content-Type-Options, Referrer-Policy: no-referrer e frame-ancestors 'none'.

### Segredos e dados

- Variáveis de ambiente e arquivos de segredo têm prioridade sobre o banco, e a página os mostra como somente leitura.
- Segredos entrados pela página (v0.2) são só de escrita, e a API devolve apenas "configurado".
- Nenhum token ou senha vai para log, para mensagem de erro ou para a resposta da API. Erros de rede são registrados só com o tipo e o código.
- Trocar token ou senha pede a senha de novo.

### Fora do alcance da v0.1

- URL de fonte ou de webhook definida pelo usuário. Os endereços das fontes e dos notificadores são fixos no código (com o servidor do ntfy configurável), o que evita requisições a endereços internos (SSRF).
- O projeto não deve ser publicado direto na internet. O exemplo de compose publica a porta só em 127.0.0.1.

## Configuração por variáveis de ambiente

Na v0.1 toda a configuração vem de variáveis de ambiente. Os segredos aceitam também o sufixo `_FILE`, que lê o valor de um arquivo (convenção dos secrets do Docker). Os nomes abaixo são uma proposta e podem mudar antes da v0.1.

| Variável | Padrão | Função |
|----------|--------|--------|
| `DATA_DIR` | `/data` | Pasta do banco SQLite |
| `TZ_MARKET` | `America/Sao_Paulo` | Fuso do pregão da B3 |
| `POLL_MINUTES` | `15` | Intervalo das rodadas |
| `B3_OPEN` e `B3_CLOSE` | `10:00` e `18:00` | Horário da B3 |
| `REARM_PCT` | `1` | Distância acima do alvo para rearmar |
| `SUSPECT_PCT` | `20` | Variação do dia que gera o aviso de conferir evento corporativo |
| `STALE_MINUTES_B3` e `STALE_MINUTES_CRYPTO` | `75` e `30` | Idade a partir da qual a cotação é "desatualizada" |
| `FAIL_NOTIFY_AFTER` | `3` | Rodadas com erro antes do aviso |
| `BRAPI_TOKEN` | vazio | Token da brapi (e `_FILE`) |
| `BRAPI_MONTHLY_LIMIT` | `15000` | Limite do plano, para a contagem de cota |
| `TELEGRAM_TOKEN` e `TELEGRAM_CHAT_ID` | vazio | Canal Telegram (token também com `_FILE`) |
| `NTFY_URL`, `NTFY_TOPIC` e `NTFY_TOKEN` | `https://ntfy.sh`, vazio, vazio | Canal ntfy (tópico e token também com `_FILE`) |
| `NOTIFY_CLICK_URL` | vazio | Endereço que abre ao tocar na notificação |
| `COOKIE_SECURE` | `auto` | auto, true ou false |
| `TRUSTED_PROXIES` | vazio | Lista de proxies em que se confia para o IP do cliente |
| `BASE_PATH` | `/` | Subcaminho, se a instância ficar atrás de um |
| `LOG_LEVEL` | `info` | Nível de log |

### Regras

- Sem nenhum notificador configurado, o worker sobe, mas a página e o `/api/status` mostram o aviso "nenhum canal de aviso configurado".
- Uma variável inválida impede a subida, com a mensagem dizendo qual.
- Nenhum valor padrão contém dado pessoal do autor (domínio, chat ID, tópico, IP).
- Na v0.2, a página de configurações grava no banco o que não vier de variável de ambiente, e mostra o que vem de variável como somente leitura.

## Interfaces de fontes e notificadores

Fontes e notificadores são módulos com uma interface pequena e comum. Quem quiser acrescentar um Yahoo Finance ou um Discord escreve um módulo novo, sem mexer no resto. É o que torna as contribuições pequenas e isoladas.

### Fonte de cotação

- Declara o mercado que atende, se consulta em lote ou um símbolo por chamada, e o endereço (fixo no código).
- Recebe os símbolos e devolve, por símbolo, o preço, a hora da cotação (UTC) e, quando existir, a variação do dia.
- Falhas têm tipos próprios: limite de uso, símbolo não encontrado, autenticação, indisponível e resposta inesperada. O worker decide pela mensagem e pela repetição a partir do tipo, e nunca pelo texto do erro.
- Não repete a chamada em laço quando recebe limite de uso. Espera a rodada seguinte.
- Respostas gravadas dos testes mostram o formato esperado, para o CI não depender de rede.

### Notificador

- Recebe uma mensagem com título (sempre), texto, prioridade, etiqueta e endereço para abrir.
- Devolve sucesso ou falha, sem lançar exceção.
- O título é obrigatório porque, em canais públicos como o ntfy.sh, o nome do tópico funciona como senha e não pode aparecer como título da notificação.
- A regra "o aviso conta como enviado se pelo menos um canal aceitar" fica no worker, e não nos notificadores.
- Cada canal tem um botão de teste na página, que envia um aviso sem gravar nada.

### Primeiras implementações

| Módulo | Tipo | Detalhes |
|--------|------|----------|
| brapi | Fonte, B3 | Uma chamada por ticker, token, cota mensal contada pelo projeto |
| Binance | Fonte, cripto | Lote por chamada, sem chave. Tenta o endereço principal e cai para o espelho de dados públicos em caso de bloqueio (HTTP 451). Um símbolo inválido derruba o lote, então o módulo repete um a um |
| Telegram | Notificador | Token e chat ID |
| ntfy | Notificador | Publica em JSON (evita problemas de acento em cabeçalhos HTTP), com URL, tópico e token opcional |

### Contribuições previstas

Yahoo Finance, CoinGecko, Discord, webhook genérico e e-mail. O CONTRIBUTING traz um exemplo mínimo de cada tipo.

## Frontend: telas e estados

React com TypeScript, Vite e React Router no modo de biblioteca. O build gera arquivos estáticos entregues pelo web, então o navegador é quem executa o React e o servidor não gasta memória a mais com ele. O visual atual (claro e escuro automáticos, tabela fina, números monoespaçados) é a referência.

### Telas da v0.1

| Rota | Tela | Observação |
|------|------|------------|
| `/setup` | Criar o administrador | Só existe sem usuário. Pede o token do log |
| `/login` | Entrar | Campo "lembrar de mim". Mostra o bloqueio por excesso de tentativas |
| `/` | Alertas | Lista por mercado, adicionar, editar, remover com desfazer, salvar |
| `/status` | Status | Fontes, canais, cota do mês, última e próxima rodada |

### Estados que o design atual ainda não cobre

- Mercado fechado: a B3 fora do pregão, para não aparecer como "desatualizado" à noite e no fim de semana.
- Variação suspeita: o dia com variação acima do limite, com o texto para conferir evento corporativo.
- Falha de aviso: o resultado da última tentativa por canal, e o item que ainda não conta como alertado.
- Fonte com erro ou cota esgotada, em lugar de uma faixa de erro genérica.
- Sessão expirada no meio de uma edição, sem perder as alterações não salvas.
- Primeiro acesso, login bloqueado e erro de rede ao salvar.
- Moeda do item (R$ ou US$), derivada do símbolo.

### Comportamento

- A página consulta `/api/watch` e `/api/status` a cada minuto e nunca sobrescreve alterações ainda não salvas nem um campo em edição.
- Salvar manda a lista inteira. A API responde com o erro e o ticker que falhou.
- Remover mantém a linha por 5 segundos com o botão "Desfazer", e só então a retira.
- Alvos de toque de 44 px, foco visível, estados sempre com texto além da cor, aria-live nos avisos e contraste mínimo de 4,5:1 para texto e 3:1 para controles.
- Sem dependências de kit de componentes. CSS próprio com variáveis, como hoje.

### Ferramentas

- npm com package-lock.json e `npm ci` no CI, Node fixado em .nvmrc, eslint e tsc obrigatórios no CI, Vitest e Testing Library para os componentes.
- Tipos da API gerados do OpenAPI do FastAPI.
- `npm audit` no CI e um bot de atualização de dependências.

## Saúde e observabilidade

O projeto não conhece nenhuma ferramenta de monitoramento. Ele expõe um endereço `/healthz` e escreve logs no formato que o Docker já sabe tratar.

### `/healthz`

- Responde 200 ok se o worker gravou `run.last_run` há menos de duas vezes o intervalo das rodadas, e 503 caso contrário.
- Mede a vida do worker, não o mercado: continua ok quando a B3 está fechada, porque o worker registra o sinal de vida mesmo sem consultar nada.
- Não devolve nenhum dado e não exige login, para servir ao HEALTHCHECK do Docker e a monitores externos.
- A degradação (uma fonte com erro, cota esgotada, nenhum canal configurado) aparece em `/api/status` e na página, e não derruba o `/healthz`.

### Logs

- Saída padrão, uma linha por evento, em texto simples ou JSON, com a rotação por conta do Docker.
- Registra eventos relevantes: rodada concluída (com a duração), aviso enviado, falha por canal e por fonte, cota em 80%, login falho e criação da conta.
- Nunca registra tokens, senhas, o texto do aviso nem o corpo das respostas das fontes.
- Quem hospeda aponta o monitor que quiser (Uptime Kuma, healthchecks.io ou outro) para o `/healthz`.

## Testes e CI

A lógica de alerta é a parte que mais importa e a mais fácil de testar sem rede, então ela é coberta primeiro.

### Testes do backend (sem rede)

- Máquina de estados: cruzamento do alvo, aviso uma vez só, rearme depois da margem, troca de alvo com o ticker já alertado, aviso que falhou e é repetido, e variação suspeita.
- Mercado: horário do pregão, fins de semana, fuso, e a transição para "mercado fechado" e "desatualizado".
- Fontes: leitura de respostas gravadas da brapi e da Binance, incluindo símbolo inexistente, limite de uso e resposta inesperada.
- Cota: contagem por mês, aviso de 80% uma vez só e parada em 100%.
- Notificadores: corpo enviado e tratamento de falha, com servidor simulado.
- Login: criação da conta pelo token, sessão com e sem "lembrar de mim", expiração, limite de tentativas, Origin e Content-Type.
- API: validação de ticker e alvo, listas repetidas e limites de tamanho.
- Migrações: banco novo, atualização de uma versão anterior e recusa de banco mais novo.

### Testes do frontend

- Componentes com Vitest e Testing Library: lista, desfazer, alterações não salvas, estados e formatação de números.
- Testes de ponta a ponta (Playwright) ficam para depois da v0.1.

### CI

- Em cada pull request: ruff, testes do backend, eslint, tsc, testes do frontend, conferência de que os tipos gerados do OpenAPI estão em dia, `npm audit` e uma varredura de segredos.
- Em cada versão marcada: construir a imagem em dois estágios (Node compila o front, a imagem final do Python leva só o resultado) para amd64 e arm64, e publicar no GHCR.
- Um teste de fumaça da imagem: sobe com o compose de exemplo e confere o `/healthz`.

## Consumo de recursos no servidor atual

Cabe com folga: a versão nova soma cerca de 55 a 110 MB a mais do que o stock-alert de hoje, ou 1,5% a 3% dos 3,7 GiB do notebook. Os números abaixo são estimativas de ordem de grandeza, sem medição, e a medição vira critério de aceite.

### O que foi medido (24/10/2026, antes de subir IT-Tools e Excalidraw)

- RAM total de 3,7 GiB, com 1,1 GiB usados e 2,7 GiB disponíveis.
- Swap de 974 MiB, com 6,5 a 8,8 MiB usados.
- Desde então entraram IT-Tools, Excalidraw, PairDrop, Caddy com o plugin da Cloudflare, Diun e a página do stock-alert. Estimo uns 0,15 a 0,25 GiB a mais, ou seja, cerca de 1,3 GiB usados e 2,3 a 2,5 GiB disponíveis hoje. Isso precisa ser remedido.

### Estimativa do que entra e do que sai

| Componente | Em repouso | Pico | Limite sugerido |
|------------|------------|------|-----------------|
| web (uvicorn e FastAPI) | 50 a 80 MB | +16 a 32 MB durante um login (scrypt) | 160 MB |
| worker (Python, sem importar o FastAPI) | 25 a 40 MB | +10 MB durante a rodada | 96 MB |
| Processo de apoio do Docker por container | cerca de 10 MB cada | | |
| **Novo, total** | **95 a 140 MB** | | |
| **Sai: stock-web atual com http.server e o apoio dele** | **30 a 40 MB** | | |
| **Líquido** | **+55 a +110 MB** | | |

### Outros recursos

- Frontend: zero de RAM no servidor, porque o React roda no navegador e o web só entrega arquivos estáticos.
- CPU: uma rodada leva segundos a cada 15 minutos. Um scrypt de login custa algumas centenas de milissegundos num i3 de 2012.
- Disco: a imagem deve ocupar uns 200 a 250 MB, contra uns 50 MB da imagem Alpine de hoje. O que pesa é baixar e extrair no HDD de 5400 rpm, que leva alguns minutos.
- Build: nunca no servidor. `npm install` e o build do Vite pedem de 0,5 a 1 GB de RAM e muito tempo num i3 com HDD. O build roda no PC ou no CI, e o servidor só baixa a imagem pronta.

### Onde a conta pode falhar

- São estimativas. Homepage (Next.js), PairDrop (Node) e Uptime Kuma v2 podem ocupar mais do que o suposto, e isso afeta o disponível, e não o stock-alert.
- Se o worker importar o FastAPI e o pydantic, ele sobe de 25 a 40 MB para algo como 50 a 70 MB. Por isso ele não deve importar a camada web.
- Várias tentativas de login simultâneas multiplicam os 16 MiB do scrypt. A regra de uma ou duas tentativas ao mesmo tempo existe por isso.
- Passar do limite do container derruba o processo. O `docker inspect` mostra OOMKilled, e o container reinicia sozinho.
- Não há zram configurado. O swap em HDD existe, mas é lento e só vale como emergência.

### Critérios de aceite (medir antes e depois, com `free -m` e `docker stats --no-stream`)

| Medida | Meta |
|--------|------|
| web em repouso, após 24 horas | até 80 MB |
| worker em repouso, após 24 horas | até 50 MB |
| Pico do web com 5 logins simultâneos | abaixo do limite, sem OOMKilled |
| Memória disponível do servidor | acima de 1,5 GiB |
| Swap usado | abaixo de 100 MiB |

### Se passar da meta

- Unir web e worker num processo só, o que economiza uns 35 a 50 MB com o apoio do Docker. O custo é perder a independência entre a página e os alertas.
- Revisar as importações e as dependências, que são o que mais pesa em Python.
- Último recurso: reescrever só o worker em Go, que é o componente pequeno e sem interface.

## Migração dos dados do homelab atual

O stock-alert atual continua rodando no homelab do autor até a versão nova provar que se comporta igual. A troca é por importação da lista, sem mexer no banco antigo.

### O que existe hoje

- Um SQLite com watch, quote, run e quota, gravado pelo check.py e pela página provisória.
- O estado do alerta fica em `quote.alerted`, e a troca de alvo o zera.
- Os avisos saem pelo Telegram e pelo ntfy, configurados no .env.

### Plano

1. Subir a versão nova em paralelo, com o banco vazio e outro tópico de aviso, sem derrubar a antiga.
2. Importar só a lista de tickers e alvos, com um comando de importação. O estado "alertado" não é importado, e a primeira rodada o recalcula.
3. Comparar por uma ou duas semanas: mesmos preços, mesmos avisos, mesma hora de chegada.
4. Trocar o endereço no Caddy, desligar a versão antiga e guardar o banco dela como cópia.

### Cuidados

- Depois da importação, os tickers que já estão abaixo do alvo disparam um aviso na primeira rodada. Isso é esperado e deve ser avisado no README do comando.
- Os tokens e o tópico atuais não entram em nenhum arquivo do repositório novo.
- O .env e o banco antigo entram na cópia de segurança antes de qualquer mudança.

## Decisões em aberto

As escolhas de stack estão fechadas: Python com FastAPI, SQLite, React com TypeScript, Vite e React Router, Telegram na v1 e ntfy em seguida. Faltam estas, em ordem de quanto travam o resto.

| Decisão | Opções | Recomendação | Quando |
|---------|--------|--------------|--------|
| Nome do projeto | A definir, sem citar brapi nem Binance. Pesquisar se já existe | Antes de criar o repositório | **DECIDIDO: Baixou** |
| Licença | MIT ou Apache-2.0 (simples), ou AGPL (impede fechar o código num serviço hospedado) | MIT ou Apache-2.0, salvo preferência pela AGPL | **DECIDIDA: MIT** |
| Idioma da documentação | Só português, ou português e inglês | Português na v0.1, inglês depois | **DECIDIDO: só português na v0.1** |
| Migrações do banco | SQL numerado à mão, ou SQLAlchemy com Alembic | SQL à mão, porque o esquema é pequeno e poupa dependências | M1 |
| Imagem base do Python | slim ou alpine | slim, pela compatibilidade em amd64 e arm64 | M4 |
| Segredos no banco (v0.2) | Em texto no volume de dados, ou cifrados com uma chave de ambiente | Cifrar só se houver chave externa. Sem ela, a cifra não protege nada | v0.2 |
| Armazenar o histórico de fechamentos desde já | Sim (prepara relatórios) ou não | Não na v0.1 | M1 |
| Calendário de feriados da B3 | Fora da v0.1, ou arquivo de feriados configurável | Fora da v0.1 | v0.2+ |
| Tempo de manutenção | Quanto o autor quer dedicar a responder issues e acompanhar as fontes | Decidir antes de publicar | Antes de publicar |

## Antes de publicar

- Uso real da versão atual por uma ou duas semanas.
- Repositório novo e limpo, sem domínio, IPs, chat ID, tópicos do ntfy nem histórico antigo.
- Revogar os tokens que já passaram por conversas e por logs.
- Conferir os termos de uso da brapi e da Binance, e os da CoinGecko se ela entrar. Colocar o aviso de que o projeto não é recomendação de investimento.
