// D-11: login com 401 inline e bloqueio 429 em contagem regressiva pelo
// Retry-After. O módulo client é MOCKADO (vi.mock) — nenhum teste depende de
// rede; a ApiError é construída com status/corpo/retryAfterSeconds por
// cenário e a contagem usa fake timers. Proibição do plano asserida no fim:
// localStorage/sessionStorage permanecem vazios.
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import LoginPage from "../pages/LoginPage";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));

vi.mock("../api/client", () => {
  // Espelho do contrato do client real: status + corpo + Retry-After opcional.
  class ApiError extends Error {
    status: number;
    body: unknown;
    retryAfterSeconds?: number;
    constructor(status: number, body: unknown, mensagem?: string) {
      super(mensagem ?? `erro ${status}`);
      this.name = "ApiError";
      this.status = status;
      this.body = body;
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

function erroDeApi(status: number, body: unknown, retryAfterSeconds?: number): ApiError {
  const erro = new ApiError(status, body);
  if (retryAfterSeconds !== undefined) erro.retryAfterSeconds = retryAfterSeconds;
  return erro;
}

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<p>PROBE-PAGINA-INICIAL</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function submeterCom(
  usuario: string,
  senha: string,
  lembrar = false,
): Promise<void> {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await user.type(screen.getByLabelText("Usuário"), usuario);
  await user.type(screen.getByLabelText("Senha"), senha);
  if (lembrar) await user.click(screen.getByLabelText("lembrar de mim"));
  await user.click(screen.getByRole("button", { name: "Entrar" }));
}

beforeEach(() => {
  requestMock.mockReset();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("LoginPage (D-11)", () => {
  it("401 → 'usuário ou senha inválidos' inline (mesmo corpo do contrato de auth.py)", async () => {
    requestMock.mockRejectedValueOnce(
      erroDeApi(401, { detail: "usuário ou senha inválidos" }),
    );
    renderLogin();
    await submeterCom("admin", "senha-super-segura-123");
    expect(await screen.findByText("usuário ou senha inválidos")).toBeInTheDocument();
  });

  it("429 → 'muitas tentativas' inline e botão Entrar desabilitado com contagem mm:ss derivada do Retry-After, reabilitado ao zerar (fake timers)", async () => {
    vi.useFakeTimers();
    requestMock.mockRejectedValueOnce(
      erroDeApi(
        429,
        { detail: "muitas tentativas — aguarde e tente novamente" },
        30,
      ),
    );
    renderLogin();
    await submeterCom("admin", "senha-super-segura-123");

    expect(await screen.findByText(/muitas tentativas/)).toBeInTheDocument();
    const entrar = screen.getByRole("button", { name: "Entrar" });
    expect(entrar).toBeDisabled();
    expect(screen.getByText(/00:30/)).toBeInTheDocument();

    // Contagem regressiva visível (mm:ss) e nova submissão bloqueada…
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByText(/00:25/)).toBeInTheDocument();
    expect(entrar).toBeDisabled();

    // …zerada, o botão reabilita.
    await act(async () => {
      vi.advanceTimersByTime(25_000);
    });
    expect(entrar).toBeEnabled();
  });

  it("contagem deriva do Retry-After da resposta (não de valor fixo)", async () => {
    vi.useFakeTimers();
    requestMock.mockRejectedValueOnce(
      erroDeApi(
        429,
        { detail: "muitas tentativas — aguarde e tente novamente" },
        90,
      ),
    );
    renderLogin();
    await submeterCom("admin", "senha-super-segura-123");
    expect(await screen.findByText(/01:30/)).toBeInTheDocument();
  });

  it("checkbox 'lembrar de mim' marcada envia remember: true no corpo", async () => {
    requestMock.mockResolvedValueOnce({});
    renderLogin();
    await submeterCom("admin", "senha-super-segura-123", true);
    expect(requestMock).toHaveBeenCalledWith(
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          username: "admin",
          password: "senha-super-segura-123",
          remember: true,
        }),
      }),
    );
  });

  it("checkbox desmarcada envia remember: false", async () => {
    requestMock.mockResolvedValueOnce({});
    renderLogin();
    await submeterCom("admin", "senha-super-segura-123");
    const corpo = JSON.parse(
      (requestMock.mock.calls[0]?.[1] as { body: string }).body,
    ) as { remember: boolean };
    expect(corpo.remember).toBe(false);
  });

  it("os fluxos não tocam localStorage nem sessionStorage (proibição do plano)", async () => {
    requestMock.mockRejectedValueOnce(
      erroDeApi(401, { detail: "usuário ou senha inválidos" }),
    );
    renderLogin();
    await submeterCom("admin", "senha-super-segura-123");
    await screen.findByText("usuário ou senha inválidos");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
