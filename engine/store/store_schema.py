# What the store's tables are and how a file is brought to the current version: every table's text rendered from the
# registry's word lists, the indexes, the numbered schema steps and the open-time check that a store matches the code.
# Header tables (snapshots, decisions, outcomes) carry the design's text ids; the long value tables hang off them by
# integer rowid and keep one unique index on the design's own grain.
from engine.store import components, registry

SCHEMA_VERSION = 12
VALUE_COLUMNS_SQL = "value_number REAL, value_bool INTEGER, value_text TEXT, value_at TEXT,"
ONE_VALUE_OR_BLANK_SQL = """CHECK ((value_number IS NOT NULL) + (value_bool IS NOT NULL) + (value_text IS NOT NULL)
           + (value_at IS NOT NULL) + (blank_reason IS NOT NULL) = 1)"""


def check_list(options):
    """The quoted, comma-joined word list a CHECK constraint takes."""
    return ", ".join(f"'{word}'" for word in options)


TABLE_COLUMNS_SQL = {
    "registry_measures": f"""
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, plain_meaning_text TEXT NOT NULL,
    pillar_name TEXT NOT NULL, stored_in_table_name TEXT NOT NULL, subject_kinds_text TEXT NOT NULL,
    value_type TEXT NOT NULL, value_column TEXT NOT NULL CHECK (value_column IN ({check_list(registry.VALUE_COLUMNS)})),
    unit TEXT NOT NULL, allowed_words_text TEXT, allowed_minimum_number REAL, allowed_maximum_number REAL,
    signal_lane TEXT NOT NULL CHECK (signal_lane IN ({check_list(registry.LANES)})),
    information_group_name TEXT NOT NULL, overlap_group_name TEXT, positive_value_means TEXT NOT NULL,
    neutral_value_number REAL, is_history_derived INTEGER NOT NULL, source_feed_name TEXT,
    blank_means_text TEXT NOT NULL, max_carry_forward_seconds REAL, coverage_twin_measure_name TEXT,
    available_since_trade_date TEXT, available_from_minutes_after_open REAL, defined_at TEXT NOT NULL,
    PRIMARY KEY (measure_name, measure_version)""",
    "registry_components": f"""
    component_kind TEXT NOT NULL, component_name TEXT NOT NULL, component_version TEXT NOT NULL,
    revision_number INTEGER NOT NULL, plain_meaning_text TEXT NOT NULL,
    parameters_json_text TEXT NOT NULL, parameters_sha256 TEXT NOT NULL,
    starting_trust_weight_portion REAL NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ({check_list(registry.STORAGE_SCOPES)})),
    released_at TEXT NOT NULL,
    PRIMARY KEY (component_kind, component_name, component_version, revision_number)""",
    "collect_snapshots": f"""
    snapshot_rowid INTEGER PRIMARY KEY, snapshot_id TEXT NOT NULL UNIQUE, trade_date TEXT NOT NULL,
    snapshot_kind TEXT NOT NULL CHECK (snapshot_kind IN ({check_list(registry.SNAPSHOT_KINDS)})),
    read_id TEXT, read_lane TEXT, read_lane_cadence_minutes REAL, scheduled_slot_eastern TEXT,
    slot_status TEXT NOT NULL CHECK (slot_status IN ({check_list(registry.SLOT_STATUSES)})), slot_lateness_minutes REAL,
    is_training_grid_read INTEGER NOT NULL, read_moment_set_by TEXT NOT NULL,
    cut_at TEXT NOT NULL, newest_input_market_at TEXT, known_at TEXT NOT NULL,
    known_at_rule TEXT NOT NULL CHECK (known_at_rule IN ({check_list(registry.KNOWN_AT_RULES)})),
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    build_attempt_number INTEGER NOT NULL, builder_name TEXT NOT NULL, builder_version TEXT NOT NULL,
    builder_code_commit TEXT, was_code_tree_dirty INTEGER, component_versions_json_text TEXT NOT NULL,
    source_files_json_text TEXT, build_seconds REAL, snapshot_content_sha256 TEXT,
    feeds_not_ok_count INTEGER, blank_values_count INTEGER,
    snapshot_status TEXT NOT NULL CHECK (snapshot_status IN ({check_list(registry.SNAPSHOT_STATUSES)})),
    snapshot_status_reason TEXT, written_at TEXT NOT NULL,
    CHECK (newest_input_market_at IS NULL OR newest_input_market_at <= cut_at)""",
    "collect_observations": f"""
    observation_rowid INTEGER PRIMARY KEY,
    snapshot_rowid INTEGER NOT NULL REFERENCES collect_snapshots (snapshot_rowid),
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, subject_key TEXT NOT NULL, subject_points REAL,
    {VALUE_COLUMNS_SQL}
    value_origin TEXT NOT NULL CHECK (value_origin IN ({check_list(registry.VALUE_ORIGINS)})),
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    source_feed_name TEXT,
    feed_status TEXT CHECK (feed_status IS NULL OR feed_status IN ({check_list(registry.FEED_STATUSES)})),
    feed_age_seconds REAL, newest_input_market_at TEXT, newest_input_received_at TEXT,
    {ONE_VALUE_OR_BLANK_SQL}""",
    "collect_market_prices": f"""
    price_record_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, instrument_symbol TEXT NOT NULL,
    instrument_kind TEXT NOT NULL, price_kind TEXT NOT NULL, bar_minutes REAL, priced_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    known_at_rule TEXT NOT NULL CHECK (known_at_rule IN ({check_list(registry.KNOWN_AT_RULES)})),
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})), value_origin TEXT NOT NULL,
    source_feed_name TEXT NOT NULL, feed_status TEXT NOT NULL, quote_unit TEXT NOT NULL,
    open_in_quote_units REAL, high_in_quote_units REAL, low_in_quote_units REAL, close_in_quote_units REAL""",
    "collect_calendar_and_events": f"""
    fact_record_id TEXT PRIMARY KEY,
    fact_kind TEXT NOT NULL CHECK (fact_kind IN ({check_list(registry.FACT_KINDS)})),
    fact_id TEXT NOT NULL, fact_version TEXT NOT NULL, trade_date TEXT NOT NULL, starts_at TEXT,
    known_at TEXT NOT NULL, fact_name TEXT, calendar_tier TEXT, fact_status TEXT, session_closes_at TEXT,
    source_reference_text TEXT, row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    written_at TEXT NOT NULL""",
    "reads_decisions": f"""
    decision_rowid INTEGER PRIMARY KEY, decision_id TEXT NOT NULL UNIQUE, trade_date TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ({check_list(registry.STORAGE_SCOPES)})),
    decision_kind TEXT NOT NULL,
    parent_decision_id TEXT REFERENCES reads_decisions (decision_id),
    snapshot_id TEXT NOT NULL REFERENCES collect_snapshots (snapshot_id), read_id TEXT, cut_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    run_kind TEXT NOT NULL CHECK (run_kind IN ({check_list(registry.RUN_KINDS)})),
    variant_name TEXT, producer_kind TEXT NOT NULL, producer_name TEXT NOT NULL, producer_version TEXT NOT NULL,
    builder_code_commit TEXT, horizon_name TEXT, horizon_minutes REAL, graded_price_measure TEXT,
    grade_start_rule_name TEXT, grade_start_rule_version TEXT, grade_window_starts_at TEXT, grade_window_ends_at TEXT,
    subject_key TEXT NOT NULL, revision_number INTEGER NOT NULL,
    decision_status TEXT NOT NULL CHECK (decision_status IN ({check_list(registry.DECISION_STATUSES)})),
    decision_status_reason TEXT, written_at TEXT NOT NULL""",
    "reads_decision_values": f"""
    decision_value_rowid INTEGER PRIMARY KEY,
    decision_rowid INTEGER NOT NULL REFERENCES reads_decisions (decision_rowid),
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, subject_key TEXT NOT NULL, subject_points REAL,
    value_role TEXT NOT NULL CHECK (value_role IN ({check_list(registry.VALUE_ROLES)})),
    {VALUE_COLUMNS_SQL}
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    trust_weight_applied_portion REAL,
    {ONE_VALUE_OR_BLANK_SQL}""",
    "results_outcomes": f"""
    outcome_rowid INTEGER PRIMARY KEY, outcome_id TEXT NOT NULL UNIQUE, trade_date TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ({check_list(registry.STORAGE_SCOPES)})),
    outcome_kind TEXT NOT NULL CHECK (outcome_kind IN ({check_list(registry.OUTCOME_KINDS)})),
    anchor_id TEXT NOT NULL, judged_decision_id TEXT REFERENCES reads_decisions (decision_id),
    market_outcome_id TEXT REFERENCES results_outcomes (outcome_id), read_id TEXT, horizon_name TEXT,
    graded_price_measure TEXT, grade_start_rule_name TEXT, grade_start_rule_version TEXT,
    window_starts_at TEXT NOT NULL, window_ends_at TEXT NOT NULL, was_window_clipped INTEGER NOT NULL,
    outcome_rule_name TEXT NOT NULL, outcome_rule_version TEXT NOT NULL,
    label_status TEXT NOT NULL CHECK (label_status IN ({check_list(registry.LABEL_STATUSES)})),
    label_status_reason TEXT, label_resolved_at TEXT NOT NULL, does_count_toward_skill INTEGER NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    build_attempt_number INTEGER NOT NULL, builder_code_commit TEXT, written_at TEXT NOT NULL""",
    "results_outcome_values": f"""
    outcome_value_rowid INTEGER PRIMARY KEY,
    outcome_rowid INTEGER NOT NULL REFERENCES results_outcomes (outcome_rowid),
    measure_name TEXT NOT NULL, measure_version INTEGER NOT NULL, subject_key TEXT NOT NULL, subject_points REAL,
    {VALUE_COLUMNS_SQL}
    value_status TEXT NOT NULL CHECK (value_status IN ({check_list(registry.VALUE_STATUSES)})),
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    {ONE_VALUE_OR_BLANK_SQL}""",
    "results_evaluations": f"""
    evaluation_rowid INTEGER PRIMARY KEY, evaluation_row_id TEXT NOT NULL UNIQUE, evaluation_id TEXT NOT NULL,
    evaluation_kind TEXT NOT NULL CHECK (evaluation_kind IN ({check_list(components.EVALUATION_KINDS)})),
    trade_date TEXT NOT NULL,
    evaluated_kind TEXT NOT NULL CHECK (evaluated_kind IN ({check_list(components.EVALUATED_KINDS)})),
    evaluated_name TEXT NOT NULL, evaluated_version TEXT NOT NULL, judge_name TEXT NOT NULL,
    judge_version TEXT NOT NULL, signal_lane TEXT NOT NULL CHECK (signal_lane IN ({check_list(registry.LANES)})),
    overlap_group_name TEXT, horizon_name TEXT NOT NULL, graded_price_measure TEXT NOT NULL,
    slice_key TEXT NOT NULL, metric_name TEXT NOT NULL, metric_value REAL,
    metric_text TEXT CHECK (metric_text IS NULL OR metric_text IN ({check_list(components.VERDICTS)})),
    blank_reason TEXT CHECK (blank_reason IS NULL OR blank_reason IN ({check_list(registry.BLANK_REASONS)})),
    metric_interval_low REAL, metric_interval_high REAL,
    interval_method TEXT
      CHECK (interval_method IS NULL OR interval_method IN ({check_list(components.INTERVAL_METHODS)})),
    baseline_name TEXT NOT NULL CHECK (baseline_name IN ({check_list(components.BASELINES)})),
    baseline_metric_value REAL, sessions_count INTEGER NOT NULL, independent_windows_count INTEGER NOT NULL,
    rows_count INTEGER NOT NULL, live_frozen_rows_count INTEGER NOT NULL, data_from_trade_date TEXT,
    data_through_trade_date TEXT NOT NULL, applies_from_at TEXT NOT NULL, was_pre_registered INTEGER NOT NULL,
    test_family_name TEXT NOT NULL, test_family_size_count INTEGER NOT NULL, detail_text TEXT, known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})), builder_code_commit TEXT,
    written_at TEXT NOT NULL,
    CHECK ((metric_value IS NOT NULL) + (blank_reason IS NOT NULL) = 1)""",
}
TABLE_INDEXES_SQL = {
    "collect_snapshots": ("CREATE INDEX ix_snapshots_day_cut ON collect_snapshots (trade_date, cut_at)",
                          "CREATE INDEX ix_snapshots_origin_cut ON collect_snapshots (row_origin, cut_at)"),
    "collect_observations": ("CREATE UNIQUE INDEX ux_observations_key ON collect_observations "
                             "(snapshot_rowid, measure_name, measure_version, subject_key, value_origin)",),
    "collect_market_prices": (
        "CREATE INDEX ix_prices_symbol_time ON collect_market_prices (instrument_symbol, priced_at)",
        "CREATE INDEX ix_prices_day_origin ON collect_market_prices (trade_date, row_origin)"),
    "collect_calendar_and_events": (
        "CREATE INDEX ix_calendar_day_known ON collect_calendar_and_events (trade_date, known_at)",),
    "reads_decisions": ("CREATE INDEX ix_decisions_snapshot ON reads_decisions (snapshot_id)",
                        "CREATE INDEX ix_decisions_day_kind ON reads_decisions (trade_date, decision_kind)"),
    "reads_decision_values": ("CREATE UNIQUE INDEX ux_decision_values_key ON reads_decision_values "
                              "(decision_rowid, measure_name, measure_version, subject_key, value_role)",),
    "results_outcomes": ("CREATE INDEX ix_outcomes_day_kind ON results_outcomes (trade_date, outcome_kind)",
                         "CREATE INDEX ix_outcomes_anchor ON results_outcomes (anchor_id)",
                         "CREATE INDEX ix_outcomes_judged ON results_outcomes (judged_decision_id)"),
    "results_outcome_values": ("CREATE UNIQUE INDEX ux_outcome_values_key ON results_outcome_values "
                               "(outcome_rowid, measure_name, measure_version, subject_key)",),
    "results_evaluations": ("CREATE INDEX ix_evaluations_day_kind ON results_evaluations (trade_date, evaluation_kind)",
                            "CREATE INDEX ix_evaluations_evaluated ON results_evaluations "
                            "(evaluated_kind, evaluated_name, horizon_name, known_at)"),
}
# Each long value table with its header table and the two columns that join them: the header's text id (what the
# value rows carried before step 5) and the integer rowid they carry now.
VALUE_TABLE_PARENTS = {
    "collect_observations": ("collect_snapshots", "snapshot_id", "snapshot_rowid"),
    "reads_decision_values": ("reads_decisions", "decision_id", "decision_rowid"),
    "results_outcome_values": ("results_outcomes", "outcome_id", "outcome_rowid"),
}
# The tables by pillar: the two registries with the collect pillar, the reads pillar, the results pillar's outcomes
# and its ledger.
REGISTRY_AND_COLLECT_TABLES = ("registry_measures", "registry_components", "collect_snapshots", "collect_observations",
                               "collect_market_prices", "collect_calendar_and_events")
READS_TABLES = ("reads_decisions", "reads_decision_values")
RESULTS_TABLES = ("results_outcomes", "results_outcome_values")
LEDGER_TABLES = ("results_evaluations",)
ALL_TABLES = REGISTRY_AND_COLLECT_TABLES + READS_TABLES + RESULTS_TABLES + LEDGER_TABLES


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


def table_columns(conn, table):
    """The table's column names as SQLite holds them."""
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def rebuild_table(conn, table, parent=None, rewrite=None):
    """Recreate a table with the code's text, keeping the shared columns' rows; a no-op when the text is the same."""
    # A value table moving from its parent's text id to the parent's rowid is joined to the parent on the way; rewrite
    # gives a column the SQL that turns its old word into the new one (over old.<column>) as the rows are copied.
    stored = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if stored is not None and stored["sql"] == create_table_sql(table):
        return
    old_columns = table_columns(conn, table)
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    conn.execute(create_table_sql(table))
    shared = [column for column in table_columns(conn, table) if column in old_columns]
    rewrite = rewrite or {}
    columns, picked = list(shared), [rewrite.get(column, f"old.{column}") for column in shared]
    source = f"{table}_old old"
    if parent is not None:
        parent_table, parent_id, parent_rowid = parent
        if parent_rowid not in old_columns:
            columns.append(parent_rowid)
            picked.append(f"parent.{parent_rowid}")
            source += f" JOIN {parent_table} parent ON parent.{parent_id} = old.{parent_id}"
    conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) SELECT {', '.join(picked)} FROM {source}")
    conn.execute(f"DROP TABLE {table}_old")
    create_indexes(conn, table)


def rebuild_tables(conn, tables):
    """Bring each table to the code's text, rows kept (the ones already at it are left alone)."""
    for table in tables:
        rebuild_table(conn, table, VALUE_TABLE_PARENTS.get(table))


def schema_step_1(conn):
    """The registry and collect tables."""
    for table in REGISTRY_AND_COLLECT_TABLES:
        create_table(conn, table)


def schema_step_2(conn):
    """The reads tables; the snapshots table rebuilt so a nightly_history_fit cut is allowed."""
    rebuild_table(conn, "collect_snapshots")
    for table in READS_TABLES:
        create_table(conn, table)


def schema_step_3(conn):
    """The tables so far brought to the code's text (the snapshots table gains the thin status), rows kept."""
    rebuild_tables(conn, REGISTRY_AND_COLLECT_TABLES + READS_TABLES)


def schema_step_4(conn):
    """The results tables: what happened in each window and how each decision graded."""
    for table in RESULTS_TABLES:
        create_table(conn, table)


def schema_step_5(conn):
    """Rowid keys on the three value tables, the grade-start rule's name beside its version, the renamed grade words."""
    start_rule = f"{components.GRADE_START_RULE}@{components.GRADE_START_RULE_VERSION}"
    for table in ("reads_decisions", "results_outcomes"):
        if "grade_start_rule_name" not in table_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN grade_start_rule_name TEXT")
        conn.execute(f"UPDATE {table} SET grade_start_rule_name = grade_start_rule_version, "
                     "grade_start_rule_version = ? WHERE grade_start_rule_version = ?",
                     (components.GRADE_START_RULE_VERSION, components.GRADE_START_RULE))
    if "outcome_id" in table_columns(conn, "results_outcome_values"):
        conn.execute("UPDATE results_outcomes SET outcome_id = replace(outcome_id, ?, ?), "
                     "market_outcome_id = replace(market_outcome_id, ?, ?)",
                     (f"|{components.GRADE_START_RULE}|", f"|{start_rule}|") * 2)
        conn.execute("UPDATE results_outcome_values SET outcome_id = replace(outcome_id, ?, ?)",
                     (f"|{components.GRADE_START_RULE}|", f"|{start_rule}|"))
        conn.execute("UPDATE results_outcome_values SET measure_name = 'stayed_inside_rung', "
                     "value_bool = 1 - value_bool WHERE measure_name = 'did_leave_rung'")
        conn.execute("UPDATE results_outcome_values SET measure_name = 'band_used_ratio' "
                     "WHERE measure_name = 'band_used_portion'")
    rebuild_tables(conn, REGISTRY_AND_COLLECT_TABLES + READS_TABLES + RESULTS_TABLES)


def schema_step_6(conn):
    """Rebuilt outcomes re-stamped with the moment their last-arriving bar was known; live rows keep their write."""
    conn.execute(
        "UPDATE results_outcomes SET label_resolved_at = ("
        " SELECT MAX(first_known) FROM (SELECT MIN(p.known_at) AS first_known FROM collect_market_prices p"
        "  WHERE p.trade_date = results_outcomes.trade_date AND p.price_kind = 'bar' AND p.instrument_symbol = ?"
        "   AND p.priced_at >= results_outcomes.window_starts_at AND p.priced_at < results_outcomes.window_ends_at"
        "  GROUP BY p.priced_at)) "
        "WHERE row_origin = 'history_rebuilt' AND label_status IN (?, ?) AND EXISTS ("
        " SELECT 1 FROM collect_market_prices p WHERE p.trade_date = results_outcomes.trade_date"
        "  AND p.price_kind = 'bar' AND p.instrument_symbol = ? AND p.priced_at >= results_outcomes.window_starts_at"
        "  AND p.priced_at < results_outcomes.window_ends_at)",
        (registry.SPX_SYMBOL, *registry.LABELS_THAT_COUNT, registry.SPX_SYMBOL))


def schema_step_7(conn):
    """The trust ledger table; the stored fast-over-slow dial takes its name, acceleration."""
    for table in LEDGER_TABLES:
        create_table(conn, table)
    conn.execute("UPDATE reads_decision_values SET subject_key = ? WHERE subject_key = ?",
                 (registry.subject_key(adjustment=components.ACCELERATION_DIAL), "adjustment=fast_over_slow"))


def schema_step_8(conn):
    """The ledger table at the code's text (the verdict list grew: held, then held_down), rows kept."""
    rebuild_tables(conn, LEDGER_TABLES)


def schema_step_10(conn):
    """Columns and words nothing writes dropped, the stretch and acceleration measures renamed, every table rebuilt."""
    # The headline coverage measure could never be filled (its poller leaves no stamps); its blank rows go with it.
    conn.execute("DELETE FROM collect_observations WHERE measure_name = 'feed_coverage_last_60_minutes_portion'")
    for table in ("collect_observations", "reads_decision_values"):
        conn.execute(f"UPDATE {table} SET measure_name = 'stretch_multiplier' WHERE measure_name = 'band_multiplier'")
    conn.execute("UPDATE collect_observations SET measure_name = ? WHERE measure_name = 'speed_fast_over_slow_ratio'",
                 (components.ACCELERATION_MEASURE,))
    rebuild_tables(conn, ALL_TABLES)


# Step 11's vocabulary: the range lane carries how far price travels, the push_pull lane which way, and the push
# readings are the push/pull factors. Push and pull only ever describe price (a factor leaning up pushes it, one
# leaning down pulls it), never the range; the retired stored words are kept here only so an older store upgrades.
LANE_RENAMES = (("pull", "range"), ("push", "push_pull"))
MEASURE_RENAMES = (("push_lean_probability", "push_pull_lean_probability"),
                   ("push_reading_text", "push_pull_factor_text"))
SUBJECT_RENAME = ("reading=", "factor=")
STATUS_REASON_RENAME = ("push_failed", "push_pull_failed")
OLD_FACTOR_KIND = "push_reading"


def schema_step_11(conn):
    """Every stored row moved onto the range and push_pull lanes and the push/pull factors' names."""
    # The measures are written again from the code as the store opens; the ledger's lanes and kinds are rewritten on
    # the way into its rebuilt table, since the old words fail the new word lists. The parameters an earlier
    # component revision stored stay as they were released.
    conn.execute("DELETE FROM registry_measures")
    old_subject, new_subject = SUBJECT_RENAME
    for table in ("collect_observations", "reads_decision_values"):
        for old, new in MEASURE_RENAMES:
            conn.execute(f"UPDATE {table} SET measure_name = ? WHERE measure_name = ?", (new, old))
        conn.execute(f"UPDATE {table} SET subject_key = ? || substr(subject_key, ?) "
                     "WHERE substr(subject_key, 1, ?) = ?",
                     (new_subject, len(old_subject) + 1, len(old_subject), old_subject))
    old_reason, new_reason = STATUS_REASON_RENAME
    conn.execute("UPDATE collect_snapshots SET snapshot_status_reason = replace(snapshot_status_reason, ?, ?) "
                 "WHERE instr(snapshot_status_reason, ?) > 0", (old_reason, new_reason, old_reason))
    conn.execute("UPDATE registry_components SET component_kind = ? WHERE component_kind = ?",
                 (components.PUSH_PULL_FACTOR_KIND, OLD_FACTOR_KIND))
    lane = "CASE old.signal_lane " + " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in LANE_RENAMES) \
        + " ELSE old.signal_lane END"
    kind = (OLD_FACTOR_KIND, components.PUSH_PULL_FACTOR_KIND)
    rebuild_table(conn, "results_evaluations", rewrite={
        "signal_lane": lane, "evaluated_kind": "replace(old.evaluated_kind, '{}', '{}')".format(*kind),
        "evaluation_row_id": "replace(old.evaluation_row_id, '|{}:', '|{}:')".format(*kind)})
    rebuild_tables(conn, ALL_TABLES)


# Step 12's retired words: the old seven factors' lean rows, the lean test and its baseline word.
RETIRED_LEAN_MEASURES = ("push_pull_lean_probability", "push_pull_factor_text")
RETIRED_LEAN_TEST = "lean_brier_test"


def schema_step_12(conn):
    """The old seven push/pull factors' lean rows, their component rows and the lean test's ledger rows dropped: the
    push/pull pipeline stores every step of every reading instead (push_pull_raw_value and on). The ledger table is
    rebuilt at the code's text, since the lean test's baseline word left its list."""
    holes = ", ".join("?" for _ in RETIRED_LEAN_MEASURES)
    for table in ("collect_observations", "reads_decision_values"):
        conn.execute(f"DELETE FROM {table} WHERE measure_name IN ({holes})", RETIRED_LEAN_MEASURES)
    conn.execute("DELETE FROM results_evaluations WHERE judge_name = ?", (RETIRED_LEAN_TEST,))
    conn.execute("DELETE FROM registry_components WHERE component_kind = ? OR (component_kind = 'trust_test' "
                 "AND component_name = ?)", (components.PUSH_PULL_FACTOR_KIND, RETIRED_LEAN_TEST))
    rebuild_tables(conn, LEDGER_TABLES)


# Step 9 repeated step 8 for the second verdict word; both numbers stay so a stored version 8 still upgrades.
SCHEMA_STEPS = {1: schema_step_1, 2: schema_step_2, 3: schema_step_3, 4: schema_step_4, 5: schema_step_5,
                6: schema_step_6, 7: schema_step_7, 8: schema_step_8, 9: schema_step_8, 10: schema_step_10,
                11: schema_step_11, 12: schema_step_12}


def upgrade(conn):
    """Bring an open store to the code's version, each step in one transaction, with the renames legacy ALTER needs."""
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


def check_schema_matches_code(conn):
    """Refuse a store whose table text differs from the code's (a word list or column changed without a schema step)."""
    for table in TABLE_COLUMNS_SQL:
        stored = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
        if stored is None or stored["sql"] != create_table_sql(table):
            raise SystemExit(f"kaze: table {table} in the store differs from the code; add a schema step")
