# Paridade com o homelab e publicação (runbook M5)

Este runbook guia a validação **M5** inteira numa única página, em ordem:
backup prévio, subir a versão nova em paralelo com a atual, importar a lista,
comparar por uma ou duas semanas, medir a memória, trocar o Caddy e, por fim,
passar pelo checklist **"antes de publicar"**.

> **Sobre os números:** as metas de memória citadas aqui estão fixadas em
> `scripts/medir-memoria.sh` — o script é o ÚNICO lugar com os números e com a
> lógica de comparação; este documento só cita as metas. Os números **medidos**
> do servidor não são versionados em nenhum documento: a evidência (saída do
> script, tempo da simulação "de fora") fica registrada na verificação da fase.

## 0. Antes de começar

Nada muda no homelab antes de dois cuidados:

1. **Cópia de segurança do `.env` e do banco antigo do homelab** — antes de
   QUALQUER mudança. O `.env` tem os tokens e o tópico em uso; o banco tem a
   lista e o estado dos alertas. Guarde as cópias fora do repositório do
   Baixou.
2. **Confirme que nenhum token entra em arquivo do repositório** — os tokens
   do homelab (`BRAPI_TOKEN`, `TELEGRAM_TOKEN`, tópico do ntfy) nunca entram
   em arquivo versionado do Baixou. Se algum deles já passou por conversas ou
   logs, a revogação está no checklist final (seção 6).

## 1. Subir em paralelo

Instale a versão nova **pelo README**, com a seção **"Instalar (produção)"** —
são os dois comandos dela: o `curl -O` do `compose.install.yaml` e o
`docker compose -f compose.install.yaml up -d`. Nada é derrubado: a versão
atual do homelab continua rodando.

Regras desta etapa:

- **Banco vazio** — a subida nasce sem lista; a lista chega por importação
  (seção 2), nunca de novo à mão;
- **OUTRO tópico de aviso** — o lado novo usa um tópico ntfy DIFERENTE do que
  o homelab usa (**nunca o mesmo tópico durante a paridade**) — assim cada
  aviso tem dono óbvio e ninguém confunde a origem;
- configure o `.env` do lado novo com canal e `BRAPI_TOKEN` próprios, como o
  README explica em "Para alertar de verdade".

## 2. Importar a lista

Com o admin criado no navegador (o cadastro do primeiro acesso) e a lista
**vazia** — o import só funciona com banco vazio —, rode os dois comandos
documentados no README (seção "Importar a lista do homelab"): copie o banco do
homelab para dentro do container e importe:

```bash
docker compose -f compose.install.yaml cp ./stock.db worker:/mnt/homelab.db
docker compose -f compose.install.yaml exec worker python -m baixou.worker.import_homelab /mnt/homelab.db
```

A alternativa de montagem somente-leitura num container descartável também
está documentada no README:

```bash
docker compose -f compose.install.yaml run --rm -v ./stock.db:/mnt/homelab.db:ro worker python -m baixou.worker.import_homelab /mnt/homelab.db
```

O arquivo antigo é aberto somente-leitura de qualquer forma (modo `ro` do
sqlite3) — o banco do homelab nunca é modificado pelo import.

> **Aviso esperado:** itens que já estão abaixo do alvo **disparam um aviso na
> primeira rodada**. Isso é o comportamento previsto — o estado "alertado" do
> homelab não é importado, e a primeira rodada o recalcula.

## 3. Comparar (1–2 semanas)

Deixe os dois lados rodando em paralelo por uma ou duas semanas, cada um com
seu tópico de aviso. Por dia, confira:

- **mesmos preços** — o valor do aviso do lado novo coincide com o do homelab;
- **mesmos avisos** — os mesmos itens cruzam o alvo nos dois lados (lembrando:
  na paridade o lado novo repete o aviso inicial dos itens já abaixo do alvo,
  só uma vez);
- **mesma hora de chegada** — os avisos chegam nas mesmas rodadas (a cada 15
  minutos alinhados ao relógio).

Uma diferença isolada por dia é normal (fonte pode responder diferente a cada
chamada); padrão sistemático de diferença é ajuste antes da troca (seção 5).

## 4. Medir a memória

A medição é um comando — `scripts/medir-memoria.sh` — no servidor de
referência, na pasta onde o `compose.install.yaml` está:

```bash
bash scripts/medir-memoria.sh --antes    # antes da troca (lado novo em paralelo)
bash scripts/medir-memoria.sh --depois   # depois da troca (seção 5 concluída)
```

O `--antes`/`--depois` é só um rótulo na saída. O script roda `free -m` e
`docker stats --no-stream`, compara cada medida contra as metas da arquitetura
(web até 80 MB; worker até 50 MB; swap abaixo de 100 MiB; memória disponível
acima de 1,5 GiB) e sai com erro se alguma estourar.

Duas regras da arquitetura que o script também lembra na saída:

- as metas valem para os processos **em repouso após 24 horas** — não meça
  logo depois de subir ou reiniciar;
- o **pico de login** do web (scrypt) não é medido por este script.

**Onde registrar os números:** na verificação da fase — cole a saída do
script lá. Não crie documento versionado com números medidos.

## 5. Trocar o Caddy

A comparação provou a paridade e a medição passou nas metas — hora da troca.
Estes passos são **executados por você** (guiados aqui, na sua máquina):

1. **Aponte o endereço ao lado novo** — no Caddy, mude o destino do endereço
   público do serviço antigo para o serviço novo (a porta que o
   `compose.install.yaml` publica é local, em `127.0.0.1` — o Caddy é quem
   expõe com TLS);
2. **Desligue a versão antiga** — pare o serviço/cron do homelab e confirme
   que os avisos continuam chegando pelo lado novo no horário esperado;
3. **Guarde o banco da versão antiga como cópia** — junto com a cópia do
   `.env` feita na seção 0. É o histórico da paridade; não é importado de
   volta.

O lado novo já se protege sozinho a partir daqui: o backup noturno nasce
ligado (`BACKUP_ENABLED=1`) e grava cópias diárias em `BACKUP_DIR` com
retenção de `BACKUP_KEEP` dias — confira na tela de status que o backup está
verde depois do primeiro dia.

## 6. Antes de publicar

O checklist final da publicação (D-16). **Só publique com tudo marcado.**

- [ ] **Simulação de instalação "de fora", feita por VOCÊ** — numa máquina ou
      VM limpa, siga **só o README** como quem nunca viu o projeto: do
      `curl -O` do `compose.install.yaml` até o primeiro aviso no celular.
      Registre quanto tempo levou — a meta do PRD é **até 5 minutos**.
      Tempo gasto: __________
- [ ] **Revogar os tokens** que já passaram por conversas e por logs
      (`BRAPI_TOKEN`, `TELEGRAM_TOKEN` e qualquer tópico ntfy que tenha
      funcionado como senha) e gerar novos só onde ainda precisam.
- [ ] **gitleaks verde no CI** — a varredura de segredos do
      `.github/workflows/ci.yml` passando no commit de publicação.
- [ ] **Termos de uso da brapi e da Binance conferidos** (e da CoinGecko,
      se ela entrar numa versão futura).
- [ ] **Aviso de não-recomendação de investimento visível** no topo do README.
- [ ] **Tempo de manutenção declarado** — decida e escreva no README quanto
      você dedica a responder issues e acompanhar as fontes (decisão em aberto
      da arquitetura). Declaração: __________
- [ ] **Pacote da imagem no GHCR tornado PÚBLICO** após o primeiro release —
      pacote criado por workflow nasce privado, e o `curl` de instalação de
      terceiros falha enquanto ele não for público (Settings → Packages →
      mudar visibilidade para public).
- [ ] **Nenhum dado pessoal seu no repositório nem no histórico** — re-checar:
      nenhum domínio, IP, chat ID, tópico ntfy real ou token em `git log -p`
      (o gitleaks cobre segredos; o resto é conferência manual sua).

Publicou? Atualize a seção "Atualizar" do README com a versão nova e marque
o M5 como fechado.
