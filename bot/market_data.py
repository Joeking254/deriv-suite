import re
import time
from typing import Dict, List, Optional, Tuple

from deriv_ws import DerivWS


_CONTRACT_CACHE: Dict[str, Dict[str, object]] = {}


def _parse_duration(value: str) -> Optional[Tuple[int, str]]:
    if not value:
        return None
    match = re.match(r"^(\d+)([a-zA-Z]+)$", value.strip())
    if not match:
        return None
    return int(match.group(1)), match.group(2)


def _duration_to_seconds(duration: int, unit: str) -> Optional[int]:
    unit = unit.lower()
    if unit == "s":
        return duration
    if unit == "m":
        return duration * 60
    if unit == "h":
        return duration * 3600
    if unit == "d":
        return duration * 86400
    return None


def _duration_in_range(duration: int, unit: str, min_d: Optional[str], max_d: Optional[str]) -> bool:
    if not min_d or not max_d:
        return True
    parsed_min = _parse_duration(min_d)
    parsed_max = _parse_duration(max_d)
    if not parsed_min or not parsed_max:
        return True

    min_val, min_unit = parsed_min
    max_val, max_unit = parsed_max

    if unit == min_unit == max_unit:
        return min_val <= duration <= max_val

    duration_sec = _duration_to_seconds(duration, unit)
    min_sec = _duration_to_seconds(min_val, min_unit)
    max_sec = _duration_to_seconds(max_val, max_unit)
    if duration_sec is None or min_sec is None or max_sec is None:
        return True
    return min_sec <= duration_sec <= max_sec


async def _get_contracts(ws: DerivWS, symbol: str, cache_ttl: int) -> List[dict]:
    now = time.time()
    cached = _CONTRACT_CACHE.get(symbol)
    if cached and now - float(cached.get("ts", 0)) < cache_ttl:
        return cached.get("data", [])

    resp = await ws.request({"contracts_for": symbol, "product_type": "basic"})
    available = resp.get("contracts_for", {}).get("available", [])
    _CONTRACT_CACHE[symbol] = {"ts": now, "data": available}
    return available


async def _contract_supported(
    ws: DerivWS,
    symbol: str,
    contract_types: Optional[List[str]],
    duration: int,
    duration_unit: str,
    cache_ttl: int,
) -> bool:
    if not contract_types:
        return True
    available = await _get_contracts(ws, symbol, cache_ttl)
    for contract in available:
        ctype = contract.get("contract_type")
        if ctype not in contract_types:
            continue
        if _duration_in_range(duration, duration_unit, contract.get("min_duration"), contract.get("max_duration")):
            return True
    return False


async def fetch_symbols(ws: DerivWS, config, logger=None) -> List[str]:
    resp = await ws.request({"active_symbols": "brief", "product_type": "basic"})
    raw_symbols = resp.get("active_symbols", [])
    symbols = []
    for s in raw_symbols:
        symbol = s.get("symbol")
        market = s.get("market")
        submarket = s.get("submarket")
        is_open = s.get("exchange_is_open")

        if not symbol:
            continue
        if is_open in (0, "0", False):
            continue
        if config.markets and market not in config.markets:
            continue
        if config.submarkets and submarket not in config.submarkets:
            continue
        if config.symbols and symbol not in config.symbols:
            continue
        if config.blocked_symbols and symbol in config.blocked_symbols:
            continue
        symbols.append(symbol)

    if config.max_symbols > 0 and len(symbols) > config.max_symbols:
        symbols = symbols[: config.max_symbols]

    if config.filter_contracts:
        filtered = []
        for symbol in symbols:
            try:
                ok = await _contract_supported(
                    ws,
                    symbol,
                    config.contract_types,
                    config.duration,
                    config.duration_unit,
                    config.contract_cache_ttl,
                )
                if ok:
                    filtered.append(symbol)
            except Exception:
                continue
        symbols = filtered

    if logger:
        logger.info("Loaded %d symbols", len(symbols))
    return symbols


async def get_candles(ws: DerivWS, symbol: str, config, granularity: Optional[int] = None, count: Optional[int] = None) -> List[dict]:
    payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": count or config.candle_count,
        "end": "latest",
        "start": 1,
        "style": "candles",
        "granularity": granularity or config.candle_granularity,
    }
    resp = await ws.request(payload)
    return resp.get("candles", [])


def extract_closes(candles: List[dict]) -> List[float]:
    closes = []
    for c in candles:
        try:
            closes.append(float(c.get("close")))
        except Exception:
            continue
    return closes
