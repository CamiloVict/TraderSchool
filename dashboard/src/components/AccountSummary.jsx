import { useEffect, useRef, useState } from "react";
import { createChart, LineSeries } from "lightweight-charts";

import { formatDateTime, formatUsd } from "../lib/format";
import { computeClosedTrades, countUnknownPnl, totalRealizedPnlUsd } from "../lib/pnl";
import { deriveOpenPosition } from "../lib/position";
import { estimatePortfolioValueSeries, latestBalances } from "../lib/portfolio";

const DATA_DIR = "/data";
const COLORS = { text: "#8b92a5", border: "#232733", blue: "#5b8def" };

function toUnixSeconds(ms) {
  return Math.floor(ms / 1000);
}

function useJson(url) {
  const [data, setData] = useState(null);
  useEffect(() => {
    fetch(url)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setData);
  }, [url]);
  return data;
}

// The cross-bot view: what does the account actually hold right now,
// what has it realized in P&L across both PAXG and BTC, and how has
// its total value moved over time -- questions neither per-symbol tab
// can answer alone, since each only ever sees its own trade journal.
export default function AccountSummary() {
  const balanceHistory = useJson(`${DATA_DIR}/balance_history.json`);
  const paxgTrades = useJson(`${DATA_DIR}/trade_journal.json`);
  const btcTrades = useJson(`${DATA_DIR}/trade_journal_btc.json`);
  const paxgReport = useJson(`${DATA_DIR}/backtest_paxg.json`);
  const btcReport = useJson(`${DATA_DIR}/backtest_btc.json`);

  const containerRef = useRef(null);

  const latest = latestBalances(balanceHistory);
  const paxgClosed = computeClosedTrades(paxgTrades);
  const btcClosed = computeClosedTrades(btcTrades);
  const allClosed = [...paxgClosed, ...btcClosed];
  const totalPnl = totalRealizedPnlUsd(allClosed);
  const unknownCount = countUnknownPnl(allClosed);

  const paxgPosition = deriveOpenPosition(paxgTrades);
  const btcPosition = deriveOpenPosition(btcTrades);
  const openPositions = [
    paxgPosition && { label: "Oro (PAXG)", ...paxgPosition },
    btcPosition && { label: "BTC", ...btcPosition },
  ].filter(Boolean);

  const candlesByCurrency = {
    PAXG: paxgReport?.candles ?? [],
    BTC: btcReport?.candles ?? [],
  };
  const valueSeries = estimatePortfolioValueSeries(balanceHistory, candlesByCurrency);

  useEffect(() => {
    if (!containerRef.current || valueSeries.length < 2) return undefined;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: COLORS.text, fontSize: 12 },
      grid: { vertLines: { color: COLORS.border }, horzLines: { color: COLORS.border } },
      rightPriceScale: { borderColor: COLORS.border },
      timeScale: { borderColor: COLORS.border, timeVisible: true, secondsVisible: false },
    });

    const series = chart.addSeries(LineSeries, { color: COLORS.blue, lineWidth: 2 });
    series.setData(valueSeries.map((v) => ({ time: toUnixSeconds(v.timestamp), value: v.totalUsd })));
    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [valueSeries]);

  if (!balanceHistory || balanceHistory.length === 0) {
    return (
      <div className="panel">
        <div className="panel__header">
          <h2>Resumen de cuenta</h2>
        </div>
        <p className="text-dim">
          Balance real de la cuenta, combinado entre todos los bots. Todavía no hay nada acá porque{" "}
          <code>data/balance_history.json</code> no se copió al dashboard.
        </p>
        <p className="text-dim">
          Si corrés el bot con <code>scripts/run_trade_cycle.sh</code> (cron o el timer de systemd), esto se
          copia solo después de cada ciclo — solo hace falta esperar la próxima corrida y refrescar.
        </p>
        <pre className="code-block">cp data/balance_history.json dashboard/public/data/balance_history.json</pre>
        <p className="text-dim">No se commitea al repo — son datos reales de tu cuenta.</p>
      </div>
    );
  }

  const hasUnpriced = valueSeries.some((v) => v.hasUnpriced);

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>Resumen de cuenta</h2>
        <span className="text-dim panel__note">{formatDateTime(latest.datetime)} · última corrida</span>
      </div>

      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-card__label">P&L realizado total</div>
          <div className={`kpi-card__value ${totalPnl >= 0 ? "positive" : "negative"}`}>{formatUsd(totalPnl)}</div>
          {unknownCount > 0 && (
            <div className="kpi-card__sublabel">{unknownCount} operación(es) sin precio de entrada</div>
          )}
        </div>
        <div className="kpi-card">
          <div className="kpi-card__label">Posiciones abiertas</div>
          <div className="kpi-card__value">{openPositions.length}</div>
          <div className="kpi-card__sublabel">
            {openPositions.length ? openPositions.map((p) => p.label).join(", ") : "ninguna"}
          </div>
        </div>
      </div>

      <h3 className="context-subtitle">Balances ({Object.keys(latest.balances ?? {}).length})</h3>
      <div className="table-scroll">
        <table className="trades-table">
          <thead>
            <tr>
              <th>Moneda</th>
              <th>Cantidad</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(latest.balances ?? {}).map(([currency, amount]) => (
              <tr key={currency}>
                <td>{currency}</td>
                <td>{amount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="context-subtitle">Valor total estimado (USD)</h3>
      {valueSeries.length < 2 ? (
        <p className="text-dim">
          Necesitás al menos dos corridas del cron con balance registrado para ver una curva.
        </p>
      ) : (
        <>
          <div ref={containerRef} style={{ height: 260 }} />
          {hasUnpriced && (
            <p className="text-faint panel__note" style={{ marginTop: 8 }}>
              Algunos tramos incluyen monedas sin precio cacheado (no PAXG/BTC) — el total esos días está
              subestimado.
            </p>
          )}
        </>
      )}
    </div>
  );
}
