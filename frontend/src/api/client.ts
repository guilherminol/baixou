// Cliente HTTP do Baixou — sessão NO servidor, credenciais por cookie
// same-origin. Nenhum tipo de API é escrito à mão: os tipos vêm de
// api/generated/schema.ts (contrato congelado, npm run generate-types).

/** Erro da API: status HTTP + corpo parseado (o envelope D-08 vive em body). */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown, mensagem: string) {
    super(mensagem);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

let handler401: (() => void) | null = null;

/**
 * Registra O handler global de 401 — o App registra o redirect default para
 * /login; a página de alertas troca pelo modal de re-login no 05-03 (D-10).
 * `null` desregistra (limpeza no unmount).
 */
export function onUnauthorized(handler: (() => void) | null): void {
  handler401 = handler;
}

function detailDoCorpo(body: unknown): string | null {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return null;
}

/**
 * Requisição autenticada por cookie (credentials same-origin, mesma origem
 * em dev via proxy do Vite e em produção pelo próprio FastAPI — sem CORS).
 *
 * Content-Type application/json SÓ quando há corpo: o contrato congelado
 * (WR-01) declara DELETE /api/watch/... e POST /api/auth/logout sem
 * requestBody, e o guard de mutação do backend exige JSON apenas com corpo.
 * 204 → undefined; resposta !ok → ApiError com status + corpo, depois de
 * disparar o handler 401 registrado.
 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (response.status === 204) {
    return undefined as T;
  }
  const texto = await response.text();
  let body: unknown = null;
  if (texto !== "") {
    try {
      body = JSON.parse(texto);
    } catch {
      body = texto;
    }
  }
  if (!response.ok) {
    if (response.status === 401 && handler401 !== null) {
      handler401(); // ANTES do throw: o App já navega para /login
    }
    throw new ApiError(
      response.status,
      body,
      detailDoCorpo(body) ?? `erro ${response.status}`,
    );
  }
  return body as T;
}
