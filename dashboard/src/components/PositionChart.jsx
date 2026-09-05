import { useEffect, useRef } from "react";
import { CandlestickSeries, createChart, createSeriesMarkers } from "lightweight-charts";

const COLORS = {
  text: "#8b92a5",
  border: "#232733",
  green: "#26a69a",
  red: "#ef5350",
  blue: "#5b8def",
};

function toUnixSeconds(iso) {
  return Math.floor(new Date(iso).getTime() / 1000);
}

// The price chart with what the bot actually did overlaid on it: real
// fills from trade_journal.json as markers, and — while a position is
// open — a line at the entry price so the current unrealized move is
// visible at a glance. No EMA, no backtest signal markers: those
// belonged to a specific strategy's backtest, not to "what happened."
export default function PositionChart({ candles, trades, position }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || !candles.length) return undefined;

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: COLORS.text, fontSize: 12 },
      grid: {
        vertLines: { color: COLORS.border },
        horzLines: { color: COLORS.border },
      },
      rightPriceScale: { borderColor: COLORS.border },
      timeScale: { borderColor: COLORS.border, timeVisible: true, secondsVisible: false },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.green,
      downColor: COLORS.red,
      borderVisible: false,
      wickUpColor: COLORS.green,
      wickDownColor: COLORS.red,
    });
    candleSeries.setData(
      candles.map((c) => ({
        time: toUnixSeconds(c.timestamp),
        open: c.open ?? c.close,
        high: c.high ?? c.close,
        low: c.low ?? c.close,
        close: c.close,
      }))
    );

    if (position) {
      candleSeries.createPriceLine({
        price: position.entryPrice,
        color: COLORS.blue,
        lineWidth: 2,
        lineStyle: 2, // dashed
        axisLabelVisible: true,
        title: "entrada",
      });
    }

    // Real fills only -- no text on the markers themselves (shape +
    // color already carry the meaning, and text overlaps badly once
    // there are more than a few trades on screen).
    const markers = (trades ?? [])
      .map((t) => ({
        time: toUnixSeconds(t.datetime),
        position: t.side === "buy" ? "belowBar" : "aboveBar",
        color: t.side === "buy" ? COLORS.green : COLORS.red,
        shape: t.side === "buy" ? "arrowUp" : "arrowDown",
      }))
      .sort((a, b) => a.time - b.time);
    createSeriesMarkers(candleSeries, markers);

    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [candles, trades, position]);

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>Precio</h2>
        <div className="legend-chip-row">
          <span className="legend-chip">
            <i style={{ background: COLORS.blue }} /> Entrada (posición abierta)
          </span>
          <span className="legend-chip">
            <i style={{ background: COLORS.green }} /> Compra real
          </span>
          <span className="legend-chip">
            <i style={{ background: COLORS.red }} /> Venta real
          </span>
        </div>
      </div>
      {candles.length ? (
        <div ref={containerRef} style={{ height: 420 }} />
      ) : (
        <p className="text-dim">
          Sin datos de precio todavía. Corré <code>python backtester.py --export ...</code> al
          menos una vez para cachear historial real, o esperá a que el bot acumule velas.
        </p>
      )}
    </div>
  );
}
