import logging
import time
from datetime import datetime

from . import strategy


class GridBot:
    def __init__(self, exchange, settings, product_id, base, quote, info):
        self.exchange = exchange
        self.s = settings
        self.product_id = product_id
        self.base = base
        self.quote = quote
        self.info = info
        self.slug = product_id.lower().replace("-", "_")
        self.log = logging.getLogger("grid." + product_id)
        self.maker_fee = settings.maker_fee_pct / 100.0
        self.taker_fee = settings.taker_fee_pct / 100.0
        self.anchor = 0.0
        self.levels = []
        self.lots = []
        self.open_orders = {}
        self.processed_fills = []
        self.fills_since = 0.0
        self.recent = []
        self.realized_pnl = 0.0
        self.cycles = 0
        self.last_action = ""
        self.paused = bool(settings.paused)
        self.messages = []
        self.context = {}
        self.price = 0.0
        self.base_available = 0.0
        self.quote_available = 0.0
        self.quote_budget = 0.0
        self.status = "starting"

    def load(self, state):
        self.anchor = float(state.get("anchor") or 0)
        self.levels = state.get("levels") or []
        self.lots = [list(item) for item in state.get("lots", [])]
        self.processed_fills = state.get("processed_fills", [])
        self.fills_since = float(state.get("fills_since") or 0)
        self.recent = state.get("recent", [])
        self.realized_pnl = float(state.get("realized_pnl") or 0)
        self.cycles = int(state.get("cycles") or 0)
        self.last_action = state.get("last_action") or ""
        self.paused = bool(state.get("paused", self.s.paused))

    def dump(self):
        return {
            "anchor": self.anchor,
            "levels": self.levels,
            "lots": self.lots,
            "processed_fills": self.processed_fills,
            "fills_since": self.fills_since,
            "recent": self.recent,
            "realized_pnl": self.realized_pnl,
            "cycles": self.cycles,
            "last_action": self.last_action,
            "paused": self.paused,
        }

    def bootstrap(self, price, base_available):
        if self.fills_since <= 0:
            self.fills_since = time.time()
        if self.anchor <= 0:
            self.anchor = self.s.grid_anchor_price or price
        self.levels = strategy.build_levels(
            self.anchor,
            self.s.grid_levels,
            self.s.grid_spacing_pct,
            lambda value: self.exchange.round_price(self.product_id, value),
        )
        if not self.lots and base_available > 0:
            self.lots = [[base_available, self.anchor]]

    def consume_lots(self, quantity):
        total = self.total_base()
        average = self.average_cost()
        if average is None or total <= 0:
            return 0.0
        sold = min(quantity, total)
        ratio = sold / total
        for lot in self.lots:
            lot[0] *= 1.0 - ratio
        self.lots = [lot for lot in self.lots if lot[0] > 1e-15]
        return average * sold

    def apply_fill(self, fill):
        side = fill["side"]
        size = float(fill["base_size"])
        price = float(fill["price"])
        commission = float(fill.get("commission") or 0)
        self.open_orders.pop(str(fill.get("order_id")), None)
        profit = None
        if side == "BUY":
            self.lots.append([size, price])
            action = f"{self.base} BUY {size:.8g} @ {price:.8g}"
        else:
            cost = self.consume_lots(size)
            buy_fee = cost * self.maker_fee
            sell_fee = commission or (size * price * self.maker_fee)
            profit = size * price - cost - buy_fee - sell_fee
            self.realized_pnl += profit
            self.cycles += 1
            avg_cost = (cost / size) if size else 0.0
            action = (
                f"{self.base} SELL {size:.8g} @ {price:.8g} (cost {avg_cost:.8g}) "
                f"profit {profit:+.4f} {self.quote}"
            )
        self.last_action = action
        self.recent.append(
            {
                "time": self._fill_time(fill) or time.time(),
                "side": side,
                "size": size,
                "price": price,
                "profit": round(profit, 6) if profit is not None else None,
                "product": self.product_id,
            }
        )
        self.recent = self.recent[-20:]
        self.log.info(action)
        self.messages.append(action)

    @staticmethod
    def _fill_time(fill):
        raw = fill.get("trade_time")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    def process_fills(self, price):
        fills = self.exchange.detect_fills(self.product_id, price, self.maker_fee * 100.0)
        fills.sort(key=lambda fill: self._fill_time(fill) or 0.0)
        processed = set(self.processed_fills)
        for fill in fills:
            key = str(fill.get("trade_id") or fill.get("order_id"))
            if key in processed:
                continue
            processed.add(key)
            when = self._fill_time(fill)
            if when is not None and when < self.fills_since:
                continue
            self.apply_fill(fill)
        self.processed_fills = list(processed)[-500:]

    def reconcile(self):
        orders = self.exchange.list_open_orders(self.product_id)
        rebuilt = {}
        for order in orders:
            order_id = order["order_id"]
            if order_id in self.open_orders:
                rebuilt[order_id] = self.open_orders[order_id]
                continue
            side = order["side"]
            level = strategy.nearest_level(self.levels, float(order["limit_price"]))
            rebuilt[order_id] = {
                "side": side,
                "level": level,
                "base_size": float(order["base_size"]),
                "limit_price": float(order["limit_price"]),
            }
        self.open_orders = rebuilt

    def place(self, side, level, base_size):
        limit_price = self.levels[level]
        result = self.exchange.place_limit(self.product_id, side, base_size, limit_price, post_only=True)
        if not result.get("success"):
            self.log.info(
                "order rejected (%s %s %.8g @ %.8g): %s",
                side, self.product_id, base_size, limit_price, result.get("error"),
            )
            return False
        self.open_orders[result["order_id"]] = {
            "side": side,
            "level": level,
            "base_size": result.get("base_size", base_size),
            "limit_price": limit_price,
        }
        return True

    def update_grid(self, price, base_available, quote_budget):
        if self.paused:
            return
        existing = {(order["side"], order["level"]) for order in self.open_orders.values()}
        minimum_notional = self.exchange.quote_min_size(self.product_id)
        usable_base = base_available * (1.0 - self.s.reserve_base_pct / 100.0)
        required_margin = 2.0 * self.maker_fee + self.s.min_profit_margin_pct / 100.0
        avg_cost = self.average_cost()
        min_sell_price = avg_cost * (1.0 + required_margin) if avg_cost else None
        if min_sell_price is not None:
            stale = [
                order_id
                for order_id, order in self.open_orders.items()
                if order["side"] == "SELL"
                and order["level"] < len(self.levels)
                and self.levels[order["level"]] < min_sell_price
            ]
            if stale:
                self.exchange.cancel_orders(stale)
                for order_id in stale:
                    order = self.open_orders.pop(order_id)
                    existing.discard(("SELL", order["level"]))
                self.log.info("cancelled %d sell order(s) below break-even", len(stale))
        sell_slots = []
        if self.s.allow_sells:
            sell_slots = [
                j
                for j, lvl in enumerate(self.levels)
                if lvl > price
                and ("SELL", j) not in existing
                and (min_sell_price is None or lvl >= min_sell_price)
            ]
        if sell_slots:
            per_sell = usable_base / len(sell_slots)
            for j in sell_slots:
                if len(self.open_orders) >= self.s.max_open_orders:
                    break
                size = self.exchange.round_base(self.product_id, per_sell)
                if size > 0 and size * self.levels[j] >= minimum_notional:
                    if self.place("SELL", j, size):
                        existing.add(("SELL", j))
        if not strategy.allow_buy(self.context, self.s):
            return
        usable_quote = quote_budget * (1.0 - self.s.reserve_quote_pct / 100.0)
        buy_slots = [j for j, lvl in enumerate(self.levels) if lvl < price and ("BUY", j) not in existing]
        if not buy_slots or usable_quote <= 0:
            return
        per_buy = usable_quote / len(buy_slots)
        for j in buy_slots:
            if len(self.open_orders) >= self.s.max_open_orders:
                break
            size = self.exchange.round_base(self.product_id, per_buy / self.levels[j])
            if size > 0 and size * self.levels[j] >= minimum_notional:
                if self.place("BUY", j, size):
                    existing.add(("BUY", j))

    def tick(self, balances, quote_budget):
        price = self.exchange.get_price(self.product_id)
        candles = self.exchange.get_candles(
            self.product_id, self.s.granularity, max(80, self.s.ema_slow * 3)
        )
        self.context = strategy.compute_context(candles, self.s)
        if price <= 0 and self.context.get("price"):
            price = self.context["price"]
        self.process_fills(price)
        self.reconcile()
        base_available = balances.get(self.base, {}).get("available", 0.0)
        self.price = price
        self.base_available = base_available
        self.quote_available = balances.get(self.quote, {}).get("available", 0.0)
        self.quote_budget = quote_budget
        self.update_grid(price, base_available, quote_budget)
        self.status = "paused" if self.paused else "running"

    def total_base(self):
        return sum(lot[0] for lot in self.lots)

    def average_cost(self):
        total_quantity = sum(lot[0] for lot in self.lots)
        if total_quantity <= 0:
            return None
        return sum(lot[0] * lot[1] for lot in self.lots) / total_quantity

    def market_value(self):
        return self.total_base() * self.price

    def grid_summary(self):
        holds = ", ".join(f"{lot[0]:.8g}@{lot[1]:.8g}" for lot in self.lots) or "none"
        return f"{self.product_id}: anchor={self.anchor:.8g}\nlevels={self.levels}\nlots={holds}"
