# The SQLite store: the tables built so far (hand-kept here, following the 15-table design in docs/data_map.md), the
# row shapers, and every write and read. The writer refuses unregistered measures, blanks without a reason, wrong value
# columns, out-of-range numbers and inputs newer than the cut.
import hashlib
import json
import sqlite3

from engine import paths, registry

SCHEMA_VERSION = 2
BUSY_TIMEOUT_SECONDS = 30
VALUE_COLUMNS = ("value_number", "value_bool", "value_text", "value_at")
STORED_DECIMALS = 6
MONEY_DECIMALS = 2
NUMBER_TOLERANCE = 10 ** -STORED_DECIMALS
TOLERATED_PARITY_FEEDS = ("options_tape", "options_scan")
INSERT_VERBS = {"fail": "INSERT", "replace": "INSERT OR REPLACE", "ignore": "INSERT OR IGNORE"}
TABLE_COLUMNS = {}
ORIGIN_PREFERENCE_SQL = "CASE row_origin WHEN 'live_frozen' THEN 0 WHEN 'live_rebuilt' THEN 1 ELSE 2 END"
DECISION_STORAGE_SCOPE = "shared"
RUN_KIND_FOR_ORIGIN = {"live_frozen": "live", "live_rebuilt": "live", "history_rebuilt": "history_replay"}


def check_list(options):
    """The quoted, comma-joined word list a CHECK constraint takes."""
    return ", ".join(f"'{word}'" for word in options)


# The spine's columns, shared by step 1 and the step 2 rebuild that widened its snapshot_kind list.
SNAPSHOT_COLUMNS_SQL = f"""
    snapshot_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL,
    snapshot_kind TEXT NOT NULL CHECK (snapshot_kind IN ({check_list(registry.SNAPSHOT_KINDS)})),
    read_id TEXT, read_lane TEXT, read_lane_cadence_minutes REAL, scheduled_slot_eastern TEXT,
    slot_status TEXT NOT NULL CHECK (slot_status IN ({check_list(registry.SLOT_STATUSES)})), slot_lateness_minutes REAL,
    market_moment_id TEXT NOT NULL, is_training_grid_read INTEGER NOT NULL, read_moment_set_by TEXT NOT NULL,
    cut_at TEXT NOT NULL, newest_input_market_at TEXT, known_at TEXT NOT NULL,
    known_at_rule TEXT NOT NULL CHECK (known_at_rule IN ({check_list(registry.KNOWN_AT_RULES)})),
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    build_attempt_number INTEGER NOT NULL, builder_name TEXT NOT NULL, builder_version TEXT NOT NULL,
    builder_code_commit TEXT, was_code_tree_dirty INTEGER, component_versions_json_text TEXT NOT NULL,
    source_files_json_text TEXT, build_started_at TEXT, build_seconds REAL, snapshot_content_sha256 TEXT,
    feeds_not_ok_count INTEGER, blank_values_count INTEGER,
    snapshot_status TEXT NOT NULL CHECK (snapshot_status IN ({check_list(registry.SNAPSHOT_STATUSES)})),
    snapshot_status_reason TEXT, written_at TEXT NOT NULL,
    CHECK (newest_input_market_at IS NULL OR newest_input_market_at <= cut_at)"""
SNAPSHOT_INDEXES_SQL = """
CREATE INDEX ix_snapshots_day_cut ON collect_snapshots (trade_date, cut_at);
CREATE INDEX ix_snapshots_origin_cut ON collect_snapshots (row_origin, cut_at);"""

# Numbered schema steps; open_store applies every step above the file's user_version, each in one transaction.
SCHEMA_STEPS = {
    1: f"""
CREATE TABLE registry_measures (
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, plain_meaning_text TEXT NOT NULL,
    pillar_name TEXT NOT NULL, stored_in_table_name TEXT NOT NULL, subject_kinds_text TEXT NOT NULL,
    value_type TEXT NOT NULL, value_column TEXT NOT NULL CHECK (value_column IN ({check_list(VALUE_COLUMNS)})),
    unit TEXT NOT NULL, allowed_words_text TEXT, allowed_minimum_number REAL, allowed_maximum_number REAL,
    signal_lane TEXT NOT NULL CHECK (signal_lane IN ({check_list(registry.LANES)})),
    information_group_name TEXT NOT NULL, overlap_group_name TEXT, positive_value_means TEXT NOT NULL,
    neutral_value_number REAL, is_history_derived INTEGER NOT NULL, source_feed_name TEXT,
    blank_means_text TEXT NOT NULL, max_carry_forward_seconds REAL, coverage_twin_measure_name TEXT,
    available_since_trade_date TEXT, available_from_minutes_after_open REAL, defined_at TEXT NOT NULL,
    PRIMARY KEY (measure_name, measure_version));

CREATE TABLE registry_components (
    component_kind TEXT NOT NULL, component_name TEXT NOT NULL, component_version TEXT NOT NULL,
    revision_number INTEGER NOT NULL, plain_meaning_text TEXT NOT NULL, signal_lane TEXT NOT NULL,
    parameters_json_text TEXT NOT NULL, parameters_sha256 TEXT NOT NULL,
    starting_trust_weight_portion REAL NOT NULL, evidence_status TEXT NOT NULL, approval_status TEXT NOT NULL,
    storage_scope TEXT NOT NULL, released_at TEXT NOT NULL,
    PRIMARY KEY (component_kind, component_name, component_version, revision_number));

CREATE TABLE collect_snapshots ({SNAPSHOT_COLUMNS_SQL});
{SNAPSHOT_INDEXES_SQL}

CREATE TABLE collect_observations (
    observation_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES collect_snapshots (snapshot_id),
    trade_date TEXT NOT NULL, cut_at TEXT NOT NULL, known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, subject_key TEXT NOT NULL, subject_points REAL,
    value_number REAL, value_bool INTEGER, value_text TEXT, value_at TEXT,
    value_origin TEXT NOT NULL CHECK (value_origin IN ('live', 'after_session_corrected')),
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    source_feed_name TEXT,
    feed_status TEXT CHECK (feed_status IS NULL OR feed_status IN ({check_list(registry.FEED_STATUSES)})),
    feed_age_seconds REAL, newest_input_market_at TEXT, newest_input_received_at TEXT,
    CHECK ((value_number IS NOT NULL) + (value_bool IS NOT NULL) + (value_text IS NOT NULL) + (value_at IS NOT NULL)
           + (blank_reason IS NOT NULL) = 1),
    CHECK (newest_input_market_at IS NULL OR newest_input_market_at <= cut_at));
CREATE INDEX ix_observations_snapshot ON collect_observations (snapshot_id);
CREATE INDEX ix_observations_measure_day ON collect_observations (measure_name, trade_date);

CREATE TABLE collect_market_prices (
    price_record_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, instrument_symbol TEXT NOT NULL,
    instrument_kind TEXT NOT NULL, price_kind TEXT NOT NULL, bar_minutes REAL, priced_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    known_at_rule TEXT NOT NULL CHECK (known_at_rule IN ({check_list(registry.KNOWN_AT_RULES)})),
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})), value_origin TEXT NOT NULL,
    source_feed_name TEXT NOT NULL, feed_status TEXT NOT NULL, quote_unit TEXT NOT NULL,
    open_in_quote_units REAL, high_in_quote_units REAL, low_in_quote_units REAL, close_in_quote_units REAL,
    volume_count INTEGER, quote_status TEXT NOT NULL, blank_reasons_text TEXT);
CREATE INDEX ix_prices_symbol_time ON collect_market_prices (instrument_symbol, priced_at);
CREATE INDEX ix_prices_day_origin ON collect_market_prices (trade_date, row_origin);

CREATE TABLE collect_calendar_and_events (
    fact_record_id TEXT PRIMARY KEY,
    fact_kind TEXT NOT NULL CHECK (fact_kind IN ('session_date', 'scheduled_event')),
    fact_id TEXT NOT NULL, fact_version TEXT NOT NULL, trade_date TEXT NOT NULL, starts_at TEXT,
    known_at TEXT NOT NULL, fact_name TEXT, calendar_tier TEXT, fact_status TEXT, session_closes_at TEXT,
    source_reference_text TEXT, row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    written_at TEXT NOT NULL);
CREATE INDEX ix_calendar_day_known ON collect_calendar_and_events (trade_date, known_at);
""",
    # Phase 2: the READS pillar's decision tables, and the spine rebuilt so a nightly_history_fit cut is allowed
    # (SQLite cannot widen a CHECK list in place; the copy keeps every row and runs with foreign keys off).
    2: f"""
CREATE TABLE collect_snapshots_widened ({SNAPSHOT_COLUMNS_SQL});
INSERT INTO collect_snapshots_widened SELECT * FROM collect_snapshots;
DROP TABLE collect_snapshots;
ALTER TABLE collect_snapshots_widened RENAME TO collect_snapshots;
{SNAPSHOT_INDEXES_SQL}

CREATE TABLE reads_decisions (
    decision_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ('shared', 'private')), decision_kind TEXT NOT NULL,
    parent_decision_id TEXT REFERENCES reads_decisions (decision_id), input_decision_ids_text TEXT,
    snapshot_id TEXT NOT NULL REFERENCES collect_snapshots (snapshot_id), read_id TEXT, cut_at TEXT NOT NULL,
    features_cutoff_at TEXT NOT NULL, known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    run_kind TEXT NOT NULL CHECK (run_kind IN ('live', 'test_variant', 'history_replay', 'backtest')),
    variant_name TEXT, producer_kind TEXT NOT NULL, producer_name TEXT NOT NULL, producer_version TEXT NOT NULL,
    builder_code_commit TEXT, horizon_name TEXT, horizon_minutes REAL, graded_price_measure TEXT,
    grade_start_rule_version TEXT, grade_window_starts_at TEXT, grade_window_ends_at TEXT,
    subject_key TEXT NOT NULL, revision_number INTEGER NOT NULL,
    decision_status TEXT NOT NULL CHECK (decision_status IN ('ok', 'late', 'failed', 'refused', 'blind', 'clipped',
                                                             'held_grey')),
    decision_status_reason TEXT, decision_word TEXT, written_at TEXT NOT NULL);
CREATE INDEX ix_decisions_snapshot ON reads_decisions (snapshot_id);
CREATE INDEX ix_decisions_day_kind ON reads_decisions (trade_date, decision_kind);

CREATE TABLE reads_decision_values (
    decision_value_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES reads_decisions (decision_id),
    trade_date TEXT NOT NULL, storage_scope TEXT NOT NULL CHECK (storage_scope IN ('shared', 'private')),
    decision_kind TEXT NOT NULL, known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, subject_key TEXT NOT NULL, subject_points REAL,
    value_role TEXT NOT NULL CHECK (value_role IN ('value', 'as_answered', 'after_checks', 'raw', 'applied', 'shown')),
    value_number REAL, value_bool INTEGER, value_text TEXT, value_at TEXT,
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    trust_weight_applied_portion REAL,
    CHECK ((value_number IS NOT NULL) + (value_bool IS NOT NULL) + (value_text IS NOT NULL) + (value_at IS NOT NULL)
           + (blank_reason IS NOT NULL) = 1));
CREATE INDEX ix_decision_values_decision ON reads_decision_values (decision_id);
CREATE INDEX ix_decision_values_measure_day ON reads_decision_values (measure_name, trade_date);
""",
}


def open_store():
    """Open the store (creating or upgrading its tables as needed) with the registry seeded; returns the connection."""
    file = paths.store_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(file), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for step in range(current + 1, SCHEMA_VERSION + 1):
        conn.executescript(f"BEGIN; {SCHEMA_STEPS[step]} PRAGMA user_version = {step}; COMMIT;")
    conn.execute("PRAGMA foreign_keys=ON")
    seed_registry(conn)
    return conn


def seed_registry(conn):
    """Write every registry row, replacing earlier copies of the same key and dropping names no longer registered."""
    with conn:
        insert_rows(conn, "registry_measures", registry.MEASURES, on_conflict="replace")
        conn.execute(f"DELETE FROM registry_measures WHERE measure_name NOT IN "
                     f"({', '.join('?' for _ in registry.MEASURES_BY_NAME)})", list(registry.MEASURES_BY_NAME))
        insert_rows(conn, "registry_components", registry.component_rows(), on_conflict="replace")


def table_columns(conn, table):
    """The table's column names, read once from SQLite."""
    if table not in TABLE_COLUMNS:
        TABLE_COLUMNS[table] = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
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


def snapshot_id_for(snapshot):
    """The snapshot id, built from the row's own kind, cut, origin, builder and attempt."""
    return (f"{snapshot['snapshot_kind']}|{snapshot['cut_at']}|{snapshot['row_origin']}|"
            f"{snapshot['builder_name']}@{snapshot['builder_version']}|b{snapshot['build_attempt_number']}")


def observation_id_for(observation):
    """The observation id, built from its snapshot, measure, version, subject and value origin."""
    return (f"{observation['snapshot_id']}|{observation['measure_name']}|{observation['measure_version']}|"
            f"{observation['subject_key']}|{observation['value_origin']}")


def price_record_id_for(price):
    """The price record id from instrument, kind, time, value origin and row origin (no snapshot for streaming bars)."""
    return (f"{price['instrument_symbol']}|{price['price_kind']}|{price['priced_at']}|-|"
            f"{price['value_origin']}|{price['row_origin']}")


def base_snapshot_row(snapshot_kind, cut_at_iso, trade_date, row_origin, written_at_iso, commit, dirty,
                      builder=(registry.BUILDER_NAME, registry.BUILDER_VERSION)):
    """A collect_snapshots row with the fields every kind shares; the caller adds slot and lane, the writer counts."""
    builder_name, builder_version = builder
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


def observation_row(snapshot, measure_name, value, subject_key="whole", subject_points=None, blank_reason=None,
                    source_feed_name=None, feed_status=None, feed_age_seconds=None, market_at=None, received_at=None):
    """Shape one collect_observations row for a snapshot, putting the value in the registry's column."""
    measure = registry.MEASURES_BY_NAME.get(measure_name)
    if measure is None:
        raise ValueError(f"{measure_name} is not a registered measure")
    row = {
        "snapshot_id": snapshot["snapshot_id"], "trade_date": snapshot["trade_date"], "cut_at": snapshot["cut_at"],
        "known_at": snapshot["known_at"], "row_origin": snapshot["row_origin"], "measure_name": measure_name,
        "measure_version": measure["measure_version"], "subject_key": subject_key, "subject_points": subject_points,
        "value_origin": "live", "blank_reason": blank_reason if value is None else None,
        "source_feed_name": source_feed_name or measure["source_feed_name"], "feed_status": feed_status,
        "feed_age_seconds": feed_age_seconds, "newest_input_market_at": market_at,
        "newest_input_received_at": received_at,
        **placed_value(measure, value),
    }
    row["observation_id"] = observation_id_for(row)
    return row


def placed_value(measure, value):
    """The four value columns with the value in the registry's one (yes/no stored as 0/1), or all empty when blank."""
    columns = dict.fromkeys(VALUE_COLUMNS)
    if value is not None:
        columns[measure["value_column"]] = int(value) if measure["value_type"] == "yes_no" else value
    return columns


def decision_id_for(decision):
    """The decision id: kind, snapshot, producer, run:variant, horizon, graded price, structure, subject, revision."""
    return (f"{decision['decision_kind']}|{decision['snapshot_id']}|"
            f"{decision['producer_name']}@{decision['producer_version']}|"
            f"{decision['run_kind']}:{decision['variant_name'] or 'live'}|{decision['horizon_name'] or '-'}|"
            f"{decision['graded_price_measure'] or '-'}|-|{decision['subject_key']}|r{decision['revision_number']}")


def decision_value_id_for(value):
    """The decision value id, built from its decision, measure, version, subject and role."""
    return (f"{value['decision_id']}|{value['measure_name']}|{value['measure_version']}|{value['subject_key']}|"
            f"{value['value_role']}")


def decision_row(snapshot, decision_kind, producer, fields):
    """Shape a reads_decisions row for a snapshot: the shared columns from the snapshot, the rest from fields."""
    producer_kind, producer_name, producer_version = producer
    row = {
        "decision_kind": decision_kind, "trade_date": snapshot["trade_date"], "storage_scope": DECISION_STORAGE_SCOPE,
        "parent_decision_id": None, "input_decision_ids_text": None, "snapshot_id": snapshot["snapshot_id"],
        "read_id": snapshot.get("read_id"), "cut_at": snapshot["cut_at"], "features_cutoff_at": snapshot["cut_at"],
        "known_at": snapshot["known_at"], "row_origin": snapshot["row_origin"],
        "run_kind": RUN_KIND_FOR_ORIGIN[snapshot["row_origin"]], "variant_name": None, "producer_kind": producer_kind,
        "producer_name": producer_name, "producer_version": producer_version,
        "builder_code_commit": snapshot["builder_code_commit"], "horizon_name": None, "horizon_minutes": None,
        "graded_price_measure": None, "grade_start_rule_version": None, "grade_window_starts_at": None,
        "grade_window_ends_at": None, "subject_key": "whole", "revision_number": 0, "decision_status": "ok",
        "decision_status_reason": None, "decision_word": None, "written_at": snapshot["written_at"], **fields,
    }
    row["decision_id"] = decision_id_for(row)
    return row


def decision_value_row(decision, measure_name, value, subject_key="whole", subject_points=None, value_role="value",
                       blank_reason=None, trust_weight=None):
    """Shape one reads_decision_values row for a decision, putting the value in the registry's column."""
    measure = registry.MEASURES_BY_NAME.get(measure_name)
    if measure is None:
        raise ValueError(f"{measure_name} is not a registered measure")
    row = {
        "decision_id": decision["decision_id"], "trade_date": decision["trade_date"],
        "storage_scope": decision["storage_scope"], "decision_kind": decision["decision_kind"],
        "known_at": decision["known_at"], "row_origin": decision["row_origin"], "measure_name": measure_name,
        "measure_version": measure["measure_version"], "subject_key": subject_key, "subject_points": subject_points,
        "value_role": value_role, "blank_reason": blank_reason if value is None else None,
        "trust_weight_applied_portion": trust_weight, **placed_value(measure, value),
    }
    row["decision_value_id"] = decision_value_id_for(row)
    return row


def write_decisions(conn, decisions, values):
    """Check and write decision headers with their values; returns how many decisions were written."""
    for value in values:
        check_measure_value(value)
    with conn:
        insert_rows(conn, "reads_decisions", decisions)
        insert_rows(conn, "reads_decision_values", values)
    return len(decisions)


def sessions_with_bars(conn):
    """Every session that has SPX bars, oldest first."""
    rows = conn.execute("SELECT DISTINCT trade_date FROM collect_market_prices "
                        "WHERE instrument_symbol = '$SPX' AND price_kind = 'bar' ORDER BY trade_date")
    return [row["trade_date"] for row in rows]


def session_bars(conn, trade_date, up_to_iso=None):
    """One SPX 1-minute bar per minute of a session, oldest first, live rows preferred, starting by the moment given."""
    rows = conn.execute(
        "SELECT priced_at, open_in_quote_units AS open, high_in_quote_units AS high, low_in_quote_units AS low, "
        " close_in_quote_units AS close FROM collect_market_prices "
        "WHERE trade_date = ? AND instrument_symbol = '$SPX' AND price_kind = 'bar' AND (? IS NULL OR priced_at <= ?) "
        f"ORDER BY priced_at, {ORIGIN_PREFERENCE_SQL}", (trade_date, up_to_iso, up_to_iso))
    bars = {}
    for row in rows:
        bars.setdefault(row["priced_at"], row)
    return list(bars.values())


def newest_fit_known(conn, trade_date, cut_at_iso):
    """The newest history fit a read on this day may use: serving this session or an earlier one, known by the cut."""
    return conn.execute(
        "SELECT * FROM collect_snapshots WHERE snapshot_kind = 'nightly_history_fit' AND trade_date <= ? "
        f"AND known_at <= ? ORDER BY trade_date DESC, known_at DESC, {ORIGIN_PREFERENCE_SQL} LIMIT 1",
        (trade_date, cut_at_iso)).fetchone()


def sessions_served_by_a_fit(conn):
    """The sessions that already have a history fit serving them."""
    rows = conn.execute("SELECT DISTINCT trade_date FROM collect_snapshots WHERE snapshot_kind = 'nightly_history_fit'")
    return {row["trade_date"] for row in rows}


def snapshot_observations(conn, snapshot_id):
    """A snapshot's observation rows."""
    return conn.execute("SELECT * FROM collect_observations WHERE snapshot_id = ?", (snapshot_id,)).fetchall()


def bar_price_row(bar, trade_date, row_origin, priced_at_iso, known_at_iso, known_at_rule):
    """A collect_market_prices row for one SPX 1-minute bar (Mirai's index volume is a known-wrong 0, left blank)."""
    row = {
        "trade_date": trade_date, "instrument_symbol": "$SPX", "instrument_kind": "index", "price_kind": "bar",
        "bar_minutes": 1, "priced_at": priced_at_iso, "known_at": known_at_iso, "known_at_rule": known_at_rule,
        "row_origin": row_origin, "value_origin": "live", "source_feed_name": "spx_bars", "feed_status": "ok",
        "quote_unit": "points", "open_in_quote_units": bar["open"], "high_in_quote_units": bar["high"],
        "low_in_quote_units": bar["low"], "close_in_quote_units": bar["close"], "quote_status": "ok",
        "blank_reasons_text": "volume_count=known_wrong_same_day",
    }
    row["price_record_id"] = price_record_id_for(row)
    return row


def rounded(value):
    """A float at the store's precision; anything else (None, a count, a word) unchanged."""
    return round(value, STORED_DECIMALS) if isinstance(value, float) else value


def observation_value(observation):
    """The one filled value column of an observation, or None when it is blank."""
    return next((observation[column] for column in VALUE_COLUMNS if observation[column] is not None), None)


def check_measure_value(row):
    """Refuse a value row that breaks the registry: unknown subject, blank without reason, bad column, range, word."""
    name = row["measure_name"]
    measure = registry.MEASURES_BY_NAME[name]
    filled = [column for column in VALUE_COLUMNS if row.get(column) is not None]
    if len(filled) > 1:
        raise ValueError(f"{name}: more than one value column filled")
    if filled and filled[0] != measure["value_column"]:
        raise ValueError(f"{name}: value belongs in {measure['value_column']}, not {filled[0]}")
    if not filled and row["blank_reason"] not in registry.BLANK_REASONS:
        raise ValueError(f"{name}: blank without a listed reason ({row['blank_reason']})")
    key = row["subject_key"]
    kinds = {"whole"} if key == "whole" else {part.split("=", 1)[0] for part in key.split("&")}
    if not kinds <= set(measure["subject_kinds_text"].split("|")):
        raise ValueError(f"{name}: subject {key} is not among {measure['subject_kinds_text']}")
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
    value = observation_value(observation)
    if isinstance(value, float):
        value = round(value, STORED_DECIMALS)
    return f"{observation['measure_name']}|{observation['subject_key']}|{value}|{observation['blank_reason']}"


def write_snapshot(conn, snapshot, observations):
    """Check and write a snapshot; a stored live cut gets the next attempt, a rebuilt one is replaced."""
    for observation in observations:
        check_observation(observation)
    if snapshot["row_origin"] == "history_rebuilt":
        with conn:
            conn.execute("DELETE FROM reads_decision_values WHERE decision_id IN "
                         "(SELECT decision_id FROM reads_decisions WHERE snapshot_id = ?)", (snapshot["snapshot_id"],))
            conn.execute("DELETE FROM reads_decisions WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.execute("DELETE FROM collect_observations WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.execute("DELETE FROM collect_snapshots WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
    else:
        while snapshot_exists(conn, snapshot["snapshot_id"]):
            snapshot["build_attempt_number"] += 1
            snapshot["row_origin"] = "live_rebuilt"
            snapshot["snapshot_id"] = snapshot_id_for(snapshot)
    for observation in observations:
        observation["snapshot_id"] = snapshot["snapshot_id"]
        observation["row_origin"] = snapshot["row_origin"]
        observation["observation_id"] = observation_id_for(observation)
    lines = sorted(observation_fingerprint(observation) for observation in observations)
    snapshot["snapshot_content_sha256"] = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    snapshot["blank_values_count"] = sum(1 for o in observations if o["blank_reason"] is not None)
    statuses = {o["source_feed_name"]: o["feed_status"] for o in observations if o["feed_status"] is not None}
    snapshot["feeds_not_ok_count"] = sum(1 for status in statuses.values() if status != "ok")
    newest = [o["newest_input_market_at"] for o in observations if o["newest_input_market_at"] is not None]
    snapshot["newest_input_market_at"] = max(newest) if newest else None
    with conn:
        insert_rows(conn, "collect_snapshots", [snapshot])
        insert_rows(conn, "collect_observations", observations)
    return snapshot["snapshot_id"]


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


def stored_bar_times(conn, trade_date, row_origin):
    """The priced_at stamps of the session's bars already stored under one origin."""
    rows = conn.execute("SELECT priced_at FROM collect_market_prices "
                        "WHERE trade_date = ? AND row_origin = ? AND price_kind = 'bar'",
                        (trade_date, row_origin)).fetchall()
    return {row["priced_at"] for row in rows}


def write_calendar_facts(conn, facts):
    """Add calendar facts the store has not seen; an earlier copy of the same fact keeps its first known_at."""
    with conn:
        return insert_rows(conn, "collect_calendar_and_events", facts, on_conflict="ignore")


def calendar_events_known(conn, trade_date, cut_at_iso):
    """The day's timed events known by the cut, one per fact (its newest known version), as (starts_at, kind)."""
    rows = conn.execute(
        "SELECT starts_at, fact_name FROM collect_calendar_and_events e WHERE fact_kind = 'scheduled_event' "
        "AND trade_date = ? AND known_at <= ? AND known_at = (SELECT MAX(known_at) FROM collect_calendar_and_events n "
        " WHERE n.fact_kind = e.fact_kind AND n.fact_id = e.fact_id AND n.known_at <= ?) "
        "GROUP BY fact_id ORDER BY starts_at", (trade_date, cut_at_iso, cut_at_iso)).fetchall()
    return [(row["starts_at"], row["fact_name"]) for row in rows]


def has_calendar_facts(conn):
    """True once the store holds any calendar fact (the first copy stamps facts with the calendar's own built date)."""
    return conn.execute("SELECT 1 FROM collect_calendar_and_events LIMIT 1").fetchone() is not None


def calendar_known_at(conn, cut_at_iso):
    """When the calendar version in force at the cut became known, or None when the store holds no calendar yet."""
    return conn.execute("SELECT MAX(known_at) FROM collect_calendar_and_events WHERE known_at <= ?",
                        (cut_at_iso,)).fetchone()[0]


def snapshot_exists(conn, snapshot_id):
    """True when a snapshot with this id is stored."""
    return conn.execute("SELECT 1 FROM collect_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone() is not None


FIRST_BUILT_LIVE_READS_SQL = """
    SELECT s.snapshot_id, s.cut_at, s.trade_date, s.source_files_json_text FROM collect_snapshots s
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


def parity_pairs(conn, trade_date):
    """Each live-truth observation of a session beside its history_rebuilt twin (same cut, measure and subject)."""
    return conn.execute(
        "SELECT l.cut_at, l.measure_name, l.subject_key, l.source_feed_name, l.blank_reason AS live_blank, "
        " r.blank_reason AS rebuilt_blank, "
        + ", ".join(f"l.{column} AS live_{column}, r.{column} AS rebuilt_{column}" for column in VALUE_COLUMNS)
        + " FROM collect_observations l JOIN collect_observations r "
        " ON l.cut_at = r.cut_at AND l.measure_name = r.measure_name AND l.subject_key = r.subject_key "
        f"WHERE l.trade_date = ? AND l.snapshot_id IN (SELECT snapshot_id FROM ({FIRST_BUILT_LIVE_READS_SQL})) "
        "AND r.row_origin = 'history_rebuilt'", (trade_date,)).fetchall()


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
    return all(pair[f"live_{column}"] == pair[f"rebuilt_{column}"] for column in VALUE_COLUMNS) \
        and pair["live_blank"] == pair["rebuilt_blank"]


def parity_side_value(pair, side):
    """One side ('live' or 'rebuilt') of a parity pair as text: its value, or blank:<reason>."""
    value = observation_value({column: pair[f"{side}_{column}"] for column in VALUE_COLUMNS})
    return value if value is not None else f"blank:{pair[f'{side}_blank']}"


def bar_count(conn, trade_date, row_origin):
    """How many SPX 1-minute bars of a session the store holds under one origin."""
    return conn.execute("SELECT COUNT(*) FROM collect_market_prices "
                        "WHERE trade_date = ? AND row_origin = ? AND price_kind = 'bar'",
                        (trade_date, row_origin)).fetchone()[0]
