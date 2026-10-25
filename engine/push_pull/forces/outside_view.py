# F · Outside view: Mirai's Claude forecast, counted only for what it says beyond the base rate it was shown. The
# answer is held through later reads, fading with age, and drops out at 40 minutes (the feed's own carry).
from engine import session_clock
from engine.push_pull.read_facts import Reading
from engine.store import components

HORIZON = components.HEADLINE_HORIZON
HORIZON_MINUTES = components.HORIZON_MINUTES[HORIZON]


def claude_beyond_base(read):
    """(up − down) of Claude's 30-minute answer less (up − down) of its base rate, as a share (−1..1), times how fresh
    the answer still is: max(0, 1 − age ÷ 30 minutes)."""
    up, down = (read.value("mirai_claude_forecast_pct", horizon=HORIZON, side=side) for side in ("up", "down"))
    base_up, base_down = (read.value("mirai_claude_base_rate_pct", horizon=HORIZON, side=side)
                          for side in ("up", "down"))
    read_at = read.value("mirai_claude_read_at")
    if up is None or down is None:
        return Reading.blank_for(read.blank_reason("mirai_claude_forecast_pct", horizon=HORIZON, side="up"))
    if base_up is None or base_down is None or read_at is None:
        return Reading.blank_for("feed_missing")
    age_minutes = (read.cut_at - session_clock.parse_iso(read_at)).total_seconds() / 60.0
    freshness = max(0.0, 1.0 - age_minutes / HORIZON_MINUTES)
    return Reading(((up - down) - (base_up - base_down)) / 100.0 * freshness)
