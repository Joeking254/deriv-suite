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



async def place_trade(ws: DerivWS, symbol: str, direction: str, config, logger) -> float:
    proposal = await ws.request(
        {
            "proposal": 1,
            "amount": config.stake,
            "basis": "stake",
            "contract_type": direction,
            "currency": config.currency,
            "duration": config.duration,
            "duration_unit": config.duration_unit,
            "symbol": symbol,
        }
    )
    prop = proposal.get("proposal", {})
    prop_id = prop.get("id")
    ask_price = prop.get("ask_price")
    if not prop_id:
        raise DerivAPIError("proposal", "Missing proposal id")

    buy = await ws.request({"buy": prop_id, "price": ask_price})
    contract_id = buy.get("buy", {}).get("contract_id")
    if not contract_id:
        raise DerivAPIError("buy", "Missing contract id")

    def done_predicate(msg: dict) -> bool:
        poc = msg.get("proposal_open_contract", {})
        status = poc.get("status")
        is_sold = poc.get("is_sold")
        return bool(is_sold) or status in ("sold", "expired", "won", "lost")

    final_msg = await ws.subscribe_until({"proposal_open_contract": 1, "contract_id": contract_id}, done_predicate)
    poc = final_msg.get("proposal_open_contract", {})
    profit = poc.get("profit")
    if profit is None:
        sell_price = float(poc.get("sell_price", 0))
        buy_price = float(poc.get("buy_price", 0))
        profit = sell_price - buy_price

    logger.info(
        "Trade closed | symbol=%s direction=%s profit=%s status=%s",
        symbol,
        direction,
        profit,
        poc.get("status"),
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
            await ws.connect()
            auth = await ws.request({"authorize": config.api_token})
            acct = auth.get("authorize", {})
            logger.info("Authorized | loginid=%s currency=%s", acct.get("loginid"), acct.get("currency"))
            await _notify(config, logger, f"Deriv bot authorized: {acct.get('loginid')}")

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
                            profit = await place_trade(ws, symbol, direction, config, logger)
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
