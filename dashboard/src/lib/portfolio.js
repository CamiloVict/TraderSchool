// Turns raw per-currency balance snapshots (balance_snapshot.py) into
// an approximate USD-equivalent total over time, using the closest
// cached candle close for whatever symbols this dashboard tracks.
// Approximate on purpose, same caveat as the position KPI cards
// elsewhere in this dashboard: priced off cached backtest candle
// exports, not a live feed, and only for currencies this dashboard
// actually has a price series for -- anything else (BNB from a
// testnet faucet, say) is counted as "unpriced" rather than silently
// dropped or guessed at.
function closestClose(candles, timestampMs) {
  if (!candles || candles.length === 0) return null;
  let best = null;
  let bestDiff = Infinity;
  for (const c of candles) {
    const diff = Math.abs(new Date(c.timestamp).getTime() - timestampMs);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = c.close;
    }
  }
  return best;
}

// `candlesByCurrency`: { PAXG: [...candles], BTC: [...candles] } --
// keyed by the currency code exactly as ccxt/Binance reports it in a
// balance (the base asset of each symbol this dashboard tracks).
// USDT needs no pricing, it already is the unit of account.
export function estimatePortfolioValueSeries(balanceHistory, candlesByCurrency) {
  if (!balanceHistory) return [];
  return balanceHistory.map((snapshot) => {
    let totalUsd = 0;
    let hasUnpriced = false;
    for (const [currency, amount] of Object.entries(snapshot.balances ?? {})) {
      if (currency === "USDT") {
        totalUsd += amount;
        continue;
      }
      const price = closestClose(candlesByCurrency[currency], snapshot.timestamp);
      if (price != null) {
        totalUsd += amount * price;
      } else {
        hasUnpriced = true;
      }
    }
    return { timestamp: snapshot.timestamp, datetime: snapshot.datetime, totalUsd, hasUnpriced };
  });
}

export function latestBalances(balanceHistory) {
  if (!balanceHistory || balanceHistory.length === 0) return null;
  return balanceHistory[balanceHistory.length - 1];
}
