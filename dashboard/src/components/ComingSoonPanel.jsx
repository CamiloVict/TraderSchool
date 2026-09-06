// A tab for a market this repo doesn't trade yet -- deliberately its
// own component instead of pointing AssetPanel at empty data files.
// AssetPanel's header hardcodes "en vivo contra Testnet" (true for
// every bot that actually exists today, all Binance/ccxt-based); a
// Forex tab with no broker connected yet and no Binance Testnet
// underneath it at all would make that text simply false. Better to
// say plainly what's built and what's still needed than to reuse a
// panel whose assumptions don't hold here.
export default function ComingSoonPanel({ market, steps }) {
  return (
    <div className="panel">
      <div className="panel__header">
        <h2>{market}</h2>
        <span className="badge badge--neutral">Todavía no conectado</span>
      </div>
      <p className="text-dim">
        Esta pestaña no tiene un bot corriendo todavía -- no hay operaciones, posición ni
        historial que mostrar porque no hay una cuenta real (ni siquiera demo) conectada del otro
        lado. Nada de lo que hay en las otras pestañas (Testnet de Binance) aplica acá.
      </p>
      {steps && steps.length > 0 && (
        <>
          <p className="text-dim">Lo que falta para que esto se active:</p>
          <ol className="text-dim">
            {steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
