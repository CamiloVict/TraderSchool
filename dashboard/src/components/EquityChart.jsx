import { useEffect, useRef } from "react";
import { AreaSeries, createChart } from "lightweight-charts";

const COLORS = { text: "#8b92a5", border: "#232733", green: "#26a69a", faint: "#5b6272" };

function toUnixSeconds(iso) {
  return Math.floor(new Date(iso).getTime() / 1000);
}

export default function EquityChart({ candles, initialCapital }) {
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

    const series = chart.addSeries(AreaSeries, {
      lineColor: COLORS.green,
      topColor: "rgba(38, 166, 154, 0.35)",
      bottomColor: "rgba(38, 166, 154, 0)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    series.setData(candles.map((c) => ({ time: toUnixSeconds(c.timestamp), value: c.equity })));

    series.createPriceLine({
      price: initialCapital,
      color: COLORS.faint,
      lineWidth: 1,
      lineStyle: 2, // dashed
      axisLabelVisible: true,
      title: "capital inicial",
    });

    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [candles, initialCapital]);

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>Curva de equity</h2>
        <span className="text-dim panel__note">línea punteada = capital inicial</span>
      </div>
      <div ref={containerRef} style={{ height: 240 }} />
    </div>
  );
}
