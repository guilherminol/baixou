// Consumo do GET /api/status (D-03) — estado operacional derivado no
// servidor. O tipo é GERADO do contrato congelado pelo caminho de operations
// do openapi-typescript; nenhum tipo de API escrito à mão.
import type { operations } from "./generated/schema";
import { request } from "./client";

export type StatusResponse =
  operations["status_geral_api_status_get"]["responses"][200]["content"]["application/json"];

/** Rodadas, cota, fontes e canais — alimenta a meta-line e a tela /status. */
export function buscarStatus(): Promise<StatusResponse> {
  return request<StatusResponse>("/api/status");
}
