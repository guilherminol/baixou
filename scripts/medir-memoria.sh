#!/usr/bin/env bash
# medir-memoria.sh — mede a memória do Baixou no servidor contra as metas da
# arquitetura (D-14), comparando e saindo com erro em estouro.
#
# AVISO (arquitetura, §Consumo de recursos): as metas valem para os processos
# EM REPOUSO após 24 HORAS da subida — não meça logo depois de subir ou
# reiniciar os containers. O PICO do web durante um login (scrypt de 16 MiB)
# NÃO é medido por este script: é outro critério de aceite.
#
# Este script é o ÚNICO lugar com os números-meta (docs citam as metas, sem
# duplicar a lógica). Nenhum número MEDIDO fica versionado: a evidência
# (saída do script) vai para a verificação da fase.
#
# Uso:
#   bash scripts/medir-memoria.sh              # sem rótulo
#   bash scripts/medir-memoria.sh --antes      # rótulo "antes" na saída
#   bash scripts/medir-memoria.sh --depois     # rótulo "depois" na saída
#
# O rótulo é só apresentação (a arquitetura pede medição antes/depois) — não
# muda nenhuma comparação.
#
# Dependências: bash, free, docker e coreutils (alvo: servidor Debian).
# Rode na pasta onde está o compose do Baixou (o compose.install.yaml), com
# os containers no ar.

set -euo pipefail

# ---------------------------------------------------------------- rótulo ----
ROTULO=""
case "${1:-}" in
  "") ;;
  --antes) ROTULO="antes" ;;
  --depois) ROTULO="depois" ;;
  *)
    echo "uso: $0 [--antes|--depois]  (o argumento é só um rótulo na saída)" >&2
    exit 2
    ;;
esac

# --------------------------------------------- metas da arquitetura (MiB) ----
META_WEB_MB=80           # web em repouso: até 80 MB
META_WORKER_MB=50        # worker em repouso: até 50 MB
META_SWAP_MB=100         # swap usado: abaixo de 100 MiB
META_DISPONIVEL_MB=1536  # memória disponível: acima de 1,5 GiB (= 1536 MiB)

# --------------------------------------------------------- ajuda e checagem --
if ! command -v free >/dev/null 2>&1; then
  echo "ERRO: 'free' não encontrado — o script precisa de procps no servidor." >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "ERRO: 'docker' não encontrado — o script precisa do Docker no servidor." >&2
  exit 1
fi

# Converte o uso do docker stats ("45.2MiB / 160MiB", "1.2GiB / ...", "305kB / ...")
# em INTEIRO de MiB — sem decimais frágeis: o parse fica no awk e é arredondado.
miB_do_uso() {
  local uso="$1"
  uso="${uso%%/*}"  # só a parte de uso, antes do limite (" / 160MiB")
  printf '%s\n' "$uso" | awk '{
    s = $1
    num = s; sub(/[A-Za-z]+$/, "", num)     # "45.2MiB" -> "45.2"
    unit = s; sub(/^[0-9.]+/, "", unit)     # "45.2MiB" -> "MiB"
    f = 1
    if (unit == "GiB") f = 1024
    else if (unit == "MiB") f = 1
    else if (unit == "kiB" || unit == "kB") f = 1 / 1024
    printf "%.0f", num * f
  }'
}

# Garante inteiro: medida ilegível é FALHA DE MEDIÇÃO (sai 1) — nunca vira
# "0 MB" com PASSA, que seria um falso verde.
exige_inteiro() {
  local valor="$1" nome="$2"
  if ! printf '%s\n' "$valor" | grep -Eq '^[0-9]+$'; then
    echo "ERRO: não consegui medir '$nome' (valor ilegível: '$valor')." >&2
    echo "O formato de saída do docker/free mudou? Meça à mão com 'docker stats --no-stream' e 'free -m' e compare contra as metas deste cabeçalho." >&2
    exit 1
  fi
}

# Container do serviço do compose (web/worker) — nomes reais via docker compose ps -q.
id_do_servico() {
  docker compose ps -q "$1" 2>/dev/null | head -n 1 || true
}

CID_WEB="$(id_do_servico web)"
CID_WORKER="$(id_do_servico worker)"
if [ -z "$CID_WEB" ] || [ -z "$CID_WORKER" ]; then
  echo "ERRO: containers web/worker não encontrados." >&2
  echo "Rode este script na pasta do compose do Baixou (compose.install.yaml) com ele no ar." >&2
  exit 1
fi

USO_WEB_RAW="$(docker stats --no-stream --format '{{.MemUsage}}' "$CID_WEB")" || {
  echo "ERRO: docker stats falhou para o container web — nada foi medido." >&2
  exit 1
}
USO_WORKER_RAW="$(docker stats --no-stream --format '{{.MemUsage}}' "$CID_WORKER")" || {
  echo "ERRO: docker stats falhou para o container worker — nada foi medido." >&2
  exit 1
}

USO_WEB="$(miB_do_uso "$USO_WEB_RAW")"
USO_WORKER="$(miB_do_uso "$USO_WORKER_RAW")"
exige_inteiro "$USO_WEB" "uso de memória do web"
exige_inteiro "$USO_WORKER" "uso de memória do worker"

# free -m: colunas em MiB — MemAvailable é a 7ª, swap usado é a 3ª.
DISPONIVEL="$(free -m | awk '/^Mem:/ {print $7}')"
SWAP_USADO="$(free -m | awk '/^Swap:/ {print $3}')"
exige_inteiro "$DISPONIVEL" "memória disponível (free -m)"
exige_inteiro "$SWAP_USADO" "swap usado (free -m)"

# ----------------------------------------------------------------- saída ----
ESTOUROS=0

linha() {
  # linha <medida> <medido MiB> <meta texto> <passou sim|nao>
  local medida="$1" medido="$2" meta_txt="$3" passou="$4" resultado
  if [ "$passou" = "sim" ]; then
    resultado="PASSA"
  else
    resultado="ESTOURO"
    ESTOUROS=$((ESTOUROS + 1))
  fi
  printf '%-26s %8s MB  %-26s %s\n' "$medida" "$medido" "$meta_txt" "$resultado"
}

echo "Medição de memória do Baixou${ROTULO:+ ($ROTULO)}"
echo "Metas da arquitetura — processos em REPOUSO após 24 h (o pico de login do web não é medido aqui)."
echo
printf '%-26s %10s  %-26s %s\n' "Medida" "Medido" "Meta" "Resultado"
printf '%s\n' "---------------------------------------------------------------------------"

# web: até 80 MB (<=)
if [ "$USO_WEB" -le "$META_WEB_MB" ]; then
  linha "web (container)" "$USO_WEB" "até 80 MB" sim
else
  linha "web (container)" "$USO_WEB" "até 80 MB" nao
fi

# worker: até 50 MB (<=)
if [ "$USO_WORKER" -le "$META_WORKER_MB" ]; then
  linha "worker (container)" "$USO_WORKER" "até 50 MB" sim
else
  linha "worker (container)" "$USO_WORKER" "até 50 MB" nao
fi

# swap usado: abaixo de 100 MiB (<)
if [ "$SWAP_USADO" -lt "$META_SWAP_MB" ]; then
  linha "swap usado" "$SWAP_USADO" "abaixo de 100 MiB" sim
else
  linha "swap usado" "$SWAP_USADO" "abaixo de 100 MiB" nao
fi

# memória disponível: acima de 1,5 GiB (>)
if [ "$DISPONIVEL" -gt "$META_DISPONIVEL_MB" ]; then
  linha "memória disponível" "$DISPONIVEL" "acima de 1,5 GiB" sim
else
  linha "memória disponível" "$DISPONIVEL" "acima de 1,5 GiB" nao
fi

echo
if [ "$ESTOUROS" -gt 0 ]; then
  echo "Resultado: $ESTOUROS meta(s) estourada(s) — cole esta saída na verificação da fase e investigue antes de seguir."
  exit 1
fi
echo "Resultado: todas as metas passam — cole esta saída na verificação da fase (não versiona números medidos)."
exit 0
