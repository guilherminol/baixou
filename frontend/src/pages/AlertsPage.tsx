import { useEffect, useState } from "react";
import AppHeader from "../components/AppHeader";
import { listarItens, type WatchItem } from "../api/watch";
import { buscarStatus, type StatusResponse } from "../api/status";
import { formatCurrency, formatPercent, formatTime, timeAgoMinutes } from "../lib/format";
import "../styles/alerts.css";

/** Chave única do item: symbol|market (o símbolo é único por mercado). */
function chave(item: WatchItem): string {
  return `${item.symbol}|${item.market}`;
}

/**
 * Estado do item em TEXTO, sempre além da cor (D-07), com a precedência
 * congelada do contrato: pausado → sem cotação (fresh none) → fechado
 * (fresh closed) → desatualizado (fresh stale) → no alvo (price <= alvo e
 * NÃO alertado) → alertado → aguardando. O navegador NUNCA deriva frescor
 * por idade: consome o `fresh`/`suspect`/`paused` prontos da API (D-02).
 */
export function estadoTexto(item: WatchItem): string {
  if (item.paused) return "pausado";
  if (item.fresh === "none") return "sem cotação";
  if (item.fresh === "closed") return "fechado";
  if (item.fresh === "stale") return "desatualizado";
  if (!item.alerted && item.price != null && item.price <= item.target_below) {
    return "no alvo";
  }
  if (item.alerted) return "alertado";
  return "aguardando";
}

/** Classe do estado na linguagem do mockup: accent para no alvo/alertado,
 * riscado para desatualizado, muted para o resto. */
export function estadoClasse(item: WatchItem): string {
  const texto = estadoTexto(item);
  if (texto === "no alvo" || texto === "alertado") return "tag-state state-hit";
  if (texto === "desatualizado") return "tag-state state-stale";
  return "tag-state";
}

/** Distância até o alvo (molde do mockup): price <= alvo → "no alvo" em
 * accent; senão ((alvo - preço) / preço) × 100; sem preço → "—". */
export function distancia(item: WatchItem): { texto: string; classe: string } {
  const price = item.price ?? null; // o tipo gerado traz price?: number | null
  if (price === null) return { texto: "—", classe: "num" };
  if (price <= item.target_below) {
    return { texto: "no alvo", classe: "num state-hit" };
  }
  return {
    texto: formatPercent(((item.target_below - price) / price) * 100),
    classe: "num",
  };
}

/** Linha curta de variação suspeita (D-07) — o servidor decide a borda
 * inclusiva (suspect), o navegador só formata o change_pct recebido. */
export function linhaSuspeita(item: WatchItem): string | null {
  if (!item.suspect || typeof item.change_pct !== "number") return null;
  return `variação de ${formatPercent(item.change_pct)} hoje — confira evento corporativo`;
}

/** Falha de canal sob o estado (D-07/D-15): cada entrada de notify_state
 * com valor != "ok" vira a linha curta "{canal}: erro". */
export function falhasDeCanal(item: WatchItem): string[] {
  const estado = item.notify_state;
  if (estado === null || estado === undefined) return [];
  return Object.entries(estado)
    .filter(([, valor]) => valor !== "ok")
    .map(([canal]) => `${canal}: erro`);
}

/** Meta-line do cabeçalho (D-03): "Última consulta há X min · próxima em
 * Y min · cota Z/W" — tudo derivado do /api/status, números pt-BR. */
function metaDoStatus(status: StatusResponse): string {
  const ultima = status.ultima_rodada
    ? `há ${timeAgoMinutes(status.ultima_rodada)} min`
    : "—";
  const proxima = Math.max(
    0,
    Math.round((new Date(status.proxima_rodada).getTime() - Date.now()) / 60000),
  );
  return `Última consulta ${ultima} · próxima em ${proxima} min · cota ${status.cota.usadas.toLocaleString("pt-BR")}/${status.cota.limite.toLocaleString("pt-BR")}`;
}

/**
 * Problemas do banner "Aviso do sistema" (D-17) — derivados SOMENTE do
 * /api/status, nunca de limiares no cliente: cota esgotada (usadas >=
 * limite), fonte em falha_prolongada (citando o NOME da fonte) e nenhum
 * canal configurado.
 */
export function problemasDoStatus(status: StatusResponse): string[] {
  const problemas: string[] = [];
  if (status.cota.usadas >= status.cota.limite) {
    problemas.push("cota mensal da brapi esgotada");
  }
  for (const fonte of status.fontes) {
    if (fonte.estado === "falha_prolongada") {
      problemas.push(`fonte ${fonte.nome} em falha prolongada`);
    }
  }
  if (status.canais.every((canal) => !canal.configurado)) {
    problemas.push("nenhum canal configurado");
  }
  return problemas;
}

const COLUNAS = 8; // Ticker · Preço · Alvo · Distância · Estado · Atualização · Disparo · ações

function CabecalhoTabela() {
  return (
    <thead>
      <tr>
        <th>Ticker</th>
        <th className="num">Preço</th>
        <th className="num">Alvo (&lt;)</th>
        <th className="num col-dobra">Distância</th>
        <th>Estado</th>
        <th className="num col-dobra">Atualização</th>
        <th className="num col-dobra">Disparo</th>
        <th></th>
      </tr>
    </thead>
  );
}

function LinhaItem({ item }: { item: WatchItem }) {
  const dist = distancia(item);
  const suspeita = linhaSuspeita(item);
  const canais = falhasDeCanal(item);
  return (
    <>
      <tr>
        <td className="symbol-cell">{item.symbol}</td>
        <td className="num">{formatCurrency(item.price, item.currency)}</td>
        <td className="num">
          {/* A edição liga no próximo passo; aqui o molde já está no DOM. */}
          <input
            className="input-target"
            type="number"
            step="any"
            min="0"
            aria-label={`Alvo de ${item.symbol}`}
            defaultValue={item.target_below}
          />
        </td>
        <td className={dist.classe + " col-dobra"}>{dist.texto}</td>
        <td>
          <span className={estadoClasse(item)}>{estadoTexto(item)}</span>
          {suspeita !== null ? (
            <span className="estado-extra">{suspeita}</span>
          ) : null}
          {canais.map((falha) => (
            <span key={falha} className="estado-extra">
              {falha}
            </span>
          ))}
        </td>
        <td className="num col-dobra">{formatTime(item.price_at)}</td>
        <td className="num col-dobra">{formatTime(item.alerted_at)}</td>
        <td></td>
      </tr>
      {/* Sub-linha D-06: oculta no desktop, revelada <= 600px pela media
          query de alerts.css (o conteúdo dobra distância + horas). */}
      <tr className="sub-linha-row">
        <td colSpan={COLUNAS} className="sub-linha">
          <span>distância {dist.texto}</span>
          <span>atualização {formatTime(item.price_at)}</span>
          <span>disparo {formatTime(item.alerted_at)}</span>
        </td>
      </tr>
    </>
  );
}

function SecaoMercado({
  titulo,
  itens,
  vazia,
  carregando,
}: {
  titulo: string;
  itens: WatchItem[];
  vazia: string;
  carregando: boolean;
}) {
  return (
    <section className="market-section">
      <h2 className="market-title">{titulo}</h2>
      <div className="table-wrap">
        <table>
          <CabecalhoTabela />
          <tbody>
            {carregando ? null : itens.length === 0 ? (
              <tr>
                <td colSpan={COLUNAS} className="empty-state">
                  {vazia}
                </td>
              </tr>
            ) : (
              itens.map((item) => <LinhaItem key={chave(item)} item={item} />)
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// Página de alertas (UI-01) — caminho de LEITURA: carrega /api/watch e
// /api/status em paralelo, agrupa por mercado e renderiza estados em texto
// (D-07), coluna Disparo (D-08), meta-line (D-03) e banner do sistema
// (D-17). As mutações (adicionar/editar/remover/salvar/pausar) entram na
// sequência deste plano; o polling e o re-login no lugar (D-10) depois delas.
export default function AlertsPage() {
  const [itens, setItens] = useState<WatchItem[] | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);

  useEffect(() => {
    let vivo = true;
    void listarItens()
      .then((resposta) => {
        if (vivo) setItens(resposta.itens);
      })
      .catch(() => {
        // 401 vai pelo handler global; erro de rede preserva o estado atual.
      });
    void buscarStatus()
      .then((estado) => {
        if (vivo) setStatus(estado);
      })
      .catch(() => {
        // Sem /api/status não há meta-line nem banner — a lista segue.
      });
    return () => {
      vivo = false;
    };
  }, []);

  const carregando = itens === null;
  const b3 = (itens ?? []).filter((item) => item.market === "b3");
  const cripto = (itens ?? []).filter((item) => item.market === "crypto");
  const problemas = status === null ? [] : problemasDoStatus(status);

  return (
    <main className="container">
      <AppHeader meta={status === null ? "Aguardando dados..." : metaDoStatus(status)} />
      {problemas.length > 0 ? (
        <div className="banner-error visivel" role="alert">
          Aviso do sistema: {problemas.join("; ")}
        </div>
      ) : null}
      <SecaoMercado
        titulo="Ações (B3)"
        itens={b3}
        vazia="Nenhuma ação monitorada. Use o formulário abaixo."
        carregando={carregando}
      />
      <SecaoMercado
        titulo="Cripto"
        itens={cripto}
        vazia="Nenhuma criptomoeda monitorada. Use o formulário abaixo."
        carregando={carregando}
      />
    </main>
  );
}
