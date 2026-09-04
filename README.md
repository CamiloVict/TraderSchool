# trading-bot

Bot de trading para BTC/USDT (1h) sobre Binance, desarrollado y probado
primero contra **Binance Spot Testnet** (https://testnet.binance.vision/).
Ninguna orden real se ejecuta hasta que la estrategia esté backtesteada
y validada.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

1. Entra a https://testnet.binance.vision/, loguéate con GitHub y genera
   una API key (HMAC_SHA256).
2. Copia `BINANCE_API_KEY` y `BINANCE_API_SECRET` en tu `.env`.
3. Verifica la conexión:

```bash
python main.py
```

Deberías ver la lista de mercados cargada, las últimas 10 velas de
BTC/USDT y (si pusiste tus keys) tu balance de testnet.

## Backtest

```bash
python backtester.py
```

Trae ~180 días de velas de BTC/USDT 1h desde el testnet y corre la
estrategia de cruce de medias sobre ese histórico, imprimiendo:
retorno total, win rate, drawdown máximo, número de operaciones y
retorno promedio por operación.

## Trading en Testnet (una orden por ejecución)

```bash
python main.py --trade
```

Corre **un solo ciclo**: trae las velas más recientes, calcula la señal
EMA 20/50, mira tu balance real en la cuenta testnet para saber si ya
estás en posición, y si la señal cambió, coloca UNA orden de mercado
(compra o venta) dimensionada por `risk_manager.position_size()`. No
corre en loop infinito — está pensado para invocarse una vez por cierre
de vela (cron/systemd timer cada hora), así cada corrida es corta, fácil
de loguear y de matar/reiniciar sin perder estado (el "estado" es
simplemente lo que la cuenta testnet tiene en ese momento).

**Nunca coloca órdenes si `BINANCE_TESTNET` no es `true`** — `executor.py`
lo verifica antes de cada orden y lanza `LiveTradingDisabledError` si no.

## Dashboard (React)

```bash
cd dashboard
npm install
npm run dev
```

Panel visual del backtest: KPIs (retorno, win rate, drawdown, # de
operaciones), gráfico de precio con EMA 20/50 y marcadores de
compra/venta, curva de equity, y tabla de operaciones. Ver
`dashboard/README.md`.

Es un panel **estático**, no en vivo: lee `dashboard/public/data/backtest.json`,
que genera `backtester.py`:

```bash
python backtester.py --export dashboard/public/data/backtest.json
```

Ya incluye un `backtest.json` de ejemplo con datos sintéticos (marcado
como demo en el propio dashboard) para que no se vea vacío hasta que
corras el backtest con datos reales del testnet.

## Estado del proyecto

- [x] Estructura del proyecto
- [x] `data_fetcher.py`: conexión vía ccxt en modo sandbox + histórico OHLCV
- [x] `main.py`: verificación de conexión (`python main.py`) y ciclo de
  trading en testnet (`python main.py --trade`)
- [x] `strategy.py`: cruce de EMA 20/50, long-only
- [x] `backtester.py`: simulación + métricas (retorno, win rate, drawdown)
  y exportación a JSON para el dashboard (`--export`)
- [x] `risk_manager.py`: tamaño de posición por % de riesgo, precios de
  stop-loss/take-profit, límite de pérdida diaria (`DailyLossTracker`)
- [x] `executor.py`: órdenes de mercado en Testnet, bloqueadas si
  `USE_TESTNET` es `False`
- [x] `dashboard/`: panel React (Vite) para visualizar resultados del backtest

## Decisiones tomadas hasta ahora

- **Spot, no Futures**: `testnet.binance.vision` es el testnet de Spot;
  se usa `ccxt.binance` con `set_sandbox_mode(True)` y `defaultType: spot`.
  El testnet de Futures es un servicio distinto (`testnet.binancefuture.com`)
  — si en algún momento quieres apalancamiento/futuros, es un cambio de
  exchange/cliente, no solo de configuración.
- **`USE_TESTNET` por defecto en `true`**: solo se vuelve `false` con un
  cambio explícito en `.env`; ningún código hardcodea `false`.
- **Parámetros de riesgo en `config.py` son placeholders** (1% riesgo por
  operación, 2% stop-loss, 4% take-profit, 5% límite de pérdida diaria)
  para que `risk_manager.py` tenga algo que importar en la Fase 3. Son
  valores conservadores típicos, no una decisión de estrategia final —
  se revisan antes de implementar `risk_manager.py`.
- **Estrategia: cruce de EMA 20/50, solo largos.** EMA en vez de SMA
  porque reacciona más rápido a cambios de precio recientes (menos
  retraso entre la señal y el movimiento real). 20/50 como punto medio:
  suficientemente rápido para capturar tendencias de varios días,
  suficientemente lento para no operar en cada ruido de una vela. Sin
  shorts porque una cuenta Spot no puede vender en corto de forma nativa.
- **Backtest asume fill al cierre de la vela de cruce** y aplica una
  comisión plana de 0.1% (taker fee típico de Binance Spot) por operación,
  para no inflar artificialmente el retorno. Es una simplificación — por
  eso después probamos en testnet además de backtestear.
- **Sizing por riesgo, no por "quiero comprar X"**: `position_size()`
  calcula cuánto comprar para que, SI se toca el stop-loss, la pérdida
  sea exactamente `RISK_PER_TRADE_PCT` del capital — no una cantidad
  arbitraria de BTC.
- **Sin estado local**: `main.py --trade` no guarda en disco si "está en
  posición" — lo deduce leyendo el balance real de la cuenta testnet en
  cada corrida. Es más simple y sobrevive a reinicios/crashes sin
  desincronizarse del exchange.
- **Limitación importante, señalada a propósito**: la salida de una
  operación depende solo de que la EMA rápida vuelva a cruzar por debajo
  de la lenta en la siguiente vela — todavía **no se coloca una orden de
  stop-loss real en el exchange**. `risk_manager.stop_loss_price()` se
  usa para calcular el tamaño de la posición, pero un movimiento brusco
  entre cierres de vela no está protegido hasta el siguiente chequeo
  horario. Es aceptable en testnet (dinero ficticio); antes de capital
  real habría que agregar una orden STOP_LOSS_LIMIT real — lo dejo para
  discutir contigo, es una decisión de arquitectura no trivial (maneja
  fills parciales, cancelaciones, etc.).
- **Dashboard sin backend, por ahora**: en vez de levantar una API
  (FastAPI/Flask) para que React consuma datos en vivo, el dashboard
  lee un JSON estático exportado por `backtester.py`. Es la opción más
  simple para "ver los resultados del backtest" — que es lo que
  necesitábamos ahora — sin sumar un servidor a mantener. Si más
  adelante quieres ver la posición/balance en vivo (no solo backtests),
  eso sí requiere un pequeño backend — decisión de arquitectura que
  prefiero discutir contigo antes de construirla.
