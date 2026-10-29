// Consumo da lista de alertas (GET/PUT /api/watch, PATCH/DELETE item) — os
// tipos vêm de api/generated/schema.ts (contrato congelado da Fase 5); nenhum
// tipo de API escrito à mão.
import type { components } from "./generated/schema";
import { request } from "./client";

/** Item da lista com cotação, moeda e estado pronto (D-02) — saída do GET/PATCH. */
export type WatchItem = components["schemas"]["WatchItem"];
export type Market = WatchItem["market"];

/** Envelope do GET/PUT: a lista completa, ordenada (market, symbol). */
export type WatchListResponse = components["schemas"]["WatchListResponse"];

/** Item do lote do PUT: SÓ os três campos que o servidor consome (D-06) —
 * campos extras são ignorados pelo pydantic, mas o cliente envia só estes. */
export type ItemDoLote = Pick<WatchItem, "symbol" | "market" | "target_below">;

/** Itens completos e ordenados (market, symbol) — a leitura da página. */
export function listarItens(): Promise<WatchListResponse> {
  return request<WatchListResponse>("/api/watch");
}

/**
 * PUT em lote TUDO-OU-NADA (D-06/D-07): cria/edita os itens do corpo e NUNCA
 * remove — remoção persiste exclusivamente pelo DELETE item a item. A
 * resposta devolve a lista completa atualizada (mesma forma do GET) e é ELA
 * que a página renderiza.
 */
export function salvarLote(itens: ItemDoLote[]): Promise<WatchListResponse> {
  // O tipo GERADO marca os campos com default do pydantic como obrigatórios;
  // na ENTRADA o contrato aceita só symbol/market/target_below (schemas.py:
  // "Na ENTRADA só symbol/market/target_below interessam") — o recorte é
  // seguro e o corpo segue o envelope PutWatch exato.
  const corpo = { itens } as components["schemas"]["PutWatch"];
  return request<WatchListResponse>("/api/watch", {
    method: "PUT",
    body: JSON.stringify(corpo),
  });
}

/** Pausa/retoma o item (D-09, PATCH imediato fora do lote) — a resposta é o
 * WatchItem completo (fresh/currency preenchidos) que substitui a linha. */
export function patchPause(
  symbol: string,
  market: Market,
  paused: boolean,
): Promise<WatchItem> {
  return request<WatchItem>(
    `/api/watch/${encodeURIComponent(symbol)}/${encodeURIComponent(market)}`,
    { method: "PATCH", body: JSON.stringify({ paused }) },
  );
}

/**
 * Remove o item — SEM corpo e SEM Content-Type: o contrato congelado
 * (WR-01 opção (a)) declara o DELETE sem requestBody e o guard de mutação
 * só exige JSON quando há corpo.
 */
export function removerItem(symbol: string, market: Market): Promise<void> {
  return request<void>(
    `/api/watch/${encodeURIComponent(symbol)}/${encodeURIComponent(market)}`,
    { method: "DELETE" },
  );
}
