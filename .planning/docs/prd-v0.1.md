# PRD — Alertas de preço (v0.1)

> Documento-fonte fornecido pelo autor em 27/10/2026. Preservado integralmente como referência de produto autoritativa para as fases de planejamento e execução. Documento irmão: [arquitetura-v0.1.md](arquitetura-v0.1.md).

27 de out. de 2026 · @Guilhermino

## Resumo

A v0.1 transforma o alerta de preço que já roda no homelab do autor em um projeto de código aberto que qualquer pessoa instala com `docker compose up`.

**Problema.** O alerta atual funciona, mas só no servidor do autor. Ele depende do cron do host, de uma senha feita no Caddy, de um .env editado à mão e de fuso e horário fixos no código. Outra pessoa não consegue instalar nem manter.

**Solução da v0.1.** Dois serviços da mesma imagem, web e worker, e um banco SQLite. O worker consulta a brapi (B3) e a Binance (cripto) a cada 15 minutos e avisa por Telegram e ntfy quando o preço chega ao alvo "abaixo de". O web entrega uma página em React para gerir a lista, com login próprio.

**Estado atual.** Existe uma versão provisória rodando no homelab do autor (script Python com cron e página em HTML único). Ela serve de referência de comportamento e de aparência, e roda em paralelo até a nova provar que se comporta igual.

**Documento irmão.** O desenho técnico (componentes, banco, API, máquina de estados e consumo de memória) está em *Arquitetura — Alertas de preço (v0.1)*. Este PRD diz o que entregar e em que ordem, e a arquitetura diz como.

## Objetivos e métricas de sucesso

A v0.1 está pronta quando uma pessoa de fora instala pelo README em menos de cinco minutos, recebe um aviso de teste e o projeto cabe com folga num notebook de 4 GB. As metas abaixo são propostas, e o autor pode ajustá-las.

### Objetivos

1. Instalar com `docker compose up`, sem Node, sem cron do host e sem proxy específico.
2. Avisar de forma confiável: um aviso por cruzamento, com repetição só depois do rearme, e nunca marcado como enviado se nenhum canal o aceitou.
3. Proteger o acesso e os segredos por padrão.
4. Pesar pouco, para rodar ao lado de outros serviços num homelab modesto.
5. Ser fácil de estender: fonte nova e notificador novo viram módulos pequenos e isolados.

### Métricas e metas

| Métrica | Meta da v0.1 | Como medir |
|---------|--------------|------------|
| Tempo de instalação por alguém de fora | Até 5 minutos, só com o README | Teste com uma ou duas pessoas que não conhecem o projeto |
| Latência do aviso depois que a fonte traz o preço | Até uma rodada (15 minutos) | Aviso real e comparação do price_at com a hora do envio |
| RAM do web em repouso | Até 80 MB | docker stats após 24 horas |
| RAM do worker em repouso | Até 50 MB | docker stats após 24 horas |
| RAM disponível no servidor de referência | Acima de 1,5 GiB | `free -m` com a pilha completa |
| Avisos duplicados por cruzamento | Zero | Testes da máquina de estados e uma semana de uso real |
| Segredos em logs, erros e respostas da API | Zero | Teste automático e revisão manual |
| Arquiteturas da imagem | amd64 e arm64 | CI publica as duas |
| Cobertura das regras de alerta | Todas as regras listadas na arquitetura, com teste | Lista de regras contra a lista de testes |

## Público e casos de uso

O projeto é para quem hospeda os próprios serviços e quer um alerta de preço simples, sem depender de uma conta num serviço de terceiros além das fontes de dados.

### Quem

| Quem | O que precisa | Como a v0.1 atende |
|------|---------------|--------------------|
| Pessoa com homelab (o autor e quem seguir o mesmo caminho) | Ser avisada quando uma ação ou cripto chega ao preço que ela definiu | Lista com alvos, rodadas a cada 15 minutos e aviso no celular |
| Quem hospeda em Raspberry Pi, NAS ou VPS pequena | Pouca memória e instalação sem compilar nada | Imagem pronta para amd64 e arm64, uso de RAM medido |
| Quem contribui com código | Entender e estender o projeto sem ler tudo | Interfaces de fonte e notificador, testes e CONTRIBUTING |

### Casos de uso principais

1. **Primeiro acesso**: subir o compose, abrir a página, criar o administrador com o token do log e configurar um canal de aviso.
2. **Cadastrar** um ticker da B3 ou uma cripto com um alvo, salvar e ver o preço aparecer depois da próxima rodada.
3. **Receber o aviso** quando o preço chega ao alvo, abrir a página pelo toque na notificação e ajustar o alvo.
4. **Perceber que algo está errado**: uma fonte com erro, a cota da brapi quase no fim, ou a cotação desatualizada, sem precisar ler logs.
5. **Plugar um monitor externo** no `/healthz` e ser avisado se o worker parar.
6. **Manter**: atualizar a imagem sem perder a lista nem a conta, e recuperar o acesso pelo comando de redefinição de senha.

## Escopo

A v0.1 cobre só o alerta "abaixo de", para B3 e cripto, com Telegram e ntfy. Tudo o que exige mais telas, mais fontes ou dados históricos fica para as versões seguintes.

| Versão | Entra | Fica de fora |
|--------|-------|--------------|
| v0.1 | Alerta "abaixo de", brapi, Binance, Telegram, ntfy, login de usuário único, página (setup, login, alertas, status), /healthz, configuração por variáveis de ambiente, importação da lista do homelab atual | Página de configurações, alerta "acima de", relatórios, carteira, vários usuários, idiomas, histórico de preços |
| v0.2 | Página de configurações com segredos só de escrita, botão de teste dos canais, horário de silêncio, exportar e importar a lista, notificadores extras (webhook, Discord, e-mail), calendário de feriados da B3, lista de sessões ativas | Carteira e relatórios |
| v0.3 | Carteira (operações, preço médio), relatórios semanal e mensal em valores absolutos e relativos, histórico de fechamentos | Vários usuários |

### Restrições que valem para todas as versões

- Cada pessoa usa as próprias chaves das fontes, e o projeto não embute nenhuma.
- Instância única, para uma pessoa, na rede doméstica e atrás de um proxy com HTTPS. Não é feito para ficar exposto direto na internet.
- O projeto não conhece ferramentas de monitoramento. Ele só expõe o `/healthz`.
- O projeto não é recomendação de investimento, e os dados de mercado têm atraso e podem errar.

## Requisitos funcionais

A v0.1 tem 18 requisitos: 10 essenciais, 7 importantes e 1 desejável. Os essenciais definem o que impede a publicação.

| ID | Requisito | Prioridade | Critério de aceite |
|----|-----------|------------|--------------------|
| FR-01 | Cadastrar, editar o alvo e remover tickers da B3 e de cripto, com alvo maior que zero, validação do símbolo e recusa de duplicados | Essencial | Salvar lista inválida devolve o erro e o ticker que falhou. Remover mantém o "Desfazer" por 5 segundos |
| FR-02 | Rodadas a cada 15 minutos alinhadas ao relógio. B3 só de segunda a sexta, das 10h às 18h (fuso configurável), e cripto o dia todo | Essencial | Testes de horário e fuso. Uma rodada grava cotação de pelo menos um ticker de cada mercado |
| FR-03 | Alerta "abaixo de": um aviso por cruzamento, rearme depois da margem acima do alvo, e reavaliação quando o alvo muda | Essencial | Testes da máquina de estados cobrem todas as regras da arquitetura |
| FR-04 | Canais Telegram e ntfy, ambos sempre com título. O item só conta como alertado se pelo menos um canal aceitar | Essencial | Servidor simulado: um canal ok e outro com falha dá "alertado". Os dois com falha repete na rodada seguinte |
| FR-05 | Fontes brapi (um ticker por chamada) e Binance (lote, com espelho em caso de 451 e repetição símbolo a símbolo em caso de 400) | Essencial | Respostas gravadas: símbolo inexistente, limite de uso e resposta inesperada |
| FR-06 | Cota mensal da brapi: contagem, aviso a 80% e parada a 100% | Importante | Um aviso por mês. Sem consulta à B3 após 100%. Zera na virada do mês |
| FR-07 | Estados da cotação (fresca, desatualizada, mercado fechado, sem cotação) e aviso de variação suspeita | Importante | Página mostra o estado em texto. O aviso pede a conferência quando a variação passa do limite |
| FR-08 | Aviso depois de 3 rodadas seguidas com erro e aviso quando normalizar | Importante | Um aviso por episódio, e não um por rodada |
| FR-09 | Página de alertas: lista por mercado, preço, distância até o alvo, estado, hora da cotação, adicionar, editar, remover com desfazer e salvar | Essencial | Fluxo completo nos testes de componente. Tela de 360 px sem rolagem lateral |
| FR-10 | Tela de status: última e próxima rodada, cota do mês, estado de cada fonte e de cada canal | Importante | `/api/status` e a tela mostram os campos. Erro de fonte aparece com o nome da fonte |
| FR-11 | Login de usuário único, criado no primeiro acesso com o token do log. Sessão com e sem "lembrar de mim" e sair | Essencial | Testes de sessão, expiração e validade conferida no servidor |
| FR-12 | Limite de tentativas de login por usuário e por IP, com espera crescente | Essencial | Após o limite, resposta 429 com Retry-After. Resposta igual para usuário inexistente |
| FR-13 | Redefinir a senha por comando dentro do container | Importante | Comando documentado. Encerra as sessões existentes |
| FR-14 | `/healthz`: 200 se o worker registrou sinal de vida em menos de duas vezes o intervalo, 503 caso contrário, sem devolver dados | Essencial | Teste com worker parado. O HEALTHCHECK da imagem usa o endereço |
| FR-15 | Configuração por variáveis de ambiente e `_FILE`. Valor inválido impede a subida | Essencial | Tabela de variáveis documentada. Teste com valores inválidos |
| FR-16 | Moeda do item (R$ ou US$) derivada do sufixo do símbolo (BRL, USDT, USDC, USD) | Importante | Página e aviso mostram a moeda correta |
| FR-17 | Importar por comando a lista de tickers e alvos do banco do homelab atual, sem o estado "alertado" | Importante | Lista importada. A primeira rodada recalcula o estado |
| FR-18 | Botão de teste dos canais de aviso (v0.2) | Desejável | Envia um aviso de teste sem gravar nada |

## Requisitos não funcionais

O que mais restringe a v0.1 é a memória do servidor de referência, um notebook de 4 GB com HDD, e a proteção dos segredos. As metas de memória são estimativas e viram critério de aceite depois da medição.

| Área | Requisito | Meta ou verificação |
|------|-----------|---------------------|
| Memória | web em repouso até 80 MB, worker até 50 MB, com limites de container de 160 MB e 96 MB | docker stats após 24 horas. Sem OOMKilled com 5 logins simultâneos |
| Servidor de referência | Memória disponível acima de 1,5 GiB e swap abaixo de 100 MiB com a pilha completa | `free -m` antes e depois |
| Processos | Um único processo do uvicorn e o worker sem importar a camada web | Revisão de código e medição |
| Segurança | scrypt, sessão no servidor, cookie HttpOnly e SameSite=Strict, Secure configurável, política de conteúdo restrita a 'self', segredos só de escrita | Testes de login e revisão contra a lista da arquitetura |
| Privacidade | Nenhum token, senha, texto de aviso ou corpo de resposta nos logs e nos erros | Teste automático e varredura de segredos no CI |
| Confiabilidade | Uma fonte com erro não impede a outra. O processo sobrevive a qualquer erro de rodada e para com SIGTERM sem gravar pela metade | Testes de falha simulada |
| Dados | Migrações versionadas e transacionais. Recusa de banco mais novo que a versão do programa | Testes de migração |
| Compatibilidade | Imagem para amd64 e arm64. Sem Node em produção. Sem dependência de CDN, fonte externa ou serviço de terceiros no navegador | Teste de fumaça no CI |
| Instalação | Compose de exemplo, publicado só em 127.0.0.1, e README de cinco minutos | Teste com alguém de fora |
| Acessibilidade | Contraste mínimo de 4,5:1 no texto e 3:1 nos controles, estados com texto além da cor, alvos de toque de 44 px, teclado e foco visíveis, zoom de 200% | Checagem manual e ferramenta automática |
| Interface | Português do Brasil. Tema claro e escuro automáticos. Largura mínima de 360 px sem rolagem lateral | Teste visual nos dois temas |
| Manutenção | Lint e tipos obrigatórios no CI, tipos da API gerados do OpenAPI, dependências fixadas e auditadas | CI verde em cada pull request |
| Legal | Licença aberta, aviso de que não é recomendação de investimento, termos das fontes respeitados e nenhuma chave embutida | Revisão antes da publicação |

## Plano de implementação

A v0.1 sai de seis marcos em sequência, cada um com um gate de saída que precisa ser cumprido antes de seguir. Não há datas, porque o tempo de dedicação do autor ainda não foi definido.

| Marco | Entrega | Tamanho | Gate de saída | Depende de |
|-------|---------|---------|---------------|------------|
| M0. Preparo e decisões | Decisões fechadas, repositório novo e limpo, medições de base | Pequeno | Nome, licença e tempo de dedicação definidos. Tokens expostos revogados. Memória do servidor medida | Uso real da versão atual |
| M1. Núcleo do backend | Esquema e migrações, fontes, notificadores, máquina de estados, cota e laço do worker | Grande | Testes da lógica de alerta, do mercado e do fuso passando. Uma rodada real em modo de ensaio | M0 |
| M2. Acesso e API | Primeiro acesso, login, sessões, limite de tentativas, rotas da API, /healthz e os comandos de redefinição e importação | Médio | Testes de login e de API passando. Revisão da lista de segurança | M1 |
| M3. Frontend | React com TypeScript: setup, login, alertas, status, estados, temas e acessibilidade | Grande | Testes de componente passando. Tela de 360 px e os dois temas conferidos | Contrato da API, do M2 |
| M4. Imagem, CI e documentação | Dockerfile em dois estágios, compose de exemplo, CI, imagem multi-arquitetura, README e documentos do repositório | Médio | CI verde e imagem publicada. A instalação do zero funciona | M2 e M3 |
| M5. Validação no homelab | Execução em paralelo com a versão atual, comparação de avisos, medição de memória e teste de instalação de fora | Pequeno em esforço, e de uma a duas semanas no calendário | Critérios de aceite da v0.1 atendidos | M4 |

Os tamanhos são relativos entre os marcos, e não horas. O M1 e o M3 concentram a maior parte do trabalho.

## Backlog por marco

O backlog segue a ordem dos marcos. Marque as tarefas conforme avançam, e acrescente o que aparecer pelo caminho.

### M0. Preparo e decisões

- [ ] Concluir uma ou duas semanas de uso real da versão atual e anotar os defeitos
- [ ] Validar a página provisória: senha, troca de tema, salvar, desfazer e tela estreita
- [ ] Medir o atraso real da brapi no pregão, comparando a hora da cotação com a hora da consulta
- [ ] Medir a memória do servidor antes de começar (`free -m` e `docker stats`)
- [ ] Definir nome e licença, e pesquisar se o nome já existe
- [ ] Revogar os tokens de bot e de tópico que já passaram por conversas e logs
- [ ] Criar o repositório novo, limpo, sem histórico antigo nem dados pessoais

### M1. Núcleo do backend

- [ ] Estrutura do repositório: backend/, frontend/, docker/ e docs/
- [ ] Esquema SQLite e migrações versionadas
- [ ] Interface de fonte, com brapi e Binance, e respostas gravadas para os testes
- [ ] Interface de notificador, com Telegram e ntfy (JSON e título obrigatório)
- [ ] Máquina de estados do alerta e a regra "pelo menos um canal aceita"
- [ ] Contagem de cota e avisos de falha prolongada
- [ ] Laço do worker: alinhamento ao relógio, trava de instância única e parada com SIGTERM
- [ ] Testes da lógica de alerta, do horário do mercado e do fuso

### M2. Acesso e API

- [ ] Token de primeiro acesso e criação do administrador
- [ ] Login, sessões no servidor, "lembrar de mim" e sair
- [ ] Limite de tentativas e confiança só em proxies declarados
- [ ] Rotas de /api e /healthz, com o OpenAPI e a documentação automática desligada por padrão
- [ ] Cabeçalhos de segurança e conferência de Origin
- [ ] Comandos de redefinição de senha e de importação da lista do homelab atual
- [ ] Testes de login, de API e de migração

### M3. Frontend

- [ ] Projeto Vite com TypeScript e React Router em modo biblioteca, com eslint e tsc
- [ ] Tipos gerados do OpenAPI
- [ ] Telas de setup, login, alertas e status, com todos os estados da arquitetura
- [ ] Temas claro e escuro, layout de 360 px e acessibilidade
- [ ] Testes de componente

### M4. Imagem, CI e documentação

- [ ] Dockerfile em dois estágios e compose de exemplo com web, worker e volume
- [ ] HEALTHCHECK apontando para o /healthz
- [ ] CI com lint, testes, tipos, auditoria de dependências e varredura de segredos
- [ ] Publicação multi-arquitetura no GHCR
- [ ] README, CONTRIBUTING, SECURITY.md, aviso legal e modelos de issue

### M5. Validação no homelab

- [ ] Subir a versão nova em paralelo, com outro tópico de aviso
- [ ] Importar a lista e comparar preços e avisos por uma ou duas semanas
- [ ] Medir a memória e comparar com as metas
- [ ] Teste de instalação por alguém de fora
- [ ] Ajustar o que a validação mostrar e fechar os critérios de aceite

## Critérios de aceite da v0.1

A v0.1 só é publicada quando todos os itens abaixo estiverem marcados. O último depende de uma pessoa de fora do projeto.

### Comportamento

- [ ] Os 10 requisitos essenciais passam nos critérios de aceite da tabela.
- [ ] Uma semana de uso real no homelab sem aviso duplicado, sem aviso perdido e sem queda do worker não percebida.
- [ ] Os avisos da versão nova coincidem com os da atual na comparação em paralelo.

### Qualidade e segurança

- [ ] CI verde: lint, tipos, testes do backend e do frontend, auditoria de dependências e varredura de segredos.
- [ ] Nenhum token, senha ou texto de aviso aparece em logs, erros ou respostas da API.
- [ ] A lista de segurança da arquitetura está atendida, e o login foi testado com tentativas em excesso e com requisições de outra origem.
- [ ] Contraste, teclado e zoom de 200% conferidos nos dois temas.

### Desempenho

- [ ] web até 80 MB e worker até 50 MB em repouso, após 24 horas.
- [ ] Memória disponível do servidor de referência acima de 1,5 GiB com a pilha completa.

### Distribuição

- [ ] Imagem publicada para amd64 e arm64, e o compose de exemplo sobe do zero.
- [ ] Atualizar da versão anterior preserva a lista e a conta.
- [ ] README, CONTRIBUTING, SECURITY.md, licença e aviso legal publicados.
- [ ] O repositório não contém domínio, IP, chat ID, tópico ou token do autor, nem no histórico.

### Teste de fora

- [ ] Alguém que não conhece o projeto instala só com o README em até 5 minutos e recebe um aviso.

## Riscos e mitigações

Os três riscos de severidade alta são as fontes de dados gratuitas, o vazamento de segredos e o login. Os dois primeiros já têm mitigação no desenho, e o login é coberto por testes.

| Risco | Severidade | Mitigação |
|-------|------------|-----------|
| APIs gratuitas mudam o formato, o limite ou o acesso (brapi, Binance) | Alta | Interface de fonte com respostas gravadas nos testes. Estado de erro visível na página. Aviso depois de 3 rodadas com erro. Fontes reserva (CoinGecko, Yahoo) como contribuição |
| Atraso da brapi maior do que o esperado, e o alerta chega tarde | Média | Medir no pregão durante o M0. Documentar o atraso. Limite de "desatualizado" configurável |
| Alerta falso por evento corporativo (desdobramento, bonificação) | Média | Aviso de variação suspeita junto do alerta. Documentar a limitação. Calendário de eventos só depois |
| Vazamento de segredos em logs, respostas, histórico do Git ou capturas de tela | Alta | Segredos só de escrita. Varredura de segredos no CI. Repositório novo sem histórico. Revogar os tokens já expostos antes de publicar |
| Memória acima da estimada no servidor de referência | Média | Medir no M0 e no M5. Unir web e worker, revisar dependências e, último recurso, reescrever o worker em Go |
| Login frágil: força bruta, cookie Secure em HTTP, IP errado atrás de proxy | Alta | Limite de tentativas, token de primeiro acesso, Secure configurável, lista de proxies confiáveis e testes dedicados |
| Pessoas expõem o serviço direto na internet | Média | Exemplo de compose em 127.0.0.1, aviso no README e no SECURITY.md, nenhum recurso que dependa de exposição pública |
| O escopo cresce (carteira, relatórios, outros mercados) antes da v0.1 | Média | Congelar a v0.1 nos requisitos da tabela. Pedidos novos vão para a v0.2 e a v0.3 |
| Falta de tempo para manter o projeto depois de publicado | Média | Decidir o tempo de manutenção antes de publicar. CONTRIBUTING claro. Fontes e notificadores como módulos isolados. README com o nível de suporte |
| Termos de uso das fontes (atribuição, uso comercial) | Média | Conferir os termos antes de publicar. Cada pessoa usa as próprias chaves. Créditos no README, e atribuição da CoinGecko se ela entrar |

## Dependências e premissas

O projeto depende de quatro serviços externos gratuitos e de um servidor de referência. Cada pessoa usa as próprias contas e chaves.

| Dependência | Para quê | Observação |
|-------------|----------|------------|
| brapi | Cotações da B3 | Plano grátis com 15.000 chamadas por mês e um ticker por chamada. Exige token grátis para tickers fora dos de teste. O atraso real ainda precisa ser medido no pregão |
| Binance (dados públicos) | Cotações de cripto | Sem chave. Espelho de dados públicos em caso de bloqueio (HTTP 451). O par BTCBRL já foi testado, e a lista de pares em reais não foi conferida |
| Telegram Bot API | Avisos | Cada pessoa cria o próprio bot e descobre o próprio chat ID |
| ntfy | Avisos | Servidor público ntfy.sh ou próprio. No público, o nome do tópico funciona como senha |
| Docker e GHCR | Distribuição | Imagem multi-arquitetura. Assume GitHub e GitHub Actions |
| Servidor de referência | Medição | Notebook com Core i3-2350M, 4 GB de RAM, HDD de 5400 rpm e Debian 12 |

### Premissas

- Uma pessoa por instância, e o HTTPS vem de um proxy que a própria pessoa mantém.
- O autor mantém a versão atual no homelab durante a validação, e só a desliga depois do M5.
- Não há custo de infraestrutura além do que cada pessoa já tem, e as fontes usadas são gratuitas.
- Os nomes das variáveis de ambiente da arquitetura são uma proposta e podem mudar antes da v0.1.
- Não há datas neste plano, porque a disponibilidade do autor não foi definida. Os marcos têm tamanho relativo, e as datas entram depois de fechar o tempo de dedicação.

## Decisões em aberto

Nome e licença são as duas decisões que travam o começo, porque o repositório novo precisa dos dois. As decisões técnicas (migrações, imagem base, segredos no banco, histórico de fechamentos e feriados) continuam abertas na arquitetura.

| Decisão | Opções | Recomendação | Trava |
|---------|--------|--------------|-------|
| Nome do projeto | A definir, sem citar brapi nem Binance. Pesquisar se já existe | Escolher antes de criar o repositório | **DECIDIDO: Baixou** | M0 |
| Licença | MIT ou Apache-2.0, ou AGPL | MIT ou Apache-2.0, salvo preferência pela AGPL | **DECIDIDA: MIT** | M0 |
| Tempo de dedicação do autor | Quantas horas por semana, e por quanto tempo depois da publicação | Definir antes de publicar, para fechar datas e o nível de suporte do README | M0 |
| Idioma da documentação | Só português, ou português e inglês | Português na v0.1 | **DECIDIDO: só português** | M4 |
| Repositório e CI | GitHub e GitHub Actions (assumido), ou outra plataforma | Manter a premissa | M1 |
| Quem faz o teste de instalação de fora | Uma ou duas pessoas que não conhecem o projeto | Escolher antes do M5 | M5 |
| Margem de rearme e limite de variação suspeita | 1% e 20% como hoje, ou outros valores | Manter os valores atuais como padrão configurável | M1 |
