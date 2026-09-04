import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDateShort, formatDateTime, formatUsd } from "../lib/format";

export default function EquityChart({ candles, initialCapital }) {
  const data = candles.map((c) => ({ x: new Date(c.timestamp).getTime(), equity: c.equity }));

  return (
    <div className="panel">
      <div className="panel__header">
        <h2>Curva de equity</h2>
        <span className="text-dim panel__note">línea punteada = capital inicial</span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--green)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="var(--green)" stopOpacity={0} />
            </linearGradient>
          </defs>
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
            formatter={(value) => [formatUsd(value), "Equity"]}
          />
          <ReferenceLine y={initialCapital} stroke="var(--text-faint)" strokeDasharray="4 4" />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="var(--green)"
            fill="url(#equityFill)"
            strokeWidth={2}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
