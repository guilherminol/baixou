import { useEffect, useState } from "react";
import AppHeader from "../components/AppHeader";
import { buscarStatus, type StatusResponse } from "../api/status";
import { ApiError, request } from "../api/client";
import type { components } from "../api/generated/schema";
import { formatTime, timeAgoMinutes } from "../lib/format";
import {
  classeEstadoFonte,
  larguraFill,
  textoEstadoFonte,
} from "../lib/estados";
import "../styles/status.css";

// Tela /status (UI-02): o painel de operação do admin — quando o aviso não
// sai, esta tela diz por quê (fonte com erro nomeada, cota esgotada, canal
// sem configuração). Tudo vem de UM endpoint: o GET /api/status (D-03), cujos
// estados nascem DERIVADOS NO SERVIDOR — o navegador nunca calcula limiar
// (D-02): só consome `estado` da fonte, `configurado` do canal e a cota
// pronta. A única aritmética daqui é de EXIBIÇÃO (largura da barra).

const INTERVALO_POLLING_MS = 60_000; // mesmo ciclo da página de alertas
const POSICAO_MARCADOR = 80; // D-16: o ponto do aviso único (80% da cota)

type StatusFonte = StatusResponse["fontes"][number];
type StatusCanal = StatusResponse["canais"][number];
type TesteNotificador = components["schemas"]["TesteNotificador"];

/** Resultado do teste de UM canal — estado do CLIENTE, só em memória (D-05):
 * nada vai a storage nem ao banco; recarregar a página perde tudo. */
interface ResultadoTeste {
  ok: boolean;
  mensagem: string;
}

/** Meta-line do cabeçalho (D-03) — a MESMA derivação da página de alertas:
 * "Última consulta há X min · próxima em Y min · cota Z/W" em pt-BR. */
function metaDoStatus(status: StatusResponse): string {
  const ultima = status.ultima_rodada
    ? `há ${timeAgoMinutes(status.ultima_rodada)} min`
    : "—";
  const proxima = Math.max(
    0,
    Math.round((new Date(status.proxima_rodada).getTime() - Date.now()) / 60000),
  );
  return `Última consulta ${ultima} · próxima em ${proxima} min · cota ${status.cota.usadas.toLocaleString("pt-BR")}/${status.cota.limite.toLocaleString("pt-BR")}`;
}

/** Linha de UMA fonte (D-14, literal UI-02): o NOME da fonte está na linha do
 * erro — estado em texto, "N rodadas seguidas" quando houver falhas, o último
 * erro quando existir e a hora do último sucesso ("—" quando null). */
function linhaFonte(fonte: StatusFonte) {
  return (
    <div className="linha" data-testid={`fonte-${fonte.nome}`}>
      <span className="symbol-cell">{fonte.nome}</span>
      <span className={classeEstadoFonte(fonte.estado)}>
        {textoEstadoFonte(fonte.estado)}
      </span>
      {fonte.falhas_seguidas > 0 ? (
        <span className="fonte-extra">
          {fonte.falhas_seguidas} rodadas seguidas
        </span>
      ) : null}
      {fonte.ultimo_erro ? (
        <span className="fonte-extra">último erro: {fonte.ultimo_erro}</span>
      ) : null}
      <span className="num fonte-sucesso">
        último sucesso {formatTime(fonte.ultimo_sucesso)}
      </span>
    </div>
  );
}

/** Linha de UM canal (D-15): nome, configurado ou não em texto e o último
 * resultado conhecido VERBATIM da API (ex. "erro unavailable (HTTP 500)").
 * Nenhum segredo passa por aqui: o contrato só expõe nome/booleano (K2).
 * NOTIF-02 (D-05): canal configurado ganha o botão "Testar" — aviso REAL,
 * um canal por vez (o próprio botão desabilita em voo, SC-4); canais
 * diferentes disparam em paralelo, cada resultado na linha do seu canal. */
function linhaCanal(
  canal: StatusCanal,
  teste: { emVoo: boolean; resultado: ResultadoTeste | null; aoTestar: () => void },
) {
  return (
    <div className="linha" data-testid={`canal-${canal.nome}`}>
      <span className="symbol-cell">{canal.nome}</span>
      <span className="tag-state">
        {canal.configurado ? "configurado" : "não configurado"}
      </span>
      {canal.ultimo_resultado ? (
        <span className="canal-resultado">{canal.ultimo_resultado}</span>
      ) : null}
      {canal.configurado ? (
        <>
          <button
            type="button"
            className="btn-testar"
            aria-label={`Testar canal ${canal.nome}`}
            disabled={teste.emVoo}
            onClick={teste.aoTestar}
          >
            {teste.emVoo ? "testando…" : "Testar"}
          </button>
          {teste.resultado ? (
            <span
              className={"resultado-teste" + (teste.resultado.ok ? "" : " falha")}
              role="status"
              aria-live="polite"
              data-testid={`resultado-${canal.nome}`}
            >
              {teste.resultado.mensagem}
            </span>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/**
 * Tela /status (UI-02): Rodadas, Cota da brapi (barra D-16), Fontes (D-14) e
 * Canais (D-15), com polling de 60 s no mesmo ciclo da página de alertas. O
 * 401 segue o default do app (o handler global navega para /login — não há
 * edição a preservar aqui, então esta página NÃO registra handler próprio).
 */
export default function StatusPage() {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  // Estado do teste de canal (NOTIF-02): SÓ em memória — sem storage (D-05).
  const [testesEmVoo, setTestesEmVoo] = useState<Set<string>>(new Set());
  const [resultadosTeste, setResultadosTeste] = useState<Map<string, ResultadoTeste>>(
    new Map(),
  );

  useEffect(() => {
    let vivo = true;
    async function carregar() {
      try {
        const estado = await buscarStatus();
        if (vivo) setStatus(estado);
      } catch {
        // 401 já disparou o handler global (redirect default); erro de rede
        // preserva o estado atual — a leitura nunca derruba a tela.
      }
    }
    void carregar();
    const id = window.setInterval(() => void carregar(), INTERVALO_POLLING_MS);
    return () => {
      vivo = false;
      window.clearInterval(id);
    };
  }, []);

  /** Dispara o aviso real de teste pelo canal (D-05/NOTIF-02): POST sem corpo
   * (o guard só exige Content-Type com corpo, igual ao logout). A falha de
   * entrega é RESULTADO ({ok:false}), nunca erro de rede; 409 vira texto
   * inline (defesa em profundidade — o botão já não existe para canal não
   * configurado); qualquer outra ApiError vira mensagem genérica com o
   * status. Um canal por vez: o disable do próprio botão em voo (SC-4) — e o
   * guard aqui reforça. NADA é gravado: resultado só no estado do componente. */
  async function testeCanal(canal: string) {
    if (testesEmVoo.has(canal)) return;
    setTestesEmVoo((anterior) => new Set(anterior).add(canal));
    try {
      const resposta = await request<TesteNotificador>(
        `/api/notifiers/${canal}/test`,
        { method: "POST" },
      );
      setResultadosTeste((anterior) => new Map(anterior).set(canal, resposta));
    } catch (e) {
      const resultado: ResultadoTeste =
        e instanceof ApiError && e.status === 409
          ? { ok: false, mensagem: "canal não configurado" }
          : e instanceof ApiError
            ? {
                ok: false,
                mensagem: `não foi possível concluir o teste (erro ${e.status})`,
              }
            : { ok: false, mensagem: "não foi possível concluir o teste" };
      setResultadosTeste((anterior) => new Map(anterior).set(canal, resultado));
    } finally {
      setTestesEmVoo((anterior) => {
        const proximo = new Set(anterior);
        proximo.delete(canal);
        return proximo;
      });
    }
  }

  if (status === null) {
    return (
      <main className="container">
        <AppHeader meta="Aguardando dados..." />
      </main>
    );
  }

  const semCota = status.cota.limite <= 0;
  const fill = larguraFill(status.cota.usadas, status.cota.limite);
  const nenhumCanal = status.canais.every((canal) => !canal.configurado);

  return (
    <main className="container">
      <AppHeader meta={metaDoStatus(status)} />

      <section className="market-section">
        <h2 className="market-title">Rodadas</h2>
        <div className="table-wrap">
          <div className="rodadas-grid">
            <div className="rodada">
              <span className="rotulo">última</span>
              {/* null → "—" (formatTime); próxima é SEMPRE presente (D-04). */}
              <span className="num" data-testid="rodada-ultima">
                {formatTime(status.ultima_rodada)}
              </span>
            </div>
            <div className="rodada">
              <span className="rotulo">próxima</span>
              <span className="num" data-testid="rodada-proxima">
                {formatTime(status.proxima_rodada)}
              </span>
            </div>
          </div>
        </div>
      </section>

      <section className="market-section">
        <h2 className="market-title">Cota da brapi</h2>
        <div className="table-wrap">
          <div className="cota-bloco">
            {/* D-16: barra fina com o marcador FIXO na posição 80% — o ponto
                do aviso único. A classe .atingido é display (fill passou do
                marcador), nunca limiar de estado no cliente. */}
            <div
              className="cota-barra"
              role="img"
              aria-label={`cota da brapi: ${status.cota.usadas} de ${status.cota.limite} chamadas no mês`}
            >
              <div
                className="cota-fill"
                data-testid="cota-fill"
                style={{ width: `${fill}%` }}
              />
              <div
                className={
                  "cota-marcador" + (fill >= POSICAO_MARCADOR ? " atingido" : "")
                }
                data-testid="cota-marcador"
                style={{ left: `${POSICAO_MARCADOR}%` }}
              />
            </div>
            <span className="num cota-numeros" data-testid="cota-numeros">
              {semCota
                ? "—"
                : `${status.cota.usadas.toLocaleString("pt-BR")}/${status.cota.limite.toLocaleString("pt-BR")}`}
            </span>
          </div>
        </div>
      </section>

      <section className="market-section">
        <h2 className="market-title">Fontes</h2>
        <div className="table-wrap">
          {status.fontes.map((fonte) => (
            <div key={fonte.nome}>{linhaFonte(fonte)}</div>
          ))}
        </div>
      </section>

      <section className="market-section">
        <h2 className="market-title">Canais</h2>
        {nenhumCanal ? (
          // Literal UI-02 (D-15): faixa fixa INFORMATIVA no lugar das linhas —
          // não é erro de sistema, é o estado vazio dos canais.
          <div className="faixa-informativa" role="status">
            nenhum canal configurado
          </div>
        ) : (
          <div className="table-wrap">
            {status.canais.map((canal) => (
              <div key={canal.nome}>
                {linhaCanal(canal, {
                  emVoo: testesEmVoo.has(canal.nome),
                  resultado: resultadosTeste.get(canal.nome) ?? null,
                  aoTestar: () => void testeCanal(canal.nome),
                })}
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
