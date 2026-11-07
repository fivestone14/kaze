# E · Option prices: what same-day protection costs. The skew is the median implied volatility of puts less that of
# calls among trades near 25 delta, in volatility points, over the last 30 minutes; Put Insurance is that skew against
# its usual at the same time of day: protection dearer or cheaper than usual.
from engine.push_pull.read_facts import Reading

SKEW_NOW = "option_skew_last_30_minutes_pct"


def put_insurance(read):
    """Puts' implied volatility less calls' near 25 delta over the last 30 minutes, in volatility points."""
    value = read.value(SKEW_NOW)
    if value is None:
        return Reading.blank_for(read.blank_reason(SKEW_NOW))
    return Reading(value)
