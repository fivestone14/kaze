# What is usual for a reading at this time of day, and how widely it varies: the baseline every reading's z is measured
# against. The history is the reading's own stored raw values at the same clock (±15 minutes) over the past 60
# sessions, each session counting once (its values in the window share one weight) and recent sessions counting more
# (a half-life in sessions). Directional readings are centred on 0; readings with a built-in level take the weighted
# median as their usual. The spread is the weighted median absolute deviation × 1.4826 (the spread a normal curve
# would show), never below the reading's floor. Fewer than MIN_SESSIONS sessions of history leaves the z blank.
from engine import session_clock
from engine.store import store_queries

SESSIONS = 60
CLOCK_POOL_MINUTES = 15
# A rarely firing gated reading pools its history across the whole session instead (the grind fires on a few reads
# a day, so ten sessions firing within ±15 minutes of one clock may never come).
WHOLE_DAY_MINUTES = 400
MIN_SESSIONS = 10
MAD_TO_SPREAD = 1.4826
USUAL_ZERO, USUAL_SAME_CLOCK = "zero", "same_clock_median"
USUAL_RULES = (USUAL_ZERO, USUAL_SAME_CLOCK)
# The reasons a z stays blank with a raw value present.
TOO_FEW_SESSIONS = "too_few_sessions"


def history(conn, trade_date, cut_at, measure_name, pool_minutes=CLOCK_POOL_MINUTES):
    """Every stored value of a measure at the same clock (±pool_minutes, the whole session at WHOLE_DAY_MINUTES) over
    the past SESSIONS sessions, by subject: {subject_key: [(sessions_ago, value), ...]}, sessions_ago counting 1 for
    the newest past session."""
    minute = session_clock.minute_of_session(cut_at, session_clock.to_eastern(cut_at).date())
    sessions = store_queries.read_sessions_before(conn, trade_date, SESSIONS)
    if not sessions:
        return {}
    ages = {day: len(sessions) - index for index, day in enumerate(sessions)}
    by_subject = {}
    for row in store_queries.values_at_same_clock(conn, measure_name, sessions[0], trade_date, cut_at,
                                                 pool_minutes + 60):
        at = session_clock.parse_iso(row["cut_at"])
        if abs(session_clock.minute_of_session(at, session_clock.to_eastern(at).date()) - minute) > pool_minutes:
            continue
        by_subject.setdefault(row["subject_key"], []).append((ages[row["trade_date"]], row["value_number"]))
    return by_subject


def baseline(values, usual_rule, half_life_sessions, floor, gated=False):
    """The usual and the spread from (sessions_ago, value) pairs, or (None, None, sessions) with too few sessions. A
    gated reading's zeros (its gate shut) are left out, so the spread is that of the readings that spoke."""
    if gated:
        values = [(age, value) for age, value in values if value != 0]
    sessions = {age for age, _ in values}
    if len(sessions) < MIN_SESSIONS:
        return None, None, len(sessions)
    per_session = {age: sum(1 for a, _ in values if a == age) for age in sessions}
    weights = [0.5 ** (age / half_life_sessions) / per_session[age] for age, _ in values]
    points = [value for _, value in values]
    usual = 0.0 if usual_rule == USUAL_ZERO else weighted_median(points, weights)
    spread = MAD_TO_SPREAD * weighted_median([abs(value - usual) for value in points], weights)
    return usual, max(spread, floor), len(sessions)


def weighted_median(values, weights):
    """The value at half the total weight, values sorted."""
    order = sorted(range(len(values)), key=lambda index: values[index])
    total, running = sum(weights), 0.0
    for index in order:
        running += weights[index]
        if running >= total / 2:
            return values[index]
    return values[order[-1]]


def z_score(value, usual, spread):
    """How many spreads the value sits from usual; None without a baseline or with a zero spread."""
    if value is None or usual is None or not spread:
        return None
    return (value - usual) / spread


