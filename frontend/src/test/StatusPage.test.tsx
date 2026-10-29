// Suíte de componente da UI-02 (tracer do 05-04): rodadas, cota com barra e
// marcador na posição 80% (D-16), fontes com o NOME na linha do erro (D-14,
// literal UI-02), canais com a faixa fixa de nenhum canal (D-15), o polling
// de 60 s e o botão de teste por canal (NOTIF-02, D-05). buscarStatus() e o
// client são MOCKADOS (vi.mock) — zero rede.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
// O CSS e o código da página são lidos do DISCO para as verificações
// estruturais (mesmo molde do 05-03): node:fs existe no runtime do vitest,
// mas @types/node não está na lista fechada de pacotes aprovados.
// @ts-expect-error — módulo builtin do Node sem tipos no tsconfig (DOM/vite only)
import { readFileSync } from "node:fs";

declare const process: { cwd(): string };
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { StatusResponse } from "../api/status";
import StatusPage from "../pages/StatusPage";

const { buscarStatusMock, requestMock } = vi.hoisted(() => ({
  buscarStatusMock: vi.fn(),
  requestMock: vi.fn(),
}));

vi.mock("../api/status", () => ({
  buscarStatus: buscarStatusMock,
}));

// Espelho FIEL do contrato do client real (mesmo molde das suítes do 05-02/
// 05-03): o POST do teste de canal passa por aqui sem tocar rede.
vi.mock("../api/client", () => {
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
    onUnauthorized: () => {},
    parseErrosEnvelope: () => [],
    request: requestMock,
  };
});

/** Promise controlada pelo teste: segura o POST em voo para provar o estado
 * "testando…" e a serialização por canal (SC-4). */
function diferido<T>() {
  let resolver!: (valor: T) => void;
  const promise = new Promise<T>((res) => {
    resolver = res;
  });
  return { promise, resolver };
}

/** Envelope completo do /api/status (contrato congelado D-03) com dados
 * operacionais sadios — os testes sobrepõem só o que exercitam. */
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

async function montarCom(estado: StatusResponse) {
  buscarStatusMock.mockResolvedValue(estado);
  render(
    <MemoryRouter>
      <StatusPage />
    </MemoryRouter>,
  );
  // O flush dentro de act resolve a promise do GET mockado (asserções
  // seguintes usam getBy* síncronos contra a renderização pós-carga).
  await act(async () => {});
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  buscarStatusMock.mockReset();
  requestMock.mockReset();
});

describe("StatusPage — UI-02 (tracer 05-04)", () => {
  it("renderiza as quatro seções do /api/status com os títulos do mockup e uma linha por fonte/canal", async () => {
    await montarCom(statusPadrao());
    expect(screen.getByRole("heading", { name: "Rodadas" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Cota da brapi" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Fontes" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Canais" })).toBeInTheDocument();
    expect(screen.getByTestId("fonte-brapi")).toBeInTheDocument();
    expect(screen.getByTestId("fonte-binance")).toBeInTheDocument();
    expect(screen.getByTestId("canal-telegram")).toBeInTheDocument();
    expect(screen.getByTestId("canal-ntfy")).toBeInTheDocument();
  });

  it("rodadas: última e próxima em HH:mm pt-BR; ultima_rodada null vira '—' e a próxima está SEMPRE presente (D-04)", async () => {
    await montarCom(statusPadrao({ ultima_rodada: "2026-10-29T20:30:00Z" }));
    expect(screen.getByTestId("rodada-ultima").textContent).toMatch(/\d{2}:\d{2}/);
    expect(screen.getByTestId("rodada-proxima").textContent).toMatch(/\d{2}:\d{2}/);
    cleanup();
    await montarCom(statusPadrao({ ultima_rodada: null }));
    expect(screen.getByTestId("rodada-ultima").textContent).toBe("—");
    expect(screen.getByTestId("rodada-proxima").textContent).toMatch(/\d{2}:\d{2}/);
  });

  it("cota da brapi (D-16): 'usadas/limite' pt-BR em mono e barra com fill proporcional e marcador FIXO em 80%", async () => {
    // 12000/15000 → fill 80% COINCIDINDO com o marcador (o ponto do aviso único)
    await montarCom(statusPadrao({ cota: { usadas: 12000, limite: 15000 } }));
    expect(screen.getByTestId("cota-numeros").textContent).toBe("12.000/15.000");
    const fill = screen.getByTestId("cota-fill");
    const marcador = screen.getByTestId("cota-marcador");
    expect(fill.style.width).toBe("80%");
    expect(marcador.style.left).toBe("80%");
  });

  it("barra (D-16): acima de 100% o fill satura e o marcador dentro dele ganha destaque — abaixo, sem destaque (display, nunca estado)", async () => {
    await montarCom(statusPadrao({ cota: { usadas: 16000, limite: 15000 } }));
    expect(screen.getByTestId("cota-fill").style.width).toBe("100%");
    expect(screen.getByTestId("cota-marcador")).toHaveClass("atingido");
    cleanup();
    await montarCom(statusPadrao({ cota: { usadas: 100, limite: 15000 } }));
    // 100/15000 → fill mínimo (< 1%), longe do marcador — sem destaque.
    expect(parseFloat(screen.getByTestId("cota-fill").style.width)).toBeLessThan(1);
    expect(screen.getByTestId("cota-marcador")).not.toHaveClass("atingido");
  });

  it("cota sem limite (limite 0): os números viram '—' e o fill fica em 0 (sem cota)", async () => {
    await montarCom(statusPadrao({ cota: { usadas: 0, limite: 0 } }));
    expect(screen.getByTestId("cota-numeros").textContent).toBe("—");
    expect(screen.getByTestId("cota-fill").style.width).toBe("0%");
  });

  it("fontes (D-14): estado em TEXTO, 'último sucesso HH:mm' ('—' quando null) e a ordem da API", async () => {
    await montarCom(
      statusPadrao({
        fontes: [
          {
            nome: "brapi",
            estado: "operacional",
            falhas_seguidas: 0,
            ultimo_sucesso: "2026-10-29T20:30:00Z",
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
      }),
    );
    const brapi = screen.getByTestId("fonte-brapi");
    expect(brapi.textContent).toContain("brapi");
    expect(brapi.textContent).toContain("operacional");
    expect(brapi.textContent).toMatch(/último sucesso \d{2}:\d{2}/);
    expect(brapi.textContent).not.toContain("rodadas seguidas");
    const binance = screen.getByTestId("fonte-binance");
    expect(binance.textContent).toContain("último sucesso —");
    expect(binance.textContent).not.toContain("rodadas seguidas");
  });

  it("fonte em falha_prolongada: o NOME da fonte acompanha o estado legível, a contagem 'N rodadas seguidas' e o último erro (literal UI-02)", async () => {
    await montarCom(
      statusPadrao({
        fontes: [
          {
            nome: "brapi",
            estado: "falha_prolongada",
            falhas_seguidas: 3,
            ultimo_sucesso: "2026-10-29T10:00:00Z",
            ultimo_erro: "rate_limit (HTTP 429)",
          },
          {
            nome: "binance",
            estado: "erro",
            falhas_seguidas: 1,
            ultimo_sucesso: null,
            ultimo_erro: "transporte",
          },
        ],
      }),
    );
    const brapi = screen.getByTestId("fonte-brapi");
    expect(brapi.textContent).toContain("brapi"); // o NOME na linha do erro
    expect(brapi.textContent).toContain("falha prolongada"); // estado legível
    expect(brapi.textContent).toContain("3 rodadas seguidas");
    expect(brapi.textContent).toContain("último erro: rate_limit (HTTP 429)");
    expect(brapi.querySelector(".fonte-destaque")).not.toBeNull(); // destaque
    const binance = screen.getByTestId("fonte-binance");
    expect(binance.querySelector(".symbol-cell")?.textContent).toBe("binance");
    expect(binance.textContent).toContain("erro"); // estado legível "erro"
    expect(binance.textContent).toContain("1 rodadas seguidas");
    expect(binance.textContent).toContain("último erro: transporte");
  });

  it("canais (D-15): com os DOIS desconfigurados a faixa fixa 'nenhum canal configurado' aparece NO LUGAR das linhas (literal UI-02)", async () => {
    await montarCom(
      statusPadrao({
        canais: [
          { nome: "telegram", configurado: false, ultimo_resultado: null },
          { nome: "ntfy", configurado: false, ultimo_resultado: null },
        ],
      }),
    );
    expect(screen.getByText("nenhum canal configurado")).toBeInTheDocument();
    expect(screen.queryByTestId("canal-telegram")).not.toBeInTheDocument();
    expect(screen.queryByTestId("canal-ntfy")).not.toBeInTheDocument();
  });

  it("canais (D-15): nome + configurado/não configurado + ultimo_resultado VERBATIM da API; com um canal configurado a faixa NÃO aparece", async () => {
    await montarCom(
      statusPadrao({
        canais: [
          {
            nome: "telegram",
            configurado: true,
            ultimo_resultado: "erro unavailable (HTTP 500)",
          },
          { nome: "ntfy", configurado: false, ultimo_resultado: null },
        ],
      }),
    );
    const telegram = screen.getByTestId("canal-telegram");
    expect(telegram.querySelector(".tag-state")?.textContent).toBe("configurado");
    expect(telegram.textContent).toContain("erro unavailable (HTTP 500)");
    const ntfy = screen.getByTestId("canal-ntfy");
    expect(ntfy.querySelector(".tag-state")?.textContent).toBe("não configurado");
    expect(screen.queryByText("nenhum canal configurado")).not.toBeInTheDocument();
  });

  it("meta-line do cabeçalho: 'Aguardando dados...' na primeira carga e a MESMA derivação da página de alertas depois (D-03)", async () => {
    buscarStatusMock.mockResolvedValue(statusPadrao());
    render(
      <MemoryRouter>
        <StatusPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Aguardando dados...")).toBeInTheDocument();
    await act(async () => {});
    const meta = screen.getByText(/Última consulta/);
    expect(meta.textContent).toMatch(/próxima em \d+ min/);
    expect(meta.textContent).toContain("cota 3.120/15.000");
  });

  it("polling de 60 s refaz o /api/status (a tela substitui no lugar) e a limpeza acontece no unmount", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    buscarStatusMock.mockResolvedValue(statusPadrao());
    const { unmount } = render(
      <MemoryRouter>
        <StatusPage />
      </MemoryRouter>,
    );
    await act(async () => {});
    expect(buscarStatusMock).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("cota-numeros").textContent).toBe("3.120/15.000");
    // O tick seguinte traz cota diferente — a tela substitui sem recarregar.
    buscarStatusMock.mockResolvedValue(
      statusPadrao({ cota: { usadas: 13000, limite: 15000 } }),
    );
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(buscarStatusMock).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("cota-numeros").textContent).toBe("13.000/15.000");
    unmount();
    await act(async () => {
      vi.advanceTimersByTime(180_000);
    });
    expect(buscarStatusMock).toHaveBeenCalledTimes(2); // sem vazamento de intervalo
  });

  it("nenhuma string de segredo existe no componente e NENHUM handler 401 próprio é registrado (K2 + default do app)", () => {
    const codigo = readFileSync(process.cwd() + "/src/pages/StatusPage.tsx", "utf8");
    expect(codigo).not.toMatch(/TELEGRAM_TOKEN|NTFY_TOPIC|chat_id|NTFY_URL|bot\d{6,}/);
    expect(codigo).not.toMatch(/onUnauthorized/);
  });

  it("status.css no contrato do artefato: track 4px, fill accent, marcador 2px, linhas de 44px e faixa informativa (D-16/D-14/D-15)", () => {
    const css = readFileSync(process.cwd() + "/src/styles/status.css", "utf8");
    expect(css).toMatch(/\.cota-barra\s*\{[^}]*height:\s*4px/);
    expect(css).toMatch(/\.cota-fill\s*\{[^}]*background:\s*var\(--accent\)/);
    expect(css).toMatch(/\.cota-marcador\s*\{[^}]*width:\s*2px/);
    expect(css).toMatch(/\.linha\s*\{[^}]*min-height:\s*44px/);
    expect(css).toMatch(/\.faixa-informativa\s*\{/);
  });
});

describe("StatusPage — botão de teste por canal (NOTIF-02, D-05)", () => {
  it("cada canal configurado=true tem o botão 'Testar' (aria-label, alvo 44px); configurado=false NÃO tem botão", async () => {
    await montarCom(
      statusPadrao({
        canais: [
          { nome: "telegram", configurado: true, ultimo_resultado: null },
          { nome: "ntfy", configurado: false, ultimo_resultado: null },
        ],
      }),
    );
    const botao = screen.getByRole("button", { name: "Testar canal telegram" });
    expect(botao).toHaveTextContent("Testar");
    expect(
      screen.queryByRole("button", { name: "Testar canal ntfy" }),
    ).not.toBeInTheDocument();
    // Alvo de 44px no CSS do botão (contrato do artefato).
    const css = readFileSync(process.cwd() + "/src/styles/status.css", "utf8");
    expect(css).toMatch(/\.btn-testar\s*\{[^}]*min-height:\s*44px/);
  });

  it("clique dispara POST /api/notifiers/telegram/test SEM corpo (contrato do guard) e o {ok:true, mensagem} renderiza inline com aria-live", async () => {
    await montarCom(
      statusPadrao({
        canais: [{ nome: "telegram", configurado: true, ultimo_resultado: null }],
      }),
    );
    requestMock.mockResolvedValueOnce({
      ok: true,
      mensagem: "aviso de teste aceito pelo canal",
    });
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    const resultado = await screen.findByTestId("resultado-telegram");
    expect(resultado.textContent).toBe("aviso de teste aceito pelo canal");
    expect(resultado).toHaveAttribute("aria-live", "polite");
    const chamada = requestMock.mock.calls.find(
      (c) => c[0] === "/api/notifiers/telegram/test",
    );
    expect(chamada?.[1]).toMatchObject({ method: "POST" });
    // SEM corpo — o guard só exige Content-Type quando há corpo (WR-01).
    expect((chamada?.[1] as RequestInit | undefined)?.body).toBeUndefined();
    // O botão reabilita com o texto padrão.
    const depois = screen.getByRole("button", { name: "Testar canal telegram" });
    expect(depois).toBeEnabled();
    expect(depois).toHaveTextContent("Testar");
  });

  it("{ok:false, mensagem} renderiza a mensagem EXATA da API — falha de entrega é resultado visível, não erro de rede", async () => {
    await montarCom(
      statusPadrao({
        canais: [{ nome: "telegram", configurado: true, ultimo_resultado: null }],
      }),
    );
    requestMock.mockResolvedValueOnce({
      ok: false,
      mensagem: "o canal recusou o aviso de teste (erro http_status, HTTP 400)",
    });
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    const resultado = await screen.findByTestId("resultado-telegram");
    expect(resultado.textContent).toBe(
      "o canal recusou o aviso de teste (erro http_status, HTTP 400)",
    );
    expect(resultado).toHaveClass("falha"); // --error-text na falha (CSS)
  });

  it("enquanto em voo o botão fica desabilitado com 'testando…' — o SEGUNDO clique no mesmo canal é impossível (um canal por vez, SC-4)", async () => {
    await montarCom(
      statusPadrao({
        canais: [{ nome: "telegram", configurado: true, ultimo_resultado: null }],
      }),
    );
    const pendente = diferido<{ ok: boolean; mensagem: string }>();
    requestMock.mockReturnValueOnce(pendente.promise);
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    await screen.findByText("testando…");
    expect(screen.getByRole("button", { name: "Testar canal telegram" })).toBeDisabled();
    // O segundo clique num botão desabilitado não dispara nada (jsdom respeita).
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    pendente.resolver({ ok: true, mensagem: "aviso de teste aceito pelo canal" });
    const resultado = await screen.findByTestId("resultado-telegram");
    expect(resultado.textContent).toBe("aviso de teste aceito pelo canal");
    const posts = requestMock.mock.calls.filter(
      (c) => c[0] === "/api/notifiers/telegram/test",
    );
    expect(posts).toHaveLength(1); // exatamente UM aviso saiu
  });

  it("canais DIFERENTES podem disparar em paralelo e cada resultado fica na linha do seu canal", async () => {
    await montarCom(
      statusPadrao({
        canais: [
          { nome: "telegram", configurado: true, ultimo_resultado: null },
          { nome: "ntfy", configurado: true, ultimo_resultado: null },
        ],
      }),
    );
    const pendenteTelegram = diferido<{ ok: boolean; mensagem: string }>();
    requestMock.mockImplementation((path: string) => {
      if (path === "/api/notifiers/telegram/test") return pendenteTelegram.promise;
      if (path === "/api/notifiers/ntfy/test") {
        return Promise.resolve({ ok: true, mensagem: "aviso de teste aceito pelo canal" });
      }
      throw new Error(`chamada inesperada: ${path}`);
    });
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    expect(screen.getByRole("button", { name: "Testar canal telegram" })).toBeDisabled();
    // O ntfy continua HABILITADO enquanto o telegram está em voo.
    fireEvent.click(screen.getByRole("button", { name: "Testar canal ntfy" }));
    const resultadoNtfy = await screen.findByTestId("resultado-ntfy");
    expect(resultadoNtfy.textContent).toBe("aviso de teste aceito pelo canal");
    expect(screen.queryByTestId("resultado-telegram")).not.toBeInTheDocument();
    pendenteTelegram.resolver({
      ok: false,
      mensagem: "o canal não respondeu ao teste (erro transporte)",
    });
    const resultadoTelegram = await screen.findByTestId("resultado-telegram");
    expect(resultadoTelegram.textContent).toBe(
      "o canal não respondeu ao teste (erro transporte)",
    );
    expect(screen.getByRole("button", { name: "Testar canal telegram" })).toBeEnabled();
  });

  it("409 (defesa em profundidade) → 'canal não configurado' renderiza inline", async () => {
    await montarCom(
      statusPadrao({
        canais: [{ nome: "telegram", configurado: true, ultimo_resultado: null }],
      }),
    );
    requestMock.mockRejectedValueOnce(
      new ApiError(409, { detail: "canal não configurado" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    const resultado = await screen.findByTestId("resultado-telegram");
    expect(resultado.textContent).toBe("canal não configurado");
  });

  it("outra ApiError → mensagem genérica PT-BR com o status; falha de rede → genérica sem status", async () => {
    await montarCom(
      statusPadrao({
        canais: [{ nome: "telegram", configurado: true, ultimo_resultado: null }],
      }),
    );
    requestMock.mockRejectedValueOnce(new ApiError(500, { detail: "boom" }));
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    const resultado = await screen.findByTestId("resultado-telegram");
    expect(resultado.textContent).toBe("não foi possível concluir o teste (erro 500)");
    requestMock.mockRejectedValueOnce(new Error("fetch failed"));
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    await screen.findByText("não foi possível concluir o teste");
  });

  it("remount com mocks zerados → nenhum resultado reaparece (estado em memória, nada gravado em storage)", async () => {
    const canais = [{ nome: "telegram", configurado: true, ultimo_resultado: null }];
    await montarCom(statusPadrao({ canais }));
    requestMock.mockResolvedValueOnce({
      ok: true,
      mensagem: "aviso de teste aceito pelo canal",
    });
    fireEvent.click(screen.getByRole("button", { name: "Testar canal telegram" }));
    await screen.findByTestId("resultado-telegram");
    // "Recarregar a página": remount com o estado de memória zerado.
    cleanup();
    buscarStatusMock.mockResolvedValue(statusPadrao({ canais }));
    render(
      <MemoryRouter>
        <StatusPage />
      </MemoryRouter>,
    );
    await act(async () => {});
    expect(screen.queryByTestId("resultado-telegram")).not.toBeInTheDocument();
    // Proibição D-05: nada do resultado é gravado em storage.
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});


