import { useEffect, useState } from "react";

import KpiCard from "./components/KpiCard";
import LiveTradesPanel from "./components/LiveTradesPanel";
import MarketContext from "./components/MarketContext";
import PositionChart from "./components/PositionChart";
import { formatDateTime, formatPct, formatUsd } from "./lib/format";
import { deriveOpenPosition } from "./lib/position";

import "./App.css";

const DATA_DIR = "/data";
// Real OHLC history, exported by `python backtester.py --export ...`.
// Reused here purely as cached price history for the chart background
// — none of its strategy/metrics fields are read or shown anymore; see
// the "Falta para producción" history in README for why this dashboard
// moved from "backtest viewer" to "what is the live bot actually doing."
const CANDLES_URL = `${DATA_DIR}/backtest_paxg.json`;
const TRADE_JOURNAL_URL = `${DATA_DIR}/trade_journal.json`;
const CONTEXT_URL = `${DATA_DIR}/context_paxg.json`;

export default function App() {
  const [priceReport, setPriceReport] = useState(null);
  const [trades, setTrades] = useState(null);
  const [context, setContext] = useState(null);

  useEffect(() => {
    fetch(CANDLES_URL)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setPriceReport);
  }, []);

  useEffect(() => {
    fetch(TRADE_JOURNAL_URL)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setTrades);
  }, []);

  useEffect(() => {
    fetch(CONTEXT_URL)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setContext);
  }, []);

  const candles = priceReport?.candles ?? [];
  const symbol = priceReport?.symbol ?? context?.asset ?? "—";
  const timeframe = priceReport?.timeframe ?? "1h";

  const position = deriveOpenPosition(trades);
  const lastCandle = candles.length ? candles[candles.length - 1] : null;
  const currentPrice = lastCandle?.close;
  const pnlPct =
    position && currentPrice != null
      ? ((currentPrice - position.entryPrice) / position.entryPrice) * 100
      : null;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>Trading Bot Dashboard</h1>
          <p className="text-dim">
            {symbol} · {timeframe} · en vivo contra Testnet
          </p>
        </div>
        <div className="app-header__actions">
          {position ? (
            <span className="badge badge--positive">En posición</span>
          ) : trades ? (
            <span className="badge badge--neutral">Sin posición</span>
          ) : null}
        </div>
      </header>

      {position && (
        <section className="kpi-grid">
          <KpiCard label="Precio de entrada" value={formatUsd(position.entryPrice)} sublabel={formatDateTime(position.entryTime)} />
          <KpiCard
            label="Precio actual"
            value={currentPrice != null ? formatUsd(currentPrice) : "—"}
            sublabel="última vela cacheada, no un feed en vivo"
          />
          <KpiCard
            label="P&L no realizado"
            value={pnlPct != null ? formatPct(pnlPct) : "—"}
            tone={pnlPct != null ? (pnlPct >= 0 ? "positive" : "negative") : "neutral"}
          />
          <KpiCard label="Cantidad" value={position.amount ?? "—"} sublabel={position.symbol} />
        </section>
      )}

      <PositionChart candles={candles} trades={trades ?? []} position={position} />

      <LiveTradesPanel trades={trades} />

      <MarketContext context={context} />
    </div>
  );
}
