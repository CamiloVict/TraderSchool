# Notas del entorno del usuario

- **El usuario corre este proyecto con `python3.9`**, no con un `python`
  ni `python3` genérico. Su máquina (macOS) no tiene un binario
  `python` a secas, y no usa un `.venv` (o si existe, no confiar en
  que tenga un `python` propio utilizable). Cualquier script o
  instrucción que invoque Python para este repo debe usar `python3.9`
  explícitamente, o resolver el intérprete probando `python3.9` primero
  (ver `scripts/run_trade_cycle.sh` para el patrón ya usado).
- El repo vive en `~/TradeSchool` en su máquina (lo movimos fuera de
  `~/Documents` porque `cron` necesitaba permiso de "Acceso completo al
  disco" para carpetas protegidas de macOS).
- El bot corre real contra **Binance Spot Testnet** vía un cron
  instalado en su Mac — no hay acceso de red desde este entorno de
  desarrollo hacia Binance ni hacia la máquina del usuario. Cualquier
  verificación que necesite datos reales (backtests con historial real,
  confirmar que el cron corrió, revisar archivos locales) hay que
  pedírsela a él con el comando exacto a correr.
- **SIEMPRE testear cualquier implementación/experimento nuevo de
  backtest contra los DOS bots — PAXG/oro (`backtester.py`) y BTC
  (`scalping_backtester.py`) — no solo uno.** Aunque uno de los dos no
  esté (todavía) conectado a `main.py --trade`, sigue siendo parte del
  repo y una implementación nueva (una métrica, un flag, un chequeo
  como `--walk-forward`) tiene que darle al usuario el comando para
  correrla en ambos, no en el que sea más cómodo/relevante en el
  momento. Si un concepto no aplica 1:1 a los dos (p.ej. algo pensado
  específicamente para el cruce de EMA), decirlo explícitamente en vez
  de omitir el otro bot en silencio.
