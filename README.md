# trading-bot

[![Tests](https://github.com/CamiloVict/TraderSchool/actions/workflows/tests.yml/badge.svg)](https://github.com/CamiloVict/TraderSchool/actions/workflows/tests.yml)

Bot de trading (1h) sobre Binance, desarrollado y probado primero contra
**Binance Spot Testnet** (https://testnet.binance.vision/). El símbolo
por default es **PAXG/USDT** (oro) — el activo de mayor interés — pero
cualquier par soportado por Binance funciona seteando `SYMBOL` en `.env`
(por ejemplo `SYMBOL=BTC/USDT`). Ninguna orden real se ejecuta hasta que
la estrategia esté backtesteada y validada.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`python3.12` es necesario solo para crear el entorno virtual. Una vez que
lo activás con `source .venv/bin/activate`, el comando `python` (sin el
3) ya funciona correctamente dentro de esa terminal — es al que se
refieren el resto de los comandos de este README.

**Por qué `python3.12` y no `python3` a secas:** en esta máquina el
`python3` del PATH apunta al 3.14 de Homebrew, que tiene el módulo
`pyexpat` roto. Crear el venv con él falla en `ensurepip` con un
`CalledProcessError` y te deja un entorno sin `pip`, difícil de
diagnosticar porque el error no menciona ni a `pyexpat` ni a XML. Fijar
la versión evita el problema. Si en tu sistema `python3` ya es una
versión sana (3.10+), podés usarlo sin más.

1. Entra a https://testnet.binance.vision/, loguéate con GitHub y genera
   una API key (HMAC_SHA256).
2. Copia `BINANCE_API_KEY` y `BINANCE_API_SECRET` en tu `.env`.

   **Si en algún momento generás una key contra Binance real (no
   Testnet)**, creála con permiso de **spot trading únicamente** —
   nunca habilites retiros (withdrawal). Este bot no los necesita para
   nada, y una key sin ese permiso limita el daño posible de una fuga a
   "puede operar con tu capital", no a "puede vaciar la cuenta". Es
   una casilla en la propia UI de Binance al crear la key.
3. Verifica la conexión:

```bash
python main.py
```

Deberías ver la lista de mercados cargada, las últimas 10 velas de
PAXG/USDT (o el `SYMBOL` que hayas configurado) y (si pusiste tus keys)
tu balance de testnet.

## Backtest

```bash
python backtester.py --source real --days 365
```

Corre la estrategia de cruce de medias sobre el histórico e imprime:
retorno total, win rate, drawdown máximo, número de operaciones,
retorno promedio por operación, y las métricas de riesgo/calidad más
finas que agrega `compute_metrics()` (compartidas por los tres
backtesters de este repo — EMA, scalping BTC y Setup Engine):

- `sharpe_ratio`/`sortino_ratio`: anualizados sobre la curva de equity
  completa (candle a candle, incluyendo las velas sin posición abierta),
  asumiendo 0% de tasa libre de riesgo — la simplificación estándar
  para un backtest corto de cripto. Con pocas operaciones (como es el
  caso hoy) son una señal aproximada, no un score preciso.
- `profit_factor`: ganancia bruta en USD / pérdida bruta en USD entre
  operaciones cerradas. `None` cuando todas las operaciones cerradas
  ganaron (no hay pérdida contra la cual dividir — deliberadamente no
  es `0` ni infinito).
- `avg_mae_pct`/`avg_mfe_pct`: Max Adverse/Favorable Excursion promedio
  — cuánto llegó a estar en contra (MAE, `<=0`) y a favor (MFE, `>=0`)
  cada operación en algún punto de su vida, más allá de en qué precio
  terminó saliendo. Cada trade individual del reporte (`--export`)
  también trae su propio `mae_pct`/`mfe_pct`. Es lo que hubiera
  contestado de entrada la pregunta de si un take-profit del 4% tenía
  siquiera sentido, sin tener que correr el experimento aparte.

**`--walk-forward N`** (también en `scalping_backtester.py`): en vez de
una sola corrida sobre toda la ventana de `--days`, la parte en `N`
segmentos contiguos y corre exactamente la misma configuración
(sin cambiar ningún parámetro) en cada uno por separado, imprimiendo
una comparación lado a lado en vez de un solo reporte (ignora
`--export`):

```bash
python backtester.py --source real --days 90 --walk-forward 3
```

No es walk-forward *optimization* en el sentido clásico — este repo no
ajusta parámetros automáticamente a partir de los datos (`FAST_PERIOD`,
`STOP_LOSS_PCT`, etc. los elige una persona a mano vía `.env`), así que
no hay nada que "reoptimizar" por segmento. Lo que sí contesta, y es un
riesgo real que ya se dio en esta misma sesión: cada ajuste de
parámetro (umbrales de RSI, multiplicador de ATR, `TAKE_PROFIT_PCT`...)
se probó corriendo una y otra vez contra la *misma* ventana real de 30
días — la forma clásica de terminar ajustando contra el ruido de esa
ventana en particular en vez de encontrar una ventaja real. Si un
resultado solo aparece en uno de los segmentos, es más probable que
sea ruido de ese segmento que una ventaja real de la estrategia.

Una limitación a tener en cuenta: si una operación queda abierta justo
en el borde entre dos segmentos, ese segmento la marca a mercado en su
`final_capital`/`total_return_pct` (igual que ya hace un backtest
normal al final de los datos), pero sus métricas de operaciones
cerradas (`win_rate_pct`, `profit_factor`) todavía no la cuentan — así
que un segmento puede mostrar retorno positivo con 0% de win rate si
sus únicas operaciones cerradas perdieron pero terminó con una
ganancia de papel sin cerrar.

**`scalping_backtester.py --take-profit`** (BTC): a diferencia del
`--take-profit` de `backtester.py` (un % fijo, probado y descartado
para una estrategia de tendencia), acá el take-profit es el mismo
nivel de zona-premium (`range_low + premium_min% del rango`) que ya se
calculaba para el filtro de reward:risk en la entrada, nunca usado
antes como salida real — la salida seguía siendo solo el stop o la
señal de rango apagándose sola. Tiene mucho más sentido para una
estrategia de reversión a la media ("comprar el descuento, vender la
prima") que para una de tendencia, así que vale la pena probarlo antes
de descartarlo por la misma razón que el de PAXG:

```bash
python scalping_backtester.py --days 90 --walk-forward 3 --take-profit
```

**Parámetros de `scalping_strategy.py` como flags de CLI** (`--lookback`,
`--rsi-period`, `--rsi-oversold`, `--discount-max`, `--premium-min`,
`--min-range-atr-multiple`, `--stop-buffer-atr-multiple`,
`--min-reward-risk-ratio`): todos los números que se afinaron esta
sesión (`RSI_OVERSOLD`, `MIN_RANGE_ATR_MULTIPLE`, etc.) se calibraron
específicamente contra el ruido de velas de 5 minutos — probar un
`--timeframe` distinto (por ejemplo `4h`) con esos mismos valores no
prueba si la estrategia funciona a ese timeframe, prueba si esos
números en particular siguen sirviendo ahí, y la respuesta suele ser
que no (a 4h el RSI es mucho más suave y rara vez toca un umbral
calibrado para 5m, dando 0 operaciones). Reafinar para otro timeframe
es el mismo proceso iterativo de siempre, ahora sin tener que editar
código en cada vuelta:

```bash
python scalping_backtester.py --timeframe 4h --days 180 --walk-forward 3 --rsi-oversold 35
```

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
`risk_manager.stop_loss_price()` (un % fijo) o, con
`USE_STRUCTURAL_STOP=true`, de `risk_manager.structural_stop_price()`
— el último swing low confirmado (`context_engine.structure`, la misma
pieza que ya usa el stop del Setup Engine), con un colchón de
`STRUCTURAL_STOP_ATR_BUFFER_MULTIPLE` ATRs, y caída al % fijo si no hay
un swing usable todavía. Opt-in y apagado por defecto, como
`USE_PATTERN_FILTER`/`USE_SETUP_ENGINE`: probalo con
`backtester.py --structural-stop` antes de prenderlo en el cron. La
posición se cierra por lo que ocurra primero: el stop-loss se dispara,
o la EMA rápida vuelve a cruzar por debajo — en ese segundo caso, el
ciclo cancela el stop antes de vender por mercado, porque el stop deja
el balance "locked" (no disponible para otra orden). Si un ciclo
encuentra una posición abierta sin stop-loss vigente (por ejemplo, el
proceso se cortó justo después de comprar), lo reconstruye a partir del
último fill de compra en vez de dejar la posición desprotegida hasta el
próximo cruce.

No coloca take-profit — ni en real ni en el backtest por defecto. Para
*probar* (solo en `backtester.py`, no en `--trade`) si un take-profit
fijo ayudaría:

```bash
python backtester.py --source real --days 30 --take-profit
```

y compará contra la misma corrida sin la bandera. Es un experimento
deliberadamente barato: reusa `TAKE_PROFIT_PCT`/`risk_manager.
take_profit_price()`, que ya existían en el código pero nunca se usaban
en ningún lado. Antes de construir un target estructural (basado en
liquidez/swings, como el stop), vale la pena confirmar si siquiera la
versión más simple ayuda — la hipótesis de partida es que **no**,
porque esta es una estrategia de tendencia y su ventaja suele venir de
dejar correr a la ganadora hasta que la propia EMA le diga que se
acabó, no de ponerle un techo fijo.

**Nunca coloca órdenes si `BINANCE_TESTNET` no es `true`** — `executor.py`
lo verifica antes de cada orden y lanza `LiveTradingDisabledError` si no.

### Cada decisión queda auditada, no solo registrada

El dict que cada ciclo (EMA o Setup Engine) devuelve y loguea a
`logs/trading.log` ya no es solo `{"action": "buy", ...}` — trae un
campo `reason` en una sola oración explicando el porqué, más los
números concretos detrás de esa oración:

```json
{
  "action": "buy",
  "reason": "EMA fast crossed above slow with no position open; sized to risk 1.0% of capital against a flat_stop_loss_pct stop at 11750.2",
  "stop_price": 11750.2,
  "stop_source": "flat_stop_loss_pct",
  "size": 0.0417,
  "risk_pct": 1.0
}
```

Una entrada bloqueada trae el número real que la bloqueó, no solo el
nombre del límite — por ejemplo
`"today's realized loss (99.90%) has hit MAX_DAILY_LOSS_PCT"` con
`daily_loss_pct: 99.9` al lado, o
`"the last 5 closed trades all lost"` con `consecutive_losses_count: 5`.
Así una decisión se audita leyendo `logs/trading.log`, sin tener que
volver a este código para reconstruir por qué pasó lo que pasó. Ver
`main._ema_action_reason()`/`main._setup_engine_action_reason()`.

**No aplica al bot de BTC**: `scalping_backtester.py` no está conectado
a `main.py --trade` (ver "Decisiones tomadas hasta ahora" más abajo),
así que no hay un ciclo en vivo cuyas decisiones auditar todavía — esto
es específico a los dos ciclos que sí corren en `main.py`.

### Automatizarlo (cron o systemd timer)

`main.py --trade` está pensado para que algo externo lo invoque una vez
por hora en punto (cierre de vela para `TIMEFRAME=1h`) — el proceso en
sí nunca hace su propio loop ni scheduling.

**Opción 1: cron** (funciona en cualquier Linux/macOS, sin dependencias):

```bash
crontab -e
# agregar esta línea (ajustá la ruta a donde clonaste el repo):
0 * * * * /ruta/a/este/repo/scripts/run_trade_cycle.sh >> /ruta/a/este/repo/logs/cron.log 2>&1
```

`scripts/run_trade_cycle.sh` existe específicamente porque cron corre
con un entorno casi vacío — sin tu venv activado, sin tu `$PATH`
interactivo, sin tu directorio de trabajo. El script resuelve la raíz
del repo por sí mismo, activa `.venv` si existe, y corre
`python main.py --trade` — así el crontab solo necesita saber la ruta a
este archivo.

**Opción 2: systemd timer** (Linux con systemd; da `systemctl status`,
`journalctl`, y reintento automático si la máquina estuvo apagada):

```bash
# editar deploy/systemd/trading-bot.service: reemplazar el usuario y
# la ruta absoluta al repo, después:
sudo cp deploy/systemd/trading-bot.service deploy/systemd/trading-bot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-bot.timer
systemctl list-timers trading-bot.timer   # confirmar próxima corrida
journalctl -u trading-bot.service -f      # ver logs en vivo
```

Con cualquiera de las dos, cada corrida también queda en
`logs/trading.log` (rotado, ver `main._configure_logging()`) además de
donde cron/systemd la redirija — es la fuente de verdad si algo falla
a mitad de ciclo.

**Antes de dejarlo corriendo desatendido**, corré `python main.py` (sin
`--trade`) una vez a mano: valida la conexión, que `SYMBOL` esté
listado en este Testnet, y tus API keys — mucho más fácil de leer que
un fallo silencioso en la primera corrida de cron a las 3am.

### Notificaciones (`notifier.py`, opt-in)

`logs/trading.log` es la fuente de verdad, pero nadie lo mira en vivo.
Configurando cualquiera de estas dos variables en `.env`, `--trade` te
avisa cada vez que compra, vende, bloquea una entrada, o falla:

```bash
# Telegram: creá un bot con @BotFather, mandale un mensaje una vez, y
# mirá https://api.telegram.org/bot<token>/getUpdates para tu chat_id.
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# O un webhook genérico (Slack, Discord, lo que sea que acepte un POST
# con JSON) — funciona junto con Telegram o en vez de él.
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Ninguna de las dos es obligatoria — con ambas vacías (el default),
`--trade` se comporta exactamente igual que antes de que esto existiera.
No avisa en cada `hold` (sería spam cada hora) — solo en lo que de
verdad amerita mirar. Y es best-effort a propósito: si el envío mismo
falla (token vencido, sin red), queda logueado como warning pero nunca
tira abajo el ciclo de trading — una alerta rota no debería convertirse
en una razón más para que `--trade` falle.

### Historial de operaciones reales (`trade_journal.py`)

Los backtests son simulación; esto es lo que el bot **hizo de verdad**.
Cada corrida de `--trade` sincroniza el historial de operaciones real
de Binance para `SYMBOL` a `data/trade_journal.json`, deduplicado por
el id de operación propio de Binance — así no hace falta entrar a la
cuenta a mano para ver qué pasó. Guarda solo los fills crudos (id,
timestamp, lado, precio, cantidad, costo, comisión), no P&L por
posición todavía — armar esa lógica de emparejamiento entrada/salida
sin operaciones reales contra las cuales validarla sería adivinar la
forma en vez de aprenderla; un paso natural una vez que haya datos de
verdad. Igual que las notificaciones, es best-effort: un fallo acá
queda logueado pero nunca tira abajo el ciclo de trading.

**Para verlo en el dashboard** (posición abierta + historial real,
arriba de todo): si corrés `--trade` vía `scripts/run_trade_cycle.sh`
(cron o el timer de systemd de la sección anterior), el script ya copia
`data/trade_journal.json` a `dashboard/public/data/trade_journal.json`
al final de cada ciclo — no hace falta ningún `cp` a mano, solo
refrescar la página del dashboard después de una corrida. Si en cambio
corrés `main.py --trade` directo (sin el wrapper), copiá el archivo vos
mismo:

```bash
cp data/trade_journal.json dashboard/public/data/trade_journal.json
```

El archivo copiado no se commitea — son datos reales de tu cuenta, a
diferencia de los `backtest*.json` de al lado, que sí son demo/backtest
y están pensados para compartirse.

### Dead man's switch (`heartbeat.py`, opt-in)

Las notificaciones avisan si un ciclo *falla*; esto avisa si el cron
*dejó de correr*. Son problemas distintos: si el cron se desconfiguró
o el servidor se apagó, no hay ningún ciclo fallando — simplemente no
hay ciclos, y sin ciclos tampoco hay notificaciones. Silencio total en
vez de alerta.

`heartbeat.py` escribe `data/heartbeat.json` con la hora de la última
corrida exitosa en cada ciclo — útil para chequear a mano
(`cat data/heartbeat.json`), pero **no alcanza como dead man's switch
real**: un chequeo que corre en la misma máquina que lo que está
vigilando se apaga junto con ella, así que nunca puede notar su propio
silencio. Para eso hace falta algo externo:

```bash
# Un servicio gratis como https://healthchecks.io o https://cronitor.io,
# configurado ahí (fuera de esta máquina) para esperar un ping al menos
# cada ~1.5x el intervalo de --trade, y avisarte si no llega.
HEARTBEAT_PING_URL=https://hc-ping.com/tu-uuid-aca
```

Vacío por default — sin configurar nada, solo queda el archivo local.

## Filtro de patrones de chart (`patterns.py`, opt-in)

```bash
python backtester.py --source real --days 365 --pattern-filter
```

Detecta patrones clásicos de reversión/continuación — doble techo/piso
(dos picos o valles a precio similar, separados por un retroceso
significativo), hombro-cabeza-hombro/invertido (tres picos donde el
del medio es más alto, hombros a nivel similar), y triángulos
(ascendente, descendente, simétrico: dos rectas de tendencia
ajustadas sobre los dos últimos picos y los dos últimos valles) — y
los usa como **veto sobre las entradas** de la EMA: un patrón bajista
confirmado (doble techo, H&S, triángulo descendente, o un simétrico
rompiendo hacia abajo) bloquea una nueva entrada por
`PATTERN_VETO_LOOKBACK` velas, aunque la EMA acabe de cruzar hacia
arriba. No genera operaciones propias, no toca las salidas (esas
siguen siendo el cruce de EMA o el stop-loss) — es puramente un
filtro de confirmación, apagado por defecto (`USE_PATTERN_FILTER=false`
en `.env`).

Los pivotes (picos/valles) se detectan reusando
`context_engine.structure.find_swings` en vez de reimplementar la
lógica: esa función ya resuelve algo que una primera versión de este
módulo tenía mal — un patrón no puede confirmarse en una vela anterior
a que su último pivote (el hombro derecho, el segundo pico/valle)
esté realmente confirmado (`confirmed_at`), aunque el precio ya
hubiera roto el nivel de ruptura antes de eso. Confirmar antes es
exactamente el look-ahead que el propio `context_engine` señala como
el bug más peligroso de este tipo de código.

**Sobre los triángulos, dos detalles no obvios:**
- Una recta "plana" dentro de tolerancia es fácil de cumplir por
  puro ruido si no se exige que las dos rectas arranquen realmente
  separadas — por eso el triángulo pasa por el mismo filtro de
  profundidad mínima (`min_depth_pct`) que doble techo/piso y H&S,
  medido como la distancia entre ambas rectas al inicio del patrón.
- El escaneo compara cada par de picos consecutivos contra cada par
  de valles consecutivos (no solo los más recientes de cada lado),
  porque en un triángulo real los picos y valles se intercalan en el
  tiempo — al principio esto corría en ~14s sobre un año de velas 1h
  por recalcular la posición de cada pivote con `DataFrame.index.get_loc()`
  en cada iteración; precalcular esas posiciones en un dict lo bajó a
  <1s sin cambiar el resultado.

**Por qué está limitado a esto y no a "modelos predictivos":** los
patrones de chart (hombro-cabeza-hombro, doble techo, triángulos,
etc.) son reconocimiento de patrones sobre precio *pasado* — la misma
familia que el cruce de EMA, solo que geométrico en vez de basado en
medias. La evidencia académica (Lo-Mamaysky-Wang 2000,
Savin-Weller-Zvingelis 2007) los respalda como señal de confirmación
en velas diarias, "poco o nada" como estrategia autónoma, y varios
estudios en velas de 1 minuto que sí parecían prometedores (Miller et
al. 2019, Corbet et al. 2019) resultan no rentables en cuanto se
descuentan comisiones reales (Resta et al. 2020, Frömmel & Deprez
2024). Por eso: patrón como veto sobre una entrada ya validada por
EMA, no como estrategia propia.

## Daily Market Context Engine (`context_engine/`)

```bash
python -m context_engine --source real --days 540
python -m context_engine --days 540 --export dashboard/public/data/context.json
```

Describe **en qué estado está el mercado** antes de pensar en ninguna
entrada: régimen, bias por timeframe, estructura, liquidez, volatilidad,
posición dentro del rango, sesión activa, score, y —lo más importante—
qué tendría que pasar para que esa lectura quede invalidada.

No genera señales ni órdenes por sí solo. Es la capa de contexto sobre
la que corre el Setup Engine (ver más abajo) — `preferred_setups` ya no
sale vacío por default: trae `LIQUIDITY_SWEEP_RECLAIM` y
`CHART_PATTERN_REVERSAL` cuando confirman.

Pedile bastante historial: los niveles semanales necesitan ~60 velas de
1w para tener estructura, así que con `--days 120` el timeframe `1w`
queda en `UNDEFINED`. Con `--days 540` (unas 77 semanas) ya resuelve.

### Cómo está partido

Un módulo por motor, todos deterministas y puros — las mismas velas
producen siempre el mismo snapshot, y ninguna función lee el reloj, la
red ni el disco:

| Módulo | Qué resuelve |
| --- | --- |
| `validation.py` | Calidad de datos: timestamps duplicados, OHLC inconsistente, velas faltantes, gaps anómalos, volumen negativo |
| `timeframes.py` | `ensure_utc()` y resampleo anclado a UTC para derivar 4h/1d/1w desde 1h |
| `features.py` | Primitivos compartidos: `ema`, `atr`, `rsi`, `vwap`, `true_range`, anatomía de vela |
| `structure.py` | Swings confirmados, secuencia HH/HL/LH/LL, BOS, CHOCH, fase |
| `liquidity.py` | PDH/PDL/PWH/PWL/PMH/PML, equal highs/lows, sweeps con `reclaimed` y `displacement` |
| `volatility.py` | ATR, ATR%, régimen `VERY_LOW..EXTREME` por percentil, expansión/contracción |
| `ranges.py` | Premium/equilibrio/descuento, siempre con el rango nombrado |
| `sessions.py` | Asia/Londres/Nueva York en ventanas UTC |
| `bias.py` | Bias por timeframe desde estructura + alineación entre timeframes |
| `regime.py` | Régimen y clasificador de `market_state` (sin memoria — ver `state_machine.py`) |
| `state_machine.py` | Transiciones acotadas entre estados y registro de qué setups permite/prohíbe cada uno |
| `setups.py` | Setup Engine: `LIQUIDITY_SWEEP_RECLAIM` y `CHART_PATTERN_REVERSAL` |
| `scoring.py` | Score ponderado y versionado, con el desglose de cada componente |
| `engine.py` | Orquestador: arma el `ContextSnapshot`, las condiciones de no-trade y la invalidación |
| `llm_interface.py` | Solo el contrato JSON de entrada/salida, sin proveedor |

### Regla de oro: cero look-ahead

Todo primitivo estructural declara **cuándo se supo**, no solo cuándo
pasó. Un swing high en el índice `i` con `right=2` recién se conoce dos
velas después, así que cada `Swing` lleva `timestamp` (cuándo imprimió el
extremo) y `confirmed_at` (cuándo pasó a ser conocible). Filtrar por
`timestamp` en vez de `confirmed_at` es exactamente cómo se cuela el
look-ahead en un backtest.

Lo mismo con el resto: un BOS necesita un **cierre** más allá del nivel
(una mecha que lo perfora es liquidez tomada, no cambio de estructura), y
un sweep no existe hasta que cierra la vela que decide si el nivel se
recuperó o no.

`build_context(frames, as_of=...)` corta todo a lo conocible en ese
momento, y además **reconstruye** los timeframes altos desde la serie
base recortada. Esto último es sutil y es donde estaba el bug más feo del
milestone: una vela diaria se etiqueta con su hora de apertura, así que a
las 14:00 la vela de las 00:00 pasa cualquier filtro `index <= as_of`
—pero su máximo, mínimo y cierre se calcularon con las 24 horas
completas, nueve de ellas todavía en el futuro. El test que lo agarró:

```python
def test_context_is_identical_when_future_candles_are_removed(self):
    full = build_context(frames, as_of=t)
    truncated = build_context(build_timeframe_set(df[df.index <= t]), as_of=t)
    self.assertEqual(full.to_dict(), truncated.to_dict())
```

### El contrato

`ContextSnapshot.to_dict()` devuelve un dict plano y serializable. Todo
lo que expresa una decisión es un enum, nunca texto libre, y toda
hipótesis viaja con su evidencia (`reasons`) y con lo que la refutaría
(`invalidations`): una lectura que no se puede falsear no sirve.

```json
{
  "timestamp": "2026-09-04T19:00:00+00:00",
  "asset": "BTC/USDT",
  "version": "0.1.0",
  "data_quality": { "valid": true, "degraded": false, "issues": [] },
  "regime": { "primary": "RANGING", "volatility": "HIGH", "phase": "EXPANSION" },
  "multi_timeframe": { "1w": "DOWN", "1d": "RANGING", "4h": "RANGING", "1h": "DOWN" },
  "alignment": "PARTIAL_ALIGNMENT",
  "bias": { "direction": "BEARISH", "confidence": 0.44, "reasons": ["..."], "invalidations": ["..."] },
  "context_score": { "total": -5.0, "label": "BEARISH", "weights_version": "0.1.0", "components": [] },
  "market_state": "NO_TRADE",
  "preferred_direction": "NONE",
  "preferred_setups": [],
  "avoid": ["price sits mid-range on the daily range"],
  "no_trade": true,
  "invalidation": { "type": "CLOSE_ABOVE", "level": 82300.0, "detail": "..." }
}
```

Dos campos que conviene leer con atención:

- **`no_trade` viene con `avoid`**: "no operar" es una respuesta válida,
  pero solo sirve si dice *por qué*. Nunca sale un `no_trade: true` con
  la lista vacía.
- **`risk`** refleja de solo lectura los límites de `config.py`. El
  contexto no calcula tamaño de posición ni puede ensanchar un límite;
  eso sigue siendo territorio de `risk_manager.py`.

### Setup Engine y Market State Machine

`preferred_setups` ya no sale siempre vacío. `setups.py` implementa dos
setups:

- **`LIQUIDITY_SWEEP_RECLAIM`** (sección 43 del master prompt) — bias
  HTF direccional + un nivel de liquidez barrido que se recuperó con
  desplazamiento + una ruptura de estructura (BOS) en el timeframe de
  ejecución que confirma en la misma dirección. Las cuatro condiciones
  tienen que darse juntas; ninguna por sí sola alcanza.
- **`CHART_PATTERN_REVERSAL`** — reutiliza el detector de patrones de
  velas de `patterns.py` (doble techo/piso, HCH/invertido, triángulos),
  pero solo confirma como setup si el patrón coincide con el bias de
  mayor timeframe. Un patrón aislado nunca alcanza — es exactamente la
  misma regla, aplicada al análisis técnico de velas en vez de a
  liquidez/estructura.

Ninguno de los dos confía en una sola pieza de evidencia ("nunca trates
un patrón aislado como señal suficiente", regla principal del master
prompt).

`state_machine.py` construye la máquina de estados de la sección 15:
`classify_state()` sigue siendo un clasificador sin memoria (mira la
vela actual, no sabe qué pasó antes), y `next_state(previous_state,
classified_state)` es quien decide si ese cambio es válido — una
tabla de transiciones de un solo salto (ej. `TREND_UP` puede pasar a
`PULLBACK` o `REVERSAL_ATTEMPT`, pero no directo a `TREND_DOWN`) más
un escape inmediato para entrar/salir de `HIGH_VOLATILITY`/`NO_TRADE`
(el riesgo pisa por encima del estado, sección 38). Cada estado
también declara en `STATE_DEFINITIONS` qué setups permite — un setup
que se cumple mientras el estado es `NO_TRADE` igual no aparece en el
snapshot.

**`build_context()` sigue siendo puro** (no persiste nada): quien
llama es responsable de pasarle `previous_state` si quiere que la
máquina de estados tenga memoria. `main.py` lo resuelve reconstruyendo
el contexto también `as_of` la vela anterior en vez de guardar un
archivo de estado, para no romper el "sin estado local" del resto del
bot.

**Conectado a `main.py --trade`** vía `USE_SETUP_ENGINE=true` (`false`
por defecto). Con el flag activo, la entrada deja de depender del
cruce de EMA — la única razón para comprar es un setup `LONG`
confirmado — y el stop-loss se ubica en el nivel de invalidación
estructural del setup en vez del `STOP_LOSS_PCT` fijo. La salida (sin
tocar el stop-loss real, que sigue igual) ocurre cuando el bias deja de
ser alcista, el contexto pasa a `no_trade`, **o un patrón de vela
bajista se confirma** en el timeframe de ejecución — esta última, a
diferencia de la regla de entrada, no exige que el bias esté de acuerdo
primero: cerrar antes de tiempo reduce riesgo en vez de tomarlo, así
que el umbral de evidencia para salir es más bajo que para entrar.
Apagado por defecto:
cambia qué coloca las órdenes, y solo se probó contra los tests de este
repo — nunca contra un feed de Testnet en vivo. Como necesita
estructura semanal, el ciclo pasa a pedir `CONTEXT_HISTORY_DAYS` (540
por defecto) de historial **real** de Binance para construir el
contexto — mismo split que ya usa el backtest: datos reales para ver
el mercado, Testnet solo para ejecutar.

### Backtest del Setup Engine (`setup_engine_backtester.py`)

```bash
python setup_engine_backtester.py --days 200 --context-window-days 90
```

`build_context()` reconstruye los timeframes altos desde cero en cada
llamada, así que su costo crece con cuánto historial le des —
alimentarlo con una ventana que crece con cada vela del backtest (todo
el historial desde el principio) haría que el costo total sea
cuadrático: en las mediciones de esta sesión, cada llamada tarda
~0.06s + ~0.00007s por vela de historial, y sumando eso sobre un año
completo de decisiones da **~50-100 minutos** para un solo backtest.

Por eso este backtest usa una **ventana móvil**: en cada vela reconstruye
el contexto con los últimos `--context-window-days` días nada más, tal
como haría un ciclo en vivo de `main.py --trade` (que tampoco pide
"todo el historial", pide `CONTEXT_HISTORY_DAYS`). Eso vuelve el costo
total **lineal** en vez de cuadrático — sigue siendo lento (esperá del
orden de un minuto cada ~1000 velas testeadas en esta máquina), pero
predecible y sin crecer sin límite. Además, a diferencia de
`main.py`, este loop no necesita reconstruir el contexto dos veces por
vela para conseguir `previous_state` — lo toma directo del
`market_state` que la propia iteración anterior ya calculó, algo que
un proceso de un solo disparo como `main.py --trade` no puede hacer
sin guardar estado en disco.

**Recomendación práctica**: empezá con `--days` en el orden de cientos
(no un año) y con `--context-window-days` más chico que
`CONTEXT_HISTORY_DAYS` (90-180 en vez de 540) para iterar rápido;
subilo recién cuando quieras un resultado para tomar en serio. El
`--export` genera un JSON con la misma forma de métricas que
`backtester.py` (reusa `compute_metrics()`, no la reimplementa) más
`snapshots` (estado/bias/setups por vela testeada) en vez de las
columnas de EMA, ya que acá no hay un indicador único para graficar.

**Sigue pendiente, señalado a propósito**: nada de esto hace que
`build_context()` sea más rápido por dentro — solo evita que un
backtest le pida cada vez más historial. Si en algún momento hace
falta correr años de historia rápido (para optimizar parámetros, por
ejemplo), la solución real es un contexto incremental (actualizar
estructura/liquidez con la vela nueva en vez de recalcular todo desde
cero), que es un cambio bastante más grande y riesgoso — toca justo el
código que más cuidado tiene con el look-ahead.

### Versionado

Dos versiones separadas, porque cambian por motivos distintos:

- `CONTEXT_ENGINE_VERSION` sube cuando cambia el **significado** de algún
  campo, para que un snapshot viejo no se compare en silencio con uno
  nuevo.
- `WEIGHTS_VERSION` sube cuando se re-pesa el score. Repesar no cambia lo
  que significan los demás campos, pero sí invalida cualquier comparación
  de scores, así que va estampada en cada snapshot.

Los pesos viven en `params.py`, se pueden pasar por parámetro a
`build_context(weights=...)`, y **son estimaciones, no valores
optimizados**: elegidos para ser conservadores y legibles. Optimizarlos
en serio necesita walk-forward, que todavía no existe.

### Supuestos que conviene tener presentes

- **Sesiones en UTC fijo** (Asia 00–08, Londres 07–16, Nueva York 12–21).
  Londres y Nueva York se mueven con el horario de verano, así que
  durante parte del año estas ventanas están corridas una hora. Es la
  única aproximación consciente del motor; hacerlo bien implica mapear a
  `Europe/London` y `America/New_York`, y quedó para después.
- **`strategy.py` no se tocó.** Sigue calculando sus propias EMAs para el
  backtest de cruce. Refactorizarlo para usar `features.ema()` arrastraría
  al backtester, al dashboard y a dos tests sin ganar nada de
  comportamiento. De acá en adelante, `features.ema()` es el primitivo
  compartido para todo lo nuevo.
- **Los `events` son un input**, con default vacío. El motor nunca
  inventa un evento macro ni trae un feed propio; lista vacía significa
  "no sé de ningún evento", no "no hay eventos".
- **Las cadenas de `reasons`, `avoid` y `data_quality` salen en inglés**,
  igual que los enums: son el contrato de máquina, el mismo que consume
  `llm_interface.py`. Las etiquetas del panel sí están en español.

### Tests

```bash
python -m unittest test_context_validation test_context_structure \
                   test_context_liquidity test_context_engine \
                   test_context_setups test_context_state_machine -v
```

O toda la suite del repo (212 tests) con `python -m unittest discover`.

## Dashboard (React)

```bash
cd dashboard
npm install
npm run dev
```

Panel de lo que el bot **hizo de verdad** contra Testnet, no un visor
de backtests. Gráfico de velas real (librería
[lightweight-charts](https://tradingview.github.io/lightweight-charts/),
de TradingView) con la posición abierta marcada encima: si hay una
compra sin su venta correspondiente, se dibuja una línea en el precio
de entrada y arriba unas tarjetas con precio de entrada, precio actual,
P&L no realizado y cantidad. Las compras/ventas reales quedan marcadas
sobre las velas como flechas. Debajo, la tabla completa del historial
real de operaciones (`data/trade_journal.json`). El contexto de mercado
(régimen, liquidez, bias del `context_engine`) va en un panel plegable
al final — es lectura de fondo, no lo primero que hay que mirar. Ver
`dashboard/README.md`.

Tiene una pestaña por bot/símbolo (hoy: Oro/PAXG y BTC) — cada uno
corre como un cron separado, con sus propios archivos de estado, así
que nunca se mezcla la posición o el historial de uno con el del otro.
Agregar un símbolo nuevo es agregar una entrada a `TABS` en
`dashboard/src/App.jsx` con sus tres rutas de datos.

Sigue sin haber backend: todo sale de JSON estáticos en
`dashboard/public/data/`.

- **Historial real / posición abierta** (`trade_journal.json` para
  PAXG; `trade_journal_btc.json` para BTC, cuando ese bot exista): lo
  escribe `trade_journal.py` en cada ciclo de `main.py --trade`, en
  `data/<archivo>`. Si corrés el bot vía `scripts/run_trade_cycle.sh`
  (cron o el timer de systemd, ver más abajo), ese archivo se copia
  solo a `dashboard/public/data/<archivo>` en cada corrida — **no hace
  falta ningún `cp` manual** una vez que el cron está instalado. Son
  datos reales de tu cuenta: nunca se commitean (`.gitignore`).
- **Precio de fondo** (`backtest_paxg.json` / `backtest_btc.json`):
  velas OHLC reales para dibujar el gráfico, generadas una vez (y
  regeneradas cuando quieras refrescar el historial) con:

  ```bash
  python backtester.py --export dashboard/public/data/backtest_paxg.json
  SYMBOL="BTC/USDT" python backtester.py --export dashboard/public/data/backtest_btc.json
  ```

  Ninguna métrica de backtest (retorno, win rate, equity, etc.) se
  muestra ya en el dashboard — esas quedaron solo en la salida de
  `backtester.py` por consola/JSON crudo.
- **Contexto de mercado** (`context_paxg.json` / `context.json`,
  opcional): generado con
  `python -m context_engine --export dashboard/public/data/context_paxg.json`
  (agregando `--symbol BTC/USDT --export .../context.json` para BTC).
  Si no existe, el panel se repliega solo y explica el comando.

## Estado del proyecto

- [x] Estructura del proyecto
- [x] `data_fetcher.py`: conexión vía ccxt en modo sandbox + histórico OHLCV
- [x] `main.py`: verificación de conexión (`python main.py`) y ciclo de
  trading en testnet (`python main.py --trade`) — por EMA (default) o
  por Setup Engine (`USE_SETUP_ENGINE=true`)
- [x] `strategy.py`: cruce de EMA 20/50, long-only
- [x] `backtester.py`: simulación (con el mismo stop-loss real que usa
  `main.py --trade`) + métricas (retorno vs. buy & hold, win rate,
  drawdown, comisiones, duración de operaciones) y exportación a JSON
  para el dashboard (`--export`)
- [x] `backtester.compute_metrics()`: Sharpe/Sortino anualizados,
  profit factor (USD, no %), y MAE/MFE promedio por operación —
  compartido por los tres backtesters (EMA, scalping BTC, Setup
  Engine), no reimplementado en cada uno
- [x] `backtester.split_into_segments()` + `--walk-forward N`
  (`backtester.py` y `scalping_backtester.py`): corre la misma
  configuración sin cambios sobre N ventanas históricas separadas, en
  vez de una sola vez sobre toda la historia — chequeo de
  out-of-sample, no optimización walk-forward clásica (acá no hay
  parámetros que se ajusten solos). No está conectado a
  `setup_engine_backtester.py`: su loop ya es lento por diseño (un
  contexto multi-timeframe completo por vela) y trocearlo en segmentos
  necesitaría además decidir cómo darle a cada segmento su propia
  ventana de lookback previa, no solo repetir el `run_backtest` de
  turno
- [x] `risk_manager.py`: tamaño de posición por % de riesgo (con stop
  fijo o un `stop_price` estructural explícito), precios de
  stop-loss/take-profit, límite de pérdida diaria (`DailyLossTracker`)
  — conectado a `main.py --trade` (ver siguiente punto), no solo
  implementado
- [x] `daily_loss_state.py`: persiste la equity de arranque del día
  (UTC) en `data/daily_loss_state.json` para que `DailyLossTracker`
  (en memoria por diseño) funcione de verdad bajo el modelo de
  invocación de `--trade` — un proceso nuevo por cada corrida de cron,
  no uno de larga vida. Ambos ciclos (EMA y Setup Engine) bloquean
  nuevas entradas (`entry_blocked_by_daily_loss_limit`) apenas la
  pérdida realizada del día llega a `MAX_DAILY_LOSS_PCT`; una posición
  ya abierta sigue saliendo por sus reglas normales
- [x] `weekly_loss_state.py` + `WeeklyLossTracker`: mismo mecanismo que
  el límite diario, pero sobre la semana ISO (UTC) actual —
  independiente del diario, porque una racha de pérdidas chicas que
  nunca dispara el límite de un día puede sumar una semana mala.
  Bloquea con `entry_blocked_by_weekly_loss_limit` al llegar a
  `MAX_WEEKLY_LOSS_PCT`
- [x] `risk_manager.consecutive_losses()`: cuenta cuántas operaciones
  *cerradas* seguidas perdieron plata, leyendo directamente
  `trade_journal.json` (empareja compra/venta con el mismo FIFO trivial
  que ya usa el dashboard — long-only, una sola posición, nunca es
  ambiguo qué compra cierra qué venta). Al llegar a
  `MAX_CONSECUTIVE_LOSSES`, bloquea nuevas entradas
  (`entry_blocked_by_consecutive_losses`) hasta que una operación
  cierre en ganancia. No ajusta el riesgo por operación automáticamente
  — eso necesitaría evidencia estadística de que ayuda, no solo una
  mala racha
- [x] `portfolio_risk.py` + `MAX_PORTFOLIO_RISK_PCT`: riesgo
  correlacionado entre los dos bots que trackea este repo (PAXG y
  BTC) — cada uno dimensiona sus propias entradas contra solo su
  propio equity, así que dos posiciones abiertas a la vez en la misma
  cuenta podrían sumar más riesgo simultáneo real del que cualquiera
  de los dos config values sugiere por separado. Antes de abrir una
  entrada nueva, chequea si el balance del *otro* símbolo trackeado ya
  vale más que el mismo umbral de $10 que usa `in_position` en
  cualquier otro lado de este código — si sí, y `2x RISK_PER_TRADE_PCT`
  superaría `MAX_PORTFOLIO_RISK_PCT`, bloquea con
  `entry_blocked_by_portfolio_risk`. Suma simple, no ajustada por
  correlación — PAXG (oro) y BTC no tienen una correlación establecida
  que modelar, así que sumar en el peor caso es la opción honesta y
  conservadora hasta que haya historial real multi-activo que
  justifique algo más fino. **Hoy está inactivo en la práctica**:
  `scalping_backtester.py` no está conectado a ningún ciclo en vivo
  (ver más abajo), así que el chequeo casi siempre ve balance cero del
  otro activo — construido y testeado igual, listo para el día que BTC
  sí opere en vivo
- [x] `executor.meets_exchange_minimums()`: `risk_manager.position_size()`
  es matemática de riesgo pura, sin idea de los filtros propios de
  Binance (`LOT_SIZE`/`MIN_NOTIONAL`) — una posición bien dimensionada
  para `RISK_PER_TRADE_PCT` igual puede salir por debajo de lo mínimo
  que Binance acepta (una cuenta chica, o un stop inusualmente cerca de
  la entrada), y sin este chequeo esa orden llegaba a `create_order()`
  para que Binance la rechace, apareciendo como un fallo no controlado
  del ciclo (`logger.exception("Trading cycle failed")`) en vez de una
  decisión normal de "esta operación no es ejecutable a este tamaño de
  cuenta". Ahora ambos ciclos (EMA y Setup Engine) chequean esto antes
  de llamar a `create_order()` y, si no pasa, declinan con
  `entry_skipped_below_exchange_minimum` y el motivo exacto en `reason`
  — mismo tratamiento que cualquier otro límite de riesgo, no una
  excepción que tira abajo el ciclo
- [x] `backtester.py --take-profit`: experimento (apagado por defecto,
  no conectado a `main.py --trade`) de un take-profit fijo en
  `risk_manager.take_profit_price()` (`TAKE_PROFIT_PCT`), la versión
  más barata de probar antes de construir un target estructural. La
  hipótesis a validar contra datos reales: en una estrategia de
  tendencia como EMA 20/50, un techo fijo probablemente **resta**
  retorno en vez de sumarlo, porque el edge de la estrategia depende
  de dejar correr a las operaciones ganadoras hasta su propia señal de
  salida
- [x] `main.py --trade`: loggea a consola y a `logs/trading.log`
  (rotado) en vez de `print()`, y envuelve el ciclo completo en un
  `try/except` que deja el traceback en el log antes de salir con
  código de error — sin esto, un cron sin nadie mirando stdout en vivo
  no tiene forma de notar que una corrida falló
- [x] `main._ema_action_reason()`/`main._setup_engine_action_reason()`:
  cada decisión de cada ciclo (compra, venta, entrada bloqueada, hold,
  stop reconstruido) trae un `reason` en una oración más los números
  concretos detrás (`stop_price`, `stop_source`, `size`, `risk_pct`,
  o el `daily_loss_pct`/`weekly_loss_pct`/`consecutive_losses_count`
  real que bloqueó la entrada) — auditable desde `logs/trading.log`
  sin releer este código. `DailyLossTracker`/`WeeklyLossTracker`
  ganaron `current_loss_pct()` para esto (antes solo exponían el
  booleano `trading_allowed()`)
- [x] `retry.py`: reintentos con backoff exponencial ante
  `ccxt.NetworkError` transitorio (velas, balances, órdenes abiertas,
  historial de trades) — un timeout puntual ya no tira abajo el ciclo
  entero. Deliberadamente **no** se usa para colocar órdenes: un
  timeout ahí no dice si la orden llegó a Binance o no, así que
  reintentar a ciegas arriesga duplicarla
- [x] `executor.py`: `newClientOrderId` determinístico (símbolo + lado
  + hora UTC actual) en toda orden — si la misma orden se reenvía dos
  veces (un reintento, o el mismo ciclo horario corriendo de nuevo),
  Binance rechaza el duplicado en vez de ejecutarlo otra vez
- [x] `notifier.py`: notificaciones opt-in por Telegram y/o un webhook
  genérico para lo que `--trade` hace (compra/venta, entrada bloqueada,
  fallo) — silencioso en `hold` para no ser spam horario, y best-effort
  a propósito: un envío fallido queda logueado y nunca tira abajo el
  ciclo de trading. El mensaje de error se loguea sin el `str()` de la
  excepción — `requests`/`urllib3` suelen incluir la URL completa en
  ese mensaje, y esa URL tiene el token/webhook secreto embebido
- [x] `trade_journal.py`: sincroniza el historial real de operaciones
  de Binance a `data/trade_journal.json` en cada ciclo, deduplicado por
  id — el track record real del bot, no solo el de los backtests.
  Best-effort igual que `notifier.py`
- [x] `heartbeat.py`: archivo local + ping opcional a un servicio
  externo (healthchecks.io/cronitor) en cada ciclo exitoso — el ping
  externo es lo único que puede notar que el cron dejó de correr del
  todo, ya que un chequeo en la misma máquina se apaga junto con ella
- [x] `executor.py`: órdenes de mercado y stop-loss real
  (`STOP_LOSS_LIMIT`) en Testnet, bloqueadas si `USE_TESTNET` es `False`
- [x] `dashboard/`: panel React (Vite) con gráfico de velas real
  (lightweight-charts), selector de reportes (multi-símbolo) y panel
  explicativo de la estrategia
- [x] `patterns.py`: filtro de confirmación por doble techo/piso,
  hombro-cabeza-hombro/invertido y triángulos (ascendente/descendente/
  simétrico) sobre las entradas de EMA, opt-in (`USE_PATTERN_FILTER` /
  `--pattern-filter`), reusando los swings look-ahead-safe de
  `context_engine.structure`
- [x] `test_trading_cycle.py`: tests offline (sin red) del ciclo de
  trading contra un exchange falso — compra + coloca stop, cancela el
  stop antes de vender por señal, reconstruye un stop faltante, filtro
  de patrones bloquea/permite la entrada según corresponda.
- [x] `test_backtester.py`: tests de la simulación del stop-loss (sale
  por stop aunque la señal siga alcista; sale por señal si el stop
  nunca se toca) y del cableado del filtro de patrones.
- [x] `test_patterns.py`: tests de la detección de doble techo/piso,
  H&S/invertido, los tres triángulos, el veto temporal sobre entradas,
  que ningún patrón confirma antes de que su último pivote esté
  realmente confirmado, y una cota de falsos positivos sobre ruido
  (no promete cero, acota la tasa contra la que se vio en BTC real).
  Correr los cuatro con
  `python -m unittest test_trading_cycle test_backtester test_patterns test_context_engine -v`
  (o toda la suite del repo con `python -m unittest discover`)
- [x] `context_engine/`: motor determinista de contexto diario
  (validación de datos, estructura, liquidez, volatilidad, sesiones,
  rango, bias multi-timeframe, régimen y score versionado), con CLI
  `python -m context_engine --export ...` y panel en el dashboard
- [x] `context_engine/state_machine.py` + `setups.py`: transiciones de
  estado acotadas y el Setup Engine (`LIQUIDITY_SWEEP_RECLAIM` y
  `CHART_PATTERN_REVERSAL`), conectados a `preferred_setups`/`setups`
  del snapshot y, opt-in, al ciclo de trading real en `main.py`
- [x] `test_context_*.py`: 112 tests del motor de contexto (validación,
  estructura, liquidez, engine, setups, state machine), incluido el de
  look-ahead (el contexto en el momento `t` tiene que dar idéntico con o
  sin las velas posteriores a `t` en la entrada) y el de
  `CHART_PATTERN_REVERSAL` (un patrón confirmado contra el bias no
  alcanza, tiene que coincidir con él)
- [x] `test_risk_manager.py`: tests del `stop_price` explícito en
  `position_size()` (sizing correcto con un stop más ancho/angosto que
  el fijo, división por cero evitada, funciona también en `short`)
- [x] `setup_engine_backtester.py`: backtest del ciclo del Setup Engine
  con ventana móvil de contexto (costo lineal, no cuadrático, en la
  cantidad de velas testeadas), reusando `compute_metrics()` de
  `backtester.py`. Además de stop-loss/bias-flip/no_trade, una salida
  cierra directamente ante un patrón de vela bajista recién confirmado
  en el timeframe de ejecución — sin exigir que el bias esté de acuerdo
  primero (cerrar antes reduce riesgo, no lo toma, a diferencia de
  entrar). `test_setup_engine_backtester.py` (11 tests): la ventana
  nunca crece, `previous_state` viene de la propia iteración anterior,
  P&L correcto contra un contexto simulado, la salida por patrón
  bajista dispara con bias todavía alcista, los snapshots llevan
  OHLC/volumen/equity para el dashboard, y un smoke test end-to-end
  real (más lento, a propósito, para agarrar roturas de integración que
  un mock no vería)
- [x] `test_daily_loss_state.py` (5 tests) + tests nuevos en
  `test_trading_cycle.py` (freno de pérdida diaria bloqueando la
  entrada en ambos ciclos, el `try/except` de `main()` saliendo con
  código de error en vez de propagar la excepción, y el chequeo de que
  `SYMBOL` esté listado antes de operar)
- [x] `test_retry.py` (6 tests), `test_executor.py` (8 tests) y
  `test_data_fetcher.py` (3 tests): reintentos con backoff exponencial
  que se recuperan de un `NetworkError` transitorio pero no tocan
  ningún otro error, y el `newClientOrderId` determinístico por
  símbolo+lado+hora en toda orden colocada
- [x] `test_notifier.py` (8 tests): fan-out a los canales configurados
  nada más, un envío fallido nunca propaga, y — el que de verdad
  importa — que un error de red que ecoa la URL completa (típico de
  `requests`/`urllib3`) nunca termina logueando el token de Telegram ni
  la URL del webhook en texto plano
- [x] `test_trade_journal.py` (7 tests): dedup por id de Binance
  incluso cuando `fetch_my_trades` devuelve historial solapado (su
  comportamiento real), orden por timestamp, y que un fallo del
  journal nunca tira abajo el ciclo de trading
- [x] `test_heartbeat.py` (7 tests): mismo patrón que `test_notifier.py`
  — un fallo del ping nunca propaga ni loguea la URL secreta, y un
  error de escritura local sí propaga (no hay secreto que proteger ahí)
- [x] 212 tests en total en el repo — `python -m unittest discover`
- [x] `.github/workflows/tests.yml`: la suite corre sola en cada push y
  PR, contra Python 3.9 y 3.12 — 3.9 específicamente para volver a
  agarrar la regresión de sintaxis `X | None` que ya rompió una vez en
  ese entorno (ver `context_engine/schema.py` / `executor.py`), no solo
  la versión con la que se desarrolló. Sin acceso de red: todo lo que
  tocaría a Binance pasa por un `FakeExchange` en los tests, así que no
  hace falta ningún secret configurado para que corra

## Producción (correr desatendido contra Testnet)

Dinero real sigue siendo una decisión aparte y deliberada (ver
`executor.py`) — pero correr de forma confiable y desatendida contra
**Testnet** ya está validado de punta a punta:

- [x] **`PAXG/USDT` confirmado en Binance Spot Testnet.** Listado y
  operable, con velas reales sirviendo (~$4,432 al momento de probar).
- [x] **Cron/systemd timer listos.** `scripts/run_trade_cycle.sh` +
  `deploy/systemd/trading-bot.{service,timer}` — ver "Automatizarlo"
  arriba. Instalarlos (elegir dónde corre) queda a tu criterio.
- [x] **Corrida real de punta a punta contra Testnet, confirmada.**
  `python main.py --trade` autenticó, detectó una posición abierta real
  (`in_position_before: True`, vía `fetch_balance()`), y colocó una
  orden de venta real en el exchange (`order_id` devuelto por Binance)
  — no un mock. API keys, sizing, y colocación de órdenes, todo
  validado contra el Testnet real, no solo contra `FakeExchange` en los
  tests.

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
- **Dashboard sin backend**: en vez de levantar una API (FastAPI/Flask)
  para que React consuma datos en vivo, el dashboard lee JSON estáticos
  que `main.py --trade` y sus scripts van dejando en
  `dashboard/public/data/` (`trade_journal.json` para la posición
  abierta y el historial real, `backtest_paxg.json` como fondo de
  precio, `context_paxg.json` opcional). `scripts/run_trade_cycle.sh`
  ya copia `trade_journal.json` al dashboard en cada ciclo de cron, así
  que "posición abierta en vivo" no necesitó un servidor propio — solo
  refrescar la página después de un ciclo. Un feed de precio realmente
  en vivo (vela por vela, no la última cacheada) sí requeriría un
  backend — decisión que prefiero discutir contigo antes de
  construirla.
- **Resultado real de `--walk-forward 3` sobre los últimos 90 días**
  (`--days 90 --walk-forward 3`, corrido 2026-09-05 — un punto en el
  tiempo, no una garantía permanente; las condiciones de mercado
  cambian):
  - **PAXG (EMA), el bot que sí está en producción**: 2/3 segmentos de
    30 días ganadores (+0.45%, +0.71%), pero el del medio perdió
    -0.91% con solo 1 de 10 operaciones ganadoras (`profit_factor`
    0.08) — probablemente un tramo lateral/picado donde el cruce de
    EMA generó varias entradas falsas, la debilidad clásica de una
    estrategia de tendencia sin tendencia. Conclusión: los +0.87%/
    +1.42% de backtests puntuales de 30 días vistos antes en esta
    sesión eran el mejor de tres tramos posibles, no un promedio
    representativo — no asumir que el próximo mes se parece al último
    que se vio bien.
  - **BTC scalping (`scalping_backtester.py`), nunca conectado a
    `main.py --trade`**: **0/3 segmentos ganadores** (-2.87%, -1.93%,
    -2.78%), con `profit_factor` empeorando en cada tramo (0.60 → 0.40
    → 0.18). El peor tramo coincide con una suba real de BTC del
    +24.24% — la firma de una estrategia de reversión a la media
    peleando contra un mercado que rompió el rango y no volvió. Esto
    confirma, con tres ventanas independientes en vez de una sola, lo
    que ya sugerían los backtests previos (-32% → -2.99% → -2.81% a
    medida que se ajustaba). **Decisión: no conectar este bot a
    `main.py --trade` mientras no muestre una ventaja real** — seguir
    afinándolo es un proyecto de tuneo aparte, no algo a apurar.
  - **El mismo walk-forward con `--take-profit`** (target de zona-premium
    en vez de solo el stop/señal de rango): -0.85%, -1.99%, -2.84% —
    **sigue en 0/3 segmentos ganadores**. Mejora el primer tramo
    (`profit_factor` 0.60 → 0.87) pero no cambia el diagnóstico de
    fondo: `profit_factor` sigue por debajo de 1 en los tres tramos, o
    sea que el promedio de operación cerrada sigue perdiendo plata con
    o sin este take-profit. No es la pieza que le faltaba a esta
    estrategia.
