# Outcomes and grades: closes every range decision window from the store's bars once its last bar is held, grades
# each decision against its bands and rung, builds the phone's windows block and prints the scoreboard.
import itertools

from engine import range_fit, range_read, registry, session_clock, store

WINDOWS = "windows"
CURRENT, FINISHED, SCORE = "current", "finished", "score"
ENDS_INSIDE, AVERAGE_INSIDE, NEVER_LEFT = registry.GRADE_WORDS
END_BAR_MISSING = "window_end_bar_missing"
SCOREBOARD_SESSIONS = 20
SLICE_WORDS = {0: "unclipped", 1: "clipped"}


# ---- Closing windows: one market_window outcome per read and horizon, one forecast_grade per decision ------------

def window_minutes(decision, day):
    """The session minutes a decision's window covers, first and last (12:36 to 13:06 covers minutes 186 to 215)."""
    open_at = session_clock.session_open(day)
    first = (session_clock.parse_iso(decision["grade_window_starts_at"]) - open_at).total_seconds()
    last = (session_clock.parse_iso(decision["grade_window_ends_at"]) - open_at).total_seconds()
    return int(first // 60), int(last // 60) - 1


def close_windows(conn, day, cut_at, written_at):
    """Outcome and grades for every range decision window of the day ended by the cut; returns (windows, grades)."""
    trade_date, cut_iso = day.isoformat(), session_clock.iso_utc(cut_at)
    decisions = store.decisions_with_closed_windows(conn, trade_date, cut_iso, range_read.DECISION_KIND)
    if not decisions:
        return 0, 0
    settled = cut_at >= session_clock.session_close(day, store.half_day_sessions(conn))
    closes, highs, lows = range_fit.minute_series(store.session_bars(conn, trade_date, cut_iso), trade_date)
    written_iso = session_clock.iso_utc(written_at)
    windows = grades = 0
    for (snapshot_id, _), group in itertools.groupby(decisions, key=lambda d: (d["snapshot_id"], d["horizon_name"])):
        group = list(group)
        first, last = window_minutes(group[0], day)
        facts = range_fit.window_facts(closes, highs, lows, first, last)
        if facts is None and not settled:
            continue
        start_price, start_bar_at = store.read_price_at(conn, snapshot_id)
        window, values = window_rows(group[0], start_price, start_bar_at, facts, last - first + 1, written_iso)
        outcomes = [window]
        for decision in group:
            grade, grade_values = grade_rows(conn, decision, window, start_price, start_bar_at, facts, written_iso)
            outcomes.append(grade)
            values.extend(grade_values)
        store.write_outcomes(conn, outcomes, values)
        windows += 1
        grades += len(group)
    return windows, grades


def window_status(facts, start_price, minutes_wanted):
    """The label status and reason a window's bars and read price give it."""
    if facts is None:
        return registry.LABEL_MISSING_BARS, END_BAR_MISSING
    if start_price is None:
        return registry.LABEL_UNPRICEABLE, registry.NO_READ_PRICE_REASON
    if facts["minutes"] < minutes_wanted:
        return registry.LABEL_PARTIAL_BARS, f"{facts['minutes']}_of_{minutes_wanted}_minutes_held"
    return registry.LABEL_RESOLVED, None


def signed_moves(start_price, facts):
    """End, average, high and low moves from the start price, or None when either side is missing."""
    if facts is None or start_price is None:
        return None
    return {"end": facts["end"] - start_price, "average": facts["average"] - start_price,
            "high": facts["high"] - start_price, "low": facts["low"] - start_price}


def value_adder(outcome, rows):
    """A closure that appends one rounded value row of the outcome, blank with the reason given when None."""
    def add(measure_name, value, blank=None, subject_points=None, **subject):
        rows.append(store.outcome_value_row(outcome, measure_name, store.rounded(value),
                                            registry.subject_key(**subject), subject_points=subject_points,
                                            blank_reason=blank))
    return add


def grade_start_rows(add, start_price, start_bar_at):
    """The grade start every outcome and grade carries: the read's price, its bar and the rule's name."""
    add("grade_start_price_points", start_price, registry.NOT_APPLICABLE_REASON)
    add("grade_start_bar_at", start_bar_at, registry.NOT_APPLICABLE_REASON)
    add("grade_start_rule_name", registry.GRADE_START_RULE)


def window_rows(decision, start_price, start_bar_at, facts, minutes_wanted, written_iso):
    """The market_window outcome of a decision's window with its facts, blank with the reason where unmeasurable."""
    status, reason = window_status(facts, start_price, minutes_wanted)
    window = store.outcome_row(registry.MARKET_WINDOW_KIND, decision, registry.WINDOW_RULE, written_iso, status, reason)
    rows = []
    add = value_adder(window, rows)
    moves = signed_moves(start_price, facts)
    no_bars = registry.BARS_MISSING_REASON
    no_move = no_bars if facts is None else registry.NOT_APPLICABLE_REASON
    grade_start_rows(add, start_price, start_bar_at)
    for side in ("end", "average", "high", "low"):
        add(f"{side}_price_points", facts and facts[side], no_bars)
        add(f"{side}_move_points", moves and moves[side], no_move)
    add("minutes_covered_count", facts and facts["minutes"], no_bars)
    for rung in sorted(set(registry.RUNG_POINTS.values())):
        add("did_leave_rung", left_rung(moves, rung), no_move, subject_points=float(rung), rung_points=rung)
    return window, rows


def left_rung(moves, rung):
    """True when the window's farthest excursion reached the rung on either side; None without moves."""
    return None if moves is None else max(moves["high"], -moves["low"]) >= rung


def decision_bands(conn, decision_id):
    """A decision's bands as {band: {half_width, low, high}} from its value rows, and the blank reason if unformed."""
    bands = {band: dict.fromkeys(range_read.BAND_EDGES) for band in registry.BAND_PRICE_MEASURE}
    blank = None
    for row in store.decision_values(conn, decision_id):
        subject = registry.subject_parts(row["subject_key"])
        if row["measure_name"] == "band_half_width_points":
            bands[subject["band"]]["half_width"] = row["value_number"]
            blank = blank or row["blank_reason"]
        elif row["measure_name"] == "band_low_points":
            bands[subject["band"]]["low"] = row["value_number"]
        elif row["measure_name"] == "band_high_points":
            bands[subject["band"]]["high"] = row["value_number"]
    return bands, blank


def grade_rows(conn, decision, window, start_price, start_bar_at, facts, written_iso):
    """The forecast_grade of one decision against the window: inside and miss per band, the rung, the band used."""
    bands, band_blank = decision_bands(conn, decision["decision_id"])
    status, reason = window["label_status"], window["label_status_reason"]
    if status in registry.LABELS_THAT_COUNT and band_blank is not None:
        status, reason = registry.LABEL_UNPRICEABLE, decision["decision_status_reason"]
    grade = store.outcome_row(registry.FORECAST_GRADE_KIND, decision, registry.GRADE_RULE, written_iso, status, reason,
                              judged_decision_id=decision["decision_id"], market_outcome_id=window["outcome_id"],
                              graded_price_measure=decision["graded_price_measure"])
    rows = []
    add = value_adder(grade, rows)
    moves = signed_moves(start_price, facts)
    blank = registry.BARS_MISSING_REASON if facts is None else registry.NOT_APPLICABLE_REASON if moves is None \
        else band_blank
    grade_start_rows(add, start_price, start_bar_at)
    for band, move_name in ((registry.BAND, "end"), (registry.AVERAGE_BAND, "average")):
        half = bands[band]["half_width"]
        move = moves[move_name] if moves and half is not None else None
        inside = None if move is None else abs(move) <= half
        add("was_inside_band", inside, blank, band=band)
        add("band_miss_points", None if inside is None else band_miss(move, half), blank, band=band)
    rung = registry.RUNG_POINTS[decision["horizon_name"]]
    add("did_leave_rung", None if band_blank else left_rung(moves, rung), blank, subject_points=float(rung),
        rung_points=rung)
    half = bands[registry.BAND]["half_width"]
    for side, move_name, sign in (("upper", "high", 1), ("lower", "low", -1)):
        used = max(0.0, sign * moves[move_name]) / half if moves and half else None
        add("band_used_portion", used, blank, band=registry.BAND, side=side)
    return grade, rows


def band_miss(move, half_width):
    """Points the move landed past the band's edge, signed with the move; 0 when it stayed inside."""
    if abs(move) <= half_width:
        return 0.0
    return move - half_width if move > 0 else move + half_width


# ---- The phone's windows block: the live window per channel, today's finished headline windows, the score ---------

def phone_block(conn, day, cut_at):
    """The windows block of the phone's range feed, built from the store after the read and its grades are written."""
    cut_iso = session_clock.iso_utc(cut_at)
    bars = store.session_bars(conn, day.isoformat(), cut_iso)
    current = {name: live_window(conn, day, cut_iso, name, minutes, bars)
               for name, minutes in registry.HORIZON_MINUTES.items()}
    finished = finished_windows(conn, day)
    return {CURRENT: current, FINISHED: finished, SCORE: score_counts(finished)}


def lane_followed(minutes):
    """The lane a channel's live window follows: the headline lane from 30 minutes up, else every read."""
    return session_clock.LANE_HEADLINE if minutes >= session_clock.READ_LANES[session_clock.LANE_HEADLINE] else None


def live_window(conn, day, cut_iso, channel, minutes, bars):
    """The channel's open window at the cut: its read, bands, the bars since, running average, gaps and so-far words."""
    read = store.newest_live_read(conn, day.isoformat(), cut_iso, lane_followed(minutes))
    decisions = store.decisions_of_read(conn, read["snapshot_id"], range_read.DECISION_KIND, channel) if read else ()
    if not decisions:
        return None
    base = decisions[0]
    bands = decision_bands(conn, base["decision_id"])[0]
    owner_rule = decision_bands(conn, decisions[1]["decision_id"])[0] if len(decisions) > 1 else None
    price = store.read_price_at(conn, read["snapshot_id"])[0]
    since = [bar for bar in bars if bar["priced_at"] >= base["grade_window_starts_at"]]
    live = bars[-1]["close"] if bars else None
    gap = None if live is None or price is None else live - price
    held = {"end": live, "average": sum(bar["close"] for bar in since) / len(since),
            "high": max(bar["high"] for bar in since), "low": min(bar["low"] for bar in since)} if since else None
    moves = signed_moves(price, held)
    rung = registry.RUNG_POINTS[channel]
    return {
        "read_id": read["read_id"], "read_at": session_clock.parse_iso(read["cut_at"]), "price": price,
        "window_from": session_clock.parse_iso(base["grade_window_starts_at"]),
        "ends_at": session_clock.parse_iso(base["grade_window_ends_at"]), "minutes": minutes,
        "clipped": base["decision_status"] == registry.DECISION_CLIPPED, "status": base["decision_status"],
        "reason": base["decision_status_reason"], "rung_points": rung, "bands": bands, "owner_rule_bands": owner_rule,
        "minutes_in": len(since),
        "live": {"price": live, "gap": gap, "average": held and held["average"],
                 "average_gap": moves and moves["average"], "high": held and held["high"], "low": held and held["low"],
                 "farthest_up": moves and moves["high"], "farthest_down": moves and -moves["low"]},
        "so_far": {ENDS_INSIDE: inside_half(gap, bands[registry.BAND]["half_width"]),
                   AVERAGE_INSIDE: inside_half(moves and moves["average"], bands[registry.AVERAGE_BAND]["half_width"]),
                   NEVER_LEFT: None if moves is None else not left_rung(moves, rung)},
    }


def inside_half(move, half_width):
    """True when a move sits inside a half-width; None when either is missing."""
    return None if move is None or half_width is None else abs(move) <= half_width


def finished_windows(conn, day):
    """Today's finished headline-lane windows of the headline channel, newest first, each with its base grade."""
    entries = []
    for window in store.live_windows_of_day(conn, day.isoformat(), registry.HEADLINE_HORIZON,
                                            session_clock.LANE_HEADLINE, registry.GRADE_RULE):
        facts = values_by_key(store.outcome_values(conn, window["outcome_id"]))
        grade = values_by_key(store.outcome_values(conn, window["grade_outcome_id"])) if window["grade_outcome_id"] \
            else {}
        bands = decision_bands(conn, window["graded_decision_id"])[0] if window["graded_decision_id"] else None
        end_band, average_band = (registry.subject_key(band=band) for band in registry.BAND_PRICE_MEASURE)
        rung = registry.subject_key(rung_points=registry.RUNG_POINTS[registry.HEADLINE_HORIZON])
        low_move, minutes = facts.get("low_move_points"), facts.get("minutes_covered_count")
        entries.append({
            "read_id": window["read_id"], "read_at": session_clock.parse_iso(window["read_cut_at"]),
            "price": facts.get("grade_start_price_points"),
            "window_from": session_clock.parse_iso(window["window_starts_at"]),
            "ends_at": session_clock.parse_iso(window["window_ends_at"]), "clipped": bool(window["was_window_clipped"]),
            "minutes": None if minutes is None else int(minutes), "status": window["label_status"],
            "half_width": bands and bands[registry.BAND]["half_width"],
            "average_half_width": bands and bands[registry.AVERAGE_BAND]["half_width"],
            "high": facts.get("high_price_points"), "low": facts.get("low_price_points"),
            "farthest_up": facts.get("high_move_points"), "farthest_down": None if low_move is None else -low_move,
            "end": facts.get("end_price_points"), "end_move": facts.get("end_move_points"),
            "average": facts.get("average_price_points"), "average_move": facts.get("average_move_points"),
            "grades": {ENDS_INSIDE: yes_no(grade.get(f"was_inside_band|{end_band}")),
                       AVERAGE_INSIDE: yes_no(grade.get(f"was_inside_band|{average_band}")),
                       NEVER_LEFT: yes_no(grade.get(f"did_leave_rung|{rung}"), flip=True)},
            "band_used": {side: grade.get(f"band_used_portion|{registry.subject_key(band=registry.BAND, side=side)}")
                          for side in registry.BAND_SIDES},
        })
    return entries


def values_by_key(rows):
    """{measure or measure|subject: value} over value rows, blanks left out."""
    return {(row["measure_name"] if row["subject_key"] == "whole" else f"{row['measure_name']}|{row['subject_key']}"):
            store.observation_value(row) for row in rows if store.observation_value(row) is not None}


def yes_no(stored, flip=False):
    """A stored 0/1 as True/False (flipped when asked), None when blank."""
    return None if stored is None else bool(stored) != flip


def score_counts(finished):
    """Today's score for the finished list: per grade word, how many said yes and how many were graded."""
    return {word: {"yes": sum(1 for entry in finished if entry["grades"][word] is True),
                   "of": sum(1 for entry in finished if entry["grades"][word] is not None)}
            for word in registry.GRADE_WORDS}


# ---- The scoreboard --------------------------------------------------------------------------------------------------

def scoreboard_lines(conn, day=None):
    """The three grades as yes/graded per session (or one), run kind, channel, variant and slice, then 20 pooled."""
    sessions = store.graded_sessions(conn, registry.GRADE_RULE)
    last = day if day else sessions[-1] if sessions else None
    pooled = [session for session in sessions if session <= last][-SCOREBOARD_SESSIONS:]
    if not pooled:
        return [f"no grades stored for {last} or before" if last else "no grades stored yet"]
    first = last if day else sessions[0]
    rule = f"{registry.GRADE_RULE[0]}@{registry.GRADE_RULE[1]}, grade start {registry.GRADE_START_RULE}"
    lines = [f"scoreboard ({rule}): ends inside, average inside, never left, as yes/graded",
             *count_lines(store.grade_counts(conn, first, last, registry.GRADE_RULE))]
    lines.append(f"last {len(pooled)} graded sessions ({pooled[0]} to {last}) pooled:")
    lines.extend(count_lines(store.grade_counts(conn, pooled[0], last, registry.GRADE_RULE, by_session=False)))
    return lines


def count_lines(rows):
    """One line per count row: session, run kind, channel, variant, slice, then yes/graded (share) per grade word."""
    rows = sorted(rows, key=lambda row: (row["trade_date"], row["run_kind"],
                                         registry.HORIZON_MINUTES[row["horizon_name"]], row["variant"], row["clipped"]))
    lines = []
    for row in rows:
        counts = "  ".join(f"{word} {row[word]}/{row[word + '_graded']}{share(row[word], row[word + '_graded'])}"
                           for word in registry.GRADE_WORDS)
        lines.append(f"{row['trade_date']}  {row['run_kind']:<14} {row['horizon_name']:<10} {row['variant']:<10} "
                     f"{SLICE_WORDS[row['clipped']]:<9} {counts}")
    return lines


def share(yes, graded):
    """' (0.740)' for a count with graded rows, blank otherwise."""
    return f" ({yes / graded:.3f})" if graded else ""
