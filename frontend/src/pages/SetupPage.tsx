import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { ApiError, parseErrosEnvelope, request } from "../api/client";

// D-13: formulário ÚNICO — token do log, usuário, senha e confirmação, com
// erros inline PT-BR no envelope D-08. Ao criar (201) o cookie de sessão já
// foi setado PELO SERVIDOR: navegar para / já entra logado. O token existe
// SÓ no campo do formulário (nunca em storage nem URL — proibição do plano).
export default function SetupPage() {
  const navigate = useNavigate();
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmacao, setConfirmacao] = useState("");
  // Erro por CAMPO da API (token/username/password) + pseudo-campos locais
  // (confirmacao/formulario) — o valor é sempre o problema PT-BR.
  const [erros, setErros] = useState<Record<string, string>>({});

  async function submeter(evento: FormEvent) {
    evento.preventDefault();
    setErros({});
    // Divergência de senhas é erro LOCAL: nem chega a chamar a API.
    if (password !== confirmacao) {
      setErros({ confirmacao: "as senhas não coincidem" });
      return;
    }
    try {
      await request("/api/setup", {
        method: "POST",
        body: JSON.stringify({ token, username, password }),
      });
      navigate("/");
    } catch (erro) {
      if (erro instanceof ApiError) {
        // 422 no envelope D-08: o problema de cada campo vira erro inline
        // junto ao campo (mesmo parser compartilhado com o 05-03).
        const doEnvelope = parseErrosEnvelope(erro.body);
        if (doEnvelope.length > 0) {
          const mapa: Record<string, string> = {};
          for (const item of doEnvelope) {
            mapa[item.campo] = item.problema;
          }
          setErros(mapa);
          return;
        }
        if (erro.status === 401) {
          setErros({ token: "token inválido" });
          return;
        }
        if (erro.status === 403) {
          // Administrador já existe: a porta certa é o login.
          navigate("/login");
          return;
        }
      }
      setErros({ formulario: "erro ao criar o administrador — tente novamente" });
    }
  }

  return (
    <main className="container">
      <section className="add-block form-acesso">
        <h2>Criar administrador</h2>
        <form onSubmit={(evento) => void submeter(evento)} noValidate>
          <div className="campo">
            <label htmlFor="setup-token">Token do log</label>
            <input
              id="setup-token"
              value={token}
              onChange={(evento) => setToken(evento.target.value)}
              autoComplete="off"
              autoFocus
            />
            {erros["token"] !== undefined ? (
              <p className="erro-campo" role="alert">
                {erros["token"]}
              </p>
            ) : null}
            <p className="meta-line">veja o token com: docker compose logs web</p>
          </div>
          <div className="campo">
            <label htmlFor="setup-usuario">Usuário</label>
            <input
              id="setup-usuario"
              value={username}
              onChange={(evento) => setUsername(evento.target.value)}
              autoComplete="username"
            />
            {erros["username"] !== undefined ? (
              <p className="erro-campo" role="alert">
                {erros["username"]}
              </p>
            ) : null}
          </div>
          <div className="campo">
            <label htmlFor="setup-senha">Senha</label>
            <input
              id="setup-senha"
              type="password"
              value={password}
              onChange={(evento) => setPassword(evento.target.value)}
              autoComplete="new-password"
            />
            {erros["password"] !== undefined ? (
              <p className="erro-campo" role="alert">
                {erros["password"]}
              </p>
            ) : null}
          </div>
          <div className="campo">
            <label htmlFor="setup-confirmacao">Confirmação da senha</label>
            <input
              id="setup-confirmacao"
              type="password"
              value={confirmacao}
              onChange={(evento) => setConfirmacao(evento.target.value)}
              autoComplete="new-password"
            />
            {erros["confirmacao"] !== undefined ? (
              <p className="erro-campo" role="alert">
                {erros["confirmacao"]}
              </p>
            ) : null}
          </div>
          {erros["formulario"] !== undefined ? (
            <p className="banner-error visivel" role="alert">
              {erros["formulario"]}
            </p>
          ) : null}
          <button type="submit" className="btn-primario">
            Criar administrador
          </button>
        </form>
      </section>
    </main>
  );
}
