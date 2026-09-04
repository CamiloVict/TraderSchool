import { formatDateTime, formatDuration, formatPct, formatUsd } from "../lib/format";

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
                        {isStopLoss ? "Stop-loss" : "Señal EMA"}
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
