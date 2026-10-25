# C · Catch-up: another market leads SPX. SPY reprices first and SPX's print catches up within a minute or two: the
# SPY-implied SPX less SPX on the same 1-minute bar, against today's median basis. Logged at weight 0: by the time a
# read is published the catch-up has mostly happened.
from engine.push_pull.read_facts import Reading
from engine.store import registry

# Beyond this many points the basis is a bad print, not a lead.
BASIS_LIMIT_POINTS = 3.0


def spy_basis(read):
    """SPY scaled onto SPX at the day's first shared bar, less SPX, at the newest shared bar, minus today's median of
    that series: how far SPY has run ahead of (positive) or behind SPX, in points."""
    spx = {bar["at"]: bar["close"] for bar in read.bars}
    spy = {bar["at"]: bar["close"] for bar in read.spy_bars}
    shared = sorted(set(spx) & set(spy))
    if not shared or not read.spy_bars:
        return Reading.blank_for("feed_missing" if not read.spy_bars else registry.NOT_FORMED_REASON)
    first = shared[0]
    ratio = spx[first] / spy[first]
    basis = [spy[at] * ratio - spx[at] for at in shared]
    ordered = sorted(basis)
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else (ordered[len(ordered) // 2 - 1]
                                                                   + ordered[len(ordered) // 2]) / 2
    value = basis[-1] - median
    if abs(value) > BASIS_LIMIT_POINTS:
        return Reading.blank_for("no_activity")
    return Reading(value)
