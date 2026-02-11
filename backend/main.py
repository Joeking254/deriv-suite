import asyncio
import base64
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "bot"
FRONTEND_DIR = ROOT / "frontend"

if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from config import load_config  # noqa: E402
from deriv_ws import DerivAPIError, DerivWS  # noqa: E402
from market_data import extract_closes, fetch_symbols, get_candles  # noqa: E402
from strategy import compute_indicators  # noqa: E402
from token_store import load_tokens, resolve_token, update_tokens  # noqa: E402
from trading import place_trade  # noqa: E402

load_dotenv(ROOT / "bot" / ".env")
CONFIG = load_config()

SYMBOL_CACHE: Dict[str, object] = {"ts": 0.0, "data": []}
ANALYSIS_CACHE: Dict[str, Dict[str, object]] = {}
SYMBOL_CACHE_TTL = int(os.getenv("SYMBOL_CACHE_TTL", "300"))
ANALYSIS_CACHE_TTL = int(os.getenv("ANALYSIS_CACHE_TTL", "20"))
SCAN_LIMIT = int(os.getenv("SCAN_LIMIT", "6"))
DASH_USER = os.getenv("DASH_USER", "").strip()
DASH_PASS = os.getenv("DASH_PASS", "").strip()
DASH_ALLOWED_IPS = [ip.strip() for ip in os.getenv("DASH_ALLOWED_IPS", "").split(",") if ip.strip()]
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").strip().lower() in ("1", "true", "yes", "y")

app = FastAPI(title="Deriv Signal Desk")
TRADE_LOCK = asyncio.Lock()

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def _client_ip(request: Request) -> str:
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _basic_auth_valid(request: Request) -> bool:
    if not DASH_USER or not DASH_PASS:
        return True
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return False
    try:
        encoded = auth.split(" ", 1)[1].strip()
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False
    return secrets.compare_digest(username, DASH_USER) and secrets.compare_digest(password, DASH_PASS)


@app.middleware("http")
async def enforce_access(request: Request, call_next):
    if DASH_ALLOWED_IPS:
        ip = _client_ip(request)
        if ip not in DASH_ALLOWED_IPS:
            return JSONResponse({"detail": "Forbidden"}, status_code=403)

    if not _basic_auth_valid(request):
        return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})

    return await call_next(request)


async def _with_ws() -> DerivWS:
    token, mode = resolve_token(CONFIG.api_token, CONFIG.account_mode, CONFIG.token_store_path)
    if not token:
        raise HTTPException(status_code=503, detail="Missing API token for selected mode")
    ws = DerivWS(CONFIG.app_id)
    await ws.connect()
    await ws.request({"authorize": token})
    return ws


def _token_status() -> Dict[str, object]:
    tokens = load_tokens(CONFIG.token_store_path)
    return {
        "active_mode": tokens.get("active_mode", "demo"),
        "demo_token_set": bool(tokens.get("demo_token")),
        "live_token_set": bool(tokens.get("live_token")),
    }


async def _get_symbols_cached() -> List[str]:
    now = time.time()
    cached = SYMBOL_CACHE.get("data", [])
    if cached and now - float(SYMBOL_CACHE.get("ts", 0)) < SYMBOL_CACHE_TTL:
        return cached

    ws = await _with_ws()
    try:
        symbols = await fetch_symbols(ws, CONFIG)
        SYMBOL_CACHE["data"] = symbols
        SYMBOL_CACHE["ts"] = now
        return symbols
    finally:
        await ws.close()


async def _analyze_symbol(symbol: str) -> Dict[str, object]:
    now = time.time()
    cached = ANALYSIS_CACHE.get(symbol)
    if cached and now - float(cached.get("ts", 0)) < ANALYSIS_CACHE_TTL:
        return cached["data"]

    ws = await _with_ws()
    try:
        candles = await get_candles(ws, symbol, CONFIG)
    finally:
        await ws.close()

    closes = extract_closes(candles)
    if len(closes) < CONFIG.rsi_period + 2:
        raise HTTPException(status_code=400, detail="Not enough candle data for indicators")

    indicators = compute_indicators(
        closes,
        CONFIG.rsi_period,
        CONFIG.rsi_overbought,
        CONFIG.rsi_oversold,
        CONFIG.ema_fast,
        CONFIG.ema_slow,
        CONFIG.macd_fast,
        CONFIG.macd_slow,
        CONFIG.macd_signal,
        CONFIG.bb_period,
        CONFIG.bb_stddev,
        CONFIG.confirmations_required,
    )
    if not indicators:
        raise HTTPException(status_code=400, detail="Could not compute indicators")

    payload = {
        "symbol": symbol,
        "timestamp": int(now),
        **indicators,
        "entry_ready": indicators.get("signal") in ("CALL", "PUT"),
    }

    ANALYSIS_CACHE[symbol] = {"ts": now, "data": payload}
    return payload


@app.get("/")
def index() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(index_path)


@app.get("/api/health")
def health() -> Dict[str, object]:
    status = _token_status()
    return {
        "status": "ok",
        "app_id": CONFIG.app_id,
        "active_mode": status["active_mode"],
        "demo_token_set": status["demo_token_set"],
        "live_token_set": status["live_token_set"],
        "timestamp": int(time.time()),
    }


@app.get("/api/config")
def config() -> Dict[str, object]:
    return {
        "account_mode": CONFIG.account_mode,
        "trade_mode": CONFIG.trade_mode,
        "markets": CONFIG.markets,
        "submarkets": CONFIG.submarkets,
        "symbols": CONFIG.symbols,
        "max_symbols": CONFIG.max_symbols,
        "duration": CONFIG.duration,
        "duration_unit": CONFIG.duration_unit,
        "stake": CONFIG.stake,
        "candle_granularity": CONFIG.candle_granularity,
        "candle_count": CONFIG.candle_count,
        "rsi_period": CONFIG.rsi_period,
        "rsi_overbought": CONFIG.rsi_overbought,
        "rsi_oversold": CONFIG.rsi_oversold,
        "ema_fast": CONFIG.ema_fast,
        "ema_slow": CONFIG.ema_slow,
        "macd_fast": CONFIG.macd_fast,
        "macd_slow": CONFIG.macd_slow,
        "macd_signal": CONFIG.macd_signal,
        "bb_period": CONFIG.bb_period,
        "bb_stddev": CONFIG.bb_stddev,
        "confirmations_required": CONFIG.confirmations_required,
        "htf_enabled": CONFIG.htf_enabled,
        "htf_granularity": CONFIG.htf_granularity,
        "htf_candle_count": CONFIG.htf_candle_count,
        "filter_contracts": CONFIG.filter_contracts,
        "contract_types": CONFIG.contract_types,
        "global_trade_cooldown_sec": CONFIG.global_trade_cooldown_sec,
        "max_open_positions": CONFIG.max_open_positions,
        "paper_trade": CONFIG.paper_trade,
        "dry_run": CONFIG.dry_run,
    }


@app.get("/api/auth")
def auth_status() -> Dict[str, object]:
    return _token_status()


@app.post("/api/auth")
async def auth_update(request: Request) -> Dict[str, object]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    active_mode = payload.get("active_mode")
    demo_token = payload.get("demo_token")
    live_token = payload.get("live_token")

    tokens = update_tokens(
        active_mode=active_mode,
        demo_token=demo_token if isinstance(demo_token, str) and demo_token.strip() else None,
        live_token=live_token if isinstance(live_token, str) and live_token.strip() else None,
        token_store_path=CONFIG.token_store_path,
    )

    active_mode = tokens.get("active_mode", "demo")
    if active_mode == "demo" and not tokens.get("demo_token") and not CONFIG.api_token:
        raise HTTPException(status_code=400, detail="Demo token not set")
    if active_mode == "live" and not tokens.get("live_token"):
        raise HTTPException(status_code=400, detail="Live token not set")

    return _token_status()


@app.get("/api/symbols")
async def symbols(limit: int = Query(100, ge=1, le=300)) -> Dict[str, object]:
    try:
        symbols_list = await _get_symbols_cached()
    except DerivAPIError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"symbols": symbols_list[:limit], "count": len(symbols_list)}


@app.get("/api/analysis")
async def analysis(symbol: str = Query(..., min_length=2)) -> Dict[str, object]:
    try:
        return await _analyze_symbol(symbol)
    except DerivAPIError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/scan")
async def scan(limit: int = Query(None, ge=1, le=20)) -> Dict[str, object]:
    try:
        symbols_list = await _get_symbols_cached()
        batch = symbols_list[: (limit or SCAN_LIMIT)]
        results = []
        for symbol in batch:
            try:
                results.append(await _analyze_symbol(symbol))
            except HTTPException:
                continue
        return {"results": results, "count": len(results)}
    except DerivAPIError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/api/balance")
async def balance() -> Dict[str, object]:
    ws = await _with_ws()
    try:
        resp = await ws.request({"balance": 1})
        bal = resp.get("balance", {})
        return {
            "balance": bal.get("balance"),
            "currency": bal.get("currency"),
            "loginid": bal.get("loginid"),
        }
    finally:
        await ws.close()


@app.post("/api/trade")
async def trade(request: Request) -> Dict[str, object]:
    trade_mode = (CONFIG.trade_mode or "auto").lower()
    if trade_mode == "auto":
        raise HTTPException(status_code=400, detail="Manual trades are disabled in auto mode")

    if CONFIG.dry_run or CONFIG.paper_trade:
        raise HTTPException(status_code=400, detail="Disable DRY_RUN and PAPER_TRADE to execute trades")

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    symbol = str(payload.get("symbol", "")).strip()
    direction = str(payload.get("direction", "")).strip().upper()
    duration = payload.get("duration")
    duration_unit = payload.get("duration_unit")
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")

    indicators = await _analyze_symbol(symbol)
    signal = indicators.get("signal")
    if direction:
        if direction not in ("CALL", "PUT"):
            raise HTTPException(status_code=400, detail="direction must be CALL or PUT")
        if signal not in ("CALL", "PUT") or direction != signal:
            raise HTTPException(status_code=400, detail="direction does not match current signal")
    else:
        direction = signal if signal in ("CALL", "PUT") else ""

    if direction not in ("CALL", "PUT"):
        raise HTTPException(status_code=400, detail="No trade signal for this symbol")

    token, mode = resolve_token(CONFIG.api_token, CONFIG.account_mode, CONFIG.token_store_path)
    if not token:
        raise HTTPException(status_code=400, detail="Missing API token for selected mode")

    duration_value = None
    if isinstance(duration, int) and duration > 0:
        duration_value = duration
    elif isinstance(duration, str) and duration.isdigit():
        duration_value = int(duration)

    duration_unit_value = None
    if isinstance(duration_unit, str) and duration_unit.strip():
        duration_unit_value = duration_unit.strip().lower()

    async with TRADE_LOCK:
        ws = DerivWS(CONFIG.app_id)
        try:
            await ws.connect()
            await ws.request({"authorize": token})
            result = await place_trade(
                ws,
                symbol,
                direction,
                CONFIG,
                duration=duration_value,
                duration_unit=duration_unit_value,
            )
        except DerivAPIError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            await ws.close()

    return {
        "symbol": symbol,
        "direction": direction,
        "mode": mode,
        **result,
    }
