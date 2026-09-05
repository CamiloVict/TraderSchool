import { useEffect, useState } from "react";

import { formatDateTime, formatUsd } from "../lib/format";

const DATA_DIR = "/data";

// trade_journal.py writes raw Binance fills (real account activity),
// not a demo/backtest artifact -- unlike backtest.json, this file is
// never meant to be committed (see dashboard/.gitignore) and has to be
// copied in locally, so a missing file is the *expected* state on a
// fresh checkout, not an error to alarm about.
export default function LiveTradesPanel() {
  const [trades, setTrades] = useState(null);

  useEffect(() => {
    fetch(`${DATA_DIR}/trade_journal.json`)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setTrades);
  }, []);

  if (!trades || trades.length === 0) {
    return (
      <div className="panel">
        <div className="panel__header">
          <h2>Historial real de operaciones</h2>
        </div>
        <p className="text-dim">
          Esto es lo que el bot <strong>hizo de verdad</strong> contra Testnet — no un backtest.
          Todavía no hay nada acá porque <code>data/trade_journal.json</code> (donde{" "}
          <code>main.py --trade</code> lo va guardando) no se copió al dashboard. Corré:
        </p>
        <pre className="code-block">
          cp data/trade_journal.json dashboard/public{DATA_DIR}/trade_journal.json
        </pre>
        <p className="text-dim">
          y refrescá — no se commitea al repo (son datos reales de tu cuenta), así que hay que
          repetir esto cada vez que quieras ver operaciones nuevas.
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
