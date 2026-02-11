import asyncio
import time
from datetime import datetime

from alerts import send_telegram_message
from dotenv import load_dotenv

from config import load_config
from deriv_ws import DerivAPIError, DerivWS
from logger import setup_logger
from market_data import extract_closes, fetch_symbols, get_candles
from risk import RiskManager
from strategy import compute_indicators
from token_store import resolve_token
from trading import place_trade


def _duration_to_seconds(duration: int, unit: str) -> int | None:
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


async def _notify(config, logger, text: str) -> None:
    if not config.alert_telegram:
        return
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    try:
        await asyncio.to_thread(
            send_telegram_message,
            config.telegram_bot_token,
            config.telegram_chat_id,
            text,
        )
    except Exception:
        logger.warning("Failed to send alert")


async def simulate_trade(ws: DerivWS, symbol: str, direction: str, entry_price: float, config, logger) -> float:
    duration_seconds = _duration_to_seconds(config.duration, config.duration_unit)
    if duration_seconds is None:
        logger.warning("Paper trade skipped: unsupported duration unit %s", config.duration_unit)
        return 0.0
    await asyncio.sleep(duration_seconds)
    candles = await get_candles(ws, symbol, config)
    closes = extract_closes(candles)
    if not closes:
        return 0.0
    exit_price = closes[-1]
    win = exit_price > entry_price if direction == "CALL" else exit_price < entry_price
    profit = config.stake if win else -config.stake
    logger.info(
        "Paper trade closed | symbol=%s direction=%s entry=%.5f exit=%.5f profit=%.2f",
        symbol,
        direction,
        entry_price,
        exit_price,
        profit,
    )
    return float(profit)



async def main() -> None:
    load_dotenv()
    config = load_config()
    logger = setup_logger(config.log_level, config.log_file)

    logger.info("Starting Deriv bot | DRY_RUN=%s", config.dry_run)

    ws = DerivWS(config.app_id)
    risk = RiskManager(config.max_daily_loss, config.max_consecutive_losses)
    symbol_cooldowns = {}
    symbol_index = 0
    open_positions = 0
    last_trade_ts = 0.0

    while True:
        try:
            token, mode = resolve_token(config.api_token, config.account_mode, config.token_store_path)
            if not token:
                logger.warning("Missing API token for selected mode. Sleeping 30s")
                await asyncio.sleep(30)
                continue

            await ws.connect()
            auth = await ws.request({"authorize": token})
            acct = auth.get("authorize", {})
            logger.info(
                "Authorized | loginid=%s currency=%s mode=%s",
                acct.get("loginid"),
                acct.get("currency"),
                mode,
            )
            await _notify(config, logger, f"Deriv bot authorized ({mode}): {acct.get('loginid')}")

            symbols = await fetch_symbols(ws, config, logger)
            if not symbols:
                logger.warning("No symbols available. Sleeping...")
                await asyncio.sleep(30)
                continue

            ping_task = asyncio.create_task(ws.ping_loop())

            while True:
                if not risk.can_trade():
                    logger.warning("Risk limit reached. Sleeping 60s")
                    await asyncio.sleep(60)
                    continue

                symbol = symbols[symbol_index % len(symbols)]
                symbol_index += 1

                cooldown_until = symbol_cooldowns.get(symbol)
                if cooldown_until and cooldown_until > datetime.utcnow().timestamp():
                    await asyncio.sleep(1)
                    continue

                if config.max_open_positions > 0 and open_positions >= config.max_open_positions:
                    await asyncio.sleep(config.loop_sleep_sec)
                    continue

                if config.global_trade_cooldown_sec > 0:
                    wait_for = config.global_trade_cooldown_sec - (time.time() - last_trade_ts)
                    if wait_for > 0:
                        await asyncio.sleep(min(wait_for, config.loop_sleep_sec))
                        continue

                try:
                    candles = await get_candles(ws, symbol, config)
                    closes = extract_closes(candles)
                    if len(closes) < config.rsi_period + 2:
                        await asyncio.sleep(config.loop_sleep_sec)
                        continue

                    indicators = compute_indicators(
                        closes,
                        config.rsi_period,
                        config.rsi_overbought,
                        config.rsi_oversold,
                        config.ema_fast,
                        config.ema_slow,
                        config.macd_fast,
                        config.macd_slow,
                        config.macd_signal,
                        config.bb_period,
                        config.bb_stddev,
                        config.confirmations_required,
                    )

                    if not indicators:
                        await asyncio.sleep(config.loop_sleep_sec)
                        continue

                    direction = indicators.get("signal")
                    if direction not in ("CALL", "PUT"):
                        await asyncio.sleep(config.loop_sleep_sec)
                        continue

                    htf_ok = True
                    htf_trend = None
                    if config.htf_enabled:
                        htf_candles = await get_candles(
                            ws,
                            symbol,
                            config,
                            granularity=config.htf_granularity,
                            count=config.htf_candle_count,
                        )
                        htf_closes = extract_closes(htf_candles)
                        if len(htf_closes) >= config.rsi_period + 2:
                            htf_indicators = compute_indicators(
                                htf_closes,
                                config.rsi_period,
                                config.rsi_overbought,
                                config.rsi_oversold,
                                config.ema_fast,
                                config.ema_slow,
                                config.macd_fast,
                                config.macd_slow,
                                config.macd_signal,
                                config.bb_period,
                                config.bb_stddev,
                                config.confirmations_required,
                            )
                            if htf_indicators:
                                htf_trend = htf_indicators.get("trend")
                                if direction == "CALL" and htf_trend != "bullish":
                                    htf_ok = False
                                if direction == "PUT" and htf_trend != "bearish":
                                    htf_ok = False

                    if config.htf_enabled and not htf_ok:
                        await asyncio.sleep(config.loop_sleep_sec)
                        continue

                    logger.info(
                        "Signal | symbol=%s direction=%s score=%s/%s rsi=%.2f macd_hist=%.4f",
                        symbol,
                        direction,
                        indicators.get("confirmation_score"),
                        indicators.get("confirmations_required"),
                        indicators.get("rsi") or 0.0,
                        indicators.get("macd_hist") or 0.0,
                    )

                    trade_mode = (config.trade_mode or "auto").lower()
                    if trade_mode not in ("auto", "manual", "hybrid"):
                        trade_mode = "auto"

                    if trade_mode == "manual":
                        if config.alert_on_trade:
                            await _notify(
                                config,
                                logger,
                                f"Signal {direction} | {symbol} (manual mode, no auto trade)",
                            )
                        await asyncio.sleep(config.post_trade_cooldown_sec)
                        continue

                    if config.dry_run:
                        logger.info("DRY_RUN: skipping order")
                        await asyncio.sleep(config.post_trade_cooldown_sec)
                        continue

                    open_positions += 1
                    try:
                        if config.alert_on_trade:
                            await _notify(
                                config,
                                logger,
                                f"Signal {direction} | {symbol} score {indicators.get('confirmation_score')}/{indicators.get('confirmations_required')}",
                            )
                        entry_price = float(closes[-1])
                        if config.paper_trade:
                            profit = await simulate_trade(ws, symbol, direction, entry_price, config, logger)
                        else:
                            result = await place_trade(ws, symbol, direction, config, logger)
                            profit = float(result.get("profit", 0))
                        risk.record_trade(profit)
                        last_trade_ts = time.time()
                        if config.alert_on_trade:
                            await _notify(
                                config,
                                logger,
                                f"Trade closed | {symbol} {direction} profit {profit:.2f}",
                            )
                    finally:
                        open_positions = max(0, open_positions - 1)

                    await asyncio.sleep(config.post_trade_cooldown_sec)

                except DerivAPIError as e:
                    logger.warning("Deriv error on %s: %s", symbol, str(e))
                    symbol_cooldowns[symbol] = datetime.utcnow().timestamp() + config.symbol_cooldown_sec
                    if config.alert_on_error:
                        await _notify(config, logger, f"Deriv error on {symbol}: {str(e)}")
                    await asyncio.sleep(config.loop_sleep_sec)
                except Exception as e:
                    logger.exception("Unexpected error on %s: %s", symbol, str(e))
                    if config.alert_on_error:
                        await _notify(config, logger, f"Unexpected error on {symbol}: {str(e)}")
                    await asyncio.sleep(config.loop_sleep_sec)

            ping_task.cancel()

        except Exception as e:
            logger.exception("Connection error: %s", str(e))
            await ws.close()
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
