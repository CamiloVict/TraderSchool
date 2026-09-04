export function formatPct(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatUsd(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDateShort(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

// Recharts renders every point in the DOM; a multi-month 1h series can be
// thousands of candles, which gets sluggish. Downsample for display only —
// metrics and the trades table still use the full-resolution data.
export function downsample(rows, maxPoints = 500) {
  if (rows.length <= maxPoints) return rows;
  const step = Math.ceil(rows.length / maxPoints);
  const sampled = rows.filter((_, i) => i % step === 0);
  const last = rows[rows.length - 1];
  if (sampled[sampled.length - 1] !== last) sampled.push(last);
  return sampled;
}
