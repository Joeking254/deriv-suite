import asyncio
import base64
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
from trading import open_trade, place_trade  # noqa: E402

load_dotenv(ROOT / "bot" / ".env")
CONFIG = load_config()

SYMBOL_CACHE: Dict[str, object] = {"ts": 0.0, "data": []}
ANALYSIS_CACHE: Dict[str, Dict[str, object]] = {}
CONTRACT_CACHE: Dict[str, Dict[str, object]] = {}
PROBE_CACHE: Dict[str, Dict[str, object]] = {}
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


async def _with_ws_public() -> DerivWS:
    ws = DerivWS(CONFIG.app_id)
    await ws.connect()
    token, _ = resolve_token(CONFIG.api_token, CONFIG.account_mode, CONFIG.token_store_path)
    if token:
        try:
            await ws.request({"authorize": token})
        except DerivAPIError:
            pass
    return ws


def _parse_duration(value: str) -> Optional[Tuple[int, str]]:
    if not value:
        return None
    value = value.strip()
    num = ""
    unit = ""
    for ch in value:
        if ch.isdigit():
            num += ch
        else:
            unit += ch
    if not num or not unit:
        return None
    return int(num), unit.lower()


def _duration_to_seconds(amount: int, unit: str) -> Optional[int]:
    unit = unit.lower()
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 3600
    if unit == "d":
        return amount * 86400
    return None


def _duration_in_range(amount: int, unit: str, min_d: str | None, max_d: str | None) -> bool:
    if not min_d or not max_d:
        return True
    parsed_min = _parse_duration(min_d)
    parsed_max = _parse_duration(max_d)
    if not parsed_min or not parsed_max:
        return True

    min_val, min_unit = parsed_min
    max_val, max_unit = parsed_max

    if unit == min_unit == max_unit:
        return min_val <= amount <= max_val

    amount_sec = _duration_to_seconds(amount, unit)
    min_sec = _duration_to_seconds(min_val, min_unit)
    max_sec = _duration_to_seconds(max_val, max_unit)
    if amount_sec is None or min_sec is None or max_sec is None:
        return False
    return min_sec <= amount_sec <= max_sec


def _is_duration_error(exc: DerivAPIError) -> bool:
    text = f"{exc.code} {exc.message}".lower()
    return "duration" in text


async def _get_contracts_cached(symbol: str, product_type: str = "basic") -> Dict[str, object]:
    now = time.time()
    cached = CONTRACT_CACHE.get(symbol)
    ttl = getattr(CONFIG, "contract_cache_ttl", 3600)
    if cached and now - float(cached.get("ts", 0)) < ttl:
        return cached.get("data", {})

    ws = await _with_ws_public()
    try:
        resp = await ws.request({"contracts_for": symbol, "product_type": product_type})
        data = resp.get("contracts_for", {})
    finally:
        await ws.close()

    CONTRACT_CACHE[symbol] = {"ts": now, "data": data}
    return data


def _select_contract(available: List[dict], direction: str) -> Optional[dict]:
    candidates = [c for c in available if c.get("contract_type") == direction]
    if not candidates:
        return None

    def sort_key(item: dict) -> Tuple[int, int]:
        parsed = _parse_duration(item.get("min_duration") or "")
        if parsed:
            amount, unit = parsed
            unit_weight = {"t": 0, "s": 1, "m": 2, "h": 3, "d": 4}.get(unit, 9)
            return unit_weight, amount
        return (9, 999999)

    candidates.sort(key=sort_key)
    return candidates[0]


async def _probe_duration(symbol: str, direction: str) -> Optional[Tuple[int, str]]:
    ws = await _with_ws_public()
    try:
        candidates = [
            (1, "t"),
            (2, "t"),
            (5, "t"),
            (10, "t"),
            (15, "t"),
            (30, "t"),
            (1, "m"),
            (2, "m"),
            (5, "m"),
            (10, "m"),
            (15, "m"),
            (30, "m"),
            (1, "h"),
            (2, "h"),
            (1, "d"),
        ]
        for amount, unit in candidates:
            try:
                await ws.request(
                    {
                        "proposal": 1,
                        "amount": CONFIG.stake,
                        "basis": "stake",
                        "contract_type": direction,
                        "currency": CONFIG.currency,
                        "duration": amount,
                        "duration_unit": unit,
                        "symbol": symbol,
                    }
                )
                return amount, unit
            except DerivAPIError:
                continue
        return None
    finally:
        await ws.close()


async def _probe_duration_cached(symbol: str, direction: str) -> Optional[Tuple[int, str]]:
    key = f"{symbol}:{direction}"
    now = time.time()
    cached = PROBE_CACHE.get(key)
    if cached and now - float(cached.get("ts", 0)) < 3600:
        return cached.get("data")
    result = await _probe_duration(symbol, direction)
    PROBE_CACHE[key] = {"ts": now, "data": result}
    return result


async def _trade_params(symbol: str, direction: str) -> Optional[Dict[str, object]]:
    data = await _get_contracts_cached(symbol)
    available = data.get("available", [])
    contract = _select_contract(available, direction)
    if not contract:
        return None
    min_duration = contract.get("min_duration")
    max_duration = contract.get("max_duration")
    parsed = _parse_duration(min_duration) if min_duration else None
    duration_value = parsed[0] if parsed else None
    duration_unit = parsed[1] if parsed else None
    if duration_value is None or duration_unit is None:
        probed = await _probe_duration_cached(symbol, direction)
        if probed:
            duration_value, duration_unit = probed
    return {
        "contract_type": contract.get("contract_type"),
        "min_duration": min_duration,
        "max_duration": max_duration,
        "duration": duration_value,
        "duration_unit": duration_unit,
    }


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

    trade_params = {}
    for direction in ("CALL", "PUT"):
        params = await _trade_params(symbol, direction)
        if params:
            trade_params[direction] = params

    recommended_trade = None
    signal = indicators.get("signal")
    if signal in trade_params:
        recommended_trade = {
            "direction": signal,
            **trade_params[signal],
        }

    payload = {
        "symbol": symbol,
        "timestamp": int(now),
        **indicators,
        "entry_ready": indicators.get("signal") in ("CALL", "PUT"),
        "trade": recommended_trade,
        "trade_params": trade_params,
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


@app.get("/api/active-symbols")
async def active_symbols(
    product_type: str = Query("basic"),
    brief: bool = Query(True),
    open_only: bool = Query(True),
) -> Dict[str, object]:
    ws = await _with_ws_public()
    try:
        resp = await ws.request(
            {
                "active_symbols": "brief" if brief else "full",
                "product_type": product_type,
            }
        )
    except DerivAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await ws.close()

    symbols = resp.get("active_symbols", [])
    if open_only:
        symbols = [s for s in symbols if s.get("exchange_is_open") in (1, "1", True)]
    return {"symbols": symbols, "count": len(symbols)}


@app.get("/api/contracts")
async def contracts(
    symbol: str = Query(..., min_length=2),
    product_type: str = Query("basic"),
) -> Dict[str, object]:
    try:
        data = await _get_contracts_cached(symbol, product_type)
        return data
    except DerivAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/trading-times")
async def trading_times(date: str | None = None) -> Dict[str, object]:
    ws = await _with_ws_public()
    try:
        payload = {"trading_times": date} if date else {"trading_times": "today"}
        resp = await ws.request(payload)
        return resp.get("trading_times", {})
    except DerivAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await ws.close()


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


@app.get("/api/contract")
async def contract_status(contract_id: int = Query(..., ge=1)) -> Dict[str, object]:
    ws = await _with_ws()
    try:
        resp = await ws.request({"proposal_open_contract": 1, "contract_id": contract_id})
        poc = resp.get("proposal_open_contract", {})
        profit = poc.get("profit")
        if profit is None:
            sell_price = float(poc.get("sell_price", 0) or 0)
            buy_price = float(poc.get("buy_price", 0) or 0)
            profit = sell_price - buy_price
        return {
            "contract_id": contract_id,
            "status": poc.get("status"),
            "is_sold": bool(poc.get("is_sold")),
            "profit": float(profit or 0),
            "buy_price": float(poc.get("buy_price", 0) or 0),
            "sell_price": float(poc.get("sell_price", 0) or 0),
            "payout": poc.get("payout"),
            "currency": poc.get("currency") or CONFIG.currency,
            "current_spot": poc.get("current_spot"),
            "entry_spot": poc.get("entry_spot"),
            "date_start": poc.get("date_start"),
            "date_expiry": poc.get("date_expiry"),
        }
    except DerivAPIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    wait = payload.get("wait", False)
    if isinstance(wait, str):
        wait = wait.strip().lower() in ("1", "true", "yes", "y")
    elif isinstance(wait, (int, float)):
        wait = bool(wait)
    else:
        wait = bool(wait)
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

    params = await _trade_params(symbol, direction)
    if not params:
        raise HTTPException(status_code=400, detail="No contracts available for this symbol")

    requested_duration = duration_value
    requested_unit = duration_unit_value

    if duration_value is None or duration_unit_value is None:
        duration_value = params.get("duration")
        duration_unit_value = params.get("duration_unit")

    if duration_value is None or duration_unit_value is None:
        raise HTTPException(status_code=400, detail="Unable to resolve a valid duration for this contract")

    adjusted = False
    if not _duration_in_range(duration_value, duration_unit_value, params.get("min_duration"), params.get("max_duration")):
        duration_value = params.get("duration")
        duration_unit_value = params.get("duration_unit")
        adjusted = True

    async with TRADE_LOCK:
        ws = DerivWS(CONFIG.app_id)
        try:
            await ws.connect()
            await ws.request({"authorize": token})
            async def execute_trade() -> Dict[str, object]:
                if wait:
                    return await place_trade(
                        ws,
                        symbol,
                        direction,
                        CONFIG,
                        duration=duration_value,
                        duration_unit=duration_unit_value,
                    )
                opened = await open_trade(
                    ws,
                    symbol,
                    direction,
                    CONFIG,
                    duration=duration_value,
                    duration_unit=duration_unit_value,
                )
                return {**opened, "status": "open", "is_sold": False}

            try:
                result = await execute_trade()
            except DerivAPIError as exc:
                if _is_duration_error(exc):
                    probed = await _probe_duration_cached(symbol, direction)
                    if probed:
                        new_duration, new_unit = probed
                        if new_duration != duration_value or new_unit != duration_unit_value:
                            duration_value = new_duration
                            duration_unit_value = new_unit
                            adjusted = True
                            result = await execute_trade()
                        else:
                            raise
                    else:
                        raise
                else:
                    raise
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
        "duration_requested": requested_duration,
        "duration_unit_requested": requested_unit,
        "duration_adjusted": adjusted,
        "min_duration": params.get("min_duration"),
        "max_duration": params.get("max_duration"),
        **result,
    }
