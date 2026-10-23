# The push/pull factors (which way): each read's inputs turned into a value in words, a short answer from the factor's
# fixed words and a lean, the chance of up if price moves; a factor leaning above the even zone pushes price up, below
# it pulls price down. The direction call weighs every factor with a lean at an equal share, the tug-of-war's net. A
# factor with a blank input stays blank, no answer, no side, with that reason. How far price travels is the range
# engine's band, never a factor here.
import math

from engine.store import components, registry, store


def tilted_lean(unit):
    """A lean from a unit tilt in -1..1: even plus the tilt times the unit."""
    return components.EVEN_CHANCE + components.LEAN_TILT * unit


def squashed_lean(value, scale, leans_up=True):
    """A signed value's lean: the tilt of tanh(value over scale), its sign from what positive means."""
    return tilted_lean(math.tanh(value / scale) * (1 if leans_up else -1))


def feed_of(row):
    """The feed stamp an observation carries (feed, status, age, newest input), to stamp a factor built from it."""
    if row is None:
        return None
    return {"source_feed_name": row["source_feed_name"], "feed_status": row["feed_status"],
            "feed_age_seconds": row["feed_age_seconds"], "market_at": row["newest_input_market_at"]}


def side_of(lean):
    """Which side a lean is on: push above the even zone, pull below it, even inside it, None without a lean."""
    if lean is None:
        return None
    if abs(lean - components.EVEN_CHANCE) <= components.EVEN_LEAN_ZONE:
        return components.EVEN_SIDE
    return components.PUSH_SIDE if lean > components.EVEN_CHANCE else components.PULL_SIDE


def factor_entry(name, value, answer, lean, reason, stamp_row):
    """One factor as the forecast carries it: name, value and answer in words, lean, side, blank reason, feed stamp."""
    return {"name": name, "value": value, "answer": answer, "lean": lean, "side": side_of(lean), "reason": reason,
            "feed": feed_of(stamp_row)}


def leaning_answer(name, lean, positive, **numbers):
    """A factor's short answer from its fixed words: even inside the even zone, else up or down by the sign given."""
    up, down, even = components.PUSH_PULL_ANSWER_WORDS[name]
    if lean is None:
        return None
    if abs(lean - components.EVEN_CHANCE) <= components.EVEN_LEAN_ZONE:
        return even
    return (up if positive else down).format(**numbers)


def build_factor(name, inputs, build):
    """One factor from its input rows' values, or blank with the first blank input's reason; the first input stamps."""
    stamp = next((row for row in inputs if row is not None), None)
    values = [store.observation_value(row) if row is not None else None for row in inputs]
    for row, value in zip(inputs, values):
        if value is None:
            return factor_entry(name, None, None, None, row["blank_reason"] if row is not None else "feed_missing",
                                stamp)
    text, lean, answer = build(*values)
    return factor_entry(name, text, answer, lean, None if lean is not None else "no_activity", stamp)


def factors(observations, path_moves):
    """Every push/pull factor of a read from its observations and the range engine's path moves, in registry order."""
    # The value in words is the exact figure the phone shows under the answer, so it is short and title-cased here.
    def row(measure_name, **subject):
        """The read's observation of a measure and subject."""
        return store.observation_of(observations, measure_name, registry.subject_key(**subject))

    scale = components.LEAN_SCALE
    yield_measure = registry.MEASURES_BY_NAME["ten_year_yield_change_basis_points"]
    yield_leans_up = yield_measure["positive_value_means"] == "leans_up"

    def signed(name, text, value, lean, **numbers):
        """A signed factor's value in words, lean and answer, the answer's side from the value's sign."""
        return text, lean, leaning_answer(name, lean, value > 0, **numbers)

    def share(name, text, portion):
        """A share factor's value in words, lean (the share itself) and answer, the side from the share."""
        return text, portion, leaning_answer(name, portion, portion is not None and portion > components.EVEN_CHANCE)

    return [
        build_factor(components.PRICE_TREND,
                      [row("move_points", since="prior_close"), row("move_points", since="open")],
                      lambda since_close, since_open: signed(
                          components.PRICE_TREND, f"{since_close:+.0f} Since Close", since_close,
                          squashed_lean(since_close, scale[components.PRICE_TREND]), points=f"{abs(since_close):.0f}")),
        straightness_factor(path_moves[components.STRAIGHTNESS_WINDOW_MINUTES], row("spx_price_points")),
        build_factor(components.BREADTH, [row("nyse_advancing_portion")],
                      lambda portion: (f"{portion:.0%} Advancing", portion,
                                       components.PUSH_PULL_ANSWER_WORDS[components.BREADTH][0].format(
                                           share=f"{portion:.0%}"))),
        build_factor(components.BIG_TECH_VS_REST,
                      [row("fund_change_from_prior_close_pct", symbol=components.BIG_TECH_SYMBOL),
                       row("fund_change_from_prior_close_pct", symbol=components.REST_SYMBOL)],
                      lambda big_tech, rest: signed(
                          components.BIG_TECH_VS_REST,
                          f"{components.BIG_TECH_SYMBOL} {big_tech - rest:+.1f}% vs {components.REST_SYMBOL}",
                          big_tech - rest, squashed_lean(big_tech - rest, scale[components.BIG_TECH_VS_REST]))),
        build_factor(components.OPTIONS_BUYERS, [row("bullish_portion_of_sided_premium")],
                      lambda bullish: share(components.OPTIONS_BUYERS, f"{bullish:.0%} Bullish", bullish)),
        build_factor(components.YIELD_CHANGE, [row("ten_year_yield_change_basis_points")],
                      lambda change: signed(components.YIELD_CHANGE, f"{change:+.1f} bp", change,
                                            squashed_lean(change, scale[components.YIELD_CHANGE], yield_leans_up))),
        build_factor(components.MIRAI_CLAUDE, [row("mirai_claude_forecast_pct", horizon=components.CLAUDE_LEAN_HORIZON,
                                                    side=side) for side in components.CLAUDE_SIDES],
                      lambda up, flat, down: share(components.MIRAI_CLAUDE,
                                                   f"{up / (up + down):.0%} Up" if up + down else "No Side",
                                                   up / (up + down) if up + down else None)),
    ]


def straightness_factor(path, price_row):
    """The 30-minute straightness as a lean signed by the net move, answered steady, drifting (up or down) or choppy."""
    if path is None:
        reason = price_row["blank_reason"] if price_row is not None and price_row["blank_reason"] \
            else registry.NOT_FORMED_REASON
        return factor_entry(components.STRAIGHTNESS, None, None, None, reason, price_row)
    net = path["up"] - path["down"]
    if path["straightness"] is None:
        return factor_entry(components.STRAIGHTNESS, None, None, None, "no_activity", price_row)
    lean = tilted_lean(path["straightness"] * (1 if net > 0 else -1 if net < 0 else 0))
    up, down, choppy, drift_up, drift_down = components.PUSH_PULL_ANSWER_WORDS[components.STRAIGHTNESS]
    if abs(lean - 0.5) <= components.EVEN_LEAN_ZONE:
        answer = choppy
    elif path["straightness"] >= components.STEADY_STRAIGHTNESS:
        answer = up if net > 0 else down
    else:
        answer = drift_up if net > 0 else drift_down
    return factor_entry(components.STRAIGHTNESS, f"{path['straightness']:.2f} Straight, Net {net:+.1f}", answer, lean,
                        None, price_row)


def combined_lean(leans, weights):
    """Even plus the sum of weight times (lean less even) over the leans present; the one formula of the call."""
    return components.EVEN_CHANCE + sum(weights.get(name, 0.0) * (lean - components.EVEN_CHANCE)
                                        for name, lean in leans.items() if lean is not None)


def direction(factors):
    """One channel's direction call: every factor with a lean at an equal share, the shares summing to 1."""
    # The shares actually applied come back beside it; with no lean at all the call is even at weight 0.
    present = {entry["name"]: entry["lean"] for entry in factors if entry["lean"] is not None}
    applied = {name: 1 / len(present) for name in present}
    total = 1.0 if present else 0.0
    chance = combined_lean(present, applied)
    return {"chance_up_if_moves": chance, "weight_applied": total, "weights": applied,
            "text": direction_text(chance, total)}


def direction_text(chance, weight_applied):
    """The call in words: which way and how strongly, even when the pushes and pulls cancel or no factor has a lean."""
    if weight_applied == 0:
        return components.DIRECTION_TEXT_NO_FACTORS
    if round(chance, 2) == components.EVEN_CHANCE:
        return "even"
    return f"up {chance:.0%}" if chance > components.EVEN_CHANCE else f"down {1 - chance:.0%}"


def apply_push_pull(forecast, factors):
    """Put the factors on the forecast and call the direction per channel from them."""
    # Each entry gains its share per channel; the headline channel's call goes on top of the forecast.
    forecast["push_pull"] = factors
    for channel in forecast["channels"].values():
        channel["direction"] = direction(factors)
    for entry in factors:
        entry["weight_by_channel"] = {name: channel["direction"]["weights"].get(entry["name"], 0.0)
                                      for name, channel in forecast["channels"].items()}
    headline = forecast["channels"][components.HEADLINE_HORIZON]["direction"]
    forecast.update({"chance_up_if_moves": headline["chance_up_if_moves"],
                     "weight_applied": headline["weight_applied"], "direction_text": headline["text"]})


def lean_rows(snapshot, factors):
    """The push/pull factors as observations on the read's snapshot: each lean and its value in words."""
    # Stamped with the input's feed, blank with the input's reason.
    rows = []
    for entry in factors:
        feed = entry["feed"] or {}
        subject = registry.subject_key(factor=entry["name"])
        rows.append(store.observation_row(snapshot, "push_pull_lean_probability", store.rounded(entry["lean"]),
                                          subject, blank_reason=entry["reason"], **feed))
        rows.append(store.observation_row(snapshot, "push_pull_factor_text", entry["value"], subject,
                                          blank_reason=entry["reason"], **feed))
    return rows
