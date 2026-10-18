# Builds one read: Mirai's files as they stood at a cut become one snapshot row, one observation per measure (a blank
# carries its reason, never 0.0) and the session's new 1-minute bars with arrival stamps. Raw inputs only; speed and bands are Phase 2.
import subprocess
import time
from datetime import timedelta

from engine import measures, mirai_files, paths, session_clock, store
from engine.measures import subject_key

VIX_SYMBOLS = ("$VIX", "$VIX1D", "$VIX9D", "$VIX3M", "$VVIX")
FUND_SYMBOLS = ("SPY", "RSP", "QQQ", "IWM", "SMH", "UUP", "USO")
SECTOR_FUNDS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
LARGEST_STOCKS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK/B")
MOVE_SINCE_MINUTES = {"5_minutes": 5, "15_minutes": 15, "30_minutes": 30, "60_minutes": 60}
CLAUDE_HORIZONS = {"30_minutes": "next_30_minutes", "to_close": "to_close"}
CLAUDE_SIDES = ("up", "flat", "down")
TAPE_WINDOW_MINUTES = 30
HEADLINE_WINDOW_MINUTES = 60
BAR_ARRIVAL_SECONDS = 62
BAR_LENGTH_SECONDS = 60
STRIKE_STEP_POINTS = 5
LOGIN_RENEW_WARNING_DAYS = 2
GAP_MARKERS_PER_WINDOW = TAPE_WINDOW_MINUTES


class ReadBuilder:
    """Collects the observations of one snapshot, stamping each with its feed's status and age at the cut."""

    def __init__(self, snapshot, cut_at):
        self.snapshot = snapshot
        self.cut_at = cut_at
        self.rows = []
        self.feeds = {}

    def feed_seen(self, feed, newest_at, received_at=None, present=True):
        """Record what a feed had landed by the cut; its status is missing, stale or ok by the registry's limit."""
        if not present or newest_at is None:
            self.feeds[feed] = {"status": "missing", "age": None, "market_at": None, "received_at": None}
            return
        age = max(0.0, round((self.cut_at - newest_at).total_seconds(), 3))
        status = "stale" if age > measures.feed_stale_limit_seconds(feed) else "ok"
        self.feeds[feed] = {"status": status, "age": age, "market_at": session_clock.iso_utc(newest_at),
                            "received_at": session_clock.iso_utc(received_at) if received_at else None}

    def add(self, measure_name, value, blank=None, source=None, subject_points=None, **subject):
        """Add one observation stamped with its source feed's state; a None value is blank with the reason given, else feed_missing."""
        source = source or measures.MEASURES_BY_NAME[measure_name]["source_feed_name"]
        state = self.feeds.get(source, {"status": None, "age": None, "market_at": None, "received_at": None})
        if value is None and blank is None:
            blank = "feed_missing"
        self.rows.append(store.new_observation(
            self.snapshot, measure_name, value, subject_key(**subject), feed=source, feed_status=state["status"],
            feed_age_seconds=state["age"], market_at=state["market_at"], received_at=state["received_at"],
            blank_reason=blank, subject_points=subject_points))

    def feed_ok(self, feed):
        """True when the feed's newest input is inside its stale limit."""
        return self.feeds.get(feed, {}).get("status") == "ok"


def build_read(cut_at, row_origin, written_at, slot=None, calendar=None, login=None):
    """Read every Mirai file at the cut and return the snapshot row, its observations, the session's bar rows and feed states."""
    started = time.monotonic()
    day = session_clock.to_eastern(cut_at).date()
    calendar = calendar if calendar is not None else mirai_files.read_event_calendar()
    half_days = mirai_files.half_day_dates(calendar)
    snapshot = new_snapshot("read", cut_at, row_origin, written_at, slot)
    read = ReadBuilder(snapshot, cut_at)

    bars = mirai_files.read_spx_bars(day, cut_at)
    collect_prices(read, bars)
    collect_clock(read, cut_at, day, half_days)
    quotes_rows = mirai_files.read_quotes_rows(day, cut_at)
    collect_quotes(read, quotes_rows, bars)
    collect_breadth(read, quotes_rows)
    collect_scan(read, mirai_files.read_native_scans(day, cut_at))
    collect_tape(read, day, cut_at, bars)
    collect_overnight(read, mirai_files.read_overnight_futures(day))
    collect_events(read, calendar, day, cut_at)
    collect_headlines(read, mirai_files.read_headlines(day, cut_at, HEADLINE_WINDOW_MINUTES))
    collect_claude(read, mirai_files.read_claude_final_forecast(day, cut_at), cut_at)
    if login is not None:
        collect_login(read, login, cut_at)
    collect_feed_health(read, {"spx_bars": len(bars), "watchlist_quotes": sum(1 for r in quotes_rows if r.get("quotes")),
                               "nyse_breadth": sum(1 for r in quotes_rows if r.get("bars"))})
    snapshot["snapshot_status"] = "built" if read.feed_ok("spx_bars") or read.feeds["spx_bars"]["status"] == "stale" else "blind"
    snapshot["snapshot_status_reason"] = None if snapshot["snapshot_status"] == "built" else "spx_bars_missing"
    snapshot["build_seconds"] = round(time.monotonic() - started, 3)
    return snapshot, read.rows, bar_rows(bars, day, row_origin), read.feeds


def new_snapshot(snapshot_kind, cut_at, row_origin, written_at, slot=None):
    """A collect_snapshots row for a read (or a nightly check) at this cut; the store fills the health counts on write."""
    cut_iso = session_clock.iso_utc(cut_at)
    day = session_clock.to_eastern(cut_at).date()
    commit, dirty = code_commit()
    is_read = snapshot_kind == "read"
    lane = session_clock.lane_for_slot(slot) if is_read else None
    lateness = None
    if slot and row_origin == "live":
        slot_due = session_clock.cut_for_slot(day, slot) + timedelta(seconds=1)
        lateness = round((written_at - slot_due).total_seconds() / 60, 2)
    return {
        "snapshot_id": f"{snapshot_kind}|{cut_iso}|{row_origin}|{measures.BUILDER_NAME}@{measures.BUILDER_VERSION}|b1",
        "trade_date": day.isoformat(), "snapshot_kind": snapshot_kind,
        "read_id": f"{lane}:{session_clock.iso_eastern(cut_at)}" if is_read else None, "read_lane": lane,
        "read_lane_cadence_minutes": session_clock.lane_cadence_minutes(lane) if is_read else None,
        "scheduled_slot_eastern": slot, "slot_status": "made" if slot else "not_scheduled", "slot_lateness_minutes": lateness,
        "market_moment_id": cut_iso, "is_training_grid_read": int(slot is not None),
        "read_moment_set_by": "clock" if slot or not is_read else "manual", "cut_at": cut_iso,
        "known_at": session_clock.iso_utc(written_at), "known_at_rule": "written_at", "row_origin": row_origin,
        "build_attempt_number": 1, "builder_name": measures.BUILDER_NAME, "builder_version": measures.BUILDER_VERSION,
        "builder_code_commit": commit, "was_code_tree_dirty": dirty,
        "component_versions_json_text": store.json_text({measures.BUILDER_NAME: measures.BUILDER_VERSION,
                                                         "measures_registry": measures.REGISTRY_VERSION}),
        "build_started_at": session_clock.iso_utc(written_at), "build_seconds": None, "did_exceed_build_budget": None,
        "source_manifest_s3_key": None, "source_manifest_sha256": None, "snapshot_content_sha256": None,
        "feeds_not_ok_count": None, "blank_values_count": None, "blank_values_without_reason_count": None,
        "snapshot_status": None, "snapshot_status_reason": None, "written_at": session_clock.iso_utc(written_at),
    }


def code_commit():
    """The repo's HEAD commit and whether the working tree had uncommitted changes; blanks when git is unavailable."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.REPO_ROOT, capture_output=True, text=True, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=paths.REPO_ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return head.stdout.strip(), int(bool(status.stdout.strip()))


def collect_clock(read, cut_at, day, half_days):
    """Minutes finished and minutes left in the session."""
    since_open = session_clock.minutes_since_open(cut_at)
    read.add("minutes_since_session_open", since_open, source="spx_bars")
    read.add("minutes_until_session_close", session_clock.session_minutes(day, half_days) - since_open, source="spx_bars")


def collect_prices(read, bars):
    """Last price, open, high and low (first bar's stale print ignored), range so far and the moves since open and N minutes."""
    newest = bars[-1]["at"] if bars else None
    read.feed_seen("spx_bars", newest, present=bool(bars))
    if not bars:
        for name in ("spx_price_points", "today_open_points", "session_high_points", "session_low_points", "range_so_far_points"):
            read.add(name, None)
        for since in ("open", *MOVE_SINCE_MINUTES):
            read.add("move_points", None, since=since)
        return
    closes = [bar["close"] for bar in bars]
    first = bars[0]
    first_high = first["high"] if first["high"] != first["open"] else max(first["close"], first["low"])
    first_low = first["low"] if first["low"] != first["open"] else min(first["close"], first["high"])
    high = max([first_high] + [bar["high"] for bar in bars[1:]])
    low = min([first_low] + [bar["low"] for bar in bars[1:]])
    read.add("spx_price_points", closes[-1])
    read.add("today_open_points", first["open"])
    read.add("session_high_points", high)
    read.add("session_low_points", low)
    read.add("range_so_far_points", round(high - low, 6))
    read.add("move_points", round(closes[-1] - first["open"], 6), since="open")
    for since, minutes in MOVE_SINCE_MINUTES.items():
        value = round(closes[-1] - closes[-1 - minutes], 6) if len(closes) > minutes else None
        read.add("move_points", value, blank="not_formed_yet", since=since)


def collect_quotes(read, quotes_rows, bars):
    """Prior close, the VIX family, fund changes, sector and largest-stock counts and the 10-year yield from the newest quotes row."""
    row = mirai_files.newest_with(quotes_rows, "quotes")
    read.feed_seen("watchlist_quotes", row["at"] if row else None, received_at=row["at"] if row else None, present=bool(row))
    quotes = row["quotes"] if row else {}
    spx = quotes.get("$SPX") or {}
    read.add("prior_close_points", spx.get("close"))
    last = bars[-1]["close"] if bars else None
    if last is not None and spx.get("close"):
        read.add("move_points", round(last - spx["close"], 6), source="spx_bars", since="prior_close")
    else:
        read.add("move_points", None, blank="feed_missing", source="spx_bars", since="prior_close")
    for symbol in VIX_SYMBOLS:
        quote = quotes.get(symbol) or {}
        read.add("vix_calendar_year_pct", quote.get("last"), blank=symbol_blank(row), symbol=symbol)
        read.add("vix_prior_close_calendar_year_pct", quote.get("close"), blank=symbol_blank(row), symbol=symbol)
    for symbol in FUND_SYMBOLS:
        read.add("fund_change_from_prior_close_pct", change_pct(quotes, symbol), blank=symbol_blank(row), symbol=symbol)
    read.add("sector_funds_up_count", count_up(quotes, SECTOR_FUNDS), blank="partial_coverage" if row else "feed_missing")
    read.add("largest_stocks_up_count", count_up(quotes, LARGEST_STOCKS), blank="partial_coverage" if row else "feed_missing")
    tnx = quotes.get("$TNX") or {}
    yield_pct = tnx["last"] / 10 if tnx.get("last") else None
    yield_change = round((tnx["last"] - tnx["close"]) * 10, 6) if tnx.get("last") and tnx.get("close") else None
    read.add("ten_year_yield_annual_pct", yield_pct, blank=symbol_blank(row))
    read.add("ten_year_yield_change_basis_points", yield_change, blank=symbol_blank(row))


def symbol_blank(quotes_row):
    """Why a quoted value is blank: no quotes row at all, or the provider left this symbol out."""
    return "provider_refused_symbol" if quotes_row else "feed_missing"


def change_pct(quotes, symbol):
    """A symbol's last quote against its prior close in percent, or None when either is missing."""
    quote = quotes.get(symbol) or {}
    if not quote.get("last") or not quote.get("close"):
        return None
    return round((quote["last"] / quote["close"] - 1) * 100, 6)


def count_up(quotes, symbols):
    """How many of the symbols are above their prior close; None unless every one of them quotes."""
    changes = [change_pct(quotes, symbol) for symbol in symbols]
    if any(change is None for change in changes):
        return None
    return sum(1 for change in changes if change > 0)


def collect_breadth(read, quotes_rows):
    """NYSE advancers and decliners from the newest quotes row that carried breadth bars."""
    row = mirai_files.newest_with(quotes_rows, "bars")
    bars = row["bars"] if row else {}
    advancing = (bars.get("$ADVN") or {}).get("close")
    declining = (bars.get("$DECN") or {}).get("close")
    newest = mirai_files.parse_moment(bars["$ADVN"]["ts"]) if advancing is not None and bars["$ADVN"].get("ts") else None
    read.feed_seen("nyse_breadth", newest, received_at=row["at"] if row else None, present=advancing is not None)
    read.add("nyse_advancing_count", advancing)
    read.add("nyse_declining_count", declining)
    total = (advancing or 0) + (declining or 0)
    portion = round(advancing / total, 6) if advancing is not None and declining is not None and total > 0 else None
    read.add("nyse_advancing_portion", portion, blank="no_activity" if advancing is not None else "feed_missing")


def collect_scan(read, scans):
    """VWAP, walls, gamma, ATM IV from the newest native scan; the straddle from the newest scan with an ok ruler."""
    scan = scans[-1] if scans else None
    read.feed_seen("options_scan", scan["at"] if scan else None, received_at=scan["at"] if scan else None, present=bool(scan))
    views = (scan or {}).get("gex_views") or {}
    read.add("vwap_points", (scan or {}).get("vwap"))
    read.add("level_price_points", views.get("call_wall_gamma"), level_type="call_wall")
    read.add("level_price_points", views.get("put_wall_gamma"), level_type="put_wall")
    read.add("level_gamma_share_portion", views.get("call_wall_gamma_share"), level_type="call_wall")
    read.add("level_gamma_share_portion", views.get("put_wall_gamma_share"), level_type="put_wall")
    read.add("dealer_net_gamma_usd", views.get("net_gex"))
    read.add("dealer_gamma_sign", (scan or {}).get("gamma_sign"))
    atm_iv = (scan or {}).get("atm_iv")
    read.add("same_day_atm_iv_calendar_year_pct", round(atm_iv * 100, 6) if atm_iv else None)
    with_ruler = [s for s in scans if (s.get("range_ruler") or {}).get("quality") == "ok"]
    ruler = with_ruler[-1]["range_ruler"] if with_ruler else None
    blank = "not_priceable" if scan else "feed_missing"
    read.add("same_day_straddle_mid_points", mirai_files.scan_straddle_points(ruler, "em_points"), blank=blank)
    read.add("opening_straddle_mid_points", mirai_files.scan_straddle_points(ruler, "em_open"), blank=blank)


def collect_tape(read, day, cut_at, bars):
    """Trade count, call and put premium, bullish share and gap markers over the last 30 minutes, plus the ATM quotes."""
    window = mirai_files.tape_window(day, cut_at, TAPE_WINDOW_MINUTES)
    read.feed_seen("options_tape", window["newest_trade_at"], present=window["present"])
    present = window["present"]
    read.add("options_trades_last_30_minutes_count", window["trades"] if present else None)
    read.add("options_call_premium_last_30_minutes_usd", round(window["calls_usd"], 2) if present else None)
    read.add("options_put_premium_last_30_minutes_usd", round(window["puts_usd"], 2) if present else None)
    sided = window["bull_usd"] + window["bear_usd"]
    read.add("bullish_portion_of_sided_premium", round(window["bull_usd"] / sided, 6) if present and sided > 0 else None,
             blank="no_activity" if present else "feed_missing")
    read.add("tape_gap_markers_last_30_minutes_count", window["gaps"] if present else None, source="options_tape", feed="options_tape")
    coverage = max(0.0, 1 - window["gaps"] / GAP_MARKERS_PER_WINDOW) if present else None
    read.add("feed_coverage_last_30_minutes_portion", coverage, source="options_tape", feed="options_tape")
    if not bars:
        for side in ("call", "put"):
            read.add("atm_option_mid_points", None, blank="not_formed_yet", side=side)
        return
    strike = int(round(bars[-1]["close"] / STRIKE_STEP_POINTS) * STRIKE_STEP_POINTS)
    mids = mirai_files.atm_option_mids(day, cut_at, strike) if present else {}
    for side in ("call", "put"):
        read.add("atm_option_mid_points", mids.get(side), blank="no_activity" if present else "feed_missing",
                 subject_points=float(strike), side=side, strike_points=strike)


def collect_overnight(read, night):
    """The /ES overnight high and low."""
    read.feed_seen("futures_bars", night["newest_at"] if night else None, present=bool(night))
    read.add("overnight_high_points", night["high"] if night else None, symbol="/ES")
    read.add("overnight_low_points", night["low"] if night else None, symbol="/ES")


def collect_events(read, calendar, day, cut_at):
    """Events still ahead today from Mirai's calendar: how many, minutes to the next one and its kind."""
    read.feed_seen("event_calendar", calendar["changed_at"] if calendar else None,
                   received_at=calendar["changed_at"] if calendar else None, present=calendar is not None)
    if calendar is None:
        for name in ("events_left_today_count", "minutes_until_next_event", "next_event_kind", "does_calendar_cover_day"):
            read.add(name, None, **({"impact": "any"} if name == "minutes_until_next_event" else {}))
        return
    covers = mirai_files.calendar_covers(calendar, day)
    read.add("does_calendar_cover_day", covers)
    if not covers:
        read.add("events_left_today_count", None, blank="calendar_not_covering_day")
        read.add("minutes_until_next_event", None, blank="calendar_not_covering_day", impact="any")
        read.add("next_event_kind", None, blank="calendar_not_covering_day")
        return
    left = mirai_files.events_left_today(calendar, day, cut_at)
    read.add("events_left_today_count", len(left))
    next_at, next_kind = left[0] if left else (None, None)
    minutes = round((next_at - cut_at).total_seconds() / 60, 3) if next_at else None
    read.add("minutes_until_next_event", minutes, blank="none_left_today", impact="any")
    read.add("next_event_kind", next_kind, blank="none_left_today")


def collect_headlines(read, headlines):
    """Headlines captured in the last 60 minutes; the feed has no poll stamps, so its coverage twin stays blank."""
    read.feed_seen("news_headlines", headlines["newest_at"], received_at=headlines["newest_at"], present=headlines["present"])
    read.add("headlines_last_60_minutes_count", headlines["count"] if headlines["present"] else None, kind="all")
    read.add("feed_coverage_last_60_minutes_portion", None, blank="not_known_at_read", source="news_headlines", feed="news_headlines")


def collect_claude(read, forecast, cut_at):
    """Mirai's newest Claude forecast at the cut: the three chances per horizon, the base rates shown, and the read moment."""
    present = forecast is not None
    read.feed_seen("claude_forecast", forecast["written_at_moment"] if present else None,
                   received_at=forecast["written_at_moment"] if present else None, present=present)
    carry = measures.MEASURES_BY_NAME["mirai_claude_forecast_pct"]["max_carry_forward_seconds"]
    too_old = present and (cut_at - forecast["written_at_moment"]).total_seconds() > carry
    blank = "too_old" if too_old else "feed_missing"
    usable = present and not too_old
    for horizon, field in CLAUDE_HORIZONS.items():
        answer = (forecast["forecast"].get(field) or {}) if usable else {}
        ok = answer.get("status") == "ok"
        base = answer.get("base_rate_shown_to_claude") or answer.get("base_rate") or {}
        for side in CLAUDE_SIDES:
            read.add("mirai_claude_forecast_pct", answer.get(f"{side}_pct") if ok else None, blank=blank, horizon=horizon, side=side)
            read.add("mirai_claude_base_rate_pct", base.get(f"{side}_pct") if ok else None, blank=blank, horizon=horizon, side=side)
    read.add("mirai_claude_read_at", session_clock.iso_utc(forecast["read_at"]) if usable else None, blank=blank)


def collect_login(read, login, cut_at):
    """The Schwab login's state, hours to expiry and expiry moment, from the auth watch's newest line (live reads only)."""
    read.feed_seen("schwab_login", login["reported_at"], received_at=login["reported_at"])
    expires_at = login["reported_at"] + timedelta(days=login["days_left"])
    hours_left = round((expires_at - cut_at).total_seconds() / 3600, 2)
    if hours_left <= 0:
        state = "expired"
    elif login["state"] != "ok":
        state = "unknown"
    elif hours_left <= LOGIN_RENEW_WARNING_DAYS * 24:
        state = "renew_soon"
    else:
        state = "ok"
    read.add("login_state", state, login="schwab")
    read.add("login_hours_until_expiry", hours_left, login="schwab")
    read.add("login_expires_at", session_clock.iso_utc(expires_at), login="schwab")


def collect_feed_health(read, rows_received):
    """Status and age for every feed seen, rows landed today where counted, and the range engine's all-ok flag."""
    for feed, state in read.feeds.items():
        read.add("feed_status", state["status"], source=feed, feed=feed)
        read.add("feed_age_seconds", state["age"], source=feed, feed=feed)
    for feed, count in rows_received.items():
        read.add("rows_received_today_count", count, source=feed, feed=feed)
    for consumer, needed in measures.CONSUMERS.items():
        read.add("are_needed_feeds_all_ok", all(read.feed_ok(feed) for feed in needed), source="spx_bars", consumer=consumer)


def bar_rows(bars, day, row_origin):
    """collect_market_prices rows for the session's SPX bars, known 62 seconds after each bar ends (Mirai stamps no arrival)."""
    rows = []
    for bar in bars:
        priced_at = session_clock.iso_utc(bar["at"])
        rows.append({
            "price_record_id": f"$SPX|bar|{priced_at}|-|live|{row_origin}", "trade_date": day.isoformat(),
            "instrument_symbol": "$SPX", "instrument_kind": "index", "price_kind": "bar", "bar_minutes": 1,
            "priced_at": priced_at,
            "known_at": session_clock.iso_utc(bar["at"] + timedelta(seconds=BAR_LENGTH_SECONDS + BAR_ARRIVAL_SECONDS)),
            "known_at_rule": "bar_end_plus_62_seconds", "snapshot_id": None, "row_origin": row_origin, "value_origin": "live",
            "source_feed_name": "spx_bars", "feed_status": "ok", "quote_unit": "points",
            "open_in_quote_units": bar["open"], "high_in_quote_units": bar["high"], "low_in_quote_units": bar["low"],
            "close_in_quote_units": bar["close"], "last_in_quote_units": None, "prior_close_in_quote_units": None,
            "is_prior_close_a_stand_in": None, "bid_in_quote_units": None, "ask_in_quote_units": None,
            "broker_mark_in_quote_units": None, "bid_size_count": None, "ask_size_count": None, "volume_count": None,
            "volume_bought_at_ask_count": None, "volume_sold_at_bid_count": None, "volume_unsided_count": None,
            "open_interest_count": None, "open_interest_published_for_date": None, "option_root": None,
            "expiration_date": None, "strike_points": None, "call_or_put": None, "settlement_style": None,
            "implied_vol_calendar_year_pct": None, "implied_vol_trading_year_pct": None, "delta_per_point": None,
            "gamma_per_point": None, "theta_per_share_per_day_usd": None, "vega_per_share_per_vol_point_usd": None,
            "greeks_source": None, "quote_status": "ok", "blank_reasons_text": "volume_count=known_wrong_same_day",
        })
    return rows
