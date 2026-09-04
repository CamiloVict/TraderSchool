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

## Estado del proyecto (Fase 1)

- [x] Estructura del proyecto
- [x] `data_fetcher.py`: conexión vía ccxt en modo sandbox + histórico OHLCV
- [x] `main.py`: script de verificación de conexión y datos
- [ ] `strategy.py`: cruce de medias móviles (Fase 2)
- [ ] `backtester.py`: métricas sobre datos históricos (Fase 2)
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
- **`strategy.py`/`backtester.py`/`risk_manager.py`/`executor.py` son
  stubs** con TODOs: la estructura pedida está completa, pero solo
  Fase 1 (datos) está implementada, según lo indicado.
