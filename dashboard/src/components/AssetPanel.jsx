import { useEffect, useState } from "react";

import KpiCard from "./KpiCard";
import LiveTradesPanel from "./LiveTradesPanel";
import MarketContext from "./MarketContext";
import PositionChart from "./PositionChart";
import { formatDateTime, formatPct, formatUsd } from "../lib/format";
import { deriveOpenPosition } from "../lib/position";

// Everything the dashboard shows for one traded symbol -- its own price
// chart, position, real trade history and market context. Pulled out of
// App.jsx so each tab (one per bot/symbol -- they run as separate crons
// with separate state, see README) mounts a fresh instance instead of
// one component juggling multiple data sets at once.
export default function AssetPanel({ candlesUrl, tradeJournalUrl, contextUrl }) {
  const [priceReport, setPriceReport] = useState(null);
  const [trades, setTrades] = useState(null);
  const [context, setContext] = useState(null);

  useEffect(() => {
    setPriceReport(null);
    fetch(candlesUrl)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setPriceReport);
  }, [candlesUrl]);

  useEffect(() => {
    setTrades(null);
    fetch(tradeJournalUrl)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setTrades);
  }, [tradeJournalUrl]);

  useEffect(() => {
    setContext(null);
    fetch(contextUrl)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setContext);
  }, [contextUrl]);

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
    <div className="asset-panel">
      <div className="asset-panel__header">
        <p className="text-dim">
          {symbol} · {timeframe} · en vivo contra Testnet
        </p>
        {position ? (
          <span className="badge badge--positive">En posición</span>
        ) : trades ? (
          <span className="badge badge--neutral">Sin posición</span>
        ) : null}
      </div>

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

      <LiveTradesPanel trades={trades} journalUrl={tradeJournalUrl} />

      <MarketContext context={context} />
    </div>
  );
}
