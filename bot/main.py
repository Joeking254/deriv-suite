import asyncio
from datetime import datetime
from dotenv import load_dotenv

from config import load_config
from deriv_ws import DerivAPIError, DerivWS
from logger import setup_logger
from market_data import extract_closes, fetch_symbols, get_candles
from risk import RiskManager
from strategy import compute_indicators



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

    while True:
        try:
            await ws.connect()
            auth = await ws.request({"authorize": config.api_token})
            acct = auth.get("authorize", {})
            logger.info("Authorized | loginid=%s currency=%s", acct.get("loginid"), acct.get("currency"))

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

                    profit = await place_trade(ws, symbol, direction, config, logger)
                    risk.record_trade(profit)

                    await asyncio.sleep(config.post_trade_cooldown_sec)

                except DerivAPIError as e:
                    logger.warning("Deriv error on %s: %s", symbol, str(e))
                    symbol_cooldowns[symbol] = datetime.utcnow().timestamp() + config.symbol_cooldown_sec
                    await asyncio.sleep(config.loop_sleep_sec)
                except Exception as e:
                    logger.exception("Unexpected error on %s: %s", symbol, str(e))
                    await asyncio.sleep(config.loop_sleep_sec)

            ping_task.cancel()

        except Exception as e:
            logger.exception("Connection error: %s", str(e))
            await ws.close()
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
