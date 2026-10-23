# The SQLite store: opening a file at the code's version (store_schema.py holds the tables), the ids and row shapers,
# the checks and every write; every read lives in store_queries.py. The writer refuses unregistered measures, blanks
# without a reason, wrong value columns, out-of-range numbers and inputs newer than the cut; a read's snapshot,
# observations and decisions land in one transaction, as do a window's outcome and its grades.
import functools
import hashlib
import json
import sqlite3
import subprocess

from engine import paths, session_clock
from engine.store import components, registry, store_queries, store_schema

BUSY_TIMEOUT_SECONDS = 30
STORED_DECIMALS = 6
MONEY_DECIMALS = 2
INSERT_VERBS = {"fail": "INSERT", "replace": "INSERT OR REPLACE", "ignore": "INSERT OR IGNORE"}
TABLE_COLUMNS = {}
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
    """Write every registry row: measures replace earlier copies; a changed component gets the next revision."""
    # A measure name dropped from the code is removed unless rows still use it; a component whose parameters or
    # meaning changed under the same version gets the next revision, earlier revisions and retired names stay.
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
        newest = store_queries.newest_component_revisions(conn)
        revised = []
        for row in registry.component_rows():
            stored = newest.get((row["component_kind"], row["component_name"], row["component_version"]))
            if stored is None or stored[1:] != (row["parameters_sha256"], row["plain_meaning_text"]):
                revised.append(dict(row, revision_number=stored[0] + 1 if stored else 1))
        insert_rows(conn, "registry_components", revised)


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


def calendar_fact_id_for(fact):
    """The calendar fact record id: the fact's kind, its own id and the calendar version it came from."""
    return f"{fact['fact_kind']}|{fact['fact_id']}|{fact['fact_version']}"


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


def snapshot_row_shared(snapshot_kind, cut_at_iso, trade_date, row_origin, written_at_iso,
                        builder=(registry.BUILDER_NAME, registry.BUILDER_VERSION)):
    """The collect_snapshots columns every kind shares, with the code commit; a read adds its slot and lane."""
    builder_name, builder_version = builder
    commit, dirty = code_head()
    row = {
        "snapshot_kind": snapshot_kind, "trade_date": trade_date, "cut_at": cut_at_iso,
        "known_at": written_at_iso, "known_at_rule": "written_at", "row_origin": row_origin, "build_attempt_number": 1,
        "builder_name": builder_name, "builder_version": builder_version,
        "builder_code_commit": commit, "was_code_tree_dirty": dirty,
        "component_versions_json_text": json.dumps({builder_name: builder_version,
                                                    "measures_registry": registry.REGISTRY_VERSION}, sort_keys=True),
        "written_at": written_at_iso, "slot_status": "not_scheduled",
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
        "parent_decision_id": None, "snapshot_id": snapshot["snapshot_id"], "read_id": snapshot.get("read_id"),
        "cut_at": snapshot["cut_at"], "known_at": snapshot["known_at"], "row_origin": snapshot["row_origin"],
        "run_kind": registry.RUN_KIND_FOR_ORIGIN[snapshot["row_origin"]], "variant_name": None,
        "producer_kind": producer_kind, "producer_name": producer_name, "producer_version": producer_version,
        "builder_code_commit": snapshot["builder_code_commit"], "horizon_name": None, "horizon_minutes": None,
        "graded_price_measure": None, "grade_start_rule_name": None, "grade_start_rule_version": None,
        "grade_window_starts_at": None, "grade_window_ends_at": None, "subject_key": WHOLE, "revision_number": 0,
        "decision_status": registry.DECISION_OK, "decision_status_reason": None, "written_at": snapshot["written_at"],
        **fields,
    }
    row["decision_id"] = decision_id_for(row)
    return row


def decision_value_row(decision, measure_name, value, subject_key=WHOLE, subject_points=None,
                       value_role=registry.VALUE_ROLE, blank_reason=None, trust_weight=None):
    """Shape one reads_decision_values row; it names its decision, which the write turns into the decision's rowid."""
    return {"decision_id": decision["decision_id"], "value_role": value_role,
            "trust_weight_applied_portion": rounded(trust_weight),
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


def row_adder(shaper, parent, rows):
    """A closure that shapes one rounded value row of the parent with the shaper given and appends it to rows."""
    def add(measure_name, value, subject_key=WHOLE, blank=None, **extra):
        """One value row: blank with the reason given when the value is None; extra goes to the shaper."""
        rows.append(shaper(parent, measure_name, rounded(value), subject_key, blank_reason=blank, **extra))
    return add


def evaluation_row_id_for(evaluation):
    """The evaluation row id: run, evaluated kind, name and version, horizon, slice, metric."""
    return (f"{evaluation['evaluation_id']}|{evaluation['evaluated_kind']}:{evaluation['evaluated_name']}@"
            f"{evaluation['evaluated_version']}|{evaluation['horizon_name']}|{evaluation['slice_key']}|"
            f"{evaluation['metric_name']}")


def evaluation_row(evaluation_id, judged, judge, horizon_name, *, kind, metric_name, value, baseline, counts, run,
                   verdict=None, interval=None, baseline_value=None, slice_key=WHOLE, detail=None, family_size=1):
    """Shape a results_evaluations row: one metric of one judged component on one channel, its sample and verdict."""
    # `run` carries the dates, the moment the row is in force from and the write.
    evaluated_kind, evaluated_name, evaluated_version = judged
    judge_name, judge_version = judge
    measure = registered_measure(metric_name)
    if measure["stored_in_table_name"] != "results_evaluations":
        raise ValueError(f"{metric_name} is not an evaluation metric")
    weighted = registry.MEASURES_BY_NAME[components.WEIGHTED_MEASURE[evaluated_kind]]
    low, high = interval if interval is not None else (None, None)
    row = {
        "evaluation_id": evaluation_id, "evaluation_kind": kind, "evaluated_kind": evaluated_kind,
        "evaluated_name": evaluated_name, "evaluated_version": evaluated_version, "judge_name": judge_name,
        "judge_version": judge_version, "signal_lane": weighted["signal_lane"],
        "overlap_group_name": weighted["overlap_group_name"], "horizon_name": horizon_name,
        "graded_price_measure": components.BAND_PRICE_MEASURE[components.BAND], "slice_key": slice_key,
        "metric_name": metric_name, "metric_value": rounded(value), components.VERDICT_COLUMN: verdict,
        "blank_reason": registry.TOO_FEW_SESSIONS if value is None else None, "metric_interval_low": rounded(low),
        "metric_interval_high": rounded(high),
        "interval_method": components.BOOTSTRAP_BY_DAY if interval is not None else None, "baseline_name": baseline,
        "baseline_metric_value": rounded(baseline_value), "was_pre_registered": 1,
        "test_family_name": f"{judge_name}@{judge_version}", "test_family_size_count": family_size,
        "detail_text": detail, "row_origin": "live_frozen", "builder_code_commit": code_head()[0], **counts, **run,
    }
    row["evaluation_row_id"] = evaluation_row_id_for(row)
    return row


def bar_price_row(bar, trade_date, row_origin, priced_at_iso, known_at_iso, known_at_rule):
    """A collect_market_prices row for one SPX 1-minute bar (no volume: Mirai's index volume is a known-wrong 0)."""
    row = {
        "trade_date": trade_date, "instrument_symbol": registry.SPX_SYMBOL, "instrument_kind": "index",
        "price_kind": "bar", "bar_minutes": 1, "priced_at": priced_at_iso, "known_at": known_at_iso,
        "known_at_rule": known_at_rule, "row_origin": row_origin, "value_origin": registry.LIVE_VALUE_ORIGIN,
        "source_feed_name": "spx_bars", "feed_status": "ok", "quote_unit": "points",
        "open_in_quote_units": bar["open"], "high_in_quote_units": bar["high"], "low_in_quote_units": bar["low"],
        "close_in_quote_units": bar["close"],
    }
    row["price_record_id"] = price_record_id_for(row)
    return row


def rounded(value):
    """A float at the store's precision; anything else (None, a count, a word) unchanged."""
    return round(value, STORED_DECIMALS) if isinstance(value, float) else value


def observation_value(row):
    """The one filled value column of a long value row, or None when it is blank."""
    return next((row[column] for column in registry.VALUE_COLUMNS if row[column] is not None), None)


def observation_of(observations, measure_name, subject_key=WHOLE):
    """One observation row of a read by measure and subject, or None when the read holds none."""
    return next((row for row in observations
                 if row["measure_name"] == measure_name and row["subject_key"] == subject_key), None)


def value_key_for(measure_name, subject_key):
    """The one key a value goes by outside the store: its measure name, plus |subject when there is one."""
    return measure_name if subject_key == WHOLE else f"{measure_name}|{subject_key}"


def value_key(row):
    """A value row's key outside the store (value_key_for on its measure and subject)."""
    return value_key_for(row["measure_name"], row["subject_key"])


def values_by_key(rows):
    """{value key: value} over value rows, blanks left out."""
    return {value_key(row): observation_value(row) for row in rows if observation_value(row) is not None}


def bars_known_by(snapshot, cut_at):
    """The moment a live read's bars must have been known by (its write or its cut bar's arrival); None rebuilt."""
    # The later of the two, so a read sees every bar it stored itself and nothing a later process stored.
    if snapshot["row_origin"] == "history_rebuilt":
        return None
    return max(snapshot["written_at"], session_clock.iso_utc(session_clock.bar_deadline(cut_at)))


def add_status_reason(snapshot, reason):
    """Add a failed step's word to a snapshot row's status reason (comma-joined after any reason already there)."""
    stored = snapshot["snapshot_status_reason"]
    snapshot["snapshot_status_reason"] = f"{stored},{reason}" if stored else reason


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
        while store_queries.snapshot_exists(conn, snapshot["snapshot_id"]):
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
        snapshot["snapshot_rowid"] = store_queries.snapshot_rowid(conn, snapshot["snapshot_id"])
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
    add_status_reason(snapshot, reason)
    with conn:
        conn.execute("UPDATE collect_snapshots SET snapshot_status_reason = ? WHERE snapshot_id = ?",
                     (snapshot["snapshot_status_reason"], snapshot["snapshot_id"]))


def write_missed_snapshot(conn, snapshot):
    """Record a scheduled slot that no live read served; nothing is written if a live read exists at that cut."""
    if store_queries.live_snapshot_at(conn, snapshot["cut_at"]) is not None:
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


def write_evaluations(conn, rows):
    """Write a ledger run in one transaction, replacing every row of the same run; returns the rows written."""
    # The same run is the same judge and data-through day, so a rerun never leaves a stale row behind.
    runs = sorted({row["evaluation_id"] for row in rows})
    with conn:
        conn.execute(f"DELETE FROM results_evaluations WHERE evaluation_id IN ({', '.join('?' for _ in runs)})", runs)
        return insert_rows(conn, "results_evaluations", rows)
