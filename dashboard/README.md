# Dashboard

Panel React (Vite) que visualiza los resultados del backtest: métricas
clave (incluyendo retorno vs. buy & hold, comisiones, duración de
operaciones y cuántas salidas fueron por stop-loss vs. por señal),
velas OHLC reales con EMA 20/50 y marcadores de compra/venta/stop-loss
(vía [lightweight-charts](https://tradingview.github.io/lightweight-charts/),
la librería de gráficos de TradingView), curva de equity, tabla de
operaciones, y un panel plegable que explica la estrategia con los
números concretos del reporte cargado.

No es un panel "en vivo": lee uno o más archivos JSON estáticos en
`public/data/` generados por `backtester.py`. Así el dashboard
funciona sin backend — para verlo con tus propios datos de Binance
Testnet, corre el backtest con `--export` y refresca la página.

Para ver más de un símbolo (por ejemplo BTC y oro/PAXG), exportá cada
uno a un archivo distinto y listalos en `public/data/reports.json`:

```json
[
  { "label": "BTC/USDT", "file": "backtest.json" },
  { "label": "PAXG/USDT (oro)", "file": "backtest_paxg.json" }
]
```

Con más de una entrada, aparece un selector arriba a la derecha del
dashboard. Si ese archivo no existe, el dashboard cae de vuelta a
mostrar solo `backtest.json`.

## Setup

```bash
npm install
```

## Generar datos reales (desde la raíz del proyecto)

```bash
python backtester.py --export dashboard/public/data/backtest.json
```

Ya viene un `backtest.json` de ejemplo (datos sintéticos, marcado
`is_demo: true` en el propio dashboard) para que no se vea vacío antes
de correr el backtest real.

## Desarrollo

```bash
npm run dev
```

## Build de producción

```bash
npm run build
npm run preview
```
