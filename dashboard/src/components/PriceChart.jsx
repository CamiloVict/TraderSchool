import {
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDateShort, formatDateTime, formatUsd } from "../lib/format";

export default function PriceChart({ candles, trades }) {
  const data = candles.map((c) => ({
    x: new Date(c.timestamp).getTime(),
    close: c.close,
    ema_fast: c.ema_fast,
    ema_slow: c.ema_slow,
  }));

  const buys = trades.map((t) => ({ x: new Date(t.entry_time).getTime(), y: t.entry_price }));
  const sells = trades.map((t) => ({ x: new Date(t.exit_time).getTime(), y: t.exit_price }));

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>Precio + EMA 20/50</h2>
        <div className="legend-chip-row">
          <span className="legend-chip">
            <i style={{ background: "var(--text-dim)" }} /> Close
          </span>
          <span className="legend-chip">
            <i style={{ background: "var(--blue)" }} /> EMA 20
          </span>
          <span className="legend-chip">
            <i style={{ background: "var(--orange)" }} /> EMA 50
          </span>
          <span className="legend-chip">
            <i style={{ background: "var(--green)" }} /> Compra
          </span>
          <span className="legend-chip">
            <i style={{ background: "var(--red)" }} /> Venta
          </span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={360}>
        <ComposedChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="x"
            type="number"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(v) => formatDateShort(new Date(v).toISOString())}
            stroke="var(--text-faint)"
            tick={{ fill: "var(--text-dim)", fontSize: 12 }}
            minTickGap={40}
          />
          <YAxis
            domain={["auto", "auto"]}
            tickFormatter={(v) => formatUsd(v, 0)}
            stroke="var(--text-faint)"
            tick={{ fill: "var(--text-dim)", fontSize: 12 }}
            width={72}
          />
          <Tooltip
            contentStyle={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "var(--text-dim)" }}
            labelFormatter={(v) => formatDateTime(new Date(v).toISOString())}
            formatter={(value, name) => [formatUsd(value), name]}
          />
          <Line
            type="monotone"
            dataKey="close"
            name="Close"
            stroke="var(--text-dim)"
            dot={false}
            strokeWidth={1.25}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="ema_fast"
            name="EMA 20"
            stroke="var(--blue)"
            dot={false}
            strokeWidth={1.75}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="ema_slow"
            name="EMA 50"
            stroke="var(--orange)"
            dot={false}
            strokeWidth={1.75}
            isAnimationActive={false}
          />
          <Scatter data={buys} dataKey="y" name="Compra" fill="var(--green)" />
          <Scatter data={sells} dataKey="y" name="Venta" fill="var(--red)" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
