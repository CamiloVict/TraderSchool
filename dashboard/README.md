# Dashboard

Panel React (Vite) que muestra qué hizo el bot **de verdad** contra
Binance Testnet: no es un visor de backtests. Un gráfico de velas real
([lightweight-charts](https://tradingview.github.io/lightweight-charts/),
la librería de TradingView) con las compras/ventas reales marcadas
sobre el precio y, si hay una posición abierta, una línea con el precio
de entrada y el P&L no realizado en vivo; debajo, la tabla completa del
historial real de operaciones. El contexto de mercado (régimen,
liquidez, bias) queda en un panel plegable al final — es lectura de
fondo sobre *por qué* el motor ve el mercado así, no lo primero que hay
que mirar.

No hay backend: todo sale de JSON estáticos en `public/data/`.

- `trade_journal.json` — historial real de operaciones (lo escribe
  `trade_journal.py` en cada ciclo de `main.py --trade`, en
  `data/trade_journal.json`). Si corrés el bot vía
  `scripts/run_trade_cycle.sh` (cron/systemd), este archivo se copia
  solo a `dashboard/public/data/trade_journal.json` en cada ciclo — no
  hace falta ningún `cp` manual. Son datos reales de tu cuenta, así que
  nunca se commitea (ver `.gitignore` en la raíz).
- `backtest_paxg.json` — velas OHLC reales, generadas una vez con
  `python backtester.py --export dashboard/public/data/backtest_paxg.json`.
  Se usan solo como fondo de precio para el gráfico; ninguna métrica de
  backtest se muestra en el dashboard.
- `context_paxg.json` — contexto de mercado, generado con
  `python -m context_engine --export dashboard/public/data/context_paxg.json`.
  Opcional: si no existe, el panel se repliega solo y explica cómo
  generarlo.

## Setup

```bash
npm install
```

## Desarrollo

```bash
npm run dev
```

## Build de producción

```bash
npm run build
npm run preview
```
