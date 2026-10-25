# Builds one read: Mirai's files as they stood at a cut become one snapshot row, one observation per measure (a blank
# carries its reason, never 0.0), the calendar facts as known, and the session's new SPX and SPY bars stamped by
# arrival. Raw inputs only; the range engine and the push/pull pipeline read them back from the store.
import json
import time
from datetime import date, datetime, timedelta, timezone

from engine import paths, session_clock
from engine.collect import mirai_files
from engine.store import components, registry, store, store_queries

VIX_SYMBOLS = ("$VIX", "$VIX1D", "$VIX9D", "$VIX3M", "$VVIX")
FUND_SYMBOLS = ("SPY", "RSP", "QQQ", "IWM", "SMH", "UUP", "USO")
SECTOR_FUNDS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
LARGEST_STOCKS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK/B")
MOVE_SINCE_MINUTES = {"5_minutes": 5, "15_minutes": 15, "30_minutes": 30, "60_minutes": 60}
CLAUDE_HORIZONS = {"30_minutes": "next_30_minutes", "to_close": "to_close"}
TAPE_WINDOW_MINUTES = 30
# The windows the watchlist bars are measured over (the leaders' and breadth's change), and the two reads a day the
# same-day open interest is kept on: the first read, and the read fifty minutes before the close.
BAR_WINDOW_MINUTES = (60,)
OPEN_INTEREST_READS = {"first_minutes_after_open": session_clock.FIRST_SLOT_MINUTES_AFTER_OPEN,
                       "minutes_before_close": 50}
LEADER_SYMBOLS = ("SMH", "QQQ")
# An instrument whose bars the price table keeps: symbol, kind and the feed the bars come from.
SPX_INSTRUMENT = (registry.SPX_SYMBOL, "index", "spx_bars")
SPY_INSTRUMENT = (registry.SPY_SYMBOL, "etf", "watchlist_bars")
HEADLINE_WINDOW_MINUTES = 60
STRIKE_STEP_POINTS = 5
TNX_QUOTE_PER_YIELD_PCT = 10
GAP_MARKERS_FOR_EMPTY_WINDOW = TAPE_WINDOW_MINUTES
ONE_HOUR = timedelta(hours=1)
# The per-day files a live read measures first (byte length), so a rebuild of the cut parses the same prefix.
SOURCE_FILES = {feed: paths.MIRAI_FILES[feed] for feed in registry.FEEDS_WITH_LINE_COUNTS}


def feed_state(status):
    """A feed's state before anything landed: the status given (missing, or None for a feed never noted), no age."""
    return {"status": status, "age": None, "market_at": None, "received_at": None}


class ReadBuilder:
    """Collects the observations of one snapshot, stamping each with its source feed's status and age at the cut."""

    def __init__(self, snapshot, cut_at):
        """An empty read at a cut."""
        self.snapshot = snapshot
        self.cut_at = cut_at
        self.observations = []
        self.feed_states = {}

    def note_feed(self, feed, newest_at, received_at=None, present=True, status=None):
        """Record what a feed had landed by the cut; its status is missing, stale or ok unless one is given."""
        if not present or newest_at is None:
            self.feed_states[feed] = feed_state("missing")
            return
        age = round((self.cut_at - newest_at).total_seconds(), 3)
        if status is None:
            status = "stale" if age > registry.feed_stale_limit_seconds(feed) else "ok"
        self.feed_states[feed] = {"status": status, "age": age, "market_at": session_clock.iso_utc(newest_at),
                                  "received_at": session_clock.iso_utc(received_at) if received_at else None}

    def feed_ok(self, feed):
        """True when the feed's newest input is inside its stale limit."""
        return self.feed_states.get(feed, feed_state("missing"))["status"] == "ok"

    def add_observation(self, measure_name, value, blank=None, source=None, subject_points=None, **subject):
        """Add one observation stamped with its feed's state; None is blank with the reason given, else the feed's."""
        source = source or registry.MEASURES_BY_NAME[measure_name]["source_feed_name"]
        state = self.feed_states.get(source, feed_state(None))
        if value is None and blank is None:
            blank = "feed_stale" if state["status"] in ("stale", "late") else "feed_missing"
        self.observations.append(store.observation_row(
            self.snapshot, measure_name, value, registry.subject_key(**subject), subject_points=subject_points,
            blank_reason=blank, source_feed_name=source, feed_status=state["status"], feed_age_seconds=state["age"],
            market_at=state["market_at"], received_at=state["received_at"]))

    def add_blank_observations(self, measure_names, blank, source=None):
        """Add several whole-subject observations blank for one reason."""
        for name in measure_names:
            self.add_observation(name, None, blank=blank, source=source)


def build_read(conn, cut_at, row_origin, written_at, slot, slot_status, calendar, half_days, login=None,
               bar_arrived=True):
    """Read every Mirai file at the cut; returns the snapshot row, observations, new bar rows and feed states."""
    started = time.monotonic()
    day = session_clock.to_eastern(cut_at).date()
    snapshot = snapshot_row_for_read(cut_at, row_origin, written_at, slot, slot_status)
    read = ReadBuilder(snapshot, cut_at)
    file_limits = source_file_limits(conn, day, row_origin, snapshot["cut_at"])
    if row_origin != "history_rebuilt":
        snapshot["source_files_json_text"] = json.dumps(file_limits, sort_keys=True)

    bars = mirai_files.read_spx_bars(day, cut_at)
    collect_prices(read, bars, bar_arrived)
    collect_clock(read, cut_at, day, half_days)
    quotes_rows = mirai_files.read_quotes_rows(day, cut_at)
    prior_close = collect_watchlist_quotes(read, quotes_rows, bars)
    if prior_close is None:
        prior_close = stored_prior_session_close(conn, day)
    collect_breadth(read, quotes_rows)
    watch_rows = mirai_files.read_watchlist_bars(day, cut_at)
    collect_watchlist_bars(read, watch_rows, day, cut_at, prior_close, half_days)
    note_daily_bars(read, conn, day, cut_at)
    collect_options_scan(read, mirai_files.read_native_scans(day, cut_at, file_limits.get("options_scan")),
                         is_open_interest_read(cut_at, day, half_days))
    collect_options_tape(read, day, cut_at, bars, file_limits.get("options_tape"))
    collect_overnight(read, mirai_files.read_overnight_futures(day))
    collect_events(read, conn, calendar, day, cut_at, row_origin, written_at)
    collect_headlines(read, mirai_files.read_headlines(day, cut_at, HEADLINE_WINDOW_MINUTES))
    collect_mirai_claude_forecast(read, mirai_files.read_claude_final_forecast(day, cut_at), cut_at)
    collect_login(read, login, cut_at, row_origin)
    collect_feed_health(read, day, {"spx_bars": len(bars),
                                    "watchlist_quotes": sum(1 for r in quotes_rows if r.get("quotes")),
                                    "nyse_breadth": sum(1 for r in quotes_rows if r.get("bars")),
                                    "watchlist_bars": len(watch_rows)})
    bars_state = read.feed_states["spx_bars"]["status"]
    snapshot["snapshot_status"] = "blind" if bars_state == "missing" else "built"
    snapshot["snapshot_status_reason"] = {"missing": "spx_bars_missing", "late": "cut_bar_missing"}.get(bars_state)
    snapshot["build_seconds"] = round(time.monotonic() - started, 3)
    bar_rows = session_bar_rows(conn, bars, day, row_origin, cut_at, written_at, SPX_INSTRUMENT)
    bar_rows += session_bar_rows(conn, mirai_files.symbol_bars(watch_rows, registry.SPY_SYMBOL), day, row_origin,
                                 cut_at, written_at, SPY_INSTRUMENT)
    return snapshot, read.observations, bar_rows, read.feed_states


def snapshot_row_for_read(cut_at, row_origin, written_at, slot, slot_status):
    """The collect_snapshots row of a read at this cut, with its slot, lane and lateness."""
    day = session_clock.to_eastern(cut_at).date()
    lane = session_clock.lane_for_slot(slot)
    snapshot = store.snapshot_row_shared("read", session_clock.iso_utc(cut_at), day.isoformat(), row_origin,
                                         session_clock.iso_utc(written_at))
    snapshot.update({
        "read_id": f"{lane}:{session_clock.iso_eastern(cut_at)}", "read_lane": lane,
        "read_lane_cadence_minutes": session_clock.READ_LANES[lane], "scheduled_slot_eastern": slot,
        "slot_status": slot_status, "is_training_grid_read": int(slot is not None),
        "read_moment_set_by": "clock" if slot else "manual",
    })
    if slot and row_origin != "history_rebuilt":
        late_by = written_at - session_clock.slot_start(day, slot)
        snapshot["slot_lateness_minutes"] = round(late_by.total_seconds() / 60, 2)
    return snapshot


def source_file_limits(conn, day, row_origin, cut_at_iso):
    """Byte length per source file this read may parse: measured now when live, the live twin's record when rebuilt."""
    if row_origin == "history_rebuilt":
        live_twin = store_queries.live_snapshot_at(conn, cut_at_iso)
        if live_twin and live_twin["source_files_json_text"]:
            return json.loads(live_twin["source_files_json_text"])
        return {}
    sizes = {feed: mirai_files.file_bytes(template, day) for feed, template in SOURCE_FILES.items()}
    return {feed: size for feed, size in sizes.items() if size is not None}


def collect_clock(read, cut_at, day, half_days):
    """Minutes finished and minutes left in the session (no feed: the clock is the clock)."""
    since_open = session_clock.minutes_since_open(cut_at)
    read.add_observation("minutes_since_session_open", since_open)
    read.add_observation("minutes_until_session_close",
                         session_clock.session_length_minutes(day, half_days) - since_open)


def collect_prices(read, bars, bar_arrived):
    """Last price, open, high and low (first bar's stale print ignored), range so far and the moves since."""
    newest = bars[-1]["at"] if bars else None
    read.note_feed("spx_bars", newest, present=bool(bars), status=None if bar_arrived else "late")
    if not bars:
        read.add_blank_observations(("spx_price_points", "today_open_points", "session_high_points",
                                     "session_low_points", "range_so_far_points"), "feed_missing")
        for since in ("open", *MOVE_SINCE_MINUTES):
            read.add_observation("move_points", None, blank="feed_missing", since=since)
        return
    closes = [bar["close"] for bar in bars]
    first = bars[0]
    first_high = first["high"] if first["high"] != first["open"] else max(first["close"], first["low"])
    first_low = first["low"] if first["low"] != first["open"] else min(first["close"], first["high"])
    high = max([first_high] + [bar["high"] for bar in bars[1:]])
    low = min([first_low] + [bar["low"] for bar in bars[1:]])
    read.add_observation("spx_price_points", closes[-1])
    read.add_observation("today_open_points", first["open"])
    read.add_observation("session_high_points", high)
    read.add_observation("session_low_points", low)
    read.add_observation("range_so_far_points", store.rounded(high - low))
    read.add_observation("move_points", store.rounded(closes[-1] - first["open"]), since="open")
    for since, minutes in MOVE_SINCE_MINUTES.items():
        value = store.rounded(closes[-1] - closes[-1 - minutes]) if len(closes) > minutes else None
        read.add_observation("move_points", value, blank="not_formed_yet", since=since)


def collect_watchlist_quotes(read, quotes_rows, bars):
    """Prior close, VIX family, fund changes, sector and largest-stock counts and the 10-year yield from the quotes;
    returns the prior close for the true open."""
    row = mirai_files.newest_row_with(quotes_rows, "quotes")
    polled_at = row["at"] if row else None
    read.note_feed("watchlist_quotes", polled_at, received_at=polled_at, present=bool(row))
    quotes = row["quotes"] if row else {}
    spx = quotes.get("$SPX") or {}
    read.add_observation("prior_close_points", spx.get("close"))
    last = bars[-1]["close"] if bars else None
    move = store.rounded(last - spx["close"]) if last is not None and spx.get("close") else None
    read.add_observation("move_points", move, blank="feed_missing", source="spx_bars", since="prior_close")
    for symbol in VIX_SYMBOLS:
        quote = quotes.get(symbol) or {}
        read.add_observation("vix_calendar_year_pct", quote.get("last"), blank=blank_reason_for_symbol(row),
                             symbol=symbol)
        read.add_observation("vix_prior_close_calendar_year_pct", quote.get("close"),
                             blank=blank_reason_for_symbol(row),
                             symbol=symbol)
    for symbol in FUND_SYMBOLS:
        read.add_observation("fund_change_from_prior_close_pct", change_from_prior_close_pct(quotes, symbol),
                             blank=blank_reason_for_symbol(row), symbol=symbol)
    partial = "partial_coverage" if row else "feed_missing"
    read.add_observation("sector_funds_up_count", count_above_prior_close(quotes, SECTOR_FUNDS), blank=partial)
    read.add_observation("largest_stocks_up_count", count_above_prior_close(quotes, LARGEST_STOCKS), blank=partial)
    tnx = quotes.get("$TNX") or {}
    yield_pct = tnx["last"] / TNX_QUOTE_PER_YIELD_PCT if tnx.get("last") else None
    yield_change = None
    if tnx.get("last") and tnx.get("close"):
        yield_change = store.rounded((tnx["last"] - tnx["close"]) * TNX_QUOTE_PER_YIELD_PCT)
    read.add_observation("ten_year_yield_annual_pct", yield_pct, blank=blank_reason_for_symbol(row))
    read.add_observation("ten_year_yield_change_basis_points", yield_change, blank=blank_reason_for_symbol(row))
    return spx.get("close")


def collect_watchlist_bars(read, rows, day, cut_at, prior_close, half_days):
    """From the watchlist's minute bars: the true open (/ES from the prior session's last bar to its 09:30 bar, on
    the prior close), the leaders' change over each bar window, and breadth's net change over each window."""
    newest = rows[-1]["at"] if rows else None
    read.note_feed("watchlist_bars", newest, received_at=newest, present=bool(rows))
    true_open = true_open_points(day, cut_at, prior_close, half_days)
    read.add_observation("true_open_points", true_open,
                         blank="not_applicable" if day in mirai_files.es_roll_days() else "feed_missing",
                         source="watchlist_quotes" if true_open is None and prior_close is None else None)
    for symbol in LEADER_SYMBOLS:
        bars = mirai_files.symbol_bars(rows, symbol)
        for minutes in BAR_WINDOW_MINUTES:
            change, blank = window_change(bars, cut_at, minutes, rows)
            read.add_observation("fund_change_window_pct", change, blank=blank, symbol=symbol, window_minutes=minutes)
    breadth = mirai_files.symbol_bars(rows, mirai_files.BREADTH_NET_SYMBOL)
    for minutes in BAR_WINDOW_MINUTES:
        change, blank = window_change(breadth, cut_at, minutes, rows, as_percent=False)
        read.add_observation("breadth_net_change_count", change, blank=blank, window_minutes=minutes)


def stored_prior_session_close(conn, day):
    """The prior session's SPX close from the store, for history before the quotes feed began (2026-09-28): its daily
    bar's close, else its last 1-minute bar's close; None when the store holds neither. History rows carry the
    load's known_at, so only their market times are checked against the cut."""
    prior_day = session_clock.latest_trading_day(day - timedelta(days=1))
    daily = store_queries.daily_bars(conn, registry.SPX_SYMBOL, prior_day.isoformat())
    if daily and daily[-1]["trade_date"] == prior_day.isoformat():
        return daily[-1]["close"]
    bars = store_queries.session_bars(conn, prior_day.isoformat())
    return bars[-1]["close"] if bars else None


def true_open_points(day, cut_at, prior_close, half_days):
    """The prior close plus /ES's move from the prior session's last bar to today's 09:30 bar; None when any part is
    missing or the contract rolled overnight."""
    if prior_close is None or day in mirai_files.es_roll_days():
        return None
    opening = next((bar for bar in mirai_files.es_bars(day, cut_at) if bar["at"] == session_clock.session_open(day)),
                   None)
    prior_day = session_clock.latest_trading_day(day - timedelta(days=1))
    es_prior_close = mirai_files.es_prior_session_close(day, prior_day, session_clock.session_close(prior_day, half_days))
    if opening is None or es_prior_close is None:
        return None
    return store.rounded(prior_close + (opening["open"] - es_prior_close))


def window_change(bars, cut_at, minutes, rows, as_percent=True):
    """A symbol's bar close now against its close a window earlier (percent, or a count), with the blank reason."""
    if not bars:
        return None, "provider_refused_symbol" if rows else "feed_missing"
    window_start = bars[-1]["at"] - timedelta(minutes=minutes)
    earlier = mirai_files.bar_close_at_or_before(bars, window_start)
    if earlier is None or window_start < session_clock.session_open(session_clock.to_eastern(cut_at).date()):
        return None, "not_formed_yet"
    # The 60-minute window first forms on the bar that starts 60 minutes after the open, so the 10:30:59 read is
    # still not_formed_yet and the first value lands at 10:35, like the hour's move in points.
    now = bars[-1]["close"]
    if as_percent:
        return (store.rounded((now / earlier - 1) * 100) if earlier else None), "no_activity"
    return int(round(now - earlier)), "no_activity"


def blank_reason_for_symbol(quotes_row):
    """Why a quoted value is blank: no quotes row at all, or the provider left this symbol out."""
    return "provider_refused_symbol" if quotes_row else "feed_missing"


def change_from_prior_close_pct(quotes, symbol):
    """A symbol's last quote against its prior close in percent, or None when either is missing."""
    quote = quotes.get(symbol) or {}
    if not quote.get("last") or not quote.get("close"):
        return None
    return store.rounded((quote["last"] / quote["close"] - 1) * 100)


def count_above_prior_close(quotes, symbols):
    """How many of the symbols are above their prior close; None unless every one of them quotes."""
    changes = [change_from_prior_close_pct(quotes, symbol) for symbol in symbols]
    if any(change is None for change in changes):
        return None
    return sum(1 for change in changes if change > 0)


def collect_breadth(read, quotes_rows):
    """NYSE advancers and decliners from the newest quotes row that carried breadth bars."""
    row = mirai_files.newest_row_with(quotes_rows, "bars")
    bars = row["bars"] if row else {}
    advancing = (bars.get("$ADVN") or {}).get("close")
    declining = (bars.get("$DECN") or {}).get("close")
    newest = None
    if advancing is not None and bars["$ADVN"].get("ts"):
        newest = session_clock.parse_iso(bars["$ADVN"]["ts"])
    read.note_feed("nyse_breadth", newest, received_at=row["at"] if row else None, present=advancing is not None)
    read.add_observation("nyse_advancing_count", advancing)
    read.add_observation("nyse_declining_count", declining)
    portion, reason = None, "feed_missing"
    if advancing is not None and declining is not None:
        total = advancing + declining
        portion = store.rounded(advancing / total) if total > 0 else None
        reason = "no_activity"
    read.add_observation("nyse_advancing_portion", portion, blank=reason)


def is_open_interest_read(cut_at, day, half_days):
    """True on the two reads a day that keep the same-day open interest: the first, and fifty minutes before close."""
    slot_minute = session_clock.minute_of_session(cut_at, day)
    until_close = session_clock.session_length_minutes(day, half_days) - slot_minute
    return slot_minute == OPEN_INTEREST_READS["first_minutes_after_open"] \
        or until_close == OPEN_INTEREST_READS["minutes_before_close"]


def collect_options_scan(read, scans, keep_open_interest):
    """VWAP, walls, gamma and ATM IV from the newest native scan; the straddle from the newest scan with an ok ruler;
    on the two open-interest reads, every same-day strike's open interest by side (no row at all when the scan is
    absent or lists none: the options_scan feed row says why)."""
    present = bool(scans)
    scan = scans[-1] if present else {}
    read.note_feed("options_scan", scan.get("at"), received_at=scan.get("at"), present=present)
    views = scan.get("gex_views") or {}
    if keep_open_interest:
        held = mirai_files.scan_open_interest_by_strike(scan)
        for (side, strike), contracts in sorted(held.items()):
            read.add_observation("same_day_open_interest_contracts", contracts, subject_points=strike, side=side,
                                 strike_points=int(strike) if strike == int(strike) else strike)
    read.add_observation("vwap_points", scan.get("vwap"))
    for level in components.WALL_TYPES:
        read.add_observation("level_price_points", views.get(f"{level}_gamma"), level_type=level)
        read.add_observation("level_gamma_share_portion", views.get(f"{level}_gamma_share"), level_type=level)
    read.add_observation("dealer_net_gamma_usd", views.get("net_gex"))
    read.add_observation("dealer_gamma_sign", scan.get("gamma_sign"))
    atm_iv = scan.get("atm_iv")
    atm_iv_pct = store.rounded(atm_iv * 100) if atm_iv else None
    read.add_observation("same_day_atm_iv_calendar_year_pct", atm_iv_pct)
    with_ruler = [s for s in scans if (s.get("range_ruler") or {}).get("quality") == "ok"]
    ruler = with_ruler[-1]["range_ruler"] if with_ruler else None
    blank = "not_priceable" if present else "feed_missing"
    read.add_observation("same_day_straddle_mid_points",
                         store.rounded(mirai_files.scan_straddle_points(ruler, "em_points")), blank=blank)
    read.add_observation("opening_straddle_mid_points",
                         store.rounded(mirai_files.scan_straddle_points(ruler, "em_open")), blank=blank)


def collect_options_tape(read, day, cut_at, bars, max_bytes):
    """Trade count, call and put premium, bullish share and gap markers over 30 minutes, plus the ATM quotes."""
    window = mirai_files.tape_window_totals(day, cut_at, TAPE_WINDOW_MINUTES, max_bytes)
    read.note_feed("options_tape", window["newest_trade_at"], present=window["present"])
    counted = read.feed_ok("options_tape")
    read.add_observation("options_trades_last_30_minutes_count", window["trades"] if counted else None)
    calls_usd = round(window["calls_usd"], store.MONEY_DECIMALS) if counted else None
    puts_usd = round(window["puts_usd"], store.MONEY_DECIMALS) if counted else None
    read.add_observation("options_call_premium_last_30_minutes_usd", calls_usd)
    read.add_observation("options_put_premium_last_30_minutes_usd", puts_usd)
    sided = window["bull_usd"] + window["bear_usd"]
    bullish = store.rounded(window["bull_usd"] / sided) if counted and sided > 0 else None
    read.add_observation("bullish_portion_of_sided_premium", bullish, blank="no_activity" if counted else None)
    minute_closes = {bar["at"].replace(second=0, microsecond=0): bar["close"] for bar in bars}

    def spx_close_at(ts_ms):
        """SPX's last finished close at a trade's minute (the bar before it), or None early in the session."""
        moment = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).replace(second=0, microsecond=0)
        return minute_closes.get(moment - timedelta(minutes=1))

    urgent = mirai_files.tape_urgent_orders(day, cut_at, TAPE_WINDOW_MINUTES, spx_close_at, max_bytes) \
        if counted else {}
    for kind in mirai_files.URGENT_KINDS:
        read.add_observation("urgent_orders_count", urgent.get(kind) if counted else None, kind=kind,
                             window_minutes=TAPE_WINDOW_MINUTES)
    read.add_observation("tape_gap_markers_last_30_minutes_count", window["gaps"] if counted else None,
                         feed="options_tape")
    coverage = max(0.0, 1 - window["gaps"] / GAP_MARKERS_FOR_EMPTY_WINDOW) if counted else None
    read.add_observation("feed_coverage_last_30_minutes_portion", coverage, source="options_tape", feed="options_tape")
    if not bars:
        for side in ("call", "put"):
            read.add_observation("atm_option_mid_points", None, blank="not_formed_yet", side=side)
        return
    strike = int(round(bars[-1]["close"] / STRIKE_STEP_POINTS) * STRIKE_STEP_POINTS)
    mids = mirai_files.atm_option_mids(day, cut_at, strike, max_bytes) if window["present"] else {}
    for side in ("call", "put"):
        read.add_observation("atm_option_mid_points", mids.get(side),
                             blank="no_activity" if window["present"] else None,
                             subject_points=float(strike), side=side, strike_points=strike)


def collect_overnight(read, night):
    """The /ES overnight high and low."""
    read.note_feed("futures_bars", night["newest_at"] if night else None, present=bool(night))
    read.add_observation("overnight_high_points", night["high"] if night else None, symbol=mirai_files.ES_SYMBOL)
    read.add_observation("overnight_low_points", night["low"] if night else None, symbol=mirai_files.ES_SYMBOL)


def note_daily_bars(read, conn, day, cut_at):
    """The daily-bars feed's state from the store (Mirai's file is one snapshot, so only stored rows say what was
    known by the cut; a rebuild of history reads them by market date, as its forces do): ok while the newest $SPX
    daily bar is the prior session's or later, else stale."""
    newest = store_queries.newest_daily_bar(conn, registry.SPX_SYMBOL, store.bars_known_by(read.snapshot, cut_at),
                                            session_clock.latest_trading_day(day - timedelta(days=1)).isoformat())
    if newest is None:
        read.note_feed("daily_bars", None, present=False)
        return
    known = session_clock.parse_iso(newest["known_at"])
    prior_session = session_clock.latest_trading_day(day - timedelta(days=1))
    status = "ok" if date.fromisoformat(newest["trade_date"]) >= prior_session else "stale"
    read.note_feed("daily_bars", known, received_at=known, status=status)


def collect_events(read, conn, calendar, day, cut_at, row_origin, written_at):
    """Events still ahead today, taken from the store's calendar facts as they were known at the cut."""
    if calendar is not None:
        first_copy = not store_queries.has_calendar_facts(conn)
        store.write_calendar_facts(conn, calendar_fact_rows(calendar, row_origin, written_at, first_copy))
    known_at = store_queries.calendar_known_at(conn, session_clock.iso_utc(cut_at))
    known_moment = session_clock.parse_iso(known_at) if known_at else None
    if calendar is None or known_moment is not None:
        read.note_feed("event_calendar", known_moment, received_at=known_moment, present=known_moment is not None)
    if known_moment is None:
        reason = "feed_missing" if calendar is None else "not_known_at_read"
        read.add_blank_observations(("events_left_today_count", "next_event_kind", "does_calendar_cover_day"), reason)
        read.add_observation("minutes_until_next_event", None, blank=reason, impact="any")
        return
    covers = calendar is not None and mirai_files.calendar_covers(calendar, day)
    read.add_observation("does_calendar_cover_day", covers)
    if not covers:
        read.add_blank_observations(("events_left_today_count", "next_event_kind"), "calendar_not_covering_day")
        read.add_observation("minutes_until_next_event", None, blank="calendar_not_covering_day", impact="any")
        return
    cut_iso = session_clock.iso_utc(cut_at)
    left = [(starts_at, kind) for starts_at, kind, _
            in store_queries.calendar_events_known(conn, day.isoformat(), cut_iso) if starts_at > cut_iso]
    read.add_observation("events_left_today_count", len(left))
    next_at, next_kind = left[0] if left else (None, None)
    minutes = round((session_clock.parse_iso(next_at) - cut_at).total_seconds() / 60, 3) if next_at else None
    read.add_observation("minutes_until_next_event", minutes, blank="none_left_today", impact="any")
    read.add_observation("next_event_kind", next_kind, blank="none_left_today")


def calendar_fact_rows(calendar, row_origin, written_at, first_copy):
    """Calendar facts for the store: events and half days, known since the first copy's built date or file change."""
    known_at = session_clock.iso_utc(calendar["changed_at"])
    if first_copy:
        built_day = date.fromisoformat(calendar["built"])
        known_at = session_clock.iso_utc(session_clock.eastern_from_hhmm(built_day, "00:00"))
    written = session_clock.iso_utc(written_at)
    version = str(calendar.get("built"))
    facts = []
    for event in calendar["events"]:
        if not event.get("date"):
            continue
        shared = {"fact_version": version, "trade_date": event["date"], "known_at": known_at,
                  "fact_name": event.get("kind"),
                  "calendar_tier": str(event.get("tier")), "source_reference_text": event.get("source"),
                  "row_origin": row_origin, "written_at": written}
        if event.get("kind") == mirai_files.HALF_DAY_EVENT_KIND:
            closes_at = session_clock.iso_utc(session_clock.eastern_from_hhmm(date.fromisoformat(event["date"]),
                                                                         event.get("time_et") or "13:00"))
            facts.append({"fact_kind": registry.SESSION_DATE_FACT, "fact_id": event["date"], "fact_status": "half_day",
                          "session_closes_at": closes_at, **shared})
        if event.get("time_et"):
            starts_at = session_clock.iso_utc(session_clock.eastern_from_hhmm(date.fromisoformat(event["date"]),
                                                                         event["time_et"]))
            fact_id = f"{event['date']}|{event['time_et']}|{event.get('kind')}"
            facts.append({"fact_kind": registry.SCHEDULED_EVENT_FACT, "fact_id": fact_id,
                          "starts_at": starts_at, **shared})
    for fact in facts:
        fact["fact_record_id"] = store.calendar_fact_id_for(fact)
    return facts


def collect_headlines(read, headlines):
    """Headlines captured in the last 60 minutes, counted only while the feed is fresh."""
    read.note_feed("news_headlines", headlines["newest_at"], received_at=headlines["newest_at"],
                   present=headlines["present"])
    counted = read.feed_ok("news_headlines")
    read.add_observation("headlines_last_60_minutes_count", headlines["count"] if counted else None,
                         kind="all")


def collect_mirai_claude_forecast(read, forecast, cut_at):
    """Mirai's newest Claude forecast at the cut: three chances per horizon, the base rates shown, the read moment."""
    present = forecast is not None
    written = forecast["at"] if present else None
    read.note_feed("claude_forecast", written, received_at=written,
                   present=present)
    carry = registry.MEASURES_BY_NAME["mirai_claude_forecast_pct"]["max_carry_forward_seconds"]
    too_old = present and (cut_at - forecast["at"]).total_seconds() > carry
    blank = "too_old" if too_old else "feed_missing"
    usable = present and not too_old
    for horizon, field in CLAUDE_HORIZONS.items():
        answer = (forecast["forecast"].get(field) or {}) if usable else {}
        ok = answer.get("status") == "ok"
        base = answer.get("base_rate_shown_to_claude") or answer.get("base_rate") or {}
        for side in components.CLAUDE_SIDES:
            read.add_observation("mirai_claude_forecast_pct", answer.get(f"{side}_pct") if ok else None, blank=blank,
                                 horizon=horizon, side=side)
            read.add_observation("mirai_claude_base_rate_pct", base.get(f"{side}_pct") if ok else None, blank=blank,
                                 horizon=horizon, side=side)
    read_at = session_clock.iso_utc(forecast["read_at"]) if usable else None
    read.add_observation("mirai_claude_read_at", read_at, blank=blank)


def collect_login(read, login, cut_at, row_origin):
    """The Schwab login from the auth watch's newest line; a rebuilt read cannot know it, so its rows stay blank."""
    usable = login is not None and login["days_left"] is not None
    if row_origin != "history_rebuilt":
        reported_at = min(login["reported_at"], cut_at) if login else None
        read.note_feed("schwab_login", reported_at, received_at=reported_at, present=login is not None)
    if not usable:
        state = "unknown" if login else None
        read.add_observation("login_state", state, blank="not_known_at_read", login="schwab")
        read.add_observation("login_hours_until_expiry", None, blank="not_known_at_read", login="schwab")
        read.add_observation("login_expires_at", None, blank="not_known_at_read", login="schwab")
        return
    expires_at = login["reported_at"] + timedelta(days=login["days_left"])
    left = expires_at - cut_at
    if left <= timedelta(0):
        state = "expired"
    elif login["state"] != "ok":
        state = "unknown"
    elif left <= timedelta(days=registry.LOGIN_RENEW_WARNING_DAYS):
        state = "renew_soon"
    else:
        state = "ok"
    read.add_observation("login_state", state, login="schwab")
    read.add_observation("login_hours_until_expiry", round(left / ONE_HOUR, 2), login="schwab")
    read.add_observation("login_expires_at", session_clock.iso_utc(expires_at), login="schwab")


def collect_feed_health(read, day, rows_received):
    """Status, age, rows landed and lines skipped per feed, and the range engine's all-ok flag."""
    for feed, state in read.feed_states.items():
        read.add_observation("feed_status", state["status"], source=feed, feed=feed)
        read.add_observation("feed_age_seconds", state["age"], source=feed, feed=feed)
    for feed, count in rows_received.items():
        landed = read.feed_states[feed]["status"] != "missing"
        read.add_observation("rows_received_today_count", count if landed else None, source=feed, feed=feed)
    for feed in registry.FEEDS_WITH_LINE_COUNTS:
        skipped = mirai_files.lines_skipped(SOURCE_FILES[feed], day)
        read.add_observation("lines_skipped_today_count", skipped, blank="feed_missing", source=feed, feed=feed)
    for consumer, needed in registry.CONSUMERS.items():
        read.add_observation("are_needed_feeds_all_ok", all(read.feed_ok(feed) for feed in needed), consumer=consumer)


def session_bar_rows(conn, bars, day, row_origin, cut_at, written_at, instrument):
    """Price rows for an instrument's 1-minute bars not yet stored under this origin, each stamped with the moment it
    became known."""
    # A bar of the read's own slot, or any bar a rebuild writes once the session has closed, is known 62 s after it
    # ends; anything else is known when it is written.
    already = store_queries.stored_bar_times(conn, day.isoformat(), row_origin, instrument[0])
    own_slot_starts_at = cut_at - timedelta(minutes=session_clock.SLOT_MINUTES)
    session_closed = written_at >= session_clock.session_close(day, store_queries.half_day_sessions(conn))
    rows = []
    for bar in bars:
        priced_at = session_clock.iso_utc(bar["at"])
        if priced_at in already:
            continue
        if (session_closed if row_origin == "history_rebuilt" else bar["at"] >= own_slot_starts_at):
            arrival = timedelta(seconds=session_clock.BAR_LENGTH_SECONDS + registry.BAR_ARRIVAL_SECONDS)
            known_at = session_clock.iso_utc(bar["at"] + arrival)
            rule = registry.BAR_ARRIVAL_RULE
        else:
            known_at, rule = session_clock.iso_utc(written_at), "written_at"
        rows.append(store.bar_price_row(bar, day.isoformat(), row_origin, priced_at, known_at, rule, instrument))
    return rows


def daily_bar_rows(conn, today, half_days, written_at):
    """Daily bars of $SPX and $TNX not yet stored, priced at each day's close (13:00 on the half days the calendar
    lists, else 16:00): today's is live_frozen, earlier days are history_rebuilt; each is known at the moment Mirai's
    file stamps the fetch with (its file write)."""
    rows = []
    for symbol, kind in registry.DAILY_BAR_SYMBOLS.items():
        stored = store_queries.daily_bar_days(conn, symbol)
        for bar in mirai_files.read_daily_bars(symbol):
            if bar["day"] in stored:
                continue
            day = date.fromisoformat(bar["day"])
            fetched = session_clock.parse_iso(bar["fetched_at"]) if bar.get("fetched_at") else written_at
            close = session_clock.session_close(day, half_days)
            origin = "live_frozen" if day == today else "history_rebuilt"
            rows.append(store.bar_price_row(bar, bar["day"], origin, session_clock.iso_utc(close),
                                            session_clock.iso_utc(min(fetched, written_at)), "file_modified_at",
                                            (symbol, kind, "daily_bars"), "day",
                                            session_clock.session_length_minutes(day, half_days)))
    return rows
