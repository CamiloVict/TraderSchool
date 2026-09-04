export default function StrategyExplainer({
  symbol,
  timeframe,
  strategy,
  riskManagement,
  backtestAssumptions,
}) {
  const fast = strategy?.fast_ema;
  const slow = strategy?.slow_ema;
  const riskPct = riskManagement?.risk_per_trade_pct;
  const stopPct = riskManagement?.stop_loss_pct;
  const takeProfitPct = riskManagement?.take_profit_pct;
  const feePct = backtestAssumptions?.taker_fee_pct;

  return (
    <details className="panel explainer">
      <summary className="explainer__summary">¿Cómo funciona esta estrategia?</summary>
      <div className="explainer__body">
        <ol>
          <li>
            Cada vela de {timeframe || "1h"} de {symbol || "el símbolo"} calcula dos promedios
            móviles exponenciales del precio de cierre: una rápida (EMA {fast ?? 20}) y una lenta
            (EMA {slow ?? 50}). La rápida reacciona antes a cambios de precio recientes.
          </li>
          <li>
            <strong>Entrada:</strong> cuando la EMA rápida cruza por encima de la lenta (empieza
            una tendencia alcista), se compra al cierre de esa vela. Solo posiciones largas —no
            hay ventas en corto.
          </li>
          <li>
            <strong>Tamaño de la posición:</strong> se calcula para arriesgar como máximo{" "}
            {riskPct ?? 1}% del capital si se toca el stop-loss — no una cantidad fija de moneda.
          </li>
          <li>
            <strong>Salida por stop-loss:</strong> al comprar se coloca un stop-loss a{" "}
            {stopPct ?? 2}% por debajo del precio de entrada. Si el precio de la vela baja hasta
            ahí, la posición se cierra ahí mismo — no espera a que la EMA cruce de vuelta.
          </li>
          <li>
            <strong>Salida por señal:</strong> si el stop no se toca, la posición se cierra cuando
            la EMA rápida vuelve a cruzar por debajo de la lenta (fin de la tendencia).
          </li>
          {takeProfitPct != null && (
            <li>
              <strong>Take-profit ({takeProfitPct}%):</strong> se calcula pero{" "}
              <em>no se usa activamente todavía</em> — una salida favorable sigue esperando el
              cruce de EMA, no un objetivo fijo de ganancia.
            </li>
          )}
          <li>
            <strong>Comisiones:</strong> cada entrada y salida descuenta {feePct ?? 0.1}% de
            comisión (fee taker típico de Binance Spot), para que el resultado no sea
            artificialmente optimista.
          </li>
        </ol>
        <p className="text-dim explainer__note">
          Este backtest simula la misma lógica que corre en Testnet (<code>main.py --trade</code>
          ): entra por cruce de EMA, protege con un stop-loss real, y sale por lo que ocurra
          primero. La diferencia con la operación real es la ejecución exacta (slippage, fills
          parciales) — por eso Testnet sigue siendo el paso siguiente antes de considerar capital
          real.
        </p>
      </div>
    </details>
  );
}
