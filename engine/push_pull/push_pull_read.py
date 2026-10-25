# The push/pull pipeline for one read: every reading in reading_specs run through its force (the raw value), its
# same-clock baseline (z), its shape (strength), its earned weight and its context booster into points on each channel
# (strength × weight × booster × swing); the group sums under their caps; the net push; the odds of up, flat and down
# on the average grade; the wind and where the band centres move. Only forces add points, the wider view only sizes
# them, and how far price travels is the range engine's band, never a reading here. Everything lands on the forecast
# and every step is stored: raw, z and strength as observations on the read, weight, booster, points, net, odds, wind
# and centres as values on each channel's decision.
from engine.push_pull import context_boosters as ctx, earned_weights, net_and_odds, reading_specs, strength_shapes
from engine.push_pull import same_clock_baseline as baseline
from engine.push_pull.read_facts import ReadFacts, Reading
from engine.store import components, registry, store, store_queries

RAW_MEASURE, Z_MEASURE, STRENGTH_MEASURE = "push_pull_raw_value", "push_pull_z_score", "push_pull_strength"
NO_READINGS_TEXT = "even, no reading counts yet"
# The reading the phone may move between reads with the live price: the trend since the true open.
LIVE_READING = "trend_since_open"


def read_forces(conn, snapshot, observations, forecast, cut_at, half_days):
    """Run every reading for a read and put the push/pull block on the forecast; returns the block."""
    facts = ReadFacts(conn, snapshot, observations, forecast, cut_at, half_days)
    raw_history = baseline.history(conn, facts.trade_date, cut_at, RAW_MEASURE)
    context = ctx.context_of(facts, baseline.history(conn, facts.trade_date, cut_at, ctx.CONTEXT_MEASURE))
    ledger = store_queries.push_pull_ledger_known(conn, facts.trade_date, facts.cut_iso)
    readings = [reading_entry(spec, facts, raw_history, context, ledger.get(spec.weight_name))
                for spec in reading_specs.SPECS]
    promoted_groups = {spec.group for spec in reading_specs.SPECS
                       if (ledger.get(spec.weight_name) or {}).get("promoted")}
    channels = {name: channel_block(readings, channel, name, forecast["price"], promoted_groups)
                for name, channel in forecast["channels"].items()}
    return place_on_forecast(forecast, {"readings": readings, "context": context, "channels": channels})


def empty_forces(forecast):
    """The block a read carries when the push/pull step failed: no readings, every channel even at weight 0."""
    channels = {name: channel_block([], channel, name, forecast["price"], set())
                for name, channel in forecast["channels"].items()}
    return place_on_forecast(forecast, {"readings": [], "context": {}, "channels": channels})


def place_on_forecast(forecast, block):
    """Put the block on the forecast, each channel's call beside its bands and the headline's on top."""
    forecast["push_pull"] = block
    for name, channel in forecast["channels"].items():
        call = block["channels"][name]
        channel["direction"] = {key: call[key] for key in ("chance_up_if_moves", "weight_applied", "text")}
    headline = block["channels"][components.HEADLINE_HORIZON]
    forecast.update({"chance_up_if_moves": headline["chance_up_if_moves"],
                     "weight_applied": headline["weight_applied"], "direction_text": headline["text"]})
    return block


def reading_entry(spec, facts, raw_history, context, ledger_entry):
    """One reading through every step at this read: what it measured, how unusual, how strong, how much it counts."""
    speaks = spec.speaks.is_open(facts.minute, facts.session_length, facts.half_day)
    reading = spec.measure(facts) if speaks else Reading.blank_for(registry.NOT_APPLICABLE)
    history = raw_history.get(registry.subject_key(factor=spec.name), [])
    usual, spread, sessions = baseline.baseline(history, spec.usual, spec.half_life_sessions, spec.spread_floor,
                                                spec.gated)
    z = baseline.z_score(reading.value, usual, spread)
    measured = reading.value is not None
    z_blank = reading.blank if not measured else baseline.TOO_FEW_SESSIONS if z is None else None
    sized = measured and (z is not None or spec.shape == strength_shapes.CATCH_UP)
    strength = strength_shapes.strength(spec.shape, z, reading.side, reading.value) if sized else None
    weight = earned_weights.weight_for(spec, context.get(ctx.TREND_NESS), ledger_entry)
    booster = spec.fixed_booster(facts) if spec.fixed_booster else ctx.booster(spec.boosters, context)
    points = {name: net_and_odds.points_of(spec, strength, weight, booster, channel["swing"])
              for name, channel in facts.forecast["channels"].items()}
    entry = {
        "name": spec.name, "label": spec.label, "group": spec.group, "status": spec.status,
        "in_the_net": spec.in_the_net, "speaks": speaks, "raw": reading.value, "raw_blank": reading.blank,
        "raw_side": reading.side, "unit": spec.unit, "usual": usual, "spread": spread, "sessions": sessions,
        "z": z, "z_blank": z_blank, "strength": strength, "weight": weight, "booster": booster,
        "points_by_channel": points, "side": side_of(points.get(components.HEADLINE_HORIZON)), "live": None,
    }
    if spec.name == LIVE_READING and strength is not None and facts.true_open is not None:
        entry["live"] = {"follows": registry.LIVE_PRICE_FEED, "since_price": facts.true_open, "usual": usual,
                         "spread": spread, "weight": weight, "booster": booster, "shape": spec.shape,
                         "wiggle": strength_shapes.WIGGLE}
    return entry


def side_of(points):
    """Which way a reading's points go: push up, pull down, even at 0, None without points."""
    if points is None:
        return None
    return components.PUSH_SIDE if points > 0 else components.PULL_SIDE if points < 0 else components.EVEN_SIDE


def channel_block(readings, channel, name, price, promoted_groups):
    """One channel's net and outputs from the readings' points on it."""
    swing = channel["swing"]
    entries = [{"group": entry["group"], "points": entry["points_by_channel"].get(name),
                "in_the_net": entry["in_the_net"]} for entry in readings]
    counted = [entry for entry in entries if entry["in_the_net"] and entry["points"] is not None]
    net, groups, shares = net_and_odds.net_of(entries, swing, promoted_groups) if counted and swing else (None, {}, {})
    odds = net_and_odds.odds(net, swing, channel["average_stretch"], channel["stretch"], channel["rung_points"]) \
        if net is not None else None
    return {
        "net": net, "groups": groups, "group_shares": shares, "odds": odds,
        "chance_up_if_moves": net_and_odds.chance_up_if_moves(odds), "weight_applied": 1.0 if counted else 0.0,
        "text": call_text(odds, counted), "wind": net_and_odds.wind(net, swing),
        "centres": net_and_odds.centres(price, net, channel["minutes"]),
    }


def call_text(odds, counted):
    """The call in words: up or down with its chance of the average ending that side, even when nothing leans."""
    if not counted:
        return NO_READINGS_TEXT
    if odds is None:
        return "even, no fit for the odds yet"
    up, down = odds["up"], odds["down"]
    if abs(up - down) < 0.005:
        return "even"
    return f"up {up:.0%}" if up > down else f"down {down:.0%}"


# ---- Stored rows -------------------------------------------------------------------------------------------------------

def observation_rows(snapshot, block, stamp):
    """Each reading's raw value, z and strength, and each context input, as observations on the read's snapshot."""
    rows = []
    add = store.row_adder(store.observation_row, snapshot, rows)
    for entry in block["readings"]:
        subject = registry.subject_key(factor=entry["name"])
        add(RAW_MEASURE, entry["raw"], subject, entry["raw_blank"], **stamp)
        add(Z_MEASURE, entry["z"], subject, entry["z_blank"], **stamp)
        add(STRENGTH_MEASURE, entry["strength"], subject, entry["z_blank"], **stamp)
    for kind, value in block["context"].items():
        add(ctx.CONTEXT_MEASURE, value, registry.subject_key(kind=kind), registry.NOT_FORMED_REASON, **stamp)
    return rows


def value_rows(decision, block, name):
    """One channel's push/pull values on its decision: per reading the weight, booster and points; the group sums,
    the net, the odds, the wind, the centres and the chance of up if price moves."""
    rows = []
    call = block["channels"][name]
    add = store.row_adder(store.decision_value_row, decision, rows)
    not_formed = registry.NOT_FORMED_REASON
    for entry in block["readings"]:
        subject = registry.subject_key(factor=entry["name"])
        add("push_pull_weight", entry["weight"], subject)
        add("push_pull_booster", entry["booster"], subject)
        add("push_pull_points", entry["points_by_channel"].get(name), subject, entry["z_blank"] or not_formed)
    for group, total in call["groups"].items():
        add("push_pull_group_points", total, registry.subject_key(kind=reading_specs.GROUPS[group][0]))
    add("push_pull_net_points", call["net"], blank=not_formed)
    no_odds = registry.TOO_FEW_SESSIONS if call["net"] is not None else not_formed
    for side in ("up", "flat", "down"):
        add("push_pull_odds_probability", call["odds"][side] if call["odds"] else None,
            registry.subject_key(side=side), no_odds)
    add("push_pull_wind_ratio", call["wind"]["ratio"] if call["wind"] else None, blank=not_formed)
    for band, centre in (call["centres"] or {}).items():
        add("push_pull_centre_points", centre, registry.subject_key(band=band, horizon=name))
    add("chance_up_if_moves_probability", call["chance_up_if_moves"], blank=no_odds,
        trust_weight=call["weight_applied"])
    return rows
