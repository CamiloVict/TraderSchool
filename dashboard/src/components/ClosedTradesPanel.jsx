import { computeClosedTrades, countUnknownPnl, totalRealizedPnlUsd } from "../lib/pnl";
import { formatDateTime, formatPct, formatUsd } from "../lib/format";

// The real P&L counterpart to LiveTradesPanel's raw fills table: that
// table shows every fill as Binance recorded it (useful as an audit
// trail), this one pairs them into closed round trips and says
// plainly whether each one won or lost -- what "manejar mis finanzas"
// actually needs, not just "here's what happened."
export default function ClosedTradesPanel({ trades }) {
  const closed = computeClosedTrades(trades);

  if (closed.length === 0) {
    return null; // nothing closed yet -- LiveTradesPanel already covers the empty state for this journal
  }

  const totalPnl = totalRealizedPnlUsd(closed);
  const unknownCount = countUnknownPnl(closed);
  const sorted = [...closed].reverse();

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>P&L realizado ({closed.length} cerradas)</h2>
        <span className={`text-dim panel__note ${totalPnl >= 0 ? "positive" : "negative"}`}>
          total conocido: {formatUsd(totalPnl)}
          {unknownCount > 0 && ` · ${unknownCount} sin precio de entrada registrado`}
        </span>
      </div>
      <div className="table-scroll">
        <table className="trades-table">
          <thead>
            <tr>
              <th>Entrada</th>
              <th>Salida</th>
              <th>Precio entrada</th>
              <th>Precio salida</th>
              <th>Cantidad</th>
              <th>P&L</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((t) => (
              <tr key={`${t.entryTime ?? "unknown"}-${t.exitTime}`}>
                <td>{t.entryTime ? formatDateTime(t.entryTime) : <span className="text-faint">desconocida</span>}</td>
                <td>{formatDateTime(t.exitTime)}</td>
                <td>{t.entryPrice != null ? formatUsd(t.entryPrice) : "—"}</td>
                <td>{formatUsd(t.exitPrice)}</td>
                <td>{t.amount ?? "—"}</td>
                <td className={t.pnlPct == null ? "text-faint" : t.pnlPct >= 0 ? "positive" : "negative"}>
                  {t.pnlPct != null ? `${formatPct(t.pnlPct)} (${formatUsd(t.pnlUsd)})` : "sin dato"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
