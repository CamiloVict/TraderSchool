import { useEffect, useState } from "react";

import EquityChart from "./components/EquityChart";
import KpiCard from "./components/KpiCard";
import MarketContext from "./components/MarketContext";
import StrategyExplainer from "./components/StrategyExplainer";
import TradesTable from "./components/TradesTable";
import TradingChart from "./components/TradingChart";
import { formatDateTime, formatHours, formatPct, formatUsd } from "./lib/format";

import "./App.css";

const DATA_DIR = "/data";
const REPORTS_MANIFEST_URL = `${DATA_DIR}/reports.json`;
// Falls back to this single entry if reports.json is missing, so a
// dashboard set up before the manifest existed still works.
const DEFAULT_REPORTS = [{ label: "Backtest", file: "backtest.json" }];

export default function App() {
  const [reports, setReports] = useState(DEFAULT_REPORTS);
  const [selectedFile, setSelectedFile] = useState(DEFAULT_REPORTS[0].file);
  const [report, setReport] = useState(null);
  const [context, setContext] = useState(null);
  const [error, setError] = useState(null);

  // The market context is optional and independent of the backtest, so
  // a missing file resolves to null and the panel explains how to
  // generate it rather than breaking the dashboard.
  useEffect(() => {
    fetch(`${DATA_DIR}/context.json`)
      .then((res) => (res.ok ? res.json() : null))
      .catch(() => null)
      .then(setContext);
  }, []);

  useEffect(() => {
    fetch(REPORTS_MANIFEST_URL)
      .then((res) => (res.ok ? res.json() : DEFAULT_REPORTS))
      .catch(() => DEFAULT_REPORTS)
      .then((list) => {
        const safeList = Array.isArray(list) && list.length ? list : DEFAULT_REPORTS;
        setReports(safeList);
        setSelectedFile(safeList[0].file);
      });
  }, []);

  useEffect(() => {
    if (!selectedFile) return;
    setReport(null);
    setError(null);
    fetch(`${DATA_DIR}/${selectedFile}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then(setReport)
      .catch((err) => setError(err.message));
  }, [selectedFile]);

  if (error) {
    return (
      <div className="app-shell app-shell--center">
        <div className="panel">
          <h2>No se pudo cargar el reporte</h2>
          <p className="text-dim">
            No encontré <code>{DATA_DIR}/{selectedFile}</code>. Generalo corriendo, desde la raíz
            del proyecto:
          </p>
          <pre className="code-block">
            python backtester.py --export dashboard/public{DATA_DIR}/{selectedFile}
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

  const {
    metrics,
    candles,
    trades,
    symbol,
    timeframe,
    strategy,
    risk_management: riskManagement,
    backtest_assumptions: backtestAssumptions,
    generated_at,
    is_demo,
  } = report;

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
        <div className="app-header__actions">
          {is_demo && <span className="badge badge--demo">DATOS DEMO (sintéticos)</span>}
          {reports.length > 1 && (
            <select
              className="report-select"
              value={selectedFile}
              onChange={(e) => setSelectedFile(e.target.value)}
              aria-label="Elegir backtest"
            >
              {reports.map((r) => (
                <option key={r.file} value={r.file}>
                  {r.label}
                </option>
              ))}
            </select>
          )}
        </div>
      </header>

      <MarketContext context={context} />

      <StrategyExplainer
        symbol={symbol}
        timeframe={timeframe}
        strategy={strategy}
        riskManagement={riskManagement}
        backtestAssumptions={backtestAssumptions}
      />

      <section className="kpi-grid">
        <KpiCard
          label="Retorno total"
          value={formatPct(metrics.total_return_pct)}
          sublabel={`buy & hold: ${formatPct(metrics.buy_hold_return_pct)}`}
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
        <KpiCard
          label="Operaciones"
          value={metrics.num_trades}
          sublabel={
            metrics.stop_loss_exits != null
              ? `${metrics.signal_exits} por señal · ${metrics.stop_loss_exits} por stop-loss`
              : undefined
          }
        />
        <KpiCard
          label="Retorno prom. / operación"
          value={formatPct(metrics.avg_trade_return_pct)}
          tone={metrics.avg_trade_return_pct >= 0 ? "positive" : "negative"}
        />
        <KpiCard
          label="Mejor / peor operación"
          value={`${formatPct(metrics.best_trade_pct)} / ${formatPct(metrics.worst_trade_pct)}`}
        />
        <KpiCard label="Duración prom. posición" value={formatHours(metrics.avg_trade_duration_hours)} />
        <KpiCard
          label="Comisiones pagadas"
          value={formatUsd(metrics.total_fees_paid)}
          sublabel={
            backtestAssumptions?.taker_fee_pct != null
              ? `${backtestAssumptions.taker_fee_pct}% por operación`
              : undefined
          }
        />
      </section>

      <TradingChart candles={candles} trades={trades} />
      <EquityChart candles={candles} initialCapital={metrics.initial_capital} />
      <TradesTable trades={trades} />

      <footer className="app-footer text-dim">
        Resultado de backtest, no es consejo financiero. Simulación long-only con stop-loss real,
        comisión {backtestAssumptions?.taker_fee_pct ?? 0.1}% por operación, sin slippage — el
        desempeño en testnet/real puede diferir.
      </footer>
    </div>
  );
}
