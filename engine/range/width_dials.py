# The width dials: each dial's raw value for one channel's window (Fed, release, closing, acceleration, options vs
# tape), the applied value the ledger's weight gives it, and the one half-width formula the read and the trust ledger
# share: the dials other than the Fed multiply under the history's cap, the Fed multiplier stacks on top.
import math
from datetime import timedelta

from engine import session_clock
from engine.store import components, registry

# A dial at rest: the multiplier that changes nothing.
NO_EFFECT = 1.0
# Why a dial's raw value can be blank; the dials left out always read.
DIAL_BLANK = {components.ACCELERATION_DIAL: registry.NOT_FORMED_REASON,
              components.OPTIONS_VS_TAPE_DIAL: registry.NOT_PRICEABLE_REASON,
              components.CLOSING_DIAL: registry.TOO_FEW_SESSIONS, components.RELEASE_DIAL: registry.TOO_FEW_SESSIONS}
RELEASE_SPAN = timedelta(minutes=session_clock.SLOT_MINUTES)


def is_fed_event(kind):
    """True for a Fed decision, minutes, presser, testimony or speech, never a release; an event with no kind is not."""
    return str(kind or "").startswith(components.FED_EVENT_PREFIXES)


def fed_decision_at(events):
    """When the day's Fed decision lands, from the (starts_at, kind, tier) facts known at the cut, or None."""
    times = [starts_at for starts_at, kind, _ in events if kind in components.FED_DECISION_EVENT_KINDS]
    return session_clock.parse_iso(min(times)) if times else None


def releases(events):
    """The day's timed economic releases known at the cut, as {name, at}: data tiers only, Fed kinds never."""
    return [{"name": kind, "at": session_clock.parse_iso(starts_at)} for starts_at, kind, tier in events
            if str(tier or "").startswith(components.RELEASE_TIER_PREFIX) and not is_fed_event(kind)]


def release_times(events):
    """The moments of the day's timed economic releases known at the cut."""
    return [release["at"] for release in releases(events)]


def release_in_window(releases_at, window_from, ends_at):
    """True when a release's own five minutes overlap the window."""
    return any(at < ends_at and at + RELEASE_SPAN > window_from for at in releases_at)


def in_closing_window(ends_at, close_at, clipped):
    """True for an unclipped window ending inside the last minutes before the close, the fitted family."""
    return (not clipped and close_at is not None
            and ends_at >= close_at - timedelta(minutes=components.CLOSING_WINDOW_MINUTES))


def raw_values(horizon_name, ratios, window, fed_decision, releases_at, acceleration, options_over_tape):
    """Every dial's raw value for one channel's window; None where its input is missing (blank with DIAL_BLANK)."""
    in_fed_window = fed_decision is not None and fed_decision < window["ends_at"]
    release_raw = components.RELEASE_WINDOW_MULTIPLIER if horizon_name == components.RELEASE_FIXED_HORIZON \
        else ratios["release"]
    return {
        components.FED_DIAL: components.FED_OWNER_RULE_MULTIPLIER if in_fed_window else NO_EFFECT,
        components.RELEASE_DIAL: release_raw
        if release_in_window(releases_at, window["from"], window["ends_at"]) else NO_EFFECT,
        components.CLOSING_DIAL: ratios["closing"]
        if in_closing_window(window["ends_at"], window["close_at"], window["clipped"]) else NO_EFFECT,
        components.ACCELERATION_DIAL: acceleration,
        components.OPTIONS_VS_TAPE_DIAL: options_over_tape ** components.OPTIONS_VS_TAPE_POWER
        if options_over_tape is not None else None,
    }


def applied_value(raw, weight):
    """A dial's applied value, 1 + weight x (raw - 1); a blank raw applies nothing."""
    return NO_EFFECT + weight * (raw - NO_EFFECT) if raw is not None else NO_EFFECT


def dials_for(raw, weights, horizon_name):
    """Each dial with its raw value, the weight the ledger holds for it on this channel, and its applied value."""
    dials = {}
    for dial, value in raw.items():
        weight = weights.get((components.WIDTH_DIAL_KIND, dial, horizon_name), 0.0)
        dials[dial] = {registry.RAW_ROLE: value, registry.APPLIED_ROLE: applied_value(value, weight),
                       "weight": weight}
    return dials


def product_of(dials, role, fed_multiplier=None):
    """The product of the dials' values in a role, the Fed dial's replaced when a multiplier is given: (others, fed)."""
    others = math.prod(dial[role] if dial[role] is not None else NO_EFFECT
                       for name, dial in dials.items() if name != components.FED_DIAL)
    fed = dials[components.FED_DIAL][role] if fed_multiplier is None else fed_multiplier
    return others, fed


def capped(product, cap):
    """A product of dials held under the history's cap (no cap known: the product as it is)."""
    return min(product, cap) if cap is not None else product


def half_width(undialed, others_product, fed_multiplier, cap):
    """The band's half-width: swing x stretch, times the other dials held under the cap, times the Fed multiplier."""
    return undialed * capped(others_product, cap) * fed_multiplier
