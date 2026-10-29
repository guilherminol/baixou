// Suíte de componente da UI-01 — caminho de LEITURA (tracer do 05-03):
// agrupamento por mercado, estados em TEXTO além da cor (D-07), formatação
// por moeda da API, coluna Disparo (D-08), banner "Aviso do sistema" (D-17),
// meta-line e a sub-linha D-06 (360px). O módulo client é MOCKADO (vi.mock)
// — zero rede; a ApiError espelha o contrato do client real (mesmo molde das
// suítes de acesso do 05-02).
import { act, cleanup, render, screen, within } from "@testing-library/react";
// O CSS é lido do DISCO para a verificação estrutural da D-06: node:fs existe
// no runtime do vitest (Node), mas os tipos do projeto são DOM/vite — @types/node
// não está na lista fechada de pacotes aprovados, então declaramos só o que é
// usado. O `?raw` do Vite devolve string vazia para CSS (pipeline css do vitest).
// @ts-expect-error — módulo builtin do Node sem tipos no tsconfig (DOM/vite only)
import { readFileSync } from "node:fs";

declare const process: { cwd(): string };
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { WatchItem } from "../api/watch";
import type { StatusResponse } from "../api/status";
import AlertsPage from "../pages/AlertsPage";

const { requestMock, onUnauthorizedMock } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  onUnauthorizedMock: vi.fn(),
}));

vi.mock("../api/client", () => {
  // Espelho FIEL do contrato do client real (mesmo molde do LoginPage.test).
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
    onUnauthorized: onUnauthorizedMock,
    parseErrosEnvelope: (body: unknown): { campo: string; problema: string }[] => {
      if (typeof body !== "object" || body === null || !("erros" in body)) return [];
      const erros = (body as { erros: unknown }).erros;
      if (!Array.isArray(erros)) return [];
      return erros as { campo: string; problema: string }[];
    },
    request: requestMock,
  };
});

// Fábrica de WatchItem com os defaults do contrato (item sem cotação).
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

function statusPadrao(overrides: Partial<StatusResponse> = {}): StatusResponse {
  return {
    ultima_rodada: "2026-10-29T20:30:00Z",
    proxima_rodada: "2026-10-29T20:45:00Z",
    cota: { usadas: 3120, limite: 15000 },
    fontes: [
      {
        nome: "brapi",
        estado: "operacional",
        falhas_seguidas: 0,
        ultimo_sucesso: null,
        ultimo_erro: null,
      },
      {
        nome: "binance",
        estado: "operacional",
        falhas_seguidas: 0,
        ultimo_sucesso: null,
        ultimo_erro: null,
      },
    ],
    canais: [
      { nome: "telegram", configurado: true, ultimo_resultado: "ok" },
      { nome: "ntfy", configurado: true, ultimo_resultado: null },
    ],
    ...overrides,
  };
}

function carregarCom(itens: WatchItem[], estado: StatusResponse = statusPadrao()) {
  requestMock.mockImplementation(async (path: string) => {
    if (path === "/api/watch") return { itens };
    if (path === "/api/status") return estado;
    throw new Error(`chamada inesperada: ${path}`);
  });
}

function renderAlertas() {
  return render(
    <MemoryRouter>
      <AlertsPage />
    </MemoryRouter>,
  );
}

async function montarCom(itens: WatchItem[], estado: StatusResponse = statusPadrao()) {
  carregarCom(itens, estado);
  renderAlertas();
  // O flush dentro de act resolve as promises dos GETs mockados (as asserções
  // seguintes usam getBy* síncronos contra a renderização pós-carga).
  await act(async () => {});
}

// O CSS da página lido do disco (o cwd do vitest é a raiz do frontend; o fs
// do Node aceita barras normais no Windows).
const CSS_ALERTS = readFileSync(process.cwd() + "/src/styles/alerts.css", "utf8");

/** A <tr> principal do item (a célula do símbolo vive nela). */
function linhaDe(symbol: string): HTMLTableRowElement {
  return screen.getByText(symbol).closest("tr") as HTMLTableRowElement;
}

const T1 = "2026-10-29T17:00:00Z"; // cotação
const T2 = "2026-10-29T16:30:00Z"; // disparo

// Lista mista: cobre os 8 estados em TEXTO + as duas moedas + suspeita +
// falha de canal numa única renderização.
const listaMista: WatchItem[] = [
  item({ symbol: "PETR4", market: "b3", target_below: 36, price: 38.42, price_at: T1, fresh: "ok" }),
  item({
    symbol: "VALE3",
    market: "b3",
    target_below: 55,
    price: 54.1,
    price_at: T1,
    alerted: true,
    alerted_at: T2,
    fresh: "ok",
  }),
  item({ symbol: "ITUB4", market: "b3", target_below: 30, price: 30, price_at: T1, fresh: "ok" }),
  item({ symbol: "BBAS3", market: "b3", target_below: 27, price: 28.2, price_at: T1, fresh: "closed" }),
  item({ symbol: "BBSE3", market: "b3", target_below: 40, fresh: "none" }),
  item({ symbol: "SBSP3", market: "b3", target_below: 44, price: 45.1, price_at: T1, fresh: "stale" }),
  item({
    symbol: "TAEE11",
    market: "b3",
    target_below: 8,
    price: 8.9,
    price_at: T1,
    paused: true,
    fresh: "ok",
    notify_state: { telegram: "erro unavailable (HTTP 500)" },
  }),
  item({
    symbol: "WEGE3",
    market: "b3",
    target_below: 25,
    price: 30,
    price_at: T1,
    change_pct: 25,
    suspect: true,
    fresh: "ok",
  }),
  item({
    symbol: "BTCUSDT",
    market: "crypto",
    target_below: 320000,
    price: 351200,
    price_at: T1,
    currency: "US$",
    fresh: "ok",
  }),
];

afterEach(() => {
  cleanup();
  requestMock.mockReset();
  onUnauthorizedMock.mockReset();
});

describe("AlertsPage — lista real agrupada (tracer UI-01)", () => {
  it("renderiza duas seções com títulos serif do mockup e agrupa os itens por mercado", async () => {
    await montarCom(listaMista);
    expect(screen.getByRole("heading", { name: "Ações (B3)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cripto" })).toBeInTheDocument();
    const [tabelaB3, tabelaCripto] = screen.getAllByRole("table");
    expect(within(tabelaB3 as HTMLElement).getByText("PETR4")).toBeInTheDocument();
    expect(within(tabelaB3 as HTMLElement).queryByText("BTCUSDT")).not.toBeInTheDocument();
    expect(within(tabelaCripto as HTMLElement).getByText("BTCUSDT")).toBeInTheDocument();
  });

  it("cada estado renderiza o TEXTO esperado (D-07 — estado sempre além da cor)", async () => {
    await montarCom(listaMista);
    expect(linhaDe("TAEE11").textContent).toContain("pausado");
    expect(linhaDe("BBSE3").textContent).toContain("sem cotação");
    expect(linhaDe("BBAS3").textContent).toContain("fechado");
    expect(linhaDe("SBSP3").textContent).toContain("desatualizado");
    expect(linhaDe("ITUB4").textContent).toContain("no alvo");
    expect(linhaDe("VALE3").textContent).toContain("alertado");
    expect(linhaDe("PETR4").textContent).toContain("aguardando");
  });

  it("as classes acompanham o texto: accent no alvo/alertado, riscado no desatualizado", async () => {
    await montarCom(listaMista);
    expect(linhaDe("ITUB4").querySelector(".state-hit")).not.toBeNull();
    expect(linhaDe("VALE3").querySelector(".state-hit")).not.toBeNull();
    expect(linhaDe("SBSP3").querySelector(".state-stale")).not.toBeNull();
  });

  it("alertado vence 'no alvo' na precedência (preço <= alvo só marca 'no alvo' quando NÃO alertado)", async () => {
    // VALE3 tem price 54.1 <= alvo 55 E alerted=true → o estado é "alertado".
    await montarCom(listaMista);
    const estado = linhaDe("VALE3").querySelector(".tag-state");
    expect(estado?.textContent).toBe("alertado");
  });

  it("moeda por item: R$ na B3 e US$ na cripto (currency da API, não R$ fixo)", async () => {
    await montarCom(listaMista);
    expect(linhaDe("PETR4").textContent).toContain("R$");
    expect(linhaDe("BTCUSDT").textContent).toContain("US$");
    expect(linhaDe("BTCUSDT").textContent).toContain("351.200,00");
    expect(linhaDe("BBSE3").textContent).toContain("—"); // sem preço → "—"
  });

  it("distância usa a fórmula do mockup ((alvo - preço) / preço × 100) e 'no alvo' na borda price <= target", async () => {
    await montarCom(listaMista);
    expect(linhaDe("PETR4").textContent).toContain("−6,3"); // ((36-38.42)/38.42)*100
    expect(linhaDe("ITUB4").textContent).toContain("no alvo"); // 30 <= 30 (borda inclusiva)
  });

  it("coluna Disparo (D-08): hora do alerted_at e '—' quando nunca disparou", async () => {
    await montarCom(listaMista);
    expect(linhaDe("VALE3").textContent).toMatch(/\d{2}:\d{2}/); // alerted_at 16:30 UTC → HH:mm local
    expect(linhaDe("PETR4").textContent).toContain("—"); // alerted_at null
    const cabecalho = screen.getAllByRole("table")[0] as HTMLElement;
    expect(within(cabecalho).getByText("Disparo")).toBeInTheDocument();
  });

  it("suspeita vira linha curta extra no item (D-07), com a variação formatada", async () => {
    await montarCom(listaMista);
    // O normalizador do Testing Library colapsa o NBSP do formatPercent em
    // espaço — \s cobre os dois na asserção.
    expect(
      screen.getByText(/variação de \+25,0\s?% hoje — confira evento corporativo/),
    ).toBeInTheDocument();
    expect(linhaDe("WEGE3").querySelector(".estado-extra")).not.toBeNull();
  });

  it("falha de canal (notify_state != ok) vira '{canal}: erro' sob o estado", async () => {
    await montarCom(listaMista);
    expect(screen.getByText("telegram: erro")).toBeInTheDocument();
  });

  it("empty-state por seção com a copy fixa do mockup", async () => {
    await montarCom([]);
    expect(
      screen.getByText("Nenhuma ação monitorada. Use o formulário abaixo."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Nenhuma criptomoeda monitorada. Use o formulário abaixo."),
    ).toBeInTheDocument();
  });

  it("meta-line do cabeçalho deriva do /api/status com números pt-BR (D-03)", async () => {
    await montarCom(listaMista);
    expect(screen.getByText(/cota 3\.120\/15\.000/)).toBeInTheDocument();
    expect(screen.getByText(/próxima em \d+ min/)).toBeInTheDocument();
  });

  it("meta-line mostra 'Aguardando dados...' durante a primeira carga", () => {
    carregarCom(listaMista);
    renderAlertas();
    expect(screen.getByText("Aguardando dados...")).toBeInTheDocument();
  });

  it("banner D-17 cita o NOME da fonte em falha_prolongada (role=alert)", async () => {
    await montarCom(
      listaMista,
      statusPadrao({
        fontes: [
          {
            nome: "brapi",
            estado: "falha_prolongada",
            falhas_seguidas: 3,
            ultimo_sucesso: null,
            ultimo_erro: "rate_limit",
          },
          statusPadrao().fontes[1] as StatusResponse["fontes"][number],
        ],
      }),
    );
    const banner = screen.getByRole("alert");
    expect(banner.textContent).toContain("Aviso do sistema:");
    expect(banner.textContent).toContain("fonte brapi em falha prolongada");
  });

  it("banner D-17 nas outras duas condições fechadas: cota esgotada e nenhum canal", async () => {
    await montarCom(listaMista, statusPadrao({ cota: { usadas: 15000, limite: 15000 } }));
    expect(screen.getByRole("alert").textContent).toContain("cota mensal da brapi esgotada");
    cleanup();
    await montarCom(
      listaMista,
      statusPadrao({
        canais: [
          { nome: "telegram", configurado: false, ultimo_resultado: null },
          { nome: "ntfy", configurado: false, ultimo_resultado: null },
        ],
      }),
    );
    expect(screen.getByRole("alert").textContent).toContain("nenhum canal configurado");
  });

  it("sem problema no /api/status o banner não existe no DOM", async () => {
    await montarCom(listaMista);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sub-linha D-06 está no DOM com distância + atualização + disparo dobrados", async () => {
    await montarCom(listaMista);
    const sub = linhaDe("PETR4").nextElementSibling;
    expect(sub?.className).toBe("sub-linha-row");
    expect(sub?.textContent).toContain("distância −6,3");
    expect(sub?.textContent).toContain("atualização");
    expect(sub?.textContent).toContain("disparo —");
  });

  it("alerts.css: media query <= 600px esconde as 3 colunas e revela a sub-linha (verificação estrutural)", () => {
    const css = CSS_ALERTS;
    const media = css.slice(css.indexOf("@media (max-width: 600px)"));
    expect(media).not.toBe("");
    const bloco = media.slice(0, media.indexOf("}", media.indexOf("display")) + 1);
    expect(bloco).toContain(".col-dobra");
    expect(bloco).toContain("display: none");
    expect(css).toContain(".sub-linha-row {\n  display: none;");
    expect(media).toContain(".sub-linha-row");
    expect(media).toContain("display: table-row");
  });

  it("alerts.css: nenhuma largura fixa maior que 360px em elementos de linha", () => {
    const css = CSS_ALERTS;
    // Propriedade: nenhuma largura fixa (width direto) de elemento de linha
    // passa de 360px — hoje o arquivo não declara nenhuma; o guarda vale para
    // o CSS que crescer aqui (max-width de TELA na media query não conta).
    const larguras = [
      ...css.matchAll(/(?<![-a-z])width:\s*(\d+(?:\.\d+)?)px/g),
    ].map((m) => Number(m[1]));
    expect(larguras.every((largura) => largura <= 360)).toBe(true);
  });

  it("a coluna Estado consome fresh/paused da API — nenhum item ganha estado no cliente sem o campo", async () => {
    // Um item fresh "ok" com preço null (impossível no servidor, mas prova que
    // o cliente não compara horários): cai em "aguardando", nunca em
    // "desatualizado" — nenhuma derivação por idade no navegador.
    await montarCom([item({ symbol: "EDGE1", market: "b3", target_below: 10, fresh: "ok" })]);
    expect(linhaDe("EDGE1").textContent).toContain("aguardando");
    expect(linhaDe("EDGE1").textContent).not.toContain("desatualizado");
  });

  it("ApiError segue exportada pelo mock (contrato do molde das suítes)", () => {
    expect(new ApiError(422, { erros: [] }, "x").status).toBe(422);
  });
});
