# What the store's tables are and how a file is brought to the current version: every table's text rendered from the
# registry's word lists, the indexes, the numbered schema steps and the open-time check that a store matches the code.
# Header tables (snapshots, decisions, outcomes) carry the design's text ids; the long value tables hang off them by
# integer rowid and keep one unique index on the design's own grain.
from engine import registry

SCHEMA_VERSION = 6
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
    "registry_components": """
    component_kind TEXT NOT NULL, component_name TEXT NOT NULL, component_version TEXT NOT NULL,
    revision_number INTEGER NOT NULL, plain_meaning_text TEXT NOT NULL, signal_lane TEXT NOT NULL,
    parameters_json_text TEXT NOT NULL, parameters_sha256 TEXT NOT NULL,
    starting_trust_weight_portion REAL NOT NULL, evidence_status TEXT NOT NULL, approval_status TEXT NOT NULL,
    storage_scope TEXT NOT NULL, released_at TEXT NOT NULL,
    PRIMARY KEY (component_kind, component_name, component_version, revision_number)""",
    "collect_snapshots": f"""
    snapshot_rowid INTEGER PRIMARY KEY, snapshot_id TEXT NOT NULL UNIQUE, trade_date TEXT NOT NULL,
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
    decision_rowid INTEGER PRIMARY KEY, decision_id TEXT NOT NULL UNIQUE, trade_date TEXT NOT NULL,
    storage_scope TEXT NOT NULL CHECK (storage_scope IN ({check_list(registry.STORAGE_SCOPES)})),
    decision_kind TEXT NOT NULL,
    parent_decision_id TEXT REFERENCES reads_decisions (decision_id), input_decision_ids_text TEXT,
    snapshot_id TEXT NOT NULL REFERENCES collect_snapshots (snapshot_id), read_id TEXT, cut_at TEXT NOT NULL,
    features_cutoff_at TEXT NOT NULL, known_at TEXT NOT NULL,
    row_origin TEXT NOT NULL CHECK (row_origin IN ({check_list(registry.ROW_ORIGINS)})),
    run_kind TEXT NOT NULL CHECK (run_kind IN ({check_list(registry.RUN_KINDS)})),
    variant_name TEXT, producer_kind TEXT NOT NULL, producer_name TEXT NOT NULL, producer_version TEXT NOT NULL,
    builder_code_commit TEXT, horizon_name TEXT, horizon_minutes REAL, graded_price_measure TEXT,
    grade_start_rule_name TEXT, grade_start_rule_version TEXT, grade_window_starts_at TEXT, grade_window_ends_at TEXT,
    subject_key TEXT NOT NULL, revision_number INTEGER NOT NULL,
    decision_status TEXT NOT NULL CHECK (decision_status IN ({check_list(registry.DECISION_STATUSES)})),
    decision_status_reason TEXT, decision_word TEXT, written_at TEXT NOT NULL""",
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
}
# Each long value table with its header table and the two columns that join them: the header's text id (what the
# value rows carried before step 5) and the integer rowid they carry now.
VALUE_TABLE_PARENTS = {
    "collect_observations": ("collect_snapshots", "snapshot_id", "snapshot_rowid"),
    "reads_decision_values": ("reads_decisions", "decision_id", "decision_rowid"),
    "results_outcome_values": ("results_outcomes", "outcome_id", "outcome_rowid"),
}
PHASE_1_TABLES = ("registry_measures", "registry_components", "collect_snapshots", "collect_observations",
                  "collect_market_prices", "collect_calendar_and_events")
PHASE_2_TABLES = ("reads_decisions", "reads_decision_values")
PHASE_3_TABLES = ("results_outcomes", "results_outcome_values")


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


def rebuild_table(conn, table, parent=None):
    """Recreate a table with the code's text, keeping the shared columns' rows (joined to the parent's rowid when a
    value table moves from the parent's text id to its rowid) and its children; no-op when the text is the same."""
    stored = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if stored is not None and stored["sql"] == create_table_sql(table):
        return
    old_columns = table_columns(conn, table)
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    conn.execute(create_table_sql(table))
    shared = [column for column in table_columns(conn, table) if column in old_columns]
    columns, picked, source = list(shared), [f"old.{column}" for column in shared], f"{table}_old old"
    if parent is not None:
        parent_table, parent_id, parent_rowid = parent
        if parent_rowid not in old_columns:
            columns.append(parent_rowid)
            picked.append(f"parent.{parent_rowid}")
            source += f" JOIN {parent_table} parent ON parent.{parent_id} = old.{parent_id}"
    conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) SELECT {', '.join(picked)} FROM {source}")
    conn.execute(f"DROP TABLE {table}_old")
    create_indexes(conn, table)


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


def schema_step_5(conn):
    """Phase 3 fix round: rowid keys on the three value tables, the grade-start rule's name beside its version on
    decisions and outcomes (and in the outcome ids), the renamed grade words, every table at the code's text."""
    start_rule = f"{registry.GRADE_START_RULE}@{registry.GRADE_START_RULE_VERSION}"
    for table in ("reads_decisions", "results_outcomes"):
        if "grade_start_rule_name" not in table_columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN grade_start_rule_name TEXT")
        conn.execute(f"UPDATE {table} SET grade_start_rule_name = grade_start_rule_version, "
                     "grade_start_rule_version = ? WHERE grade_start_rule_version = ?",
                     (registry.GRADE_START_RULE_VERSION, registry.GRADE_START_RULE))
    if "outcome_id" in table_columns(conn, "results_outcome_values"):
        conn.execute("UPDATE results_outcomes SET outcome_id = replace(outcome_id, ?, ?), "
                     "market_outcome_id = replace(market_outcome_id, ?, ?)",
                     (f"|{registry.GRADE_START_RULE}|", f"|{start_rule}|") * 2)
        conn.execute("UPDATE results_outcome_values SET outcome_id = replace(outcome_id, ?, ?)",
                     (f"|{registry.GRADE_START_RULE}|", f"|{start_rule}|"))
        conn.execute("UPDATE results_outcome_values SET measure_name = 'stayed_inside_rung', "
                     "value_bool = 1 - value_bool WHERE measure_name = 'did_leave_rung'")
        conn.execute("UPDATE results_outcome_values SET measure_name = 'band_used_ratio' "
                     "WHERE measure_name = 'band_used_portion'")
    for table in PHASE_1_TABLES + PHASE_2_TABLES + PHASE_3_TABLES:
        rebuild_table(conn, table, VALUE_TABLE_PARENTS.get(table))


def schema_step_6(conn):
    """Phase 3 fix round: rebuilt outcomes with bars re-stamped with the moment their last-arriving bar was known
    (the earliest stored copy of each minute), the rule the grader has written since; live rows keep their write."""
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


SCHEMA_STEPS = {1: schema_step_1, 2: schema_step_2, 3: schema_step_3, 4: schema_step_4, 5: schema_step_5,
                6: schema_step_6}


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
