# Outcomes and grades: measures every range decision window from the store's bars once all its bars are held (or
# the session is settled), writes the window's outcome and one grade per decision, and prints the scoreboard.
import itertools

from engine import range_fit, range_read, registry, session_clock, store

SCOREBOARD_SESSIONS = 20
CLIPPED_SLICE_WORD = {0: "unclipped", 1: "clipped"}


# ---- Measuring a window --------------------------------------------------------------------------------------------

def window_minute_span(decision, day):
    """The first and last session minute a decision's window covers (12:36 to 13:06 covers minutes 186 to 215)."""
    first = session_clock.session_minute(session_clock.parse_iso(decision["grade_window_starts_at"]), day)
    last = session_clock.session_minute(session_clock.parse_iso(decision["grade_window_ends_at"]), day) - 1
    return first, last


def measure_window(conn, decision, day, series, known_at_by_minute, snapshot_rowid):
    """Everything a window's outcome and grades are built from: the read's start price and bar, the bars' facts, the
    signed moves, the minutes wanted and held, and the moment the window's last-arriving bar was known."""
    first, last = window_minute_span(decision, day)
    facts = range_fit.window_facts(*series, first, last)
    start_price, start_bar_at = store.read_price_and_bar(conn, snapshot_rowid)
    held = [known_at_by_minute[minute] for minute in range(first, last + 1) if minute in known_at_by_minute]
    return {"start_price": start_price, "start_bar_at": start_bar_at, "facts": facts,
            "moves": signed_moves(start_price, facts), "minutes_wanted": last - first + 1,
            "known_at": max(held) if held else None}


def signed_moves(start_price, facts):
    """End, average, high and low moves from the start price, or None when either side is missing."""
    if facts is None or start_price is None:
        return None
    return {side: facts[side] - start_price for side in registry.WINDOW_SIDES}


def window_status(measured):
    """The label status and reason a window's bars and read price give it."""
    facts = measured["facts"]
    if facts is None:
        return registry.BARS_MISSING, registry.END_BAR_MISSING_LABEL_REASON
    if measured["start_price"] is None:
        return registry.LABEL_UNPRICEABLE, registry.NO_READ_PRICE_LABEL_REASON
    if facts["minutes"] < measured["minutes_wanted"]:
        return registry.LABEL_PARTIAL_BARS, f"{facts['minutes']}_of_{measured['minutes_wanted']}_minutes_held"
    return registry.LABEL_RESOLVED, None


def stayed_inside(moves, rung):
    """True when the window's farthest excursion stayed short of the rung on both sides; None without moves."""
    return None if moves is None else max(moves["high"], -moves["low"]) < rung


def band_miss(move, half_width):
    """Points the move landed past the band's edge, signed with the move; 0 when it stayed inside."""
    if abs(move) <= half_width:
        return 0.0
    return move - half_width if move > 0 else move + half_width


# ---- Closing windows: one market_window outcome per read and horizon, one forecast_grade per decision ------------

def close_windows(conn, day, cut_at, written_at, known_by_iso=None):
    """Outcome and grades for every range decision window of the day ended by the cut whose bars are all held (any
    window once the session is settled); returns (windows, grades)."""
    trade_date, cut_iso = day.isoformat(), session_clock.iso_utc(cut_at)
    decisions = store.decisions_with_closed_windows(conn, trade_date, cut_iso, range_read.DECISION_KIND)
    if not decisions:
        return 0, 0
    settled = cut_at >= session_clock.session_close(day, store.half_day_sessions(conn))
    bars = store.session_bars(conn, trade_date, cut_iso, known_by_iso)
    series = range_fit.minute_series(bars, trade_date)
    known_at_by_minute = {session_clock.session_minute(session_clock.parse_iso(bar["priced_at"]), day): bar["known_at"]
                          for bar in bars}
    written_iso = session_clock.iso_utc(written_at)
    windows = grades = 0
    for (snapshot_id, _), group in itertools.groupby(decisions, key=lambda d: (d["snapshot_id"], d["horizon_name"])):
        group = list(group)
        snapshot_rowid = conn.execute("SELECT snapshot_rowid FROM collect_snapshots WHERE snapshot_id = ?",
                                      (snapshot_id,)).fetchone()[0]
        measured = measure_window(conn, group[0], day, series, known_at_by_minute, snapshot_rowid)
        if window_status(measured)[0] != registry.LABEL_RESOLVED and not settled:
            continue
        window, values = window_outcome_with_values(group[0], measured, written_iso)
        outcomes = [window]
        for decision in group:
            grade, grade_values = grade_with_values(conn, decision, window, measured, written_iso)
            outcomes.append(grade)
            values.extend(grade_values)
        store.write_outcomes(conn, outcomes, values)
        windows += 1
        grades += len(group)
    return windows, grades


def resolved_at(decision, measured, written_iso):
    """When the outcome's label resolved: the last bar's known_at for a rebuilt row with bars, else the write."""
    if decision["row_origin"] == "history_rebuilt" and measured["facts"] is not None and measured["known_at"]:
        return measured["known_at"]
    return written_iso


def value_row_adder(outcome, rows):
    """A closure that appends one rounded value row of the outcome, blank with the reason given when None."""
    def add(measure_name, value, blank=None, subject_points=None, **subject):
        """One value row of this outcome."""
        rows.append(store.outcome_value_row(outcome, measure_name, store.rounded(value),
                                            registry.subject_key(**subject), subject_points=subject_points,
                                            blank_reason=blank))
    return add


def grade_start_rows(add, measured):
    """The grade start every outcome and grade carries: the read's price, its bar and the rule's name."""
    add("grade_start_price_points", measured["start_price"], registry.NOT_APPLICABLE)
    add("grade_start_bar_at", measured["start_bar_at"], registry.NOT_APPLICABLE)
    add("grade_start_rule_name", registry.GRADE_START_RULE)


def window_outcome_with_values(decision, measured, written_iso):
    """The market_window outcome of a decision's window with its facts, blank with the reason where unmeasurable."""
    status, reason = window_status(measured)
    window = store.outcome_row(registry.MARKET_WINDOW_KIND, decision, registry.WINDOW_RULE, written_iso,
                               resolved_at(decision, measured, written_iso), status, reason)
    rows = []
    add = value_row_adder(window, rows)
    facts, moves = measured["facts"], measured["moves"]
    no_move = registry.BARS_MISSING if facts is None else registry.NOT_APPLICABLE
    grade_start_rows(add, measured)
    for side in registry.WINDOW_SIDES:
        add(registry.WINDOW_PRICE_MEASURE[side], facts and facts[side], registry.BARS_MISSING)
        add(registry.WINDOW_MOVE_MEASURE[side], moves and moves[side], no_move)
    add("minutes_covered_count", facts and facts["minutes"], registry.BARS_MISSING)
    for rung in sorted(set(registry.RUNG_POINTS.values())):
        add("stayed_inside_rung", stayed_inside(moves, rung), no_move, subject_points=float(rung), rung_points=rung)
    return window, rows


def decision_bands(conn, decision):
    """A decision's bands as {band: {half_width, low, high}} from its value rows, and the blank reason if unformed."""
    bands = {band: dict.fromkeys(range_read.BAND_EDGES) for band in registry.BAND_PRICE_MEASURE}
    blank = None
    for row in store.decision_values(conn, decision["decision_rowid"]):
        subject = registry.subject_parts(row["subject_key"])
        if row["measure_name"] == "band_half_width_points":
            bands[subject["band"]]["half_width"] = row["value_number"]
            blank = blank or row["blank_reason"]
        elif row["measure_name"] == "band_low_points":
            bands[subject["band"]]["low"] = row["value_number"]
        elif row["measure_name"] == "band_high_points":
            bands[subject["band"]]["high"] = row["value_number"]
    return bands, blank


def grade_with_values(conn, decision, window, measured, written_iso):
    """The forecast_grade of one decision against the window: inside and miss per band, the rung, the band used."""
    bands, band_blank = decision_bands(conn, decision)
    status, reason = window["label_status"], window["label_status_reason"]
    if status in registry.LABELS_THAT_COUNT and band_blank is not None:
        status, reason = registry.LABEL_UNPRICEABLE, decision["decision_status_reason"]
    grade = store.outcome_row(registry.FORECAST_GRADE_KIND, decision, registry.GRADE_RULE, written_iso,
                              window["label_resolved_at"], status, reason, judged_decision_id=decision["decision_id"],
                              market_outcome_id=window["outcome_id"],
                              graded_price_measure=decision["graded_price_measure"])
    rows = []
    add = value_row_adder(grade, rows)
    moves = measured["moves"]
    blank = registry.BARS_MISSING if measured["facts"] is None else registry.NOT_APPLICABLE if moves is None \
        else band_blank
    grade_start_rows(add, measured)
    for band, side in ((registry.BAND, "end"), (registry.AVERAGE_BAND, "average")):
        half = bands[band]["half_width"]
        move = moves[side] if moves and half is not None else None
        inside = None if move is None else abs(move) <= half
        add("was_inside_band", inside, blank, band=band)
        add("band_miss_points", None if inside is None else band_miss(move, half), blank, band=band)
    rung = registry.RUNG_POINTS[decision["horizon_name"]]
    add("stayed_inside_rung", None if band_blank else stayed_inside(moves, rung), blank, subject_points=float(rung),
        rung_points=rung)
    half = bands[registry.BAND]["half_width"]
    for band_side, side, sign in zip(registry.BAND_SIDES, ("high", "low"), (1, -1)):
        used = max(0.0, sign * moves[side]) / half if moves and half else None
        add("band_used_ratio", used, blank, band=registry.BAND, side=band_side)
    return grade, rows


# ---- The scoreboard --------------------------------------------------------------------------------------------------

def scoreboard_lines(conn, day=None):
    """The three grades as yes/graded per session (or one), run kind, channel, variant and slice, then 20 pooled."""
    sessions = store.graded_sessions(conn, registry.GRADE_RULE)
    last = day if day else sessions[-1] if sessions else None
    pooled = [session for session in sessions if session <= last][-SCOREBOARD_SESSIONS:]
    if not pooled:
        return [f"no grades stored for {last} or before" if last else "no grades stored yet"]
    first = last if day else sessions[0]
    rule = (f"{registry.GRADE_RULE[0]}@{registry.GRADE_RULE[1]}, grade start "
            f"{registry.GRADE_START_RULE}@{registry.GRADE_START_RULE_VERSION}")
    lines = [f"scoreboard ({rule}): ends inside, average inside, never left, as yes/graded",
             *score_lines(store.grade_counts(conn, first, last, registry.GRADE_RULE))]
    lines.append(f"last {len(pooled)} graded sessions ({pooled[0]} to {last}) pooled:")
    lines.extend(score_lines(store.grade_counts(conn, pooled[0], last, registry.GRADE_RULE, by_session=False)))
    return lines


def score_lines(rows):
    """One line per count row: session, run kind, channel, variant, slice, then yes/graded (share) per grade word."""
    rows = sorted(rows, key=lambda row: (row["trade_date"], row["run_kind"],
                                         registry.HORIZON_MINUTES[row["horizon_name"]], row["variant"], row["clipped"]))
    lines = []
    for row in rows:
        counts = "  ".join(f"{word} {row[word]}/{row[word + '_graded']}{share_text(row[word], row[word + '_graded'])}"
                           for word in registry.GRADE_WORDS)
        lines.append(f"{row['trade_date']:<10}  {row['run_kind']:<14} {row['horizon_name']:<10} {row['variant']:<10} "
                     f"{CLIPPED_SLICE_WORD[row['clipped']]:<9} {counts}")
    return lines


def share_text(yes, graded):
    """' (0.740)' for a count with graded rows, blank otherwise."""
    return f" ({yes / graded:.3f})" if graded else ""
