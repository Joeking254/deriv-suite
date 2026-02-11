from deriv_ws import DerivAPIError, DerivWS


async def place_trade(ws: DerivWS, symbol: str, direction: str, config, logger=None) -> dict:
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
    buy_info = buy.get("buy", {})
    contract_id = buy_info.get("contract_id")
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
        sell_price = float(poc.get("sell_price", 0) or 0)
        buy_price = float(poc.get("buy_price", 0) or 0)
        profit = sell_price - buy_price

    if logger:
        logger.info(
            "Trade closed | symbol=%s direction=%s profit=%s status=%s",
            symbol,
            direction,
            profit,
            poc.get("status"),
        )
    return {
        "contract_id": contract_id,
        "buy_price": float(poc.get("buy_price", buy_info.get("buy_price") or ask_price) or 0),
        "sell_price": float(poc.get("sell_price", 0) or 0),
        "profit": float(profit),
        "status": poc.get("status"),
        "payout": poc.get("payout"),
        "currency": config.currency,
    }
