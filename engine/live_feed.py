# Writes the small JSON file the phone reads after every live read: the snapshot's values, each feed's status (live,
# partial or blind) and age, the Schwab login expiry, the range forecast copied whole and the windows block (the live
# window per channel, today's finished windows with their grades, the score); a failed job writes a failed document so
# the phone never keeps an old file in silence. This file is the phone's whole contract with the store.
import json
import os
from datetime import datetime

from engine import grade, paths, range_fit, range_read, registry, session_clock, store

PHONE_WORD_FOR_FEED_STATUS = {"ok": "live", "late": "partial", "stale": "partial", "frozen_feed": "partial",
                              "stand_in": "partial", "missing": "blind", "market_closed": "blind"}
LOGIN_FIELD_SHORT_NAMES = {"login_state": "state", "login_hours_until_expiry": "hours_left",
                           "login_expires_at": "expires_at"}
WINDOWS, CURRENT, FINISHED, SCORE = "windows", "current", "finished", "score"
FAILED = "failed"
ENDS_INSIDE, AVERAGE_INSIDE, NEVER_LEFT = registry.GRADE_WORDS


def split_values_and_blanks(observations):
    """{key: value} and {key: blank reason} over a read's observations, keyed the store's one way."""
    values, blanks = {}, {}
    for observation in observations:
        value = store.observation_value(observation)
        if value is None:
            blanks[store.value_key(observation)] = observation["blank_reason"]
        else:
            values[store.value_key(observation)] = value
    return values, blanks


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


def write_live_feed(snapshot, observations, feed_states, forecast):
    """Write latest.json from a snapshot's observations, feed states and forecast (None when the range step failed)."""
    values, blanks = split_values_and_blanks(observations)
    login = {short: values.pop(f"{name}|login=schwab", None) for name, short in LOGIN_FIELD_SHORT_NAMES.items()}
    return write_document({
        "read_id": snapshot["read_id"], "lane": snapshot["read_lane"], "slot": snapshot["scheduled_slot_eastern"],
        "origin": snapshot["row_origin"], "status": snapshot["snapshot_status"],
        "status_reason": snapshot["snapshot_status_reason"],
        "cut_at": session_clock.iso_eastern(session_clock.parse_iso(snapshot["cut_at"])),
        "known_at": session_clock.iso_eastern(session_clock.parse_iso(snapshot["known_at"])),
        "feeds": {name: {"status": PHONE_WORD_FOR_FEED_STATUS[state["status"]], "age_s": state["age"]}
                  for name, state in feed_states.items()},
        "login": login, "range": for_phone(forecast), "values": values, "blanks": blanks,
    })


def write_failed_feed(reason):
    """Write a latest.json that says the live read failed, with the reason and the moment."""
    return write_document({"status": FAILED, "status_reason": reason,
                           "failed_at": session_clock.iso_eastern(session_clock.now_utc())})


def failed_windows_block(failure):
    """The windows block when the grading step crashed: the phone sees the failure, not an empty page."""
    return {"status": FAILED, "reason": f"{type(failure).__name__}: {failure}"}


# ---- The windows block: the live window per channel, today's finished windows per channel, the score -------------

def windows_block(conn, day, cut_at, known_by_iso):
    """The windows block of the phone's range feed, built from the store after the read and its grades are written."""
    trade_date, cut_iso = day.isoformat(), session_clock.iso_utc(cut_at)
    bars = store.session_bars(conn, trade_date, cut_iso, known_by_iso)
    series = range_fit.minute_series(bars, trade_date)
    live = bars[-1]["close"] if bars else None
    current = {name: live_window(conn, day, cut_at, name, minutes, series, live)
               for name, minutes in registry.HORIZON_MINUTES.items()}
    finished = {name: finished_windows(conn, trade_date, name, lane_of_live_window(minutes))
                for name, minutes in registry.HORIZON_MINUTES.items()}
    return {CURRENT: current, FINISHED: finished,
            SCORE: {name: score_counts(entries) for name, entries in finished.items()}}


def lane_of_live_window(minutes):
    """The lane a channel follows: the headline lane (:05 and :35 reads) from 30 minutes up, every read for 5."""
    return session_clock.LANE_HEADLINE if minutes >= session_clock.READ_LANES[session_clock.LANE_HEADLINE] else None


def live_window(conn, day, cut_at, channel, minutes, series, live):
    """The channel's newest window at the cut: its read, bands, the bars held so far measured the grade's way, the
    moves from the read price, and the three grade words so far; ended once the cut has passed the window's end."""
    cut_iso = session_clock.iso_utc(cut_at)
    read = store.newest_live_read(conn, day.isoformat(), cut_iso, lane_of_live_window(minutes))
    decisions = store.decisions_of_read(conn, read["snapshot_id"], range_read.DECISION_KIND, channel) if read else ()
    if not decisions:
        return None
    base = decisions[0]
    bands = grade.decision_bands(conn, base)[0]
    owner_rule = grade.decision_bands(conn, decisions[1])[0] if len(decisions) > 1 else None
    price = store.read_price_and_bar(conn, read["snapshot_rowid"])[0]
    first, last = grade.window_minute_span(base, day)
    last_held = min(last, series[0].size - 1)
    facts = range_fit.window_facts(*series, first, last_held) if last_held >= first else None
    moves = grade.signed_moves(price, facts)
    rung = registry.RUNG_POINTS[channel]
    move = None if live is None or price is None else live - price
    return {
        "read_id": read["read_id"], "read_at": session_clock.parse_iso(read["cut_at"]), "price": price,
        "window_from": session_clock.parse_iso(base["grade_window_starts_at"]),
        "ends_at": session_clock.parse_iso(base["grade_window_ends_at"]), "horizon_minutes": minutes,
        "ended": cut_iso >= base["grade_window_ends_at"],
        "clipped": base["decision_status"] == registry.DECISION_CLIPPED, "status": base["decision_status"],
        "reason": base["decision_status_reason"], "rung_points": rung, "bands": bands, "owner_rule_bands": owner_rule,
        "minutes_held": facts["minutes"] if facts else 0,
        "now": {"price": live, "move": move, "average": facts and facts["average"],
                "average_move": moves and moves["average"], "high": facts and facts["high"],
                "low": facts and facts["low"], **farthest_moves(moves)},
        "so_far": {ENDS_INSIDE: inside_half(move, bands[registry.BAND]["half_width"]),
                   AVERAGE_INSIDE: inside_half(moves and moves["average"], bands[registry.AVERAGE_BAND]["half_width"]),
                   NEVER_LEFT: grade.stayed_inside(moves, rung)},
    }


def farthest_moves(moves):
    """How far price travelled above and below the read price, floored at 0 (a window that only fell went up 0)."""
    if moves is None:
        return {"farthest_up": None, "farthest_down": None}
    return {"farthest_up": max(0.0, moves["high"]), "farthest_down": max(0.0, -moves["low"])}


def inside_half(move, half_width):
    """True when a move sits inside a half-width; None when either is missing."""
    return None if move is None or half_width is None else abs(move) <= half_width


def finished_windows(conn, trade_date, channel, read_lane):
    """Today's closed windows of a channel from the lane it follows, newest first, each with its base grade."""
    entries = []
    rung = registry.subject_key(rung_points=registry.RUNG_POINTS[channel])
    for window in store.lane_windows_with_grades(conn, trade_date, channel, read_lane, registry.GRADE_RULE):
        facts = store.values_by_key(store.outcome_values(conn, window["outcome_rowid"]))
        graded = store.values_by_key(store.outcome_values(conn, window["grade_rowid"])) if window["grade_rowid"] else {}
        bands = grade.decision_bands(conn, {"decision_rowid": window["graded_decision_rowid"]})[0] \
            if window["graded_decision_rowid"] else None
        moves = {side: facts.get(registry.WINDOW_MOVE_MEASURE[side]) for side in registry.WINDOW_SIDES}
        minutes = facts.get("minutes_covered_count")
        entries.append({
            "read_id": window["read_id"], "read_at": session_clock.parse_iso(window["read_cut_at"]),
            "price": facts.get("grade_start_price_points"),
            "window_from": session_clock.parse_iso(window["window_starts_at"]),
            "ends_at": session_clock.parse_iso(window["window_ends_at"]), "clipped": bool(window["was_window_clipped"]),
            "minutes_held": None if minutes is None else int(minutes), "status": window["label_status"],
            "half_width": bands and bands[registry.BAND]["half_width"],
            "average_half_width": bands and bands[registry.AVERAGE_BAND]["half_width"],
            **{side: facts.get(registry.WINDOW_PRICE_MEASURE[side]) for side in registry.WINDOW_SIDES},
            **{f"{side}_move": moves[side] for side in registry.WINDOW_SIDES},
            **farthest_moves(moves if moves["high"] is not None else None),
            "grades": {word: grade_word_value(graded, word, rung) for word in registry.GRADE_WORDS},
            "band_used": {side: graded.get(f"band_used_ratio|{registry.subject_key(band=registry.BAND, side=side)}")
                          for side in registry.BAND_SIDES},
        })
    return entries


def grade_word_value(graded, word, rung_subject):
    """A grade word as True / False from the grade's values (None when ungraded), read the registry's one way."""
    measure_name, band, yes = registry.GRADE_WORD_MEASURE[word]
    subject = registry.subject_key(band=band) if band else rung_subject
    stored = graded.get(f"{measure_name}|{subject}")
    return None if stored is None else stored == yes


def score_counts(finished):
    """A channel's score over its finished list: per grade word, how many said yes and how many were graded."""
    return {word: {"yes": sum(1 for entry in finished if entry["grades"][word] is True),
                   "graded": sum(1 for entry in finished if entry["grades"][word] is not None)}
            for word in registry.GRADE_WORDS}
