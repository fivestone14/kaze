# How much a reading's strength counts: its weight in swings per unit strength, earned. History is worth a fixed
# number of days (twenty for a live reading, none for one still earning) and live days add theirs, both shrunk towards
# 0 by a prior worth forty days: w = (h·ŵ_hist + n·k·ŵ_live) ÷ (h + n·k + 40). k is how much of the live fit is signal
# rather than noise, 1 − (se ÷ ŵ_live)² held to 0..1 with se the fit's day-clustered standard error, so a fit as
# shaky as its own size counts for no days at all, and live days count for nothing until there are enough of them to
# measure their noise. A live reading so starts at a third of its tested size and an earning one at 0; either moves
# only as live days vouch for it. The Trend's weight is a day-type pair, a + c·tn, blended the same way coefficient by
# coefficient. The live fit, its noise and its day count come from the promotion ledger (grade/promotion_ledger.py),
# as known at the read's cut. Until a reading is promoted, live days may shrink its weight to 0 but never turn it
# against the way its history says it pushes; for the Trend that is checked at today's trend-ness.
from engine.push_pull import reading_specs

PRIOR_DAYS = 40


def signal_share(live_size, live_noise):
    """k = 1 − (se ÷ ŵ_live)², held to 0..1: the share of a live fit that is signal; 0 with no noise measured yet."""
    if live_noise is None or not live_size:
        return 0.0
    return max(0.0, 1.0 - (live_noise / live_size) ** 2)


def earned(history_size, history_days, live_size, live_days, live_noise):
    """(h·ŵ_hist + n·k·ŵ_live) ÷ (h + n·k + 40): the shrunk blend of what history and live days say."""
    days = live_days * signal_share(live_size, live_noise)
    return (history_days * history_size + days * (live_size or 0.0)) / (history_days + days + PRIOR_DAYS)


def weight_for(spec, trend_ness, ledger):
    """A reading's weight at this read: 0 for a logged or demoted reading; else its earned fixed size, or its earned
    day-type pair at today's trend-ness. `ledger` is the promotion ledger's entry for the reading's weight name, or
    None."""
    if spec.status == reading_specs.LOGGED or (ledger and ledger.get("demoted")):
        return 0.0
    live_days = ledger["live_days"] if ledger else 0
    if spec.weight_rule == reading_specs.DAY_TYPE:
        tn = trend_ness if trend_ness is not None else 0.5
        a_hist, c_hist = spec.day_type
        a_live, c_live = (ledger["live_day_type"] if ledger and ledger.get("live_day_type") else (0.0, 0.0))
        a_noise, c_noise = (ledger["live_day_type_noise"] if ledger and ledger.get("live_day_type_noise")
                            else (None, None))
        a = earned(a_hist, spec.history_days, a_live, live_days, a_noise)
        c = earned(c_hist, spec.history_days, c_live, live_days, c_noise)
        weight, history_weight = a + c * tn, a_hist + c_hist * tn
    else:
        weight = earned(spec.history_size, spec.history_days, ledger["live_size"] if ledger else 0.0, live_days,
                        ledger.get("live_noise") if ledger else None)
        history_weight = spec.history_size
    return weight if (ledger and ledger.get("promoted")) or weight * history_weight > 0 else 0.0
