// A ÚNICA função de login do app (D-10): a LoginPage e o ReloginModal
// compartilham este caminho — nenhuma das duas telas fala direto com o
// client para autenticar. O tipo é GERADO do contrato congelado.
import type { operations } from "./generated/schema";
import { request } from "./client";

type RespostaLogin =
  operations["login_api_auth_login_post"]["responses"][200]["content"]["application/json"];

/** POST /api/auth/login com corpo {username, password, remember} — a sessão
 * vive no cookie do servidor; "lembrar de mim" estende a validade (D-10/D-11
 * da Fase 4). 401 e 429 (com Retry-After na ApiError) propagam tipados. */
export function entrar(
  username: string,
  password: string,
  remember: boolean,
): Promise<RespostaLogin> {
  return request<RespostaLogin>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password, remember }),
  });
}
