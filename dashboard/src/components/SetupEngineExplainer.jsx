export default function SetupEngineExplainer({
  symbol,
  timeframe,
  contextWindowDays,
  riskManagement,
  backtestAssumptions,
}) {
  const riskPct = riskManagement?.risk_per_trade_pct;
  const fallbackStopPct = riskManagement?.stop_loss_pct;
  const feePct = backtestAssumptions?.taker_fee_pct;
  const stopSource = backtestAssumptions?.stop_priced_off;

  return (
    <details className="panel explainer">
      <summary className="explainer__summary">¿Cómo funciona el Setup Engine?</summary>
      <div className="explainer__body">
        <ol>
          <li>
            En cada vela de {timeframe || "1h"} de {symbol || "el símbolo"} se reconstruye el{" "}
            <strong>Contexto de Mercado</strong> (régimen, sesgo por timeframe, estructura,
            liquidez, volatilidad, rango) usando solo las últimas {contextWindowDays ?? 90} días
            de historia — nunca datos futuros a esa vela (ver panel de contexto arriba).
          </li>
          <li>
            Ese contexto alimenta una <strong>máquina de estados</strong> (RANGE, TREND_UP,
            BREAKOUT_ATTEMPT, HIGH_VOLATILITY, NO_TRADE, etc.) que decide qué tipo de setup está
            permitido operar en este momento — a diferencia de la EMA, acá no hay un único
            indicador: son varias condiciones estructurales que tienen que coincidir.
          </li>
          <li>
            <strong>Entrada:</strong> solo cuando el motor confirma un setup{" "}
            <code>LIQUIDITY_SWEEP_RECLAIM</code> a favor del sesgo de mayor timeframe — un nivel de
            liquidez barrido, reclamado, y confirmado por una ruptura de estructura (BOS) en la
            misma dirección. Un sesgo o un patrón aislado nunca son señal suficiente por sí solos.
          </li>
          <li>
            <strong>Tamaño de la posición:</strong> se calcula para arriesgar como máximo{" "}
            {riskPct ?? 1}% del capital si se toca el stop.
          </li>
          <li>
            <strong>Salida por stop-loss:</strong> el stop se ubica en el nivel de invalidación del
            setup ({stopSource || "el nivel de invalidación del setup"}), no en un % fijo — si ese
            nivel es inválido, cae a un stop del {fallbackStopPct ?? 2}% como respaldo.
          </li>
          <li>
            <strong>Salida por señal:</strong> si el stop no se toca, la posición se cierra cuando
            el sesgo deja de ser alcista o el estado de mercado pasa a <code>NO_TRADE</code> — no
            espera un cruce de indicador.
          </li>
          <li>
            <strong>Comisiones:</strong> cada entrada y salida descuenta {feePct ?? 0.1}% de
            comisión (fee taker típico de Binance Spot).
          </li>
        </ol>
        <p className="text-dim explainer__note">
          Este motor reemplaza el cruce de EMA cuando <code>USE_SETUP_ENGINE=true</code> en{" "}
          <code>main.py --trade</code>. Es más exigente que la EMA (varias condiciones tienen que
          alinearse a la vez), por eso suele operar mucho menos seguido — priorizá calidad de señal
          sobre cantidad de operaciones al leer este backtest. Nunca se probó contra un feed de
          Testnet en vivo, solo contra historia real y la suite de tests de este repo.
        </p>
      </div>
    </details>
  );
}
