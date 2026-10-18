# How to read each of Mirai's files as it stood at a given moment: bars, quotes, scans, the options tape, futures,
# the event calendar, Claude's forecast, headlines and the auth watch's login line. Read-only; nothing here imports Mirai's code.
import gzip
import json
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np

from engine import paths
from engine.session_clock import eastern_moment

SPX_BARS = "market/spx_price_bars_by_minute/{day}.jsonl"
WATCHLIST_QUOTES = "market/watchlist_quotes_by_minute/{day}.jsonl"
OPTIONS_SCANS = "spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl"
OPTIONS_TAPE = "market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl"
FUTURES_BARS = "market/futures_price_bars_by_minute/{day}.jsonl"
CLAUDE_FORECASTS = "spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl"
HEADLINES = "market/news_headlines_by_article/{day}.jsonl"
EVENT_CALENDAR = "market_event_calendar_by_event.json"
AUTH_WATCH_LOG = "operations/logs/auth_watch_output_log.txt"
NIGHT_START = timedelta(hours=18)
MULTI_LEG_CONDITIONS = range(130, 145)
SCAN_STRADDLE_SHARE = 0.85
LOGIN_DAYS_LEFT = re.compile(r"\[auth-check\] state=(\w+) age=([\d.]+)d left=(-?[\d.]+)d")
_TAPE_CACHE = {}


def day_file(template, day):
    """The plain or gzipped copy of a per-day file, or None when neither exists."""
    plain = paths.mirai_data(template.format(day=day))
    if plain.exists():
        return plain
    zipped = plain.with_name(plain.name + ".gz")
    return zipped if zipped.exists() else None


def read_jsonl(path):
    """Every well-formed JSON line of a file (gzipped or plain); malformed lines are skipped."""
    if path is None:
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    rows = []
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def parse_moment(text):
    """Mirai's ISO timestamps (with offset) as aware datetimes."""
    return datetime.fromisoformat(text)


def read_spx_bars(day, cut):
    """SPX 1-minute bars whose start is at or before the cut, each with a parsed 'at'; empty when the file is absent."""
    bars = []
    for bar in read_jsonl(day_file(SPX_BARS, day)):
        at = parse_moment(bar["ts"])
        if at <= cut:
            bar["at"] = at
            bars.append(bar)
    return bars


def read_quotes_rows(day, cut):
    """Watchlist rows (quotes and breadth bars) polled at or before the cut, each with a parsed 'at'."""
    rows = []
    for row in read_jsonl(day_file(WATCHLIST_QUOTES, day)):
        at = parse_moment(row["ts"])
        if at <= cut:
            row["at"] = at
            rows.append(row)
    return rows


def newest_with(rows, field):
    """The last row that carries a non-empty field, or None."""
    return next((row for row in reversed(rows) if row.get(field)), None)


def read_native_scans(day, cut):
    """Options scans built from the native chain at or before the cut, each with a parsed 'at'."""
    scans = []
    for scan in read_jsonl(day_file(OPTIONS_SCANS, day)):
        if not str(scan.get("gex_source", "")).startswith("native"):
            continue
        at = parse_moment(scan["ts"])
        if at <= cut:
            scan["at"] = at
            scans.append(scan)
    return scans


def scan_straddle_points(ruler, field):
    """A straddle value from the scan's range ruler, undoing the 85% the scanner stores."""
    value = (ruler or {}).get(field)
    return value / SCAN_STRADDLE_SHARE if value else None


def tape_day(day):
    """The day's whole options tape as numpy columns plus its gap markers, parsed once per process."""
    if day in _TAPE_CACHE:
        return _TAPE_CACHE[day]
    path = day_file(OPTIONS_TAPE, day)
    columns = {"ts_ms": [], "strike": [], "is_call": [], "price": [], "size": [], "bid": [], "ask": [], "condition": []}
    gaps = []
    if path is not None:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt") as handle:
            for line in handle:
                if '"ts_ms"' not in line:
                    if '"gap"' in line:
                        gap_at = parse_gap_line(line)
                        if gap_at is not None:
                            gaps.append(gap_at)
                    continue
                try:
                    trade = json.loads(line)
                except ValueError:
                    continue
                if trade.get("ts_ms") is None or trade.get("price") is None:
                    continue
                columns["ts_ms"].append(trade["ts_ms"])
                columns["strike"].append(trade.get("strike") or 0.0)
                columns["is_call"].append(trade.get("right") == "call")
                columns["price"].append(trade["price"])
                columns["size"].append(trade.get("size") or 0)
                columns["bid"].append(trade.get("bid") or 0.0)
                columns["ask"].append(trade.get("ask") or 0.0)
                columns["condition"].append(trade.get("condition") or 0)
    tape = {name: np.array(values, dtype=float) for name, values in columns.items()}
    tape["present"] = path is not None
    tape["gaps"] = gaps
    _TAPE_CACHE[day] = tape
    return tape


def parse_gap_line(line):
    """The moment of a collector gap marker, or None when the line has no usable time."""
    try:
        text = json.loads(line).get("ts")
        return parse_moment(text) if text else None
    except ValueError:
        return None


def tape_window(day, cut, minutes):
    """Trade count, call and put premium, sided bull and bear premium, gap markers and newest trade in the window to the cut."""
    tape = tape_day(day)
    end_ms = int(cut.timestamp() * 1000)
    start_ms = end_ms - minutes * 60 * 1000
    inside = (tape["ts_ms"] >= start_ms) & (tape["ts_ms"] <= end_ms)
    premium = tape["price"] * tape["size"] * 100.0
    is_call = tape["is_call"] > 0
    sided = inside & ~np.isin(tape["condition"], list(MULTI_LEG_CONDITIONS)) & (tape["bid"] > 0) & (tape["ask"] > 0)
    mid = (tape["bid"] + tape["ask"]) / 2.0
    above = sided & (tape["price"] > mid)
    below = sided & (tape["price"] < mid)
    bull = premium[(above & is_call) | (below & ~is_call)].sum()
    bear = premium[(above & ~is_call) | (below & is_call)].sum()
    window_start = cut - timedelta(minutes=minutes)
    all_ms = tape["ts_ms"][tape["ts_ms"] <= end_ms]
    return {
        "present": tape["present"],
        "trades": int(inside.sum()),
        "calls_usd": float(premium[inside & is_call].sum()),
        "puts_usd": float(premium[inside & ~is_call].sum()),
        "bull_usd": float(bull), "bear_usd": float(bear),
        "gaps": sum(1 for gap_at in tape["gaps"] if window_start <= gap_at <= cut),
        "newest_trade_at": datetime.fromtimestamp(all_ms.max() / 1000.0, tz=timezone.utc) if all_ms.size else None,
    }


def atm_option_mids(day, cut, strike):
    """Mid of the newest quoted trade at or before the cut for the call and the put at one strike."""
    tape = tape_day(day)
    end_ms = int(cut.timestamp() * 1000)
    usable = (tape["strike"] == float(strike)) & (tape["ts_ms"] <= end_ms) & (tape["bid"] > 0) & (tape["ask"] > 0)
    mids = {}
    for side, want_call in (("call", True), ("put", False)):
        picks = np.flatnonzero(usable & ((tape["is_call"] > 0) == want_call))
        if picks.size:
            newest = picks[np.argmax(tape["ts_ms"][picks])]
            mids[side] = float((tape["bid"][newest] + tape["ask"][newest]) / 2.0)
    return mids


def read_overnight_futures(day):
    """/ES 1-minute bars from 18:00 the evening before to the 09:30 open: high, low and newest bar start."""
    night_start = eastern_moment(day, datetime.min.time()) - timedelta(days=1) + NIGHT_START
    open_at = eastern_moment(day, datetime.min.time()) + timedelta(hours=9, minutes=30)
    highs, lows, newest = [], [], None
    for bar in read_jsonl(day_file(FUTURES_BARS, day)):
        if bar.get("symbol") != "/ES" or bar.get("bar_minutes") != 1:
            continue
        at = parse_moment(bar["ts"])
        if night_start <= at < open_at:
            highs.append(bar["high"])
            lows.append(bar["low"])
            newest = at if newest is None or at > newest else newest
    if not highs:
        return None
    return {"high": max(highs), "low": min(lows), "newest_at": newest}


def read_event_calendar():
    """Mirai's event calendar: its events, the span it covers and when the file was last changed; None when absent."""
    path = paths.mirai_settings(EVENT_CALENDAR)
    if not path.exists():
        return None
    calendar = json.loads(path.read_text())
    calendar["changed_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return calendar


def calendar_covers(calendar, day):
    """True when the day sits inside the calendar's covered span."""
    return date.fromisoformat(calendar["covers_from"]) <= day <= date.fromisoformat(calendar["covers_through"])


def events_left_today(calendar, day, cut):
    """Scheduled events on the day whose time is after the cut, as (moment, kind) pairs in time order."""
    left = []
    for event in calendar["events"]:
        if event.get("date") != day.isoformat() or not event.get("time_et"):
            continue
        hour, minute = (int(part) for part in event["time_et"].split(":"))
        at = eastern_moment(day, datetime.min.time()) + timedelta(hours=hour, minutes=minute)
        if at > cut:
            left.append((at, event.get("kind")))
    return sorted(left)


def half_day_dates(calendar):
    """Days the calendar marks as a 13:00 close."""
    if calendar is None:
        return set()
    return {date.fromisoformat(event["date"]) for event in calendar["events"] if event.get("kind") == "HALF_DAY_CLOSE"}


def read_claude_final_forecast(day, cut):
    """Mirai's newest live final forecast written at or before the cut with a usable 30-minute answer, or None."""
    newest = None
    for row in read_jsonl(day_file(CLAUDE_FORECASTS, day)):
        if row.get("line_type") != "final_forecast" or row.get("test_variant") not in (None, "none"):
            continue
        if ((row.get("forecast") or {}).get("next_30_minutes") or {}).get("status") != "ok":
            continue
        written_at = parse_moment(row["written_at"])
        if written_at <= cut:
            row["written_at_moment"] = written_at
            row["read_at"] = parse_moment(row["read_id"].split(":", 1)[1])
            newest = row
    return newest


def read_headlines(day, cut, minutes):
    """How many headlines were captured in the window to the cut, the newest capture time, and whether the file exists."""
    path = day_file(HEADLINES, day)
    window_start = cut - timedelta(minutes=minutes)
    count, newest = 0, None
    for headline in read_jsonl(path):
        captured = headline.get("captured_at")
        if not captured:
            continue
        at = parse_moment(captured)
        if at <= cut:
            newest = at if newest is None or at > newest else newest
            if at >= window_start:
                count += 1
    return {"present": path is not None, "count": count, "newest_at": newest}


def read_login_state():
    """The Schwab login as the auth watch last logged it: state word, days left, and when the log was written; None if absent."""
    path = paths.mirai_data(AUTH_WATCH_LOG)
    if not path.exists():
        return None
    for line in reversed(path.read_text().splitlines()):
        match = LOGIN_DAYS_LEFT.search(line)
        if match:
            return {"state": match.group(1), "days_left": float(match.group(3)),
                    "reported_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)}
    return None

