// Derives "are we in a position, and since when/at what price" from
// trade_journal.py's raw fills -- the bot is long-only, single-position
// (see main.py's own docstring), so the simplest correct rule is also
// the only one that matches how it actually trades: if the most recent
// fill (by timestamp) was a buy, that fill is the open position; if it
// was a sell, we're flat. No FIFO/average-cost lot-matching needed for
// a bot that never holds more than one open buy at a time.
export function deriveOpenPosition(trades) {
  if (!trades || trades.length === 0) return null;

  const sorted = [...trades].sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
  const last = sorted[sorted.length - 1];
  if (last.side !== "buy") return null;

  return {
    symbol: last.symbol,
    entryPrice: last.price,
    amount: last.amount,
    entryTime: last.datetime,
  };
}
