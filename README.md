# trading-bot

Bot de trading para BTC/USDT (1h) sobre Binance, desarrollado y probado
primero contra **Binance Spot Testnet** (https://testnet.binance.vision/).
Ninguna orden real se ejecuta hasta que la estrategia esté backtesteada
y validada.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`python3` es necesario solo para crear el entorno virtual. Una vez que lo
activás con `source .venv/bin/activate`, el comando `python` (sin el 3)
ya funciona correctamente dentro de esa terminal — es al que se refieren
el resto de los comandos de este README.

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
python backtester.py --source real --days 365
```

Corre la estrategia de cruce de medias sobre el histórico e imprime:
retorno total, win rate, drawdown máximo, número de operaciones y
retorno promedio por operación.

**Usá `--source real`** (Binance real, datos públicos, sin API key, sin
riesgo — no coloca órdenes) en vez del default `--source testnet`. El
testnet solo guarda una ventana corta de velas (en la práctica, unas
pocas semanas) — no alcanza para un backtest confiable, y con pocas
operaciones el resultado puede depender casi por completo de una sola
racha. La ejecución de órdenes (`main.py --trade`) sigue usando
*siempre* el testnet, sin importar de dónde vengan los datos del
backtest.

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

Cada compra coloca de inmediato una orden `STOP_LOSS_LIMIT` real en el
exchange (`executor.place_stop_loss_order`), al precio de
`risk_manager.stop_loss_price()`. La posición se cierra por lo que
ocurra primero: el stop-loss se dispara, o la EMA rápida vuelve a
cruzar por debajo — en ese segundo caso, el ciclo cancela el stop antes
de vender por mercado, porque el stop deja el balance "locked" (no
disponible para otra orden). Si un ciclo encuentra una posición abierta
sin stop-loss vigente (por ejemplo, el proceso se cortó justo después
de comprar), lo reconstruye a partir del último fill de compra en vez
de dejar la posición desprotegida hasta el próximo cruce.

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
- [x] `executor.py`: órdenes de mercado y stop-loss real
  (`STOP_LOSS_LIMIT`) en Testnet, bloqueadas si `USE_TESTNET` es `False`
- [x] `dashboard/`: panel React (Vite) para visualizar resultados del backtest
- [x] `test_trading_cycle.py`: tests offline (sin red) del ciclo de
  trading contra un exchange falso — compra + coloca stop, cancela el
  stop antes de vender por señal, reconstruye un stop faltante. Correr
  con `python -m unittest test_trading_cycle -v`

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
- **Backtest contra Binance real, ejecución solo en testnet.**
  `data_fetcher.get_public_data_exchange()` trae velas del Binance real
  (endpoint público, sin API key, sin poder colocar órdenes) porque el
  testnet solo retiene una ventana corta de historial — en nuestra
  primera corrida con datos reales del testnet, "180 días" pedidos
  devolvieron apenas ~28, con solo 5 operaciones cerradas y el resultado
  dominado por una sola racha ganadora. Separar de dónde vienen los
  datos (backtest) de dónde se ejecutan las órdenes (siempre testnet,
  vía `get_exchange()`) resuelve eso sin tocar la garantía de seguridad
  de `executor.py`.
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
- **Stop-loss real en el exchange (`STOP_LOSS_LIMIT`)**: cada compra
  coloca de inmediato una orden protectora al precio de
  `risk_manager.stop_loss_price()`, en vez de depender solo del
  siguiente cruce de EMA. El precio límite de esa orden queda un poco
  por debajo del precio de disparo (`STOP_LOSS_LIMIT_SLIPPAGE_PCT`,
  0.5% por defecto) para que siga llenándose en una caída rápida en vez
  de quedar como limit sin ejecutar por encima del mercado.
  **Consecuencia no obvia**: mientras el stop está activo, esos BTC
  quedan "locked" en el balance de Binance, no "free" — por eso
  `main.py --trade` ahora decide "¿estoy en posición?" mirando balance
  total (free + locked), y antes de vender por la señal de EMA cancela
  el stop primero (si no, compiten por el mismo balance). Si un ciclo
  arranca con una posición abierta pero sin stop vigente (el proceso se
  cortó entre comprar y colocar el stop, o alguien lo canceló a mano),
  lo reconstruye leyendo el último fill de compra vía
  `exchange.fetch_my_trades()` en vez de dejarla desprotegida.
  **Sigue pendiente**, y señalado a propósito: no hay take-profit real
  en el exchange — `risk_manager.take_profit_price()` está calculado
  pero no se usa; una salida favorable todavía espera el cruce inverso
  de EMA, no un objetivo fijo.
- **Dashboard sin backend, por ahora**: en vez de levantar una API
  (FastAPI/Flask) para que React consuma datos en vivo, el dashboard
  lee un JSON estático exportado por `backtester.py`. Es la opción más
  simple para "ver los resultados del backtest" — que es lo que
  necesitábamos ahora — sin sumar un servidor a mantener. Si más
  adelante quieres ver la posición/balance en vivo (no solo backtests),
  eso sí requiere un pequeño backend — decisión de arquitectura que
  prefiero discutir contigo antes de construirla.
