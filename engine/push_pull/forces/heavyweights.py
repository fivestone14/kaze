# H · Heavyweights: the big stocks pulling SPX. The leader surge fade (chips racing ahead of the Nasdaq get given
# back) and the grind at an extreme (the index held near its high or low by its largest names while breadth fades).
from engine.push_pull.read_facts import Reading
from engine.store import registry

LEADER, BENCHMARK = "SMH", "QQQ"
WINDOW_MINUTES = 60
# Within this many swings of the day's high or low the surge is quiet (the grind's turf) and the grind may speak.
EXTREME_SWINGS = 0.25
# The grind needs a slow approach: the last 15 minutes moved less than one swing towards the extreme.
SLOW_MINUTES, SLOW_SWINGS = 15, 1.0


def distance_to_extremes(read):
    """(swings below the day's high, swings above the day's low), or None when the day's range is not formed."""
    swing = read.swing()
    if read.price is None or swing is None or read.day_high is None or read.day_low is None:
        return None
    return (read.day_high - read.price) / swing, (read.price - read.day_low) / swing


def extreme_side(read):
    """+1 within EXTREME_SWINGS of the high (and nearer it than the low), −1 within it of the low, else 0."""
    distances = distance_to_extremes(read)
    if distances is None:
        return 0
    to_high, to_low = distances
    if to_high < EXTREME_SWINGS and to_high <= to_low:
        return 1
    return -1 if to_low < EXTREME_SWINGS else 0


def leader_surge_fade(read):
    """SMH's 60-minute change less QQQ's, in percentage points (no beta: the beta removes the surge that fades); 0 near
    the day's high or low, where the grind speaks instead."""
    leader = read.value("fund_change_window_pct", symbol=LEADER, window_minutes=WINDOW_MINUTES)
    benchmark = read.value("fund_change_window_pct", symbol=BENCHMARK, window_minutes=WINDOW_MINUTES)
    if leader is None:
        return Reading.blank_for(read.blank_reason("fund_change_window_pct", symbol=LEADER,
                                                   window_minutes=WINDOW_MINUTES))
    if benchmark is None:
        return Reading.blank_for(read.blank_reason("fund_change_window_pct", symbol=BENCHMARK,
                                                   window_minutes=WINDOW_MINUTES))
    if extreme_side(read):
        return Reading.gate_closed()
    return Reading(leader - benchmark)


def grind_at_extreme(read):
    """Breadth's 60-minute change turned against the side of the extreme SPX sits at (+ means breadth fading while the
    index holds its high, or improving while it holds its low), with that side; 0 away from an extreme or after a
    fast approach."""
    change = read.value("breadth_net_change_count", window_minutes=WINDOW_MINUTES)
    if change is None:
        return Reading.blank_for(read.blank_reason("breadth_net_change_count", window_minutes=WINDOW_MINUTES))
    side = extreme_side(read)
    if not side:
        return Reading.gate_closed()
    last, swing = read.move_over(SLOW_MINUTES), read.swing()
    if last is None or swing is None:
        return Reading.blank_for(registry.NOT_FORMED_REASON)
    if last * side / swing >= SLOW_SWINGS:
        return Reading.gate_closed()
    return Reading(-side * change, side=side)
