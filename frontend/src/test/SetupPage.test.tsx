// D-13: /setup em formulário único com o token do log. O módulo client é
// MOCKADO (vi.mock) — os cenários alimentam ApiError com o status/corpo do
// contrato real de web/auth.py (422 envelope D-08, 401, 403) e a 201 prova o
// "já entra logado" navegando para /. Proibição do plano asserida: storage
// permanece vazio.
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import SetupPage from "../pages/SetupPage";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));

vi.mock("../api/client", () => {
  // Espelho FIEL do contrato do client real: mesmos argumentos do construtor
  // e `.message` carregando o detail PT-BR do corpo (o que a página
  // renderiza no inline).
  class ApiError extends Error {
    status: number;
    body: unknown;
    retryAfterSeconds?: number;
    constructor(
      status: number,
      body: unknown,
      mensagem?: string,
      retryAfterSeconds?: number,
    ) {
      const detail =
        typeof body === "object" && body !== null && "detail" in body
          ? (body as { detail: unknown }).detail
          : undefined;
      super(mensagem ?? (typeof detail === "string" ? detail : `erro ${status}`));
      this.name = "ApiError";
      this.status = status;
      this.body = body;
      if (retryAfterSeconds !== undefined) {
        this.retryAfterSeconds = retryAfterSeconds;
      }
    }
  }
  return {
    ApiError,
    onUnauthorized: vi.fn(),
    parseErrosEnvelope: (body: unknown): { campo: string; problema: string }[] => {
      if (typeof body === "object" && body !== null && "erros" in body) {
        const erros = (body as { erros: unknown }).erros;
        if (Array.isArray(erros)) return erros as { campo: string; problema: string }[];
      }
      return [];
    },
    request: requestMock,
  };
});

function renderSetup() {
  return render(
    <MemoryRouter initialEntries={["/setup"]}>
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/" element={<p>PROBE-PAGINA-INICIAL</p>} />
        <Route path="/login" element={<p>PROBE-LOGIN</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function submeter(
  valores: Partial<{
    token: string;
    usuario: string;
    senha: string;
    confirmacao: string;
  }>,
): Promise<void> {
  const user = userEvent.setup();
  const token = valores.token ?? "token-do-log";
  const usuario = valores.usuario ?? "admin";
  const senha = valores.senha ?? "senha-super-segura-123";
  const confirmacao = valores.confirmacao ?? senha;
  await user.type(screen.getByLabelText("Token do log"), token);
  await user.type(screen.getByLabelText("Usuário"), usuario);
  await user.type(screen.getByLabelText("Senha"), senha);
  await user.type(screen.getByLabelText("Confirmação da senha"), confirmacao);
  await user.click(screen.getByRole("button", { name: "Criar administrador" }));
}

beforeEach(() => {
  requestMock.mockReset();
});

afterEach(() => {
  cleanup();
});

describe("SetupPage (D-13)", () => {
  it("exibe o hint do comando para ver o token no log", () => {
    renderSetup();
    expect(screen.getByText(/docker compose logs web/)).toBeInTheDocument();
  });

  it("senha != confirmação → erro inline PT-BR SEM chamar a API", async () => {
    renderSetup();
    await submeter({ confirmacao: "outra-senha-longa-456" });
    expect(
      await screen.findByText("as senhas não coincidem"),
    ).toBeInTheDocument();
    expect(requestMock).not.toHaveBeenCalled();
  });

  it("422 do envelope D-08 → o problema do campo renderiza inline junto ao campo (password)", async () => {
    requestMock.mockRejectedValueOnce(
      new ApiError(
        422,
        {
          erros: [
            { symbol: null, campo: "password", problema: "a senha deve ter pelo menos 12 caracteres" },
          ],
        },
        "a senha deve ter pelo menos 12 caracteres",
      ),
    );
    renderSetup();
    await submeter({ senha: "curta", confirmacao: "curta" });
    const campoSenha = screen.getByLabelText("Senha").closest("div");
    expect(campoSenha).not.toBeNull();
    expect(
      await within(campoSenha as HTMLElement).findByText(
        "a senha deve ter pelo menos 12 caracteres",
      ),
    ).toBeInTheDocument();
  });

  it("422 do envelope D-08 → problema de username fica sob o campo username", async () => {
    requestMock.mockRejectedValueOnce(
      new ApiError(
        422,
        {
          erros: [{ symbol: null, campo: "username", problema: "informe um nome de usuário" }],
        },
        "informe um nome de usuário",
      ),
    );
    renderSetup();
    await submeter({});
    const campoUsuario = screen.getByLabelText("Usuário").closest("div");
    expect(campoUsuario).not.toBeNull();
    expect(
      await within(campoUsuario as HTMLElement).findByText("informe um nome de usuário"),
    ).toBeInTheDocument();
  });

  it("201 → já entra logado (cookie setado pelo servidor) e navega para /", async () => {
    requestMock.mockResolvedValueOnce({ detail: "administrador criado" });
    renderSetup();
    await submeter({});
    expect(await screen.findByText("PROBE-PAGINA-INICIAL")).toBeInTheDocument();
    expect(requestMock).toHaveBeenCalledWith(
      "/api/setup",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          token: "token-do-log",
          username: "admin",
          password: "senha-super-segura-123",
        }),
      }),
    );
  });

  it("401 (token inválido) → 'token inválido' inline", async () => {
    requestMock.mockRejectedValueOnce(
      new ApiError(401, { detail: "token inválido" }, "token inválido"),
    );
    renderSetup();
    await submeter({});
    expect(await screen.findByText("token inválido")).toBeInTheDocument();
  });

  it("403 (admin já existe) → navega para /login", async () => {
    requestMock.mockRejectedValueOnce(
      new ApiError(
        403,
        { detail: "o administrador já existe — use o login" },
        "o administrador já existe — use o login",
      ),
    );
    renderSetup();
    await submeter({});
    expect(await screen.findByText("PROBE-LOGIN")).toBeInTheDocument();
  });

  it("os fluxos não tocam localStorage nem sessionStorage (proibição do plano)", async () => {
    requestMock.mockResolvedValueOnce({ detail: "administrador criado" });
    renderSetup();
    await submeter({});
    await screen.findByText("PROBE-PAGINA-INICIAL");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
