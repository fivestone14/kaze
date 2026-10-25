# B · Dealers: forced hedging. Late in the day expiring options drag SPX towards where the same-day book's gamma sits
# (the pin to the centre) and dealers must trade the delta the decaying book hands them (the dealer drift); through the
# day an untested gamma wall pulls price towards it. The option book comes from the day's own reads (the first read
# and the read fifty minutes before the close keep it); the implied volatility from the scan.
import math

from engine.push_pull.read_facts import Reading
from engine.store import components, registry

YEAR_MINUTES = 390.0 * 252.0
# Strikes within this share of spot make the pin's centre; within this share the drift's hedge is counted.
PIN_SHARE, DRIFT_SHARE = 0.01, 0.04
# The pin needs a book: at least this many strikes near spot holding at least this many contracts in all.
PIN_MIN_STRIKES, PIN_MIN_CONTRACTS = 5, 500
# A wall counts as tested once the day's range came within this many swings of it; an untested wall speaks only
# within this many swings of price.
WALL_TOUCH_SWINGS, WALL_REACH_SWINGS = 0.15, 2.0


def normal_cdf(x):
    """The standard normal curve's area below x."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def delta(spot, strike, sigma, tau, call):
    """Black–Scholes delta (no rates), tau in years; at or past expiry the in-the-money step."""
    if tau <= 0:
        if strike == spot:
            return 0.5 if call else -0.5
        in_money = spot > strike if call else spot < strike
        return (1.0 if in_money else 0.0) * (1 if call else -1)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tau) / (sigma * math.sqrt(tau))
    return normal_cdf(d1) if call else normal_cdf(d1) - 1.0


def gamma(spot, strike, sigma, tau):
    """Black–Scholes gamma (no rates), tau in years, never shorter than one minute."""
    tau = max(tau, 1.0 / YEAR_MINUTES)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * tau) / (sigma * math.sqrt(tau))
    return math.exp(-0.5 * d1 * d1) / (spot * sigma * math.sqrt(2 * math.pi * tau))


def book_near(book, spot, share):
    """{strike: (calls, puts)} of the strikes within a share of spot."""
    near = {}
    for (side, strike), contracts in book.items():
        if abs(strike / spot - 1) <= share:
            calls, puts = near.get(strike, (0.0, 0.0))
            near[strike] = (calls + contracts, puts) if side == "call" else (calls, puts + contracts)
    return near


def implied_vol(read):
    """The same-day at-the-money implied volatility as a fraction, or None."""
    iv = read.value("same_day_atm_iv_calendar_year_pct")
    return iv / 100.0 if iv else None


def book_blank(read):
    """Why the pin or drift has no book: no open interest kept yet, or no scan."""
    return Reading.blank_for("not_known_at_read" if read.price is not None else "feed_missing")


def pin_to_centre(read):
    """The gamma-weighted centre of the same-day strikes within 1% of spot, less price, in swings: where the expiring
    book pulls price. Blank without a book, a thin book, an implied vol or a swing."""
    book, spot, swing, iv = read.option_book, read.price, read.swing(), implied_vol(read)
    if not book or spot is None:
        return book_blank(read)
    if swing is None or iv is None or read.minutes_left is None:
        return Reading.blank_for(registry.NOT_FORMED_REASON if swing is None else "feed_missing")
    near = book_near(book, spot, PIN_SHARE)
    total = sum(calls + puts for calls, puts in near.values())
    if len(near) < PIN_MIN_STRIKES or total < PIN_MIN_CONTRACTS:
        return Reading.blank_for("no_activity")
    tau = read.minutes_left / YEAR_MINUTES
    weights = {strike: (calls + puts) * gamma(spot, strike, iv, tau) for strike, (calls, puts) in near.items()}
    weight = sum(weights.values())
    if weight <= 0:
        return Reading.blank_for("no_activity")
    centre = sum(strike * w for strike, w in weights.items()) / weight
    return Reading((centre - spot) / swing)


def dealer_drift(read):
    """Billions of dollars dealers must trade if price stays put while the same-day book (customers long the calls,
    short the puts) decays to expiry: positive means dealers buy."""
    book, spot, iv = read.option_book, read.price, implied_vol(read)
    if not book or spot is None:
        return book_blank(read)
    if iv is None or read.minutes_left is None:
        return Reading.blank_for("feed_missing")
    now, then = max(read.minutes_left, 1.0) / YEAR_MINUTES, 0.0
    hedge = 0.0
    for strike, (calls, puts) in book_near(book, spot, DRIFT_SHARE).items():
        delta_now = calls * delta(spot, strike, iv, now, True) - puts * delta(spot, strike, iv, now, False)
        delta_then = calls * delta(spot, strike, iv, then, True) - puts * delta(spot, strike, iv, then, False)
        hedge += delta_then - delta_now
    return Reading(-hedge * 100.0 * spot / 1e9)


def option_wall_untested(read):
    """The nearest untested gamma wall within two swings: its gamma share ÷ (1 + distance in swings), signed towards
    it; 0 when every wall is tested (the day's range came within 0.15 swing of it) or out of reach."""
    swing, price, high, low = read.swing(), read.price, read.day_high, read.day_low
    if price is None or swing is None or high is None or low is None:
        return Reading.blank_for(registry.NOT_FORMED_REASON)
    candidates = []
    for level in components.WALL_TYPES:
        strike = read.value("level_price_points", level_type=level)
        share = read.value("level_gamma_share_portion", level_type=level)
        if strike is None:
            return Reading.blank_for(read.blank_reason("level_price_points", level_type=level))
        tolerance = WALL_TOUCH_SWINGS * swing
        touched = low - tolerance <= strike <= high + tolerance
        distance = (strike - price) / swing
        if not touched and abs(distance) <= WALL_REACH_SWINGS and share is not None:
            candidates.append((abs(distance), distance, share))
    if not candidates:
        return Reading(0.0)
    _, distance, share = min(candidates)
    return Reading(math.copysign(share / (1 + abs(distance)), distance))
