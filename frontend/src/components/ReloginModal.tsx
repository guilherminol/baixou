import { useState, type FormEvent, type KeyboardEvent } from "react";
import { ApiError } from "../api/client";
import { entrar } from "../api/auth";

/**
 * Modal de re-login NO LUGAR (D-10): a sessão expirou no meio da edição — as
 * alterações continuam em memória (nunca em storage) e o polling da página
 * fica pausado enquanto o modal está aberto. Ele reautentica com a MESMA
 * função de login do app (`entrar`, compartilhada com a LoginPage) e devolve
 * o controle via `aoRelogar` SÓ no sucesso — não há fechar sem reentrar, o
 * modal é o próprio caminho de volta (UI-SPEC, state-pair "Sessão expirada").
 * O handler global do 401 é registrado pela página de alertas; este componente
 * só apresenta o formulário e consome a ApiError (401 → erro inline).
 */
export default function ReloginModal({ aoRelogar }: { aoRelogar: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  const [entrando, setEntrando] = useState(false);

  async function submeter(evento: FormEvent) {
    evento.preventDefault();
    if (entrando) return;
    setErro(null);
    setEntrando(true);
    try {
      await entrar(username, password, false);
      aoRelogar();
    } catch (e) {
      // 401 ("usuário ou senha inválidos") e demais falhas viram inline —
      // o modal permanece aberto para nova tentativa.
      if (e instanceof ApiError) setErro(e.message);
      else setErro("erro de rede — tente novamente");
    } finally {
      setEntrando(false);
    }
  }

  // Foco preso no modal (Tab/Shift+Tab ciclam dentro; o autoFocus abre no
  // usuário) — sem armadilha de foco a edição ficaria alcançável por trás.
  function aoTeclar(evento: KeyboardEvent<HTMLElement>) {
    if (evento.key !== "Tab") return;
    const focaveis = evento.currentTarget.querySelectorAll<HTMLElement>(
      "input, button",
    );
    if (focaveis.length === 0) return;
    const primeiro = focaveis[0];
    const ultimo = focaveis[focaveis.length - 1];
    if (evento.shiftKey && document.activeElement === primeiro) {
      evento.preventDefault();
      ultimo?.focus();
    } else if (!evento.shiftKey && document.activeElement === ultimo) {
      evento.preventDefault();
      primeiro?.focus();
    }
  }

  return (
    <div
      className="modal-fundo"
      role="presentation"
      onKeyDown={aoTeclar}
    >
      <section
        className="add-block modal-relogin"
        role="dialog"
        aria-modal="true"
        aria-labelledby="relogin-titulo"
      >
        <h2 id="relogin-titulo">Sessão expirada</h2>
        <p className="meta-line">
          Entre de novo para continuar — suas alterações continuam aqui.
        </p>
        <form onSubmit={(evento) => void submeter(evento)} noValidate>
          <div className="campo">
            <label htmlFor="relogin-usuario">Usuário</label>
            <input
              id="relogin-usuario"
              value={username}
              autoComplete="username"
              autoFocus
              onChange={(evento) => setUsername(evento.target.value)}
            />
          </div>
          <div className="campo">
            <label htmlFor="relogin-senha">Senha</label>
            <input
              id="relogin-senha"
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(evento) => setPassword(evento.target.value)}
            />
          </div>
          {erro !== null ? (
            <p className="erro-campo" role="alert">
              {erro}
            </p>
          ) : null}
          <button type="submit" className="btn-primario" disabled={entrando}>
            Entrar
          </button>
        </form>
      </section>
    </div>
  );
}
