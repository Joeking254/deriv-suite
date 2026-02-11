from dataclasses import dataclass
import os
from typing import List, Optional


def _parse_list(value: str) -> Optional[List[str]]:
    if not value:
        return None
    v = value.strip()
    if not v or v.lower() == "all":
        return None
    return [item.strip() for item in v.split(",") if item.strip()]


def _parse_int(value: str, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_float(value: str, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass
class Config:
    app_id: str
    api_token: str
    currency: str

    markets: Optional[List[str]]
    submarkets: Optional[List[str]]
    symbols: Optional[List[str]]
    blocked_symbols: Optional[List[str]]
    max_symbols: int

    strategy: str
    duration: int
    duration_unit: str
    stake: float

    candle_granularity: int
    candle_count: int
    rsi_period: int
    rsi_overbought: float
    rsi_oversold: float
    ema_fast: int
    ema_slow: int
    macd_fast: int
    macd_slow: int
    macd_signal: int
    bb_period: int
    bb_stddev: float
    confirmations_required: int

    max_daily_loss: float
    max_consecutive_losses: int
    symbol_cooldown_sec: int
    post_trade_cooldown_sec: int
    loop_sleep_sec: int
    dry_run: bool

    log_level: str
    log_file: str


def load_config() -> Config:
    app_id = os.getenv("APP_ID", "").strip()
    api_token = os.getenv("API_TOKEN", "").strip()
    currency = os.getenv("CURRENCY", "USD").strip()

    markets = _parse_list(os.getenv("MARKETS", "all"))
    submarkets = _parse_list(os.getenv("SUBMARKETS", "all"))
    symbols = _parse_list(os.getenv("SYMBOLS", "all"))
    blocked_symbols = _parse_list(os.getenv("BLOCKED_SYMBOLS", ""))
    max_symbols = _parse_int(os.getenv("MAX_SYMBOLS", "25"), 25)

    strategy = os.getenv("STRATEGY", "RSI_EMA").strip()
    duration = _parse_int(os.getenv("DURATION", "1"), 1)
    duration_unit = os.getenv("DURATION_UNIT", "m").strip()
    stake = _parse_float(os.getenv("STAKE", "1.0"), 1.0)

    candle_granularity = _parse_int(os.getenv("CANDLE_GRANULARITY", "60"), 60)
    candle_count = _parse_int(os.getenv("CANDLE_COUNT", "120"), 120)
    rsi_period = _parse_int(os.getenv("RSI_PERIOD", "14"), 14)
    rsi_overbought = _parse_float(os.getenv("RSI_OVERBOUGHT", "70"), 70.0)
    rsi_oversold = _parse_float(os.getenv("RSI_OVERSOLD", "30"), 30.0)
    ema_fast = _parse_int(os.getenv("EMA_FAST", "12"), 12)
    ema_slow = _parse_int(os.getenv("EMA_SLOW", "26"), 26)
    macd_fast = _parse_int(os.getenv("MACD_FAST", "12"), 12)
    macd_slow = _parse_int(os.getenv("MACD_SLOW", "26"), 26)
    macd_signal = _parse_int(os.getenv("MACD_SIGNAL", "9"), 9)
    bb_period = _parse_int(os.getenv("BB_PERIOD", "20"), 20)
    bb_stddev = _parse_float(os.getenv("BB_STDDEV", "2.0"), 2.0)
    confirmations_required = _parse_int(os.getenv("CONFIRMATIONS_REQUIRED", "3"), 3)

    max_daily_loss = _parse_float(os.getenv("MAX_DAILY_LOSS", "10"), 10.0)
    max_consecutive_losses = _parse_int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"), 3)
    symbol_cooldown_sec = _parse_int(os.getenv("SYMBOL_COOLDOWN_SEC", "60"), 60)
    post_trade_cooldown_sec = _parse_int(os.getenv("POST_TRADE_COOLDOWN_SEC", "10"), 10)
    loop_sleep_sec = _parse_int(os.getenv("LOOP_SLEEP_SEC", "5"), 5)
    dry_run = _parse_bool(os.getenv("DRY_RUN", "true"), True)

    log_level = os.getenv("LOG_LEVEL", "INFO").strip()
    log_file = os.getenv("LOG_FILE", "../logs/bot.log").strip()

    if not app_id:
        raise ValueError("APP_ID is required")
    if not api_token:
        raise ValueError("API_TOKEN is required")

    return Config(
        app_id=app_id,
        api_token=api_token,
        currency=currency,
        markets=markets,
        submarkets=submarkets,
        symbols=symbols,
        blocked_symbols=blocked_symbols,
        max_symbols=max_symbols,
        strategy=strategy,
        duration=duration,
        duration_unit=duration_unit,
        stake=stake,
        candle_granularity=candle_granularity,
        candle_count=candle_count,
        rsi_period=rsi_period,
        rsi_overbought=rsi_overbought,
        rsi_oversold=rsi_oversold,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        macd_fast=macd_fast,
        macd_slow=macd_slow,
        macd_signal=macd_signal,
        bb_period=bb_period,
        bb_stddev=bb_stddev,
        confirmations_required=confirmations_required,
        max_daily_loss=max_daily_loss,
        max_consecutive_losses=max_consecutive_losses,
        symbol_cooldown_sec=symbol_cooldown_sec,
        post_trade_cooldown_sec=post_trade_cooldown_sec,
        loop_sleep_sec=loop_sleep_sec,
        dry_run=dry_run,
        log_level=log_level,
        log_file=log_file,
    )
