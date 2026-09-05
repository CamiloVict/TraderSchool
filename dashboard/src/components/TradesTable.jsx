import { formatDateTime, formatDuration, formatPct, formatUsd } from "../lib/format";

// exit_reason values differ by which engine produced the trade (EMA
// crossover vs. the Setup Engine's bias/no_trade exits) -- this maps
// every reason this repo's backtesters actually emit to a label,
// falling back to the raw value for anything unmapped instead of
// mislabeling it.
const EXIT_REASON_LABELS = {
  stop_loss: "Stop-loss",
  signal: "Señal EMA",
  bias_flip: "Cambio de sesgo",
  no_trade: "No trade",
  bearish_pattern: "Patrón bajista",
};

export default function TradesTable({ trades }) {
  if (!trades.length) {
    return (
      <div className="panel">
        <div className="panel__header">
          <h2>Operaciones</h2>
        </div>
        <p className="empty-state">Ninguna operación en el período backtesteado.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>Operaciones ({trades.length})</h2>
      </div>
      <div className="table-scroll">
        <table className="trades-table">
          <thead>
            <tr>
              <th>Entrada</th>
              <th>Precio entrada</th>
              <th>Salida</th>
              <th>Precio salida</th>
              <th>Duración</th>
              <th>Motivo salida</th>
              <th>Retorno</th>
            </tr>
          </thead>
          <tbody>
            {trades
              .slice()
              .reverse()
              .map((t) => {
                const isStopLoss = t.exit_reason === "stop_loss";
                return (
                  <tr key={`${t.entry_time}-${t.exit_time}`}>
                    <td>{formatDateTime(t.entry_time)}</td>
                    <td>{formatUsd(t.entry_price)}</td>
                    <td>{formatDateTime(t.exit_time)}</td>
                    <td>{formatUsd(t.exit_price)}</td>
                    <td>{formatDuration(t.entry_time, t.exit_time)}</td>
                    <td>
                      <span className={`tag ${isStopLoss ? "tag--warning" : "tag--neutral"}`}>
                        {EXIT_REASON_LABELS[t.exit_reason] ?? t.exit_reason}
                      </span>
                    </td>
                    <td className={t.return_pct >= 0 ? "positive" : "negative"}>
                      {formatPct(t.return_pct)}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
