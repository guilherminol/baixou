import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from "react-router";
import type { operations } from "./api/generated/schema";
import { onUnauthorized, request } from "./api/client";
import AlertsPage from "./pages/AlertsPage";
import StatusPage from "./pages/StatusPage";
import LoginPage from "./pages/LoginPage";
import SetupPage from "./pages/SetupPage";

// Resposta do GET /api/setup — GERADA do contrato congelado (D-13).
type RespostaSetup =
  operations["setup_status_api_setup_get"]["responses"][200]["content"]["application/json"];

function Rotas() {
  const navigate = useNavigate();
  // null = bootstrap em andamento; D-13: a rota certa aparece sozinha no
  // primeiro acesso (setup_pending → /setup).
  const [checando, setChecando] = useState(true);

  useEffect(() => {
    // Handler 401 default — o redirect para /login; a página de alertas
    // troca pelo modal de re-login no 05-03 (D-10).
    onUnauthorized(() => navigate("/login", { replace: true }));
    return () => onUnauthorized(null);
  }, [navigate]);

  useEffect(() => {
    let vivo = true;
    request<RespostaSetup>("/api/setup")
      .then((resposta) => {
        if (!vivo) return;
        if (resposta.setup_pending) {
          navigate("/setup", { replace: true });
        }
        setChecando(false);
      })
      .catch(() => {
        // Erro de rede: segue no fluxo normal (as páginas mostram o estado).
        if (vivo) setChecando(false);
      });
    return () => {
      vivo = false;
    };
  }, [navigate]);

  if (checando) {
    return (
      <main className="container">
        <p className="meta-line">Carregando…</p>
      </main>
    );
  }

  return (
    <Routes>
      <Route path="/" element={<AlertsPage />} />
      <Route path="/status" element={<StatusPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

// React Router em modo biblioteca (importado do pacote react-router —
// react-router-dom é legado): o FastAPI serve o index em qualquer rota
// (fallback SPA) e o Router resolve no navegador.
export default function App() {
  return (
    <BrowserRouter>
      <Rotas />
    </BrowserRouter>
  );
}
