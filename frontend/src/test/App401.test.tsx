// Suíte de WIRING do 401 no App REAL (D-10, Gap 1 do 05-VERIFICATION): nenhum
// vi.mock de módulo — o client REAL roda com o fetch global stubado por
// rota/método, e as asserções separam modal vs redirect por
// window.location.pathname. É o repro do verificador como teste permanente:
// na carga fresca de / o modal abre, mas o round-trip / → /status → /
// re-registrava o redirect default do App sobre o handler da página (efeito
// [navigate]) e o 401 destruía as edições navegando para /login.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import type { WatchItem } from "../api/watch";

/** O objeto MÍNIMO que o client real consome de uma Response (ok, status,
 * headers.get, text) — nenhuma API do fetch além disso é tocada. */
interface RespostaStub {
  ok: boolean;
  status: number;
  headers: { get: (nome: string) => string | null };
  text: () => Promise<string>;
}

function respostaHttp(status: number, corpo: unknown): RespostaStub {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    text: async () => JSON.stringify(corpo),
  };
}

// Fábrica de WatchItem com os defaults do contrato (mesmo molde da suíte da
// página — o tipo vem do schema gerado).
function item(
  overrides: Partial<WatchItem> & Pick<WatchItem, "symbol" | "market" | "target_below">,
): WatchItem {
  return {
    paused: false,
    currency: "R$",
    price: null,
    price_at: null,
    change_pct: null,
    alerted: false,
    alerted_at: null,
    notify_state: null,
    fresh: "none",
    suspect: false,
    ...overrides,
  };
}

const T1 = "2026-10-29T17:00:00Z"; // cotação

const listaApp: WatchItem[] = [
  item({ symbol: "PETR4", market: "b3", target_below: 36, price: 38.42, price_at: T1, fresh: "ok" }),
  item({
    symbol: "VALE3",
    market: "b3",
    target_below: 55,
    price: 54.1,
    price_at: T1,
    fresh: "ok",
  }),
];

const statusApp = {
  ultima_rodada: "2026-10-29T20:30:00Z",
  proxima_rodada: "2026-10-29T20:45:00Z",
  cota: { usadas: 3120, limite: 15000 },
  fontes: [],
  canais: [{ nome: "telegram", configurado: true, ultimo_resultado: "ok" }],
};

/** Roteador do fetch stubado: cada path/método responde o que as páginas
 * leem. `status401ApartirDaSegunda` simula a sessão expirando na carga do
 * /status (a 1ª leitura de /api/status ainda é da página de alertas). */
function roteador(opcoes: { status401ApartirDaSegunda?: boolean } = {}) {
  let leiturasStatus = 0;
  return async (path: string, init?: RequestInit): Promise<RespostaStub> => {
    const metodo = init?.method ?? "GET";
    if (metodo === "GET" && path === "/api/setup") {
      return respostaHttp(200, { setup_pending: false });
    }
    if (metodo === "GET" && path === "/api/watch") {
      return respostaHttp(200, { itens: listaApp });
    }
    if (metodo === "GET" && path === "/api/status") {
      leiturasStatus += 1;
      if (opcoes.status401ApartirDaSegunda && leiturasStatus >= 2) {
        return respostaHttp(401, { detail: "não autenticado" });
      }
      return respostaHttp(200, statusApp);
    }
    if (metodo === "PATCH" && path === "/api/watch/PETR4/b3") {
      return respostaHttp(401, { detail: "não autenticado" });
    }
    return respostaHttp(404, { detail: "não encontrado" });
  };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App real — wiring do 401 (D-10 restaurativo)", () => {
  it("carga fresca de /: 401 no meio da edição abre o modal NO LUGAR — pathname / e a edição em memória preservada", async () => {
    vi.stubGlobal("fetch", roteador());
    render(<App />);
    await act(async () => {});
    fireEvent.change(screen.getByLabelText("Alvo de VALE3"), {
      target: { value: "60" },
    });
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Pausar PETR4" }));
    await act(async () => {});
    expect(screen.getByRole("dialog", { name: "Sessão expirada" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/"); // sem navegação
    expect(screen.getByLabelText("Alvo de VALE3")).toHaveValue(60); // edição preservada sob o modal
  });

  it("round-trip / → /status → /: 401 depois de voltar a navegar abre o modal NO LUGAR (pathname /, nunca /login)", async () => {
    vi.stubGlobal("fetch", roteador());
    render(<App />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("link", { name: "Status" }));
    await act(async () => {});
    expect(window.location.pathname).toBe("/status");
    fireEvent.click(screen.getByRole("link", { name: "Alertas" }));
    await act(async () => {});
    expect(window.location.pathname).toBe("/");
    fireEvent.change(screen.getByLabelText("Alvo de VALE3"), {
      target: { value: "60" },
    });
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Pausar PETR4" }));
    await act(async () => {});
    // O repro do verificador: o default re-registrado navega para /login e
    // destrói a edição — o fix guarda a posse restaurativa do handler.
    expect(screen.getByRole("dialog", { name: "Sessão expirada" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/"); // nunca /login
    expect(screen.getByLabelText("Alvo de VALE3")).toHaveValue(60);
  });

  it("expiração em /status após / → /status: o redirect default segue dono — pathname /login e nenhum dialog (reliance do 05-04)", async () => {
    vi.stubGlobal("fetch", roteador({ status401ApartirDaSegunda: true }));
    render(<App />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("link", { name: "Status" }));
    await act(async () => {});
    // O unmount da página de alertas DEVOLVEU o default (restore) — o 401 da
    // leitura do /status navega para /login em vez de abrir modal nenhum.
    expect(window.location.pathname).toBe("/login");
    expect(screen.queryByRole("dialog", { name: "Sessão expirada" })).not.toBeInTheDocument();
  });
});
