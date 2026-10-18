# The push readings (which way): each read's inputs turned into a value in words and a lean, the chance of up if
# price moves, and the direction call that sums the leans by the trust each has earned, 0 until the ledger writes one,
# so the call stays a coin flip until a reading earns a say. A reading with a blank input stays blank with that reason.
import math

from engine import registry, store

EVEN = registry.DIRECTION_UNTIL_EARNED
CLAUDE_SIDES = ("up", "flat", "down")


def observation_of(observations, measure_name, subject_key=store.WHOLE):
    """One observation row of a read by measure and subject, or None when the read holds none."""
    return next((row for row in observations
                 if row["measure_name"] == measure_name and row["subject_key"] == subject_key), None)


def squashed_lean(value, scale, leans_up=True):
    """A signed value's lean: even plus the tilt times tanh(value over scale), the sign from what positive means."""
    return EVEN + registry.LEAN_TILT * math.tanh(value / scale) * (1 if leans_up else -1)


def feed_of(row):
    """The feed stamp an observation carries (feed, status, age, newest input), to stamp a reading built from it."""
    if row is None:
        return None
    return {"source_feed_name": row["source_feed_name"], "feed_status": row["feed_status"],
            "feed_age_seconds": row["feed_age_seconds"], "market_at": row["newest_input_market_at"]}


def entry(name, value, lean, reason, stamp_row):
    """One reading as the forecast carries it: name, value in words, lean, blank reason and the input's feed stamp."""
    return {"name": name, "value": value, "lean": lean, "reason": reason, "feed": feed_of(stamp_row)}


def reading(name, inputs, build):
    """One reading built from its input rows' values, or blank with the first blank input's reason; stamped with the
    first input's feed."""
    stamp = next((row for row in inputs if row is not None), None)
    values = [store.observation_value(row) if row is not None else None for row in inputs]
    for row, value in zip(inputs, values):
        if value is None:
            return entry(name, None, None, row["blank_reason"] if row is not None else "feed_missing", stamp)
    text, lean = build(*values)
    return entry(name, text, lean, None if lean is not None else "no_activity", stamp)


def readings(observations, path):
    """Every push reading of a read from its observations and the range engine's path legs, in the registry's order."""
    def row(measure_name, **subject):
        """The read's observation of a measure and subject."""
        return observation_of(observations, measure_name, registry.subject_key(**subject))

    scale = registry.LEAN_SCALE
    yield_measure = registry.MEASURES_BY_NAME["ten_year_yield_change_basis_points"]
    yield_leans_up = yield_measure["positive_value_means"] == "leans_up"
    return [
        reading(registry.PRICE_TREND, [row("move_points", since="prior_close"), row("move_points", since="open")],
                lambda since_close, since_open: (f"{since_close:+.2f} since close, {since_open:+.2f} since open",
                                                 squashed_lean(since_close, scale[registry.PRICE_TREND]))),
        straightness_reading(path[registry.STRAIGHTNESS_WINDOW_MINUTES], row("spx_price_points")),
        reading(registry.BREADTH, [row("nyse_advancing_portion"), row("nyse_advancing_count"),
                                   row("nyse_declining_count")],
                lambda share, advancing, declining: (f"{int(advancing)} advancing / {int(declining)} declining",
                                                     share)),
        reading(registry.BIG_TECH_VS_REST, [row("fund_change_from_prior_close_pct", symbol=registry.BIG_TECH_SYMBOL),
                                            row("fund_change_from_prior_close_pct", symbol=registry.REST_SYMBOL)],
                lambda big_tech, rest: (f"{registry.BIG_TECH_SYMBOL} {big_tech:+.2f}% vs {registry.REST_SYMBOL} "
                                        f"{rest:+.2f}%",
                                        squashed_lean(big_tech - rest, scale[registry.BIG_TECH_VS_REST]))),
        reading(registry.OPTIONS_BUYERS, [row("bullish_portion_of_sided_premium")],
                lambda bullish: (f"{bullish:.1%} of sided premium bullish", bullish)),
        reading(registry.YIELD_CHANGE, [row("ten_year_yield_change_basis_points")],
                lambda change: (f"{change:+.1f} bp since prior close",
                                squashed_lean(change, scale[registry.YIELD_CHANGE], yield_leans_up))),
        reading(registry.MIRAI_CLAUDE, [row("mirai_claude_forecast_pct", horizon=registry.CLAUDE_LEAN_HORIZON,
                                            side=side) for side in CLAUDE_SIDES],
                lambda up, flat, down: (f"up {up:.1f} / flat {flat:.1f} / down {down:.1f}",
                                        up / (up + down) if up + down else None)),
    ]


def straightness_reading(legs, price_row):
    """Straightness over the 30-minute window as a lean: even plus the tilt times straightness, signed by the net."""
    if legs is None:
        reason = price_row["blank_reason"] if price_row is not None and price_row["blank_reason"] \
            else registry.NOT_FORMED_REASON
        return entry(registry.STRAIGHTNESS, None, None, reason, price_row)
    net, path = legs["up"] - legs["down"], legs["up"] + legs["down"]
    if legs["straightness"] is None:
        return entry(registry.STRAIGHTNESS, None, None, "no_activity", price_row)
    lean = EVEN + registry.LEAN_TILT * legs["straightness"] * (1 if net > 0 else -1 if net < 0 else 0)
    return entry(registry.STRAIGHTNESS, f"{legs['straightness']:.2f} straight, net {net:+.2f} over {path:.2f} of "
                 f"path in {registry.STRAIGHTNESS_WINDOW_MINUTES} min", lean, None, price_row)


def direction(push, weights):
    """One channel's direction call: even plus the sum of trust times (lean less even) over the readings with a lean,
    the trusts scaled to sum to at most 1; the weights actually applied come back beside it."""
    present = {entry["name"]: entry["lean"] for entry in push if entry["lean"] is not None}
    applied = {name: weights.get(name, 0.0) for name in present}
    total = sum(applied.values())
    if total > 1:
        applied = {name: weight / total for name, weight in applied.items()}
        total = 1.0
    chance = EVEN + sum(weight * (present[name] - EVEN) for name, weight in applied.items())
    return {"chance_up_if_moves": chance, "trust": total, "weights": applied, "text": direction_text(chance, total)}


def direction_text(chance, trust):
    """The call in words: the coin flip until any trust is earned, then which way and how strongly."""
    if trust == 0:
        return registry.DIRECTION_TEXT_UNTIL_EARNED
    if chance == EVEN:
        return "even"
    return f"up {chance:.0%}" if chance > EVEN else f"down {1 - chance:.0%}"


def reading_rows(snapshot, push):
    """The push readings as observations on the read's snapshot: each lean and its value in words, stamped with the
    input's feed, blank with the input's reason."""
    rows = []
    for reading_entry in push:
        feed = reading_entry["feed"] or {}
        subject = registry.subject_key(reading=reading_entry["name"])
        rows.append(store.observation_row(snapshot, "push_lean_probability", store.rounded(reading_entry["lean"]),
                                          subject, blank_reason=reading_entry["reason"], **feed))
        rows.append(store.observation_row(snapshot, "push_reading_text", reading_entry["value"], subject,
                                          blank_reason=reading_entry["reason"], **feed))
    return rows
