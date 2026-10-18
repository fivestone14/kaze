# Builds one read: Mirai's files as they stood at a cut become one snapshot row, one observation per measure (a blank
# carries its reason, never 0.0), the calendar facts as known, and the session's new bars stamped by arrival.
# Raw inputs only; the range engine reads them back from the store.
import json
import time
from datetime import date, timedelta

from engine import session_clock
from engine.collect import mirai_files
from engine.store import registry, store

VIX_SYMBOLS = ("$VIX", "$VIX1D", "$VIX9D", "$VIX3M", "$VVIX")
FUND_SYMBOLS = ("SPY", "RSP", "QQQ", "IWM", "SMH", "UUP", "USO")
SECTOR_FUNDS = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
LARGEST_STOCKS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK/B")
MOVE_SINCE_MINUTES = {"5_minutes": 5, "15_minutes": 15, "30_minutes": 30, "60_minutes": 60}
CLAUDE_HORIZONS = {"30_minutes": "next_30_minutes", "to_close": "to_close"}
TAPE_WINDOW_MINUTES = 30
HEADLINE_WINDOW_MINUTES = 60
STRIKE_STEP_POINTS = 5
TNX_QUOTE_PER_YIELD_PCT = 10
GAP_MARKERS_FOR_EMPTY_WINDOW = TAPE_WINDOW_MINUTES
MISSING_FEED_STATE = {"status": "missing", "age": None, "market_at": None, "received_at": None}
UNNOTED_FEED_STATE = {"status": None, "age": None, "market_at": None, "received_at": None}
SOURCE_FILES = {"spx_bars": mirai_files.SPX_BARS, "watchlist_quotes": mirai_files.WATCHLIST_QUOTES,
                "options_scan": mirai_files.OPTIONS_SCANS, "options_tape": mirai_files.OPTIONS_TAPE,
                "claude_forecast": mirai_files.CLAUDE_FORECASTS, "news_headlines": mirai_files.HEADLINES}


class ReadBuilder:
    """Collects the observations of one snapshot, stamping each with its source feed's status and age at the cut."""

    def __init__(self, snapshot, cut_at):
        self.snapshot = snapshot
        self.cut_at = cut_at
        self.observations = []
        self.feed_states = {}

    def note_feed(self, feed, newest_at, received_at=None, present=True, status=None):
        """Record what a feed had landed by the cut; its status is missing, stale or ok unless one is given."""
        if not present or newest_at is None:
            self.feed_states[feed] = dict(MISSING_FEED_STATE)
            return
        age = round((self.cut_at - newest_at).total_seconds(), 3)
        if status is None:
            status = "stale" if age > registry.feed_stale_limit_seconds(feed) else "ok"
        self.feed_states[feed] = {"status": status, "age": age, "market_at": session_clock.iso_utc(newest_at),
                                  "received_at": session_clock.iso_utc(received_at) if received_at else None}

    def feed_ok(self, feed):
        """True when the feed's newest input is inside its stale limit."""
        return self.feed_states.get(feed, MISSING_FEED_STATE)["status"] == "ok"

    def add_observation(self, measure_name, value, blank=None, source=None, subject_points=None, **subject):
        """Add one observation stamped with its feed's state; None is blank with the reason given, else the feed's."""
        source = source or registry.MEASURES_BY_NAME[measure_name]["source_feed_name"]
        state = self.feed_states.get(source, UNNOTED_FEED_STATE)
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
    collect_watchlist_quotes(read, quotes_rows, bars)
    collect_breadth(read, quotes_rows)
    collect_options_scan(read, mirai_files.read_native_scans(day, cut_at, file_limits.get("options_scan")))
    collect_options_tape(read, day, cut_at, bars, file_limits.get("options_tape"))
    collect_overnight(read, mirai_files.read_overnight_futures(day))
    collect_events(read, conn, calendar, day, cut_at, row_origin, written_at)
    collect_headlines(read, mirai_files.read_headlines(day, cut_at, HEADLINE_WINDOW_MINUTES))
    collect_mirai_claude_forecast(read, mirai_files.read_claude_final_forecast(day, cut_at), cut_at)
    collect_login(read, login, cut_at, row_origin)
    collect_feed_health(read, day, {"spx_bars": len(bars),
                                    "watchlist_quotes": sum(1 for r in quotes_rows if r.get("quotes")),
                                    "nyse_breadth": sum(1 for r in quotes_rows if r.get("bars"))})
    bars_state = read.feed_states["spx_bars"]["status"]
    snapshot["snapshot_status"] = "blind" if bars_state == "missing" else "built"
    snapshot["snapshot_status_reason"] = {"missing": "spx_bars_missing", "late": "cut_bar_missing"}.get(bars_state)
    snapshot["build_seconds"] = round(time.monotonic() - started, 3)
    bar_rows = session_bar_rows(conn, bars, day, row_origin, cut_at, written_at)
    return snapshot, read.observations, bar_rows, read.feed_states


def snapshot_row_for_read(cut_at, row_origin, written_at, slot, slot_status):
    """The collect_snapshots row of a read at this cut, with its slot, lane and lateness."""
    day = session_clock.to_eastern(cut_at).date()
    lane = session_clock.lane_for_slot(slot)
    snapshot = store.base_snapshot_row("read", session_clock.iso_utc(cut_at), day.isoformat(), row_origin,
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
        live_twin = store.live_snapshot_at(conn, cut_at_iso)
        if live_twin and live_twin["source_files_json_text"]:
            return json.loads(live_twin["source_files_json_text"])
        return {}
    sizes = {feed: mirai_files.file_bytes(template, day) for feed, template in SOURCE_FILES.items()}
    return {feed: size for feed, size in sizes.items() if size is not None}


def collect_clock(read, cut_at, day, half_days):
    """Minutes finished and minutes left in the session (no feed: the clock is the clock)."""
    since_open = session_clock.minutes_since_open(cut_at)
    read.add_observation("minutes_since_session_open", since_open)
    read.add_observation("minutes_until_session_close", session_clock.session_minutes(day, half_days) - since_open)


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
    read.add_observation("range_so_far_points", round(high - low, store.STORED_DECIMALS))
    read.add_observation("move_points", round(closes[-1] - first["open"], store.STORED_DECIMALS), since="open")
    for since, minutes in MOVE_SINCE_MINUTES.items():
        value = round(closes[-1] - closes[-1 - minutes], store.STORED_DECIMALS) if len(closes) > minutes else None
        read.add_observation("move_points", value, blank="not_formed_yet", since=since)


def collect_watchlist_quotes(read, quotes_rows, bars):
    """Prior close, VIX family, fund changes, sector and largest-stock counts and the 10-year yield from the quotes."""
    row = mirai_files.newest_row_with(quotes_rows, "quotes")
    polled_at = row["at"] if row else None
    read.note_feed("watchlist_quotes", polled_at, received_at=polled_at, present=bool(row))
    quotes = row["quotes"] if row else {}
    spx = quotes.get("$SPX") or {}
    read.add_observation("prior_close_points", spx.get("close"))
    last = bars[-1]["close"] if bars else None
    move = round(last - spx["close"], store.STORED_DECIMALS) if last is not None and spx.get("close") else None
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
        yield_change = round((tnx["last"] - tnx["close"]) * TNX_QUOTE_PER_YIELD_PCT, store.STORED_DECIMALS)
    read.add_observation("ten_year_yield_annual_pct", yield_pct, blank=blank_reason_for_symbol(row))
    read.add_observation("ten_year_yield_change_basis_points", yield_change, blank=blank_reason_for_symbol(row))


def blank_reason_for_symbol(quotes_row):
    """Why a quoted value is blank: no quotes row at all, or the provider left this symbol out."""
    return "provider_refused_symbol" if quotes_row else "feed_missing"


def change_from_prior_close_pct(quotes, symbol):
    """A symbol's last quote against its prior close in percent, or None when either is missing."""
    quote = quotes.get(symbol) or {}
    if not quote.get("last") or not quote.get("close"):
        return None
    return round((quote["last"] / quote["close"] - 1) * 100, store.STORED_DECIMALS)


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
        portion = round(advancing / total, store.STORED_DECIMALS) if total > 0 else None
        reason = "no_activity"
    read.add_observation("nyse_advancing_portion", portion, blank=reason)


def collect_options_scan(read, scans):
    """VWAP, walls, gamma and ATM IV from the newest native scan; the straddle from the newest scan with an ok ruler."""
    present = bool(scans)
    scan = scans[-1] if present else {}
    read.note_feed("options_scan", scan.get("at"), received_at=scan.get("at"), present=present)
    views = scan.get("gex_views") or {}
    read.add_observation("vwap_points", scan.get("vwap"))
    for level in registry.WALL_TYPES:
        read.add_observation("level_price_points", views.get(f"{level}_gamma"), level_type=level)
        read.add_observation("level_gamma_share_portion", views.get(f"{level}_gamma_share"), level_type=level)
    read.add_observation("dealer_net_gamma_usd", views.get("net_gex"))
    read.add_observation("dealer_gamma_sign", scan.get("gamma_sign"))
    atm_iv = scan.get("atm_iv")
    atm_iv_pct = round(atm_iv * 100, store.STORED_DECIMALS) if atm_iv else None
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
    bullish = round(window["bull_usd"] / sided, store.STORED_DECIMALS) if counted and sided > 0 else None
    read.add_observation("bullish_portion_of_sided_premium", bullish, blank="no_activity" if counted else None)
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
    read.add_observation("overnight_high_points", night["high"] if night else None, symbol="/ES")
    read.add_observation("overnight_low_points", night["low"] if night else None, symbol="/ES")


def collect_events(read, conn, calendar, day, cut_at, row_origin, written_at):
    """Events still ahead today, taken from the store's calendar facts as they were known at the cut."""
    if calendar is not None:
        first_copy = not store.has_calendar_facts(conn)
        store.write_calendar_facts(conn, calendar_fact_rows(calendar, row_origin, written_at, first_copy))
    known_at = store.calendar_known_at(conn, session_clock.iso_utc(cut_at))
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
    left = [(starts_at, kind) for starts_at, kind, _ in store.calendar_events_known(conn, day.isoformat(), cut_iso)
            if starts_at > cut_iso]
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
        fact["fact_record_id"] = f"{fact['fact_kind']}|{fact['fact_id']}|{fact['fact_version']}"
    return facts


def collect_headlines(read, headlines):
    """Headlines captured in the last 60 minutes, counted only while the feed is fresh; the coverage twin is blank."""
    read.note_feed("news_headlines", headlines["newest_at"], received_at=headlines["newest_at"],
                   present=headlines["present"])
    counted = read.feed_ok("news_headlines")
    read.add_observation("headlines_last_60_minutes_count", headlines["count"] if counted else None,
                         kind="all")
    read.add_observation("feed_coverage_last_60_minutes_portion", None, blank="not_known_at_read",
                         source="news_headlines",
                         feed="news_headlines")


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
        for side in registry.CLAUDE_SIDES:
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
    hours_left = round((expires_at - cut_at).total_seconds() / 3600, 2)
    if hours_left <= 0:
        state = "expired"
    elif login["state"] != "ok":
        state = "unknown"
    elif hours_left <= registry.LOGIN_RENEW_WARNING_DAYS * 24:
        state = "renew_soon"
    else:
        state = "ok"
    read.add_observation("login_state", state, login="schwab")
    read.add_observation("login_hours_until_expiry", hours_left, login="schwab")
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


def session_bar_rows(conn, bars, day, row_origin, cut_at, written_at):
    """Price rows for bars not yet stored under this origin: a bar of the read's own slot, or any bar a rebuild writes
    once the session has closed, is known 62 s after it ends; anything else is known when it is written."""
    already = store.stored_bar_times(conn, day.isoformat(), row_origin)
    own_slot_starts_at = cut_at - timedelta(minutes=session_clock.SLOT_MINUTES)
    session_closed = written_at >= session_clock.session_close(day, store.half_day_sessions(conn))
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
        rows.append(store.bar_price_row(bar, day.isoformat(), row_origin, priced_at, known_at, rule))
    return rows
