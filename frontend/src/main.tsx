import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
// Design system do mockup (D-01) — extraído como arquivo externo pelo build
// (a CSP 'self' sem unsafe-inline exige style/link externos, nunca inline).
import "./styles/global.css";
import App from "./App.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
