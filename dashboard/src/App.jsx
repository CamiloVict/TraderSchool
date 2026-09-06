import { useState } from "react";

import AccountSummary from "./components/AccountSummary";
import AssetPanel from "./components/AssetPanel";
import ComingSoonPanel from "./components/ComingSoonPanel";

import "./App.css";

const DATA_DIR = "/data";

// One tab per bot -- each is its own cron, its own symbol, and its own
// state files (see README's "Automatizarlo" section), so a tab never
// mixes one bot's position/history with another's the way a single
// shared trade_journal.json would. A tab with `comingSoon: true` (see
// Forex below) renders ComingSoonPanel instead of AssetPanel -- there
// is no Binance/Testnet underneath it to describe yet, so AssetPanel's
// own "en vivo contra Testnet" header would be a plain false statement.
const TABS = [
  {
    key: "paxg",
    label: "Oro (PAXG)",
    candlesUrl: `${DATA_DIR}/backtest_paxg.json`,
    tradeJournalUrl: `${DATA_DIR}/trade_journal.json`,
    contextUrl: `${DATA_DIR}/context_paxg.json`,
  },
  {
    key: "btc",
    label: "BTC (scalping)",
    candlesUrl: `${DATA_DIR}/backtest_btc.json`,
    tradeJournalUrl: `${DATA_DIR}/trade_journal_btc.json`,
    contextUrl: `${DATA_DIR}/context.json`,
  },
  {
    key: "forex",
    label: "Forex (XAU/USD)",
    comingSoon: true,
    market: "Forex -- XAU/USD vía OANDA",
    // See README's Forex section for the full walkthrough behind each
    // of these -- this list is meant to match it step for step.
    steps: [
      "Crear una cuenta practice (demo) en OANDA y generar un personal access token.",
      "Cargar OANDA_API_TOKEN (y opcionalmente OANDA_ACCOUNT_ID) en .env.",
      "Correr forex_data_fetcher.py contra la cuenta real para confirmar que la respuesta de OANDA calza con lo documentado en el código (todavía no probado end-to-end desde este entorno).",
      "Recién ahí: construir y backtestear una estrategia para Forex -- no existe todavía, ni conectada a ningún ciclo de trading en vivo.",
    ],
  },
];

export default function App() {
  const [activeKey, setActiveKey] = useState(TABS[0].key);
  const activeTab = TABS.find((tab) => tab.key === activeKey);

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Trading Bot Dashboard</h1>
      </header>

      <AccountSummary />

      <div className="tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={tab.key === activeKey}
            className={`tab ${tab.key === activeKey ? "tab--active" : ""}`}
            onClick={() => setActiveKey(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab.comingSoon ? (
        <ComingSoonPanel key={activeTab.key} market={activeTab.market} steps={activeTab.steps} />
      ) : (
        <AssetPanel
          key={activeTab.key}
          candlesUrl={activeTab.candlesUrl}
          tradeJournalUrl={activeTab.tradeJournalUrl}
          contextUrl={activeTab.contextUrl}
        />
      )}
    </div>
  );
}
