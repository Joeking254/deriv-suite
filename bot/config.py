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
    account_mode: str
    token_store_path: str
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
    htf_enabled: bool
    htf_granularity: int
    htf_candle_count: int
    filter_contracts: bool
    contract_types: Optional[List[str]]
    contract_cache_ttl: int

    max_daily_loss: float
    max_consecutive_losses: int
    symbol_cooldown_sec: int
    post_trade_cooldown_sec: int
    global_trade_cooldown_sec: int
    max_open_positions: int
    loop_sleep_sec: int
    dry_run: bool
    paper_trade: bool

    alert_telegram: bool
    telegram_bot_token: str
    telegram_chat_id: str
    alert_on_trade: bool
    alert_on_error: bool

    log_level: str
    log_file: str


def load_config() -> Config:
    app_id = os.getenv("APP_ID", "").strip()
    api_token = os.getenv("API_TOKEN", "").strip()
    account_mode = os.getenv("ACCOUNT_MODE", "demo").strip().lower()
    token_store_path = os.getenv("TOKEN_STORE_PATH", "").strip()
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
    htf_enabled = _parse_bool(os.getenv("HTF_ENABLED", "false"), False)
    htf_granularity = _parse_int(os.getenv("HTF_GRANULARITY", "300"), 300)
    htf_candle_count = _parse_int(os.getenv("HTF_CANDLE_COUNT", "200"), 200)
    filter_contracts = _parse_bool(os.getenv("FILTER_CONTRACTS", "true"), True)
    contract_types = _parse_list(os.getenv("CONTRACT_TYPES", "CALL,PUT"))
    contract_cache_ttl = _parse_int(os.getenv("CONTRACT_CACHE_TTL", "3600"), 3600)

    max_daily_loss = _parse_float(os.getenv("MAX_DAILY_LOSS", "10"), 10.0)
    max_consecutive_losses = _parse_int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"), 3)
    symbol_cooldown_sec = _parse_int(os.getenv("SYMBOL_COOLDOWN_SEC", "60"), 60)
    post_trade_cooldown_sec = _parse_int(os.getenv("POST_TRADE_COOLDOWN_SEC", "10"), 10)
    global_trade_cooldown_sec = _parse_int(os.getenv("GLOBAL_TRADE_COOLDOWN_SEC", "30"), 30)
    max_open_positions = _parse_int(os.getenv("MAX_OPEN_POSITIONS", "1"), 1)
    loop_sleep_sec = _parse_int(os.getenv("LOOP_SLEEP_SEC", "5"), 5)
    dry_run = _parse_bool(os.getenv("DRY_RUN", "true"), True)
    paper_trade = _parse_bool(os.getenv("PAPER_TRADE", "false"), False)

    alert_telegram = _parse_bool(os.getenv("ALERT_TELEGRAM", "false"), False)
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    alert_on_trade = _parse_bool(os.getenv("ALERT_ON_TRADE", "true"), True)
    alert_on_error = _parse_bool(os.getenv("ALERT_ON_ERROR", "true"), True)

    log_level = os.getenv("LOG_LEVEL", "INFO").strip()
    log_file = os.getenv("LOG_FILE", "../logs/bot.log").strip()

    if not app_id:
        raise ValueError("APP_ID is required")
    return Config(
        app_id=app_id,
        api_token=api_token,
        account_mode=account_mode or "demo",
        token_store_path=token_store_path,
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
        htf_enabled=htf_enabled,
        htf_granularity=htf_granularity,
        htf_candle_count=htf_candle_count,
        filter_contracts=filter_contracts,
        contract_types=contract_types,
        contract_cache_ttl=contract_cache_ttl,
        max_daily_loss=max_daily_loss,
        max_consecutive_losses=max_consecutive_losses,
        symbol_cooldown_sec=symbol_cooldown_sec,
        post_trade_cooldown_sec=post_trade_cooldown_sec,
        global_trade_cooldown_sec=global_trade_cooldown_sec,
        max_open_positions=max_open_positions,
        loop_sleep_sec=loop_sleep_sec,
        dry_run=dry_run,
        paper_trade=paper_trade,
        alert_telegram=alert_telegram,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        alert_on_trade=alert_on_trade,
        alert_on_error=alert_on_error,
        log_level=log_level,
        log_file=log_file,
    )
