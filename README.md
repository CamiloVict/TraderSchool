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

## Estado del proyecto

- [x] Estructura del proyecto
- [x] `data_fetcher.py`: conexión vía ccxt en modo sandbox + histórico OHLCV
- [x] `main.py`: script de verificación de conexión y datos
- [x] `strategy.py`: cruce de EMA 20/50, long-only
- [x] `backtester.py`: simulación + métricas (retorno, win rate, drawdown)
- [ ] `risk_manager.py`: stop-loss, sizing, límites diarios (Fase 3)
- [ ] `executor.py`: órdenes en Testnet (Fase 3+, nunca en real todavía)

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
- **`risk_manager.py`/`executor.py` siguen siendo stubs**: ningún riesgo
  real de capital hasta que existan sizing/stop-loss y hasta que decidamos
  juntos pasar a ejecución en testnet.
