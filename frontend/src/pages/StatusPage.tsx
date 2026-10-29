import AppHeader from "../components/AppHeader";

// Skeleton da tela de status — a tela completa (fontes D-14, canais D-15,
// cota D-16) é o plano 05-04. O shell comum (cabeçalho com abas) já é o
// definitivo.
export default function StatusPage() {
  return (
    <main className="container">
      <AppHeader />
      <p className="empty-state">
        Tela de status em construção — fontes, canais e cota chegam no plano
        05-04.
      </p>
    </main>
  );
}
