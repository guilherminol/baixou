// Suíte estrutural do fechamento do gap G-05-2 (SC-5 — 360 px sem rolagem
// lateral): a tabela de alertas a 360 px media scrollWidth 434/446 contra
// clientWidth 360 no UAT (teste 3) — o piso que sobra depois da sub-linha
// D-06 (input de alvo 90 px + 5 td com padding horizontal de 8 px + dois
// botões de ação de 44 px+ com rótulo de texto) excede a viewport.
//
// O conserto é 100% CSS: um bloco @media (max-width: 400px) no FIM de
// alerts.css que compacta o piso declarado. jsdom não faz layout, então a
// verificação é POR DECLARAÇÃO (molde dos testes CSS do AlertsPage.test):
// o teste lê os DOIS arquivos do disco com readFileSync, mede a ECONOMIA
// declarada do bloco compacto contra as bases do global.css e trava:
//   - economia ≥ 90 px (cobre o excesso medido de 74-86 px com margem);
//   - D-07: os mínimos de 44×44 px do .btn-action ficam de pé (a base
//     declara, o bloco compacto NUNCA sobrescreve min-width/min-height —
//     compactar nunca significa encolher o alvo de toque nem trocar o
//     rótulo PT-BR por ícone);
//   - sanidade: input 40-72 px (continua digitável para alvos como 15,15),
//     paddings ≥ 2 px por lado, fonte do botão ≤ 11 px.
//
// Derivação das constantes (estrutura REAL da linha a ≤600 px,
// AlertsPage.tsx linhas 108-176):
//   - 5 td visíveis por linha de dados: ticker (symbol-cell), preço (num),
//     alvo (num com .input-target), estado e a célula de ações — as três
//     colunas .col-dobra (Distância, Atualização, Disparo) estão ocultas
//     pela media query de 600 px e NÃO contam para o piso;
//   - 5 th visíveis no thead: Ticker · Preço · Alvo · Estado + a coluna
//     vazia de ações;
//   - 2 .btn-action por linha (Pausar/Retomar + Remover).
// Fórmula da economia declarada:
//   (inputBase − inputCompacto)
//   + 5 × 2 × (padTdBase − padTdCompacto)
//   + 5 × 2 × (padThBase − padThCompacto)
//   + 2 × 2 × (padBtnBase − padBtnCompacto)
// Com a prescrição do GREEN (input 60, paddings 4): 30 + 40 + 40 + 16 =
// 126 declarados — a linha de dados sozinha responde 86 px (30 + 40 + 16),
// exatamente o pior excesso medido (446 − 360); o termo de th (40) é a
// margem que leva o orçamento do bloco a 126 ≥ 90. A fonte 12→11 encolhe o
// rótulo FORA do orçamento determinístico (contribuição de fonte em px não
// é derivável de declaração CSS).
import { describe, expect, it } from "vitest";
// O CSS é lido do DISCO (o cwd do vitest é a raiz do frontend; o fs do Node
// aceita barras normais no Windows) — mesmo molde do AlertsPage.test.
// @ts-expect-error — módulo builtin do Node sem tipos no tsconfig (DOM/vite only)
import { readFileSync } from "node:fs";

declare const process: { cwd(): string };

const CSS_ALERTS = readFileSync(process.cwd() + "/src/styles/alerts.css", "utf8");
const CSS_GLOBAL = readFileSync(process.cwd() + "/src/styles/global.css", "utf8");

// Bases do global.css — o estado que mediu scrollWidth 434/446 no UAT. Se um
// futuro conserto mover as bases, as GUARDAS do primeiro teste quebram alto
// em vez de medir a economia contra números mortos.
const INPUT_BASE = 90; // .input-target { width: 90px } (global.css)
const PAD_TD_BASE = 8; // td { padding: 6px 8px } — 8 px POR LADO
const PAD_TH_BASE = 8; // th { padding: 8px } — 8 px por lado
const PAD_BTN_BASE = 8; // .btn-action { padding: 0 8px } — 8 px POR LADO

const TD_VISIVEIS = 5;
const TH_VISIVEIS = 5;
const BOTOES_ACAO = 2;

// Excesso medido do UAT: 434/446 − 360 = 74-86 px → piso de economia ≥ 90.
const ECONOMIA_MINIMA = 90;

/** Extrai o bloco @media (max-width: 400px) de alerts.css por slicing de
 * texto (molde da verificação estrutural da D-06). Quando o bloco não
 * existe devolve "" — as asserções falham nomeando o que falta. */
function blocoCompacto(): string {
  const indice = CSS_ALERTS.indexOf("@media (max-width: 400px)");
  return indice === -1 ? "" : CSS_ALERTS.slice(indice);
}

/** Corpo da regra `seletor { … }` no CSS global (seletores na coluna 0). */
function regraGlobal(seletor: string): string {
  const escape = seletor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = CSS_GLOBAL.match(new RegExp(`^${escape}\\s*\\{([^}]*)\\}`, "m"));
  return m?.[1] ?? "";
}

/** Corpo da regra `seletor { … }` dentro do bloco compacto (regras
 * indentadas — sem âncora de início de linha). */
function regraCompacta(bloco: string, seletor: string): string {
  const escape = seletor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = bloco.match(new RegExp(`${escape}\\s*\\{([^}]*)\\}`));
  return m?.[1] ?? "";
}

/** Valor px de uma declaração (`propriedade: Npx`) no corpo da regra. */
function pxDe(regra: string, propriedade: string): number | null {
  const m = regra.match(new RegExp(`${propriedade}:\\s*(\\d+(?:\\.\\d+)?)px`));
  return m ? Number(m[1]) : null;
}

/** Os dois valores de um `padding: vertical horizontal` (0 sem unidade
 * coberto no primeiro). */
function ladosDoPadding(regra: string): { vertical: number | null; horizontal: number | null } {
  const m = regra.match(/padding:\s*(\d+(?:\.\d+)?)px?\s+(\d+(?:\.\d+)?)px/);
  return m
    ? { vertical: Number(m[1]), horizontal: Number(m[2]) }
    : { vertical: null, horizontal: null };
}

describe("AlertsCompacto360 — orçamento de largura da tabela a 360 px (G-05-2, SC-5)", () => {
  it("guardas das bases: global.css ainda declara os valores contra os quais a economia é medida", () => {
    expect(regraGlobal("td")).toContain("padding: 6px 8px");
    expect(regraGlobal("th")).toContain("padding: 8px");
    expect(regraGlobal(".input-target")).toContain("width: 90px");
    const botaoBase = regraGlobal(".btn-action");
    expect(botaoBase).toContain("padding: 0 8px");
    expect(botaoBase).toContain("min-width: 44px");
    expect(botaoBase).toContain("min-height: 44px");
  });

  it("o bloco @media (max-width: 400px) existe em alerts.css e declara os quatro consertos", () => {
    const bloco = blocoCompacto();
    expect(
      bloco,
      "alerts.css não declara o bloco @media (max-width: 400px) — o conserto do piso de 360 px (G-05-2, SC-5) está ausente e a tabela rola de lado no UAT",
    ).not.toBe("");
    expect(regraCompacta(bloco, ".input-target")).toContain("width:");
    expect(regraCompacta(bloco, ".table-wrap td")).toContain("padding:");
    expect(regraCompacta(bloco, ".table-wrap th")).toContain("padding:");
    expect(regraCompacta(bloco, ".btn-action")).toContain("padding:");
  });

  it("economia declarada ≥ 90 px contra as bases: input + 5×2 td + 5×2 th + 2×2 botões", () => {
    const bloco = blocoCompacto();
    expect(bloco, "bloco @media (max-width: 400px) ausente em alerts.css — sem bloco não há economia a declarar").not.toBe("");
    const inputCompacto = pxDe(regraCompacta(bloco, ".input-target"), "width");
    expect(inputCompacto, ".input-target sem width px no bloco compacto").not.toBeNull();
    const td = ladosDoPadding(regraCompacta(bloco, ".table-wrap td"));
    expect(td.horizontal, ".table-wrap td sem padding horizontal px no bloco compacto").not.toBeNull();
    const th = ladosDoPadding(regraCompacta(bloco, ".table-wrap th"));
    expect(th.horizontal, ".table-wrap th sem padding horizontal px no bloco compacto").not.toBeNull();
    const botao = ladosDoPadding(regraCompacta(bloco, ".btn-action"));
    expect(botao.horizontal, ".btn-action sem padding horizontal px no bloco compacto").not.toBeNull();

    // O padding VERTICAL de td/th é preservado exatamente como na base
    // (6 px e 8 px) — só o horizontal compacta (prescrição do GREEN).
    expect(td.vertical, "padding vertical do td mudou — só o horizontal pode compactar").toBe(6);
    expect(th.vertical, "padding vertical do th mudou — só o horizontal pode compactar").toBe(8);

    const economia =
      (INPUT_BASE - (inputCompacto as number)) +
      TD_VISIVEIS * 2 * (PAD_TD_BASE - (td.horizontal as number)) +
      TH_VISIVEIS * 2 * (PAD_TH_BASE - (th.horizontal as number)) +
      BOTOES_ACAO * 2 * (PAD_BTN_BASE - (botao.horizontal as number));
    expect(
      economia,
      `economia declarada do bloco compacto (${economia} px) é menor que o piso de ${ECONOMIA_MINIMA} px — o excesso medido do UAT (74-86 px: scrollWidth 434/446 vs 360) fica descoberto`,
    ).toBeGreaterThanOrEqual(ECONOMIA_MINIMA);
  });

  it("D-07: os mínimos de 44×44 px do .btn-action ficam de pé — a base declara e o bloco compacto NÃO sobrescreve", () => {
    const botaoBase = regraGlobal(".btn-action");
    expect(botaoBase).toContain("min-width: 44px");
    expect(botaoBase).toContain("min-height: 44px");
    const bloco = blocoCompacto();
    expect(bloco, "bloco @media (max-width: 400px) ausente em alerts.css — a guarda de 44 px vale para o bloco que existir").not.toBe("");
    const botaoCompacto = regraCompacta(bloco, ".btn-action");
    expect(botaoCompacto).not.toMatch(/min-width/);
    expect(botaoCompacto).not.toMatch(/min-height/);
    expect(botaoCompacto).not.toMatch(/display/);
  });

  it("limites de sanidade: input 40-72 px, paddings ≥ 2 px por lado e fonte do botão ≤ 11 px", () => {
    const bloco = blocoCompacto();
    expect(bloco, "bloco @media (max-width: 400px) ausente em alerts.css — os limites de sanidade valem para o bloco que existir").not.toBe("");
    const inputCompacto = pxDe(regraCompacta(bloco, ".input-target"), "width");
    expect(inputCompacto).not.toBeNull();
    expect(inputCompacto).toBeGreaterThanOrEqual(40); // continua digitável (15,15)
    expect(inputCompacto).toBeLessThanOrEqual(72);
    const td = ladosDoPadding(regraCompacta(bloco, ".table-wrap td"));
    expect(td.horizontal).toBeGreaterThanOrEqual(2);
    const th = ladosDoPadding(regraCompacta(bloco, ".table-wrap th"));
    expect(th.horizontal).toBeGreaterThanOrEqual(2);
    const botao = ladosDoPadding(regraCompacta(bloco, ".btn-action"));
    expect(botao.horizontal).toBeGreaterThanOrEqual(2);
    const fonteBotao = pxDe(regraCompacta(bloco, ".btn-action"), "font-size");
    expect(fonteBotao, ".btn-action sem font-size no bloco compacto — a compactação de fonte é travada como PRESENÇA").not.toBeNull();
    expect(fonteBotao).toBeLessThanOrEqual(11);
  });
});
