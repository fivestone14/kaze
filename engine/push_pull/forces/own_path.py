# A · Own path: SPX's own moves as forces on itself. The gap (the true open against the prior close), the trend since
# the open, the give-back of a big day into the close, the fresh burst against the day's pace, the 15:00 fade, and the
# gap across a long average (logged only). Each function says what it measures; reading_specs says the rest.
from engine.push_pull.read_facts import Reading
from engine.store import registry

FRESH_BURST_MINUTES = 15
FADE_FROM_MINUTE, FADE_REFERENCE_MINUTE = 330, 300      # 15:00 against 14:30
LONG_AVERAGE_DAYS = (50, 100, 200)


def gap(read):
    """True open less prior close, in points: the overnight jump SPX would have printed had every stock traded."""
    if read.true_open is None:
        return Reading.blank_for(read.blank_reason("true_open_points"))
    if read.prior_close is None:
        return Reading.blank_for(read.blank_reason("prior_close_points"))
    return Reading(read.true_open - read.prior_close)


def trend_since_open(read):
    """Price less the true open, in points: how far the day has carried from where it really began."""
    if read.true_open is None:
        return Reading.blank_for(read.blank_reason("true_open_points"))
    if read.price is None:
        return Reading.blank_for("feed_missing")
    return Reading(read.price - read.true_open)


def give_back(read):
    """The same raw value as the trend (its z, clipped, sizes the give-back); blank on half days."""
    if read.half_day:
        return Reading.blank_for(registry.NOT_APPLICABLE)
    return trend_since_open(read)


def fresh_burst(read):
    """The last 15 minutes' move less the day's pace over 15 minutes: the newest move beyond the day's drift."""
    if read.true_open is None:
        return Reading.blank_for(read.blank_reason("true_open_points"))
    last = read.move_over(FRESH_BURST_MINUTES)
    if last is None or read.minute <= 0:
        return Reading.blank_for(registry.NOT_FORMED_REASON)
    pace = FRESH_BURST_MINUTES * (read.price - read.true_open) / read.minute
    return Reading(last - pace)


def fade_15_00(read):
    """SPX at 15:00 less SPX at 14:30, in points: a late burst that tends to fade into the last hour."""
    at_15, at_14_30 = read.close_at_minute(FADE_FROM_MINUTE), read.close_at_minute(FADE_REFERENCE_MINUTE)
    if at_15 is None or at_14_30 is None:
        return Reading.blank_for(registry.NOT_FORMED_REASON)
    return Reading(at_15 - at_14_30)


def gap_across_average(read):
    """How many of the 50/100/200-day averages the gap crossed, signed the way the fade points (against the gap);
    0 when none was crossed. Logged, never in the net: rare."""
    if read.true_open is None or read.prior_close is None:
        return Reading.blank_for(read.blank_reason("true_open_points") if read.true_open is None
                                 else read.blank_reason("prior_close_points"))
    closes = read.daily_closes(registry.SPX_SYMBOL)
    if len(closes) < max(LONG_AVERAGE_DAYS):
        return Reading.blank_for(registry.TOO_FEW_SESSIONS)
    crossed = 0
    for days in LONG_AVERAGE_DAYS:
        average = sum(closes[-days:]) / days
        if (read.prior_close - average) * (read.true_open - average) < 0:
            crossed += 1
    direction = (read.prior_close > read.true_open) - (read.prior_close < read.true_open)
    return Reading(direction * crossed)
