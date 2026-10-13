import logging
import time
import uuid
from decimal import Decimal, ROUND_DOWN

from coinbase.rest import RESTClient

GRANULARITY_SECONDS = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 300,
    "FIFTEEN_MINUTE": 900,
    "THIRTY_MINUTE": 1800,
    "ONE_HOUR": 3600,
    "TWO_HOUR": 7200,
    "SIX_HOUR": 21600,
    "ONE_DAY": 86400,
}


class Exchange:
    def __init__(self, settings):
        self.s = settings
        self.log = logging.getLogger("exchange")
        self.dry_run = settings.dry_run
        api_key = settings.cdp_api_key_name
        api_secret = settings.cdp_api_key_secret
        if api_key and api_secret:
            self.client = RESTClient(api_key=api_key, api_secret=api_secret)
            self.private = True
        else:
            self.client = RESTClient()
            self.private = False
        if not self.private and not self.dry_run:
            raise RuntimeError(
                "No CDP API keys configured and dry_run is disabled. "
                "Set cdp_api_key_name and cdp_api_key_secret or enable dry_run."
            )
        self.sim_open = {}
        self._products = {}
        self._known = None

    def get_product(self, product_id):
        if product_id in self._products:
            return self._products[product_id]
        if self.private:
            data = self.client.get_product(product_id).to_dict()
        else:
            data = self.client.get_public_product(product_id).to_dict()
        self._products[product_id] = data
        return data

    def try_product(self, product_id):
        try:
            return self.get_product(product_id)
        except Exception:
            return None

    def known_products(self):
        if self._known is not None:
            return self._known
        ids = set()
        offset = 0
        try:
            while True:
                data = self.client.get_products(limit=250, offset=offset).to_dict()
                products = data.get("products", [])
                for product in products:
                    pid = product.get("product_id")
                    if pid:
                        ids.add(pid)
                if len(products) < 250:
                    break
                offset += 250
        except Exception as exc:
            self.log.warning("could not list products: %s", exc)
        self._known = ids
        return ids

    def get_price(self, product_id):
        return float(self.get_product(product_id).get("price") or 0)

    def get_candles(self, product_id, granularity, count):
        seconds = GRANULARITY_SECONDS.get(granularity, 300)
        end = int(time.time())
        count = min(300, max(80, count))
        start = end - count * seconds
        response = self.client.get_public_candles(product_id, str(start), str(end), granularity)
        candles = []
        for item in response.to_dict().get("candles", []):
            candles.append(
                {
                    "start": int(item["start"]),
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item["volume"]),
                }
            )
        candles.sort(key=lambda candle: candle["start"])
        return candles

    def get_accounts(self):
        if not self.private:
            return {}
        accounts = {}
        cursor = None
        while True:
            response = self.client.get_accounts(limit=250, cursor=cursor)
            data = response.to_dict()
            for account in data.get("accounts", []):
                currency = account.get("currency")
                if not currency:
                    continue
                available = float((account.get("available_balance") or {}).get("value") or 0)
                hold = float((account.get("hold") or {}).get("value") or 0)
                entry = accounts.setdefault(currency, {"available": 0.0, "hold": 0.0})
                entry["available"] += available
                entry["hold"] += hold
            if data.get("has_next"):
                cursor = data.get("cursor")
            else:
                break
        return accounts

    def get_fee_rates(self):
        if self.private and self.s.use_fees_from_api:
            try:
                data = self.client.get_transaction_summary().to_dict()
                tier = data.get("fee_tier") or {}
                maker = float(tier.get("maker_fee_rate") or 0)
                taker = float(tier.get("taker_fee_rate") or 0)
                if maker or taker:
                    return maker, taker
            except Exception as exc:
                self.log.warning("Could not fetch fee tier: %s", exc)
        return self.s.maker_fee_pct / 100.0, self.s.taker_fee_pct / 100.0

    def _increment(self, product_id, key, default):
        value = self.get_product(product_id).get(key)
        return Decimal(str(value)) if value else Decimal(default)

    def round_base(self, product_id, size):
        increment = self._increment(product_id, "base_increment", "0.00000001")
        value = Decimal(str(size))
        return float((value / increment).to_integral_value(rounding=ROUND_DOWN) * increment)

    def round_price(self, product_id, price):
        increment = self._increment(product_id, "price_increment", "0.01")
        value = Decimal(str(price))
        return float((value / increment).to_integral_value(rounding=ROUND_DOWN) * increment)

    def quote_min_size(self, product_id):
        return float(self.get_product(product_id).get("quote_min_size") or 1)

    def place_limit(self, product_id, side, base_size, limit_price, post_only=True):
        order_id = str(uuid.uuid4())
        base_size = self.round_base(product_id, base_size)
        limit_price = self.round_price(product_id, limit_price)
        if base_size <= 0:
            return {"success": False, "error": "zero size"}
        if Decimal(str(base_size * limit_price)) < Decimal(str(self.quote_min_size(product_id))):
            return {"success": False, "error": "below quote minimum"}
        if self.dry_run:
            self.sim_open.setdefault(product_id, {})[order_id] = {
                "side": side,
                "base_size": base_size,
                "limit_price": limit_price,
                "time": time.time(),
            }
            self.log.info("[DRY] %s %s %.10g @ %.8g", product_id, side, base_size, limit_price)
            return {"success": True, "order_id": order_id, "base_size": base_size, "limit_price": limit_price}
        try:
            if side == "BUY":
                response = self.client.limit_order_gtc_buy(
                    client_order_id=order_id,
                    product_id=product_id,
                    base_size=f"{base_size:f}",
                    limit_price=f"{limit_price:f}",
                    post_only=post_only,
                )
            else:
                response = self.client.limit_order_gtc_sell(
                    client_order_id=order_id,
                    product_id=product_id,
                    base_size=f"{base_size:f}",
                    limit_price=f"{limit_price:f}",
                    post_only=post_only,
                )
            data = response.to_dict()
            if data.get("success"):
                success = data.get("success_response") or {}
                return {
                    "success": True,
                    "order_id": success.get("order_id") or data.get("order_id") or order_id,
                    "base_size": base_size,
                    "limit_price": limit_price,
                }
            error = data.get("error_response") or data.get("failure_reason") or data
            return {"success": False, "error": error}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def cancel_orders(self, order_ids):
        order_ids = list(order_ids)
        if not order_ids:
            return {"success": True}
        if self.dry_run:
            for orders in self.sim_open.values():
                for order_id in order_ids:
                    orders.pop(order_id, None)
            return {"success": True}
        try:
            return self.client.cancel_orders(order_ids=order_ids).to_dict()
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def list_open_orders(self, product_id):
        if self.dry_run:
            return [
                {
                    "order_id": order_id,
                    "side": order["side"],
                    "base_size": order["base_size"],
                    "limit_price": order["limit_price"],
                }
                for order_id, order in self.sim_open.get(product_id, {}).items()
            ]
        orders = []
        try:
            response = self.client.list_orders(
                order_status=["OPEN"], product_ids=[product_id], limit=100
            )
            for order in response.to_dict().get("orders", []):
                configs = list((order.get("order_configuration") or {}).values())
                config = configs[0] if configs else {}
                orders.append(
                    {
                        "order_id": order.get("order_id"),
                        "side": order.get("side"),
                        "base_size": float(config.get("base_size") or 0),
                        "limit_price": float(config.get("limit_price") or 0),
                    }
                )
        except Exception as exc:
            self.log.warning("list_orders %s failed: %s", product_id, exc)
        return orders

    def detect_fills(self, product_id, price, maker_fee_pct):
        if self.dry_run:
            fills = []
            for order_id, order in list(self.sim_open.get(product_id, {}).items()):
                hit = (order["side"] == "BUY" and price <= order["limit_price"]) or (
                    order["side"] == "SELL" and price >= order["limit_price"]
                )
                if hit:
                    notional = order["base_size"] * order["limit_price"]
                    fills.append(
                        {
                            "order_id": order_id,
                            "side": order["side"],
                            "base_size": order["base_size"],
                            "price": order["limit_price"],
                            "commission": notional * (maker_fee_pct / 100.0),
                            "trade_id": order_id,
                        }
                    )
                    self.sim_open.get(product_id, {}).pop(order_id, None)
            return fills
        fills = []
        try:
            response = self.client.get_fills(product_ids=[product_id], limit=100)
            for fill in response.to_dict().get("fills", []):
                fills.append(
                    {
                        "order_id": fill.get("order_id"),
                        "side": fill.get("side"),
                        "base_size": float(fill.get("size") or 0),
                        "price": float(fill.get("price") or 0),
                        "commission": float(fill.get("commission") or 0),
                        "trade_id": fill.get("trade_id"),
                        "trade_time": fill.get("trade_time"),
                    }
                )
        except Exception as exc:
            self.log.warning("get_fills %s failed: %s", product_id, exc)
        return fills
