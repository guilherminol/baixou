// Formatação PT-BR — portagem tipada das linhas 423–443 do mockup
// (05-UI-MODELO.md). Única mudança acordada: formatCurrency recebe a moeda do
// item (currency da API — "R$" quando currency == "R$", "US$" caso contrário)
// em vez do R$ fixo. \u00A0 = NBSP antes de % e depois do símbolo.

export function formatCurrency(
  val: number | null | undefined,
  currency: string,
): string {
  if (val === null || val === undefined) return "—";
  const simbolo = currency === "R$" ? "R$" : "US$";
  return (
    simbolo +
    "\u00A0" +
    Number(val).toLocaleString("pt-BR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

export function formatPercent(val: number): string {
  const sinal = val > 0 ? "+" : "−";
  const absVal = Math.abs(val).toLocaleString("pt-BR", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
  return `${sinal}${absVal}\u00A0%`;
}

export function formatTime(isoStr: string | null | undefined): string {
  if (!isoStr) return "—";
  return new Date(isoStr).toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeAgoMinutes(isoStr: string): number {
  const diffMs = Date.now() - new Date(isoStr).getTime();
  return Math.max(0, Math.round(diffMs / 60000));
}
