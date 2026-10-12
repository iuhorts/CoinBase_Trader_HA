from . import indicators


def compute_context(candles, settings):
    closes = [candle["close"] for candle in candles]
    price = closes[-1] if closes else None
    return {
        "price": price,
        "ema_fast": indicators.ema(closes, settings.ema_fast),
        "ema_slow": indicators.ema(closes, settings.ema_slow),
        "rsi": indicators.rsi(closes, settings.rsi_period),
    }


def grid_feasible(spacing_pct, maker_fee_rate, min_profit_margin_pct):
    round_trip_pct = 2.0 * maker_fee_rate * 100.0
    required_pct = round_trip_pct + min_profit_margin_pct
    return spacing_pct > required_pct, round_trip_pct, required_pct


def build_levels(anchor, levels, spacing_pct, rounder):
    step = spacing_pct / 100.0
    half = (levels - 1) / 2.0
    result = []
    for index in range(levels):
        offset = (index - half) * step
        result.append(rounder(anchor * (1.0 + offset)))
    return sorted(set(result))


def nearest_level(levels, price):
    if not levels:
        return 0
    return min(range(len(levels)), key=lambda index: abs(levels[index] - price))


def allow_buy(context, settings):
    if not settings.use_signals:
        return True
    rsi_value = context.get("rsi")
    if rsi_value is not None and rsi_value >= settings.rsi_overbought:
        return False
    fast = context.get("ema_fast")
    slow = context.get("ema_slow")
    if fast is not None and slow is not None and slow > 0 and fast < slow:
        downtrend_pct = (slow - fast) / slow * 100.0
        if downtrend_pct > settings.max_drawdown_filter_pct:
            return False
    return True
