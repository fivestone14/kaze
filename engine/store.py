# The SQLite store: opening a file at the code's version (engine/store_schema.py holds the tables), the row shapers,
# and every write and read. The writer refuses unregistered measures, blanks without a reason, wrong value columns,
# out-of-range numbers and inputs newer than the cut; a read's snapshot, observations and decisions land in one
# transaction, as do a window's outcome and its grades.
import functools
import hashlib
import json
import sqlite3
import subprocess
from datetime import date

from engine import paths, registry, session_clock, store_schema

BUSY_TIMEOUT_SECONDS = 30
STORED_DECIMALS = 6
MONEY_DECIMALS = 2
NUMBER_TOLERANCE = 10 ** -STORED_DECIMALS
TOLERATED_PARITY_FEEDS = ("options_tape", "options_scan")
INSERT_VERBS = {"fail": "INSERT", "replace": "INSERT OR REPLACE", "ignore": "INSERT OR IGNORE"}
TABLE_COLUMNS = {}
ORIGIN_PREFERENCE_SQL = "CASE row_origin WHEN 'live_frozen' THEN 0 WHEN 'live_rebuilt' THEN 1 ELSE 2 END"
WHOLE = registry.subject_key()


# ---- Open, seed, insert --------------------------------------------------------------------------------------------

def open_store():
    """Open the store (creating or upgrading its tables, each step in one transaction) with the registry seeded."""
    file = paths.store_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(file), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    store_schema.upgrade(conn)
    seed_registry(conn)
    return conn


def seed_registry(conn):
    """Write every registry row, replacing earlier copies; a dropped name is removed unless rows still use it."""
    names = list(registry.MEASURES_BY_NAME)
    with conn:
        stored = {row["measure_name"] for row in conn.execute("SELECT measure_name FROM registry_measures")}
        dropped = sorted(stored - set(names))
        for table in store_schema.VALUE_TABLE_PARENTS:
            holes = ", ".join("?" for _ in dropped)
            used = conn.execute(f"SELECT DISTINCT measure_name FROM {table} WHERE measure_name IN ({holes})",
                                dropped).fetchall() if dropped else ()
            if used:
                raise SystemExit(f"kaze: {table} holds rows of unregistered measures {[row[0] for row in used]}")
        insert_rows(conn, "registry_measures", registry.MEASURES, on_conflict="replace")
        holes = ", ".join("?" for _ in names)
        conn.execute(f"DELETE FROM registry_measures WHERE measure_name NOT IN ({holes})", names)
        components = registry.component_rows()
        insert_rows(conn, "registry_components", components, on_conflict="replace")
        component_names = [row["component_name"] for row in components]
        conn.execute("DELETE FROM registry_components WHERE component_name NOT IN "
                     f"({', '.join('?' for _ in component_names)})", component_names)


def table_columns(conn, table):
    """The table's column names, read once from SQLite."""
    if table not in TABLE_COLUMNS:
        TABLE_COLUMNS[table] = store_schema.table_columns(conn, table)
    return TABLE_COLUMNS[table]


def insert_rows(conn, table, rows, on_conflict="fail"):
    """Insert dict rows (left-out columns become NULL; a key the table lacks is refused); returns rows added."""
    if not rows:
        return 0
    columns = table_columns(conn, table)
    unknown = set().union(*(row.keys() for row in rows)) - set(columns)
    if unknown:
        raise ValueError(f"{table} has no column {sorted(unknown)}")
    sql = f"{INSERT_VERBS[on_conflict]} INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
    before = conn.total_changes
    conn.executemany(sql, [tuple(row.get(column) for column in columns) for row in rows])
    return conn.total_changes - before


# ---- Ids and row shapers -------------------------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def code_head():
    """The repo's HEAD commit and whether the working tree had uncommitted changes; blanks when git is unavailable."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.REPO_ROOT, capture_output=True, text=True,
                              check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=paths.REPO_ROOT, capture_output=True, text=True,
                                check=True)
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return head.stdout.strip(), int(bool(status.stdout.strip()))


def snapshot_id_for(snapshot):
    """The snapshot id, built from the row's own kind, cut, origin, builder and attempt."""
    return (f"{snapshot['snapshot_kind']}|{snapshot['cut_at']}|{snapshot['row_origin']}|"
            f"{snapshot['builder_name']}@{snapshot['builder_version']}|b{snapshot['build_attempt_number']}")


def price_record_id_for(price):
    """The price record id from instrument, kind, time, value origin and row origin (no snapshot for streaming bars)."""
    return (f"{price['instrument_symbol']}|{price['price_kind']}|{price['priced_at']}|-|"
            f"{price['value_origin']}|{price['row_origin']}")


def decision_id_for(decision):
    """The decision id: kind, snapshot, producer, run:variant, horizon, graded price, structure, subject, revision."""
    return (f"{decision['decision_kind']}|{decision['snapshot_id']}|"
            f"{decision['producer_name']}@{decision['producer_version']}|"
            f"{decision['run_kind']}:{decision['variant_name'] or 'live'}|{decision['horizon_name'] or '-'}|"
            f"{decision['graded_price_measure'] or '-'}|-|{decision['subject_key']}|r{decision['revision_number']}")


def outcome_id_for(outcome):
    """The outcome id: kind, anchor, judged decision, horizon, price measure, start rule, rule, origin, attempt."""
    start_rule = f"{outcome['grade_start_rule_name']}@{outcome['grade_start_rule_version']}" \
        if outcome["grade_start_rule_name"] else "-"
    return (f"{outcome['outcome_kind']}|{outcome['anchor_id']}|{outcome['judged_decision_id'] or '-'}|"
            f"{outcome['horizon_name'] or '-'}|{outcome['graded_price_measure'] or '-'}|{start_rule}|"
            f"{outcome['outcome_rule_name']}@{outcome['outcome_rule_version']}|-|"
            f"{outcome['row_origin']}|b{outcome['build_attempt_number']}")


def base_snapshot_row(snapshot_kind, cut_at_iso, trade_date, row_origin, written_at_iso,
                      builder=(registry.BUILDER_NAME, registry.BUILDER_VERSION)):
    """A collect_snapshots row with the fields every kind shares and the code commit; the caller adds slot and lane."""
    builder_name, builder_version = builder
    commit, dirty = code_head()
    row = {
        "snapshot_kind": snapshot_kind, "trade_date": trade_date, "cut_at": cut_at_iso, "market_moment_id": cut_at_iso,
        "known_at": written_at_iso, "known_at_rule": "written_at", "row_origin": row_origin, "build_attempt_number": 1,
        "builder_name": builder_name, "builder_version": builder_version,
        "builder_code_commit": commit, "was_code_tree_dirty": dirty,
        "component_versions_json_text": json.dumps({builder_name: builder_version,
                                                    "measures_registry": registry.REGISTRY_VERSION}, sort_keys=True),
        "build_started_at": written_at_iso, "written_at": written_at_iso, "slot_status": "not_scheduled",
        "is_training_grid_read": 0, "read_moment_set_by": "clock", "snapshot_status": "built",
    }
    row["snapshot_id"] = snapshot_id_for(row)
    return row


def placed_value(measure, value):
    """The four value columns with the value in the registry's one (yes/no stored as 0/1), or all empty when blank."""
    columns = dict.fromkeys(registry.VALUE_COLUMNS)
    if value is not None:
        columns[measure["value_column"]] = int(value) if measure["value_type"] == "yes_no" else value
    return columns


def registered_measure(measure_name):
    """The registry row of a measure; refuses a name the registry lacks."""
    measure = registry.MEASURES_BY_NAME.get(measure_name)
    if measure is None:
        raise ValueError(f"{measure_name} is not a registered measure")
    return measure


def measure_value_row(measure_name, value, subject_key, subject_points, blank_reason):
    """The columns every long value row shares: measure, version, subject and the value in the registry's column."""
    measure = registered_measure(measure_name)
    return {"measure_name": measure_name, "measure_version": measure["measure_version"], "subject_key": subject_key,
            "subject_points": subject_points, "blank_reason": blank_reason if value is None else None,
            **placed_value(measure, value)}


def observation_row(snapshot, measure_name, value, subject_key=WHOLE, subject_points=None, blank_reason=None,
                    source_feed_name=None, feed_status=None, feed_age_seconds=None, market_at=None, received_at=None):
    """Shape one collect_observations row for a snapshot; the write stamps the snapshot's rowid on it."""
    return {
        "cut_at": snapshot["cut_at"], "value_origin": registry.LIVE_VALUE_ORIGIN,
        "source_feed_name": source_feed_name or registered_measure(measure_name)["source_feed_name"],
        "feed_status": feed_status, "feed_age_seconds": feed_age_seconds, "newest_input_market_at": market_at,
        "newest_input_received_at": received_at,
        **measure_value_row(measure_name, value, subject_key, subject_points, blank_reason),
    }


def decision_row(snapshot, decision_kind, producer, fields):
    """Shape a reads_decisions row for a snapshot: the shared columns from the snapshot, the rest from fields."""
    producer_kind, producer_name, producer_version = producer
    row = {
        "decision_kind": decision_kind, "trade_date": snapshot["trade_date"], "storage_scope": registry.SHARED_SCOPE,
        "parent_decision_id": None, "input_decision_ids_text": None, "snapshot_id": snapshot["snapshot_id"],
        "read_id": snapshot.get("read_id"), "cut_at": snapshot["cut_at"], "features_cutoff_at": snapshot["cut_at"],
        "known_at": snapshot["known_at"], "row_origin": snapshot["row_origin"],
        "run_kind": registry.RUN_KIND_FOR_ORIGIN[snapshot["row_origin"]], "variant_name": None,
        "producer_kind": producer_kind, "producer_name": producer_name, "producer_version": producer_version,
        "builder_code_commit": snapshot["builder_code_commit"], "horizon_name": None, "horizon_minutes": None,
        "graded_price_measure": None, "grade_start_rule_name": None, "grade_start_rule_version": None,
        "grade_window_starts_at": None, "grade_window_ends_at": None, "subject_key": WHOLE, "revision_number": 0,
        "decision_status": registry.DECISION_OK,
        "decision_status_reason": None, "decision_word": None, "written_at": snapshot["written_at"], **fields,
    }
    row["decision_id"] = decision_id_for(row)
    return row


def decision_value_row(decision, measure_name, value, subject_key=WHOLE, subject_points=None,
                       value_role=registry.VALUE_ROLE, blank_reason=None, trust_weight=None):
    """Shape one reads_decision_values row; it names its decision, which the write turns into the decision's rowid."""
    return {"decision_id": decision["decision_id"], "value_role": value_role,
            "trust_weight_applied_portion": trust_weight,
            **measure_value_row(measure_name, value, subject_key, subject_points, blank_reason)}


def outcome_row(outcome_kind, decision, rule, written_at_iso, resolved_at_iso, label_status, reason=None, **fields):
    """Shape a results_outcomes row on a decision's window: anchored on its read, carrying the window and the rules."""
    rule_name, rule_version = rule
    starts_at, ends_at = decision["grade_window_starts_at"], decision["grade_window_ends_at"]
    window_seconds = (session_clock.parse_iso(ends_at) - session_clock.parse_iso(starts_at)).total_seconds()
    row = {
        "outcome_kind": outcome_kind, "trade_date": decision["trade_date"], "storage_scope": decision["storage_scope"],
        "anchor_id": decision["snapshot_id"], "judged_decision_id": None, "market_outcome_id": None,
        "read_id": decision["read_id"], "horizon_name": decision["horizon_name"], "graded_price_measure": None,
        "grade_start_rule_name": decision["grade_start_rule_name"],
        "grade_start_rule_version": decision["grade_start_rule_version"], "window_starts_at": starts_at,
        "window_ends_at": ends_at,
        "was_window_clipped": int(window_seconds < decision["horizon_minutes"] * session_clock.BAR_LENGTH_SECONDS),
        "outcome_rule_name": rule_name, "outcome_rule_version": rule_version, "label_status": label_status,
        "label_status_reason": reason, "label_resolved_at": resolved_at_iso,
        "does_count_toward_skill": int(label_status in registry.LABELS_THAT_COUNT),
        "row_origin": decision["row_origin"], "build_attempt_number": 1, "builder_code_commit": code_head()[0],
        "written_at": written_at_iso, **fields,
    }
    row["outcome_id"] = outcome_id_for(row)
    return row


def outcome_value_row(outcome, measure_name, value, subject_key=WHOLE, subject_points=None, blank_reason=None):
    """Shape one results_outcome_values row; it names its outcome, which the write turns into the outcome's rowid."""
    row = measure_value_row(measure_name, value, subject_key, subject_points, blank_reason)
    blank = row["blank_reason"]
    row["value_status"] = registry.VALUE_STATUS_OK if value is not None \
        else blank if blank in registry.VALUE_STATUSES else registry.NOT_APPLICABLE
    return {"outcome_id": outcome["outcome_id"], **row}


def bar_price_row(bar, trade_date, row_origin, priced_at_iso, known_at_iso, known_at_rule):
    """A collect_market_prices row for one SPX 1-minute bar (Mirai's index volume is a known-wrong 0, left blank)."""
    row = {
        "trade_date": trade_date, "instrument_symbol": registry.SPX_SYMBOL, "instrument_kind": "index",
        "price_kind": "bar", "bar_minutes": 1, "priced_at": priced_at_iso, "known_at": known_at_iso,
        "known_at_rule": known_at_rule, "row_origin": row_origin, "value_origin": registry.LIVE_VALUE_ORIGIN,
        "source_feed_name": "spx_bars", "feed_status": "ok", "quote_unit": "points",
        "open_in_quote_units": bar["open"], "high_in_quote_units": bar["high"], "low_in_quote_units": bar["low"],
        "close_in_quote_units": bar["close"], "quote_status": "ok",
        "blank_reasons_text": "volume_count=known_wrong_same_day",
    }
    row["price_record_id"] = price_record_id_for(row)
    return row


def rounded(value):
    """A float at the store's precision; anything else (None, a count, a word) unchanged."""
    return round(value, STORED_DECIMALS) if isinstance(value, float) else value


def observation_value(row):
    """The one filled value column of a long value row, or None when it is blank."""
    return next((row[column] for column in registry.VALUE_COLUMNS if row[column] is not None), None)


def value_key(row):
    """The one key a value row goes by outside the store: its measure name, plus |subject when there is one."""
    return row["measure_name"] if row["subject_key"] == WHOLE else f"{row['measure_name']}|{row['subject_key']}"


def values_by_key(rows):
    """{value key: value} over value rows, blanks left out."""
    return {value_key(row): observation_value(row) for row in rows if observation_value(row) is not None}


def bars_known_by(snapshot, cut_at):
    """The moment a read's bars must have been known by: its bar deadline when live, no limit when rebuilt later."""
    if snapshot["row_origin"] == "history_rebuilt":
        return None
    return session_clock.iso_utc(session_clock.bar_deadline(cut_at))


# ---- Checks ---------------------------------------------------------------------------------------------------------

def check_measure_value(row):
    """Refuse a value row that breaks the registry: unknown subject, blank without reason, bad column, range, word."""
    name = row["measure_name"]
    measure = registry.MEASURES_BY_NAME[name]
    filled = [column for column in registry.VALUE_COLUMNS if row.get(column) is not None]
    if len(filled) > 1:
        raise ValueError(f"{name}: more than one value column filled")
    if filled and filled[0] != measure["value_column"]:
        raise ValueError(f"{name}: value belongs in {measure['value_column']}, not {filled[0]}")
    if not filled and row["blank_reason"] not in registry.BLANK_REASONS:
        raise ValueError(f"{name}: blank without a listed reason ({row['blank_reason']})")
    kinds = set(registry.subject_parts(row["subject_key"])) or {WHOLE}
    if not kinds <= set(measure["subject_kinds_text"].split("|")):
        raise ValueError(f"{name}: subject {row['subject_key']} is not among {measure['subject_kinds_text']}")
    number = row.get("value_number")
    if number is not None:
        low, high = measure["allowed_minimum_number"], measure["allowed_maximum_number"]
        if (low is not None and number < low) or (high is not None and number > high):
            raise ValueError(f"{name}: {number} is outside {low}..{high}")
    word = row.get("value_text")
    if word is not None and measure["allowed_words_text"] and word not in measure["allowed_words_text"].split("|"):
        raise ValueError(f"{name}: {word} is not an allowed word")


def check_observation(observation):
    """Refuse an observation that breaks the registry, names an unknown feed status or uses an input past the cut."""
    check_measure_value(observation)
    name = observation["measure_name"]
    if observation["feed_status"] is not None and observation["feed_status"] not in registry.FEED_STATUSES:
        raise ValueError(f"{name}: unknown feed status {observation['feed_status']}")
    market_at = observation["newest_input_market_at"]
    if market_at is not None and market_at > observation["cut_at"]:
        raise ValueError(f"{name}: input at {market_at} is newer than the cut {observation['cut_at']}")


def observation_fingerprint(observation):
    """The part of an observation a rebuild must reproduce: measure, subject, value and blank reason."""
    value = rounded(observation_value(observation))
    return f"{observation['measure_name']}|{observation['subject_key']}|{value}|{observation['blank_reason']}"


# ---- Writers --------------------------------------------------------------------------------------------------------

def claim_snapshot_id(conn, snapshot):
    """Settle a snapshot's id: a stored live cut moves to the next attempt, a rebuilt cut keeps its id (replaced)."""
    if snapshot["row_origin"] != "history_rebuilt":
        while snapshot_exists(conn, snapshot["snapshot_id"]):
            snapshot["build_attempt_number"] += 1
            snapshot["row_origin"] = "live_rebuilt"
            snapshot["snapshot_id"] = snapshot_id_for(snapshot)
    return snapshot["snapshot_id"]


def attach_to_parent(conn, rows, parent_table, parent_id, parent_rowid, parent_ids):
    """Replace each value row's parent text id with the stored parent's rowid, the step every value write takes."""
    holes = ", ".join("?" for _ in parent_ids)
    rowids = dict(conn.execute(f"SELECT {parent_id}, {parent_rowid} FROM {parent_table} "
                               f"WHERE {parent_id} IN ({holes})", list(parent_ids)).fetchall())
    for row in rows:
        row[parent_rowid] = rowids[row.pop(parent_id)]


def write_snapshot(conn, snapshot, observations, decisions=(), values=()):
    """Check and write a snapshot, its observations, decisions and values in one transaction; a rebuilt cut replaced."""
    for observation in observations:
        check_observation(observation)
    for value in values:
        check_measure_value(value)
    claim_snapshot_id(conn, snapshot)
    lines = sorted(observation_fingerprint(observation) for observation in observations)
    snapshot["snapshot_content_sha256"] = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    snapshot["blank_values_count"] = sum(1 for o in observations if o["blank_reason"] is not None)
    statuses = {o["source_feed_name"]: o["feed_status"] for o in observations if o["feed_status"] is not None}
    snapshot["feeds_not_ok_count"] = sum(1 for status in statuses.values() if status != "ok")
    newest = [o["newest_input_market_at"] for o in observations if o["newest_input_market_at"] is not None]
    snapshot["newest_input_market_at"] = max(newest) if newest else None
    decisions, values = list(decisions), list(values)
    with conn:
        if snapshot["row_origin"] == "history_rebuilt":
            delete_snapshot_rows(conn, snapshot["snapshot_id"])
        insert_rows(conn, "collect_snapshots", [snapshot])
        snapshot["snapshot_rowid"] = conn.execute("SELECT snapshot_rowid FROM collect_snapshots WHERE snapshot_id = ?",
                                                  (snapshot["snapshot_id"],)).fetchone()[0]
        for observation in observations:
            observation.pop("cut_at")
            observation["snapshot_rowid"] = snapshot["snapshot_rowid"]
        insert_rows(conn, "collect_observations", observations)
        insert_rows(conn, "reads_decisions", decisions)
        attach_to_parent(conn, values, *store_schema.VALUE_TABLE_PARENTS["reads_decision_values"],
                         {decision["decision_id"] for decision in decisions})
        insert_rows(conn, "reads_decision_values", values)
    return snapshot["snapshot_id"]


def delete_snapshot_rows(conn, snapshot_id):
    """Remove a rebuilt snapshot with its observations, decisions, values, outcomes and grades before it is replaced."""
    conn.execute("DELETE FROM results_outcome_values WHERE outcome_rowid IN "
                 "(SELECT outcome_rowid FROM results_outcomes WHERE anchor_id = ?)", (snapshot_id,))
    conn.execute("DELETE FROM results_outcomes WHERE anchor_id = ?", (snapshot_id,))
    conn.execute("DELETE FROM reads_decision_values WHERE decision_rowid IN "
                 "(SELECT decision_rowid FROM reads_decisions WHERE snapshot_id = ?)", (snapshot_id,))
    conn.execute("DELETE FROM reads_decisions WHERE snapshot_id = ?", (snapshot_id,))
    conn.execute("DELETE FROM collect_observations WHERE snapshot_rowid IN "
                 "(SELECT snapshot_rowid FROM collect_snapshots WHERE snapshot_id = ?)", (snapshot_id,))
    conn.execute("DELETE FROM collect_snapshots WHERE snapshot_id = ?", (snapshot_id,))


def mark_snapshot_reason(conn, snapshot, reason):
    """Add a reason to a stored snapshot's status reason (a step after the write failed) and on the row dict."""
    stored = snapshot["snapshot_status_reason"]
    snapshot["snapshot_status_reason"] = f"{stored},{reason}" if stored else reason
    with conn:
        conn.execute("UPDATE collect_snapshots SET snapshot_status_reason = ? WHERE snapshot_id = ?",
                     (snapshot["snapshot_status_reason"], snapshot["snapshot_id"]))


def write_missed_snapshot(conn, snapshot):
    """Record a scheduled slot that no live read served; nothing is written if a live read exists at that cut."""
    if live_snapshot_at(conn, snapshot["cut_at"]) is not None:
        return 0
    with conn:
        return insert_rows(conn, "collect_snapshots", [snapshot], on_conflict="ignore")


def write_bars(conn, bars):
    """Append bar rows to collect_market_prices, skipping ones already there; returns how many were new."""
    with conn:
        return insert_rows(conn, "collect_market_prices", bars, on_conflict="ignore")


def write_calendar_facts(conn, facts):
    """Add calendar facts the store has not seen; an earlier copy of the same fact keeps its first known_at."""
    with conn:
        return insert_rows(conn, "collect_calendar_and_events", facts, on_conflict="ignore")


def write_outcomes(conn, outcomes, values):
    """Check and write a window's outcome and its grades with their values in one transaction; never replaces."""
    for value in values:
        check_measure_value(value)
    outcomes, values = list(outcomes), list(values)
    with conn:
        insert_rows(conn, "results_outcomes", outcomes)
        attach_to_parent(conn, values, *store_schema.VALUE_TABLE_PARENTS["results_outcome_values"],
                         {outcome["outcome_id"] for outcome in outcomes})
        insert_rows(conn, "results_outcome_values", values)


# ---- Queries: calendar, snapshots, bars, fits ----------------------------------------------------------------------

def calendar_events_known(conn, trade_date, cut_at_iso):
    """The day's timed events known by the cut, one per fact (its newest known version), as (starts_at, kind)."""
    rows = conn.execute(
        "SELECT starts_at, fact_name FROM collect_calendar_and_events e WHERE fact_kind = ? "
        "AND trade_date = ? AND known_at <= ? AND known_at = (SELECT MAX(known_at) FROM collect_calendar_and_events n "
        " WHERE n.fact_kind = e.fact_kind AND n.fact_id = e.fact_id AND n.known_at <= ?) "
        "GROUP BY fact_id ORDER BY starts_at",
        (registry.SCHEDULED_EVENT_FACT, trade_date, cut_at_iso, cut_at_iso)).fetchall()
    return [(row["starts_at"], row["fact_name"]) for row in rows]


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


def snapshot_exists(conn, snapshot_id):
    """True when a snapshot with this id is stored."""
    return conn.execute("SELECT 1 FROM collect_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone() is not None


FIRST_BUILT_LIVE_READS_SQL = """
    SELECT s.* FROM collect_snapshots s
    WHERE s.snapshot_kind = 'read' AND s.row_origin IN ('live_frozen', 'live_rebuilt') AND s.snapshot_status != 'missed'
      AND s.build_attempt_number = (SELECT MIN(t.build_attempt_number) FROM collect_snapshots t
                                    WHERE t.cut_at = s.cut_at AND t.snapshot_kind = 'read'
                                      AND t.row_origin IN ('live_frozen', 'live_rebuilt')
                                      AND t.snapshot_status != 'missed')
"""


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


def stored_bar_times(conn, trade_date, row_origin):
    """The priced_at stamps of the session's bars already stored under one origin."""
    rows = conn.execute("SELECT priced_at FROM collect_market_prices "
                        "WHERE trade_date = ? AND row_origin = ? AND price_kind = 'bar'",
                        (trade_date, row_origin)).fetchall()
    return {row["priced_at"] for row in rows}


def bar_count(conn, trade_date, row_origin):
    """How many SPX 1-minute bars of a session the store holds under one origin."""
    return conn.execute("SELECT COUNT(*) FROM collect_market_prices "
                        "WHERE trade_date = ? AND row_origin = ? AND price_kind = 'bar'",
                        (trade_date, row_origin)).fetchone()[0]


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
            if minutes == session_clock.session_minutes(date.fromisoformat(day), half_days)]


def session_bars(conn, trade_date, up_to_iso=None, known_by_iso=None):
    """One SPX 1-minute bar per minute of a session, oldest first, live rows preferred, starting by the moment given
    and known by the moment given (no limit when None), each with the moment it became known."""
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


# ---- Queries: decisions, windows, grades and the scoreboard -------------------------------------------------------

OPEN_WINDOWS_SQL = ("FROM reads_decisions d WHERE decision_kind = ? AND grade_window_ends_at <= ? "
                    "AND NOT EXISTS (SELECT 1 FROM results_outcomes o WHERE o.judged_decision_id = d.decision_id)")


def decisions_with_closed_windows(conn, trade_date, cut_at_iso, decision_kind):
    """The session's decisions of a kind whose window ended by the cut and that no grade judges yet, read by read."""
    return conn.execute(f"SELECT * {OPEN_WINDOWS_SQL} AND trade_date = ? "
                        "ORDER BY cut_at, snapshot_id, horizon_minutes, variant_name",
                        (decision_kind, cut_at_iso, trade_date)).fetchall()


def sessions_with_open_windows(conn, cut_at_iso, decision_kind):
    """Every session holding a decision of the kind whose window ended by the cut and has no grade, oldest first."""
    rows = conn.execute(f"SELECT DISTINCT trade_date {OPEN_WINDOWS_SQL} ORDER BY trade_date",
                        (decision_kind, cut_at_iso)).fetchall()
    return [row["trade_date"] for row in rows]


def decisions_of_read(conn, snapshot_id, decision_kind, horizon_name):
    """A read's decisions of one kind and horizon, the base decision first, then its variants."""
    return conn.execute("SELECT * FROM reads_decisions WHERE snapshot_id = ? AND decision_kind = ? "
                        "AND horizon_name = ? ORDER BY variant_name IS NOT NULL, variant_name",
                        (snapshot_id, decision_kind, horizon_name)).fetchall()


def decision_values(conn, decision_rowid):
    """A decision's value rows."""
    return conn.execute("SELECT * FROM reads_decision_values WHERE decision_rowid = ?", (decision_rowid,)).fetchall()


def outcome_values(conn, outcome_rowid):
    """An outcome's value rows."""
    return conn.execute("SELECT * FROM results_outcome_values WHERE outcome_rowid = ?", (outcome_rowid,)).fetchall()


def lane_windows_with_grades(conn, trade_date, horizon_name, read_lane, grade_rule):
    """The session's first-built live market windows of a horizon (one lane, or every read when None), newest first,
    each with its read's cut and the base decision's grade (its rowid and decision rowid) when graded."""
    rule_name, rule_version = grade_rule
    return conn.execute(
        "SELECT w.*, s.cut_at AS read_cut_at, g.outcome_rowid AS grade_rowid, "
        " d.decision_rowid AS graded_decision_rowid "
        f"FROM results_outcomes w JOIN ({FIRST_BUILT_LIVE_READS_SQL}) s ON s.snapshot_id = w.anchor_id "
        "LEFT JOIN results_outcomes g ON g.market_outcome_id = w.outcome_id AND g.outcome_kind = ? "
        " AND g.outcome_rule_name = ? AND g.outcome_rule_version = ? "
        "LEFT JOIN reads_decisions d ON d.decision_id = g.judged_decision_id AND d.variant_name IS NULL "
        "WHERE w.trade_date = ? AND w.outcome_kind = ? AND w.horizon_name = ? AND (? IS NULL OR s.read_lane = ?) "
        " AND (g.outcome_rowid IS NULL OR d.decision_rowid IS NOT NULL) ORDER BY w.window_starts_at DESC",
        (registry.FORECAST_GRADE_KIND, rule_name, rule_version, trade_date, registry.MARKET_WINDOW_KIND, horizon_name,
         read_lane, read_lane)).fetchall()


def graded_sessions(conn, grade_rule):
    """Every session with a grade under the rule, oldest first."""
    rows = conn.execute("SELECT DISTINCT trade_date FROM results_outcomes WHERE outcome_kind = ? "
                        "AND outcome_rule_name = ? AND outcome_rule_version = ? ORDER BY trade_date",
                        (registry.FORECAST_GRADE_KIND, *grade_rule)).fetchall()
    return [row["trade_date"] for row in rows]


def grade_counts(conn, first_day, last_day, grade_rule, by_session=True):
    """Grade counts per session (or pooled), run kind, channel, variant and clipped slice: yes and graded per word,
    read from the registry's one statement of what yes means."""
    counted, parameters = [], ([] if by_session else [registry.POOLED]) + [registry.BASE_VARIANT]
    for word, (measure_name, band, yes) in registry.GRADE_WORD_MEASURE.items():
        matched = "v.measure_name = ? AND (? IS NULL OR v.subject_key = ?)"
        counted.append(f"SUM({matched} AND v.value_bool = ?) AS {word}, "
                       f"SUM({matched} AND v.value_bool IS NOT NULL) AS {word}_graded")
        subject = registry.subject_key(band=band) if band else None
        parameters.extend([measure_name, subject, subject, yes, measure_name, subject, subject])
    parameters.extend([registry.FORECAST_GRADE_KIND, *grade_rule, registry.GRADE_START_RULE,
                       registry.GRADE_START_RULE_VERSION, first_day, last_day])
    return conn.execute(
        f"SELECT {'o.trade_date' if by_session else '? AS trade_date'}, d.run_kind, d.horizon_name, "
        f"COALESCE(d.variant_name, ?) AS variant, o.was_window_clipped AS clipped, {', '.join(counted)} "
        "FROM results_outcomes o JOIN reads_decisions d ON d.decision_id = o.judged_decision_id "
        "JOIN results_outcome_values v ON v.outcome_rowid = o.outcome_rowid "
        "WHERE o.outcome_kind = ? AND o.outcome_rule_name = ? AND o.outcome_rule_version = ? "
        " AND o.grade_start_rule_name = ? AND o.grade_start_rule_version = ? AND o.trade_date BETWEEN ? AND ? "
        " AND (d.row_origin = 'history_rebuilt' OR d.snapshot_id IN "
        f" (SELECT snapshot_id FROM ({FIRST_BUILT_LIVE_READS_SQL}))) GROUP BY 1, 2, 3, 4, 5", parameters).fetchall()


# ---- Parity between live and rebuilt reads ------------------------------------------------------------------------

def parity_pairs(conn, trade_date):
    """Each live-truth observation of a session beside its history_rebuilt twin (same cut, measure and subject)."""
    columns = registry.VALUE_COLUMNS
    return conn.execute(
        "SELECT ls.cut_at, l.measure_name, l.subject_key, l.source_feed_name, l.blank_reason AS live_blank, "
        " r.blank_reason AS rebuilt_blank, "
        + ", ".join(f"l.{column} AS live_{column}, r.{column} AS rebuilt_{column}" for column in columns)
        + f" FROM ({FIRST_BUILT_LIVE_READS_SQL}) ls "
        " JOIN collect_observations l ON l.snapshot_rowid = ls.snapshot_rowid "
        " JOIN collect_snapshots rs ON rs.cut_at = ls.cut_at AND rs.snapshot_kind = 'read' "
        "  AND rs.row_origin = 'history_rebuilt' "
        " JOIN collect_observations r ON r.snapshot_rowid = rs.snapshot_rowid AND r.measure_name = l.measure_name "
        "  AND r.subject_key = l.subject_key "
        "WHERE ls.trade_date = ?", (trade_date,)).fetchall()


def parity_result(conn, trade_date):
    """Compared count, the mismatches that count, and the tolerated ones (tape and scan files land late)."""
    mismatches, tolerated, compared = [], [], 0
    for pair in parity_pairs(conn, trade_date):
        if pair["rebuilt_blank"] == "not_known_at_read":
            continue
        compared += 1
        if pair_matches(pair):
            continue
        (tolerated if pair["source_feed_name"] in TOLERATED_PARITY_FEEDS else mismatches).append(dict(pair))
    return compared, mismatches, tolerated


def pair_matches(pair):
    """True when a live value and its rebuilt twin agree (numbers within the stored precision, the rest exactly)."""
    live, rebuilt = pair["live_value_number"], pair["rebuilt_value_number"]
    if live is not None or rebuilt is not None:
        both = live is not None and rebuilt is not None
        return both and abs(live - rebuilt) <= NUMBER_TOLERANCE * max(1.0, abs(live))
    return all(pair[f"live_{column}"] == pair[f"rebuilt_{column}"] for column in registry.VALUE_COLUMNS) \
        and pair["live_blank"] == pair["rebuilt_blank"]


def parity_side_value(pair, side):
    """One side ('live' or 'rebuilt') of a parity pair as text: its value, or blank:<reason>."""
    value = observation_value({column: pair[f"{side}_{column}"] for column in registry.VALUE_COLUMNS})
    return value if value is not None else f"blank:{pair[f'{side}_blank']}"
