# From strengths to what the phone shows: each reading's points (strength × weight × booster × swing), the group sums
# under their caps, the net push, the odds of up, flat and down on the average grade, the wind and where the band's
# centres move. Only readings in the net count; logged readings are measured and stored but add nothing.
import math

from engine.push_pull import strength_shapes
from engine.store import components

# The net is held to ±0.35 swing; a group nobody has promoted yet is held to ±0.15 swing on its own.
TOTAL_CAP_SWINGS, YOUNG_GROUP_CAP_SWINGS = 0.35, 0.15
# The 80% band's half-width in spreads of a normal curve: the stretch multipliers are fitted to it.
EIGHTY_PERCENT_Z = 1.2816


def points_of(spec, strength, weight, booster, swing):
    """A reading's points: strength × weight × booster × swing; catch-up readings are already in points, so their
    weight is a plain ratio on the raw value and no swing enters. None when any part is missing."""
    if strength is None or weight is None or booster is None:
        return None
    if spec.shape == strength_shapes.CATCH_UP:
        return strength * weight * booster
    return strength * weight * booster * swing if swing is not None else None


def capped(total, cap):
    """A total held to ±cap and the share kept (1 when the cap does not bind)."""
    if cap is None or abs(total) <= cap or total == 0:
        return total, 1.0
    return math.copysign(cap, total), cap / abs(total)


def net_of(entries, swing, promoted_groups):
    """The group sums under the young-group cap, the net under the total cap, and the share each group kept, from
    the entries in the net ({name, group, points}); points None do not count."""
    groups = {}
    for entry in entries:
        if entry["points"] is not None and entry["in_the_net"]:
            groups[entry["group"]] = groups.get(entry["group"], 0.0) + entry["points"]
    shares = {}
    for group, total in groups.items():
        cap = None if swing is None or group in promoted_groups else YOUNG_GROUP_CAP_SWINGS * swing
        groups[group], shares[group] = capped(total, cap)
    net, total_share = capped(sum(groups.values()), TOTAL_CAP_SWINGS * swing if swing is not None else None)
    groups = {group: total * total_share for group, total in groups.items()}
    shares = {group: share * total_share for group, share in shares.items()}
    return net, groups, shares


def normal_cdf(x):
    """The standard normal curve's area below x."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def odds(net, swing, average_stretch, stretch, rung_points):
    """P(up), P(flat), P(down) of the average price over the window: the net against the average band's spread,
    σ_G = swing × average stretch ÷ 1.2816, with a flat zone the size of the channel's rung scaled to the average
    (rung × average stretch ÷ stretch). None without a swing or a fit."""
    if net is None or not swing or not average_stretch or not stretch:
        return None
    sigma = swing * average_stretch / EIGHTY_PERCENT_Z
    edge = rung_points * average_stretch / stretch
    up, down = normal_cdf((net - edge) / sigma), normal_cdf((-net - edge) / sigma)
    return {"up": up, "flat": max(0.0, 1.0 - up - down), "down": down, "sigma": sigma, "edge": edge}


def chance_up_if_moves(odds_of):
    """Up over up plus down: the old direction call's number, kept for the phone and the ledger."""
    if odds_of is None or odds_of["up"] + odds_of["down"] <= 0:
        return None
    return odds_of["up"] / (odds_of["up"] + odds_of["down"])


def wind(net, swing):
    """|net| ÷ swing named on the wind scale with its dots: ratio, level, dots; None without a swing."""
    if net is None or not swing:
        return None
    ratio = abs(net) / swing
    level = next(name for name, limit in components.WIND_LEVELS if limit is None or ratio < limit)
    return {"ratio": ratio, "level": level, "dots": sum(ratio >= step for step in components.WIND_DOT_STEPS),
            "side": components.PUSH_SIDE if net > 0 else components.PULL_SIDE if net < 0 else components.EVEN_SIDE}


def end_ratio(minutes):
    """How far the end price moves per point of average move for a steady push: 2T ÷ (T + 1), about 1.94 at 30."""
    return 2.0 * minutes / (minutes + 1.0)


def centres(price, net, minutes):
    """Where the bands' centres move: the average band to price + net, the end band to price + 2T/(T+1) × net."""
    if price is None or net is None:
        return None
    return {components.AVERAGE_BAND: price + net, components.BAND: price + end_ratio(minutes) * net}

