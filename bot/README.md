# Deriv Bot

A simple, safe baseline bot for Deriv that:
- Connects to Deriv WebSocket API
- Pulls candles and generates a signal
- Places a Rise/Fall trade (CALL/PUT)
- Enforces basic risk controls

## Setup
1) Install dependencies:
   python3 -m venv .venv
   ./.venv/bin/pip install -r requirements.txt

2) Configure:
   cp .env.example .env
   # edit .env with your APP_ID and API_TOKEN

3) Run:
   ./.venv/bin/python main.py

4) Optional dashboard:
   ./.venv/bin/python ../backend/serve.py

## Configuration
Key values in .env:
- APP_ID, API_TOKEN
- ACCOUNT_MODE (demo/live), TOKEN_STORE_PATH (where dashboard stores tokens)
- MARKETS, SUBMARKETS, SYMBOLS (comma lists or all)
- STAKE, DURATION, DURATION_UNIT
- RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD, EMA_FAST, EMA_SLOW
- MACD_FAST, MACD_SLOW, MACD_SIGNAL, BB_PERIOD, BB_STDDEV
- CONFIRMATIONS_REQUIRED (how many indicators must align)
- HTF_ENABLED, HTF_GRANULARITY, HTF_CANDLE_COUNT (multi-timeframe filter)
- FILTER_CONTRACTS, CONTRACT_TYPES (skip symbols that don't support your contract/duration)
- MAX_OPEN_POSITIONS, GLOBAL_TRADE_COOLDOWN_SEC
- PAPER_TRADE (simulate entry/exit without sending orders)
- MAX_DAILY_LOSS, MAX_CONSECUTIVE_LOSSES
- DRY_RUN=true to avoid live trades
- ALERT_TELEGRAM, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
