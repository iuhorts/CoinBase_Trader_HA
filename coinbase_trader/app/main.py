import json
import logging
import os
import signal
import sys
import tempfile
import time
from collections import defaultdict

from . import discovery, strategy
from .exchange import Exchange
from .grid import GridBot
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
    logging.getLogger("coinbase").setLevel(logging.CRITICAL)
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
        self.bots = []
        self.selected = []
        self.state = self._load_state()

    def _state_path(self):
        path = self.s.state_file
        directory = os.path.dirname(path) or "."
        if not os.path.isdir(directory):
            path = os.path.join(tempfile.gettempdir(), "coinbase_trader_state.json")
        return path

    def _load_state(self):
        state = {"products": {}, "paused": bool(self.s.paused)}
        path = self._state_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    state.update(loaded)
                    state.setdefault("products", {})
            except (OSError, ValueError) as exc:
                self.log.warning("could not load state: %s", exc)
        return state

    def save_state(self):
        self.state["products"] = {bot.product_id: bot.dump() for bot in self.bots}
        try:
            with open(self._state_path(), "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, indent=2)
        except OSError as exc:
            self.log.warning("could not save state: %s", exc)

    def balances(self):
        if self.exchange.private:
            return self.exchange.get_accounts()
        result = {}
        for _product_id, base, quote, _info in self.selected:
            result.setdefault(base, {"available": self.s.dry_run_base_balance, "hold": 0.0})
            result.setdefault(quote, {"available": self.s.dry_run_quote_balance, "hold": 0.0})
        return result

    def bootstrap(self):
        self.maker_fee, self.taker_fee = self.exchange.get_fee_rates()
        balances = self.balances()
        self.selected = discovery.discover_products(self.exchange, self.s, balances)
        if not self.selected:
            raise RuntimeError(
                "No tradable products found. Check balances, min_product_value or the products option."
            )
        feasible, round_trip, required = strategy.grid_feasible(
            self.s.grid_spacing_pct, self.maker_fee, self.s.min_profit_margin_pct
        )
        lines = [
            f"Coinbase Trader start ({'DRY-RUN' if self.s.dry_run else 'LIVE'})",
            f"fees maker={self.maker_fee*100:.3f}% taker={self.taker_fee*100:.3f}% "
            f"round-trip maker={round_trip:.2f}% required spacing>{required:.2f}%",
        ]
        for product_id, base, quote, info in self.selected:
            bot = GridBot(self.exchange, self.s, product_id, base, quote, info)
            bot.maker_fee = self.maker_fee
            bot.taker_fee = self.taker_fee
            bot.load(self.state["products"].get(product_id, {}))
            base_available = balances.get(base, {}).get("available", 0.0)
            bot.bootstrap(self.exchange.get_price(product_id), base_available)
            bot.paused = bool(self.state.get("paused", self.s.paused)) or bot.paused
            self.bots.append(bot)
            value = bot.total_base() * (bot.anchor or 0)
            lines.append(f"{product_id}: anchor={bot.anchor:.6g} base={bot.total_base():.8g} value~{value:.2f} {quote}")
        summary = "\n".join(lines)
        self.log.info(summary.replace("\n", " | "))
        self.notifier.send(summary)
        if not feasible:
            warning = (
                f"WARNING: grid spacing {self.s.grid_spacing_pct:.2f}% is not profitable after fees "
                f"(need > {required:.2f}%). Unprofitable rungs will be blocked."
            )
            self.log.warning(warning)
            self.notifier.send(warning)
        self.update_sensors(balances)

    def quote_budgets(self, balances):
        groups = defaultdict(list)
        for bot in self.bots:
            groups[bot.quote].append(bot)
        budgets = {}
        for quote, group in groups.items():
            total = balances.get(quote, {}).get("available", 0.0)
            share = total / len(group) if group else 0.0
            for bot in group:
                budgets[bot.product_id] = share
        return budgets

    def tick(self):
        balances = self.balances()
        self.maker_fee, self.taker_fee = self.exchange.get_fee_rates()
        budgets = self.quote_budgets(balances)
        for bot in self.bots:
            bot.maker_fee = self.maker_fee
            bot.taker_fee = self.taker_fee
            try:
                bot.tick(balances, budgets.get(bot.product_id, 0.0))
            except Exception as exc:
                self.log.exception("%s tick failed", bot.product_id)
                self.notifier.notify_throttled(f"err-{bot.product_id}", f"[{bot.product_id}] error: {exc}")
            for message in bot.messages:
                self.notifier.send(f"[{bot.product_id}] {message}")
            bot.messages = []
        self.update_sensors(balances)

    def update_sensors(self, balances):
        total_pnl = 0.0
        statuses = []
        for bot in self.bots:
            total_pnl += bot.realized_pnl
            statuses.append(bot.status)
            self.ha.set_state(f"{bot.slug}_price", round(bot.price, 8), {"unit_of_measurement": bot.quote, "friendly_name": f"{bot.base} price", "icon": "mdi:cash"})
            self.ha.set_state(f"{bot.slug}_realized_pnl", round(bot.realized_pnl, 4), {"unit_of_measurement": bot.quote, "friendly_name": f"{bot.product_id} realized P/L", "icon": "mdi:chart-line"})
            self.ha.set_state(f"{bot.slug}_cycles", bot.cycles, {"friendly_name": f"{bot.product_id} cycles", "icon": "mdi:sync"})
            self.ha.set_state(f"{bot.slug}_base", round(bot.total_base(), 8), {"unit_of_measurement": bot.base, "friendly_name": f"{bot.product_id} base holdings", "icon": "mdi:wallet"})
            self.ha.set_state(f"{bot.slug}_value", round(bot.market_value(), 4), {"unit_of_measurement": bot.quote, "friendly_name": f"{bot.product_id} value", "icon": "mdi:cash-multiple"})
            self.ha.set_state(f"{bot.slug}_open_orders", len(bot.open_orders), {"friendly_name": f"{bot.product_id} open orders", "icon": "mdi:format-list-numbered", "orders": list(bot.open_orders.values())})
            self.ha.set_state(f"{bot.slug}_rsi", round(bot.context["rsi"], 2) if bot.context.get("rsi") is not None else "unknown", {"friendly_name": f"{bot.product_id} RSI", "icon": "mdi:speedometer"})
            self.ha.set_state(f"{bot.slug}_last_action", bot.last_action, {"friendly_name": f"{bot.product_id} last action", "icon": "mdi:history", "anchor": bot.anchor, "levels": bot.levels, "lots": bot.lots})
        overall = "paused" if all(status == "paused" for status in statuses) and statuses else "running"
        self.ha.set_state("status", overall, {"friendly_name": "Coinbase Trader status", "icon": "mdi:robot"})
        self.ha.set_state("active_products", len(self.bots), {"friendly_name": "Coinbase Trader products", "icon": "mdi:format-list-bulleted"})
        self.ha.set_state("total_realized_pnl", round(total_pnl, 4), {"friendly_name": "Coinbase Trader total realized P/L", "icon": "mdi:chart-line"})
        self.ha.set_state("fee_maker", round(self.maker_fee * 100, 3), {"unit_of_measurement": "%", "friendly_name": "Coinbase maker fee", "icon": "mdi:percent"})
        self.ha.set_state("fee_taker", round(self.taker_fee * 100, 3), {"unit_of_measurement": "%", "friendly_name": "Coinbase taker fee", "icon": "mdi:percent"})

    def handle_commands(self, commands):
        for command in commands:
            name = command.split()[0].lower().lstrip("/")
            if name == "status":
                lines = [f"dry_run={self.s.dry_run} products={len(self.bots)} pnl={sum(b.realized_pnl for b in self.bots):+.4f}"]
                for bot in self.bots:
                    lines.append(f"{bot.product_id} price={bot.price:.6g} open={len(bot.open_orders)} cycles={bot.cycles} pnl={bot.realized_pnl:+.4f}")
                self.notifier.send("\n".join(lines))
            elif name == "grid":
                self.notifier.send("\n\n".join(bot.grid_summary() for bot in self.bots))
            elif name == "pause":
                for bot in self.bots:
                    bot.paused = True
                self.state["paused"] = True
                self.notifier.send("paused: no new orders")
            elif name == "resume":
                for bot in self.bots:
                    bot.paused = False
                self.state["paused"] = False
                self.notifier.send("resumed")
            elif name == "stop":
                self.cancel_all()
                self.state["paused"] = True
                self.running = False
                self.notifier.send("stopping: open orders cancelled")
            elif name in ("help", "start"):
                self.notifier.send("commands: /status /grid /pause /resume /stop")

    def cancel_all(self):
        for bot in self.bots:
            order_ids = list(bot.open_orders.keys())
            if order_ids:
                self.exchange.cancel_orders(order_ids)
                bot.open_orders = {}

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
                self.notifier.notify_throttled("error", f"runtime error: {exc}")
            for command in self.notifier.poll_commands():
                self.handle_commands([command])
            self.save_state()
            for _ in range(max(1, self.s.poll_interval_seconds)):
                if not self.running:
                    break
                time.sleep(1)
        self.save_state()


def main():
    settings = Settings()
    setup_logging(settings.log_level_name)
    logging.getLogger("trader").info("starting Coinbase Trader (dry_run=%s)", settings.dry_run)
    trader = Trader(settings)
    signal.signal(signal.SIGTERM, trader.shutdown)
    signal.signal(signal.SIGINT, trader.shutdown)
    trader.run()


if __name__ == "__main__":
    main()
