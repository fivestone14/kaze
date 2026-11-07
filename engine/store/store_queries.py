# Every read of the store: the calendar, snapshots, bars and fits; decisions, windows, grades and the scoreboard's
# counts; the live-against-rebuilt pairs the parity check compares; the trust ledger's sample and weights. Nothing
# here writes, and every live-read query goes through the one statement of "the live truth".
from datetime import date, timedelta

from engine import session_clock
from engine.store import components, registry

ORIGIN_PREFERENCE_SQL = "CASE row_origin WHEN 'live_frozen' THEN 0 WHEN 'live_rebuilt' THEN 1 ELSE 2 END"


# ---- The registry ---------------------------------------------------------------------------------------------------

def newest_component_revisions(conn):
    """{(kind, name, version): (newest revision, its parameters hash, its meaning)} over registry_components."""
    return {(row["component_kind"], row["component_name"], row["component_version"]):
            (row["revision_number"], row["parameters_sha256"], row["plain_meaning_text"])
            for row in conn.execute(
                "SELECT component_kind, component_name, component_version, revision_number, "
                "parameters_sha256, plain_meaning_text FROM registry_components c WHERE revision_number = ("
                "SELECT MAX(revision_number) FROM registry_components n WHERE n.component_kind = "
                "c.component_kind AND n.component_name = c.component_name AND n.component_version = "
                "c.component_version)")}


# ---- The calendar ---------------------------------------------------------------------------------------------------

def calendar_events_known(conn, trade_date, cut_at_iso):
    """The day's timed events known by the cut, one per fact (its newest known version), as (starts_at, kind, tier)."""
    rows = conn.execute(
        "SELECT starts_at, fact_name, calendar_tier FROM collect_calendar_and_events e WHERE fact_kind = ? "
        "AND trade_date = ? AND known_at <= ? AND known_at = (SELECT MAX(known_at) FROM collect_calendar_and_events n "
        " WHERE n.fact_kind = e.fact_kind AND n.fact_id = e.fact_id AND n.known_at <= ?) "
        "GROUP BY fact_id ORDER BY starts_at",
        (registry.SCHEDULED_EVENT_FACT, trade_date, cut_at_iso, cut_at_iso)).fetchall()
    return [(row["starts_at"], row["fact_name"], row["calendar_tier"]) for row in rows]


def has_calendar_facts(conn):
    """True once the store holds any calendar fact (the first copy stamps facts with the calendar's own built date)."""
    return conn.execute("SELECT 1 FROM collect_calendar_and_events LIMIT 1").fetchone() is not None


def calendar_known_at(conn, cut_at_iso):
    """When the calendar version in force at the cut became known, or None when the store holds no calendar yet."""
    return conn.execute("SELECT MAX(known_at) FROM collect_calendar_and_events WHERE known_at <= ?",
                        (cut_at_iso,)).fetchone()[0]


def half_day_sessions(conn):
    """The sessions the stored calendar facts list as half days."""
    rows = conn.execute("SELECT DISTINCT fact_id FROM collect_calendar_and_events "
                        "WHERE fact_kind = ? AND fact_status = 'half_day'", (registry.SESSION_DATE_FACT,))
    return {date.fromisoformat(row["fact_id"]) for row in rows}


# ---- Snapshots -------------------------------------------------------------------------------------------------------

def snapshot_exists(conn, snapshot_id):
    """True when a snapshot with this id is stored."""
    return conn.execute("SELECT 1 FROM collect_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone() is not None


def snapshot_rowid(conn, snapshot_id):
    """The integer rowid of a stored snapshot, the key its observations hang off."""
    return conn.execute("SELECT snapshot_rowid FROM collect_snapshots WHERE snapshot_id = ?",
                        (snapshot_id,)).fetchone()[0]


# Every live-read query reads this one statement of "the live truth": the first built live read at each cut.
FIRST_BUILT_LIVE_READS_SQL = """
    SELECT s.* FROM collect_snapshots s
    WHERE s.snapshot_kind = 'read' AND s.row_origin IN ('live_frozen', 'live_rebuilt') AND s.snapshot_status != 'missed'
      AND s.build_attempt_number = (SELECT MIN(t.build_attempt_number) FROM collect_snapshots t
                                    WHERE t.cut_at = s.cut_at AND t.snapshot_kind = 'read'
                                      AND t.row_origin IN ('live_frozen', 'live_rebuilt')
                                      AND t.snapshot_status != 'missed')
"""
FIRST_BUILT_LIVE_SNAPSHOT_IDS_SQL = f"SELECT snapshot_id FROM ({FIRST_BUILT_LIVE_READS_SQL})"


def live_snapshot_at(conn, cut_at_iso):
    """The first built live read at this cut (the live truth for parity and file prefixes), or None."""
    return conn.execute(f"SELECT * FROM ({FIRST_BUILT_LIVE_READS_SQL}) WHERE cut_at = ?", (cut_at_iso,)).fetchone()


def live_reads_count(conn, trade_date):
    """How many built live reads a session has (one per cut, whatever the attempt)."""
    return conn.execute(f"SELECT COUNT(*) FROM ({FIRST_BUILT_LIVE_READS_SQL}) WHERE trade_date = ?",
                        (trade_date,)).fetchone()[0]


def live_slots_served(conn, trade_date):
    """The slots of a session that have a live read (any attempt)."""
    rows = conn.execute("SELECT DISTINCT scheduled_slot_eastern FROM collect_snapshots "
                        "WHERE trade_date = ? AND snapshot_kind = 'read' "
                        "AND row_origin IN ('live_frozen', 'live_rebuilt') AND scheduled_slot_eastern IS NOT NULL",
                        (trade_date,))
    return {row["scheduled_slot_eastern"] for row in rows}


def days_with_live_and_rebuilt_reads(conn):
    """Sessions that have at least one live read and one rebuilt read at the same cut."""
    rows = conn.execute(
        f"SELECT DISTINCT l.trade_date FROM ({FIRST_BUILT_LIVE_READS_SQL}) l JOIN collect_snapshots r "
        "ON l.cut_at = r.cut_at AND r.snapshot_kind = 'read' AND r.row_origin = 'history_rebuilt' "
        "ORDER BY l.trade_date").fetchall()
    return [row["trade_date"] for row in rows]


def live_sessions_not_rebuilt(conn):
    """Sessions with live reads but no rebuilt read yet (a nightly that never ran), oldest first."""
    rows = conn.execute(
        f"SELECT DISTINCT l.trade_date FROM ({FIRST_BUILT_LIVE_READS_SQL}) l WHERE NOT EXISTS "
        "(SELECT 1 FROM collect_snapshots r WHERE r.trade_date = l.trade_date AND r.snapshot_kind = 'read' "
        " AND r.row_origin = 'history_rebuilt') ORDER BY l.trade_date").fetchall()
    return [row["trade_date"] for row in rows]


def newest_live_read(conn, trade_date, cut_at_iso, read_lane=None):
    """The session's newest first-built live read at or before the cut, in one lane or any, or None."""
    return conn.execute(
        f"SELECT * FROM ({FIRST_BUILT_LIVE_READS_SQL}) WHERE trade_date = ? AND cut_at <= ? "
        "AND (? IS NULL OR read_lane = ?) ORDER BY cut_at DESC LIMIT 1",
        (trade_date, cut_at_iso, read_lane, read_lane)).fetchone()


def snapshots_with_reason(conn, trade_date, reason):
    """How many of a session's snapshots carry a word in their status reason (a failed step after the write)."""
    return conn.execute("SELECT COUNT(*) FROM collect_snapshots WHERE trade_date = ? "
                        "AND instr(COALESCE(snapshot_status_reason, ''), ?) > 0", (trade_date, reason)).fetchone()[0]


def snapshot_observations(conn, snapshot_rowid):
    """A snapshot's observation rows."""
    return conn.execute("SELECT * FROM collect_observations WHERE snapshot_rowid = ?", (snapshot_rowid,)).fetchall()


def read_price_and_bar(conn, snapshot_rowid):
    """A read's price and the start of the bar it came from (the grade-start price observation), or (None, None)."""
    row = conn.execute("SELECT value_number, newest_input_market_at FROM collect_observations "
                       "WHERE snapshot_rowid = ? AND measure_name = ?",
                       (snapshot_rowid, registry.GRADE_START_PRICE_MEASURE)).fetchone()
    return (row["value_number"], row["newest_input_market_at"]) if row else (None, None)


# ---- Bars ------------------------------------------------------------------------------------------------------------

def stored_bar_times(conn, trade_date, row_origin, symbol=registry.SPX_SYMBOL):
    """The priced_at stamps of a session's 1-minute bars of one instrument already stored under one origin."""
    rows = conn.execute("SELECT priced_at FROM collect_market_prices "
                        "WHERE trade_date = ? AND row_origin = ? AND price_kind = 'bar' AND instrument_symbol = ?",
                        (trade_date, row_origin, symbol)).fetchall()
    return {row["priced_at"] for row in rows}


def bar_count(conn, trade_date, row_origin, symbol=registry.SPX_SYMBOL):
    """How many 1-minute bars of one instrument a session holds under one origin."""
    return conn.execute("SELECT COUNT(*) FROM collect_market_prices "
                        "WHERE trade_date = ? AND row_origin = ? AND price_kind = 'bar' AND instrument_symbol = ?",
                        (trade_date, row_origin, symbol)).fetchone()[0]


def daily_bar_days(conn, symbol):
    """The days a symbol already has a daily bar for."""
    rows = conn.execute("SELECT DISTINCT trade_date FROM collect_market_prices "
                        "WHERE instrument_symbol = ? AND price_kind = 'day'", (symbol,)).fetchall()
    return {row["trade_date"] for row in rows}


def newest_daily_bar(conn, symbol, known_by_iso, through_day):
    """A symbol's newest daily bar through a day (its trade_date and known_at), known by a moment when one is given
    (a rebuild of history passes None and reads by market date), or None."""
    return conn.execute("SELECT trade_date, known_at FROM collect_market_prices WHERE instrument_symbol = ? "
                        "AND price_kind = 'day' AND trade_date <= ? AND (? IS NULL OR known_at <= ?) "
                        "ORDER BY trade_date DESC, known_at LIMIT 1",
                        (symbol, through_day, known_by_iso, known_by_iso)).fetchone()


def daily_bars(conn, symbol, through_day, known_by_iso=None):
    """A symbol's daily bars through a day, oldest first (one per day, live rows preferred), known by a moment."""
    rows = conn.execute(
        "SELECT trade_date, known_at, open_in_quote_units AS open, high_in_quote_units AS high, "
        " low_in_quote_units AS low, close_in_quote_units AS close FROM collect_market_prices "
        "WHERE instrument_symbol = ? AND price_kind = 'day' AND trade_date <= ? AND (? IS NULL OR known_at <= ?) "
        f"ORDER BY trade_date, {ORIGIN_PREFERENCE_SQL}", (symbol, through_day, known_by_iso, known_by_iso))
    bars = {}
    for row in rows:
        bars.setdefault(row["trade_date"], row)
    return list(bars.values())


def sessions_with_bars(conn):
    """Every session that has SPX bars, oldest first, with how many distinct minutes each holds."""
    rows = conn.execute("SELECT trade_date, COUNT(DISTINCT priced_at) AS minutes FROM collect_market_prices "
                        "WHERE instrument_symbol = ? AND price_kind = 'bar' GROUP BY trade_date ORDER BY trade_date",
                        (registry.SPX_SYMBOL,))
    return [(row["trade_date"], row["minutes"]) for row in rows]


def full_sessions(conn):
    """The sessions holding a bar for every minute of their session (390, or 210 on a listed half day), oldest first."""
    half_days = half_day_sessions(conn)
    return [day for day, minutes in sessions_with_bars(conn)
            if minutes == session_clock.session_length_minutes(date.fromisoformat(day), half_days)]


def session_bars(conn, trade_date, up_to_iso=None, known_by_iso=None):
    """One SPX 1-minute bar per minute of a session, oldest first, live rows preferred, each with when it was known."""
    # Only bars starting by up_to_iso and known by known_by_iso (no limit when None).
    rows = conn.execute(
        "SELECT priced_at, known_at, open_in_quote_units AS open, high_in_quote_units AS high, "
        " low_in_quote_units AS low, close_in_quote_units AS close FROM collect_market_prices "
        "WHERE trade_date = ? AND instrument_symbol = ? AND price_kind = 'bar' AND (? IS NULL OR priced_at <= ?) "
        f"AND (? IS NULL OR known_at <= ?) ORDER BY priced_at, {ORIGIN_PREFERENCE_SQL}",
        (trade_date, registry.SPX_SYMBOL, up_to_iso, up_to_iso, known_by_iso, known_by_iso))
    bars = {}
    for row in rows:
        bars.setdefault(row["priced_at"], row)
    return list(bars.values())


# ---- The push/pull pipeline's history ----------------------------------------------------------------------------------

def read_sessions_before(conn, trade_date, count):
    """The last `count` sessions before a day that hold reads, oldest first."""
    rows = conn.execute("SELECT DISTINCT trade_date FROM collect_snapshots WHERE snapshot_kind = 'read' "
                        "AND trade_date < ? ORDER BY trade_date DESC LIMIT ?", (trade_date, count)).fetchall()
    return sorted(row["trade_date"] for row in rows)


def values_at_same_clock(conn, measure_name, first_day, before_day, cut_at, slack_minutes, recipes=None):
    """A measure's filled observations on reads of past sessions whose cut falls within ±slack of the cut's UTC clock
    (the caller narrows to the session clock), one read per cut (live first, then rebuilt; given `recipes`, {factor:
    recipe hash}, the read stamped with the reading's current recipe first, so a reading re-scored under a changed
    recipe is measured against values of that recipe wherever they are stored), with subject and day. A window that
    runs past midnight on the UTC clock wraps round it (the whole-session pool does from 16:20 UTC on), and one a whole
    day wide takes every clock."""
    lower = session_clock.iso_utc(cut_at - timedelta(minutes=slack_minutes))[11:]
    upper = session_clock.iso_utc(cut_at + timedelta(minutes=slack_minutes))[11:]
    if 2 * slack_minutes >= 24 * 60:
        clock_sql, clock_args = "", ()
    elif lower <= upper:
        clock_sql, clock_args = "AND substr(s.cut_at, 12) >= ? AND substr(s.cut_at, 12) <= ? ", (lower, upper)
    else:
        clock_sql, clock_args = "AND (substr(s.cut_at, 12) >= ? OR substr(s.cut_at, 12) <= ?) ", (lower, upper)
    recipe_sql = ("(SELECT r.value_text FROM collect_observations r WHERE r.snapshot_rowid = s.snapshot_rowid "
                  "AND r.measure_name = 'push_pull_recipe_sha256' AND r.subject_key = o.subject_key)"
                  if recipes else "NULL")
    rows = conn.execute(
        f"SELECT s.trade_date, s.cut_at, s.row_origin, o.subject_key, o.value_number, {recipe_sql} AS recipe "
        "FROM collect_snapshots s JOIN collect_observations o ON o.snapshot_rowid = s.snapshot_rowid "
        f"WHERE s.snapshot_kind = 'read' AND s.trade_date >= ? AND s.trade_date < ? {clock_sql}"
        "AND o.measure_name = ? AND o.value_number IS NOT NULL "
        f"ORDER BY s.trade_date, s.cut_at, {ORIGIN_PREFERENCE_SQL}",
        (first_day, before_day, *clock_args, measure_name)).fetchall()
    current = lambda row: recipes is not None and row["recipe"] is not None \
        and row["recipe"] == recipes.get(registry.subject_parts(row["subject_key"]).get("factor"))
    kept = {}
    for row in rows:
        key = (row["trade_date"], row["cut_at"], row["subject_key"])
        if key not in kept or (current(row) and not current(kept[key])):
            kept[key] = row
    return list(kept.values())


def same_day_open_interest_book(conn, trade_date, cut_at_iso):
    """The newest same-day open interest an earlier read of the day kept: {(side, strike): contracts}, or {}."""
    snapshot = conn.execute(
        "SELECT s.snapshot_rowid FROM collect_snapshots s JOIN collect_observations o "
        "ON o.snapshot_rowid = s.snapshot_rowid WHERE s.trade_date = ? AND s.snapshot_kind = 'read' "
        "AND s.cut_at < ? AND o.measure_name = 'same_day_open_interest_contracts' AND o.value_number IS NOT NULL "
        f"ORDER BY s.cut_at DESC, {ORIGIN_PREFERENCE_SQL} LIMIT 1", (trade_date, cut_at_iso)).fetchone()
    if snapshot is None:
        return {}
    rows = conn.execute("SELECT subject_key, subject_points, value_number FROM collect_observations "
                        "WHERE snapshot_rowid = ? AND measure_name = 'same_day_open_interest_contracts' "
                        "AND value_number IS NOT NULL", (snapshot["snapshot_rowid"],)).fetchall()
    book = {}
    for row in rows:
        side = next(part.split("=")[1] for part in row["subject_key"].split("&") if part.startswith("side="))
        book[(side, float(row["subject_points"]))] = row["value_number"]
    return book


def push_pull_ledger_known(conn, trade_date, cut_at_iso):
    """The promotion ledger as known at a cut: {weight name: {live_size, live_noise, live_day_type,
    live_day_type_noise, live_days, betas, promoted, demoted}} from the newest ledger run in force by the day and known
    by the cut; {} before the ledger has written."""
    rows = conn.execute(
        "SELECT evaluated_name, metric_name, metric_value, metric_text, slice_key, trade_date, known_at "
        "FROM results_evaluations WHERE evaluated_kind = ? AND judge_name = ? AND trade_date <= ? AND known_at <= ? "
        "ORDER BY trade_date DESC, known_at DESC",
        (components.PUSH_PULL_FACTOR_KIND, components.PROMOTION_LEDGER[0], trade_date, cut_at_iso)).fetchall()
    if not rows:
        return {}
    newest = (rows[0]["trade_date"], rows[0]["known_at"])
    ledger = {}
    pairs = {"live_day_type_a_fit": ("live_day_type", 0), "live_day_type_c_fit": ("live_day_type", 1),
             "live_day_type_a_noise": ("live_day_type_noise", 0), "live_day_type_c_noise": ("live_day_type_noise", 1)}
    for row in rows:
        if (row["trade_date"], row["known_at"]) != newest:
            break
        entry = ledger.setdefault(row["evaluated_name"], {"live_size": 0.0, "live_noise": None, "live_day_type": None,
                                                           "live_day_type_noise": None, "live_days": 0, "betas": {},
                                                           "promoted": False, "demoted": False})
        metric, value = row["metric_name"], row["metric_value"]
        if metric == "live_weight_fit":
            entry["live_size"] = value or 0.0
        elif metric == "live_weight_noise":
            entry["live_noise"] = value
        elif metric in pairs:
            key, index = pairs[metric]
            pair = list(entry[key] or ((0.0, 0.0) if key == "live_day_type" else (None, None)))
            pair[index] = (value or 0.0) if key == "live_day_type" else value
            entry[key] = tuple(pair)
        elif metric == "live_booster_beta" and value is not None:
            entry["betas"][registry.subject_parts(row["slice_key"])["kind"]] = value
        elif metric == "live_days_count":
            entry["live_days"] = int(value or 0)
        elif metric == "promotion_e_value":
            entry["promoted"] = row["metric_text"] == components.PROMOTED
            entry["demoted"] = row["metric_text"] == components.DEMOTED
    return ledger


# The days the push/pull readings were read live: the promotion ledger scores them on these alone.
LIVE_ERA_DAYS_SQL = (f"SELECT DISTINCT trade_date FROM ({FIRST_BUILT_LIVE_READS_SQL}) "
                     f"WHERE trade_date >= '{components.PUSH_PULL_LIVE_SINCE}'")
SAMPLED_ORIGINS = ("live_frozen", "history_rebuilt")


def push_pull_sample(conn, horizon_name, through_day):
    """The promotion ledger's sample: on every day read live through a day, each first-built live read and each
    rebuilt slot read (a hand rebuild of another moment is left out) whose headline-channel decision has a counting
    grade, with its origin, the window's average move from
    publish, the channel's swing, each reading's strength, booster and recipe hash, and the context inputs: rows of
    {trade_date, cut_at, origin, average_from_publish, swing, trend_ness, strengths, boosters, recipes, context}."""
    rows = conn.execute(
        "SELECT d.decision_rowid, d.trade_date, d.cut_at, d.row_origin, s.snapshot_rowid, "
        " v.value_number AS average_from_publish, "
        " (SELECT value_number FROM reads_decision_values w WHERE w.decision_rowid = d.decision_rowid "
        "  AND w.measure_name = 'typical_swing_points' AND w.subject_key = ?) AS swing "
        "FROM reads_decisions d "
        "JOIN collect_snapshots s ON s.snapshot_id = d.snapshot_id "
        "JOIN results_outcomes g ON g.judged_decision_id = d.decision_id AND g.does_count_toward_skill = 1 "
        "JOIN results_outcomes m ON m.outcome_id = g.market_outcome_id "
        "JOIN results_outcome_values v ON v.outcome_rowid = m.outcome_rowid AND v.measure_name = ? "
        "WHERE d.decision_kind = ? AND d.horizon_name = ? AND d.variant_name IS NULL AND d.row_origin IN (?, ?) "
        "AND s.slot_status != 'not_scheduled' "
        f"AND d.trade_date IN ({LIVE_ERA_DAYS_SQL}) AND d.trade_date <= ? AND v.value_number IS NOT NULL "
        "ORDER BY d.trade_date, d.cut_at, d.row_origin, s.build_attempt_number, s.snapshot_rowid",
        (registry.subject_key(horizon=horizon_name), registry.AVERAGE_FROM_PUBLISH_MEASURE,
         components.RANGE_DECISION_KIND, horizon_name, *SAMPLED_ORIGINS, through_day)).fetchall()
    sample, seen = [], set()
    for row in rows:
        if (row["cut_at"], row["row_origin"]) in seen:      # one read per cut and origin: the first built
            continue
        seen.add((row["cut_at"], row["row_origin"]))
        read = {"trade_date": row["trade_date"], "cut_at": row["cut_at"], "origin": row["row_origin"],
                "average_from_publish": row["average_from_publish"], "swing": row["swing"], "strengths": {},
                "boosters": {}, "recipes": {}, "context": {}}
        for obs in conn.execute("SELECT measure_name, subject_key, value_number, value_text FROM collect_observations "
                                "WHERE snapshot_rowid = ? AND measure_name IN ('push_pull_strength', "
                                "'push_pull_recipe_sha256', 'push_pull_context_input')", (row["snapshot_rowid"],)):
            subject = registry.subject_parts(obs["subject_key"])
            if obs["measure_name"] == "push_pull_strength" and obs["value_number"] is not None:
                read["strengths"][subject["factor"]] = obs["value_number"]
            elif obs["measure_name"] == "push_pull_recipe_sha256":
                read["recipes"][subject["factor"]] = obs["value_text"]
            elif obs["measure_name"] == "push_pull_context_input":
                read["context"][subject["kind"]] = obs["value_number"]
        for value in conn.execute("SELECT subject_key, value_number FROM reads_decision_values "
                                  "WHERE decision_rowid = ? AND measure_name = 'push_pull_booster' "
                                  "AND value_number IS NOT NULL", (row["decision_rowid"],)):
            read["boosters"][registry.subject_parts(value["subject_key"])["factor"]] = value["value_number"]
        read["trend_ness"] = read["context"].get("trend_ness")
        sample.append(read)
    return sample


def call_grades(conn, horizon_name, through_day, sessions):
    """The push/pull call's graded live reads on one channel over the last sessions through a day: the first-built
    live read's base decision whose grade counts, as rows of {trade_date, cut_at, net, up, side, given, no_push}."""
    days = [row["trade_date"] for row in conn.execute(
        f"SELECT DISTINCT trade_date FROM ({FIRST_BUILT_LIVE_READS_SQL}) WHERE trade_date <= ? "
        "ORDER BY trade_date DESC LIMIT ?", (through_day, sessions))]
    if not days:
        return []
    rows = conn.execute(
        "SELECT d.trade_date, d.cut_at, d.decision_rowid, g.outcome_rowid FROM reads_decisions d "
        "JOIN results_outcomes g ON g.judged_decision_id = d.decision_id AND g.does_count_toward_skill = 1 "
        f"WHERE d.snapshot_id IN ({FIRST_BUILT_LIVE_SNAPSHOT_IDS_SQL}) AND d.decision_kind = ? AND d.horizon_name = ? "
        f"AND d.variant_name IS NULL AND d.trade_date IN ({', '.join('?' for _ in days)}) ORDER BY d.cut_at",
        (components.RANGE_DECISION_KIND, horizon_name, *days)).fetchall()
    graded = []
    for row in rows:
        values = {(value["measure_name"], value["subject_key"]): value
                  for value in conn.execute("SELECT * FROM results_outcome_values WHERE outcome_rowid = ?",
                                            (row["outcome_rowid"],))}
        side = values.get(("push_pull_side_landed_word", "whole"))
        if side is None or side["value_text"] is None:
            continue
        call = {(value["measure_name"], value["subject_key"]): value["value_number"]
                for value in conn.execute("SELECT measure_name, subject_key, value_number FROM reads_decision_values "
                                          "WHERE decision_rowid = ? AND measure_name IN ('push_pull_net_points', "
                                          "'push_pull_odds_probability')", (row["decision_rowid"],))}
        graded.append({"trade_date": row["trade_date"], "cut_at": row["cut_at"],
                       "net": call.get(("push_pull_net_points", "whole")),
                       "up": call.get(("push_pull_odds_probability", registry.subject_key(side="up"))),
                       "side": side["value_text"],
                       "given": values[("push_pull_odds_given_probability", "whole")]["value_number"],
                       "no_push": values[("push_pull_no_push_odds_probability", "whole")]["value_number"]})
    return graded


def recipe_stamps_by_day(conn, through_day):
    """Per day read live through a day, per origin, per reading, the recipe hashes its slot reads carry:
    {day: {origin: {factor: {hash}}}}; a day or origin with no stamped read is absent."""
    stamps = {day: {} for day in live_era_days(conn, through_day)}
    for row in conn.execute(
            "SELECT DISTINCT s.trade_date, s.row_origin, o.subject_key, o.value_text FROM collect_snapshots s "
            "JOIN collect_observations o ON o.snapshot_rowid = s.snapshot_rowid "
            "WHERE s.snapshot_kind = 'read' AND s.row_origin IN (?, ?) AND s.snapshot_status != 'missed' "
            "AND s.slot_status != 'not_scheduled' "
            f"AND o.measure_name = 'push_pull_recipe_sha256' AND s.trade_date IN ({LIVE_ERA_DAYS_SQL}) "
            "AND s.trade_date <= ?", (*SAMPLED_ORIGINS, through_day)):
        factor = registry.subject_parts(row["subject_key"])["factor"]
        stamps[row["trade_date"]].setdefault(row["row_origin"], {}).setdefault(factor, set()).add(row["value_text"])
    return stamps


def live_era_days(conn, through_day):
    """The days read live (a first-built live read on them) through a day, oldest first."""
    rows = conn.execute(f"SELECT trade_date FROM ({LIVE_ERA_DAYS_SQL}) WHERE trade_date <= ? ORDER BY trade_date",
                        (through_day,)).fetchall()
    return [row["trade_date"] for row in rows]


# ---- Fits ------------------------------------------------------------------------------------------------------------

def newest_fit_known(conn, trade_date, cut_at_iso):
    """The newest history fit a read on this day may use: serving this session or an earlier one, known by the cut."""
    return conn.execute(
        "SELECT * FROM collect_snapshots WHERE snapshot_kind = ? AND trade_date <= ? AND known_at <= ? "
        f"ORDER BY trade_date DESC, known_at DESC, {ORIGIN_PREFERENCE_SQL} LIMIT 1",
        (registry.FIT_SNAPSHOT_KIND, trade_date, cut_at_iso)).fetchone()


def newest_fit_serving(conn, trade_date):
    """The newest history fit written for exactly this session, or None."""
    return conn.execute(
        "SELECT * FROM collect_snapshots WHERE snapshot_kind = ? AND trade_date = ? "
        f"ORDER BY cut_at DESC, known_at DESC, {ORIGIN_PREFERENCE_SQL} LIMIT 1",
        (registry.FIT_SNAPSHOT_KIND, trade_date)).fetchone()


def sessions_served_by_a_fit(conn):
    """The sessions that already have a history fit serving them."""
    rows = conn.execute("SELECT DISTINCT trade_date FROM collect_snapshots WHERE snapshot_kind = ?",
                        (registry.FIT_SNAPSHOT_KIND,))
    return {row["trade_date"] for row in rows}


# ---- Decisions, windows, grades and the scoreboard ----------------------------------------------------------------

def grade_under_rule_sql(alias):
    """The one statement of 'a forecast grade under the current grade and grade-start rules', for a table alias."""
    # Its four parameters come from grade_rule_parameters.
    return (f"{alias}.outcome_kind = '{registry.FORECAST_GRADE_KIND}' AND {alias}.outcome_rule_name = ? "
            f"AND {alias}.outcome_rule_version = ? AND {alias}.grade_start_rule_name = ? "
            f"AND {alias}.grade_start_rule_version = ?")


def grade_rule_parameters(grade_rule):
    """The parameters grade_under_rule_sql binds: the grade rule and the grade-start rule, name and version."""
    return (*grade_rule, components.GRADE_START_RULE, components.GRADE_START_RULE_VERSION)


# Closed windows with no grade yet: decisions of a kind whose window ended by the cut and that no outcome judges.
UNGRADED_WINDOWS_SQL = ("FROM reads_decisions d WHERE decision_kind = ? AND grade_window_ends_at <= ? "
                        "AND NOT EXISTS (SELECT 1 FROM results_outcomes o WHERE o.judged_decision_id = d.decision_id)")


def decisions_with_closed_windows(conn, trade_date, cut_at_iso, decision_kind):
    """The session's decisions of a kind whose window ended by the cut and that no grade judges yet, read by read."""
    return conn.execute(f"SELECT * {UNGRADED_WINDOWS_SQL} AND trade_date = ? "
                        "ORDER BY cut_at, snapshot_id, horizon_minutes, variant_name",
                        (decision_kind, cut_at_iso, trade_date)).fetchall()


def sessions_with_open_windows(conn, cut_at_iso, decision_kind):
    """Every session holding a decision of the kind whose window ended by the cut and has no grade, oldest first."""
    rows = conn.execute(f"SELECT DISTINCT trade_date {UNGRADED_WINDOWS_SQL} ORDER BY trade_date",
                        (decision_kind, cut_at_iso)).fetchall()
    return [row["trade_date"] for row in rows]


def running_decisions(conn, trade_date, cut_at_iso, horizon_name, read_lane, decision_kind):
    """The base decisions of a horizon whose window has not ended by the cut, newest read first, with its rowid."""
    # From the session's first-built live reads at or before the cut, in one lane or any (read_lane None).
    return conn.execute(
        f"SELECT d.*, s.snapshot_rowid FROM reads_decisions d JOIN ({FIRST_BUILT_LIVE_READS_SQL}) s "
        "ON s.snapshot_id = d.snapshot_id WHERE d.trade_date = ? AND d.decision_kind = ? AND d.horizon_name = ? "
        "AND d.variant_name IS NULL AND s.cut_at <= ? AND d.grade_window_ends_at > ? "
        "AND (? IS NULL OR s.read_lane = ?) ORDER BY s.cut_at DESC",
        (trade_date, decision_kind, horizon_name, cut_at_iso, cut_at_iso, read_lane, read_lane)).fetchall()


def decisions_of_read(conn, snapshot_id, decision_kind, horizon_name):
    """A read's decisions of one kind and horizon, the base decision first, then its variants."""
    return conn.execute("SELECT * FROM reads_decisions WHERE snapshot_id = ? AND decision_kind = ? "
                        "AND horizon_name = ? ORDER BY variant_name IS NOT NULL, variant_name",
                        (snapshot_id, decision_kind, horizon_name)).fetchall()


def grades_without_call(conn):
    """Forecast grades written before the push/pull call was graded, with their decision and the window's average
    move and end price: rows of the decision's columns plus outcome_id, average_move and end_price."""
    return conn.execute(
        "SELECT d.*, g.outcome_id, "
        " (SELECT value_number FROM results_outcome_values v WHERE v.outcome_rowid = m.outcome_rowid "
        "  AND v.measure_name = 'average_move_points') AS average_move, "
        " (SELECT value_number FROM results_outcome_values v WHERE v.outcome_rowid = m.outcome_rowid "
        "  AND v.measure_name = 'end_price_points') AS end_price "
        "FROM results_outcomes g JOIN reads_decisions d ON d.decision_id = g.judged_decision_id "
        "JOIN results_outcomes m ON m.outcome_id = g.market_outcome_id "
        f"WHERE g.outcome_kind = '{registry.FORECAST_GRADE_KIND}' AND NOT EXISTS (SELECT 1 FROM results_outcome_values c "
        " WHERE c.outcome_rowid = g.outcome_rowid AND c.measure_name = 'push_pull_side_landed_word')").fetchall()


def decision_values(conn, decision_rowid):
    """A decision's value rows."""
    return conn.execute("SELECT * FROM reads_decision_values WHERE decision_rowid = ?", (decision_rowid,)).fetchall()


def outcome_values(conn, outcome_rowid):
    """An outcome's value rows."""
    return conn.execute("SELECT * FROM results_outcome_values WHERE outcome_rowid = ?", (outcome_rowid,)).fetchall()


def lane_windows_with_grades(conn, trade_date, horizon_name, read_lane, grade_rule):
    """The session's first-built live market windows of a horizon, newest first, each with its base grade if graded."""
    # One lane, or every read when read_lane is None; the grade comes as its rowid and its decision's rowid.
    return conn.execute(
        "SELECT w.*, s.cut_at AS read_cut_at, g.outcome_rowid AS grade_rowid, "
        " d.decision_rowid AS graded_decision_rowid "
        f"FROM results_outcomes w JOIN ({FIRST_BUILT_LIVE_READS_SQL}) s ON s.snapshot_id = w.anchor_id "
        f"LEFT JOIN results_outcomes g ON g.market_outcome_id = w.outcome_id AND {grade_under_rule_sql('g')} "
        "LEFT JOIN reads_decisions d ON d.decision_id = g.judged_decision_id AND d.variant_name IS NULL "
        "WHERE w.trade_date = ? AND w.outcome_kind = ? AND w.horizon_name = ? AND (? IS NULL OR s.read_lane = ?) "
        " AND (g.outcome_rowid IS NULL OR d.decision_rowid IS NOT NULL) ORDER BY w.window_starts_at DESC",
        (*grade_rule_parameters(grade_rule), trade_date, registry.MARKET_WINDOW_KIND, horizon_name,
         read_lane, read_lane)).fetchall()


def graded_sessions(conn, grade_rule):
    """Every session with a grade under the rule, oldest first."""
    rows = conn.execute(f"SELECT DISTINCT trade_date FROM results_outcomes o WHERE {grade_under_rule_sql('o')} "
                        "ORDER BY trade_date", grade_rule_parameters(grade_rule)).fetchall()
    return [row["trade_date"] for row in rows]


def grade_counts(conn, first_day, last_day, grade_rule, by_session=True):
    """Grade counts per session (or pooled), run kind, channel, variant and clipped slice: yes and graded per word."""
    # What yes means per word is the registry's one statement, GRADE_WORD_MEASURE.
    counted, parameters = [], ([] if by_session else [registry.POOLED]) + [components.BASE_VARIANT]
    for word, (measure_name, band, yes) in registry.GRADE_WORD_MEASURE.items():
        matched = "v.measure_name = ? AND (? IS NULL OR v.subject_key = ?)"
        counted.append(f"SUM({matched} AND v.value_bool = ?) AS {word}, "
                       f"SUM({matched} AND v.value_bool IS NOT NULL) AS {word}_graded")
        subject = registry.subject_key(band=band) if band else None
        parameters.extend([measure_name, subject, subject, yes, measure_name, subject, subject])
    parameters.extend([*grade_rule_parameters(grade_rule), first_day, last_day])
    return conn.execute(
        f"SELECT {'o.trade_date' if by_session else '? AS trade_date'}, d.run_kind, d.horizon_name, "
        f"COALESCE(d.variant_name, ?) AS variant, o.was_window_clipped AS clipped, {', '.join(counted)} "
        "FROM results_outcomes o JOIN reads_decisions d ON d.decision_id = o.judged_decision_id "
        "JOIN results_outcome_values v ON v.outcome_rowid = o.outcome_rowid "
        f"WHERE {grade_under_rule_sql('o')} AND o.trade_date BETWEEN ? AND ? "
        " AND (d.row_origin = 'history_rebuilt' OR d.snapshot_id IN "
        f" ({FIRST_BUILT_LIVE_SNAPSHOT_IDS_SQL})) GROUP BY 1, 2, 3, 4, 5", parameters).fetchall()


# ---- Parity pairs: each live-truth observation beside its rebuilt twin ---------------------------------------------

def parity_pairs(conn, trade_date):
    """Each live-truth observation of a session beside its history_rebuilt twin (same cut, measure and subject), with
    the push/pull reading's recipe stamp on each side (None for other subjects)."""
    columns = registry.VALUE_COLUMNS
    return conn.execute(
        "SELECT ls.cut_at, l.measure_name, l.subject_key, l.source_feed_name, l.blank_reason AS live_blank, "
        " r.blank_reason AS rebuilt_blank, "
        " (SELECT x.value_text FROM collect_observations x WHERE x.snapshot_rowid = ls.snapshot_rowid "
        "  AND x.measure_name = 'push_pull_recipe_sha256' AND x.subject_key = l.subject_key) AS live_recipe, "
        " (SELECT x.value_text FROM collect_observations x WHERE x.snapshot_rowid = rs.snapshot_rowid "
        "  AND x.measure_name = 'push_pull_recipe_sha256' AND x.subject_key = l.subject_key) AS rebuilt_recipe, "
        + ", ".join(f"l.{column} AS live_{column}, r.{column} AS rebuilt_{column}" for column in columns)
        + f" FROM ({FIRST_BUILT_LIVE_READS_SQL}) ls "
        " JOIN collect_observations l ON l.snapshot_rowid = ls.snapshot_rowid "
        " JOIN collect_snapshots rs ON rs.cut_at = ls.cut_at AND rs.snapshot_kind = 'read' "
        "  AND rs.row_origin = 'history_rebuilt' "
        " JOIN collect_observations r ON r.snapshot_rowid = rs.snapshot_rowid AND r.measure_name = l.measure_name "
        "  AND r.subject_key = l.subject_key "
        "WHERE ls.trade_date = ?", (trade_date,)).fetchall()


# ---- The trust ledger ------------------------------------------------------------------------------------------------

LIVE_COUNTING_DECISIONS_SQL = f"""
    SELECT d.decision_rowid, d.trade_date, d.row_origin, d.cut_at, d.decision_status, m.value_number AS end_move
    FROM reads_decisions d
    JOIN results_outcomes g ON g.judged_decision_id = d.decision_id AND g.does_count_toward_skill = 1
     AND {grade_under_rule_sql('g')}
    JOIN results_outcomes w ON w.outcome_id = g.market_outcome_id
    JOIN results_outcome_values m ON m.outcome_rowid = w.outcome_rowid AND m.value_number IS NOT NULL
     AND m.measure_name = '{registry.WINDOW_MOVE_MEASURE["end"]}'
    WHERE d.decision_kind = '{components.RANGE_DECISION_KIND}' AND d.horizon_name = ? AND d.trade_date <= ?
     AND d.run_kind = '{registry.RUN_KIND_FOR_ORIGIN["live_frozen"]}' AND d.variant_name IS NULL
     AND d.snapshot_id IN ({FIRST_BUILT_LIVE_SNAPSHOT_IDS_SQL})
"""


def live_counting_decisions(conn, horizon_name, through_day, grade_rule):
    """The channel's live, first-built base range decisions through a day whose grade counts, with the end move."""
    # Oldest first; replayed history never appears here.
    return conn.execute(f"{LIVE_COUNTING_DECISIONS_SQL} ORDER BY d.cut_at",
                        (*grade_rule_parameters(grade_rule), horizon_name, through_day)).fetchall()


def live_counting_decision_values(conn, horizon_name, through_day, grade_rule):
    """Every number stored on those decisions, as (decision_rowid, measure, subject, role, value)."""
    return conn.execute(
        "SELECT v.decision_rowid, v.measure_name, v.subject_key, v.value_role, v.value_number "
        "FROM reads_decision_values v WHERE v.value_number IS NOT NULL "
        f"AND v.decision_rowid IN (SELECT decision_rowid FROM ({LIVE_COUNTING_DECISIONS_SQL}))",
        (*grade_rule_parameters(grade_rule), horizon_name, through_day)).fetchall()


def trust_weights_known(conn, trade_date, cut_at_iso):
    """The weights in force at a cut, as {(evaluated kind, name, horizon): weight}; a blank row weighs 0."""
    # The newest trust_weight row per component and channel in force for this session or an earlier one and known
    # by the cut.
    rows = conn.execute(
        "SELECT e.evaluated_kind, e.evaluated_name, e.horizon_name, e.metric_value FROM results_evaluations e "
        "WHERE e.evaluation_kind = ? AND e.trade_date <= ? AND e.known_at <= ? AND e.known_at = ("
        " SELECT MAX(n.known_at) FROM results_evaluations n WHERE n.evaluation_kind = e.evaluation_kind"
        "  AND n.evaluated_kind = e.evaluated_kind AND n.evaluated_name = e.evaluated_name"
        "  AND n.evaluated_version = e.evaluated_version AND n.horizon_name = e.horizon_name"
        "  AND n.trade_date <= ? AND n.known_at <= ?)",
        (components.TRUST_WEIGHT_KIND, trade_date, cut_at_iso, trade_date, cut_at_iso)).fetchall()
    return {(row["evaluated_kind"], row["evaluated_name"], row["horizon_name"]): row["metric_value"] or 0.0
            for row in rows}


def prior_verdicts(conn, judged, horizon_name, through_day, runs):
    """A component's last ledger runs before a data-through day, newest first, as (verdict, weight)."""
    evaluated_kind, evaluated_name, evaluated_version = judged
    rows = conn.execute(
        f"SELECT {components.VERDICT_COLUMN}, metric_value FROM results_evaluations WHERE evaluation_kind = ? "
        "AND evaluated_kind = ? AND evaluated_name = ? AND evaluated_version = ? AND horizon_name = ? "
        "AND data_through_trade_date < ? ORDER BY data_through_trade_date DESC, known_at DESC LIMIT ?",
        (components.TRUST_WEIGHT_KIND, evaluated_kind, evaluated_name, evaluated_version, horizon_name, through_day,
         runs)).fetchall()
    return [(row[0], row[1] or 0.0) for row in rows]


def newest_evaluations(conn):
    """The rows of the newest evaluation run per component and channel."""
    return conn.execute(
        "SELECT e.* FROM results_evaluations e WHERE e.known_at = ("
        " SELECT MAX(n.known_at) FROM results_evaluations n WHERE n.evaluated_kind = e.evaluated_kind"
        "  AND n.evaluated_name = e.evaluated_name AND n.evaluated_version = e.evaluated_version"
        "  AND n.horizon_name = e.horizon_name)").fetchall()
