import { useState } from "react";

import AssetPanel from "./components/AssetPanel";

import "./App.css";

const DATA_DIR = "/data";

// One tab per bot -- each is its own cron, its own symbol, and its own
// state files (see README's "Automatizarlo" section), so a tab never
// mixes one bot's position/history with another's the way a single
// shared trade_journal.json would.
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
];

export default function App() {
  const [activeKey, setActiveKey] = useState(TABS[0].key);
  const activeTab = TABS.find((tab) => tab.key === activeKey);

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1>Trading Bot Dashboard</h1>
      </header>

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

      <AssetPanel
        key={activeTab.key}
        candlesUrl={activeTab.candlesUrl}
        tradeJournalUrl={activeTab.tradeJournalUrl}
        contextUrl={activeTab.contextUrl}
      />
    </div>
  );
}
