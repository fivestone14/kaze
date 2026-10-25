# The wider view: context inputs that judge how much each force counts, never adding points of their own. Each
# reading's booster is b = clip(exp(Σ β_j u_j), ×0.5, ×2) over the context inputs its spec names, with u_j the input
# mean-normalised so β = 0 means ×1; every β starts at 0 (×1) and is learned later from live days. One reading, the
# option wall, carries a fixed clock booster instead: dealers' gamma grows as expiry nears, √(195 ÷ minutes left).
# Trend-ness (tn, 0–1) is also the day-type input the Trend weight blends with: a + c·tn.
import math

from engine import session_clock
from engine.push_pull import same_clock_baseline as baseline
from engine.store import registry

CONTEXT_MEASURE = "push_pull_context_input"
# The 10-year yield's tide: its change over 20 sessions against 250 sessions of such changes (floor 5 bp).
TIDE_SESSIONS, TIDE_HISTORY_SESSIONS, TIDE_FLOOR_BASIS_POINTS = 20, 250, 5.0

BOOSTER_FLOOR, BOOSTER_CEILING = 0.5, 2.0
TREND_NESS, PATH_STRAIGHTNESS, DEALER_GAMMA, RATES_TIDE, VIX_LEVEL, TIME_OF_DAY, DOWN_DAY = (
    "trend_ness", "path_straightness", "dealer_gamma", "rates_tide", "vix_level", "time_of_day", "down_day")
# The three same-clock ranks trend-ness averages, each stored so past sessions can be ranked against.
STRAIGHTNESS_SINCE_OPEN, SIDE_SHARE, CLOSENESS_TO_EXTREME, TREND_RANK_MEAN = (
    "straightness_since_open", "side_share_since_open", "closeness_to_extreme", "trend_rank_mean")
TREND_NESS_PARTS = (STRAIGHTNESS_SINCE_OPEN, SIDE_SHARE, CLOSENESS_TO_EXTREME)
# The ramp: trend-ness is 0.5 (no opinion) until 10:00, then eases to the full rank by 12:30.
RAMP_FROM_MINUTE, RAMP_FULL_MINUTE = 30, 180
# The wall's fixed clock booster: √(195 ÷ minutes left), 195 being the half-session at which it reads ×1.
WALL_CLOCK_MINUTES = 195
VIX_USUAL = 18.0


def trend_ness_parts(closes, true_open, high, low):
    """The three trend-ness ingredients from the day's 5-minute closes since the true open: straightness (net move over
    path), share of time on price's current side of the open, and closeness to the day's high or low (0 mid-range,
    1 at an extreme); None for any that has not formed."""
    if true_open is None or len(closes) < 2:
        return {part: None for part in TREND_NESS_PARTS}
    path = [true_open, *closes]
    steps = [abs(b - a) for a, b in zip(path, path[1:])]
    total = sum(steps)
    price = closes[-1]
    side = (price > true_open) - (price < true_open)
    on_side = [((c > true_open) - (c < true_open)) == side for c in closes] if side else []
    extremes_high, extremes_low = max(high, true_open) if high is not None else None, \
        min(low, true_open) if low is not None else None
    closeness = abs(2 * (price - extremes_low) / (extremes_high - extremes_low) - 1) \
        if extremes_high is not None and extremes_high > extremes_low else None
    return {STRAIGHTNESS_SINCE_OPEN: abs(price - true_open) / total if total > 0 else None,
            SIDE_SHARE: sum(on_side) / len(on_side) if on_side else 0.5,
            CLOSENESS_TO_EXTREME: closeness}


def same_clock_rank(value, past_values):
    """The share of past same-clock values below today's (ties count half), 0–1; None without enough history."""
    if value is None or not past_values:
        return None
    below = sum(1 for past in past_values if past < value) + 0.5 * sum(1 for past in past_values if past == value)
    return below / len(past_values)


def ramp(minute_of_session):
    """0 until RAMP_FROM_MINUTE, rising to 1 at RAMP_FULL_MINUTE: how much of the day's character is readable yet."""
    return max(0.0, min(1.0, (minute_of_session - RAMP_FROM_MINUTE) / (RAMP_FULL_MINUTE - RAMP_FROM_MINUTE)))


def trend_ness(rank_of_mean, minute_of_session):
    """tn = 0.5 + ramp × (R − 0.5): the same-clock rank of today's mean rank, eased in through the morning."""
    if rank_of_mean is None:
        return 0.5
    return 0.5 + ramp(minute_of_session) * (rank_of_mean - 0.5)


def normalised_inputs(context):
    """Each context input as u_j, centred so that the usual case reads 0: tn − 0.5, straightness − 0.5, gamma ±1,
    the rates tide z, ln(VIX ÷ 18), time of day − 0.5 (share of the session gone), down day −1/+1."""
    return {
        TREND_NESS: context[TREND_NESS] - 0.5 if context.get(TREND_NESS) is not None else 0.0,
        PATH_STRAIGHTNESS: context[PATH_STRAIGHTNESS] - 0.5 if context.get(PATH_STRAIGHTNESS) is not None else 0.0,
        DEALER_GAMMA: context.get(DEALER_GAMMA) or 0.0,
        RATES_TIDE: context.get(RATES_TIDE) or 0.0,
        VIX_LEVEL: math.log(context[VIX_LEVEL] / VIX_USUAL) if context.get(VIX_LEVEL) else 0.0,
        TIME_OF_DAY: context[TIME_OF_DAY] - 0.5 if context.get(TIME_OF_DAY) is not None else 0.0,
        DOWN_DAY: context.get(DOWN_DAY) or 0.0,
    }


def booster(betas, context):
    """clip(exp(Σ β_j u_j), ×0.5, ×2) over the inputs a reading's betas name; ×1 with none."""
    inputs = normalised_inputs(context)
    total = sum(beta * inputs[name] for name, beta in betas.items())
    return max(BOOSTER_FLOOR, min(BOOSTER_CEILING, math.exp(total)))


def wall_clock_booster(minutes_left):
    """√(195 ÷ minutes left), held ×0.5–×2: expiring gamma makes a wall pull harder as the close nears."""
    if not minutes_left or minutes_left <= 0:
        return 1.0
    return max(BOOSTER_FLOOR, min(BOOSTER_CEILING, math.sqrt(WALL_CLOCK_MINUTES / minutes_left)))


def rates_tide(ten_year_closes):
    """The 10-year yield's 20-session change in basis points against the spread of such changes over 250 sessions
    (MAD × 1.4826, floor 5 bp): a z; None under 60 sessions of daily closes."""
    closes = [close * 10.0 for close in ten_year_closes]       # $TNX quotes in tenths of a percent → basis points
    if len(closes) < 60:
        return None
    changes = [b - a for a, b in zip(closes, closes[TIDE_SESSIONS:])][-TIDE_HISTORY_SESSIONS:]
    median = baseline.weighted_median(changes, [1.0] * len(changes))
    spread = baseline.MAD_TO_SPREAD * baseline.weighted_median([abs(c - median) for c in changes], [1.0] * len(changes))
    return changes[-1] / max(spread, TIDE_FLOOR_BASIS_POINTS)


def context_of(facts, history):
    """Every context input of a read, plus trend-ness's three parts and mean rank: {kind: value or None}. `history` is
    the same-clock history of the stored context inputs ({subject_key: [(sessions_ago, value)]})."""
    parts = trend_ness_parts(facts.five_minute_closes(), facts.true_open, facts.day_high, facts.day_low)
    ranks = [same_clock_rank(parts[part], past_values(history, part)) for part in TREND_NESS_PARTS]
    mean_rank = sum(ranks) / len(ranks) if all(rank is not None for rank in ranks) else None
    rank_of_mean = same_clock_rank(mean_rank, past_values(history, TREND_RANK_MEAN))
    gamma_word = facts.value("dealer_gamma_sign")
    path = facts.forecast["path_moves"].get(30)
    return {
        **parts, TREND_RANK_MEAN: mean_rank,
        TREND_NESS: trend_ness(rank_of_mean, facts.minute),
        PATH_STRAIGHTNESS: path["straightness"] if path else None,
        DEALER_GAMMA: 1.0 if gamma_word == "positive" else -1.0 if gamma_word == "negative" else None,
        RATES_TIDE: rates_tide(facts.daily_closes(registry.TEN_YEAR_SYMBOL)),
        VIX_LEVEL: facts.value("vix_calendar_year_pct", symbol="$VIX"),
        TIME_OF_DAY: facts.minute / facts.session_length if facts.session_length else None,
        DOWN_DAY: float(facts.down_day) if facts.price is not None and facts.prior_close is not None else None,
    }


def past_values(history, kind):
    """The stored past same-clock values of one context input, one per past read."""
    return [value for _, value in history.get(registry.subject_key(kind=kind), [])]
