import { NavLink, useNavigate } from "react-router";
import { request } from "../api/client";

// Cabeçalho comum (D-12): h1 serif + abas "Alertas · Status" na linguagem
// .market-title do mockup (uppercase, muted, espaçado) e "Sair" à direita.
// O `meta` opcional é a meta-line derivada do /api/status (D-03/D-17).
export default function AppHeader({ meta }: { meta?: string }) {
  const navigate = useNavigate();

  async function sair() {
    try {
      // Sem corpo — contrato WR-01: logout não envia Content-Type.
      await request<void>("/api/auth/logout", { method: "POST" });
    } catch {
      // 401 já dispara o handler global; falha de logout não impede a saída.
    }
    navigate("/login");
  }

  return (
    <header>
      <h1>Alertas de preço</h1>
      {meta !== undefined ? <div className="meta-line">{meta}</div> : null}
      <nav className="abas" aria-label="Seções">
        <NavLink to="/" end>
          Alertas
        </NavLink>
        <NavLink to="/status">Status</NavLink>
        <button type="button" className="btn-sair" onClick={() => void sair()}>
          Sair
        </button>
      </nav>
    </header>
  );
}
