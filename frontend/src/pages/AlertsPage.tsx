import { useEffect, useRef, useState, type FormEvent } from "react";
import AppHeader from "../components/AppHeader";
import ReloginModal from "../components/ReloginModal";
import {
  listarItens,
  patchPause,
  removerItem,
  salvarLote,
  type Market,
  type WatchItem,
} from "../api/watch";
import { buscarStatus, type StatusResponse } from "../api/status";
import { ApiError, parseErrosEnvelope, pushUnauthorized } from "../api/client";
import { formatCurrency, formatTime, timeAgoMinutes } from "../lib/format";
import {
  distancia,
  estadoClasse,
  estadoTexto,
  falhasDeCanal,
  linhaSuspeita,
  problemasDoStatus,
} from "../lib/estados";
import "../styles/alerts.css";

/** Chave única do item: symbol|market (o símbolo é único por mercado). */
function chaveDe(symbol: string, market: string): string {
  return `${symbol}|${market}`;
}

function chave(item: WatchItem): string {
  return chaveDe(item.symbol, item.market);
}

/** Meta-line do cabeçalho (D-03): "Última consulta há X min · próxima em
 * Y min · cota Z/W" — tudo derivado do /api/status, números pt-BR. */
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

const COLUNAS = 8; // Ticker · Preço · Alvo · Distância · Estado · Atualização · Disparo · ações
const JANELA_UNDO_MS = 5000; // WATCH-01: "Desfazer" por 5 s, timer POR item
const INTERVALO_POLLING_MS = 60_000; // arquitetura §Frontend: consulta a cada minuto

function CabecalhoTabela() {
  return (
    <thead>
      <tr>
        <th>Ticker</th>
        <th className="num">Preço</th>
        <th className="num">Alvo (&lt;)</th>
        <th className="num col-dobra">Distância</th>
        <th>Estado</th>
        <th className="num col-dobra">Atualização</th>
        <th className="num col-dobra">Disparo</th>
        <th></th>
      </tr>
    </thead>
  );
}

function LinhaUndo({ item, desfazer }: { item: WatchItem; desfazer: () => void }) {
  return (
    <tr className="row-undo">
      <td colSpan={COLUNAS}>
        <span>{item.symbol} removido. </span>
        <button type="button" className="btn-action" onClick={desfazer}>
          Desfazer
        </button>
      </td>
    </tr>
  );
}

function LinhaItem({
  item,
  alvo,
  valorAlvo,
  erroSalva,
  aoEditarAlvo,
  aoFocarAlvo,
  aoDesfocarAlvo,
  aoAlternarPausa,
  aoRemover,
}: {
  item: WatchItem;
  alvo: number;
  valorAlvo: string;
  erroSalva: string | null;
  aoEditarAlvo: (item: WatchItem, valor: string) => void;
  aoFocarAlvo: (item: WatchItem) => void;
  aoDesfocarAlvo: (item: WatchItem) => void;
  aoAlternarPausa: (item: WatchItem) => void;
  aoRemover: (item: WatchItem) => void;
}) {
  // A linha renderiza com o alvo EFETIVO (o editado localmente quando houver):
  // distância e estado recalculam na hora (comportamento do mockup).
  const visual: WatchItem = { ...item, target_below: alvo };
  const dist = distancia(visual);
  const suspeita = linhaSuspeita(visual);
  const canais = falhasDeCanal(visual);
  return (
    <>
      <tr>
        <td className="symbol-cell">{visual.symbol}</td>
        <td className="num">{formatCurrency(visual.price, visual.currency)}</td>
        <td className="num">
          <input
            className="input-target"
            type="number"
            step="any"
            min="0"
            aria-label={`Alvo de ${visual.symbol}`}
            value={valorAlvo}
            onChange={(evento) => aoEditarAlvo(item, evento.target.value)}
            onFocus={() => aoFocarAlvo(item)}
            onBlur={() => aoDesfocarAlvo(item)}
          />
        </td>
        <td className={dist.classe + " col-dobra"}>{dist.texto}</td>
        <td>
          <span className={estadoClasse(visual)}>{estadoTexto(visual)}</span>
          {erroSalva !== null ? (
            <span className="erro-item" data-testid={`erro-item-${visual.symbol}`}>
              {erroSalva}
            </span>
          ) : null}
          {suspeita !== null ? (
            <span className="estado-extra">{suspeita}</span>
          ) : null}
          {canais.map((falha) => (
            <span key={falha} className="estado-extra">
              {falha}
            </span>
          ))}
        </td>
        <td className="num col-dobra">{formatTime(visual.price_at)}</td>
        <td className="num col-dobra">{formatTime(visual.alerted_at)}</td>
        <td className="num">
          {/* D-09: Pausar/Retomar + Remover na célula de ações, alvos de 44px.
              A pausa é PATCH imediato — nunca entra no lote do Salvar. */}
          <button
            type="button"
            className="btn-action"
            aria-label={`${visual.paused ? "Retomar" : "Pausar"} ${visual.symbol}`}
            onClick={() => aoAlternarPausa(item)}
          >
            {visual.paused ? "Retomar" : "Pausar"}
          </button>
          <button
            type="button"
            className="btn-action"
            aria-label={`Remover ${visual.symbol}`}
            onClick={() => aoRemover(item)}
          >
            Remover
          </button>
        </td>
      </tr>
      {/* Sub-linha D-06: oculta no desktop, revelada <= 600px pela media
          query de alerts.css (o conteúdo dobra distância + horas). */}
      <tr className="sub-linha-row">
        <td colSpan={COLUNAS} className="sub-linha">
          <span>distância {dist.texto}</span>
          <span>atualização {formatTime(visual.price_at)}</span>
          <span>disparo {formatTime(visual.alerted_at)}</span>
        </td>
      </tr>
    </>
  );
}

function SecaoMercado({
  titulo,
  itens,
  vazia,
  carregando,
  emUndo,
  alvoDe,
  valorAlvoDe,
  erroDe,
  aoEditarAlvo,
  aoFocarAlvo,
  aoDesfocarAlvo,
  aoAlternarPausa,
  aoRemover,
  aoDesfazer,
}: {
  titulo: string;
  itens: WatchItem[];
  vazia: string;
  carregando: boolean;
  emUndo: Set<string>;
  alvoDe: (item: WatchItem) => number;
  valorAlvoDe: (item: WatchItem) => string;
  erroDe: (item: WatchItem) => string | null;
  aoEditarAlvo: (item: WatchItem, valor: string) => void;
  aoFocarAlvo: (item: WatchItem) => void;
  aoDesfocarAlvo: (item: WatchItem) => void;
  aoAlternarPausa: (item: WatchItem) => void;
  aoRemover: (item: WatchItem) => void;
  aoDesfazer: (item: WatchItem) => void;
}) {
  return (
    <section className="market-section">
      <h2 className="market-title">{titulo}</h2>
      <div className="table-wrap">
        <table>
          <CabecalhoTabela />
          <tbody>
            {carregando ? null : itens.length === 0 && emUndo.size === 0 ? (
              <tr>
                <td colSpan={COLUNAS} className="empty-state">
                  {vazia}
                </td>
              </tr>
            ) : (
              itens.map((item) =>
                emUndo.has(chave(item)) ? (
                  <LinhaUndo
                    key={chave(item)}
                    item={item}
                    desfazer={() => aoDesfazer(item)}
                  />
                ) : (
                  <LinhaItem
                    key={chave(item)}
                    item={item}
                    alvo={alvoDe(item)}
                    valorAlvo={valorAlvoDe(item)}
                    erroSalva={erroDe(item)}
                    aoEditarAlvo={aoEditarAlvo}
                    aoFocarAlvo={aoFocarAlvo}
                    aoDesfocarAlvo={aoDesfocarAlvo}
                    aoAlternarPausa={aoAlternarPausa}
                    aoRemover={aoRemover}
                  />
                ),
              )
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// Página de alertas (UI-01): leitura agrupada + o ciclo completo de edição —
// adicionar (lista local, pendência do lote), editar alvo inline, remover com
// "Desfazer" de 5 s (timer POR item), Salvar executando os DELETE pendentes e
// SÓ ENTÃO o PUT em lote (que NUNCA remove, D-07) — e pausar/retomar por
// PATCH imediato (D-09). Alterações vivem SÓ em memória (sem storage).
export default function AlertsPage() {
  const [itens, setItens] = useState<WatchItem[] | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  // Estado de edição (molde setDirty do mockup, portado para React):
  const [edicoes, setEdicoes] = useState<Map<string, number>>(new Map()); // alvos válidos editados (chave → alvo)
  const [rascunhosAlvo, setRascunhosAlvo] = useState<Map<string, string>>(new Map()); // texto cru do input (inválido fica só no rascunho)
  const [novos, setNovos] = useState<Set<string>>(new Set()); // itens só-locais (vão no PUT como criação)
  const [emUndo, setEmUndo] = useState<Set<string>>(new Set()); // remoções na janela de 5 s (linha .row-undo)
  const [pendenciasRemocao, setPendenciasRemocao] = useState<Set<string>>(new Set()); // remoções confirmadas (DELETE no Salvar)
  const [salvando, setSalvando] = useState(false);
  const [salvoAgora, setSalvoAgora] = useState(false);
  const [erroBarra, setErroBarra] = useState<string | null>(null);
  const [errosSalvar, setErrosSalvar] = useState<Map<string, string>>(new Map()); // envelope D-08 por symbol
  const [novo, setNovo] = useState<{ symbol: string; market: Market; alvo: string }>({
    symbol: "",
    market: "b3",
    alvo: "",
  });
  const [erroNovo, setErroNovo] = useState<string | null>(null);
  const [sessaoExpirada, setSessaoExpirada] = useState(false);
  const timersUndo = useRef<Map<string, number>>(new Map());
  // Instantâneo do estado de edição lido pelo tick do polling (padrão
  // latest-ref: o foco vive SÓ aqui — focar não re-renderiza).
  const estadoRef = useRef({
    itens: null as WatchItem[] | null,
    edicoes: new Map<string, number>(),
    rascunhos: new Map<string, string>(),
    emUndo: new Set<string>(),
    pendencias: new Set<string>(),
    novos: new Set<string>(),
    foco: null as string | null,
  });
  // Espelho do estado de edição lido pelo tick do polling (padrão
  // latest-ref: o foco vive SÓ aqui — focar não re-renderiza). A escrita do
  // espelho acontece APÓS o commit (react-hooks/refs: nada de ref durante o
  // render); o intervalo e o re-login leem o instantâneo já consolidado.
  useEffect(() => {
    estadoRef.current.itens = itens;
    estadoRef.current.edicoes = edicoes;
    estadoRef.current.rascunhos = rascunhosAlvo;
    estadoRef.current.emUndo = emUndo;
    estadoRef.current.pendencias = pendenciasRemocao;
    estadoRef.current.novos = novos;
  });

  useEffect(() => {
    let vivo = true;
    void listarItens()
      .then((resposta) => {
        if (vivo) setItens(resposta.itens);
      })
      .catch(() => {
        // 401 vai pelo handler global; erro de rede preserva o estado atual.
      });
    void buscarStatus()
      .then((estado) => {
        if (vivo) setStatus(estado);
      })
      .catch(() => {
        // Sem /api/status não há meta-line nem banner — a lista segue.
      });
    return () => {
      vivo = false;
    };
  }, []);

  // 401 → modal de re-login NO LUGAR (D-10): enquanto a página de alertas
  // está montada, ELA está no TOPO da pilha restaurativa (empilha o próprio
  // handler sobre o default do App — a ordem de efeitos pai/filho deixa de
  // importar e nenhum re-registro por navegação sobrescreve o modal); o
  // restore no unmount DEVOLVE o dono anterior (o default do App), nunca
  // deixa o slot sem dono.
  useEffect(() => {
    return pushUnauthorized(() => setSessaoExpirada(true));
  }, []);

  /** Carga de watch+status com merge determinístico: itens com alvo editado
   * não salvo, rascunho em edição, input focado ou pendência local preservam
   * o estado local (alvo/existência) e atualizam SÓ os campos somente-leitura;
   * itens sem pendência substituem integralmente; itens novos entram;
   * remoções pendentes não ressuscitam. */
  async function recarregar() {
    const atual = estadoRef.current;
    try {
      const [respostaWatch, respostaStatus] = await Promise.all([
        listarItens(),
        buscarStatus(),
      ]);
      setItens((anteriorLista) => {
        const locais = new Map(
          (anteriorLista ?? []).map((i) => [chave(i), i] as const),
        );
        const mesclados: WatchItem[] = [];
        for (const itemServidor of respostaWatch.itens) {
          const chaveItem = chave(itemServidor);
          if (atual.pendencias.has(chaveItem) || atual.emUndo.has(chaveItem)) {
            continue; // remoção local não ressuscita
          }
          const itemLocal = locais.get(chaveItem);
          const protegido =
            itemLocal !== undefined &&
            (atual.novos.has(chaveItem) ||
              atual.edicoes.has(chaveItem) ||
              atual.rascunhos.has(chaveItem) ||
              atual.foco === chaveItem);
          if (itemLocal !== undefined && protegido) {
            // alvo editado/focado fica LOCAL; os campos somente-leitura da
            // resposta atualizam (preço, estados, horas).
            mesclados.push({
              ...itemServidor,
              target_below:
                atual.edicoes.get(chaveItem) ?? itemLocal.target_below,
            });
          } else {
            mesclados.push(itemServidor);
          }
        }
        // Itens só-locais (adicionados e ainda não salvos) permanecem na lista.
        const noServidor = new Set(respostaWatch.itens.map(chave));
        for (const itemLocal of anteriorLista ?? []) {
          const chaveItem = chave(itemLocal);
          if (noServidor.has(chaveItem)) continue;
          if (atual.pendencias.has(chaveItem) || atual.emUndo.has(chaveItem)) {
            continue;
          }
          mesclados.push(itemLocal);
        }
        return mesclados;
      });
      setStatus(respostaStatus);
    } catch {
      // 401 vai pelo handler global (modal); erro de rede preserva o estado.
    }
  }

  // Polling de 60 s (arquitetura §Frontend): refaz /api/watch + /api/status e
  // NUNCA sobrescreve edição em curso nem alteração não salva — o merge é
  // determinístico (T-05-10). Com o modal de re-login aberto, o tick não executa.
  // (Declarado DEPOIS de recarregar: o lint react-hooks reprova acesso a
  // variável antes da declaração.)
  useEffect(() => {
    if (sessaoExpirada) return; // guard: polling pausado enquanto o modal está aberto
    const id = window.setInterval(() => {
      void recarregar();
    }, INTERVALO_POLLING_MS);
    return () => window.clearInterval(id);
  }, [sessaoExpirada]);

  /** Fechou o modal por SUCESSO no re-login: recarrega na hora e o efeito do
   * intervalo retoma o ciclo de 60 s (a mudança de sessaoExpirada religa). */
  function aposRelogar() {
    setSessaoExpirada(false);
    void recarregar();
  }

  // Timers de undo são cancelados no unmount (sem vazamento, sem setState pós-morte).
  useEffect(() => {
    const timers = timersUndo.current;
    return () => {
      for (const timer of timers.values()) window.clearTimeout(timer);
      timers.clear();
    };
  }, []);

  const dirty =
    edicoes.size > 0 || pendenciasRemocao.size > 0 || novos.size > 0;

  function editarAlvo(itemDaLinha: WatchItem, valor: string) {
    const chaveItem = chave(itemDaLinha);
    setRascunhosAlvo((anterior) => new Map(anterior).set(chaveItem, valor));
    setSalvoAgora(false);
    if (valor.trim() === "") return; // NaN: não marca dirty
    const numero = Number(valor);
    if (!Number.isFinite(numero) || numero < 0) return; // inválido: só o rascunho
    setEdicoes((anterior) => new Map(anterior).set(chaveItem, numero));
    setErrosSalvar((anterior) => {
      if (!anterior.has(itemDaLinha.symbol)) return anterior;
      const proximo = new Map(anterior);
      proximo.delete(itemDaLinha.symbol);
      return proximo;
    });
  }

  function remover(itemDaLinha: WatchItem) {
    const chaveItem = chave(itemDaLinha);
    setSalvoAgora(false);
    setEmUndo((anterior) => new Set(anterior).add(chaveItem));
    // Timer POR item (molde 590–602): expirado, a linha sai da lista local e a
    // chave vira pendência de DELETE — remoção persiste SÓ por DELETE.
    const timer = window.setTimeout(() => {
      timersUndo.current.delete(chaveItem);
      setEmUndo((anterior) => {
        const proximo = new Set(anterior);
        proximo.delete(chaveItem);
        return proximo;
      });
      setItens((anterior) =>
        (anterior ?? []).filter((i) => chave(i) !== chaveItem),
      );
      setPendenciasRemocao((anterior) => new Set(anterior).add(chaveItem));
    }, JANELA_UNDO_MS);
    timersUndo.current.set(chaveItem, timer);
  }

  function desfazer(itemDaLinha: WatchItem) {
    const chaveItem = chave(itemDaLinha);
    const timer = timersUndo.current.get(chaveItem);
    if (timer !== undefined) window.clearTimeout(timer);
    timersUndo.current.delete(chaveItem);
    setEmUndo((anterior) => {
      const proximo = new Set(anterior);
      proximo.delete(chaveItem);
      return proximo;
    });
    // SEM rede: o item nunca saiu de itens — restaura na hora.
  }

  async function alternarPausa(itemDaLinha: WatchItem) {
    try {
      // PATCH imediato (D-09): a resposta é o WatchItem completo que substitui
      // a linha — o item pausado segue visível com estado "pausado".
      const resposta = await patchPause(
        itemDaLinha.symbol,
        itemDaLinha.market,
        !itemDaLinha.paused,
      );
      setItens((anterior) =>
        (anterior ?? []).map((i) =>
          chave(i) === chave(resposta) ? resposta : i,
        ),
      );
    } catch {
      // 401 vai pelo handler global; falha de pausa preserva a linha como está.
    }
  }

  function adicionar(evento: FormEvent) {
    evento.preventDefault();
    const symbol = novo.symbol.trim().toUpperCase(); // espelho do _normalizar do servidor
    if (!symbol) {
      setErroNovo("informe o ticker do ativo");
      return;
    }
    if (novo.alvo.trim() === "" || !Number.isFinite(Number(novo.alvo)) || Number(novo.alvo) <= 0) {
      setErroNovo("o alvo deve ser maior que zero");
      return;
    }
    const chaveNova = chaveDe(symbol, novo.market);
    const duplicado =
      novos.has(chaveNova) ||
      (itens ?? []).some(
        (i) => chave(i) === chaveNova && !emUndo.has(chave(i)),
      );
    if (duplicado) {
      // Erro inline no lugar do alert() do mockup (adaptação acordada no CONTEXT).
      setErroNovo("Este ticker já está na lista.");
      return;
    }
    setItens((anterior) => [
      ...(anterior ?? []),
      {
        symbol,
        market: novo.market,
        target_below: Number(novo.alvo),
        paused: false,
        currency: "",
        price: null,
        price_at: null,
        change_pct: null,
        alerted: false,
        alerted_at: null,
        notify_state: null,
        fresh: "none",
        suspect: false,
      },
    ]);
    setNovos((anterior) => new Set(anterior).add(chaveNova));
    setErroNovo(null);
    setSalvoAgora(false);
    setNovo((anterior) => ({ ...anterior, symbol: "", alvo: "" }));
  }

  async function salvar() {
    if (!dirty || salvando || itens === null) return;
    setSalvoAgora(false);
    // Molde 676–681: o Salvar FLUSHA os timers de undo — o que está na janela
    // é confirmado como remoção agora (vai para o DELETE, nunca para o PUT).
    const chavesEmJanela = [...emUndo];
    for (const timer of timersUndo.current.values()) window.clearTimeout(timer);
    timersUndo.current.clear();
    const chavesApagadas = [...pendenciasRemocao, ...chavesEmJanela];
    const itensRestantes = itens.filter(
      (i) => !chavesApagadas.includes(chave(i)),
    );
    if (chavesEmJanela.length > 0) {
      setEmUndo(new Set());
      setItens(itensRestantes);
    }
    setSalvando(true);
    setErroBarra(null);
    try {
      // T-05-11: DELETE item a item, em sequência; qualquer falha interrompe
      // ANTES do PUT — remoção persiste SÓ por DELETE, NUNCA por PUT menor.
      for (const chaveApagada of chavesApagadas) {
        const [symbolApagado, marketApagado] = chaveApagada.split("|");
        await removerItem(symbolApagado, marketApagado as Market);
      }
      const resposta = await salvarLote(
        itensRestantes.map((i) => ({
          symbol: i.symbol,
          market: i.market,
          target_below: edicoes.get(chave(i)) ?? i.target_below,
        })),
      );
      // A resposta do PUT É a nova lista renderizada (watch.py devolve a lista completa).
      setItens(resposta.itens);
      setNovos(new Set());
      setPendenciasRemocao(new Set());
      setEmUndo(new Set());
      setEdicoes(new Map());
      setRascunhosAlvo(new Map());
      setErrosSalvar(new Map());
      setSalvoAgora(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        // Envelope D-08: o problema de cada item renderiza inline junto ao
        // ticker nomeado; o estado permanece dirty (parser ÚNICO do client).
        const erros = new Map<string, string>();
        for (const entrada of parseErrosEnvelope(e.body)) {
          if (entrada.symbol !== null) {
            erros.set(entrada.symbol, `${entrada.campo}: ${entrada.problema}`);
          }
        }
        setErrosSalvar(erros);
      } else if (e instanceof ApiError) {
        setErroBarra(e.message);
      } else {
        setErroBarra("Erro de rede ao salvar");
      }
    } finally {
      setSalvando(false);
    }
  }

  const carregando = itens === null;
  const lista = itens ?? [];
  const b3 = lista.filter((item) => item.market === "b3");
  const cripto = lista.filter((item) => item.market === "crypto");
  const problemas = status === null ? [] : problemasDoStatus(status);
  const alvoDe = (itemDaLinha: WatchItem): number =>
    edicoes.get(chave(itemDaLinha)) ?? itemDaLinha.target_below;
  const valorAlvoDe = (itemDaLinha: WatchItem): string => {
    const chaveItem = chave(itemDaLinha);
    return rascunhosAlvo.get(chaveItem) ?? String(alvoDe(itemDaLinha));
  };

  const statusBarra = salvando
    ? "Salvando..."
    : erroBarra !== null
      ? erroBarra
      : dirty
        ? "Alterações não salvas"
        : salvoAgora
          ? "Alterações salvas com sucesso"
          : "Tudo salvo";

  const propsSecao = {
    emUndo,
    alvoDe,
    valorAlvoDe,
    erroDe: (itemDaLinha: WatchItem) => errosSalvar.get(itemDaLinha.symbol) ?? null,
    aoEditarAlvo: editarAlvo,
    aoFocarAlvo: (itemDaLinha: WatchItem) => {
      estadoRef.current.foco = chave(itemDaLinha);
    },
    aoDesfocarAlvo: (itemDaLinha: WatchItem) => {
      if (estadoRef.current.foco === chave(itemDaLinha)) {
        estadoRef.current.foco = null;
      }
    },
    aoAlternarPausa: (itemDaLinha: WatchItem) => void alternarPausa(itemDaLinha),
    aoRemover: remover,
    aoDesfazer: desfazer,
  };

  return (
    <main className="container">
      <AppHeader meta={status === null ? "Aguardando dados..." : metaDoStatus(status)} />
      {problemas.length > 0 ? (
        <div className="banner-error visivel" role="alert">
          Aviso do sistema: {problemas.join("; ")}
        </div>
      ) : null}
      <SecaoMercado
        titulo="Ações (B3)"
        itens={b3}
        vazia="Nenhuma ação monitorada. Use o formulário abaixo."
        carregando={carregando}
        {...propsSecao}
      />
      <SecaoMercado
        titulo="Cripto"
        itens={cripto}
        vazia="Nenhuma criptomoeda monitorada. Use o formulário abaixo."
        carregando={carregando}
        {...propsSecao}
      />

      {/* Novo alerta (molde .add-block): fica SÓ na lista local e entra no
          PUT do Salvar — a validação na fonte vale no Salvar (decisão do plano). */}
      <section className="add-block">
        <h2>Novo alerta</h2>
        <form
          className="add-form"
          onSubmit={adicionar}
          noValidate
          aria-label="Novo alerta"
        >
          <div>
            <label htmlFor="novo-ticker" className="rotulo-sr">
              Novo ticker
            </label>
            <input
              id="novo-ticker"
              type="text"
              placeholder="Ticker (ex: ITUB4)"
              autoComplete="off"
              autoCapitalize="characters"
              value={novo.symbol}
              onChange={(evento) =>
                setNovo((anterior) => ({ ...anterior, symbol: evento.target.value }))
              }
            />
          </div>
          <div>
            <label htmlFor="novo-mercado" className="rotulo-sr">
              Mercado
            </label>
            <select
              id="novo-mercado"
              value={novo.market}
              onChange={(evento) =>
                setNovo((anterior) => ({
                  ...anterior,
                  market: evento.target.value as Market,
                }))
              }
            >
              <option value="b3">Ações (B3)</option>
              <option value="crypto">Cripto</option>
            </select>
          </div>
          <div>
            <label htmlFor="novo-alvo" className="rotulo-sr">
              Novo alvo
            </label>
            <input
              id="novo-alvo"
              type="number"
              step="any"
              min="0"
              placeholder="Alvo em R$"
              value={novo.alvo}
              onChange={(evento) =>
                setNovo((anterior) => ({ ...anterior, alvo: evento.target.value }))
              }
            />
          </div>
          <button type="submit">Adicionar</button>
        </form>
        {erroNovo !== null ? (
          <p className="erro-campo" role="alert">
            {erroNovo}
          </p>
        ) : null}
      </section>

      {/* Barra de salvar fixa (molde .action-bar): Salvar habilita só com
          pendências; aria-live anuncia o status (contrato de interação). */}
      <div className="action-bar">
        <div className="action-bar-content">
          <span
            className={
              "status-msg" + (dirty || erroBarra !== null ? " dirty" : "")
            }
            role="status"
            aria-live="polite"
          >
            {statusBarra}
          </span>
          <button
            type="button"
            className={"btn-save" + (dirty ? " active" : "")}
            disabled={!dirty || salvando}
            onClick={() => void salvar()}
          >
            Salvar
          </button>
        </div>
      </div>

      {/* D-10: sessão expirada no meio da edição — o modal reautentica NO
          LUGAR; as alterações seguem em memória e o polling retoma depois. */}
      {sessaoExpirada ? <ReloginModal aoRelogar={aposRelogar} /> : null}
    </main>
  );
}
