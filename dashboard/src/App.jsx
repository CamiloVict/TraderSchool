import { useEffect, useState } from "react";

import EquityChart from "./components/EquityChart";
import KpiCard from "./components/KpiCard";
import PriceChart from "./components/PriceChart";
import TradesTable from "./components/TradesTable";
import { downsample, formatDateTime, formatPct, formatUsd } from "./lib/format";

import "./App.css";

const DATA_URL = "/data/backtest.json";
const CHART_MAX_POINTS = 600;

export default function App() {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch(DATA_URL)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setReport)
      .catch((err) => setError(err.message));
  }, []);

  if (error) {
    return (
      <div className="app-shell app-shell--center">
        <div className="panel">
          <h2>No se pudo cargar el reporte</h2>
          <p className="text-dim">
            No encontré <code>{DATA_URL}</code>. Generalo corriendo, desde la raíz del proyecto:
          </p>
          <pre className="code-block">
            python backtester.py --export dashboard/public/data/backtest.json
          </pre>
        </div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="app-shell app-shell--center">
        <p className="text-dim">Cargando reporte de backtest...</p>
      </div>
    );
  }

  const { metrics, candles, trades, symbol, timeframe, strategy, generated_at, is_demo } = report;
  const chartCandles = downsample(candles, CHART_MAX_POINTS);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>Trading Bot Dashboard</h1>
          <p className="text-dim">
            {symbol} · {timeframe} · EMA {strategy.fast_ema}/{strategy.slow_ema} · generado{" "}
            {formatDateTime(generated_at)}
          </p>
        </div>
        {is_demo && <span className="badge badge--demo">DATOS DEMO (sintéticos)</span>}
      </header>

      <section className="kpi-grid">
        <KpiCard
          label="Retorno total"
          value={formatPct(metrics.total_return_pct)}
          tone={metrics.total_return_pct >= 0 ? "positive" : "negative"}
        />
        <KpiCard
          label="Capital final"
          value={formatUsd(metrics.final_capital)}
          sublabel={`desde ${formatUsd(metrics.initial_capital)}`}
        />
        <KpiCard
          label="Win rate"
          value={formatPct(metrics.win_rate_pct, 1)}
          tone={metrics.win_rate_pct >= 50 ? "positive" : "neutral"}
        />
        <KpiCard label="Drawdown máximo" value={formatPct(metrics.max_drawdown_pct)} tone="negative" />
        <KpiCard label="Operaciones" value={metrics.num_trades} />
        <KpiCard
          label="Retorno prom. / operación"
          value={formatPct(metrics.avg_trade_return_pct)}
          tone={metrics.avg_trade_return_pct >= 0 ? "positive" : "negative"}
        />
      </section>

      <PriceChart candles={chartCandles} trades={trades} />
      <EquityChart candles={chartCandles} initialCapital={metrics.initial_capital} />
      <TradesTable trades={trades} />

      <footer className="app-footer text-dim">
        Resultado de backtest, no es consejo financiero. Simulación long-only, comisión 0.1% por
        operación, sin slippage — el desempeño en testnet/real puede diferir.
      </footer>
    </div>
  );
}
