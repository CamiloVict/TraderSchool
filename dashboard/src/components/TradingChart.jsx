import { useEffect, useRef } from "react";
import { CandlestickSeries, LineSeries, createChart, createSeriesMarkers } from "lightweight-charts";

const COLORS = {
  text: "#8b92a5",
  border: "#232733",
  green: "#26a69a",
  red: "#ef5350",
  blue: "#5b8def",
  orange: "#f0a742",
};

function toUnixSeconds(iso) {
  return Math.floor(new Date(iso).getTime() / 1000);
}

export default function TradingChart({ candles, trades }) {
  const containerRef = useRef(null);
  // Setup Engine reports carry market_state/bias per candle instead of
  // an EMA pair -- ema_fast/ema_slow are simply absent there, so this
  // decides whether the EMA lines (and their legend chips) render at
  // all rather than plotting a flat line of `undefined`.
  const hasEma = candles.length > 0 && candles[0].ema_fast != null;

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
    // Older exports (before OHLC was added to the report) only carry
    // `close` — fall back to a flat (wick-less) candle so the chart
    // still renders instead of breaking on missing fields.
    candleSeries.setData(
      candles.map((c) => ({
        time: toUnixSeconds(c.timestamp),
        open: c.open ?? c.close,
        high: c.high ?? c.close,
        low: c.low ?? c.close,
        close: c.close,
      }))
    );

    if (hasEma) {
      const emaFastSeries = chart.addSeries(LineSeries, {
        color: COLORS.blue,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      emaFastSeries.setData(
        candles.map((c) => ({ time: toUnixSeconds(c.timestamp), value: c.ema_fast }))
      );

      const emaSlowSeries = chart.addSeries(LineSeries, {
        color: COLORS.orange,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      emaSlowSeries.setData(
        candles.map((c) => ({ time: toUnixSeconds(c.timestamp), value: c.ema_slow }))
      );
    }

    // No text on the markers themselves: with dozens of trades on
    // screen the labels overlap into an unreadable smear. Shape +
    // color (explained in the legend above) carries the meaning.
    const markers = [];
    for (const t of trades) {
      markers.push({
        time: toUnixSeconds(t.entry_time),
        position: "belowBar",
        color: COLORS.green,
        shape: "arrowUp",
      });
      const isStopLoss = t.exit_reason === "stop_loss";
      markers.push({
        time: toUnixSeconds(t.exit_time),
        position: "aboveBar",
        color: isStopLoss ? COLORS.orange : COLORS.red,
        shape: "arrowDown",
      });
    }
    markers.sort((a, b) => a.time - b.time);
    createSeriesMarkers(candleSeries, markers);

    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [candles, trades]);

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>{hasEma ? "Precio + EMA" : "Precio"}</h2>
        <div className="legend-chip-row">
          {hasEma && (
            <>
              <span className="legend-chip">
                <i style={{ background: COLORS.blue }} /> EMA rápida
              </span>
              <span className="legend-chip">
                <i style={{ background: COLORS.orange }} /> EMA lenta
              </span>
            </>
          )}
          <span className="legend-chip">
            <i style={{ background: COLORS.green }} /> Compra
          </span>
          <span className="legend-chip">
            <i style={{ background: COLORS.red }} /> {hasEma ? "Venta (señal)" : "Venta (salida)"}
          </span>
          <span className="legend-chip">
            <i style={{ background: COLORS.orange }} /> Venta (stop-loss)
          </span>
        </div>
      </div>
      <div ref={containerRef} style={{ height: 420 }} />
    </div>
  );
}
