# Writes the small JSON file the phone reads after every live read: the read's status and values, each feed's status
# (live, partial or blind) with its age, newest input, cadence and any note (the live price's row first), the Schwab
# login, the session's clock, the nightly's standing, the range block (the phone's part of the forecast) and the
# windows block (the live window per channel with its read's own chain, the earlier windows still running, today's
# finished windows with their grades, the score). The keys written here are the phone's whole contract
# (docs/data_map.md, "The phone feed"); a failed job writes a failed document so the phone never keeps an old file in
# silence.
import json
import os
from datetime import date, datetime, timedelta

from engine import paths, session_clock
from engine.collect import mirai_files
from engine.grade import grade
from engine.range import range_fit
from engine.store import components, registry, store, store_queries

PHONE_WORD_FOR_FEED_STATUS = {"ok": "live", "late": "partial", "stale": "partial", "missing": "blind"}
PHONE_LIVE, PHONE_PARTIAL, PHONE_BLIND = "live", "partial", "blind"
CALENDAR_FEED = "event_calendar"
LOGIN_FIELD_SHORT_NAMES = {"login_state": "state", "login_hours_until_expiry": "hours_left",
                           "login_expires_at": "expires_at"}
WINDOWS, CURRENT, RUNNING, FINISHED, SCORE = "windows", "current", "running", "finished", "score"
FAILED = "failed"
# The forecast keys the phone reads, at the top of the range block, per channel and per push/pull factor.
RANGE_KEYS = ("price", "read_at", "minutes_left", "speed", "speed_fast", "speed_slow", "straddle", "releases",
              "fed_decision_at")
CHANNEL_KEYS = ("swing", "stretch", "average_stretch", "fit_sessions", "fit_windows", "rung_points",
                "stays_inside_share", "options_swing", "options_check", "clipped", "ends_at", "dials",
                "dials_product", "bands", "status", "reason")
DIRECTION_KEYS = ("chance_up_if_moves", "weight_applied", "text")
PUSH_PULL_KEYS = ("name", "value", "answer", "lean", "side", "reason", "weight_by_channel", "points_by_channel",
                  "live")
# The finished-window facts the Read page draws, as the window's outcome values name them.
FINISHED_FACTS = {"price": "grade_start_price_points", "end": registry.WINDOW_PRICE_MEASURE["end"],
                  "high": registry.WINDOW_PRICE_MEASURE["high"], "low": registry.WINDOW_PRICE_MEASURE["low"],
                  "end_move": registry.WINDOW_MOVE_MEASURE["end"]}


def for_phone(value):
    """A value, dict or list made phone-ready: floats at the store's precision, moments on the New York clock."""
    if isinstance(value, dict):
        return {key: for_phone(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [for_phone(inner) for inner in value]
    if isinstance(value, datetime):
        return session_clock.iso_eastern(value)
    return store.rounded(value)


def write_document(document):
    """Write latest.json atomically; returns the file path."""
    path = paths.live_feed_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=1))
    os.replace(temporary, path)
    return path


def session_block(day, half_days):
    """The session's clock for the phone: open, close, the closing dial's start and the next open."""
    # So the phone can tell closed from no read today. Past the holiday list's horizon the clock cannot answer for the
    # day or the next; all are left null rather than crashing the feed, so a failed read past the horizon still
    # reaches the phone as a failed document.
    opens_at, closes_at, next_open = None, None, None
    try:
        if session_clock.is_trading_day(day):
            opens_at, closes_at = session_clock.session_open(day), session_clock.session_close(day, half_days)
        next_open = session_clock.session_open(session_clock.next_trading_day(day))
    except ValueError:
        pass
    closing_from = closes_at - timedelta(minutes=components.CLOSING_WINDOW_MINUTES) if closes_at else None
    return for_phone({"opens_at": opens_at, "closes_at": closes_at, "closing_window_from": closing_from,
                      "next_session_opens_at": next_open})


def nightly_block(done_for, day):
    """The nightly's standing at a read: the session it last finished and the one it should have finished by now."""
    expected = session_clock.latest_trading_day(day - timedelta(days=1))
    return {"done_for": done_for, "expected": expected.isoformat()}


def horizon_warnings(calendar, day):
    """The plain phrases for a calendar or holiday list about to run out (or run out) on a day, else an empty list."""
    phrases = []
    if calendar is not None:
        covers_through = mirai_files.calendar_covers_through(calendar)
        days_left = (covers_through - day).days
        if days_left < 0:
            phrases.append(f"Mirai's event calendar ended {covers_through}: events, releases and half days unknown")
        elif days_left <= mirai_files.CALENDAR_WARNING_DAYS:
            phrases.append(f"Mirai's event calendar covers through {covers_through} ({days_left} days left)")
    holidays = session_clock.holiday_horizon_warning(day)
    if holidays:
        phrases.append(holidays)
    return phrases


def calendar_feed_status(calendar, day, collected_status):
    """The calendar feed's phone status and note on a day: blind past the calendar's horizon, partial near either."""
    if calendar is None:
        return collected_status, None
    covers_through = mirai_files.calendar_covers_through(calendar)
    days_left = (covers_through - day).days
    notes, status = [], collected_status
    if days_left < 0:
        status, notes = PHONE_BLIND, [f"Calendar Ended {covers_through}"]
    elif days_left <= mirai_files.CALENDAR_WARNING_DAYS:
        status = PHONE_PARTIAL if status == PHONE_LIVE else status
        notes.append(f"Calendar Ends {covers_through}")
    if session_clock.holiday_horizon_warning(day):
        status = PHONE_PARTIAL if status == PHONE_LIVE else status
        notes.append(f"Holiday List Ends {session_clock.HOLIDAYS_LISTED_THROUGH}")
    return status, " · ".join(notes) or None


def feed_entry(name, state):
    """One feed's phone row: status word, age, note, the moment of its newest input and how often it lands."""
    as_of = state["market_at"]
    return {"status": PHONE_WORD_FOR_FEED_STATUS[state["status"]], "age_s": state["age"], "note": None,
            "as_of": session_clock.iso_eastern(session_clock.parse_iso(as_of)) if as_of else None,
            "cadence": registry.feed_cadence(name)}


def live_price_entry(live_price):
    """The live price's phone row as the feed is written: live inside its stale limit, partial past it, blind absent."""
    entry = {"status": PHONE_BLIND, "age_s": None, "note": None, "as_of": None,
             "cadence": registry.feed_cadence(registry.LIVE_PRICE_FEED)}
    if live_price is not None:
        fresh = live_price["age_s"] <= registry.feed_stale_limit_seconds(registry.LIVE_PRICE_FEED)
        entry.update(status=PHONE_LIVE if fresh else PHONE_PARTIAL, age_s=live_price["age_s"],
                     as_of=session_clock.iso_eastern(live_price["at"]))
    return entry


def feeds_block(feed_states, calendar, day, live_price):
    """Each feed's phone row, the live price first; the calendar's as_of is its last covered day, with horizon notes."""
    feeds = {registry.LIVE_PRICE_FEED: live_price_entry(live_price)}
    feeds.update((name, feed_entry(name, state)) for name, state in feed_states.items())
    if CALENDAR_FEED in feeds:
        status, note = calendar_feed_status(calendar, day, feeds[CALENDAR_FEED]["status"])
        feeds[CALENDAR_FEED].update(status=status, note=note)
        if calendar is not None:
            feeds[CALENDAR_FEED]["as_of"] = mirai_files.calendar_covers_through(calendar).isoformat()
    return feeds


def range_block(forecast):
    """The phone's part of the forecast: the keys the screens read, nothing the engine keeps for itself."""
    if forecast is None:
        return None
    block = {key: forecast[key] for key in RANGE_KEYS}
    block["band_portion"] = components.BAND_PORTION
    block["push_pull"] = [{"label": components.PUSH_PULL_FACTOR_LABEL[entry["name"]],
                           **{key: entry[key] for key in PUSH_PULL_KEYS}} for entry in forecast["push_pull"]]
    block["wind"] = {"levels": components.WIND_LEVELS, "dot_steps": components.WIND_DOT_STEPS}
    block["channels"] = {}
    for name, channel in forecast["channels"].items():
        picked = {key: channel[key] for key in CHANNEL_KEYS}
        picked["direction"] = {key: channel["direction"][key] for key in DIRECTION_KEYS}
        block["channels"][name] = picked
    if WINDOWS in forecast:
        block[WINDOWS] = forecast[WINDOWS]
    return for_phone(block)


def write_live_feed(snapshot, observations, feed_states, forecast, calendar, half_days, nightly_done):
    """Write latest.json from a read's observations, feed states and forecast (None when the range step failed)."""
    values = store.values_by_key(observations)
    login = {short: values.pop(f"{name}|login=schwab", None) for name, short in LOGIN_FIELD_SHORT_NAMES.items()}
    day = date.fromisoformat(snapshot["trade_date"])
    return write_document({
        "status": snapshot["snapshot_status"], "status_reason": snapshot["snapshot_status_reason"],
        "cut_at": session_clock.iso_eastern(session_clock.parse_iso(snapshot["cut_at"])),
        "feeds": feeds_block(feed_states, calendar, day, mirai_files.read_live_price()), "login": login,
        "session": session_block(day, half_days), "nightly": nightly_block(nightly_done, day),
        "range": range_block(forecast), "values": values,
    })


def write_failed_feed(reason, half_days):
    """Write a latest.json that says the live read failed, with the reason, the moment and the session's clock."""
    now = session_clock.now_utc()
    return write_document({"status": FAILED, "status_reason": reason, "failed_at": session_clock.iso_eastern(now),
                           "session": session_block(session_clock.to_eastern(now).date(), half_days)})


def failed_windows_block(failure):
    """The windows block when the grading step crashed: the phone sees the failure, not an empty page."""
    return {"status": FAILED, "reason": f"{type(failure).__name__}: {failure}"}


# ---- The windows block: per channel the live window, the earlier windows still running, today's finished ones ----

def windows_block(conn, day, cut_at, known_by_iso):
    """The windows block of the phone's range feed, built from the store after the read and its grades are written."""
    trade_date, cut_iso = day.isoformat(), session_clock.iso_utc(cut_at)
    bars = store_queries.session_bars(conn, trade_date, cut_iso, known_by_iso)
    series = range_fit.minute_series(bars, trade_date)
    live = bars[-1]["close"] if bars else None
    current, running, finished = {}, {}, {}
    for name, minutes in components.HORIZON_MINUTES.items():
        lane = lane_for_channel(minutes)
        read = store_queries.newest_live_read(conn, trade_date, cut_iso, lane)
        current[name] = live_window(conn, day, cut_at, name, read, series, live)
        running[name] = running_windows(conn, day, cut_iso, name, lane, read, series)
        finished[name] = finished_windows(conn, trade_date, name, lane)
    return {CURRENT: current, RUNNING: running, FINISHED: finished,
            SCORE: {name: score_counts(entries) for name, entries in finished.items()}}


def lane_for_channel(minutes):
    """The lane a channel follows: the headline lane (:05 and :35 reads) from 30 minutes up, every read for 5."""
    return session_clock.LANE_HEADLINE if minutes >= session_clock.READ_LANES[session_clock.LANE_HEADLINE] else None


def facts_so_far(decision, day, series):
    """A window's bars held so far, measured the grade's way from its first minute to the newest bar, or None."""
    first, last = grade.window_minute_span(decision, day)
    last_held = min(last, series[0].size - 1)
    return range_fit.window_facts(*series, first, last_held) if last_held >= first else None


def live_window(conn, day, cut_at, channel, read, series, live):
    """The channel's newest window at the cut: its read, bands, the bars held so far measured the grade's way."""
    # With the moves from the read price and whether the running average sits inside its band; ended once the cut has
    # passed the window's end.
    cut_iso = session_clock.iso_utc(cut_at)
    decisions = store_queries.decisions_of_read(conn, read["snapshot_id"], components.RANGE_DECISION_KIND, channel) \
        if read else ()
    if not decisions:
        return None
    base = decisions[0]
    bands = grade.decision_bands(conn, base)[0]
    price = store_queries.read_price_and_bar(conn, read["snapshot_rowid"])[0]
    facts = facts_so_far(base, day, series)
    moves = grade.signed_moves(price, facts)
    rung = components.RUNG_POINTS[channel]
    move = None if live is None or price is None else live - price
    return {
        "read_at": session_clock.parse_iso(read["cut_at"]), "price": price,
        "chain": read_chain(conn, read, base, channel, rung),
        "window_from": session_clock.parse_iso(base["grade_window_starts_at"]),
        "ends_at": session_clock.parse_iso(base["grade_window_ends_at"]),
        "ended": cut_iso >= base["grade_window_ends_at"],
        "clipped": base["decision_status"] == registry.DECISION_CLIPPED, "status": base["decision_status"],
        "reason": base["decision_status_reason"], "rung_points": rung, "bands": bands,
        "minutes_held": facts["minutes"] if facts else 0,
        "now": {"move": move, "average_move": moves and moves["average"], **farthest_moves(moves)},
        "average_inside": inside_half(moves and moves["average"], bands[components.AVERAGE_BAND]["half_width"]),
    }


def read_chain(conn, read, decision, channel, rung):
    """How the live window's own read worked its band out, read back from that read's stored values."""
    # So Sight and Steps explain the same read the Read page shows: speeds, swing, stretches, fit, rung share, each
    # dial and their product, the options check and the direction call.
    rows = store_queries.decision_values(conn, decision["decision_rowid"])
    values = store.values_by_key(rows)
    seen = store.values_by_key(store_queries.snapshot_observations(conn, read["snapshot_rowid"]))
    horizon = registry.subject_key(horizon=channel)
    dials = {dial: {} for dial in components.WIDTH_DIALS + (components.ALL_DIALS,)}
    for row in rows:
        if row["measure_name"] == "width_multiplier":
            dial = dials[registry.subject_parts(row["subject_key"])["adjustment"]]
            dial[row["value_role"]] = row["value_number"]
            if row["value_role"] == registry.APPLIED_ROLE:
                dial["weight"] = row["trust_weight_applied_portion"]
    dials_product = dials.pop(components.ALL_DIALS)
    direction = next((row for row in rows if row["measure_name"] == "chance_up_if_moves_probability"), None)
    end_band = registry.subject_key(band=components.BAND, horizon=channel)
    average_band = registry.subject_key(band=components.AVERAGE_BAND, horizon=channel)
    return {
        "speed": values.get("today_typical_1_minute_move_points"),
        "speed_fast": seen.get("speed_fast_5_minute_half_life_points"),
        "speed_slow": seen.get("speed_slow_30_minute_half_life_points"),
        "swing": values.get(store.value_key_for("typical_swing_points", horizon)),
        "stretch": values.get(store.value_key_for("stretch_multiplier", end_band)),
        "average_stretch": values.get(store.value_key_for("stretch_multiplier", average_band)),
        "fit_sessions": values.get(store.value_key_for("fit_sessions_count", horizon)),
        "fit_windows": values.get(store.value_key_for("fit_windows_count", horizon)),
        "stays_inside_share": values.get(store.value_key_for(
            "chance_stays_inside_rung_probability", registry.subject_key(horizon=channel, rung_points=rung))),
        "dials": {dial: {registry.RAW_ROLE: setting.get(registry.RAW_ROLE),
                         registry.APPLIED_ROLE: setting.get(registry.APPLIED_ROLE), "weight": setting.get("weight")}
                  for dial, setting in dials.items()},
        "dials_product": {registry.RAW_ROLE: dials_product.get(registry.RAW_ROLE),
                          registry.APPLIED_ROLE: dials_product.get(registry.APPLIED_ROLE)},
        "straddle": seen.get("same_day_straddle_mid_points"),
        "minutes_left": seen.get("minutes_until_session_close"),
        "options_swing": seen.get(store.value_key_for("options_implied_swing_points", horizon)),
        "options_check": values.get(store.value_key_for("options_vs_tape_check_word", horizon)),
        "chance_up_if_moves": direction and direction["value_number"],
        "weight_applied": direction and direction["trust_weight_applied_portion"],
    }


def farthest_moves(moves):
    """How far price travelled above and below the read price, floored at 0 (a window that only fell went up 0)."""
    if moves is None:
        return {"farthest_up": None, "farthest_down": None}
    return {"farthest_up": max(0.0, moves["high"]), "farthest_down": max(0.0, -moves["low"])}


def inside_half(move, half_width):
    """True when a move sits inside a half-width; None when either is missing."""
    return None if move is None or half_width is None else abs(move) <= half_width


def running_windows(conn, day, cut_iso, channel, read_lane, live_read, series):
    """The channel's earlier windows still running at the cut, newest first, measured so far; no grades yet."""
    entries = []
    for decision in store_queries.running_decisions(conn, day.isoformat(), cut_iso, channel, read_lane,
                                                    components.RANGE_DECISION_KIND):
        if decision["snapshot_id"] == live_read["snapshot_id"]:
            continue
        bands = grade.decision_bands(conn, decision)[0]
        price = store_queries.read_price_and_bar(conn, decision["snapshot_rowid"])[0]
        facts = facts_so_far(decision, day, series)
        moves = grade.signed_moves(price, facts)
        entries.append({
            "read_at": session_clock.parse_iso(decision["cut_at"]), "price": price,
            "window_from": session_clock.parse_iso(decision["grade_window_starts_at"]),
            "ends_at": session_clock.parse_iso(decision["grade_window_ends_at"]),
            "half_width": bands[components.BAND]["half_width"], "bands": bands,
            "minutes_held": facts["minutes"] if facts else 0,
            **{side: facts and facts[side] for side in ("end", "high", "low")},
            "end_move": moves and moves["end"], **farthest_moves(moves),
        })
    return entries


def finished_windows(conn, trade_date, channel, read_lane):
    """Today's closed windows of a channel from the lane it follows, newest first, each with its base grade."""
    entries = []
    rung = registry.subject_key(rung_points=components.RUNG_POINTS[channel])
    for window in store_queries.lane_windows_with_grades(conn, trade_date, channel, read_lane, components.GRADE_RULE):
        facts = store.values_by_key(store_queries.outcome_values(conn, window["outcome_rowid"]))
        graded = store.values_by_key(store_queries.outcome_values(conn, window["grade_rowid"])) \
            if window["grade_rowid"] else {}
        bands = grade.decision_bands(conn, {"decision_rowid": window["graded_decision_rowid"]})[0] \
            if window["graded_decision_rowid"] else None
        moves = {side: facts.get(registry.WINDOW_MOVE_MEASURE[side]) for side in registry.WINDOW_SIDES}
        entries.append({
            "read_at": session_clock.parse_iso(window["read_cut_at"]),
            "window_from": session_clock.parse_iso(window["window_starts_at"]),
            "ends_at": session_clock.parse_iso(window["window_ends_at"]), "status": window["label_status"],
            "half_width": bands and bands[components.BAND]["half_width"],
            **{key: facts.get(measure) for key, measure in FINISHED_FACTS.items()},
            **farthest_moves(moves if moves["high"] is not None else None),
            "grades": {word: grade_word_value(graded, word, rung) for word in registry.GRADE_WORDS},
        })
    return entries


def grade_word_value(graded, word, rung_subject):
    """A grade word as True / False from the grade's values (None when ungraded), read the registry's one way."""
    measure_name, band, yes = registry.GRADE_WORD_MEASURE[word]
    subject = registry.subject_key(band=band) if band else rung_subject
    stored = graded.get(store.value_key_for(measure_name, subject))
    return None if stored is None else stored == yes


def score_counts(finished):
    """A channel's score over its finished list: per grade word, how many said yes and how many were graded."""
    return {word: {"yes": sum(1 for entry in finished if entry["grades"][word] is True),
                   "graded": sum(1 for entry in finished if entry["grades"][word] is not None)}
            for word in registry.GRADE_WORDS}
