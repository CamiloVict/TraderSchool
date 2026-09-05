// Pairs each sell with the buy immediately preceding it into a closed
// round trip. Safe to do with simple FIFO (no lot-matching) because
// the bot is long-only and single-position (see main.py's own
// docstring): it never holds more than one open buy at a time, so
// "the last unmatched buy before this sell" is never ambiguous.
//
// A sell with no preceding buy in the journal (the account already
// held the asset before trade_journal.py started tracking it -- see
// LiveTradesPanel's own note on this, it already happened once with
// the real PAXG journal) gets pnlPct/pnlUsd: null rather than a
// guessed entry price standing in for one that was never recorded.
export function computeClosedTrades(trades) {
  if (!trades || trades.length === 0) return [];

  const sorted = [...trades].sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
  const closed = [];
  let openBuy = null;

  for (const t of sorted) {
    if (t.side === "buy") {
      openBuy = t;
    } else if (t.side === "sell") {
      const amount = t.amount ?? openBuy?.amount ?? null;
      const pnlPct = openBuy ? ((t.price - openBuy.price) / openBuy.price) * 100 : null;
      const pnlUsd = openBuy && amount != null ? (t.price - openBuy.price) * amount : null;
      closed.push({
        symbol: t.symbol,
        entryPrice: openBuy?.price ?? null,
        entryTime: openBuy?.datetime ?? null,
        exitPrice: t.price,
        exitTime: t.datetime,
        amount,
        pnlPct,
        pnlUsd,
      });
      openBuy = null;
    }
  }

  return closed;
}

// Sum of the known pnlUsd values only -- trades with a null P&L (no
// recorded entry) are excluded rather than treated as zero, so an
// unknown doesn't silently understate the total.
export function totalRealizedPnlUsd(closedTrades) {
  return closedTrades.reduce((sum, t) => sum + (t.pnlUsd ?? 0), 0);
}

export function countUnknownPnl(closedTrades) {
  return closedTrades.filter((t) => t.pnlUsd == null).length;
}
