import math
from typing import Any, Dict, List, Optional


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def ema_series(values: List[float], period: int) -> Optional[List[float]]:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema_value = values[0]
    series = [ema_value]
    for v in values[1:]:
        ema_value = v * k + ema_value * (1 - k)
        series.append(ema_value)
    return series


def sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def stddev(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    mean = sma(values, period)
    if mean is None:
        return None
    variance = sum((v - mean) ** 2 for v in values[-period:]) / period
    return math.sqrt(variance)


def rsi(values: List[float], period: int) -> Optional[float]:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[-i] - values[-i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - (100 / (1 + rs))


def macd(values: List[float], fast: int, slow: int, signal: int) -> Optional[Dict[str, float]]:
    if len(values) < slow + signal:
        return None
    fast_series = ema_series(values, fast)
    slow_series = ema_series(values, slow)
    if not fast_series or not slow_series:
        return None
    macd_series = [f - s for f, s in zip(fast_series, slow_series)]
    signal_series = ema_series(macd_series, signal)
    if not signal_series:
        return None
    macd_line = macd_series[-1]
    signal_line = signal_series[-1]
    return {
        "macd_line": macd_line,
        "macd_signal": signal_line,
        "macd_hist": macd_line - signal_line,
    }


def compute_indicators(
    closes: List[float],
    rsi_period: int,
    rsi_overbought: float,
    rsi_oversold: float,
    ema_fast: int,
    ema_slow: int,
    macd_fast: int,
    macd_slow: int,
    macd_signal: int,
    bb_period: int,
    bb_stddev: float,
    confirmations_required: int,
) -> Optional[Dict[str, Any]]:
    rsi_value = rsi(closes, rsi_period)
    ema_f = ema(closes[-ema_fast:], ema_fast)
    ema_s = ema(closes[-ema_slow:], ema_slow)
    macd_values = macd(closes, macd_fast, macd_slow, macd_signal)
    bb_mid = sma(closes, bb_period)
    bb_dev = stddev(closes, bb_period)

    if rsi_value is None and ema_f is None and macd_values is None and bb_mid is None:
        return None

    last_close = closes[-1]
    bb_upper = None
    bb_lower = None
    bb_position = None
    if bb_mid is not None and bb_dev is not None:
        bb_upper = bb_mid + (bb_stddev * bb_dev)
        bb_lower = bb_mid - (bb_stddev * bb_dev)
        band = bb_upper - bb_lower
        if band > 0:
            bb_position = (last_close - bb_lower) / band

    trend = "neutral"
    if ema_f is not None and ema_s is not None:
        trend = "bullish" if ema_f >= ema_s else "bearish"

    confirmations = []
    call_score = 0
    put_score = 0
    available_checks = 0

    if rsi_value is not None:
        available_checks += 1
        if rsi_value <= rsi_oversold:
            confirmations.append("RSI oversold")
            call_score += 1
        elif rsi_value >= rsi_overbought:
            confirmations.append("RSI overbought")
            put_score += 1
        else:
            confirmations.append("RSI neutral")

    if ema_f is not None and ema_s is not None:
        available_checks += 1
        if ema_f >= ema_s:
            confirmations.append("EMA fast above EMA slow")
            call_score += 1
        else:
            confirmations.append("EMA fast below EMA slow")
            put_score += 1

    if macd_values is not None:
        available_checks += 1
        if macd_values["macd_hist"] >= 0:
            confirmations.append("MACD histogram positive")
            call_score += 1
        else:
            confirmations.append("MACD histogram negative")
            put_score += 1

    if bb_upper is not None and bb_lower is not None:
        available_checks += 1
        if last_close <= bb_lower:
            confirmations.append("Price below lower Bollinger band")
            call_score += 1
        elif last_close >= bb_upper:
            confirmations.append("Price above upper Bollinger band")
            put_score += 1
        else:
            confirmations.append("Price inside Bollinger bands")

    if available_checks == 0:
        return None

    required = max(1, min(confirmations_required, available_checks))
    signal = "WAIT"
    if call_score >= required and call_score > put_score:
        signal = "CALL"
    elif put_score >= required and put_score > call_score:
        signal = "PUT"

    result: Dict[str, Any] = {
        "rsi": float(rsi_value) if rsi_value is not None else None,
        "ema_fast": float(ema_f) if ema_f is not None else None,
        "ema_slow": float(ema_s) if ema_s is not None else None,
        "trend": trend,
        "signal": signal,
        "confirmations": confirmations,
        "ema_spread": float(ema_f - ema_s) if ema_f is not None and ema_s is not None else None,
        "call_score": call_score,
        "put_score": put_score,
        "confirmation_score": max(call_score, put_score),
        "confirmations_required": required,
        "last_close": last_close,
    }

    if macd_values:
        result.update(macd_values)
    if bb_mid is not None:
        result["bb_mid"] = float(bb_mid)
    if bb_upper is not None:
        result["bb_upper"] = float(bb_upper)
    if bb_lower is not None:
        result["bb_lower"] = float(bb_lower)
    if bb_position is not None:
        result["bb_position"] = float(bb_position)

    return result


def compute_signal(
    closes: List[float],
    rsi_period: int,
    rsi_overbought: float,
    rsi_oversold: float,
    ema_fast: int,
    ema_slow: int,
    macd_fast: int,
    macd_slow: int,
    macd_signal: int,
    bb_period: int,
    bb_stddev: float,
    confirmations_required: int,
) -> Optional[str]:
    indicators = compute_indicators(
        closes,
        rsi_period,
        rsi_overbought,
        rsi_oversold,
        ema_fast,
        ema_slow,
        macd_fast,
        macd_slow,
        macd_signal,
        bb_period,
        bb_stddev,
        confirmations_required,
    )
    if not indicators:
        return None
    signal = indicators.get("signal")
    if signal == "WAIT":
        return None
    return signal
