import { formatDateTime, formatUsd } from "../lib/format";

// trade_journal.py writes raw Binance fills (real account activity),
// not a demo/backtest artifact -- unlike backtest*.json, this file is
// never meant to be committed (see dashboard/.gitignore) and has to be
// copied in locally, so a missing file is the *expected* state on a
// fresh checkout, not an error to alarm about. `trades` is fetched once
// per tab in AssetPanel (it also needs it to derive the open position)
// and passed down rather than fetched again here. `journalUrl` is that
// same tab's data file, used only to name the exact path in the empty
// state -- each bot/symbol writes its own file (see README).
export default function LiveTradesPanel({ trades, journalUrl }) {
  if (!trades || trades.length === 0) {
    const filename = journalUrl.split("/").pop();
    return (
      <div className="panel">
        <div className="panel__header">
          <h2>Historial real de operaciones</h2>
        </div>
        <p className="text-dim">
          Esto es lo que el bot <strong>hizo de verdad</strong> contra Testnet — no un backtest.
          Todavía no hay nada acá porque <code>data/{filename}</code> (donde{" "}
          <code>main.py --trade</code> lo va guardando) no se copió al dashboard.
        </p>
        <p className="text-dim">
          Si corrés el bot con <code>scripts/run_trade_cycle.sh</code> (cron o el timer de
          systemd), esto se copia solo después de cada ciclo — solo hace falta esperar la próxima
          corrida y refrescar. Si preferís verlo ya, o corrés <code>main.py --trade</code> directo
          sin el wrapper, copiá el archivo a mano:
        </p>
        <pre className="code-block">
          cp data/{filename} dashboard/public/data/{filename}
        </pre>
        <p className="text-dim">
          No se commitea al repo — son datos reales de tu cuenta.
        </p>
      </div>
    );
  }

  const sorted = [...trades].sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0));

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>Historial real de operaciones ({trades.length})</h2>
        <span className="text-dim panel__note">contra Testnet — no es un backtest</span>
      </div>
      <div className="table-scroll">
        <table className="trades-table">
          <thead>
            <tr>
              <th>Fecha</th>
              <th>Símbolo</th>
              <th>Lado</th>
              <th>Precio</th>
              <th>Cantidad</th>
              <th>Costo</th>
              <th>Comisión</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((t) => (
              <tr key={t.id}>
                <td>{formatDateTime(t.datetime)}</td>
                <td>{t.symbol ?? "—"}</td>
                <td>
                  <span className={`tag ${t.side === "buy" ? "tag--positive" : "tag--negative"}`}>
                    {t.side === "buy" ? "Compra" : "Venta"}
                  </span>
                </td>
                <td>{formatUsd(t.price)}</td>
                <td>{t.amount ?? "—"}</td>
                <td>{formatUsd(t.cost)}</td>
                <td>{t.fee ? `${t.fee.cost ?? "—"} ${t.fee.currency ?? ""}` : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
