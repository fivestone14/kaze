# The push readings (which way): each read's inputs turned into a value in words and a lean, the chance of up if
# price moves were the reading trusted, and the direction call that sums the leans by the trust each has earned, 0
# until the ledger writes one, so the call stays a coin flip. A reading with a blank input stays blank with that reason.
import math

from engine.store import components, registry, store


def observation_of(observations, measure_name, subject_key=store.WHOLE):
    """One observation row of a read by measure and subject, or None when the read holds none."""
    return next((row for row in observations
                 if row["measure_name"] == measure_name and row["subject_key"] == subject_key), None)


def tilted_lean(unit):
    """A lean from a unit tilt in -1..1: even plus the tilt times the unit."""
    return components.EVEN_CHANCE + components.LEAN_TILT * unit


def squashed_lean(value, scale, leans_up=True):
    """A signed value's lean: the tilt of tanh(value over scale), its sign from what positive means."""
    return tilted_lean(math.tanh(value / scale) * (1 if leans_up else -1))


def feed_of(row):
    """The feed stamp an observation carries (feed, status, age, newest input), to stamp a reading built from it."""
    if row is None:
        return None
    return {"source_feed_name": row["source_feed_name"], "feed_status": row["feed_status"],
            "feed_age_seconds": row["feed_age_seconds"], "market_at": row["newest_input_market_at"]}


def reading_entry(name, value, lean, reason, stamp_row):
    """One reading as the forecast carries it: name, value in words, lean, blank reason and the input's feed stamp."""
    return {"name": name, "value": value, "lean_if_trusted": lean, "reason": reason, "feed": feed_of(stamp_row)}


def build_reading(name, inputs, build):
    """One reading from its input rows' values, or blank with the first blank input's reason; the first input stamps."""
    stamp = next((row for row in inputs if row is not None), None)
    values = [store.observation_value(row) if row is not None else None for row in inputs]
    for row, value in zip(inputs, values):
        if value is None:
            return reading_entry(name, None, None, row["blank_reason"] if row is not None else "feed_missing", stamp)
    text, lean = build(*values)
    return reading_entry(name, text, lean, None if lean is not None else "no_activity", stamp)


def readings(observations, path_moves):
    """Every push reading of a read from its observations and the range engine's path moves, in the registry's order."""
    # The value in words is what the phone shows beside the lean, so it is short and title-cased here, once.
    def row(measure_name, **subject):
        """The read's observation of a measure and subject."""
        return observation_of(observations, measure_name, registry.subject_key(**subject))

    scale = components.LEAN_SCALE
    yield_measure = registry.MEASURES_BY_NAME["ten_year_yield_change_basis_points"]
    yield_leans_up = yield_measure["positive_value_means"] == "leans_up"
    return [
        build_reading(components.PRICE_TREND,
                      [row("move_points", since="prior_close"), row("move_points", since="open")],
                      lambda since_close, since_open: (f"{since_close:+.0f} Since Close",
                                                       squashed_lean(since_close, scale[components.PRICE_TREND]))),
        straightness_reading(path_moves[components.STRAIGHTNESS_WINDOW_MINUTES], row("spx_price_points")),
        build_reading(components.BREADTH, [row("nyse_advancing_portion")],
                      lambda share: (f"{share:.0%} Advancing", share)),
        build_reading(components.BIG_TECH_VS_REST,
                      [row("fund_change_from_prior_close_pct", symbol=components.BIG_TECH_SYMBOL),
                       row("fund_change_from_prior_close_pct", symbol=components.REST_SYMBOL)],
                      lambda big_tech, rest: (f"{components.BIG_TECH_SYMBOL} {big_tech - rest:+.1f}% vs "
                                              f"{components.REST_SYMBOL}",
                                              squashed_lean(big_tech - rest, scale[components.BIG_TECH_VS_REST]))),
        build_reading(components.OPTIONS_BUYERS, [row("bullish_portion_of_sided_premium")],
                      lambda bullish: (f"{bullish:.0%} Bullish", bullish)),
        build_reading(components.YIELD_CHANGE, [row("ten_year_yield_change_basis_points")],
                      lambda change: (f"{change:+.1f} bp",
                                      squashed_lean(change, scale[components.YIELD_CHANGE], yield_leans_up))),
        build_reading(components.MIRAI_CLAUDE, [row("mirai_claude_forecast_pct", horizon=components.CLAUDE_LEAN_HORIZON,
                                                    side=side) for side in components.CLAUDE_SIDES],
                      lambda up, flat, down: (f"{up / (up + down):.0%} Up" if up + down else "No Side",
                                              up / (up + down) if up + down else None)),
    ]


def straightness_reading(path, price_row):
    """Straightness over the 30-minute window as a lean: the tilt of straightness, signed by the net move."""
    if path is None:
        reason = price_row["blank_reason"] if price_row is not None and price_row["blank_reason"] \
            else registry.NOT_FORMED_REASON
        return reading_entry(components.STRAIGHTNESS, None, None, reason, price_row)
    net = path["up"] - path["down"]
    if path["straightness"] is None:
        return reading_entry(components.STRAIGHTNESS, None, None, "no_activity", price_row)
    lean = tilted_lean(path["straightness"] * (1 if net > 0 else -1 if net < 0 else 0))
    return reading_entry(components.STRAIGHTNESS, f"{path['straightness']:.2f} Straight, Net {net:+.1f}", lean, None,
                         price_row)


def combined_lean(leans, weights):
    """Even plus the sum of weight times (lean less even) over the leans present; the one formula of the call."""
    return components.EVEN_CHANCE + sum(weights.get(name, 0.0) * (lean - components.EVEN_CHANCE)
                                        for name, lean in leans.items() if lean is not None)


def direction(push, weights):
    """One channel's direction call from the readings with a lean, the trusts scaled to sum to at most 1."""
    # The weights actually applied come back beside it.
    present = {entry["name"]: entry["lean_if_trusted"] for entry in push if entry["lean_if_trusted"] is not None}
    applied = {name: weights.get(name, 0.0) for name in present}
    total = sum(applied.values())
    if total > 1:
        applied = {name: weight / total for name, weight in applied.items()}
        total = 1.0
    chance = combined_lean(present, applied)
    return {"chance_up_if_moves": chance, "trust_applied": total, "weights": applied,
            "text": direction_text(chance, total)}


def direction_text(chance, trust_applied):
    """The call in words: the coin flip until any trust is earned, then which way and how strongly."""
    if trust_applied == 0:
        return components.DIRECTION_TEXT_UNTIL_EARNED
    if chance == components.EVEN_CHANCE:
        return "even"
    return f"up {chance:.0%}" if chance > components.EVEN_CHANCE else f"down {1 - chance:.0%}"


def apply_push(forecast, push, weights):
    """Put the readings on the forecast and call the direction per channel from them with the ledger's weights."""
    # Each entry gains its weight per channel; the headline channel's call goes on top of the forecast.
    forecast["push"] = push
    for name, channel in forecast["channels"].items():
        channel["direction"] = direction(push, {reading: weights.get((components.PUSH_READING_KIND, reading, name), 0.0)
                                               for reading in components.PUSH_READINGS})
    for entry in push:
        entry["weight_by_channel"] = {name: channel["direction"]["weights"].get(entry["name"], 0.0)
                                      for name, channel in forecast["channels"].items()}
    headline = forecast["channels"][components.HEADLINE_HORIZON]["direction"]
    forecast.update({"chance_up_if_moves": headline["chance_up_if_moves"], "trust_applied": headline["trust_applied"],
                     "direction_text": headline["text"]})


def lean_rows(snapshot, push):
    """The push readings as observations on the read's snapshot: each lean and its value in words."""
    # Stamped with the input's feed, blank with the input's reason.
    rows = []
    for entry in push:
        feed = entry["feed"] or {}
        subject = registry.subject_key(reading=entry["name"])
        rows.append(store.observation_row(snapshot, "push_lean_probability", store.rounded(entry["lean_if_trusted"]),
                                          subject, blank_reason=entry["reason"], **feed))
        rows.append(store.observation_row(snapshot, "push_reading_text", entry["value"], subject,
                                          blank_reason=entry["reason"], **feed))
    return rows
