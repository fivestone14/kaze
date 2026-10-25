# The registry: every measure Kaze may store (name, meaning, unit, lane, fact group, subjects, source feed, blank rule),
# every component row (feeds, the login, the collector, the range engine, the read lanes, the push/pull factors with the
# phone's question and answer words, the dials, the rules and the trust tests, their numbers in components.py) and the
# fixed word lists. Every word here has a writer; the store writer refuses anything not listed.
import hashlib
import json
import re
from collections import defaultdict

from engine import paths, session_clock
from engine.store import components

REGISTRY_VERSION = "1"
# One date for the whole registry: when these rows were defined and the first session Kaze served (2026-10-16);
# available_since_trade_date carries it for every measure (rebuilt rows may reach further back).
REGISTRY_DATE = "2026-10-16"
DEFINED_AT = f"{REGISTRY_DATE}T00:00:00+00:00"
BUILDER_NAME = "kaze_collector"
BUILDER_VERSION = "1"
MEASURE_NAME_MAX_CHARS = 40
LOGIN_RENEW_WARNING_DAYS = 2
BAR_ARRIVAL_SECONDS = 62
BAR_ARRIVAL_RULE = f"bar_end_plus_{BAR_ARRIVAL_SECONDS}_seconds"

# The two lanes a forecast fact may reach: range (how far price travels, the band) and push_pull (which way: the
# factors that push price up or pull it down).
LANES = ("range", "push_pull", "label", "id", "health")
# The four value columns of every long table; a row fills exactly one of them or carries a blank reason.
VALUE_COLUMNS = ("value_number", "value_bool", "value_text", "value_at")
VALUE_COLUMN_FOR_TYPE = {"number": "value_number", "whole_number": "value_number", "money_usd": "value_number",
                         "yes_no": "value_bool", "word": "value_text", "text": "value_text",
                         "moment": "value_at", "date": "value_at"}
FEED_STATUSES = ("ok", "late", "stale", "missing")
ROW_ORIGINS = ("live_frozen", "live_rebuilt", "history_rebuilt")
LIVE_VALUE_ORIGIN = "live"
VALUE_ORIGINS = (LIVE_VALUE_ORIGIN,)
SESSION_DATE_FACT, SCHEDULED_EVENT_FACT = "session_date", "scheduled_event"
FACT_KINDS = (SESSION_DATE_FACT, SCHEDULED_EVENT_FACT)
SNAPSHOT_KINDS = ("read", "nightly_check", "nightly_history_fit")
FIT_SNAPSHOT_KIND = "nightly_history_fit"
SNAPSHOT_STATUSES = ("built", "blind", "missed", "thin")
SLOT_STATUSES = ("made", "late", "missed", "not_scheduled")
KNOWN_AT_RULES = ("written_at", BAR_ARRIVAL_RULE, "file_modified_at")
LOGIN_STATES = ("ok", "renew_soon", "expired", "unknown")
# Blank words: the outcome rule's bars_missing and the ledger's too_few_sessions are also blank reasons.
BARS_MISSING = components.BARS_MISSING
TOO_FEW_SESSIONS = components.TOO_FEW_SESSIONS
NOT_FORMED_REASON = "not_formed_yet"
NOT_PRICEABLE_REASON = "not_priceable"
NOT_APPLICABLE = "not_applicable"
BLANK_REASONS = (
    "feed_missing", "feed_stale", NOT_FORMED_REASON, "partial_coverage", "provider_refused_symbol", "no_activity",
    "not_known_at_read", "calendar_not_covering_day", BARS_MISSING, NOT_PRICEABLE_REASON, NOT_APPLICABLE,
    "none_left_today", "too_old", TOO_FEW_SESSIONS)
# Decision word lists (the READS pillar), one home for the SQL CHECK lists and the code.
SHARED_SCOPE = "shared"
STORAGE_SCOPES = (SHARED_SCOPE,)
RUN_KINDS = ("live", "history_replay")
RUN_KIND_FOR_ORIGIN = {"live_frozen": "live", "live_rebuilt": "live", "history_rebuilt": "history_replay"}
DECISION_OK, DECISION_BLIND, DECISION_CLIPPED = "ok", "blind", "clipped"
DECISION_STATUSES = (DECISION_OK, DECISION_BLIND, DECISION_CLIPPED)
VALUE_ROLE, RAW_ROLE, APPLIED_ROLE = "value", "raw", "applied"
VALUE_ROLES = (VALUE_ROLE, RAW_ROLE, APPLIED_ROLE)
# Outcome word lists (the RESULTS pillar): what happened after a read and how a decision graded.
MARKET_WINDOW_KIND, FORECAST_GRADE_KIND = "market_window", "forecast_grade"
OUTCOME_KINDS = (MARKET_WINDOW_KIND, FORECAST_GRADE_KIND)
LABEL_RESOLVED, LABEL_PARTIAL_BARS, LABEL_UNPRICEABLE = "resolved", "partial_bars", "unpriceable"
LABEL_STATUSES = (LABEL_RESOLVED, LABEL_PARTIAL_BARS, BARS_MISSING, LABEL_UNPRICEABLE)
LABELS_THAT_COUNT = (LABEL_RESOLVED, LABEL_PARTIAL_BARS)
END_BAR_MISSING_LABEL_REASON = "end_bar_missing"
NO_READ_PRICE_LABEL_REASON = "no_read_price"
VALUE_STATUS_OK = "ok"
VALUE_STATUSES = (VALUE_STATUS_OK, BARS_MISSING, NOT_APPLICABLE)
BAND_SIDES = ("upper", "lower")
# The four prices a window is measured by, each with its price and move measure; the window writer and every
# reader use these names, never a name built from parts.
WINDOW_SIDES = ("end", "average", "high", "low")
WINDOW_PRICE_MEASURE = {side: f"{side}_price_points" for side in WINDOW_SIDES}
WINDOW_MOVE_MEASURE = {side: f"{side}_move_points" for side in WINDOW_SIDES}
GRADE_START_PRICE_MEASURE = "spx_price_points"
SPX_SYMBOL = "$SPX"
SPY_SYMBOL, TEN_YEAR_SYMBOL = "SPY", "$TNX"
# The symbols the nightly keeps daily bars for, with the instrument kind each is stored under.
DAILY_BAR_SYMBOLS = {SPX_SYMBOL: "index", TEN_YEAR_SYMBOL: "rate"}
# The three grade words and what yes means for each: the measure, its band subject (None: the channel's own rung
# subject) and the stored value that says yes. The scoreboard, the phone and the data map all read this.
GRADE_WORD_MEASURE = {"ends_inside": ("was_inside_band", components.BAND, 1),
                      "average_inside": ("was_inside_band", components.AVERAGE_BAND, 1),
                      "never_left": ("stayed_inside_rung", None, 1)}
GRADE_WORDS = tuple(GRADE_WORD_MEASURE)
POOLED = "pooled"
SUBJECT_KINDS = (
    "adjustment", "band", "consumer", "factor", "feed", "horizon", "impact", "kind", "level_type", "login",
    "rung_points", "side", "since", "strike_points", "symbol", "table_column", "whole", "window_minutes")

# Feeds: name, what it is, Mirai's file (the template mirai_files reads), how old its newest input may be, and how
# often it lands: live (a tick about every second), minute (through the session, judged by its age), once_a_day (the
# night's futures, the daily closes, the login check) or hand_kept (the calendar). The live price is no read's input: only the phone's
# feed row and its stream read it.
FEED_CADENCES = ("live", "minute", "once_a_day", "hand_kept")
LIVE_PRICE_FEED = "spx_live_price"
FEEDS = (
    (LIVE_PRICE_FEED, "SPX's last trade, streamed by Mirai's lob-collector", paths.MIRAI_FILES[LIVE_PRICE_FEED], 10,
     "live"),
    ("spx_bars", "SPX 1-minute bars", paths.MIRAI_FILES["spx_bars"], 180, "minute"),
    ("watchlist_quotes", "one minute's quotes for the watchlist (SPX, VIX family, funds, stocks, rates)",
     paths.MIRAI_FILES["watchlist_quotes"], 180, "minute"),
    ("watchlist_bars", "one minute's bars for the watchlist (SPY, /ES, $ADD, the leaders, sectors)",
     paths.MIRAI_FILES["watchlist_bars"], 180, "minute"),
    ("daily_bars", "daily bars of $SPX and $TNX, ten years, fetched after each close", paths.MIRAI_FILES["daily_bars"],
     2 * 86400, "once_a_day"),
    ("nyse_breadth", "NYSE advancers and decliners carried in the quotes rows' bars field",
     paths.MIRAI_FILES["watchlist_quotes"], 240, "minute"),
    ("options_scan", "the same-day SPX options scan: walls, VWAP, straddle ruler, dealer gamma",
     paths.MIRAI_FILES["options_scan"], 300, "minute"),
    ("options_tape", "every same-day SPX option trade with its quote at the time", paths.MIRAI_FILES["options_tape"],
     180, "minute"),
    ("futures_bars", "/ES 1-minute bars through the night", paths.MIRAI_FILES["futures_bars"], 86400, "once_a_day"),
    ("event_calendar", "the hand-kept calendar of scheduled market events", paths.EVENT_CALENDAR_FILE, 100 * 86400,
     "hand_kept"),
    ("claude_forecast", "Mirai's 30-minute Claude forecast, the newest final answer",
     paths.MIRAI_FILES["claude_forecast"], 2400, "minute"),
    ("news_headlines", "headlines captured from the news feeds", paths.MIRAI_FILES["news_headlines"], 1800,
     "minute"),
    ("schwab_login", "the Schwab sign-in every Schwab feed depends on (7-day login)",
     paths.MIRAI_FILES["schwab_login"], 2 * 86400, "once_a_day"),
)
FEED_NAMES = tuple(feed[0] for feed in FEEDS)
FEEDS_WITH_LINE_COUNTS = ("spx_bars", "watchlist_quotes", "watchlist_bars", "options_scan", "options_tape",
                          "claude_forecast", "news_headlines")
LOGIN_NAMES = ("schwab",)
CONSUMERS = {"range_engine": ("spx_bars",)}


def measure_row(name, meaning, unit, value_type, lane=None, group=None, subjects="whole", feed=None,
                blank="never blank in session", table="collect_observations", pillar="collect", words=None, low=None,
                high=None, overlap=None, leans="no_direction", neutral=None, history=False, carry=None, twin=None,
                available_from=None):
    """One registry_measures row; the arguments are the design's columns in plain words."""
    return {
        "measure_name": name, "measure_version": 1, "plain_meaning_text": meaning, "pillar_name": pillar,
        "stored_in_table_name": table, "subject_kinds_text": "|".join(sorted(subjects.split("|"))),
        "value_type": value_type, "value_column": VALUE_COLUMN_FOR_TYPE[value_type], "unit": unit,
        "allowed_words_text": "|".join(words) if words else None, "allowed_minimum_number": low,
        "allowed_maximum_number": high, "signal_lane": lane, "information_group_name": group,
        "overlap_group_name": overlap, "positive_value_means": leans, "neutral_value_number": neutral,
        "is_history_derived": int(history), "source_feed_name": feed, "blank_means_text": blank,
        "max_carry_forward_seconds": carry, "coverage_twin_measure_name": twin,
        "available_since_trade_date": REGISTRY_DATE, "available_from_minutes_after_open": available_from,
        "defined_at": DEFINED_AT,
    }


PUSH_PULL_LANE_DEFAULTS = dict(lane="push_pull", overlap="direction_all_sources", leans="leans_up", neutral=0.0)
DECISION_VALUE = dict(table="reads_decision_values", pillar="reads")
OUTCOME_VALUE = dict(table="results_outcome_values", pillar="results")
EVALUATION_VALUE = dict(table="results_evaluations", pillar="results")
NO_END_BAR = f"{BARS_MISSING} when the window's last bar never reached the store"
NO_START = f"{NOT_APPLICABLE} when the read had no price; " + NO_END_BAR
NO_WINDOW_MOVE = f"{NOT_APPLICABLE} when the read had no price to measure from; " + NO_END_BAR
NO_GRADE = "the band's own reason when the decision was blind; " + NO_WINDOW_MOVE
# A measure born on the snapshot (or the fit) and copied onto every decision that used it.
BOTH_TABLES = "collect_observations|reads_decision_values"
NO_FIT_KNOWN_MEANING = f"{TOO_FEW_SESSIONS} when no fit was known at the cut"
NO_SPEED = f"{NOT_FORMED_REASON} before the session's second bar"
POINTS_RANGE = dict(low=1000, high=20000)
SCAN_MISSING = "feed_missing when no native scan has landed today"
BARS_MISSING_MEANING = "feed_missing when no bar has landed today"
SYMBOL_MISSING = "provider_refused_symbol when the quote is missing"
NO_LOGIN_LINE = "not_known_at_read on rebuilt rows or when the auth watch printed ?"
TAPE_BLANK = "feed_missing when the tape is absent; feed_stale when its newest trade is older than the stale limit"
TAPE_TWIN = "feed_coverage_last_30_minutes_portion"

MEASURES = [
    # clock (no feed: the clock is the clock)
    measure_row("minutes_since_session_open", "whole session minutes finished by the cut", "minutes", "whole_number",
                "range", "session_clock", low=0, high=390),
    measure_row("minutes_until_session_close", "minutes from the cut to the close", "minutes", "whole_number",
                "range", "session_clock", low=0, high=390),
    # price
    measure_row("spx_price_points", "SPX close of the last finished 1-minute bar", "points", "number", "id",
                "spx_level", feed="spx_bars", blank=BARS_MISSING_MEANING, **POINTS_RANGE),
    measure_row("prior_close_points", "SPX official close of the prior session, from the quotes feed", "points",
                "number", "id", "spx_level", feed="watchlist_quotes",
                blank="feed_missing when no quotes row has landed today", **POINTS_RANGE),
    measure_row("today_open_points", "the first 1-minute bar's open (may be a stale first print)", "points", "number",
                "id", "spx_level", feed="spx_bars", blank=BARS_MISSING_MEANING, **POINTS_RANGE),
    measure_row("session_high_points", "highest bar high so far, the first bar's stale print ignored", "points",
                "number", "id", "spx_level", feed="spx_bars", blank=BARS_MISSING_MEANING, **POINTS_RANGE),
    measure_row("session_low_points", "lowest bar low so far, the first bar's stale print ignored", "points", "number",
                "id", "spx_level", feed="spx_bars", blank=BARS_MISSING_MEANING, **POINTS_RANGE),
    measure_row("vwap_points", "volume-weighted average price from the options scan", "points", "number", "id",
                "spx_level", feed="options_scan", blank=SCAN_MISSING, **POINTS_RANGE),
    measure_row("true_open_points", "the open SPX would have printed had every stock traded: the prior close plus "
                "/ES's move from its last bar of the prior session to its 09:30 bar", "points", "number", "id",
                "spx_level", feed="watchlist_bars",
                blank="feed_missing when either /ES bar is absent; not_applicable across a contract roll",
                **POINTS_RANGE),
    measure_row("range_so_far_points", "session high minus session low", "points", "number", "range", "spx_move_size",
                feed="spx_bars", blank=BARS_MISSING_MEANING, low=0, high=2000),
    measure_row("move_points", "SPX close now minus its close at the since point (signed)", "points", "number",
                group="spx_move_sign", subjects="since", feed="spx_bars",
                blank="not_formed_yet before the since point exists today", low=-2000, high=2000,
                **PUSH_PULL_LANE_DEFAULTS),
    # options
    measure_row("same_day_straddle_mid_points",
                "at-the-money same-day call plus put at mid, from the scan ruler (scan value / 0.85)", "points",
                "number", "range", "options_premium_size", feed="options_scan",
                blank="not_priceable when the scan ruler is not ok", low=0, high=500),
    measure_row("opening_straddle_mid_points", "the same straddle as it stood at the open", "points", "number", "range",
                "options_premium_size", feed="options_scan", blank="not_priceable when the scan ruler is not ok",
                low=0, high=500),
    measure_row("atm_option_mid_points", "newest tape quote mid of the at-the-money same-day option on this side",
                "points", "number", "id", "atm_option_quotes", subjects="side|strike_points", feed="options_tape",
                blank="no_activity when the strike has not traded today", low=0, high=500),
    measure_row("same_day_atm_iv_calendar_year_pct", "at-the-money implied volatility of the same-day options", "pct",
                "number", "range", "options_implied_vol", feed="options_scan", blank=SCAN_MISSING, low=0, high=500),
    measure_row("level_price_points", "price of a dealer level from the scan", "points", "number", "id",
                "level_prices", subjects="level_type", feed="options_scan", blank=SCAN_MISSING, **POINTS_RANGE),
    measure_row("level_gamma_share_portion", "share of total gamma sitting at the level", "portion", "number", "range",
                "dealer_gamma_shape", subjects="level_type", feed="options_scan", blank=SCAN_MISSING, low=0, high=1),
    measure_row("same_day_open_interest_contracts", "contracts held overnight in a same-day strike on one side, "
                "from the newest native scan, on the first read and the read fifty minutes before the close",
                "contracts", "whole_number", "id", "expiring_open_interest", subjects="side|strike_points",
                feed="options_scan", blank=SCAN_MISSING + "; no_activity when the scan lists no same-day strike; "
                "not kept on other reads", low=0),
    measure_row("dealer_net_gamma_usd", "dealers' net gamma across the same-day chain", "usd", "number", "range",
                "dealer_gamma_size", feed="options_scan", blank=SCAN_MISSING),
    measure_row("dealer_gamma_sign", "the scan's own word for the dealer gamma regime", "word", "word", "range",
                "dealer_gamma_shape", feed="options_scan", blank=SCAN_MISSING),
    # VIX family
    measure_row("vix_calendar_year_pct", "last quote of a VIX-family index", "pct", "number", "range",
                "implied_vol_level", subjects="symbol", feed="watchlist_quotes", blank=SYMBOL_MISSING, low=0, high=500),
    measure_row("vix_prior_close_calendar_year_pct", "prior close of a VIX-family index", "pct", "number", "id",
                "implied_vol_prior_close", subjects="symbol", feed="watchlist_quotes", blank=SYMBOL_MISSING,
                low=0, high=500),
    # breadth
    measure_row("nyse_advancing_count", "NYSE advancing issues at the newest breadth bar", "count", "whole_number",
                "id", "breadth_counts", feed="nyse_breadth", blank="feed_missing when no breadth bar has landed",
                low=0, high=10000),
    measure_row("nyse_declining_count", "NYSE declining issues at the newest breadth bar", "count", "whole_number",
                "id", "breadth_counts", feed="nyse_breadth", blank="feed_missing when no breadth bar has landed",
                low=0, high=10000),
    measure_row("nyse_advancing_portion", "advancers over advancers plus decliners", "portion", "number",
                group="breadth_side", feed="nyse_breadth", blank="feed_missing when no breadth bar has landed",
                low=0, high=1, lane="push_pull", overlap="direction_all_sources", leans="leans_up", neutral=0.5),
    measure_row("breadth_net_change_count", "NYSE advancers less decliners now against a window of minutes earlier",
                "count", "whole_number", group="breadth_change", subjects="window_minutes", feed="watchlist_bars",
                blank="not_formed_yet until the window has formed; provider_refused_symbol when the $ADD bars are "
                "absent from landed rows; feed_missing when no rows have landed",
                low=-6000, high=6000, **PUSH_PULL_LANE_DEFAULTS),
    # leaders, rates, dollar, oil
    measure_row("fund_change_from_prior_close_pct", "a fund's last quote against its prior close", "pct", "number",
                group="fund_change_sign", subjects="symbol", feed="watchlist_quotes", blank=SYMBOL_MISSING,
                low=-50, high=50, **PUSH_PULL_LANE_DEFAULTS),
    measure_row("fund_change_window_pct", "a fund's bar close now against its close a window of minutes earlier",
                "pct", "number", group="fund_change_window", subjects="symbol|window_minutes", feed="watchlist_bars",
                blank="not_formed_yet until the window has formed; provider_refused_symbol when the fund's bars "
                      "are missing", low=-50, high=50, **PUSH_PULL_LANE_DEFAULTS),
    measure_row("sector_funds_up_count", "how many of the 11 sector funds are above their prior close", "count",
                "whole_number", group="sector_breadth", feed="watchlist_quotes",
                blank="partial_coverage until all 11 quote", low=0, high=11, lane="push_pull",
                overlap="direction_all_sources", leans="leans_up", neutral=5.5),
    measure_row("largest_stocks_up_count", "how many of the 9 largest S&P stocks are above their prior close", "count",
                "whole_number", group="largest_stocks_breadth", feed="watchlist_quotes",
                blank="partial_coverage until all 9 quote", low=0, high=9, lane="push_pull",
                overlap="direction_all_sources", leans="leans_up", neutral=4.5),
    measure_row("ten_year_yield_annual_pct", "10-year Treasury yield", "pct", "number", "id", "rates_level",
                feed="watchlist_quotes", blank=SYMBOL_MISSING, low=0, high=25),
    measure_row("ten_year_yield_change_basis_points", "10-year yield change from its prior close", "basis_points",
                "number", group="rates_change", feed="watchlist_quotes", blank=SYMBOL_MISSING, low=-200, high=200,
                **PUSH_PULL_LANE_DEFAULTS),
    # overnight
    measure_row("overnight_high_points", "highest /ES 1-minute bar from 18:00 to 09:30", "points", "number", "id",
                "overnight_levels", subjects="symbol", feed="futures_bars",
                blank="feed_missing when the night file is absent", **POINTS_RANGE),
    measure_row("overnight_low_points", "lowest /ES 1-minute bar from 18:00 to 09:30", "points", "number", "id",
                "overnight_levels", subjects="symbol", feed="futures_bars",
                blank="feed_missing when the night file is absent", **POINTS_RANGE),
    # events (read from the store's calendar rows as known at the cut)
    measure_row("minutes_until_next_event", "minutes from the cut to the next scheduled event today", "minutes",
                "number", "range", "event_clock", subjects="impact", feed="event_calendar",
                blank="none_left_today after the day's last event; calendar_not_covering_day outside its span",
                low=0, high=600),
    measure_row("events_left_today_count", "scheduled events still ahead today", "count", "whole_number", "range",
                "event_clock", feed="event_calendar", blank="calendar_not_covering_day outside the calendar's span",
                low=0, high=50),
    measure_row("next_event_kind", "the calendar's kind word for the next event today", "word", "word", "id",
                "event_kind", feed="event_calendar", blank="none_left_today after the day's last event"),
    measure_row("does_calendar_cover_day", "the event calendar's span includes this session", "yes_no", "yes_no",
                "health", "calendar_health", feed="event_calendar"),
    # options tape
    measure_row("options_trades_last_30_minutes_count", "same-day option trades in the 30 minutes to the cut", "count",
                "whole_number", "range", "options_activity", feed="options_tape", blank=TAPE_BLANK, low=0,
                twin=TAPE_TWIN),
    measure_row("options_call_premium_last_30_minutes_usd",
                "dollars of call premium traded in the 30 minutes to the cut",
                "usd", "money_usd", "range", "options_premium_flow", feed="options_tape", blank=TAPE_BLANK, low=0,
                twin=TAPE_TWIN),
    measure_row("options_put_premium_last_30_minutes_usd", "dollars of put premium traded in the 30 minutes to the cut",
                "usd", "money_usd", "range", "options_premium_flow", feed="options_tape", blank=TAPE_BLANK, low=0,
                twin=TAPE_TWIN),
    measure_row("bullish_portion_of_sided_premium",
                "share of sided single-leg premium that was bullish (calls bought, puts sold)", "portion", "number",
                group="options_flow_side", feed="options_tape",
                blank="no_activity when no trade had a side; " + TAPE_BLANK,
                low=0, high=1, lane="push_pull", overlap="direction_all_sources", leans="leans_up", neutral=0.5,
                twin=TAPE_TWIN),
    measure_row("urgent_orders_count", "signed urgent option orders in the window: bursts over three strikes on "
                "one side within two seconds, and prints of fifty contracts or more, bullish less bearish; every "
                "trade condition counts (multi-leg legs included)", "count", "whole_number",
                group="options_urgent_flow", subjects="kind|window_minutes", feed="options_tape", blank=TAPE_BLANK,
                low=-10000, high=10000, twin=TAPE_TWIN, **PUSH_PULL_LANE_DEFAULTS),
    measure_row("tape_gap_markers_last_30_minutes_count", "collector gap markers inside the 30-minute window", "count",
                "whole_number", "health", "tape_health", subjects="feed", feed="options_tape", blank=TAPE_BLANK, low=0),
    measure_row("feed_coverage_last_30_minutes_portion",
                "share of the 30-minute window the feed covered: 1 minus gap markers over 30 (one per lost minute)",
                "portion", "number", "health", "feed_coverage", subjects="feed", blank=TAPE_BLANK, low=0, high=1),
    # headlines (the poller leaves no poll stamps, so the count has no coverage twin)
    measure_row("headlines_last_60_minutes_count", "headlines captured in the 60 minutes to the cut", "count",
                "whole_number", "range", "news_volume", subjects="kind", feed="news_headlines",
                blank="feed_missing when today's file is absent; feed_stale when the newest capture is too old",
                low=0),
    # Mirai's Claude forecast, carried as a raw input
    measure_row("mirai_claude_forecast_pct", "Mirai's Claude forecast for the horizon, chance of this side", "pct",
                "number", "id", "mirai_claude_forecast", subjects="horizon|side", feed="claude_forecast",
                blank="feed_missing before the day's first final forecast; too_old past the carry limit", low=0,
                high=100, carry=2400),
    measure_row("mirai_claude_base_rate_pct", "the base rate Claude was shown for that side", "pct", "number", "id",
                "mirai_claude_forecast", subjects="horizon|side", feed="claude_forecast",
                blank="feed_missing before the day's first final forecast", low=0, high=100, carry=2400),
    measure_row("mirai_claude_read_at", "the moment of the Claude read the forecast belongs to", "at", "moment", "id",
                "mirai_claude_forecast", feed="claude_forecast",
                blank="feed_missing before the day's first final forecast", carry=2400),
    # feed, login and consumer health
    measure_row("feed_status", "the feed's state at the cut", "word", "word", "health", "feed_health",
                subjects="feed", words=FEED_STATUSES),
    measure_row("feed_age_seconds", "seconds from the feed's newest input to the cut", "seconds", "number", "health",
                "feed_health", subjects="feed", blank="feed_missing when nothing has landed today", low=0),
    measure_row("rows_received_today_count", "rows the feed has landed today by the cut", "count", "whole_number",
                "health", "feed_health", subjects="feed", low=0),
    measure_row("lines_skipped_today_count", "malformed lines skipped while reading the feed's file today", "count",
                "whole_number", "health", "feed_health", subjects="feed",
                blank="feed_missing when the file is absent", low=0),
    measure_row("login_state", "the Schwab login's state as Mirai's auth watch last reported it", "word", "word",
                "health", "login_health", subjects="login", feed="schwab_login", blank=NO_LOGIN_LINE,
                words=LOGIN_STATES),
    measure_row("login_hours_until_expiry", "hours from the cut to the Schwab login's expiry (negative once lapsed)",
                "hours", "number", "health", "login_health", subjects="login", feed="schwab_login",
                blank=NO_LOGIN_LINE),
    measure_row("login_expires_at", "when the Schwab login expires", "at", "moment", "health", "login_health",
                subjects="login", feed="schwab_login", blank=NO_LOGIN_LINE),
    measure_row("are_needed_feeds_all_ok", "every feed the consumer needs is ok at the cut", "yes_no", "yes_no",
                "health", "consumer_health", subjects="consumer"),
    # nightly checks
    measure_row("rebuild_identical_portion", "share of live values the rebuild reproduced exactly", "portion", "number",
                "health", "rebuild_parity", subjects="table_column", low=0, high=1),
    measure_row("reads_compared_count", "reads with both a live and a rebuilt row", "count", "whole_number", "health",
                "rebuild_parity", low=0),
    measure_row("values_mismatched_count",
                "live values the rebuild did not reproduce (tape and scan measures not counted)",
                "count", "whole_number", "health", "rebuild_parity", low=0),
    measure_row("tolerated_mismatches_count",
                "tape and scan values that differed, tolerated because those files land late",
                "count", "whole_number", "health", "rebuild_parity", low=0),
    # today's speed, swings, path and the options check, written on the read's snapshot by the range engine
    measure_row("today_typical_1_minute_move_points",
                "recency-weighted mean absolute 1-minute close change, half-life 15 minutes", "points", "number",
                "range", "spx_speed", feed="spx_bars", blank=NO_SPEED, available_from=2, low=0, table=BOTH_TABLES),
    measure_row("speed_fast_5_minute_half_life_points", "the same speed with a 5-minute half-life", "points", "number",
                "range", "spx_speed", feed="spx_bars", blank=NO_SPEED, available_from=2, low=0),
    measure_row("speed_slow_30_minute_half_life_points", "the same speed with a 30-minute half-life", "points",
                "number", "range", "spx_speed", feed="spx_bars", blank=NO_SPEED, available_from=2, low=0),
    measure_row(components.ACCELERATION_MEASURE,
                "fast speed over slow speed; above 1 the market is speeding up, below 1 slowing", "ratio", "number",
                "range", "spx_acceleration", feed="spx_bars", blank=NO_SPEED + "; no_activity when the slow speed is 0",
                available_from=2, low=0),
    measure_row("typical_swing_points", "speed times the square root of the horizon's minutes", "points", "number",
                "range", "spx_speed", subjects="horizon", feed="spx_bars", blank=NO_SPEED, available_from=2, low=0,
                table=BOTH_TABLES),
    measure_row("options_implied_swing_points", "straddle left scaled by the square root of window over minutes left",
                "points", "number", "range", "options_premium_size", subjects="horizon", feed="options_scan",
                blank="not_priceable when the read has no straddle", low=0),
    measure_row("options_over_tape_swing_ratio", "options-implied swing over the tape swing over the same minutes",
                "ratio", "number", "range", "options_vs_tape_check", subjects="horizon", feed="options_scan",
                blank="not_priceable when either swing is missing", low=0),
    measure_row("path_up_points", "sum of the window's rising 1-minute changes", "points", "number",
                group="path_moves", subjects="window_minutes", feed="spx_bars",
                blank="not_formed_yet until the window has formed", low=0,
                lane="push_pull", overlap="direction_all_sources", leans="leans_up", neutral=0.0),
    measure_row("path_down_points", "sum of the window's falling 1-minute changes, as a positive number", "points",
                "number", group="path_moves", subjects="window_minutes", feed="spx_bars",
                blank="not_formed_yet until the window has formed", low=0, lane="push_pull",
                overlap="direction_all_sources", leans="leans_down", neutral=0.0),
    measure_row("trend_straightness_ratio",
                "net move over total path in the window; 1 is one way, 0 is pure back-and-forth", "ratio", "number",
                group="path_straightness", subjects="window_minutes", feed="spx_bars",
                blank="not_formed_yet until the window has formed; no_activity when the path is 0", low=0, high=1,
                lane="push_pull", overlap="direction_all_sources", leans="no_direction", neutral=0.0),
    # history fits, written at nightly_history_fit cuts and copied onto the decisions that used them
    measure_row("stretch_multiplier", "swings that cover the band's share of past window endings on the same recipe",
                "multiplier", "number", "range", "band_stretch", subjects="band|horizon", history=True, low=0,
                blank=NO_FIT_KNOWN_MEANING, table=BOTH_TABLES),
    measure_row("stretch_recipe_name", "the speed recipe and grading window the stretch was fitted on", "word", "word",
                "id", "band_stretch", subjects="band|horizon", table=BOTH_TABLES),
    measure_row("fit_sessions_count", "sessions the history fit used", "count", "whole_number", "health",
                "fit_sample", subjects="horizon", low=0, table=BOTH_TABLES),
    measure_row("fit_windows_count", "windows the history fit used", "count", "whole_number", "health", "fit_sample",
                subjects="horizon", low=0, table=BOTH_TABLES),
    measure_row("average_price_shrink_ratio", "average-price band half-width over end-price band half-width", "ratio",
                "number", "range", "band_stretch", subjects="horizon", history=True, low=0, high=1,
                blank=NO_FIT_KNOWN_MEANING),
    # range forecast values (reads_decision_values)
    measure_row("band_centre_points", "price the band is centred on: the last close at the read", "points", "number",
                "id", "band_centre", subjects="horizon", blank="feed_missing when no bar has landed", **POINTS_RANGE,
                **DECISION_VALUE),
    measure_row("band_half_width_points", "half-width of the band, dials applied", "points", "number", "range",
                "band_size", subjects="band|horizon", blank="the speed's or the fit's reason", low=0,
                **DECISION_VALUE),
    measure_row("band_low_points", "the band's lower edge", "points", "number", "id", "band_edges",
                subjects="band|horizon", blank="the speed's or the fit's reason", **POINTS_RANGE, **DECISION_VALUE),
    measure_row("band_high_points", "the band's upper edge", "points", "number", "id", "band_edges",
                subjects="band|horizon", blank="the speed's or the fit's reason", **POINTS_RANGE, **DECISION_VALUE),
    measure_row("chance_stays_inside_rung_probability", "chance price never leaves the rung over the horizon",
                "probability", "number", "range", "rung_odds", subjects="horizon|rung_points", low=0, high=1,
                blank="the speed's or the fit's reason", **DECISION_VALUE),
    measure_row("width_multiplier", "a dial on the band's width, raw and applied", "multiplier", "number", "range",
                "band_size", subjects="adjustment", overlap="width_all_adjustments", low=0,
                blank="the speed's reason when the dial reads the speed", **DECISION_VALUE),
    measure_row("options_vs_tape_check_word", "the options cross-check's verdict: agree when the options-implied swing "
                "sits within the agree ratio of the tape swing either way, else differ", "word", "word", "range",
                "options_vs_tape_check", subjects="horizon", feed="options_scan", words=components.OPTIONS_CHECK_WORDS,
                blank="not_priceable when either swing is missing", **DECISION_VALUE),
    measure_row("fit_snapshot_id", "the nightly_history_fit cut whose multipliers the decision used", "id", "text",
                "id", "band_stretch", blank=NO_FIT_KNOWN_MEANING, **DECISION_VALUE),
    measure_row("chance_up_if_moves_probability", "chance the move is up, given it moves", "probability", "number",
                group="direction_call", low=0, high=1, lane="push_pull", overlap="direction_all_sources",
                leans="leans_up", neutral=0.5, **DECISION_VALUE),
    # what happened in each decision window (market_window outcomes) and the grade start every grade carries
    measure_row("grade_start_price_points", "price the grade measured from: the read's last close", "points", "number",
                "id", "grade_start", blank=NO_START, **POINTS_RANGE, **OUTCOME_VALUE),
    measure_row("grade_start_bar_at", "start of the 1-minute bar the grade measured from", "at", "moment", "id",
                "grade_start", blank=NO_START, **OUTCOME_VALUE),
    measure_row("grade_start_rule_name", "which grading rule set the start", "word", "word", "id", "grade_start",
                words=components.GRADE_START_RULE_NAMES, **OUTCOME_VALUE),
    measure_row(WINDOW_PRICE_MEASURE["end"], "close of the window's last bar", "points", "number", "label",
                "window_result", blank=NO_END_BAR, **POINTS_RANGE, **OUTCOME_VALUE),
    measure_row(WINDOW_PRICE_MEASURE["average"], "mean of the window's 1-minute closes", "points", "number", "label",
                "window_result", blank=NO_END_BAR, **POINTS_RANGE, **OUTCOME_VALUE),
    measure_row(WINDOW_PRICE_MEASURE["high"], "highest bar high inside the window", "points", "number", "label",
                "window_result", blank=NO_END_BAR, **POINTS_RANGE, **OUTCOME_VALUE),
    measure_row(WINDOW_PRICE_MEASURE["low"], "lowest bar low inside the window", "points", "number", "label",
                "window_result", blank=NO_END_BAR, **POINTS_RANGE, **OUTCOME_VALUE),
    measure_row(WINDOW_MOVE_MEASURE["end"], "end price minus grade-start price", "points", "number", "label",
                "window_result", blank=NO_WINDOW_MOVE, **OUTCOME_VALUE),
    measure_row(WINDOW_MOVE_MEASURE["average"], "average price over the window minus grade-start price", "points",
                "number", "label", "window_result", blank=NO_WINDOW_MOVE, **OUTCOME_VALUE),
    measure_row(WINDOW_MOVE_MEASURE["high"], "window high minus grade-start price", "points", "number", "label",
                "window_result", blank=NO_WINDOW_MOVE, **OUTCOME_VALUE),
    measure_row(WINDOW_MOVE_MEASURE["low"], "window low minus grade-start price", "points", "number", "label",
                "window_result", blank=NO_WINDOW_MOVE, **OUTCOME_VALUE),
    measure_row("minutes_covered_count", "window minutes with a bar held in the store", "count", "whole_number",
                "health", "window_coverage", blank=NO_END_BAR, low=0, high=390, **OUTCOME_VALUE),
    measure_row("stayed_inside_rung", "price never reached either edge of the rung during the window", "yes_no",
                "yes_no", "label", "rung_grade", subjects="rung_points", blank=NO_GRADE, **OUTCOME_VALUE),
    # how each range decision graded (forecast_grade outcomes)
    measure_row("was_inside_band", "the band's price (end or average) landed inside the band", "yes_no", "yes_no",
                "label", "band_grade", subjects="band", blank=NO_GRADE, **OUTCOME_VALUE),
    measure_row("band_miss_points", "points the band's price landed past the edge: above the top positive, below the "
                "bottom negative, 0 inside", "points", "number", "label", "band_grade", subjects="band",
                blank=NO_GRADE, **OUTCOME_VALUE),
    measure_row("band_used_ratio", "farthest excursion on this side over the end band's half-width; above 1 means "
                "price broke out that side", "ratio", "number", "label", "band_grade", subjects="band|side",
                blank=NO_GRADE, low=0, **OUTCOME_VALUE),
    # the push/pull factors on the read's snapshot (and each lean again on every decision, with the share applied)
    measure_row("push_pull_lean_probability", "a factor's lean: its chance of up if price moves; above even it "
                "pushes, below it pulls",
                "probability", "number", group="push_pull_lean", subjects="factor", low=0, high=1,
                blank="the input's reason: feed_missing, feed_stale, provider_refused_symbol, not_formed_yet, "
                      "no_activity, too_old", table=BOTH_TABLES, lane="push_pull", overlap="direction_all_sources",
                leans="leans_up", neutral=0.5),
    measure_row("push_pull_factor_text", "the factor's value in words, what the phone shows beside its lean", "text",
                "text", "id", "push_pull_description", subjects="factor", blank="the input's reason"),
    measure_row("level_distance_from_price_points", "a dealer wall's price less the read price (above is positive)",
                "points", "number", "range", "level_distances", subjects="level_type", feed="options_scan",
                blank=SCAN_MISSING + "; feed_missing when no bar has landed", low=-2000, high=2000),
    # the time-of-day ratios and the width cap, measured on the fit's history at read time
    measure_row("closing_window_multiplier", "the 80% mark of |end move| in swings over unclipped windows ending in "
                "the last 15 minutes, over the all-day mark", "multiplier", "number", "range", "band_stretch",
                subjects="horizon", history=True, low=0, blank=NO_FIT_KNOWN_MEANING, **DECISION_VALUE),
    measure_row("release_window_multiplier", "the 80% mark of |end move| in swings over windows a timed release "
                "lands in, over the all-day mark", "multiplier", "number", "range", "band_stretch", subjects="horizon",
                history=True, low=0, blank=f"{TOO_FEW_SESSIONS} until the history holds such a window",
                **DECISION_VALUE),
    measure_row("width_cap_multiplier", "the most the dials other than the Fed may widen the band: the history's 95% "
                "mark of |end move| in swings over its 80% mark", "multiplier", "number", "range", "band_stretch",
                subjects="horizon", history=True, low=1, blank=NO_FIT_KNOWN_MEANING, **DECISION_VALUE),
    # the trust ledger's metrics (results_evaluations)
    measure_row("trust_weight_portion", "the weight a factor or dial has earned for a channel; 0 is not earned",
                "portion", "number", "health", "trust_ledger", low=0, high=1,
                blank=f"{TOO_FEW_SESSIONS} under the minimum live sessions", **EVALUATION_VALUE),
    measure_row("brier_skill_portion", "1 less the Brier score with the lean added over the Brier score of the "
                "earned leans alone; above 0 beats them", "portion", "number", "health", "trust_ledger",
                blank=f"{TOO_FEW_SESSIONS} under the minimum live sessions", **EVALUATION_VALUE),
    measure_row("interval_score_gain_points", "band interval score without the dial less the score with it; above 0 "
                "means the dial helped", "points", "number", "health", "trust_ledger",
                blank=f"{TOO_FEW_SESSIONS} under the minimum live sessions", **EVALUATION_VALUE),
    measure_row("band_coverage_portion", "share of scored windows whose end landed inside the band", "portion",
                "number", "health", "trust_ledger", low=0, high=1,
                blank=f"{TOO_FEW_SESSIONS} under the minimum live sessions", **EVALUATION_VALUE),
]

MEASURES_BY_NAME = {row["measure_name"]: row for row in MEASURES}


WORD_LISTS = (LANES, FEED_STATUSES, FEED_CADENCES, ROW_ORIGINS, VALUE_ORIGINS, FACT_KINDS, SNAPSHOT_KINDS,
              SNAPSHOT_STATUSES, SLOT_STATUSES, KNOWN_AT_RULES, LOGIN_STATES, BLANK_REASONS, STORAGE_SCOPES, RUN_KINDS,
              DECISION_STATUSES, VALUE_ROLES, OUTCOME_KINDS, LABEL_STATUSES, VALUE_STATUSES, BAND_SIDES, GRADE_WORDS,
              SUBJECT_KINDS, components.GRADE_START_RULE_NAMES, components.WIDTH_DIALS, components.PUSH_PULL_FACTORS,
              components.EVALUATION_KINDS, components.EVALUATED_KINDS, components.VERDICTS,
              components.INTERVAL_METHODS, components.BASELINES, components.WALL_TYPES, components.CLAUDE_SIDES,
              components.OPTIONS_CHECK_WORDS)
WORD_SHAPE = re.compile(r"^[a-z0-9_$/]+$")


def check_registry():
    """Refuse a registry that breaks the rules: prose for a word, duplicates, unknown lanes, subjects or feeds."""
    # And a feed with an unknown cadence, or one fact reaching both the range lane and the push_pull lane.
    for words in WORD_LISTS:
        if len(set(words)) != len(words) or not all(WORD_SHAPE.match(word) for word in words):
            raise ValueError(f"registry word list {words} must be distinct lowercase single words")
    if len(MEASURES_BY_NAME) != len(MEASURES):
        raise ValueError("duplicate measure name in the registry")
    for name, _, _, _, cadence in FEEDS:
        if cadence not in FEED_CADENCES:
            raise ValueError(f"feed {name}: unknown cadence {cadence}")
    lanes_by_group = defaultdict(set)
    for row in MEASURES:
        name = row["measure_name"]
        if len(name) > MEASURE_NAME_MAX_CHARS or name != name.lower():
            raise ValueError(f"{name}: names are lowercase and {MEASURE_NAME_MAX_CHARS} characters or fewer")
        if row["signal_lane"] not in LANES:
            raise ValueError(f"{name}: unknown lane {row['signal_lane']}")
        if not row["information_group_name"]:
            raise ValueError(f"{name}: every measure names the fact it carries")
        for kind in row["subject_kinds_text"].split("|"):
            if kind not in SUBJECT_KINDS:
                raise ValueError(f"{name}: unknown subject kind {kind}")
        if row["source_feed_name"] and row["source_feed_name"] not in FEED_NAMES:
            raise ValueError(f"{name}: unknown feed {row['source_feed_name']}")
        if row["signal_lane"] in ("range", "push_pull"):
            lanes_by_group[row["information_group_name"]].add(row["signal_lane"])
    for group, lanes in lanes_by_group.items():
        if len(lanes) > 1:
            # one fact may reach the range lane (how far) or the push_pull lane (which way), never both
            raise ValueError(f"fact {group} reaches both range and push_pull")


def component_row(kind, name, meaning, parameters, version=components.COMPONENT_VERSION):
    """One registry_components row: the component's kind, name, version, meaning and its parameters with their hash."""
    parameters_text = json.dumps(parameters, sort_keys=True)
    return {
        "component_kind": kind, "component_name": name, "component_version": version, "revision_number": 1,
        "plain_meaning_text": meaning, "parameters_json_text": parameters_text,
        "parameters_sha256": hashlib.sha256(parameters_text.encode()).hexdigest(),
        "starting_trust_weight_portion": 0.0, "storage_scope": SHARED_SCOPE, "released_at": DEFINED_AT,
    }


def component_rows():
    """Every feed, the login, the builder, the range engine, the read lanes, the rules, factors, dials and tests."""
    rows = [component_row("feed", name, meaning, {"mirai_file": mirai_file, "stale_limit_seconds": stale_limit,
                                                  "cadence": cadence})
            for name, meaning, mirai_file, stale_limit, cadence in FEEDS]
    rows.extend(component_row("login", name, "the 7-day Schwab sign-in",
                              {"renew_warning_days": LOGIN_RENEW_WARNING_DAYS})
                for name in LOGIN_NAMES)
    rows.append(component_row("builder", BUILDER_NAME,
                              "reads Mirai's files as they stood at a cut and writes one snapshot",
                              {"registry_version": REGISTRY_VERSION}, version=BUILDER_VERSION))
    rows.append(component_row("forecaster", components.RANGE_ENGINE_NAME,
                              "today's speed times the square root of the horizon, stretched by the history fit",
                              components.RANGE_RECIPE_PARAMETERS, version=components.RANGE_ENGINE_VERSION))
    rows.extend(component_row("read_lane", name, f"reads every {cadence} minutes", {"cadence_minutes": cadence})
                for name, cadence in session_clock.READ_LANES.items())
    rows.append(component_row("grade_start_rule", components.GRADE_START_RULE,
                              "the grade starts at the read's price and minute",
                              components.GRADE_START_RULE_PARAMETERS, version=components.GRADE_START_RULE_VERSION))
    rows.append(component_row("outcome_rule", components.WINDOW_RULE[0], "what happened in a decision window, from "
                              "the store's bars", components.WINDOW_RULE_PARAMETERS, version=components.WINDOW_RULE[1]))
    rows.append(component_row("grade_rule", components.GRADE_RULE[0], "a range decision graded against its bands and "
                              "rung", components.GRADE_RULE_PARAMETERS, version=components.GRADE_RULE[1]))
    rows.extend(component_row(components.PUSH_PULL_FACTOR_KIND, name, components.PUSH_PULL_FACTOR_MEANING[name],
                              {**components.PUSH_PULL_FACTOR_PARAMETERS[name],
                               "label": components.PUSH_PULL_FACTOR_LABEL[name],
                               "answers": components.PUSH_PULL_ANSWER_WORDS[name]})
                for name in components.PUSH_PULL_FACTORS)
    rows.extend(component_row(components.WIDTH_DIAL_KIND, name, components.WIDTH_DIAL_MEANING[name],
                              components.WIDTH_DIAL_PARAMETERS[name]) for name in components.WIDTH_DIALS)
    rows.append(component_row("trust_test", components.LEAN_TEST[0], "a lean's Brier skill, added to the earned "
                              "leans, on live moved windows", components.LEAN_TEST_PARAMETERS,
                              version=components.LEAN_TEST[1]))
    rows.append(component_row("trust_test", components.DIAL_TEST[0], "a dial's band interval score gain on live "
                              "windows, on top of the dials earned before it", components.DIAL_TEST_PARAMETERS,
                              version=components.DIAL_TEST[1]))
    return rows


def feed_stale_limit_seconds(feed_name):
    """How old a feed's newest input may be at the cut before its status is stale."""
    return next(feed[3] for feed in FEEDS if feed[0] == feed_name)


def feed_cadence(feed_name):
    """How often a feed lands: one of FEED_CADENCES."""
    return next(feed[4] for feed in FEEDS if feed[0] == feed_name)


def subject_parts(key):
    """The kind=value pairs of a subject key as a dict; 'whole' gives an empty dict."""
    return {} if key == "whole" else dict(part.split("=", 1) for part in key.split("&"))


def subject_key(**kinds):
    """The canonical subject key: kind=value pairs sorted by kind and joined with &; 'whole' when there are none."""
    for kind in kinds:
        if kind not in SUBJECT_KINDS:
            raise ValueError(f"unknown subject kind {kind}")
    if not kinds:
        return "whole"
    return "&".join(f"{kind}={value}" for kind, value in sorted(kinds.items()))


check_registry()
