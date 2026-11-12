# How to read each of Mirai's files as it stood at a moment: the live price, bars, quotes, the watchlist's minute bars
# (SPY, /ES, $ADD, the leaders), scans and their same-day open interest, the options tape and its urgent orders,
# futures, daily bars, the event calendar, Claude's forecast, headlines and the auth watch's login line. Read-only;
# nothing here imports Mirai's code.
import gzip
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np

from engine import paths
from engine.session_clock import eastern_from_hhmm, file_modified_at, parse_iso, session_open

FILES = paths.MIRAI_FILES
NIGHT_STARTS_AT = "18:00"
# The nightly and the phone warn this many days before the calendar's covers_through runs out.
CALENDAR_WARNING_DAYS = 14
HALF_DAY_EVENT_KIND = "HALF_DAY_CLOSE"
# Vendor trade-condition codes 130-144 mark legs of multi-leg packages, which have no clear buyer or seller side.
MULTI_LEG_CONDITIONS = range(130, 145)
# From 2026-11-11 the tape is rebuilt from Schwab's stream (rows marked src = schwab_stream), which sends no condition
# code. A package's legs are priced so the whole package lands on a legal tick, so a single leg often prints between
# the ticks an option may trade on alone (5 cents under 3 dollars, 10 cents from 3 dollars). Measured on the
# provider's tape of 2026-11-05 and 11-06 (package legs 24-25% of rows): every off-tick row was a package leg, and the
# rule caught 71-73% of them; on that tape merged into one-second rows as Schwab sends it, 79-82% of the off-tick
# volume was package legs and the rule caught about half of all package volume.
REBUILT_TAPE_SOURCE = "schwab_stream"
LEGAL_TICK_CENTS_UNDER_3 = 5
LEGAL_TICK_CENTS_FROM_3 = 10
CONTRACT_MULTIPLIER = 100
# Urgent orders: a burst is three or more strikes hit on one side inside a two-second bin; a big print is fifty
# contracts or more; near the money is within half a percent of SPX.
URGENT_BIN_MS = 2000
URGENT_BURST_STRIKES = 3
BIG_PRINT_CONTRACTS = 50
NEAR_MONEY_SHARE = 0.005
URGENT_KINDS = ("total", "near", "far")
# The delta ladder: each trade's delta at the moment it traded (Black–Scholes, no rates, the session's close as expiry,
# SPX's last finished minute as spot, the option's own volatility backed out of its quote's mid by halving), sorted by
# the delta's size into bands from far out of the money (under 0.10) to in the money (0.60 and over). Bands under 0.25
# are far out of the money.
DELTA_BANDS = ((0.0, 0.10, "0_10"), (0.10, 0.25, "10_25"), (0.25, 0.40, "25_40"), (0.40, 0.60, "40_60"),
               (0.60, 1.01, "60_100"))
FAR_OUT_BANDS = ("0_10", "10_25")
OPTION_SIDES = ("call", "put")
VOLATILITY_LOW, VOLATILITY_HIGH, VOLATILITY_HALVINGS = 0.01, 5.0, 40
YEAR_MINUTES = 365.0 * 24.0 * 60.0
MINUTE_MS = 60_000
SENTIMENT_WINDOW_MINUTES = 30
# The skew, what same-day protection costs: among trades with a two-sided quote and a delta of 0.15 to 0.35 in size
# (around 25 delta), the median implied volatility of the puts less that of the calls, in volatility points, over a
# 30-minute window; a side with fewer than SKEW_MINIMUM_TRADES such trades leaves the window unmeasured. A trade whose
# volatility sits at the halving's floor or ceiling (a quote no volatility prices) is left out.
SKEW_DELTA_LOW, SKEW_DELTA_HIGH = 0.15, 0.35
SKEW_WINDOW_MINUTES = 30
SKEW_MINIMUM_TRADES = 20
# Same-day open interest is kept for strikes within this share of spot; the scan's rows are [strike, calls, puts].
OPEN_INTEREST_SPOT_SHARE = 0.03
ES_SYMBOL = "/ES"
BREADTH_NET_SYMBOL = "$ADD"
SCAN_STRADDLE_SHARE = 0.85
AUTH_WATCH_LINE = re.compile(r"\[auth-check\] state=(\w+) age=([\d.?]+)d left=(-?[\d.?]+)d")
AUTH_WATCH_TAIL_BYTES = 4096
TAPE_CACHE = {}
SKIPPED_LINES = {}


def plain_day_file(template, day):
    """The plain (not gzipped) per-day Mirai file a template names, whether or not it exists."""
    return paths.mirai_data(template.format(day=day))


def day_file_path(template, day):
    """The plain or gzipped copy of a per-day Mirai file, or None when neither exists."""
    plain = plain_day_file(template, day)
    if plain.exists():
        return plain
    zipped = plain.with_name(plain.name + ".gz")
    return zipped if zipped.exists() else None


def open_bytes(path):
    """Open a plain or gzipped file for reading bytes."""
    return gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")


def file_bytes(template, day):
    """Byte length of the plain per-day file right now, or None when only a gzipped copy (or nothing) exists."""
    plain = plain_day_file(template, day)
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


def rows_at_or_before(template, day, cut, time_field="ts", max_bytes=None):
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


def read_live_price(now=None):
    """SPX's last streamed trade: price, trade time and its age in seconds at now; None when absent or partial."""
    try:
        row = json.loads(paths.mirai_data(FILES["spx_live_price"]).read_text())
        price, at = float(row["price"]), parse_iso(row["at"])
        age = ((now or datetime.now(timezone.utc)) - at).total_seconds()
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return {"price": price, "at": at, "age_s": round(age, 3)}


def read_spx_bars(day, cut):
    """SPX 1-minute bars whose start is at or before the cut."""
    return rows_at_or_before(FILES["spx_bars"], day, cut)


def read_quotes_rows(day, cut):
    """Watchlist rows (quotes and breadth bars) polled at or before the cut."""
    return rows_at_or_before(FILES["watchlist_quotes"], day, cut)


def read_watchlist_bars(day, cut):
    """Watchlist rows (one minute's bars per symbol) polled at or before the cut."""
    return rows_at_or_before(FILES["watchlist_bars"], day, cut)


def watchlist_bars_written_at(day):
    """When the watchlist's minute-bar file of a day was last written (Mirai writes it after the close, often again
    after the next session's), or None when it does not exist."""
    path = day_file_path(FILES["watchlist_bars"], day)
    return file_modified_at(path) if path else None


def symbol_bars(rows, symbol):
    """One symbol's bars out of watchlist rows, one per bar start (the newest poll wins), oldest first, each with
    its parsed 'at'."""
    bars = {}
    for row in rows:
        bar = (row.get("bars") or {}).get(symbol)
        if bar and bar.get("ts") and bar.get("close") is not None:
            bars[bar["ts"]] = bar
    return [dict(bars[ts], at=parse_iso(ts)) for ts in sorted(bars)]


def es_bars(day, cut):
    """/ES bars of a day file at or before the cut, each with a parsed 'at', oldest first: the watchlist's 1-minute
    bars, else the futures file's regular-session bars (1-minute when it has them, else 5-minute). Mirai writes
    both after the close, so for the day itself only a rebuild ever reaches them."""
    bars = symbol_bars(read_watchlist_bars(day, cut), ES_SYMBOL)
    if bars:
        return bars
    rows = [row for row in read_jsonl(day_file_path(FILES["futures_bars"], day))
            if row.get("symbol") == ES_SYMBOL and row.get("session") == "regular" and row.get("ts")]
    minutes = 1 if any(row.get("bar_minutes") == 1 for row in rows) else 5
    bars = [dict(row, at=parse_iso(row["ts"])) for row in rows if row.get("bar_minutes") == minutes]
    return sorted((bar for bar in bars if bar["at"] <= cut), key=lambda bar: bar["at"])


def es_prior_session_close(day, prior_day, prior_close_at):
    """/ES's last bar close of the prior session at or before its close: from the prior day's file, else from the
    prior-session bars the futures file of the day itself carries; None when neither has one."""
    for bars in (es_bars(prior_day, prior_close_at), es_bars(day, prior_close_at)):
        kept = [bar for bar in bars if bar["at"].date() == prior_day]
        if kept:
            return kept[-1]["close"]
    return None


def bar_close_at_or_before(bars, moment):
    """The close of the last bar starting at or before a moment, or None."""
    return next((bar["close"] for bar in reversed(bars) if bar["at"] <= moment), None)


def es_roll_days():
    """The sessions /ES rolled onto a new contract overnight (its 09:30 bar and the prior close differ by contract)."""
    path = paths.mirai_data(FILES["futures_rolls"])
    if not path.exists():
        return set()
    try:
        rolls = json.loads(path.read_text()).get("rolls") or []
    except ValueError:
        return set()
    return {date.fromisoformat(roll["day"]) for roll in rolls if roll.get("symbol") == ES_SYMBOL and roll.get("day")}


def read_daily_bars(symbol):
    """A symbol's daily bars from Mirai's ten-year file, oldest first, each with its day and fetch moment; empty when
    the file is absent."""
    path = paths.mirai_data(FILES["daily_bars"].format(symbol=symbol))
    rows = [row for row in read_jsonl(path if path.exists() else None) if row.get("day") and row.get("close") is not None]
    return sorted(rows, key=lambda row: row["day"])


def read_native_scans(day, cut, max_bytes=None):
    """Options scans built from the native chain at or before the cut, within the first max_bytes of the file."""
    scans = rows_at_or_before(FILES["options_scan"], day, cut, max_bytes=max_bytes)
    return [scan for scan in scans if str(scan.get("gex_source", "")).startswith("native")]


def scan_open_interest_by_strike(scan):
    """The scan's same-day open interest within the kept share of spot, as {(side, strike): contracts}."""
    spot = scan.get("spot")
    rows = (scan.get("gex_views") or {}).get("oi_side_by_strike") or []
    if not spot:
        return {}
    held = {}
    for row in rows:
        try:
            strike, calls, puts = float(row[0]), int(row[1] or 0), int(row[2] or 0)
        except (TypeError, ValueError, IndexError):
            continue
        if abs(strike / spot - 1) <= OPEN_INTEREST_SPOT_SHARE:
            held[("call", strike)] = calls
            held[("put", strike)] = puts
    return held


def scan_straddle_points(ruler, field):
    """A straddle value from the scan's range ruler, undoing the 85% the scanner stores."""
    value = (ruler or {}).get(field)
    return value / SCAN_STRADDLE_SHARE if value else None


def read_tape_day(day):
    """The day's whole tape as numpy columns (with each line's end byte offset) plus gap markers, parsed once."""
    if day in TAPE_CACHE:
        return TAPE_CACHE[day]
    path = day_file_path(FILES["options_tape"], day)
    columns = {"ts_ms": [], "offset": [], "strike": [], "is_call": [], "price": [], "size": [], "bid": [], "ask": [],
               "condition": [], "package": [], "print_size": []}
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
                columns["package"].append(is_package_leg(trade))
                # the size of one trade: the row's size, or on a rebuilt row (which can merge several trades) the size
                # of its newest single trade when the recorder knew it
                columns["print_size"].append(trade.get("last_size") or trade.get("size") or 0)
        SKIPPED_LINES[path] = skipped
    tape = {name: np.array(values, dtype=float) for name, values in columns.items()}
    tape["present"] = path is not None
    tape["gaps"] = [gap for gap in gaps if gap[0] is not None]
    TAPE_CACHE[day] = tape
    return tape


def is_package_leg(trade):
    """True for a leg of a multi-leg package: the provider's condition code, or on a rebuilt row a price off the legal
    tick (see REBUILT_TAPE_SOURCE)."""
    if (trade.get("condition") or 0) in MULTI_LEG_CONDITIONS:
        return True
    if trade.get("src") != REBUILT_TAPE_SOURCE:
        return False
    cents = round(float(trade["price"]) * 100)
    return cents % (LEGAL_TICK_CENTS_UNDER_3 if cents < 300 else LEGAL_TICK_CENTS_FROM_3) != 0


def gap_moment(line):
    """The moment of a collector gap marker line, or None when it has no usable time."""
    try:
        text = json.loads(line).get("ts")
        return parse_iso(text) if text else None
    except ValueError:
        return None


def tape_window_totals(day, cut, minutes, max_bytes=None):
    """Trade count, call and put premium, sided bull and bear premium, gap markers and newest trade in the window,
    plus the share of the window's whole minutes (those inside the session) with a trade on file: the recorder writes
    a gap marker only while it is running and failing, so a stretch it was not recording at all (a restart, a feed it
    never had) leaves no marker and shows only as minutes with no trade."""
    tape = read_tape_day(day)
    end_ms = int(cut.timestamp() * 1000)
    start_ms = end_ms - minutes * 60 * 1000
    first_ms = max(start_ms, int(session_open(day).timestamp() * 1000))
    whole_minutes = max(0, (end_ms - first_ms) // MINUTE_MS)
    on_disk = tape["offset"] <= max_bytes if max_bytes is not None else np.ones(tape["offset"].shape, dtype=bool)
    inside = on_disk & (tape["ts_ms"] >= start_ms) & (tape["ts_ms"] <= end_ms)
    premium = tape["price"] * tape["size"] * CONTRACT_MULTIPLIER
    is_call = tape["is_call"] > 0
    sided = inside & (tape["package"] == 0) & (tape["bid"] > 0) & (tape["ask"] > 0)
    mid = (tape["bid"] + tape["ask"]) / 2.0
    above = sided & (tape["price"] > mid)
    below = sided & (tape["price"] < mid)
    window_start = cut - timedelta(minutes=minutes)
    newest_ms = tape["ts_ms"][on_disk & (tape["ts_ms"] <= end_ms)]
    gaps = [gap_at for gap_at, gap_offset in tape["gaps"]
            if window_start <= gap_at <= cut and (max_bytes is None or gap_offset <= max_bytes)]
    in_session = inside & (tape["ts_ms"] >= first_ms)
    traded_minutes = np.unique(np.minimum((tape["ts_ms"][in_session] - first_ms) // MINUTE_MS,
                                          whole_minutes - 1)).size if whole_minutes else 0
    return {
        "minutes_with_trades_portion": traded_minutes / whole_minutes if whole_minutes else None,
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


def tape_urgent_orders(day, cut, minutes, spx_close_at, max_bytes=None):
    """Signed urgent orders in the window to the cut, bullish less bearish: bursts (three strikes on one side inside
    one two-second bin, counted once when the third strike lands) and big prints, in total and split by whether
    the trade's strike sat near the money (a trade before SPX's first bar counts in the total only); legs of multi-leg
    packages are left out (59-80% of big prints were spread legs, whose side says nothing), a big print counts only
    when it took the bid or the ask outright, and a big print that completes a burst counts as both. A big print is
    judged on one trade's size: on a rebuilt row that merges several trades, the newest one's (print_size)."""
    # A trade's side is the quote side it took (at or past the ask bought, at or past the bid sold, else the nearer
    # side); bought calls and sold puts are bullish. spx_close_at(ts_ms) gives SPX at the trade's minute.
    tape = read_tape_day(day)
    end_ms = int(cut.timestamp() * 1000)
    start_ms = end_ms - minutes * 60 * 1000
    on_disk = tape["offset"] <= max_bytes if max_bytes is not None else np.ones(tape["offset"].shape, dtype=bool)
    usable = (on_disk & (tape["ts_ms"] >= start_ms) & (tape["ts_ms"] <= end_ms) & (tape["bid"] > 0)
              & (tape["ask"] >= tape["bid"]) & (tape["price"] >= tape["bid"]) & (tape["price"] <= tape["ask"])
              & (tape["strike"] > 0) & (tape["package"] == 0))
    counts = dict.fromkeys(URGENT_KINDS, 0)
    bins = {}
    for index in np.flatnonzero(usable)[np.argsort(tape["ts_ms"][usable], kind="stable")]:
        price, bid, ask = tape["price"][index], tape["bid"][index], tape["ask"][index]
        side = 1 if price >= ask else -1 if price <= bid else np.sign(price - (bid + ask) / 2)
        if not side:
            continue
        bull = int(side) * (1 if tape["is_call"][index] > 0 else -1)
        spx = spx_close_at(tape["ts_ms"][index])
        kind = None if not spx else "near" if abs(tape["strike"][index] / spx - 1) < NEAR_MONEY_SHARE else "far"
        strikes = bins.setdefault((int(tape["ts_ms"][index] // URGENT_BIN_MS), bull), set())
        completes_burst = False
        if len(strikes) < URGENT_BURST_STRIKES:
            strikes.add(float(tape["strike"][index]))
            completes_burst = len(strikes) == URGENT_BURST_STRIKES
        clear_side = price >= ask or price <= bid
        big = tape["print_size"][index] >= BIG_PRINT_CONTRACTS
        for _ in range(int(big and clear_side) + int(completes_burst)):
            counts["total"] += bull
            if kind:
                counts[kind] += bull
    return counts


def normal_cdf(x):
    """The standard normal curve's area below each x (Abramowitz and Stegun 7.1.26, error under 2e-7)."""
    z = np.abs(x) / np.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * erf)


def option_value(spot, strike, volatility, years, is_call):
    """Black–Scholes value with no rates, for arrays."""
    root = volatility * np.sqrt(years)
    d1 = (np.log(spot / strike) + 0.5 * root * root) / root
    d2 = d1 - root
    calls = spot * normal_cdf(d1) - strike * normal_cdf(d2)
    return np.where(is_call, calls, calls - spot + strike)


def trade_volatilities(spot, strike, years, is_call, quote):
    """Each trade's implied volatility, the one that prices its quote, found by halving between 1% and 500%; a quote
    under the option's floor takes the low end and one over its ceiling the high end."""
    low, high = np.full(spot.shape, VOLATILITY_LOW), np.full(spot.shape, VOLATILITY_HIGH)
    for _ in range(VOLATILITY_HALVINGS):
        middle = (low + high) / 2.0
        rich = option_value(spot, strike, middle, years, is_call) > quote
        high, low = np.where(rich, middle, high), np.where(rich, low, middle)
    return (low + high) / 2.0


def trade_deltas(spot, strike, years, is_call, volatility):
    """Each trade's delta at its implied volatility."""
    root = volatility * np.sqrt(years)
    d1 = (np.log(spot / strike) + 0.5 * root * root) / root
    return np.where(is_call, normal_cdf(d1), normal_cdf(d1) - 1.0)


def tape_deltas(day, spot_at_minute_ms, close_at):
    """The day's trades' deltas, priced once and kept on the cached tape with each trade's implied volatility (both
    NaN until the trade's spot is known): a trade's spot is SPX's close in the minute before its own, read from
    {minute start in ms: close}."""
    tape = read_tape_day(day)
    if "delta" not in tape:
        tape["delta"] = np.full(tape["ts_ms"].shape, np.nan)
        tape["volatility"] = np.full(tape["ts_ms"].shape, np.nan)
    close_ms = close_at.timestamp() * 1000.0
    minute_before = (tape["ts_ms"] // MINUTE_MS - 1) * MINUTE_MS
    keys = np.array(sorted(spot_at_minute_ms), dtype=float)
    if not keys.size:
        return tape["delta"]
    found = np.minimum(np.searchsorted(keys, minute_before), keys.size - 1)
    known = keys[found] == minute_before
    waiting = np.isnan(tape["delta"]) & known & (tape["strike"] > 0) & (tape["ts_ms"] < close_ms)
    if waiting.any():
        closes = np.array([spot_at_minute_ms[key] for key in keys], dtype=float)
        spot = closes[found[waiting]]
        bid, ask, price = tape["bid"][waiting], tape["ask"][waiting], tape["price"][waiting]
        quote = np.where((bid > 0) & (ask >= bid), (bid + ask) / 2.0, price)
        years = (close_ms - tape["ts_ms"][waiting]) / MINUTE_MS / YEAR_MINUTES
        strike, is_call = tape["strike"][waiting], tape["is_call"][waiting] > 0
        volatility = trade_volatilities(spot, strike, years, is_call, np.maximum(quote, 0.0))
        tape["volatility"][waiting] = volatility
        tape["delta"][waiting] = trade_deltas(spot, strike, years, is_call, volatility)
    return tape["delta"]


def tape_skew(day, cut, spot_at_minute_ms, close_at, max_bytes=None):
    """The skew over the 30 minutes to the cut and over the 30 before them: the median implied volatility of the puts
    less that of the calls, in volatility points, among trades with a two-sided quote and a delta of SKEW_DELTA_LOW to
    SKEW_DELTA_HIGH in size, each window None when a side has fewer than SKEW_MINIMUM_TRADES; with the fewer side's
    trade count per window. Returns {last_30, prior_30, last_30_trades, prior_30_trades}."""
    tape = read_tape_day(day)
    delta = tape_deltas(day, spot_at_minute_ms, close_at)
    volatility = tape["volatility"]
    end_ms = cut.timestamp() * 1000.0
    on_disk = tape["offset"] <= max_bytes if max_bytes is not None else np.ones(tape["offset"].shape, dtype=bool)
    size_of_delta = np.abs(np.nan_to_num(delta))
    with np.errstate(invalid="ignore"):
        priced = (volatility > VOLATILITY_LOW * 1.01) & (volatility < VOLATILITY_HIGH * 0.99)
    usable = (on_disk & ~np.isnan(delta) & priced & (tape["bid"] > 0) & (tape["ask"] >= tape["bid"])
              & (size_of_delta >= SKEW_DELTA_LOW) & (size_of_delta <= SKEW_DELTA_HIGH))
    is_call = tape["is_call"] > 0
    skew = {}
    for key, window_end_ms in (("last_30", end_ms), ("prior_30", end_ms - SKEW_WINDOW_MINUTES * MINUTE_MS)):
        inside = usable & (tape["ts_ms"] > window_end_ms - SKEW_WINDOW_MINUTES * MINUTE_MS) \
            & (tape["ts_ms"] <= window_end_ms)
        puts, calls = volatility[inside & ~is_call], volatility[inside & is_call]
        skew[key + "_trades"] = int(min(puts.size, calls.size))
        skew[key] = float((np.median(puts) - np.median(calls)) * 100.0) \
            if min(puts.size, calls.size) >= SKEW_MINIMUM_TRADES else None
    return skew


def tape_delta_ladder(day, cut, spot_at_minute_ms, close_at, max_bytes=None):
    """The day's same-day trades from the open to the cut by delta band and side: contracts traded (every trade, legs
    of packages too) and contracts bought less sold (single-leg trades with an ask, a zero bid allowed: at or past the
    ask bought, at or past the bid sold, else the nearer side), plus the sentiment, calls bought less sold less the
    same for puts, since the open and over the last 30 minutes, with the contracts that took a side over each (the
    sentiment's denominators), and the share of the session's minutes so far with any trade on file. Trades before
    SPX's first finished minute have no delta and are left out. Returns {contracts: {(band, side): n}, net_bought:
    {...}, sentiment, sentiment_last_30, sided, sided_last_30, minute_coverage, trades}."""
    tape = read_tape_day(day)
    delta = tape_deltas(day, spot_at_minute_ms, close_at)
    end_ms = cut.timestamp() * 1000.0
    open_ms = session_open(day).timestamp() * 1000.0
    on_disk = tape["offset"] <= max_bytes if max_bytes is not None else np.ones(tape["offset"].shape, dtype=bool)
    inside = on_disk & (tape["ts_ms"] >= open_ms) & (tape["ts_ms"] <= end_ms) & ~np.isnan(delta)
    price, bid, ask, size = tape["price"], tape["bid"], tape["ask"], tape["size"]
    quoted = (ask > 0) & (ask >= bid) & (tape["package"] == 0)
    side = np.where(quoted, np.where(price >= ask, 1.0, np.where(price <= bid, -1.0, np.sign(price - (bid + ask) / 2))),
                    0.0)
    is_call = tape["is_call"] > 0
    size_of_delta = np.abs(np.nan_to_num(delta))
    ladder = {"contracts": {}, "net_bought": {}, "trades": int(inside.sum())}
    for low, high, band in DELTA_BANDS:
        in_band = inside & (size_of_delta >= low) & (size_of_delta < high)
        for name, on_side in (("call", is_call), ("put", ~is_call)):
            cell = in_band & on_side
            ladder["contracts"][band, name] = int(size[cell].sum())
            ladder["net_bought"][band, name] = int((size[cell] * side[cell]).sum())
    recent = inside & (tape["ts_ms"] > end_ms - SENTIMENT_WINDOW_MINUTES * MINUTE_MS)
    signed = size * side * np.where(is_call, 1.0, -1.0)
    ladder["sentiment"] = int(signed[inside].sum())
    ladder["sentiment_last_30"] = int(signed[recent].sum())
    ladder["sided"] = int(size[inside & (side != 0)].sum())
    ladder["sided_last_30"] = int(size[recent & (side != 0)].sum())
    on_file = on_disk & (tape["ts_ms"] >= open_ms) & (tape["ts_ms"] <= end_ms)
    minutes = int((end_ms - open_ms) // MINUTE_MS) + 1
    traded = np.unique((tape["ts_ms"][on_file] - open_ms) // MINUTE_MS).size
    ladder["minute_coverage"] = min(1.0, traded / minutes) if minutes > 0 else 0.0
    return ladder


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
    night_start = eastern_from_hhmm(day - timedelta(days=1), NIGHT_STARTS_AT)
    open_at = session_open(day)
    highs, lows, newest = [], [], None
    for bar in read_jsonl(day_file_path(FILES["futures_bars"], day)):
        if bar.get("symbol") != ES_SYMBOL or bar.get("bar_minutes") != 1:
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
    path = paths.mirai_settings(paths.EVENT_CALENDAR_FILE)
    if not path.exists():
        return None
    calendar = json.loads(path.read_text())
    calendar["changed_at"] = file_modified_at(path)
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
    return date.fromisoformat(calendar["covers_from"]) <= day <= calendar_covers_through(calendar)


def calendar_covers_through(calendar):
    """The last day the calendar covers; past it the event measures are blank and the half days unknown."""
    return date.fromisoformat(calendar["covers_through"])


def read_claude_final_forecast(day, cut):
    """Mirai's newest live final forecast written at or before the cut with a usable 30-minute answer, or None."""
    newest = None
    for row in rows_at_or_before(FILES["claude_forecast"], day, cut, time_field="written_at"):
        if row.get("line_type") != "final_forecast" or row.get("test_variant") not in (None, "none"):
            continue
        if ((row.get("forecast") or {}).get("next_30_minutes") or {}).get("status") != "ok":
            continue
        row["read_at"] = parse_iso(row["read_id"].split(":", 1)[1])
        newest = row
    return newest


def read_headlines(day, cut, minutes):
    """Headlines captured in the window to the cut, the newest capture time, and whether the file exists."""
    rows = rows_at_or_before(FILES["news_headlines"], day, cut, time_field="captured_at")
    window_start = cut - timedelta(minutes=minutes)
    return {"present": day_file_path(FILES["news_headlines"], day) is not None,
            "count": sum(1 for row in rows if row["at"] >= window_start),
            "newest_at": max((row["at"] for row in rows), default=None)}


def read_login_state():
    """The Schwab login as the auth watch last logged it: state, days left (None when it printed ?), the log's time."""
    path = paths.mirai_data(FILES["schwab_login"])
    if not path.exists():
        return None
    with open(path, "rb") as handle:
        handle.seek(max(0, os.path.getsize(path) - AUTH_WATCH_TAIL_BYTES))
        tail = handle.read().decode("utf-8", errors="replace")
    for line in reversed(tail.splitlines()):
        match = AUTH_WATCH_LINE.search(line)
        if match:
            days_left = None if match.group(3) == "?" else float(match.group(3))
            return {"state": match.group(1), "days_left": days_left, "reported_at": file_modified_at(path)}
    return None
