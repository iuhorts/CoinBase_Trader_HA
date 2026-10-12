import json
import os

DEFAULTS = {
    "dry_run": False,
    "log_level": "info",
    "product_id": "ETH-EUR",
    "grid_levels": 8,
    "grid_spacing_pct": 2.0,
    "grid_anchor_price": 0.0,
    "min_profit_margin_pct": 0.4,
    "maker_fee_pct": 0.60,
    "taker_fee_pct": 1.20,
    "use_fees_from_api": True,
    "use_signals": True,
    "granularity": "FIVE_MINUTE",
    "ema_fast": 21,
    "ema_slow": 55,
    "rsi_period": 14,
    "rsi_overbought": 70.0,
    "rsi_oversold": 30.0,
    "max_drawdown_filter_pct": 6.0,
    "poll_interval_seconds": 60,
    "reserve_quote_pct": 0.0,
    "allow_sells": True,
    "max_open_orders": 12,
    "ha_sensors": True,
    "state_file": "/data/state.json",
    "paused": False,
    "telegram_enabled": False,
    "telegram_commands": True,
    "telegram_chat_id": "",
    "telegram_bot_token": "",
    "cdp_api_key_name": "",
    "cdp_api_key_secret": "",
    "dry_run_quote_balance": 100.0,
    "dry_run_base_balance": 0.0,
}

BOOL_KEYS = {
    "dry_run",
    "use_fees_from_api",
    "use_signals",
    "allow_sells",
    "ha_sensors",
    "paused",
    "telegram_enabled",
    "telegram_commands",
}
INT_KEYS = {
    "grid_levels",
    "ema_fast",
    "ema_slow",
    "rsi_period",
    "poll_interval_seconds",
    "max_open_orders",
}
FLOAT_KEYS = {
    "grid_spacing_pct",
    "grid_anchor_price",
    "min_profit_margin_pct",
    "maker_fee_pct",
    "taker_fee_pct",
    "rsi_overbought",
    "rsi_oversold",
    "max_drawdown_filter_pct",
    "reserve_quote_pct",
    "dry_run_quote_balance",
    "dry_run_base_balance",
}


def _coerce(key, value):
    if key in BOOL_KEYS:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if key in INT_KEYS:
        return int(float(value))
    if key in FLOAT_KEYS:
        return float(value)
    return value


class Settings:
    def __init__(self, options_file=None):
        data = dict(DEFAULTS)
        path = options_file or os.environ.get("OPTIONS_FILE", "/data/options.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    data.update(loaded)
            except (OSError, ValueError):
                pass
        for key in list(data.keys()):
            env = os.environ.get(key.upper())
            if env is not None and env != "":
                data[key] = env
        for key, value in list(data.items()):
            data[key] = _coerce(key, value)
        if "-" in data["product_id"]:
            base, quote = data["product_id"].split("-", 1)
            data["base_currency"] = base.upper()
            data["quote_currency"] = quote.upper()
        self.__dict__.update(data)

    @property
    def log_level_name(self):
        return str(self.log_level).upper()
