# The SQLite store: creates the 15 tables from store_schema.sql, seeds the registry, and holds every write and read.
# The writer refuses unregistered measures, blanks without a reason, wrong value columns and out-of-range numbers.
import hashlib
import json
import sqlite3
from pathlib import Path

from engine import measures, paths

SCHEMA_FILE = Path(__file__).with_name("store_schema.sql")
VALUE_COLUMNS = ("value_number", "value_bool", "value_text", "value_at")
ROW_ORIGINS = ("live", "rebuilt")
NUMBER_TOLERANCE = 1e-9


def open_store(path=None):
    """Open (and on first use create) the store, with the registry seeded; returns the connection."""
    file = Path(path) if path else paths.store_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(file))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_FILE.read_text())
    seed_registry(conn)
    return conn


def seed_registry(conn):
    """Write every registry row, replacing earlier copies of the same key so edits to measures.py land."""
    insert_rows(conn, "registry_measures", measures.MEASURES, replace=True)
    insert_rows(conn, "registry_components", measures.registry_component_rows(), replace=True)
    conn.commit()


def insert_rows(conn, table, rows, replace=False, ignore=False):
    """Insert dict rows into a table by column name; returns how many rows were added."""
    if not rows:
        return 0
    columns = list(rows[0].keys())
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE" if ignore else "INSERT"
    sql = f"{verb} INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
    before = conn.total_changes
    conn.executemany(sql, [tuple(row[column] for column in columns) for row in rows])
    return conn.total_changes - before


def new_observation(snapshot, measure_name, value, subject_key="whole", feed=None, feed_status=None,
                    feed_age_seconds=None, market_at=None, received_at=None, blank_reason=None, subject_points=None):
    """Shape one collect_observations row for a snapshot, putting the value in the registry's column."""
    row = measures.MEASURES_BY_NAME.get(measure_name)
    if row is None:
        raise ValueError(f"{measure_name} is not a registered measure")
    values = dict.fromkeys(VALUE_COLUMNS)
    if value is not None:
        values[row["value_column"]] = int(value) if row["value_type"] == "yes_no" else value
    return {
        "observation_id": f"{snapshot['snapshot_id']}|{measure_name}|1|{subject_key}|live",
        "snapshot_id": snapshot["snapshot_id"], "trade_date": snapshot["trade_date"], "cut_at": snapshot["cut_at"],
        "known_at": snapshot["known_at"], "row_origin": snapshot["row_origin"], "measure_name": measure_name,
        "measure_version": 1, "subject_key": subject_key, "subject_points": subject_points, **values,
        "value_origin": "live", "blank_reason": blank_reason if value is None else None,
        "source_feed_name": feed or row["source_feed_name"], "feed_status": feed_status,
        "feed_age_seconds": feed_age_seconds, "newest_input_market_at": market_at,
        "newest_input_received_at": received_at, "stand_in_source_name": None,
    }


def check_observation(observation):
    """Refuse a row that breaks the registry: unknown measure or subject kind, blank without reason, bad column or range."""
    name = observation["measure_name"]
    row = measures.MEASURES_BY_NAME.get(name)
    if row is None:
        raise ValueError(f"{name} is not a registered measure")
    filled = [column for column in VALUE_COLUMNS if observation[column] is not None]
    if len(filled) > 1:
        raise ValueError(f"{name}: more than one value column filled")
    if filled and filled[0] != row["value_column"]:
        raise ValueError(f"{name}: value belongs in {row['value_column']}, not {filled[0]}")
    if not filled and observation["blank_reason"] not in measures.BLANK_REASONS:
        raise ValueError(f"{name}: blank without a listed reason ({observation['blank_reason']})")
    allowed_kinds = set(row["subject_kinds_text"].split("|"))
    key = observation["subject_key"]
    kinds = {"whole"} if key == "whole" else {part.split("=", 1)[0] for part in key.split("&")}
    if not kinds <= allowed_kinds:
        raise ValueError(f"{name}: subject {key} is not among {row['subject_kinds_text']}")
    number = observation["value_number"]
    if number is not None:
        low, high = row["allowed_minimum_number"], row["allowed_maximum_number"]
        if (low is not None and number < low) or (high is not None and number > high):
            raise ValueError(f"{name}: {number} is outside {low}..{high}")
    if observation["value_text"] is not None and row["allowed_words_text"]:
        if observation["value_text"] not in row["allowed_words_text"].split("|"):
            raise ValueError(f"{name}: {observation['value_text']} is not an allowed word")
    if observation["feed_status"] is not None and observation["feed_status"] not in measures.FEED_STATUSES:
        raise ValueError(f"{name}: unknown feed status {observation['feed_status']}")


def observation_fingerprint(observation):
    """The part of an observation that a rebuild must reproduce: measure, subject, value and blank reason."""
    value = next((observation[column] for column in VALUE_COLUMNS if observation[column] is not None), None)
    if isinstance(value, float):
        value = round(value, 9)
    return f"{observation['measure_name']}|{observation['subject_key']}|{value}|{observation['blank_reason']}"


def write_snapshot(conn, snapshot, observations):
    """Check every observation, fill the snapshot's health counts and hash, and write both in one transaction."""
    if snapshot["row_origin"] not in ROW_ORIGINS:
        raise ValueError(f"row_origin must be one of {ROW_ORIGINS}")
    for observation in observations:
        check_observation(observation)
    lines = sorted(observation_fingerprint(observation) for observation in observations)
    snapshot["snapshot_content_sha256"] = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    snapshot["blank_values_count"] = sum(1 for o in observations if o["blank_reason"] is not None)
    snapshot["blank_values_without_reason_count"] = 0
    statuses = {o["source_feed_name"]: o["feed_status"] for o in observations if o["feed_status"] is not None}
    snapshot["feeds_not_ok_count"] = sum(1 for status in statuses.values() if status != "ok")
    with conn:
        conn.execute("DELETE FROM collect_observations WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
        conn.execute("DELETE FROM collect_snapshots WHERE snapshot_id = ?", (snapshot["snapshot_id"],))
        insert_rows(conn, "collect_snapshots", [snapshot])
        insert_rows(conn, "collect_observations", observations)
    return snapshot["snapshot_id"]


def write_bars(conn, bars):
    """Append 1-minute bar rows to collect_market_prices, skipping ones already there; returns how many were new."""
    with conn:
        return insert_rows(conn, "collect_market_prices", bars, ignore=True)


def latest_snapshot(conn, snapshot_kind="read"):
    """The newest snapshot of a kind by cut time, or None."""
    return conn.execute("SELECT * FROM collect_snapshots WHERE snapshot_kind = ? ORDER BY cut_at DESC, written_at DESC LIMIT 1",
                        (snapshot_kind,)).fetchone()


def observations_of(conn, snapshot_id):
    """Every observation row of a snapshot, in measure and subject order."""
    return conn.execute("SELECT * FROM collect_observations WHERE snapshot_id = ? ORDER BY measure_name, subject_key",
                        (snapshot_id,)).fetchall()


def snapshots_on(conn, trade_date, row_origin=None):
    """The read snapshots of a session, oldest first, optionally one origin only."""
    sql = "SELECT * FROM collect_snapshots WHERE trade_date = ? AND snapshot_kind = 'read'"
    args = [trade_date]
    if row_origin:
        sql += " AND row_origin = ?"
        args.append(row_origin)
    return conn.execute(sql + " ORDER BY cut_at", args).fetchall()


def days_with_live_and_rebuilt_reads(conn):
    """Sessions that have at least one live read and one rebuilt read at the same cut."""
    rows = conn.execute(
        "SELECT DISTINCT l.trade_date FROM collect_snapshots l JOIN collect_snapshots r "
        "ON l.cut_at = r.cut_at AND l.snapshot_kind = 'read' AND r.snapshot_kind = 'read' "
        "WHERE l.row_origin = 'live' AND r.row_origin = 'rebuilt' ORDER BY l.trade_date").fetchall()
    return [row["trade_date"] for row in rows]


def parity_mismatches(conn, trade_date):
    """Live observations of a session whose rebuilt twin (same cut, measure, subject) holds a different value."""
    rows = conn.execute(
        "SELECT l.cut_at, l.measure_name, l.subject_key, "
        " l.value_number AS live_number, r.value_number AS rebuilt_number, l.value_bool AS live_bool, r.value_bool AS rebuilt_bool,"
        " l.value_text AS live_text, r.value_text AS rebuilt_text, l.value_at AS live_at, r.value_at AS rebuilt_at,"
        " l.blank_reason AS live_blank, r.blank_reason AS rebuilt_blank "
        "FROM collect_observations l JOIN collect_observations r "
        " ON l.cut_at = r.cut_at AND l.measure_name = r.measure_name AND l.subject_key = r.subject_key "
        "WHERE l.trade_date = ? AND l.row_origin = 'live' AND r.row_origin = 'rebuilt'", (trade_date,)).fetchall()
    mismatches = []
    for row in rows:
        if row["rebuilt_blank"] == "not_known_at_read":
            continue
        if not values_match(row):
            mismatches.append(dict(row))
    return len(rows), mismatches


def values_match(pair):
    """True when a live value and its rebuilt twin agree (numbers within a hair, everything else exactly)."""
    live, rebuilt = pair["live_number"], pair["rebuilt_number"]
    if live is not None or rebuilt is not None:
        return live is not None and rebuilt is not None and abs(live - rebuilt) <= NUMBER_TOLERANCE * max(1.0, abs(live))
    return (pair["live_bool"], pair["live_text"], pair["live_at"], pair["live_blank"]) == \
           (pair["rebuilt_bool"], pair["rebuilt_text"], pair["rebuilt_at"], pair["rebuilt_blank"])


def bar_count(conn, trade_date, row_origin):
    """How many SPX 1-minute bars of a session the store holds under one origin."""
    return conn.execute("SELECT COUNT(*) FROM collect_market_prices WHERE trade_date = ? AND row_origin = ? AND price_kind = 'bar'",
                        (trade_date, row_origin)).fetchone()[0]


def json_text(value):
    """Canonical JSON for the text columns that hold a small map."""
    return json.dumps(value, sort_keys=True)
