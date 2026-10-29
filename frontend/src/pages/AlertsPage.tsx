import { useEffect, useState } from "react";
import AppHeader from "../components/AppHeader";
import { buscarStatus, type StatusResponse } from "../api/status";
import { timeAgoMinutes } from "../lib/format";

// Meta-line do cabeçalho (formato do mockup): "Última consulta há X min ·
// próxima em Y min · cota Z/W" — tudo derivado do /api/status (D-03).
function metaDoStatus(status: StatusResponse): string {
  const ultima = status.ultima_rodada
    ? `há ${timeAgoMinutes(status.ultima_rodada)} min`
    : "—";
  const proxima = Math.max(
    0,
    Math.round((new Date(status.proxima_rodada).getTime() - Date.now()) / 60000),
  );
  return `Última consulta ${ultima} · próxima em ${proxima} min · cota ${status.cota.usadas}/${status.cota.limite}`;
}

// Skeleton da página de alertas — a lista completa (itens, edição, Desfazer)
// é o plano 05-03. Aqui o caminho inteiro já é real: buscarStatus() alimenta
// a meta-line do cabeçalho comum (D-12/D-17 usam o mesmo endpoint).
export default function AlertsPage() {
  const [status, setStatus] = useState<StatusResponse | null>(null);

  useEffect(() => {
    let vivo = true;
    buscarStatus()
      .then((estado) => {
        if (vivo) setStatus(estado);
      })
      .catch(() => {
        // 401 já redire via handler global; erro de rede deixa o
        // "Aguardando dados..." — o banner de sistema é do plano 05-03.
      });
    return () => {
      vivo = false;
    };
  }, []);

  return (
    <main className="container">
      <AppHeader
        meta={status === null ? "Aguardando dados..." : metaDoStatus(status)}
      />
      <p className="empty-state">
        Nenhuma ação monitorada — a lista de alertas chega no plano 05-03.
      </p>
    </main>
  );
}
