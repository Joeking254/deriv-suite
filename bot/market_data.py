from typing import List, Optional

from deriv_ws import DerivWS


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

    if logger:
        logger.info("Loaded %d symbols", len(symbols))
    return symbols


async def get_candles(ws: DerivWS, symbol: str, config) -> List[dict]:
    payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count": config.candle_count,
        "end": "latest",
        "start": 1,
        "style": "candles",
        "granularity": config.candle_granularity,
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
