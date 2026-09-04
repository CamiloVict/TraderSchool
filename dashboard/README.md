# Dashboard

Panel React (Vite) que visualiza los resultados del backtest: métricas
clave, precio con EMA 20/50 y marcadores de compra/venta, curva de
equity, y la tabla de operaciones.

No es un panel "en vivo": lee un archivo JSON estático
(`public/data/backtest.json`) generado por `backtester.py`. Así el
dashboard funciona sin backend — para verlo con tus propios datos de
Binance Testnet, corre el backtest con `--export` y refresca la página.

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
