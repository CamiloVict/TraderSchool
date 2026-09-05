import { formatDateTime } from "../lib/format";

// Bias, trend and zone labels all map onto the same three-way colour
// scheme the rest of the dashboard uses: green for bullish, red for
// bearish, muted for everything undecided.
const BULLISH = ["UP", "BULLISH", "STRONG_BULLISH", "TRENDING_UP", "DISCOUNT", "LONG"];
const BEARISH = ["DOWN", "BEARISH", "STRONG_BEARISH", "TRENDING_DOWN", "PREMIUM", "SHORT"];

function toneOf(value) {
  if (BULLISH.includes(value)) return "positive";
  if (BEARISH.includes(value)) return "negative";
  return "neutral";
}

// Volatility is not directional: only the extremes are worth flagging,
// and both extremes are a warning rather than good or bad news.
const VOLATILITY_TONE = {
  EXTREME: "negative",
  HIGH: "warning",
  VERY_LOW: "warning",
};

const TIMEFRAME_ORDER = ["1w", "1d", "4h", "1h", "15m"];

const LABELS = {
  STRONG_BULLISH: "muy alcista",
  BULLISH: "alcista",
  NEUTRAL: "neutral",
  BEARISH: "bajista",
  STRONG_BEARISH: "muy bajista",
  UP: "alcista",
  DOWN: "bajista",
  RANGING: "en rango",
  UNDEFINED: "sin definir",
  STRONG_ALIGNMENT: "timeframes alineados",
  PARTIAL_ALIGNMENT: "alineación parcial",
  CONFLICT: "timeframes en conflicto",
  TRENDING_UP: "tendencia alcista",
  TRENDING_DOWN: "tendencia bajista",
  TRANSITION: "en transición",
  PREMIUM: "premium",
  EQUILIBRIUM: "equilibrio",
  DISCOUNT: "descuento",
  LONG: "largo",
  SHORT: "corto",
  NONE: "ninguna",
  VERY_LOW: "muy baja",
  LOW: "baja",
  NORMAL: "normal",
  HIGH: "alta",
  IMPULSE: "impulso",
  PULLBACK: "retroceso",
  CONSOLIDATION: "consolidación",
  EXPANSION: "expansión",
  COMPRESSION: "compresión",
  ASIA: "Asia",
  LONDON: "Londres",
  NEW_YORK: "Nueva York",
};

function label(value) {
  if (value === null || value === undefined) return "—";
  return LABELS[value] ?? value.toString().toLowerCase().replace(/_/g, " ");
}

function Tag({ value, tone }) {
  return <span className={`tag tag--${tone ?? toneOf(value)}`}>{label(value)}</span>;
}

function Row({ term, children }) {
  return (
    <div className="context-row">
      <dt className="context-row__term">{term}</dt>
      <dd className="context-row__value">{children}</dd>
    </div>
  );
}

function formatPrice(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

export default function MarketContext({ context }) {
  // The panel is optional: if context.json was never generated, say how
  // to produce it instead of rendering an empty shell.
  if (!context) {
    return (
      <details className="panel explainer">
        <summary className="explainer__summary">Contexto de mercado</summary>
        <div className="explainer__body">
          <p className="text-dim">
            Todavía no hay un contexto generado. Crealo corriendo, desde la raíz del proyecto:
          </p>
          <pre className="code-block">
            python -m context_engine --export dashboard/public/data/context.json
          </pre>
        </div>
      </details>
    );
  }

  const {
    timestamp,
    asset,
    version,
    regime,
    multi_timeframe: multiTimeframe = {},
    alignment,
    liquidity,
    volatility,
    range: rangeState,
    sessions,
    bias,
    context_score: score,
    market_state: marketState,
    preferred_direction: direction,
    avoid = [],
    no_trade: noTrade,
    invalidation,
    data_quality: dataQuality,
  } = context;

  const timeframes = TIMEFRAME_ORDER.filter((tf) => tf in multiTimeframe);
  const levels = liquidity?.levels ?? [];
  const lastEvent = liquidity?.events?.[liquidity.events.length - 1];

  // A collapsible <details>, not a plain <section>: this is background
  // reading on *why* the engine reads the market the way it does, not
  // the primary thing to look at (that's the price chart + open
  // position above it) -- secondary, not hidden.
  return (
    <details className="panel">
      <summary className="panel__header panel__header--clickable">
        <h2>Contexto de mercado</h2>
        <span className="text-dim panel__note">
          {asset} · {formatDateTime(timestamp)} · motor v{version}
        </span>
      </summary>

      {/* The verdict comes first: whether to trade at all, and why not. */}
      <div className={`context-verdict ${noTrade ? "context-verdict--blocked" : ""}`}>
        <div className="context-verdict__main">
          <span className="context-verdict__state">{label(marketState)}</span>
          <Tag value={bias?.direction} />
          <span className="text-dim">
            confianza {bias?.confidence != null ? bias.confidence.toFixed(2) : "—"}
          </span>
          <span className="text-dim">
            score {score?.total > 0 ? "+" : ""}
            {score?.total ?? "—"} ({label(score?.label)})
          </span>
        </div>
        <div className="context-verdict__side">
          {noTrade ? (
            <span className="tag tag--warning">no operar</span>
          ) : (
            <Tag value={direction} />
          )}
        </div>
      </div>

      {noTrade && avoid.length > 0 && (
        <>
          <h3 className="context-subtitle">Motivos para no operar</h3>
          <ul className="context-list context-list--warning">
            {avoid.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </>
      )}

      <div className="context-grid">
        <dl className="context-block">
          <Row term="Régimen">
            <Tag value={regime?.primary} />
            <span className="text-dim">{label(regime?.phase)}</span>
          </Row>
          <Row term="Volatilidad">
            <Tag
              value={volatility?.regime}
              tone={VOLATILITY_TONE[volatility?.regime] ?? "neutral"}
            />
            <span className="text-dim">
              ATR {formatPrice(volatility?.atr)} ({volatility?.atr_percent?.toFixed(2)}%) · pct{" "}
              {volatility?.percentile}
            </span>
          </Row>
          <Row term="Posición en rango">
            <Tag value={rangeState?.zone} />
            <span className="text-dim">
              {rangeState?.position_percent?.toFixed(0)}% del rango {rangeState?.name} (
              {formatPrice(rangeState?.low)} – {formatPrice(rangeState?.high)})
            </span>
          </Row>
          <Row term="Sesión">
            {sessions?.current ? (
              <span>
                {label(sessions.current)}
                <span className="text-dim">
                  {" "}
                  · {formatPrice(sessions.low)} – {formatPrice(sessions.high)}
                </span>
              </span>
            ) : (
              <span className="text-dim">fuera de sesión</span>
            )}
          </Row>
        </dl>

        <dl className="context-block">
          <Row term="Bias por timeframe">
            <div className="context-timeframes">
              {timeframes.map((tf) => (
                <span key={tf} className="context-timeframe">
                  <b>{tf}</b>
                  <Tag value={multiTimeframe[tf]} />
                </span>
              ))}
            </div>
            <span className="text-dim">{label(alignment)}</span>
          </Row>
          <Row term="Liquidez">
            {levels.length ? (
              <div className="context-levels">
                {levels.map((level) => (
                  <span
                    key={level.name}
                    className={`context-level ${level.swept ? "context-level--swept" : ""}`}
                    title={level.swept ? "nivel ya barrido" : "liquidez sin tocar"}
                  >
                    <b>{level.name}</b> {formatPrice(level.price)}
                  </span>
                ))}
              </div>
            ) : (
              <span className="text-dim">sin niveles de referencia</span>
            )}
            {lastEvent && (
              <span className="text-dim">
                último evento: {label(lastEvent.kind)} en {lastEvent.level_name}
                {lastEvent.reclaimed && " · recuperado"}
                {lastEvent.displacement && " · con desplazamiento"}
              </span>
            )}
          </Row>
        </dl>
      </div>

      {bias?.reasons?.length > 0 && (
        <>
          <h3 className="context-subtitle">Evidencia del motor</h3>
          <ul className="context-list">
            {bias.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </>
      )}

      {/* Falsifiability made visible: the hypothesis always ships with
          the level that would kill it. Built from `type` and `level`
          rather than reusing `detail`, which is the engine's own
          English audit string and already contains the price. */}
      <div className="context-invalidation">
        <span className="context-invalidation__label">Invalidación</span>
        <span>
          {invalidation?.level != null ? (
            <>
              {invalidation.type === "CLOSE_BELOW"
                ? "un cierre por debajo de "
                : "un cierre por encima de "}
              <b className="context-invalidation__level">{formatPrice(invalidation.level)}</b>{" "}
              rompe la estructura y anula este bias
            </>
          ) : (
            <span className="text-dim">
              bias neutral: no hay hipótesis direccional que invalidar
            </span>
          )}
        </span>
      </div>

      {dataQuality?.issues?.length > 0 && (
        <details className="context-quality">
          <summary className="text-dim">
            Calidad de datos: {dataQuality.valid ? "usable" : "insuficiente"}
            {dataQuality.degraded && " (degradada)"} · {dataQuality.issues.length} aviso(s)
          </summary>
          <ul className="context-list">
            {dataQuality.issues.map((issue) => (
              <li key={`${issue.timeframe}-${issue.code}`}>
                <span className={issue.severity === "FATAL" ? "negative" : "text-dim"}>
                  [{issue.severity}]
                </span>{" "}
                {issue.timeframe} · {issue.code}: {issue.detail}
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="text-dim panel__note context-footnote">
        Contexto determinista: no propone entradas todavía (eso es el Setup Engine). Sesiones en
        UTC fijo, sin ajuste por horario de verano. La evidencia y los motivos vienen en inglés
        tal como los emite el motor, que es también el contrato que consume la capa LLM.
      </p>
    </details>
  );
}
