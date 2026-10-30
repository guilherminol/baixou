import { useEffect, useRef, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from "react-router";
import type { operations } from "./api/generated/schema";
import { pushUnauthorized, request } from "./api/client";
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
  // Espelho do navigate — escrito em efeito PÓS-commit (precedente do espelho
  // do AlertsPage: react-hooks/refs reprova escrita de ref no render); o
  // handler default abaixo lê por aqui o navigate atual em cada 401.
  const navigateRef = useRef(navigate);
  useEffect(() => {
    navigateRef.current = navigate;
  });

  // Posse restaurativa do handler 401 (D-10; Gap 1 do 05-VERIFICATION): o
  // redirect default é registrado UMA VEZ na vida do Rotas (deps []) e a
  // página montada depois EMPILHA o próprio handler por cima
  // (pushUnauthorized) — o restore do unmount dela devolve este default ao
  // topo. O efeito antigo [navigate] re-registrava o default a cada location
  // (o useNavigateUnstable do react-router 7 recria `navigate`) e, na ordem
  // pai/filho do mesmo commit, sobrescrevia o handler do modal em toda volta
  // de navegação para / — exatamente a causa do Gap 1, que não volta.
  useEffect(
    () => pushUnauthorized(() => navigateRef.current("/login", { replace: true })),
    [],
  );

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
