import json
import logging
import os
import signal
import sys
import tempfile
import time

from . import strategy
from .exchange import Exchange
from .ha import HomeAssistant
from .notifier import Notifier
from .settings import Settings


def setup_logging(level_name):
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("coinbase").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class Trader:
    def __init__(self, settings):
        self.s = settings
        self.log = logging.getLogger("trader")
        self.exchange = Exchange(settings)
        self.ha = HomeAssistant(settings)
        self.notifier = Notifier(settings)
        self.running = True
        self.maker_fee, self.taker_fee = 0.0, 0.0
        self.anchor = 0.0
        self.levels = []
        self.state = self._load_state()

    def _state_path(self):
        path = self.s.state_file
        directory = os.path.dirname(path) or "."
        if not os.path.isdir(directory):
            path = os.path.join(tempfile.gettempdir(), "coinbase_trader_state.json")
        return path

    def _load_state(self):
        state = {
            "anchor": 0.0,
            "levels": [],
            "holdings": {},
            "open_orders": {},
            "processed_fills": [],
            "realized_pnl": 0.0,
            "cycles": 0,
            "paused": bool(self.s.paused),
            "status": "starting",
            "last_action": "",
        }
        path = self._state_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    state.update(loaded)
            except (OSError, ValueError) as exc:
                self.log.warning("could not load state: %s", exc)
        return state

    def save_state(self):
        try:
            with open(self._state_path(), "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2)
        except OSError as exc:
            self.log.warning("could not save state: %s", exc)

    def bootstrap(self):
        product = self.exchange.refresh_product()
        self.maker_fee, self.taker_fee = self.exchange.get_fee_rates()
        feasible, round_trip, required = strategy.grid_feasible(
            self.s.grid_spacing_pct, self.maker_fee, self.s.min_profit_margin_pct
        )
        price = self.exchange.get_price()
        if not self.state.get("anchor"):
            self.state["anchor"] = self.s.grid_anchor_price or price
        self.anchor = float(self.state["anchor"])
        self.levels = strategy.build_levels(
            self.anchor, self.s.grid_levels, self.s.grid_spacing_pct, self.exchange.round_price
        )
        self.state["levels"] = self.levels
        self.state["status"] = "running"
        summary = (
            f"Coinbase Trader start\n"
            f"product={self.s.product_id} mode={'DRY-RUN' if self.s.dry_run else 'LIVE'}\n"
            f"price={price} anchor={self.anchor}\n"
            f"levels={self.levels}\n"
            f"fees maker={self.maker_fee*100:.3f}% taker={self.taker_fee*100:.3f}% "
            f"(round-trip maker {round_trip:.2f}%, required spacing > {required:.2f}%)\n"
            f"signals={'on' if self.s.use_signals else 'off'}"
        )
        self.log.info(summary.replace("\n", " | "))
        self.notifier.send(summary)
        if not feasible:
            warning = (
                f"WARNING: grid spacing {self.s.grid_spacing_pct:.2f}% is not profitable "
                f"after fees (need > {required:.2f}%). Bot will not place unprofitable rungs."
            )
            self.log.warning(warning)
            self.notifier.send(warning)
        self.update_sensors(price, {})

    def balances(self):
        if self.exchange.dry_run:
            spent = 0.0
            held = float(self.s.dry_run_base_balance)
            for index, size in self.state["holdings"].items():
                spent += self.levels[int(index)] * size
                held += size
            return {
                self.s.quote_currency: {"available": max(0.0, self.s.dry_run_quote_balance - spent), "hold": 0.0},
                self.s.base_currency: {"available": held, "hold": 0.0},
            }
        return self.exchange.get_balances()

    def quote_per_level(self, price, balances):
        open_buy_notional = sum(
            order["base_size"] * order["limit_price"]
            for order in self.state["open_orders"].values()
            if order["side"] == "BUY"
        )
        total = balances.get(self.s.quote_currency, {}).get("available", 0.0)
        usable = total * (1.0 - self.s.reserve_quote_pct / 100.0) - open_buy_notional
        buy_slots = [
            index
            for index, level in enumerate(self.levels)
            if level < price and str(index) not in self.state["holdings"]
        ]
        if not buy_slots or usable <= 0:
            return 0.0
        return usable / len(buy_slots)

    def process_fills(self, price):
        fills = self.exchange.detect_fills(price)
        processed = set(self.state["processed_fills"])
        for fill in fills:
            key = str(fill.get("trade_id") or fill.get("order_id"))
            if key in processed:
                continue
            processed.add(key)
            self.apply_fill(fill)
        self.state["processed_fills"] = list(processed)[-500:]

    def apply_fill(self, fill):
        side = fill["side"]
        size = float(fill["base_size"])
        price = float(fill["price"])
        commission = float(fill.get("commission") or 0)
        index = strategy.nearest_level(self.levels, price)
        self.state["open_orders"].pop(str(fill.get("order_id")), None)
        if side == "BUY":
            self.state["holdings"][str(index)] = self.state["holdings"].get(str(index), 0.0) + size
            action = f"BUY filled {size:.8f} @ {price:.2f} (level {index})"
        else:
            buy_index = max(0, index - 1)
            held = self.state["holdings"].pop(str(buy_index), size)
            buy_price = self.levels[buy_index]
            buy_fee = buy_price * held * self.maker_fee
            sell_fee = commission or price * held * self.maker_fee
            profit = (price - buy_price) * held - buy_fee - sell_fee
            self.state["realized_pnl"] = self.state.get("realized_pnl", 0.0) + profit
            self.state["cycles"] = self.state.get("cycles", 0) + 1
            action = (
                f"SELL filled {held:.8f} @ {price:.2f} (opened @ {buy_price:.2f}) "
                f"profit {profit:.4f} {self.s.quote_currency}"
            )
        self.state["last_action"] = action
        self.log.info(action)
        self.notifier.send(action)

    def reconcile_open_orders(self):
        orders = self.exchange.list_open_orders()
        rebuilt = {}
        for order in orders:
            order_id = order["order_id"]
            if order_id in self.state["open_orders"]:
                rebuilt[order_id] = self.state["open_orders"][order_id]
                continue
            side = order["side"]
            index = strategy.nearest_level(self.levels, float(order["limit_price"]))
            level = index if side == "BUY" else max(0, index - 1)
            rebuilt[order_id] = {
                "side": side,
                "level": level,
                "base_size": float(order["base_size"]),
                "limit_price": float(order["limit_price"]),
            }
        self.state["open_orders"] = rebuilt

    def place(self, side, buy_level, base_size):
        if side == "BUY":
            limit_price = self.levels[buy_level]
        else:
            if buy_level + 1 >= len(self.levels):
                return False
            limit_price = self.levels[buy_level + 1]
        result = self.exchange.place_limit(side, base_size, limit_price, post_only=True)
        if not result.get("success"):
            self.log.info("order rejected (%s %s @ %.2f): %s", side, base_size, limit_price, result.get("error"))
            return False
        self.state["open_orders"][result["order_id"]] = {
            "side": side,
            "level": buy_level,
            "base_size": result.get("base_size", base_size),
            "limit_price": limit_price,
        }
        action = f"{side} {result.get('base_size', base_size):.8f} @ {limit_price:.2f} (level {buy_level})"
        self.state["last_action"] = action
        self.log.info("placed %s", action)
        return True

    def update_grid(self, price, context):
        if self.state.get("paused"):
            return
        balances = self.balances()
        existing = {(order["side"], order["level"]) for order in self.state["open_orders"].values()}
        allow_buys = strategy.allow_buy(context, self.s)
        per_level = self.quote_per_level(price, balances)
        for index, level in enumerate(self.levels):
            if len(self.state["open_orders"]) >= self.s.max_open_orders:
                break
            held_size = self.state["holdings"].get(str(index))
            if held_size:
                if self.s.allow_sells and ("SELL", index) not in existing:
                    if self.place("SELL", index, held_size):
                        existing.add(("SELL", index))
                continue
            if level < price and ("BUY", index) not in existing and allow_buys and per_level > 0:
                base_size = self.exchange.round_base(per_level / level)
                if base_size > 0 and base_size * level >= float(self.exchange.quote_min_size):
                    if self.place("BUY", index, base_size):
                        existing.add(("BUY", index))

    def update_sensors(self, price, context):
        balances = self.balances()
        quote = balances.get(self.s.quote_currency, {})
        base = balances.get(self.s.base_currency, {})
        self.ha.set_state("status", self.state.get("status", "unknown"), {"friendly_name": "Coinbase Trader status", "icon": "mdi:robot"})
        self.ha.set_state("product", self.s.product_id, {"friendly_name": "Coinbase Trader product", "icon": "mdi:currency-usd"})
        self.ha.set_state("price", round(price, 2), {"unit_of_measurement": self.s.quote_currency, "friendly_name": f"{self.s.base_currency} price", "icon": "mdi:cash"})
        self.ha.set_state("balance_quote", round(quote.get("available", 0.0), 2), {"unit_of_measurement": self.s.quote_currency, "friendly_name": f"Coinbase {self.s.quote_currency} available", "icon": "mdi:wallet"})
        self.ha.set_state("balance_base", round(base.get("available", 0.0), 8), {"unit_of_measurement": self.s.base_currency, "friendly_name": f"Coinbase {self.s.base_currency} available", "icon": "mdi:ethereum"})
        self.ha.set_state("open_orders", len(self.state["open_orders"]), {"friendly_name": "Coinbase open orders", "icon": "mdi:format-list-numbered", "orders": list(self.state["open_orders"].values())})
        self.ha.set_state("holds", len(self.state["holdings"]), {"friendly_name": "Coinbase grid holdings", "icon": "mdi:grid", "holdings": self.state["holdings"], "levels": self.levels, "anchor": self.anchor})
        self.ha.set_state("realized_pnl", round(self.state.get("realized_pnl", 0.0), 4), {"unit_of_measurement": self.s.quote_currency, "friendly_name": "Coinbase realized P/L", "icon": "mdi:chart-line"})
        self.ha.set_state("cycles", self.state.get("cycles", 0), {"friendly_name": "Coinbase completed cycles", "icon": "mdi:sync"})
        self.ha.set_state("fee_maker", round(self.maker_fee * 100, 3), {"unit_of_measurement": "%", "friendly_name": "Coinbase maker fee", "icon": "mdi:percent"})
        self.ha.set_state("fee_taker", round(self.taker_fee * 100, 3), {"unit_of_measurement": "%", "friendly_name": "Coinbase taker fee", "icon": "mdi:percent"})
        self.ha.set_state("last_action", self.state.get("last_action", ""), {"friendly_name": "Coinbase last action", "icon": "mdi:history"})
        self.ha.set_state("rsi", round(context["rsi"], 2) if context.get("rsi") is not None else "unknown", {"friendly_name": "Coinbase RSI", "icon": "mdi:speedometer"})
        self.ha.set_state("ema_fast", round(context["ema_fast"], 2) if context.get("ema_fast") is not None else "unknown", {"friendly_name": "Coinbase EMA fast", "icon": "mdi:chart-bell-curve"})
        self.ha.set_state("ema_slow", round(context["ema_slow"], 2) if context.get("ema_slow") is not None else "unknown", {"friendly_name": "Coinbase EMA slow", "icon": "mdi:chart-bell-curve-cumulative"})

    def tick(self):
        price = self.exchange.get_price()
        candles = self.exchange.get_candles()
        context = strategy.compute_context(candles, self.s)
        self.maker_fee, self.taker_fee = self.exchange.get_fee_rates()
        self.process_fills(price)
        self.reconcile_open_orders()
        self.update_grid(price, context)
        self.state["status"] = "paused" if self.state.get("paused") else "running"
        self.update_sensors(price, context)
        self.log.info(
            "price=%.2f rsi=%s ema_f=%s ema_s=%s open=%d holds=%d pnl=%.4f",
            price,
            f"{context['rsi']:.1f}" if context.get("rsi") is not None else "na",
            f"{context['ema_fast']:.1f}" if context.get("ema_fast") is not None else "na",
            f"{context['ema_slow']:.1f}" if context.get("ema_slow") is not None else "na",
            len(self.state["open_orders"]),
            len(self.state["holdings"]),
            self.state.get("realized_pnl", 0.0),
        )

    def handle_commands(self, commands):
        for command in commands:
            parts = command.split()
            name = parts[0].lower().lstrip("/")
            if name == "status":
                balances = self.balances()
                self.notifier.send(
                    f"status={self.state.get('status')} paused={self.state.get('paused')} "
                    f"price={self.exchange.get_price():.2f} open={len(self.state['open_orders'])} "
                    f"holds={len(self.state['holdings'])} pnl={self.state.get('realized_pnl', 0.0):.4f} "
                    f"{self.s.quote_currency}={balances.get(self.s.quote_currency, {}).get('available', 0.0):.2f}"
                )
            elif name == "grid":
                holds = ", ".join(f"{i}:{size:.6f}" for i, size in self.state["holdings"].items()) or "none"
                self.notifier.send(f"anchor={self.anchor:.2f}\nlevels={self.levels}\nholds={holds}")
            elif name == "pause":
                self.state["paused"] = True
                self.notifier.send("paused: no new orders will be placed")
            elif name == "resume":
                self.state["paused"] = False
                self.notifier.send("resumed")
            elif name == "stop":
                self.state["paused"] = True
                self.cancel_all()
                self.running = False
                self.notifier.send("stopping: open orders cancelled")
            elif name in ("help", "start"):
                self.notifier.send("commands: /status /grid /pause /resume /stop")

    def cancel_all(self):
        order_ids = list(self.state["open_orders"].keys())
        if order_ids:
            self.exchange.cancel_orders(order_ids)
            self.state["open_orders"] = {}

    def poll_telegram(self):
        commands = self.notifier.poll_commands()
        if commands:
            self.handle_commands(commands)

    def shutdown(self, *_args):
        self.log.info("shutdown requested")
        self.running = False

    def run(self):
        self.bootstrap()
        while self.running:
            try:
                self.tick()
            except Exception as exc:
                self.log.exception("tick failed")
                self.state["status"] = "error"
                self.notifier.notify_throttled("error", f"runtime error: {exc}")
            self.poll_telegram()
            self.save_state()
            for _ in range(max(1, self.s.poll_interval_seconds)):
                if not self.running:
                    break
                time.sleep(1)
        self.save_state()


def main():
    settings = Settings()
    setup_logging(settings.log_level_name)
    logging.getLogger("trader").info(
        "starting Coinbase Trader (dry_run=%s product=%s)", settings.dry_run, settings.product_id
    )
    trader = Trader(settings)
    signal.signal(signal.SIGTERM, trader.shutdown)
    signal.signal(signal.SIGINT, trader.shutdown)
    trader.run()


if __name__ == "__main__":
    main()
