# Deriv Trading Bot Suite

This repo contains a minimal, production-ready Deriv trading bot that runs on a VPS.
It includes a dashboard that analyzes markets using RSI, EMA, MACD, and Bollinger Bands.
Confirmation thresholds, contract filtering, multi-timeframe checks, and token mode are configurable in .env.

Structure:
- bot: core trading bot
- backend: web API for the dashboard
- frontend: static dashboard UI
- systemd: service unit for Ubuntu
- logs: runtime logs
- install_ubuntu.sh: one-shot VPS installer

Quick start (Ubuntu 24.04):
1) Create a Python venv and install deps:
   python3 -m venv .venv
   ./.venv/bin/pip install -r bot/requirements.txt
2) Copy bot/.env.example to bot/.env and fill in your Deriv APP_ID + API_TOKEN
3) Run:
   ./.venv/bin/python bot/main.py
4) Run the dashboard:
   ./.venv/bin/python backend/serve.py
   Open http://localhost:8080

Notes:
- Use a trading account token (CR or VRTC). Wallet tokens (CRW/VRW) cannot trade.
- Start with DRY_RUN=true or a demo token before going live.
- The dashboard supports basic auth and IP allowlists via .env:
  - DASH_USER, DASH_PASS
  - DASH_ALLOWED_IPS (comma-separated)
  - TRUST_PROXY=true if using Nginx reverse proxy
- You can set demo/live tokens in the dashboard; they are stored in bot/tokens.json (gitignored).
