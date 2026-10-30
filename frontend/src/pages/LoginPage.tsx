import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api/client";
// D-10: a LoginPage consome a MESMA função de login do app que o ReloginModal
// — nenhuma tela fala direto com o client para autenticar.
import { entrar } from "../api/auth";

// Contagem no formato mm:ss do bloqueio (D-11).
function mmss(total: number): string {
  const minutos = Math.floor(total / 60).toString().padStart(2, "0");
  const segundos = (total % 60).toString().padStart(2, "0");
  return `${minutos}:${segundos}`;
}

// D-11: login com 401 inline (mesmo corpo para usuário inexistente) e
// bloqueio 429 em CONTAGEM REGRESSIVA derivada do Retry-After — o botão
// Entrar fica desabilitado até zerar e novas submissões são bloqueadas.
// Nada vai a localStorage/sessionStorage (proibição do plano).
export default function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [espera, setEspera] = useState(0); // segundos restantes do bloqueio

  const bloqueado = espera > 0;
  useEffect(() => {
    if (!bloqueado) return;
    const id = setInterval(() => {
      setEspera((atual) => (atual > 1 ? atual - 1 : 0));
    }, 1000);
    return () => clearInterval(id);
  }, [bloqueado]);

  async function submeter(evento: FormEvent) {
    evento.preventDefault();
    if (bloqueado) return;
    setErro(null);
    try {
      await entrar(username, password, remember);
      navigate("/");
    } catch (e) {
      if (e instanceof ApiError) {
        // A mensagem PT-BR do servidor (detail do contrato) vira o inline.
        setErro(e.message);
        if (e.status === 429 && e.retryAfterSeconds !== undefined) {
          setEspera(e.retryAfterSeconds);
        }
        return;
      }
      setErro("erro de rede — tente novamente");
    }
  }

  return (
    <main className="container">
      <section className="add-block form-acesso">
        <h2>Entrar</h2>
        <form onSubmit={(evento) => void submeter(evento)} noValidate>
          <div className="campo">
            <label htmlFor="login-usuario">Usuário</label>
            <input
              id="login-usuario"
              value={username}
              onChange={(evento) => setUsername(evento.target.value)}
              autoComplete="username"
              autoFocus
            />
          </div>
          <div className="campo">
            <label htmlFor="login-senha">Senha</label>
            <input
              id="login-senha"
              type="password"
              value={password}
              onChange={(evento) => setPassword(evento.target.value)}
              autoComplete="current-password"
            />
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={remember}
              onChange={(evento) => setRemember(evento.target.checked)}
            />
            lembrar de mim
          </label>
          {erro !== null ? (
            <p className="erro-campo" role="alert">
              {erro}
            </p>
          ) : null}
          {bloqueado ? (
            <p className="erro-campo" role="status">
              novas tentativas liberadas em {mmss(espera)}
            </p>
          ) : null}
          <button type="submit" className="btn-primario" disabled={bloqueado}>
            Entrar
          </button>
        </form>
      </section>
    </main>
  );
}
