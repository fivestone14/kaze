# How much a reading's strength counts: its weight in swings per unit strength, earned. History is worth a fixed
# number of days (twenty for a live reading, none for one still earning) and live days add theirs, both shrunk towards
# 0 by a prior worth forty days: w = (h·ŵ_hist + n·ŵ_live) ÷ (h + n + 40). A live reading so starts at a third of its
# tested size and an earning one at 0; either moves only as live days vouch for it. The Trend's weight is a day-type
# pair, a + c·tn, blended the same way coefficient by coefficient. The live fit and its day count come from the
# promotion ledger (grade/promotion_ledger.py), as known at the read's cut.
from engine.push_pull import reading_specs

PRIOR_DAYS = 40


def earned(history_size, history_days, live_size, live_days):
    """(h·ŵ_hist + n·ŵ_live) ÷ (h + n + 40): the shrunk blend of what history and live days say."""
    return (history_days * history_size + live_days * (live_size or 0.0)) / (history_days + live_days + PRIOR_DAYS)


def weight_for(spec, trend_ness, ledger):
    """A reading's weight at this read: 0 for a logged or demoted reading; else its earned fixed size, or its earned
    day-type pair at today's trend-ness. `ledger` is the promotion ledger's entry for the reading's weight name, or
    None."""
    if spec.status == reading_specs.LOGGED or (ledger and ledger.get("demoted")):
        return 0.0
    live_days = ledger["live_days"] if ledger else 0
    if spec.weight_rule == reading_specs.DAY_TYPE:
        a_hist, c_hist = spec.day_type
        a_live, c_live = (ledger["live_day_type"] if ledger and ledger.get("live_day_type") else (0.0, 0.0))
        a = earned(a_hist, spec.history_days, a_live, live_days)
        c = earned(c_hist, spec.history_days, c_live, live_days)
        return a + c * (trend_ness if trend_ness is not None else 0.5)
    return earned(spec.history_size, spec.history_days, ledger["live_size"] if ledger else 0.0, live_days)

