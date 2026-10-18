# The SQLite store: the tables built so far (hand-kept here, following the 15-table design in docs/data_map.md), the
# row shapers, and every write and read. The writer refuses unregistered measures, blanks without a reason, wrong value
# columns, out-of-range numbers and inputs newer than the cut; a read's snapshot, observations and decisions land in
# one transaction.
import functools
import hashlib
import json
import sqlite3
import subprocess
from datetime import date

from engine import paths, registry, session_clock

SCHEMA_VERSION = 4
BUSY_TIMEOUT_SECONDS = 30
VALUE_COLUMNS = ("value_number", "value_bool", "value_text", "value_at")
STORED_DECIMALS = 6
MONEY_DECIMALS = 2
NUMBER_TOLERANCE = 10 ** -STORED_DECIMALS
TOLERATED_PARITY_FEEDS = ("options_tape", "options_scan")
INSERT_VERBS = {"fail": "INSERT", "replace": "INSERT OR REPLACE", "ignore": "INSERT OR IGNORE"}
TABLE_COLUMNS = {}
ORIGIN_PREFERENCE_SQL = "CASE row_origin WHEN 'live_frozen' THEN 0 WHEN 'live_rebuilt' THEN 1 ELSE 2 END"
MEASURE_ROW_TABLES = ("collect_observations", "reads_decision_values", "results_outcome_values")


# ---- Schema: every table's columns rendered from the registry's word lists, numbered steps, open and seed ----------

def check_list(options):
    """The quoted, comma-joined word list a CHECK constraint takes."""
    return ", ".join(f"'{word}'" for word in options)


VALUE_COLUMNS_SQL = "value_number REAL, value_bool INTEGER, value_text TEXT, value_at TEXT,"
ONE_VALUE_OR_BLANK_SQL = """CHECK ((value_number IS NOT NULL) + (value_bool IS NOT NULL) + (value_text IS NOT NULL)
           + (value_at IS NOT NULL) + (blank_reason IS NOT NULL) = 1)"""

TABLE_COLUMNS_SQL = {
    "registry_measures": f"""
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, plain_meaning_text TEXT NOT NULL,
    pillar_name TEXT NOT NULL, stored_in_table_name TEXT NOT NULL, subject_kinds_text TEXT NOT NULL,
    value_type TEXT NOT NULL, value_column TEXT NOT NULL CHECK (value_column IN ({check_list(VALUE_COLUMNS)})),
    unit TEXT NOT NULL, allowed_words_text TEXT, allowed_minimum_number REAL, allowed_maximum_number REAL,
    signal_lane TEXT NOT NULL CHECK (signal_lane IN ({check_list(registry.LANES)})),
    information_group_name TEXT NOT NULL, overlap_group_name TEXT, positive_value_means TEXT NOT NULL,
    neutral_value_number REAL, is_history_derived INTEGER NOT NULL, source_feed_name TEXT,
    blank_means_text TEXT NOT NULL, max_carry_forward_seconds REAL, coverage_twin_measure_name TEXT,
    available_since_trade_date TEXT, available_from_minutes_after_open REAL, defined_at TEXT NOT NULL,
    PRIMARY KEY (measure_name, measure_version)""",
    "registry_components": """
    component_kind TEXT NOT NULL, component_name TEXT NOT NULL, component_version TEXT NOT NULL,
    revision_number INTEGER NOT NULL, plain_meaning_text TEXT NOT NULL, signal_lane TEXT NOT NULL,
    parameters_json_text TEXT NOT NULL, parameters_sha256 TEXT NOT NULL,
    starting_trust_weight_portion REAL NOT NULL, evidence_status TEXT NOT NULL, approval_status TEXT NOT NULL,
    storage_scope TEXT NOT NULL, released_at TEXT NOT NULL,
    PRIMARY KEY (component_kind, component_name, component_version, revision_number)""",
    "collect_snapshots": f"""
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
    CHECK (newest_input_market_at IS NULL OR newest_input_market_at <= cut_at)""",
    "collect_observations": f"""
    observation_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES collect_snapshots (snapshot_id),
    trade_date TEXT NOT NULL, cut_at TEXT NOT NULL, known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, subject_key TEXT NOT NULL, subject_points REAL,
    {VALUE_COLUMNS_SQL}
    value_origin TEXT NOT NULL CHECK (value_origin IN ({check_list(registry.VALUE_ORIGINS)})),
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    source_feed_name TEXT,
    feed_status TEXT CHECK (feed_status IS NULL OR feed_status IN ({check_list(registry.FEED_STATUSES)})),
    feed_age_seconds REAL, newest_input_market_at TEXT, newest_input_received_at TEXT,
    {ONE_VALUE_OR_BLANK_SQL},
    CHECK (newest_input_market_at IS NULL OR newest_input_market_at <= cut_at)""",
    "collect_market_prices": f"""
    price_record_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, instrument_symbol TEXT NOT NULL,
    instrument_kind TEXT NOT NULL, price_kind TEXT NOT NULL, bar_minutes REAL, priced_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    known_at_rule TEXT NOT NULL CHECK (known_at_rule IN ({check_list(registry.KNOWN_AT_RULES)})),
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})), value_origin TEXT NOT NULL,
    source_feed_name TEXT NOT NULL, feed_status TEXT NOT NULL, quote_unit TEXT NOT NULL,
    open_in_quote_units REAL, high_in_quote_units REAL, low_in_quote_units REAL, close_in_quote_units REAL,
    volume_count INTEGER, quote_status TEXT NOT NULL, blank_reasons_text TEXT""",
    "collect_calendar_and_events": f"""
    fact_record_id TEXT PRIMARY KEY,
    fact_kind TEXT NOT NULL CHECK (fact_kind IN ({check_list(registry.FACT_KINDS)})),
    fact_id TEXT NOT NULL, fact_version TEXT NOT NULL, trade_date TEXT NOT NULL, starts_at TEXT,
    known_at TEXT NOT NULL, fact_name TEXT, calendar_tier TEXT, fact_status TEXT, session_closes_at TEXT,
    source_reference_text TEXT, row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    written_at TEXT NOT NULL""",
    "reads_decisions": f"""
    decision_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ({check_list(registry.STORAGE_SCOPES)})),
    decision_kind TEXT NOT NULL,
    parent_decision_id TEXT REFERENCES reads_decisions (decision_id), input_decision_ids_text TEXT,
    snapshot_id TEXT NOT NULL REFERENCES collect_snapshots (snapshot_id), read_id TEXT, cut_at TEXT NOT NULL,
    features_cutoff_at TEXT NOT NULL, known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    run_kind TEXT NOT NULL CHECK (run_kind IN ({check_list(registry.RUN_KINDS)})),
    variant_name TEXT, producer_kind TEXT NOT NULL, producer_name TEXT NOT NULL, producer_version TEXT NOT NULL,
    builder_code_commit TEXT, horizon_name TEXT, horizon_minutes REAL, graded_price_measure TEXT,
    grade_start_rule_version TEXT, grade_window_starts_at TEXT, grade_window_ends_at TEXT,
    subject_key TEXT NOT NULL, revision_number INTEGER NOT NULL,
    decision_status TEXT NOT NULL CHECK (decision_status IN ({check_list(registry.DECISION_STATUSES)})),
    decision_status_reason TEXT, decision_word TEXT, written_at TEXT NOT NULL""",
    "reads_decision_values": f"""
    decision_value_id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES reads_decisions (decision_id),
    trade_date TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ({check_list(registry.STORAGE_SCOPES)})),
    decision_kind TEXT NOT NULL, known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, subject_key TEXT NOT NULL, subject_points REAL,
    value_role TEXT NOT NULL CHECK (value_role IN ({check_list(registry.VALUE_ROLES)})),
    {VALUE_COLUMNS_SQL}
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    trust_weight_applied_portion REAL,
    {ONE_VALUE_OR_BLANK_SQL}""",
    "results_outcomes": f"""
    outcome_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ({check_list(registry.STORAGE_SCOPES)})),
    outcome_kind TEXT NOT NULL CHECK (outcome_kind IN ({check_list(registry.OUTCOME_KINDS)})),
    anchor_id TEXT NOT NULL, judged_decision_id TEXT REFERENCES reads_decisions (decision_id),
    market_outcome_id TEXT REFERENCES results_outcomes (outcome_id), read_id TEXT, horizon_name TEXT,
    graded_price_measure TEXT, grade_start_rule_version TEXT, window_starts_at TEXT NOT NULL,
    window_ends_at TEXT NOT NULL, was_window_clipped INTEGER NOT NULL,
    outcome_rule_name TEXT NOT NULL, outcome_rule_version TEXT NOT NULL,
    label_status TEXT NOT NULL CHECK (label_status IN ({check_list(registry.LABEL_STATUSES)})),
    label_status_reason TEXT, label_resolved_at TEXT NOT NULL, does_count_toward_skill INTEGER NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    build_attempt_number INTEGER NOT NULL, builder_code_commit TEXT, written_at TEXT NOT NULL""",
    "results_outcome_values": f"""
    outcome_value_id TEXT PRIMARY KEY, outcome_id TEXT NOT NULL REFERENCES results_outcomes (outcome_id),
    trade_date TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ({check_list(registry.STORAGE_SCOPES)})),
    outcome_kind TEXT NOT NULL CHECK (outcome_kind IN ({check_list(registry.OUTCOME_KINDS)})),
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, subject_key TEXT NOT NULL, subject_points REAL,
    {VALUE_COLUMNS_SQL}
    value_status TEXT NOT NULL CHECK (value_status IN ({check_list(registry.VALUE_STATUSES)})),
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    label_resolved_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    {ONE_VALUE_OR_BLANK_SQL}""",
}
TABLE_INDEXES_SQL = {
    "collect_snapshots": ("CREATE INDEX ix_snapshots_day_cut ON collect_snapshots (trade_date, cut_at)",
                          "CREATE INDEX ix_snapshots_origin_cut ON collect_snapshots (row_origin, cut_at)"),
    "collect_observations": (
        "CREATE INDEX ix_observations_snapshot ON collect_observations (snapshot_id)",
        "CREATE INDEX ix_observations_measure_day ON collect_observations (measure_name, trade_date)"),
    "collect_market_prices": (
        "CREATE INDEX ix_prices_symbol_time ON collect_market_prices (instrument_symbol, priced_at)",
        "CREATE INDEX ix_prices_day_origin ON collect_market_prices (trade_date, row_origin)"),
    "collect_calendar_and_events": (
        "CREATE INDEX ix_calendar_day_known ON collect_calendar_and_events (trade_date, known_at)",),
    "reads_decisions": ("CREATE INDEX ix_decisions_snapshot ON reads_decisions (snapshot_id)",
                        "CREATE INDEX ix_decisions_day_kind ON reads_decisions (trade_date, decision_kind)"),
    "reads_decision_values": (
        "CREATE INDEX ix_decision_values_decision ON reads_decision_values (decision_id)",
        "CREATE INDEX ix_decision_values_measure_day ON reads_decision_values (measure_name, trade_date)"),
    "results_outcomes": ("CREATE INDEX ix_outcomes_day_kind ON results_outcomes (trade_date, outcome_kind)",
                         "CREATE INDEX ix_outcomes_anchor ON results_outcomes (anchor_id)",
                         "CREATE INDEX ix_outcomes_judged ON results_outcomes (judged_decision_id)"),
    "results_outcome_values": (
        "CREATE INDEX ix_outcome_values_outcome ON results_outcome_values (outcome_id)",
        "CREATE INDEX ix_outcome_values_measure_day ON results_outcome_values (measure_name, trade_date)"),
}


def create_table_sql(table):
    """The exact CREATE TABLE text of a table, the text a fresh store and a rebuilt table both carry."""
    return f"CREATE TABLE {table} ({TABLE_COLUMNS_SQL[table]})"


def create_table(conn, table):
    """Create a table and its indexes."""
    conn.execute(create_table_sql(table))
    create_indexes(conn, table)


def create_indexes(conn, table):
    """Create a table's indexes."""
    for index_sql in TABLE_INDEXES_SQL.get(table, ()):
        conn.execute(index_sql)


def rebuild_table(conn, table):
    """Recreate a table with the code's text, keeping the shared columns' rows and its children; no-op when same."""
    stored = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if stored is not None and stored["sql"] == create_table_sql(table):
        return
    old_columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    conn.execute(create_table_sql(table))
    new_columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    shared = ", ".join(column for column in new_columns if column in old_columns)
    conn.execute(f"INSERT INTO {table} ({shared}) SELECT {shared} FROM {table}_old")
    conn.execute(f"DROP TABLE {table}_old")
    create_indexes(conn, table)


PHASE_1_TABLES = ("registry_measures", "registry_components", "collect_snapshots", "collect_observations",
                  "collect_market_prices", "collect_calendar_and_events")
PHASE_2_TABLES = ("reads_decisions", "reads_decision_values")
PHASE_3_TABLES = ("results_outcomes", "results_outcome_values")


def schema_step_1(conn):
    """Phase 1: the registry and the COLLECT pillar."""
    for table in PHASE_1_TABLES:
        create_table(conn, table)


def schema_step_2(conn):
    """Phase 2: the READS pillar's decision tables; the spine rebuilt so a nightly_history_fit cut is allowed."""
    rebuild_table(conn, "collect_snapshots")
    for table in PHASE_2_TABLES:
        create_table(conn, table)


def schema_step_3(conn):
    """Phase 2 fix round: the tables so far brought to the code's text (the spine gains the thin status), rows kept."""
    for table in PHASE_1_TABLES + PHASE_2_TABLES:
        rebuild_table(conn, table)


def schema_step_4(conn):
    """Phase 3: the RESULTS pillar's outcome tables (what happened in each window and how each decision graded)."""
    for table in PHASE_3_TABLES:
        create_table(conn, table)


SCHEMA_STEPS = {1: schema_step_1, 2: schema_step_2, 3: schema_step_3, 4: schema_step_4}


def open_store():
    """Open the store (creating or upgrading its tables, each step in one transaction) with the registry seeded."""
    file = paths.store_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(file), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for step in range(current + 1, SCHEMA_VERSION + 1):
        conn.execute("BEGIN")
        SCHEMA_STEPS[step](conn)
        conn.execute(f"PRAGMA user_version = {step}")
        conn.execute("COMMIT")
    conn.execute("PRAGMA legacy_alter_table=OFF")
    conn.execute("PRAGMA foreign_keys=ON")
    check_schema_matches_code(conn)
    seed_registry(conn)
    return conn


def check_schema_matches_code(conn):
    """Refuse a store whose table text differs from the code's (a word list or column changed without a schema step)."""
    for table in TABLE_COLUMNS_SQL:
        stored = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
        if stored is None or stored["sql"] != create_table_sql(table):
            raise SystemExit(f"kaze: table {table} in the store differs from the code; add a schema step")


def seed_registry(conn):
    """Write every registry row, replacing earlier copies and dropping unlisted names (never one that has rows)."""
    names = list(registry.MEASURES_BY_NAME)
    holes = ", ".join("?" for _ in names)
    with conn:
        insert_rows(conn, "registry_measures", registry.MEASURES, on_conflict="replace")
        for table in MEASURE_ROW_TABLES:
            used = conn.execute(f"SELECT DISTINCT measure_name FROM {table} WHERE measure_name NOT IN ({holes})",
                                names).fetchall()
            if used:
                raise SystemExit(f"kaze: {table} holds rows of unregistered measures {[row[0] for row in used]}")
        conn.execute(f"DELETE FROM registry_measures WHERE measure_name NOT IN ({holes})", names)
        components = registry.component_rows()
        insert_rows(conn, "registry_components", components, on_conflict="replace")
        component_names = [row["component_name"] for row in components]
        conn.execute("DELETE FROM registry_components WHERE component_name NOT IN "
                     f"({', '.join('?' for _ in component_names)})", component_names)


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


def observation_id_for(observation):
    """The observation id, built from its snapshot, measure, version, subject and value origin."""
    return (f"{observation['snapshot_id']}|{observation['measure_name']}|{observation['measure_version']}|"
            f"{observation['subject_key']}|{observation['value_origin']}")


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


def decision_value_id_for(value):
    """The decision value id, built from its decision, measure, version, subject and role."""
    return (f"{value['decision_id']}|{value['measure_name']}|{value['measure_version']}|{value['subject_key']}|"
            f"{value['value_role']}")


def outcome_id_for(outcome):
    """The outcome id: kind, anchor, judged decision, horizon, price measure, grade start, rule, origin, attempt."""
    return (f"{outcome['outcome_kind']}|{outcome['anchor_id']}|{outcome['judged_decision_id'] or '-'}|"
            f"{outcome['horizon_name'] or '-'}|{outcome['graded_price_measure'] or '-'}|"
            f"{outcome['grade_start_rule_version'] or '-'}|"
            f"{outcome['outcome_rule_name']}@{outcome['outcome_rule_version']}|-|"
            f"{outcome['row_origin']}|b{outcome['build_attempt_number']}")


def outcome_value_id_for(value):
    """The outcome value id, built from its outcome, measure, version and subject."""
    return f"{value['outcome_id']}|{value['measure_name']}|{value['measure_version']}|{value['subject_key']}"


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
    columns = dict.fromkeys(VALUE_COLUMNS)
    if value is not None:
        columns[measure["value_column"]] = int(value) if measure["value_type"] == "yes_no" else value
    return columns


def registered_measure(measure_name):
    """The registry row of a measure; refuses a name the registry lacks."""
    measure = registry.MEASURES_BY_NAME.get(measure_name)
    if measure is None:
        raise ValueError(f"{measure_name} is not a registered measure")
    return measure


def observation_row(snapshot, measure_name, value, subject_key="whole", subject_points=None, blank_reason=None,
                    source_feed_name=None, feed_status=None, feed_age_seconds=None, market_at=None, received_at=None):
    """Shape one collect_observations row for a snapshot, putting the value in the registry's column."""
    measure = registered_measure(measure_name)
    row = {
        "snapshot_id": snapshot["snapshot_id"], "trade_date": snapshot["trade_date"], "cut_at": snapshot["cut_at"],
        "known_at": snapshot["known_at"], "row_origin": snapshot["row_origin"], "measure_name": measure_name,
        "measure_version": measure["measure_version"], "subject_key": subject_key, "subject_points": subject_points,
        "value_origin": registry.LIVE_VALUE_ORIGIN, "blank_reason": blank_reason if value is None else None,
        "source_feed_name": source_feed_name or measure["source_feed_name"], "feed_status": feed_status,
        "feed_age_seconds": feed_age_seconds, "newest_input_market_at": market_at,
        "newest_input_received_at": received_at,
        **placed_value(measure, value),
    }
    row["observation_id"] = observation_id_for(row)
    return row


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
        "graded_price_measure": None, "grade_start_rule_version": None, "grade_window_starts_at": None,
        "grade_window_ends_at": None, "subject_key": "whole", "revision_number": 0,
        "decision_status": registry.DECISION_OK,
        "decision_status_reason": None, "decision_word": None, "written_at": snapshot["written_at"], **fields,
    }
    row["decision_id"] = decision_id_for(row)
    return row


def decision_value_row(decision, measure_name, value, subject_key="whole", subject_points=None,
                       value_role=registry.VALUE_ROLE, blank_reason=None, trust_weight=None):
    """Shape one reads_decision_values row for a decision, putting the value in the registry's column."""
    measure = registered_measure(measure_name)
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


def outcome_row(outcome_kind, decision, rule, written_at_iso, label_status, reason=None, **fields):
    """Shape a results_outcomes row on a decision's window: anchored on its read, resolved when it was written."""
    rule_name, rule_version = rule
    starts_at, ends_at = decision["grade_window_starts_at"], decision["grade_window_ends_at"]
    window_seconds = (session_clock.parse_iso(ends_at) - session_clock.parse_iso(starts_at)).total_seconds()
    row = {
        "outcome_kind": outcome_kind, "trade_date": decision["trade_date"], "storage_scope": decision["storage_scope"],
        "anchor_id": decision["snapshot_id"], "judged_decision_id": None, "market_outcome_id": None,
        "read_id": decision["read_id"], "horizon_name": decision["horizon_name"], "graded_price_measure": None,
        "grade_start_rule_version": decision["grade_start_rule_version"], "window_starts_at": starts_at,
        "window_ends_at": ends_at,
        "was_window_clipped": int(window_seconds < decision["horizon_minutes"] * session_clock.BAR_LENGTH_SECONDS),
        "outcome_rule_name": rule_name, "outcome_rule_version": rule_version, "label_status": label_status,
        "label_status_reason": reason, "label_resolved_at": written_at_iso,
        "does_count_toward_skill": int(label_status in registry.LABELS_THAT_COUNT),
        "row_origin": decision["row_origin"], "build_attempt_number": 1, "builder_code_commit": code_head()[0],
        "written_at": written_at_iso, **fields,
    }
    row["outcome_id"] = outcome_id_for(row)
    return row


def outcome_value_row(outcome, measure_name, value, subject_key="whole", subject_points=None, blank_reason=None):
    """Shape one results_outcome_values row for an outcome, putting the value in the registry's column."""
    measure = registered_measure(measure_name)
    blank = blank_reason if value is None else None
    row = {
        "outcome_id": outcome["outcome_id"], "trade_date": outcome["trade_date"],
        "storage_scope": outcome["storage_scope"], "outcome_kind": outcome["outcome_kind"],
        "measure_name": measure_name, "measure_version": measure["measure_version"], "subject_key": subject_key,
        "subject_points": subject_points, "blank_reason": blank,
        "value_status": registry.VALUE_STATUS_OK if value is not None
        else registry.VALUE_STATUS_FOR_BLANK.get(blank, registry.BLANK_VALUE_STATUS),
        "label_resolved_at": outcome["label_resolved_at"], "row_origin": outcome["row_origin"],
        **placed_value(measure, value),
    }
    row["outcome_value_id"] = outcome_value_id_for(row)
    return row


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


def observation_value(observation):
    """The one filled value column of an observation, or None when it is blank."""
    return next((observation[column] for column in VALUE_COLUMNS if observation[column] is not None), None)


# ---- Checks ---------------------------------------------------------------------------------------------------------

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
    kinds = set(registry.subject_parts(row["subject_key"])) or {"whole"}
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


def write_snapshot(conn, snapshot, observations, decisions=(), values=()):
    """Check and write a snapshot, its observations, decisions and values in one transaction; a rebuilt cut replaced."""
    for observation in observations:
        check_observation(observation)
    for value in values:
        check_measure_value(value)
    claim_snapshot_id(conn, snapshot)
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
        if snapshot["row_origin"] == "history_rebuilt":
            conn.execute("DELETE FROM results_outcome_values WHERE outcome_id IN "
                         "(SELECT outcome_id FROM results_outcomes WHERE anchor_id = ?)", (snapshot["snapshot_id"],))
            conn.execute("DELETE FROM results_outcomes WHERE anchor_id = ?", (snapshot["snapshot_id"],))
            conn.execute("DELETE FROM reads_decision_values WHERE decision_id IN "
                         "(SELECT decision_id FROM reads_decisions WHERE snapshot_id = ?)", (snapshot["snapshot_id"],))
            conn.execute("DELETE FROM reads_decisions WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.execute("DELETE FROM collect_observations WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
            conn.execute("DELETE FROM collect_snapshots WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
        insert_rows(conn, "collect_snapshots", [snapshot])
        insert_rows(conn, "collect_observations", observations)
        insert_rows(conn, "reads_decisions", list(decisions))
        insert_rows(conn, "reads_decision_values", list(values))
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


def write_calendar_facts(conn, facts):
    """Add calendar facts the store has not seen; an earlier copy of the same fact keeps its first known_at."""
    with conn:
        return insert_rows(conn, "collect_calendar_and_events", facts, on_conflict="ignore")


def write_outcomes(conn, outcomes, values):
    """Check and write a window's outcome and its grades with their values in one transaction; never replaces."""
    for value in values:
        check_measure_value(value)
    with conn:
        insert_rows(conn, "results_outcomes", list(outcomes))
        insert_rows(conn, "results_outcome_values", list(values))


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


def newest_live_read(conn, trade_date, cut_at_iso, read_lane=None):
    """The session's newest first-built live read at or before the cut, in one lane or any, or None."""
    return conn.execute(
        f"SELECT * FROM ({FIRST_BUILT_LIVE_READS_SQL}) WHERE trade_date = ? AND cut_at <= ? "
        "AND (? IS NULL OR read_lane = ?) ORDER BY cut_at DESC LIMIT 1",
        (trade_date, cut_at_iso, read_lane, read_lane)).fetchone()


def session_has_reads(conn, trade_date):
    """True once the session holds any read snapshot, live or rebuilt."""
    return conn.execute("SELECT 1 FROM collect_snapshots WHERE trade_date = ? AND snapshot_kind = 'read' "
                        "AND snapshot_status != 'missed' LIMIT 1", (trade_date,)).fetchone() is not None


def snapshot_observations(conn, snapshot_id):
    """A snapshot's observation rows."""
    return conn.execute("SELECT * FROM collect_observations WHERE snapshot_id = ?", (snapshot_id,)).fetchall()


def read_price_at(conn, snapshot_id):
    """A read's price and the start of the bar it came from (its spx_price_points observation), or (None, None)."""
    row = conn.execute("SELECT value_number, newest_input_market_at FROM collect_observations "
                       "WHERE snapshot_id = ? AND measure_name = 'spx_price_points'", (snapshot_id,)).fetchone()
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


def session_bars(conn, trade_date, up_to_iso=None):
    """One SPX 1-minute bar per minute of a session, oldest first, live rows preferred, starting by the moment given."""
    rows = conn.execute(
        "SELECT priced_at, open_in_quote_units AS open, high_in_quote_units AS high, low_in_quote_units AS low, "
        " close_in_quote_units AS close FROM collect_market_prices "
        "WHERE trade_date = ? AND instrument_symbol = ? AND price_kind = 'bar' AND (? IS NULL OR priced_at <= ?) "
        f"ORDER BY priced_at, {ORIGIN_PREFERENCE_SQL}", (trade_date, registry.SPX_SYMBOL, up_to_iso, up_to_iso))
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

def decisions_with_closed_windows(conn, trade_date, cut_at_iso, decision_kind):
    """The session's decisions of a kind whose window ended by the cut and that no grade judges yet, read by read."""
    return conn.execute(
        "SELECT * FROM reads_decisions d WHERE trade_date = ? AND decision_kind = ? AND grade_window_ends_at <= ? "
        "AND NOT EXISTS (SELECT 1 FROM results_outcomes o WHERE o.judged_decision_id = d.decision_id) "
        "ORDER BY cut_at, snapshot_id, horizon_minutes, variant_name",
        (trade_date, decision_kind, cut_at_iso)).fetchall()


def decisions_of_read(conn, snapshot_id, decision_kind, horizon_name):
    """A read's decisions of one kind and horizon, the base decision first, then its variants."""
    return conn.execute("SELECT * FROM reads_decisions WHERE snapshot_id = ? AND decision_kind = ? "
                        "AND horizon_name = ? ORDER BY variant_name IS NOT NULL, variant_name",
                        (snapshot_id, decision_kind, horizon_name)).fetchall()


def decision_values(conn, decision_id):
    """A decision's value rows."""
    return conn.execute("SELECT * FROM reads_decision_values WHERE decision_id = ?", (decision_id,)).fetchall()


def outcome_values(conn, outcome_id):
    """An outcome's value rows."""
    return conn.execute("SELECT * FROM results_outcome_values WHERE outcome_id = ?", (outcome_id,)).fetchall()


def live_windows_of_day(conn, trade_date, horizon_name, read_lane, grade_rule):
    """The session's first-built live market windows in a lane and horizon, newest first, with the base grade's ids."""
    rule_name, rule_version = grade_rule
    return conn.execute(
        "SELECT w.*, s.cut_at AS read_cut_at, g.outcome_id AS grade_outcome_id, "
        " g.judged_decision_id AS graded_decision_id FROM results_outcomes w "
        f"JOIN ({FIRST_BUILT_LIVE_READS_SQL}) s ON s.snapshot_id = w.anchor_id "
        "LEFT JOIN results_outcomes g ON g.market_outcome_id = w.outcome_id AND g.outcome_kind = ? "
        " AND g.outcome_rule_name = ? AND g.outcome_rule_version = ? AND g.judged_decision_id IN "
        " (SELECT decision_id FROM reads_decisions WHERE snapshot_id = w.anchor_id AND variant_name IS NULL) "
        "WHERE w.trade_date = ? AND w.outcome_kind = ? AND w.horizon_name = ? AND s.read_lane = ? "
        "ORDER BY w.window_starts_at DESC",
        (registry.FORECAST_GRADE_KIND, rule_name, rule_version, trade_date, registry.MARKET_WINDOW_KIND, horizon_name,
         read_lane)).fetchall()


def graded_sessions(conn, grade_rule):
    """Every session with a grade under the rule, oldest first."""
    rows = conn.execute("SELECT DISTINCT trade_date FROM results_outcomes WHERE outcome_kind = ? "
                        "AND outcome_rule_name = ? AND outcome_rule_version = ? ORDER BY trade_date",
                        (registry.FORECAST_GRADE_KIND, *grade_rule)).fetchall()
    return [row["trade_date"] for row in rows]


GRADE_WORD_SQL = {
    "ends_inside": "v.measure_name = 'was_inside_band' AND v.subject_key = ? AND v.value_bool = 1",
    "average_inside": "v.measure_name = 'was_inside_band' AND v.subject_key = ? AND v.value_bool = 1",
    "never_left": "v.measure_name = 'did_leave_rung' AND v.value_bool = 0",
}


def grade_counts(conn, first_day, last_day, grade_rule, by_session=True):
    """Grade counts per session (or pooled), run kind, channel, variant and clipped slice: yes and graded per word."""
    bands = {"ends_inside": registry.subject_key(band=registry.BAND),
             "average_inside": registry.subject_key(band=registry.AVERAGE_BAND)}
    counted, parameters = [], ([] if by_session else [last_day]) + [registry.BASE_VARIANT]
    for word, yes_sql in GRADE_WORD_SQL.items():
        graded_sql = yes_sql.rsplit(" AND ", 1)[0] + " AND v.value_bool IS NOT NULL"
        counted.append(f"SUM({yes_sql}) AS {word}, SUM({graded_sql}) AS {word}_graded")
        parameters.extend([bands[word]] * 2 if word in bands else [])
    parameters.extend([registry.FORECAST_GRADE_KIND, *grade_rule, registry.GRADE_START_RULE, first_day, last_day])
    return conn.execute(
        f"SELECT {'o.trade_date' if by_session else '? AS trade_date'}, d.run_kind, d.horizon_name, "
        f"COALESCE(d.variant_name, ?) AS variant, o.was_window_clipped AS clipped, {', '.join(counted)} "
        "FROM results_outcomes o JOIN reads_decisions d ON d.decision_id = o.judged_decision_id "
        "JOIN results_outcome_values v ON v.outcome_id = o.outcome_id "
        "WHERE o.outcome_kind = ? AND o.outcome_rule_name = ? AND o.outcome_rule_version = ? "
        " AND o.grade_start_rule_version = ? AND o.trade_date BETWEEN ? AND ? "
        " AND (d.row_origin = 'history_rebuilt' OR d.snapshot_id IN "
        f" (SELECT snapshot_id FROM ({FIRST_BUILT_LIVE_READS_SQL}))) GROUP BY 1, 2, 3, 4, 5", parameters).fetchall()


# ---- Parity between live and rebuilt reads ------------------------------------------------------------------------

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
