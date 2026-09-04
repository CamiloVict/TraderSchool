export default function KpiCard({ label, value, sublabel, tone = "neutral" }) {
  return (
    <div className={`kpi-card kpi-card--${tone}`}>
      <div className="kpi-card__label">{label}</div>
      <div className="kpi-card__value">{value}</div>
      {sublabel && <div className="kpi-card__sublabel">{sublabel}</div>}
    </div>
  );
}
