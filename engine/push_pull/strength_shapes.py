# How unusual becomes how strong: the shapes a reading's z (how far from usual, in spreads) is turned into a strength.
# Inside the wiggle (|z| < 0.5) nothing counts; beyond it tanh saturates, so a huge reading is never more than 1 in
# size. Steady follows the sign of z (more of it pushes the same way); reversing is steady with the sign flipped (what
# surged gets given back); one-sided counts only z above the wiggle and points the way the reading's side says (the
# grind at an extreme); clipped keeps z itself, held to ±3 (the give-back's size grows with the day's trend); catch-up
# is the raw value itself in points, no z at all (SPY leads, SPX follows by that much).
import math

WIGGLE = 0.5
CLIP = 3.0
STEADY, REVERSING, ONE_SIDED, CLIPPED, CATCH_UP = "steady", "reversing", "one_sided", "clipped", "catch_up"
SHAPES = (STEADY, REVERSING, ONE_SIDED, CLIPPED, CATCH_UP)


def steady(z):
    """sign(z) × tanh(max(0, |z| − wiggle)): nothing inside the wiggle, saturating towards ±1 beyond it."""
    return math.copysign(math.tanh(max(0.0, abs(z) - WIGGLE)), z) if z else 0.0


def reversing(z):
    """Steady with the sign flipped: the move is expected to be given back."""
    return -steady(z)


def one_sided(z, side):
    """side × tanh(max(0, z − wiggle)): only a reading above the wiggle counts, pointing the way its side says."""
    return side * math.tanh(max(0.0, z - WIGGLE)) if side else 0.0


def clipped(z):
    """z held to ±CLIP: a size that grows with how unusual the day is, never beyond three spreads."""
    return max(-CLIP, min(CLIP, z))


def strength(shape, z=None, side=None, raw=None):
    """A reading's strength from its shape: z-shapes from z (one-sided also from the side), clipped from z, catch-up
    from the raw value in points; None when the input the shape needs is missing."""
    if shape == CATCH_UP:
        return raw
    if z is None:
        return None
    if shape == STEADY:
        return steady(z)
    if shape == REVERSING:
        return reversing(z)
    if shape == ONE_SIDED:
        return one_sided(z, side or 0)
    if shape == CLIPPED:
        return clipped(z)
    raise ValueError(f"unknown strength shape {shape}")
