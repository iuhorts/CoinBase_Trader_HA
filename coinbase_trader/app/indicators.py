def ema(values, period):
    if period <= 0 or len(values) < period:
        return None
    factor = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    for value in values[period:]:
        current = value * factor + current * (1 - factor)
    return current


def sma(values, period):
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / len(window)


def rsi(values, period=14):
    if period <= 0 or len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
