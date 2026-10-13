import logging

STABLES = {"USD", "USDC", "USDT", "DAI", "EUR", "GBP", "EURC", "PYUSD"}


def discover_products(exchange, settings, balances):
    log = logging.getLogger("discovery")
    selected = []
    if settings.products:
        for product_id in settings.products:
            product_id = product_id.strip().upper()
            info = exchange.try_product(product_id)
            if not info:
                log.warning("product %s not available, skipping", product_id)
                continue
            base = info.get("base_currency_id")
            quote = info.get("quote_currency_id")
            selected.append((product_id, base, quote, info))
        return selected

    preference = settings.quote_currency_preference
    for base, balance in sorted(balances.items()):
        if base in STABLES:
            continue
        quantity = balance.get("available", 0.0)
        if quantity <= 0:
            continue
        for quote in preference:
            product_id = f"{base}-{quote}"
            info = exchange.try_product(product_id)
            if not info:
                continue
            if info.get("status") != "online" or info.get("trading_disabled"):
                continue
            price = float(info.get("price") or 0)
            value = price * quantity
            if value < settings.min_product_value:
                break
            selected.append((product_id, base, quote, info))
            break
    selected.sort(key=lambda item: float(item[3].get("price") or 0) * balances[item[1]]["available"], reverse=True)
    return selected
