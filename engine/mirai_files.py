# How to read each of Mirai's files as it stood at a moment: bars, quotes, scans, the options tape, futures, the event
# calendar, Claude's forecast, headlines and the auth watch's login line. Read-only; nothing here imports Mirai's code.
import gzip
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np

from engine import paths
from engine.session_clock import eastern_clock, parse_iso, session_open

SPX_BARS = "market/spx_price_bars_by_minute/{day}.jsonl"
WATCHLIST_QUOTES = "market/watchlist_quotes_by_minute/{day}.jsonl"
OPTIONS_SCANS = "spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl"
OPTIONS_TAPE = "market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl"
FUTURES_BARS = "market/futures_price_bars_by_minute/{day}.jsonl"
CLAUDE_FORECASTS = "spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl"
HEADLINES = "market/news_headlines_by_article/{day}.jsonl"
EVENT_CALENDAR = "market_event_calendar_by_event.json"
AUTH_WATCH_LOG = "operations/logs/auth_watch_output_log.txt"
NIGHT_STARTS_AT = "18:00"
HALF_DAY_EVENT_KIND = "HALF_DAY_CLOSE"
# Vendor trade-condition codes 130-144 mark legs of multi-leg packages, which have no clear buyer or seller side.
MULTI_LEG_CONDITIONS = range(130, 145)
CONTRACT_MULTIPLIER = 100
SCAN_STRADDLE_SHARE = 0.85
AUTH_WATCH_LINE = re.compile(r"\[auth-check\] state=(\w+) age=([\d.?]+)d left=(-?[\d.?]+)d")
AUTH_WATCH_TAIL_BYTES = 4096
TAPE_CACHE = {}
SKIPPED_LINES = {}


def day_file_path(template, day):
    """The plain or gzipped copy of a per-day Mirai file, or None when neither exists."""
    plain = paths.mirai_data(template.format(day=day))
    if plain.exists():
        return plain
    zipped = plain.with_name(plain.name + ".gz")
    return zipped if zipped.exists() else None


def open_bytes(path):
    """Open a plain or gzipped file for reading bytes."""
    return gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")


def file_bytes(template, day):
    """Byte length of the plain per-day file right now, or None when only a gzipped copy (or nothing) exists."""
    plain = paths.mirai_data(template.format(day=day))
    return plain.stat().st_size if plain.exists() else None


def read_jsonl(path, max_bytes=None):
    """Every well-formed JSON line within the first max_bytes of a file; malformed lines count in SKIPPED_LINES."""
    rows, skipped, offset = [], 0, 0
    if path is not None:
        with open_bytes(path) as handle:
            for line in handle:
                offset += len(line)
                if max_bytes is not None and offset > max_bytes:
                    break
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    skipped += 1
        SKIPPED_LINES[path] = skipped
    return rows


def lines_skipped(template, day):
    """How many malformed lines the last read of this day's file skipped, or None when the file was never read."""
    path = day_file_path(template, day)
    return SKIPPED_LINES.get(path) if path else None


def rows_through(template, day, cut, time_field="ts", max_bytes=None):
    """A day file's rows whose time field is at or before the cut, each with a parsed 'at', oldest first as written."""
    kept = []
    for row in read_jsonl(day_file_path(template, day), max_bytes):
        text = row.get(time_field)
        if not text:
            continue
        at = parse_iso(text)
        if at <= cut:
            row["at"] = at
            kept.append(row)
    return kept


def newest_row_with(rows, field):
    """The last row that carries a non-empty field, or None."""
    return next((row for row in reversed(rows) if row.get(field)), None)


def read_spx_bars(day, cut):
    """SPX 1-minute bars whose start is at or before the cut."""
    return rows_through(SPX_BARS, day, cut)


def read_quotes_rows(day, cut):
    """Watchlist rows (quotes and breadth bars) polled at or before the cut."""
    return rows_through(WATCHLIST_QUOTES, day, cut)


def read_native_scans(day, cut, max_bytes=None):
    """Options scans built from the native chain at or before the cut, within the first max_bytes of the file."""
    scans = rows_through(OPTIONS_SCANS, day, cut, max_bytes=max_bytes)
    return [scan for scan in scans if str(scan.get("gex_source", "")).startswith("native")]


def scan_straddle_points(ruler, field):
    """A straddle value from the scan's range ruler, undoing the 85% the scanner stores."""
    value = (ruler or {}).get(field)
    return value / SCAN_STRADDLE_SHARE if value else None


def read_tape_day(day):
    """The day's whole tape as numpy columns (with each line's end byte offset) plus gap markers, parsed once."""
    if day in TAPE_CACHE:
        return TAPE_CACHE[day]
    path = day_file_path(OPTIONS_TAPE, day)
    columns = {"ts_ms": [], "offset": [], "strike": [], "is_call": [], "price": [], "size": [], "bid": [], "ask": [],
               "condition": []}
    gaps, skipped, offset = [], 0, 0
    if path is not None:
        with open_bytes(path) as handle:
            for line in handle:
                offset += len(line)
                if b'"ts_ms"' not in line:
                    if b'"gap"' in line:
                        gaps.append((gap_moment(line), offset))
                    continue
                try:
                    trade = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if trade.get("ts_ms") is None or trade.get("price") is None:
                    skipped += 1
                    continue
                columns["ts_ms"].append(trade["ts_ms"])
                columns["offset"].append(offset)
                columns["strike"].append(trade.get("strike") or 0.0)
                columns["is_call"].append(trade.get("right") == "call")
                columns["price"].append(trade["price"])
                columns["size"].append(trade.get("size") or 0)
                columns["bid"].append(trade.get("bid") or 0.0)
                columns["ask"].append(trade.get("ask") or 0.0)
                columns["condition"].append(trade.get("condition") or 0)
        SKIPPED_LINES[path] = skipped
    tape = {name: np.array(values, dtype=float) for name, values in columns.items()}
    tape["present"] = path is not None
    tape["gaps"] = [gap for gap in gaps if gap[0] is not None]
    TAPE_CACHE[day] = tape
    return tape


def gap_moment(line):
    """The moment of a collector gap marker line, or None when it has no usable time."""
    try:
        text = json.loads(line).get("ts")
        return parse_iso(text) if text else None
    except ValueError:
        return None


def tape_window_totals(day, cut, minutes, max_bytes=None):
    """Trade count, call and put premium, sided bull and bear premium, gap markers and newest trade in the window."""
    tape = read_tape_day(day)
    end_ms = int(cut.timestamp() * 1000)
    start_ms = end_ms - minutes * 60 * 1000
    on_disk = tape["offset"] <= max_bytes if max_bytes is not None else np.ones(tape["offset"].shape, dtype=bool)
    inside = on_disk & (tape["ts_ms"] >= start_ms) & (tape["ts_ms"] <= end_ms)
    premium = tape["price"] * tape["size"] * CONTRACT_MULTIPLIER
    is_call = tape["is_call"] > 0
    sided = inside & ~np.isin(tape["condition"], list(MULTI_LEG_CONDITIONS)) & (tape["bid"] > 0) & (tape["ask"] > 0)
    mid = (tape["bid"] + tape["ask"]) / 2.0
    above = sided & (tape["price"] > mid)
    below = sided & (tape["price"] < mid)
    window_start = cut - timedelta(minutes=minutes)
    newest_ms = tape["ts_ms"][on_disk & (tape["ts_ms"] <= end_ms)]
    gaps = [gap_at for gap_at, gap_offset in tape["gaps"]
            if window_start <= gap_at <= cut and (max_bytes is None or gap_offset <= max_bytes)]
    return {
        "present": tape["present"],
        "trades": int(inside.sum()),
        "calls_usd": float(premium[inside & is_call].sum()),
        "puts_usd": float(premium[inside & ~is_call].sum()),
        "bull_usd": float(premium[(above & is_call) | (below & ~is_call)].sum()),
        "bear_usd": float(premium[(above & ~is_call) | (below & is_call)].sum()),
        "gaps": len(gaps),
        "newest_trade_at": (datetime.fromtimestamp(newest_ms.max() / 1000.0, tz=timezone.utc)
                            if newest_ms.size else None),
    }


def atm_option_mids(day, cut, strike, max_bytes=None):
    """Mid of the newest quoted trade at or before the cut for the call and the put at one strike."""
    tape = read_tape_day(day)
    end_ms = int(cut.timestamp() * 1000)
    usable = (tape["strike"] == float(strike)) & (tape["ts_ms"] <= end_ms) & (tape["bid"] > 0) & (tape["ask"] > 0)
    if max_bytes is not None:
        usable &= tape["offset"] <= max_bytes
    mids = {}
    for side, want_call in (("call", True), ("put", False)):
        picks = np.flatnonzero(usable & ((tape["is_call"] > 0) == want_call))
        if picks.size:
            newest = picks[np.argmax(tape["ts_ms"][picks])]
            mids[side] = float((tape["bid"][newest] + tape["ask"][newest]) / 2.0)
    return mids


def read_overnight_futures(day):
    """/ES 1-minute bars from 18:00 the evening before to the 09:30 open: high, low and newest bar start, or None."""
    night_start = eastern_clock(day - timedelta(days=1), NIGHT_STARTS_AT)
    open_at = session_open(day)
    highs, lows, newest = [], [], None
    for bar in read_jsonl(day_file_path(FUTURES_BARS, day)):
        if bar.get("symbol") != "/ES" or bar.get("bar_minutes") != 1:
            continue
        at = parse_iso(bar["ts"])
        if night_start <= at < open_at:
            highs.append(bar["high"])
            lows.append(bar["low"])
            newest = at if newest is None or at > newest else newest
    if not highs:
        return None
    return {"high": max(highs), "low": min(lows), "newest_at": newest}


def read_event_calendar():
    """Mirai's event calendar with its covered span and the file's last-changed moment; None when the file is absent."""
    path = paths.mirai_settings(EVENT_CALENDAR)
    if not path.exists():
        return None
    calendar = json.loads(path.read_text())
    calendar["changed_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    return calendar


def session_calendar():
    """The calendar and the half-day dates it lists, read once per run."""
    calendar = read_event_calendar()
    half_days = set()
    if calendar is not None:
        half_days = {date.fromisoformat(event["date"]) for event in calendar["events"]
                     if event.get("kind") == HALF_DAY_EVENT_KIND}
    return calendar, half_days


def calendar_covers(calendar, day):
    """True when the day sits inside the calendar's covered span."""
    return date.fromisoformat(calendar["covers_from"]) <= day <= date.fromisoformat(calendar["covers_through"])


def read_claude_final_forecast(day, cut):
    """Mirai's newest live final forecast written at or before the cut with a usable 30-minute answer, or None."""
    newest = None
    for row in rows_through(CLAUDE_FORECASTS, day, cut, time_field="written_at"):
        if row.get("line_type") != "final_forecast" or row.get("test_variant") not in (None, "none"):
            continue
        if ((row.get("forecast") or {}).get("next_30_minutes") or {}).get("status") != "ok":
            continue
        row["read_at"] = parse_iso(row["read_id"].split(":", 1)[1])
        newest = row
    return newest


def read_headlines(day, cut, minutes):
    """Headlines captured in the window to the cut, the newest capture time, and whether the file exists."""
    rows = rows_through(HEADLINES, day, cut, time_field="captured_at")
    window_start = cut - timedelta(minutes=minutes)
    return {"present": day_file_path(HEADLINES, day) is not None,
            "count": sum(1 for row in rows if row["at"] >= window_start),
            "newest_at": max((row["at"] for row in rows), default=None)}


def read_login_state():
    """The Schwab login as the auth watch last logged it: state, days left (None when it printed ?), the log's time."""
    path = paths.mirai_data(AUTH_WATCH_LOG)
    if not path.exists():
        return None
    with open(path, "rb") as handle:
        handle.seek(max(0, os.path.getsize(path) - AUTH_WATCH_TAIL_BYTES))
        tail = handle.read().decode("utf-8", errors="replace")
    for line in reversed(tail.splitlines()):
        match = AUTH_WATCH_LINE.search(line)
        if match:
            days_left = None if match.group(3) == "?" else float(match.group(3))
            return {"state": match.group(1), "days_left": days_left,
                    "reported_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)}
    return None
