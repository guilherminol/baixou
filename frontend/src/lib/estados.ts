// Derivações de EXIBIÇÃO em TEXTO dos itens (D-07/D-08) e do status
// (D-14/D-16/D-17) — sempre texto além da cor. O navegador NUNCA calcula
// limiar de frescor: consome `fresh`/`suspect`/`paused`/`estado` PRONTOS da
// API (D-02). Extraído das páginas no 05-05 (Task 1, eslint): páginas de
// React exportam SÓ componentes (react-refresh/only-export-components) —
// helpers compartilhados moram na lib, como format.ts.
import type { StatusResponse } from "../api/status";
import type { WatchItem } from "../api/watch";
import { formatPercent } from "./format";

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

/** Estado da fonte em TEXTO legível (D-14): o enum vem pronto do servidor
 * ("operacional" | "erro" | "falha_prolongada"); só o snake_case vira espaço. */
export function textoEstadoFonte(estado: string): string {
  if (estado === "falha_prolongada") return "falha prolongada";
  return estado;
}

/** Classe do estado da fonte (D-14): erro e falha prolongada ganham destaque
 * na cor de erro do design system — estado sempre além da cor, nunca só ela. */
export function classeEstadoFonte(estado: string): string {
  return estado === "operacional" ? "tag-state" : "tag-state fonte-destaque";
}

/** Largura do fill da barra de cota (D-16) — aritmética de EXIBIÇÃO
 * (usadas/limite), não de estado: satura em 100%; limite 0 → 0 (sem cota). */
export function larguraFill(usadas: number, limite: number): number {
  if (limite <= 0) return 0;
  return Math.min(100, (usadas / limite) * 100);
}
