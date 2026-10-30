// Suíte de componente da UI-01 — caminho de LEITURA (tracer do 05-03):
// agrupamento por mercado, estados em TEXTO além da cor (D-07), formatação
// por moeda da API, coluna Disparo (D-08), banner "Aviso do sistema" (D-17),
// meta-line e a sub-linha D-06 (360px). O módulo client é MOCKADO (vi.mock)
// — zero rede; a ApiError espelha o contrato do client real (mesmo molde das
// suítes de acesso do 05-02).
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
// O CSS é lido do DISCO para a verificação estrutural da D-06: node:fs existe
// no runtime do vitest (Node), mas os tipos do projeto são DOM/vite — @types/node
// não está na lista fechada de pacotes aprovados, então declaramos só o que é
// usado. O `?raw` do Vite devolve string vazia para CSS (pipeline css do vitest).
// @ts-expect-error — módulo builtin do Node sem tipos no tsconfig (DOM/vite only)
import { readFileSync } from "node:fs";

declare const process: { cwd(): string };
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { WatchItem } from "../api/watch";
import type { StatusResponse } from "../api/status";
import AlertsPage from "../pages/AlertsPage";

const { requestMock, pushUnauthorizedMock, holder401 } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  pushUnauthorizedMock: vi.fn(),
  // Guarda o ÚLTIMO handler registrado — o mock de request dispara-o ANTES de
  // lançar um 401, replicando o contrato do client real (handler antes do throw).
  holder401: { handler: null as null | (() => void) },
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
    // RED→GREEN: a página fala o protocolo antigo até o feat trocar os
    // importadores; o pushUnauthorized restaurativo já vive no mock (o teste
    // de registro nasce vermelho contra a árvore intocada).
    onUnauthorized: (handler: (() => void) | null) => {
      holder401.handler = handler;
    },
    // Protocolo restaurativo (D-10): o registro EMPILHA e devolve o restore,
    // que limpa o holder só se o topo é o próprio handler — o unmount da
    // página devolve o dono anterior em vez de esvaziar o slot.
    pushUnauthorized: (handler: () => void) => {
      holder401.handler = handler;
      pushUnauthorizedMock(handler);
      return () => {
        if (holder401.handler === handler) holder401.handler = null;
      };
    },
    parseErrosEnvelope: (body: unknown): { campo: string; problema: string }[] => {
      if (typeof body !== "object" || body === null || !("erros" in body)) return [];
      const erros = (body as { erros: unknown }).erros;
      if (!Array.isArray(erros)) return [];
      return erros as { campo: string; problema: string }[];
    },
    request: requestMock,
  };
});

/** Reproduz o client real num 401: o handler registrado é disparado ANTES do
 * throw (client.ts chama handler401() antes de lançar a ApiError). */
function disparar401(): never {
  holder401.handler?.();
  throw new ApiError(401, { detail: "não autenticado" }, "não autenticado");
}

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
  vi.useRealTimers();
  requestMock.mockReset();
  pushUnauthorizedMock.mockReset();
  holder401.handler = null;
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

  it("meta-line mostra 'Aguardando dados...' durante a primeira carga", async () => {
    carregarCom(listaMista);
    renderAlertas();
    expect(screen.getByText("Aguardando dados...")).toBeInTheDocument();
    // A carga termina DENTRO do act (sem warning de update pós-teste).
    await act(async () => {});
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

// ─── Mutações (Task 2): adicionar, editar alvo, remover com Desfazer de 5 s,
// Salvar (DELETE pendentes → PUT lote) e Pausar/Retomar ─────────────────────

type Iniciar = { method?: string; body?: string };

/** Handler composto: responde os GETs com a lista/estado e as rotas extras
 * na forma "MÉTODO caminho"; rota extra pode devolver valor OU lançar. */
function handlerDe(
  itens: WatchItem[],
  extras: Record<string, (init?: Iniciar) => unknown> = {},
  estado: StatusResponse = statusPadrao(),
) {
  return async (path: string, init?: Iniciar) => {
    const metodo = init?.method ?? "GET";
    const rota = `${metodo} ${path}`;
    if (rota in extras) return extras[rota](init);
    if (metodo === "GET" && path === "/api/watch") return { itens };
    if (metodo === "GET" && path === "/api/status") return estado;
    throw new Error(`chamada inesperada: ${rota}`);
  };
}

function chamadasDeMutacao(): Array<[string, Iniciar | undefined]> {
  return requestMock.mock.calls.filter(
    ([, init]) => (init as Iniciar | undefined)?.method !== undefined,
  ) as Array<[string, Iniciar | undefined]>;
}

function nomeDoBotao(rotulo: string) {
  return screen.getByRole("button", { name: rotulo });
}

/** Renderiza com fake timers completos (a janela de Desfazer de 5 s é
 * setTimeout POR item) e interações via fireEvent — o user-event depende de
 * setTimeout real e não serve aqui (lição do 05-02, agora no inverso). */
async function montarComTimersFake(itens: WatchItem[], extras: Record<string, (init?: Iniciar) => unknown> = {}) {
  vi.useFakeTimers();
  requestMock.mockImplementation(handlerDe(itens, extras));
  renderAlertas();
  await act(async () => {});
}

describe("AlertsPage — mutações da lista (UI-01)", () => {
  it("adicionar válido: símbolo trimado/maiúsculo entra na seção do mercado escolhido e a barra vira 'Alterações não salvas'", async () => {
    await montarComTimersFake(listaMista);
    fireEvent.change(screen.getByLabelText("Novo ticker"), { target: { value: " itub4 " } });
    fireEvent.change(screen.getByLabelText("Mercado"), { target: { value: "crypto" } });
    fireEvent.change(screen.getByLabelText("Novo alvo"), { target: { value: "12.5" } });
    fireEvent.click(nomeDoBotao("Adicionar"));
    await act(async () => {});
    const tabelaCripto = screen.getAllByRole("table")[1] as HTMLElement;
    expect(within(tabelaCripto).getByText("ITUB4")).toBeInTheDocument();
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
    expect(nomeDoBotao("Salvar")).toBeEnabled();
  });

  it("adicionar com símbolo duplicado → erro inline 'Este ticker já está na lista.' (nunca alert()), sem adicionar", async () => {
    await montarComTimersFake(listaMista);
    fireEvent.change(screen.getByLabelText("Novo ticker"), { target: { value: "petr4" } });
    fireEvent.change(screen.getByLabelText("Mercado"), { target: { value: "b3" } });
    fireEvent.change(screen.getByLabelText("Novo alvo"), { target: { value: "30" } });
    fireEvent.click(nomeDoBotao("Adicionar"));
    await act(async () => {});
    expect(screen.getByText("Este ticker já está na lista.")).toBeInTheDocument();
    expect(screen.getAllByText("PETR4").length).toBe(1);
    expect(screen.queryByText("Alterações não salvas")).not.toBeInTheDocument();
  });

  it("adicionar com alvo inválido (zero/negativo/vazio) não adiciona nem marca dirty", async () => {
    await montarComTimersFake(listaMista);
    for (const alvo of ["0", "-5", ""]) {
      fireEvent.change(screen.getByLabelText("Novo ticker"), { target: { value: "BBDC4" } });
      fireEvent.change(screen.getByLabelText("Novo alvo"), { target: { value: alvo } });
      fireEvent.click(nomeDoBotao("Adicionar"));
      await act(async () => {});
    }
    expect(screen.queryByText("BBDC4")).not.toBeInTheDocument();
    expect(screen.getByText("Tudo salvo")).toBeInTheDocument();
  });

  it("editar alvo marca dirty, recalcula distância/estado da linha e preserva o valor no input", async () => {
    await montarComTimersFake(listaMista);
    fireEvent.change(screen.getByLabelText("Alvo de PETR4"), { target: { value: "40" } });
    await act(async () => {});
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
    expect(linhaDe("PETR4").textContent).toContain("no alvo"); // 38.42 <= 40
    expect(screen.getByLabelText("Alvo de PETR4")).toHaveValue(40);
  });

  it("editar alvo com valor inválido (NaN/negativo) NÃO marca dirty", async () => {
    await montarComTimersFake(listaMista);
    fireEvent.change(screen.getByLabelText("Alvo de PETR4"), { target: { value: "-3" } });
    await act(async () => {});
    expect(screen.getByText("Tudo salvo")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Alvo de PETR4"), { target: { value: "abc" } });
    await act(async () => {});
    expect(screen.getByText("Tudo salvo")).toBeInTheDocument();
  });

  it("remover: linha vira 'PETR4 removido.' + Desfazer; em 4999 ms ainda está lá e Desfazer restaura com ZERO chamadas de api", async () => {
    await montarComTimersFake(listaMista);
    fireEvent.click(nomeDoBotao("Remover PETR4"));
    await act(async () => {});
    expect(screen.getByText("PETR4 removido.")).toBeInTheDocument();
    await act(async () => {
      vi.advanceTimersByTime(4_999);
    });
    expect(screen.getByText("PETR4 removido.")).toBeInTheDocument();
    fireEvent.click(nomeDoBotao("Desfazer"));
    await act(async () => {});
    expect(screen.queryByText("PETR4 removido.")).not.toBeInTheDocument();
    expect(linhaDe("PETR4").textContent).toContain("aguardando");
    expect(chamadasDeMutacao()).toHaveLength(0); // nenhuma rede no undo
  });

  it("remover sem desfazer: aos 5 s a linha sai da lista local e fica pendente de DELETE", async () => {
    await montarComTimersFake(listaMista);
    fireEvent.click(nomeDoBotao("Remover PETR4"));
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.queryByText("PETR4 removido.")).not.toBeInTheDocument();
    expect(screen.queryByText("PETR4")).not.toBeInTheDocument();
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
    expect(chamadasDeMutacao()).toHaveLength(0); // o DELETE só sai no Salvar
  });

  it("salvar: flush de undo + DELETE de cada pendência e SÓ ENTÃO UM PUT cujo corpo NÃO contém os removidos (PUT nunca remove)", async () => {
    const respostaPut = { itens: listaMista.filter((i) => i.symbol !== "PETR4") };
    await montarComTimersFake(listaMista, {
      "DELETE /api/watch/PETR4/b3": () => undefined,
      "PUT /api/watch": () => respostaPut,
    });
    fireEvent.click(nomeDoBotao("Remover PETR4"));
    await act(async () => {
      vi.advanceTimersByTime(5_000); // pendência confirmada
    });
    fireEvent.click(nomeDoBotao("Salvar"));
    await act(async () => {});
    const mutacoes = chamadasDeMutacao();
    expect(mutacoes.map(([path]) => path)).toEqual([
      "/api/watch/PETR4/b3", // DELETE primeiro
      "/api/watch", // PUT depois
    ]);
    expect(mutacoes[0]?.[1]?.method).toBe("DELETE");
    expect(mutacoes[0]?.[1]?.body).toBeUndefined(); // DELETE sem corpo (WR-01)
    expect(mutacoes[1]?.[1]?.method).toBe("PUT");
    const corpo = JSON.parse(mutacoes[1]?.[1]?.body ?? "{}") as {
      itens: Array<{ symbol: string; market: string; target_below: number }>;
    };
    expect(corpo.itens.some((i) => i.symbol === "PETR4")).toBe(false);
    expect(corpo.itens.find((i) => i.symbol === "VALE3")).toEqual({
      symbol: "VALE3",
      market: "b3",
      target_below: 55,
    });
    // A resposta do PUT É a nova lista renderizada.
    expect(screen.queryByText("PETR4")).not.toBeInTheDocument();
    expect(screen.getByText("Alterações salvas com sucesso")).toBeInTheDocument();
    expect(nomeDoBotao("Salvar")).toBeDisabled();
  });

  it("salvar com remoção ainda na janela de undo flusha o timer: removida vai para o DELETE, não para o PUT", async () => {
    const respostaPut = { itens: listaMista.filter((i) => i.symbol !== "PETR4") };
    await montarComTimersFake(listaMista, {
      "DELETE /api/watch/PETR4/b3": () => undefined,
      "PUT /api/watch": (init) => {
        const corpo = JSON.parse(init?.body ?? "{}") as {
          itens: Array<{ symbol: string; target_below: number }>;
        };
        // O flush acontece ANTES do payload: VALE3 leva o alvo editado, e
        // PETR4 (na janela de undo no clique) não entra.
        expect(corpo.itens.find((i) => i.symbol === "VALE3")?.target_below).toBe(60);
        expect(corpo.itens.some((i) => i.symbol === "PETR4")).toBe(false);
        return respostaPut;
      },
    });
    fireEvent.change(screen.getByLabelText("Alvo de VALE3"), { target: { value: "60" } });
    fireEvent.click(nomeDoBotao("Remover PETR4"));
    fireEvent.click(nomeDoBotao("Salvar")); // dentro da janela de 5 s
    await act(async () => {});
    const mutacoes = chamadasDeMutacao();
    expect(mutacoes.map(([path, init]) => `${init?.method} ${path}`)).toEqual([
      "DELETE /api/watch/PETR4/b3",
      "PUT /api/watch",
    ]);
    expect(screen.queryByText("PETR4")).not.toBeInTheDocument();
  });

  it("falha no DELETE interrompe ANTES do PUT com o estado dirty preservado (nenhum commit parcial)", async () => {
    await montarComTimersFake(listaMista, {
      "DELETE /api/watch/PETR4/b3": () => {
        throw new ApiError(500, { detail: "item não encontrado" }, "item não encontrado");
      },
    });
    fireEvent.click(nomeDoBotao("Remover PETR4"));
    await act(async () => {
      vi.advanceTimersByTime(5_000);
    });
    fireEvent.click(nomeDoBotao("Salvar"));
    await act(async () => {});
    const metodos = chamadasDeMutacao().map(([, init]) => init?.method);
    expect(metodos).toEqual(["DELETE"]); // o PUT não aconteceu
    // A barra carrega a mensagem de erro na classe dirty; o estado permanece
    // dirty (Salvar segue habilitado — nenhum commit parcial no servidor).
    expect(screen.getByText("item não encontrado")).toHaveClass("dirty");
    expect(nomeDoBotao("Salvar")).toBeEnabled();
  });

  it("PUT 422 → envelope D-08 renderiza o problema junto ao item nomeado e a barra segue 'Alterações não salvas'", async () => {
    await montarComTimersFake(listaMista, {
      "PUT /api/watch": () => {
        throw new ApiError(
          422,
          {
            erros: [
              {
                symbol: "VALE3",
                campo: "target_below",
                problema: "o alvo deve ser maior que zero",
              },
            ],
          },
          "erro de validação",
        );
      },
    });
    fireEvent.change(screen.getByLabelText("Alvo de VALE3"), { target: { value: "0" } });
    await act(async () => {});
    fireEvent.click(nomeDoBotao("Salvar"));
    await act(async () => {});
    const erro = screen.getByTestId("erro-item-VALE3");
    expect(erro.textContent).toBe("target_below: o alvo deve ser maior que zero");
    expect(erro.closest("tr")?.textContent).toContain("VALE3");
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
  });

  it("pausar chama PATCH imediato e substitui a linha pela resposta (estado 'pausado' sem recarregar)", async () => {
    const pausado = item({
      symbol: "PETR4",
      market: "b3",
      target_below: 36,
      price: 38.42,
      price_at: T1,
      fresh: "ok",
      paused: true,
    });
    await montarComTimersFake(listaMista, {
      "PATCH /api/watch/PETR4/b3": (init) => {
        expect(init?.body).toBe(JSON.stringify({ paused: true }));
        return pausado;
      },
    });
    fireEvent.click(nomeDoBotao("Pausar PETR4"));
    await act(async () => {});
    expect(linhaDe("PETR4").textContent).toContain("pausado");
    expect(screen.getByText("Tudo salvo")).toBeInTheDocument(); // pausa não é pendência do lote
    expect(screen.getByRole("button", { name: "Retomar PETR4" })).toBeInTheDocument();
  });

  it("retomar devolve o item à regra do D-07 (estado pela cotação, não mais 'pausado')", async () => {
    const taee = listaMista.find((i) => i.symbol === "TAEE11") as WatchItem;
    const retomado = { ...taee, paused: false };
    await montarComTimersFake(listaMista, {
      "PATCH /api/watch/TAEE11/b3": (init) => {
        expect(init?.body).toBe(JSON.stringify({ paused: false }));
        return retomado;
      },
    });
    expect(linhaDe("TAEE11").textContent).toContain("pausado");
    fireEvent.click(nomeDoBotao("Retomar TAEE11"));
    await act(async () => {});
    expect(linhaDe("TAEE11").textContent).toContain("aguardando"); // 8.9 > 8
  });

  it("salvar sem dirty não dispara chamada nenhuma (botão desabilitado)", async () => {
    await montarComTimersFake(listaMista);
    expect(nomeDoBotao("Salvar")).toBeDisabled();
    fireEvent.click(nomeDoBotao("Salvar"));
    await act(async () => {});
    expect(chamadasDeMutacao()).toHaveLength(0);
  });

  it("adicionar + salvar: o novo item vai no PUT e volta da resposta com a cotação vazia do ciclo novo", async () => {
    const criado = item({ symbol: "BBDC4", market: "b3", target_below: 30, fresh: "none" });
    const respostaPut = { itens: [...listaMista, criado] };
    await montarComTimersFake(listaMista, {
      "PUT /api/watch": (init) => {
        const corpo = JSON.parse(init?.body ?? "{}") as { itens: unknown[] };
        expect(corpo.itens).toContainEqual({
          symbol: "BBDC4",
          market: "b3",
          target_below: 30,
        });
        return respostaPut;
      },
    });
    fireEvent.change(screen.getByLabelText("Novo ticker"), { target: { value: "bbdc4" } });
    fireEvent.change(screen.getByLabelText("Novo alvo"), { target: { value: "30" } });
    fireEvent.click(nomeDoBotao("Adicionar"));
    await act(async () => {});
    fireEvent.click(nomeDoBotao("Salvar"));
    await act(async () => {});
    expect(screen.getByText("Alterações salvas com sucesso")).toBeInTheDocument();
    expect(linhaDe("BBDC4").textContent).toContain("—"); // sem cotação ainda
  });
});

// ─── Resiliência da edição (Task 3): polling de 60 s sem sobrescrever
// (merge determinístico, T-05-10) e 401 → modal de re-login no lugar (D-10) ──

function contagemGet(caminho: string): number {
  return requestMock.mock.calls.filter(
    ([path, init]) => path === caminho && ((init as Iniciar | undefined)?.method ?? "GET") === "GET",
  ).length;
}

function renderAlertasComRotas() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<AlertsPage />} />
        <Route path="/login" element={<p>PROBE-PAGINA-LOGIN</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AlertsPage — polling de 60 s e re-login no lugar (D-10)", () => {
  it("60 s após a carga refaz /api/watch e /api/status e atualiza os campos somente-leitura", async () => {
    let carga = 0;
    const atualizada = listaMista.map((i) =>
      i.symbol === "PETR4" ? { ...i, price: 40.0, change_pct: 5 } : i,
    );
    vi.useFakeTimers();
    requestMock.mockImplementation(async (path, init) => {
      const metodo = init?.method ?? "GET";
      if (metodo === "GET" && path === "/api/watch") {
        carga += 1;
        return { itens: carga === 1 ? listaMista : atualizada };
      }
      if (metodo === "GET" && path === "/api/status") return statusPadrao();
      throw new Error(`chamada inesperada: ${metodo} ${path}`);
    });
    renderAlertas();
    await act(async () => {});
    expect(linhaDe("PETR4").textContent).toContain("38,42");
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(linhaDe("PETR4").textContent).toContain("40,00"); // preço novo no mesmo tick
    expect(contagemGet("/api/watch")).toBe(2);
    expect(contagemGet("/api/status")).toBe(2);
  });

  it("merge (T-05-10): alvo editado NÃO salvo é preservado enquanto o preço atualiza no MESMO tick; item sem pendência substitui integralmente", async () => {
    const segundoTick = listaMista.map((i) => {
      if (i.symbol === "PETR4") return { ...i, price: 39.0, target_below: 36 };
      if (i.symbol === "VALE3") return { ...i, price: 60.0 };
      return i;
    });
    vi.useFakeTimers();
    let carga = 0;
    requestMock.mockImplementation(async (path, init) => {
      const metodo = init?.method ?? "GET";
      if (metodo === "GET" && path === "/api/watch") {
        carga += 1;
        return { itens: carga === 1 ? listaMista : segundoTick };
      }
      if (metodo === "GET" && path === "/api/status") return statusPadrao();
      throw new Error(`chamada inesperada: ${metodo} ${path}`);
    });
    renderAlertas();
    await act(async () => {});
    fireEvent.change(screen.getByLabelText("Alvo de PETR4"), { target: { value: "40" } });
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    // Alvo editado fica LOCAL; preço (somente-leitura) atualiza no mesmo tick.
    expect(screen.getByLabelText("Alvo de PETR4")).toHaveValue(40);
    expect(linhaDe("PETR4").textContent).toContain("39,00");
    expect(linhaDe("PETR4").textContent).toContain("no alvo"); // 39 <= 40 (alvo local)
    // Item sem pendência substitui integralmente (preço novo do servidor).
    expect(linhaDe("VALE3").textContent).toContain("60,00");
  });

  it("input de alvo EM FOCO não é substituído pelo servidor no refresh; após o blur, o valor do servidor entra no tick seguinte", async () => {
    const tickFoco = listaMista.map((i) =>
      i.symbol === "PETR4" ? { ...i, target_below: 99 } : i,
    );
    const tickBlur = listaMista.map((i) =>
      i.symbol === "PETR4" ? { ...i, target_below: 88 } : i,
    );
    vi.useFakeTimers();
    let carga = 0;
    requestMock.mockImplementation(async (path, init) => {
      const metodo = init?.method ?? "GET";
      if (metodo === "GET" && path === "/api/watch") {
        carga += 1;
        return { itens: carga === 1 ? listaMista : carga === 2 ? tickFoco : tickBlur };
      }
      if (metodo === "GET" && path === "/api/status") return statusPadrao();
      throw new Error(`chamada inesperada: ${metodo} ${path}`);
    });
    renderAlertas();
    await act(async () => {});
    fireEvent.focus(screen.getByLabelText("Alvo de PETR4"));
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByLabelText("Alvo de PETR4")).toHaveValue(36); // foco protege
    fireEvent.blur(screen.getByLabelText("Alvo de PETR4"));
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByLabelText("Alvo de PETR4")).toHaveValue(88); // sem foco, servidor vence
  });

  it("pendência de remoção não ressuscita no refresh e item novo do servidor aparece", async () => {
    const comNovo = [
      ...listaMista.filter((i) => i.symbol !== "PETR4"),
      item({ symbol: "BTCBRL", market: "crypto", target_below: 300000, price: 350000, currency: "R$", price_at: T1, fresh: "ok" }),
    ];
    vi.useFakeTimers();
    let carga = 0;
    requestMock.mockImplementation(async (path, init) => {
      const metodo = init?.method ?? "GET";
      if (metodo === "GET" && path === "/api/watch") {
        carga += 1;
        return { itens: carga === 1 ? listaMista : comNovo }; // o servidor AINDA tem PETR4
      }
      if (metodo === "GET" && path === "/api/status") return statusPadrao();
      throw new Error(`chamada inesperada: ${metodo} ${path}`);
    });
    renderAlertas();
    await act(async () => {});
    fireEvent.click(nomeDoBotao("Remover PETR4"));
    await act(async () => {
      vi.advanceTimersByTime(5_000); // pendência de DELETE confirmada
    });
    await act(async () => {
      vi.advanceTimersByTime(60_000); // tick de polling
    });
    expect(screen.queryByText("PETR4")).not.toBeInTheDocument(); // não ressuscita
    expect(linhaDe("BTCBRL").textContent).toContain("R$"); // item novo aparece
  });

  it("401 no meio da edição → ReloginModal NO LUGAR (sem navegar), campos preservados e polling pausado", async () => {
    vi.useFakeTimers();
    requestMock.mockImplementation(async (path, init) => {
      const metodo = init?.method ?? "GET";
      if (metodo === "GET" && path === "/api/watch") return { itens: listaMista };
      if (metodo === "GET" && path === "/api/status") return statusPadrao();
      if (metodo === "PATCH" && path === "/api/watch/PETR4/b3") disparar401();
      throw new Error(`chamada inesperada: ${metodo} ${path}`);
    });
    renderAlertasComRotas();
    await act(async () => {});
    fireEvent.change(screen.getByLabelText("Alvo de VALE3"), { target: { value: "60" } });
    await act(async () => {});
    fireEvent.click(nomeDoBotao("Pausar PETR4")); // 401 a caminho
    await act(async () => {});
    expect(screen.getByText("Sessão expirada")).toBeInTheDocument();
    expect(screen.queryByText("PROBE-PAGINA-LOGIN")).not.toBeInTheDocument(); // sem navegação
    expect(screen.getByLabelText("Alvo de VALE3")).toHaveValue(60); // edição em memória
    const chamadasAntes = contagemGet("/api/watch");
    await act(async () => {
      vi.advanceTimersByTime(120_000); // dois ciclos com o modal aberto
    });
    expect(contagemGet("/api/watch")).toBe(chamadasAntes); // polling pausado
  });

  it("re-login com sucesso dentro do modal: fecha, recarrega watch+status na hora e o ciclo de 60 s retoma", async () => {
    let carga = 0;
    const aposRelogar = listaMista.map((i) =>
      i.symbol === "PETR4" ? { ...i, price: 41.0 } : i,
    );
    vi.useFakeTimers();
    requestMock.mockImplementation(async (path, init) => {
      const metodo = init?.method ?? "GET";
      if (metodo === "GET" && path === "/api/watch") {
        carga += 1;
        return { itens: carga >= 2 ? aposRelogar : listaMista };
      }
      if (metodo === "GET" && path === "/api/status") return statusPadrao();
      if (metodo === "PATCH" && path === "/api/watch/PETR4/b3") disparar401();
      if (metodo === "POST" && path === "/api/auth/login") return {};
      throw new Error(`chamada inesperada: ${metodo} ${path}`);
    });
    renderAlertasComRotas();
    await act(async () => {});
    fireEvent.click(nomeDoBotao("Pausar PETR4"));
    await act(async () => {});
    expect(screen.getByText("Sessão expirada")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Usuário"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Senha"), { target: { value: "senha-super-segura-123" } });
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    await act(async () => {});
    expect(screen.queryByText("Sessão expirada")).not.toBeInTheDocument(); // modal fechou
    expect(contagemGet("/api/watch")).toBe(2); // carga IMEDIATA após re-entrar
    expect(linhaDe("PETR4").textContent).toContain("41,00");
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(contagemGet("/api/watch")).toBe(3); // o ciclo de 60 s retomou
  });

  it("re-login com credenciais erradas mostra o erro inline no modal, que permanece aberto", async () => {
    vi.useFakeTimers();
    requestMock.mockImplementation(async (path, init) => {
      const metodo = init?.method ?? "GET";
      if (metodo === "GET" && path === "/api/watch") return { itens: listaMista };
      if (metodo === "GET" && path === "/api/status") return statusPadrao();
      if (metodo === "PATCH" && path === "/api/watch/PETR4/b3") disparar401();
      if (metodo === "POST" && path === "/api/auth/login") {
        throw new ApiError(401, { detail: "usuário ou senha inválidos" }, "usuário ou senha inválidos");
      }
      throw new Error(`chamada inesperada: ${metodo} ${path}`);
    });
    renderAlertasComRotas();
    await act(async () => {});
    fireEvent.click(nomeDoBotao("Pausar PETR4"));
    await act(async () => {});
    fireEvent.change(screen.getByLabelText("Usuário"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("Senha"), { target: { value: "errada" } });
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));
    await act(async () => {});
    expect(screen.getByText("usuário ou senha inválidos")).toBeInTheDocument();
    expect(screen.getByText("Sessão expirada")).toBeInTheDocument(); // segue aberto
  });

  it("o handler de 401 é EMPILHADO no mount (pushUnauthorized devolve o restore) e o unmount INVOCa o restore — o dono anterior volta", async () => {
    vi.useFakeTimers();
    requestMock.mockImplementation(async (path, init) => {
      const metodo = init?.method ?? "GET";
      if (metodo === "GET" && path === "/api/watch") return { itens: listaMista };
      if (metodo === "GET" && path === "/api/status") return statusPadrao();
      throw new Error(`chamada inesperada: ${metodo} ${path}`);
    });
    const { unmount } = renderAlertasComRotas();
    await act(async () => {});
    expect(pushUnauthorizedMock).toHaveBeenCalledWith(expect.any(Function));
    expect(holder401.handler).not.toBeNull(); // o registro empilhou o handler da página
    unmount();
    // O restore INVOCADO devolve o dono anterior (o holder volta a null aqui —
    // mock de página única; no app real o default do App volta ao topo).
    expect(holder401.handler).toBeNull();
  });

  it("nenhum dado de edição vai a localStorage/sessionStorage (proibição do plano, arquitetura)", async () => {
    const respostaPut = { itens: listaMista };
    await montarComTimersFake(listaMista, {
      "DELETE /api/watch/PETR4/b3": () => undefined,
      "PUT /api/watch": () => respostaPut,
    });
    // Um pouco de cada fluxo de edição: adicionar, editar alvo, remover, salvar.
    fireEvent.change(screen.getByLabelText("Novo ticker"), { target: { value: "BBDC4" } });
    fireEvent.change(screen.getByLabelText("Novo alvo"), { target: { value: "30" } });
    fireEvent.click(nomeDoBotao("Adicionar"));
    await act(async () => {});
    fireEvent.change(screen.getByLabelText("Alvo de VALE3"), { target: { value: "60" } });
    fireEvent.click(nomeDoBotao("Remover PETR4"));
    fireEvent.click(nomeDoBotao("Salvar"));
    await act(async () => {});
    expect(screen.getByText("Alterações salvas com sucesso")).toBeInTheDocument();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});
