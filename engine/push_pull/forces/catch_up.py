# C · Catch-up: another market leads SPX. SPY reprices first and SPX's print catches up within a minute or two: the
# SPY-implied SPX less SPX at the same minute poll, against today's median basis (measured by the collect step from the
# minute quotes, which land live). Logged at weight 0: by the time a read is published the catch-up has mostly
# happened.
from engine.push_pull.read_facts import Reading

# Beyond this many points the basis is a bad print, not a lead.
BASIS_LIMIT_POINTS = 3.0


def spy_basis(read):
    """How far SPY has run ahead of (positive) or behind SPX against today's median basis, in points."""
    value = read.value("spy_basis_points")
    if value is None:
        return Reading.blank_for(read.blank_reason("spy_basis_points"))
    if abs(value) > BASIS_LIMIT_POINTS:
        return Reading.blank_for("no_activity")
    return Reading(value)
