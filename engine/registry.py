# The registry: every measure Kaze may store (name, meaning, unit, lane, fact group, subjects, source feed, blank rule),
# every component (feeds, the login, the collector, the range engine with its recipe's numbers, the read lanes) and the
# fixed word lists. The store writer refuses anything not listed here.
import hashlib
import json
import re
from collections import defaultdict

from engine import mirai_files, session_clock

REGISTRY_VERSION = "1"
# One date for the whole registry: when these rows were defined and the first session Kaze served (2026-10-16);
# available_since_trade_date carries it for every written measure (rebuilt rows may reach further back).
REGISTRY_DATE = "2026-10-16"
DEFINED_AT = f"{REGISTRY_DATE}T00:00:00+00:00"
BUILDER_NAME = "kaze_collector"
BUILDER_VERSION = "1"
MEASURE_NAME_MAX_CHARS = 40
LOGIN_RENEW_WARNING_DAYS = 2
BAR_ARRIVAL_SECONDS = 62
BAR_ARRIVAL_RULE = f"bar_end_plus_{BAR_ARRIVAL_SECONDS}_seconds"

LANES = ("pull", "push", "trade", "label", "id", "health")
# The design's four value columns of every long table; a row fills exactly one of them or carries a blank reason.
VALUE_COLUMNS = ("value_number", "value_bool", "value_text", "value_at")
VALUE_COLUMN_FOR_TYPE = {"number": "value_number", "whole_number": "value_number", "money_usd": "value_number",
                         "yes_no": "value_bool", "word": "value_text", "text": "value_text",
                         "moment": "value_at", "date": "value_at"}
FEED_STATUSES = ("ok", "late", "stale", "frozen_feed", "market_closed", "missing", "stand_in")
ROW_ORIGINS = ("live_frozen", "live_rebuilt", "history_rebuilt")
LIVE_VALUE_ORIGIN = "live"
VALUE_ORIGINS = (LIVE_VALUE_ORIGIN, "after_session_corrected")
SESSION_DATE_FACT, SCHEDULED_EVENT_FACT = "session_date", "scheduled_event"
FACT_KINDS = (SESSION_DATE_FACT, SCHEDULED_EVENT_FACT)
SNAPSHOT_KINDS = ("read", "nightly_check", "nightly_history_fit")
FIT_SNAPSHOT_KIND = "nightly_history_fit"
SNAPSHOT_STATUSES = ("built", "blind", "missed", "thin")
SLOT_STATUSES = ("made", "late", "missed", "not_scheduled")
KNOWN_AT_RULES = ("written_at", BAR_ARRIVAL_RULE, "file_modified_at")
LOGIN_STATES = ("ok", "renew_soon", "expired", "unknown")
BLANK_REASONS = (
    "feed_missing", "feed_stale", "frozen_feed", "market_closed", "market_halted", "before_feed_existed",
    "not_formed_yet", "partial_coverage", "feed_coverage_below_limit", "provider_refused_symbol", "no_activity",
    "not_known_at_read", "known_wrong_same_day", "known_wrong_recipe", "calendar_not_covering_day", "gate_unchecked",
    "bars_missing", "not_priceable", "not_applicable", "none_left_today", "not_yet_resolved", "not_touched",
    "too_old", "too_few_sessions", "receipt_time_unknown")
# Decision word lists (the READS pillar), one home for the SQL CHECK lists and the code.
STORAGE_SCOPES = ("shared", "private")
SHARED_SCOPE = "shared"
RUN_KINDS = ("live", "test_variant", "history_replay", "backtest")
RUN_KIND_FOR_ORIGIN = {"live_frozen": "live", "live_rebuilt": "live", "history_rebuilt": "history_replay"}
DECISION_OK, DECISION_BLIND, DECISION_CLIPPED = "ok", "blind", "clipped"
DECISION_STATUSES = (DECISION_OK, "late", "failed", "refused", DECISION_BLIND, DECISION_CLIPPED, "held_grey")
VALUE_ROLE, RAW_ROLE, APPLIED_ROLE = "value", "raw", "applied"
VALUE_ROLES = (VALUE_ROLE, "as_answered", "after_checks", RAW_ROLE, APPLIED_ROLE, "shown")
# Outcome word lists (the RESULTS pillar): what happened after a read and how a decision graded. Each word here
# has a writer; a word arrives with its writer and its schema step.
MARKET_WINDOW_KIND, FORECAST_GRADE_KIND = "market_window", "forecast_grade"
OUTCOME_KINDS = (MARKET_WINDOW_KIND, FORECAST_GRADE_KIND)
# One word for "the bars are not there": the window's label status, a value's blank reason and its value status.
BARS_MISSING = "bars_missing"
NOT_APPLICABLE = "not_applicable"
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
SUBJECT_KINDS = (
    "adjustment", "band", "bucket", "builder", "cited_order", "claim", "consumer", "exit_rule", "expiry",
    "fake_shift_points", "feed", "gate", "group", "horizon", "impact", "index", "kind", "leg", "level_type", "login",
    "measure", "metric_bin", "minute", "query_role", "quantile", "read_lane", "reading", "request", "rung_points",
    "scope", "segment", "shown_order", "side", "since", "strike_points", "symbol", "table_column", "whole",
    "window_minutes")

# Feeds: name, what it is, Mirai's file (the template mirai_files reads), and how old its newest input may be.
FEEDS = (
    ("spx_bars", "SPX 1-minute bars", mirai_files.SPX_BARS, 180),
    ("watchlist_quotes", "one minute's quotes for the watchlist (SPX, VIX family, funds, stocks, rates)",
     mirai_files.WATCHLIST_QUOTES, 180),
    ("nyse_breadth", "NYSE advancers and decliners carried in the quotes rows' bars field",
     mirai_files.WATCHLIST_QUOTES, 240),
    ("options_scan", "the same-day SPX options scan: walls, VWAP, straddle ruler, dealer gamma",
     mirai_files.OPTIONS_SCANS, 300),
    ("options_tape", "every same-day SPX option trade with its quote at the time", mirai_files.OPTIONS_TAPE, 180),
    ("futures_bars", "/ES 1-minute bars through the night", mirai_files.FUTURES_BARS, 86400),
    ("event_calendar", "the hand-kept calendar of scheduled market events", mirai_files.EVENT_CALENDAR, 100 * 86400),
    ("claude_forecast", "Mirai's 30-minute Claude forecast, the newest final answer", mirai_files.CLAUDE_FORECASTS,
     2400),
    ("news_headlines", "headlines captured from the news feeds", mirai_files.HEADLINES, 1800),
    ("schwab_login", "the Schwab sign-in every Schwab feed depends on (7-day login)", mirai_files.AUTH_WATCH_LOG,
     2 * 86400),
)
FEED_NAMES = tuple(feed[0] for feed in FEEDS)
FEEDS_WITH_LINE_COUNTS = ("spx_bars", "watchlist_quotes", "options_scan", "options_tape", "claude_forecast",
                          "news_headlines")
LOGIN_NAMES = ("schwab",)
CONSUMERS = {"range_engine": ("spx_bars",)}

# The range engine's recipe: the one home for its fixed numbers, read by the fit, the read and its component row.
RANGE_ENGINE_NAME = "range_engine"
RANGE_ENGINE_VERSION = "1"
SPEED_HALF_LIFE_MINUTES = 15
FAST_SPEED_HALF_LIFE_MINUTES = 5
SLOW_SPEED_HALF_LIFE_MINUTES = 30
HORIZON_MINUTES = {"5_minutes": 5, "30_minutes": 30, "60_minutes": 60}
HEADLINE_HORIZON = "30_minutes"
RANGE_INPUT_FEEDS = ("spx_bars", "options_scan")
PATH_WINDOW_MINUTES = (15, 30, 60)
RUNG_POINTS = {"5_minutes": 5, "30_minutes": 10, "60_minutes": 15}
BAND_PORTION = 0.8
# The design's band words: the 80% band of the end price, and the 80% band of the average price over the window.
BAND = "80"
AVERAGE_BAND = "average_80"
BAND_PRICE_MEASURE = {BAND: "end_price", AVERAGE_BAND: "average_price"}
GRADE_START_RULE = "at_read"
GRADE_START_RULE_VERSION = "1"
GRADE_START_RULE_NAMES = (GRADE_START_RULE,)
# The three grade words and what yes means for each: the measure, its band subject (None: the channel's own rung
# subject) and the stored value that says yes. The scoreboard, the phone and the data map all read this.
GRADE_WORD_MEASURE = {"ends_inside": ("was_inside_band", BAND, 1),
                      "average_inside": ("was_inside_band", AVERAGE_BAND, 1),
                      "never_left": ("stayed_inside_rung", None, 1)}
GRADE_WORDS = tuple(GRADE_WORD_MEASURE)
POOLED = "pooled"
FIT_LOOKBACK_SESSIONS = 60
FULL_SESSION_RULE = "every_minute_held"
FIT_FIRST_WINDOW_MINUTE = 30
FIT_WINDOW_STEP_MINUTES = 5
STRETCH_RECIPE = {band: f"speed_half_life_{SPEED_HALF_LIFE_MINUTES}_close_changes|{GRADE_START_RULE}|{price}"
                  f"|every_{FIT_WINDOW_STEP_MINUTES}_minutes_from_{FIT_FIRST_WINDOW_MINUTE}"
                  for band, price in BAND_PRICE_MEASURE.items()}
FED_DECISION_EVENT_KINDS = ("FOMC",)
FED_OWNER_RULE_MULTIPLIER = 2.6
OWNER_RULE_VARIANT = "owner_rule"
BASE_VARIANT = "base"
EVEN_CHANCE = 0.5
TOO_FEW_SESSIONS = "too_few_sessions"
NOT_FORMED_REASON = "not_formed_yet"
NOT_PRICEABLE_REASON = "not_priceable"
RANGE_DECISION_KIND = "range_forecast"
COMPONENT_VERSION = "1"
WALL_TYPES = ("call_wall", "put_wall")
CLAUDE_SIDES = ("up", "flat", "down")
# Phase 4: the width dials, in the order the trust ledger scores them (each earns weight only for what it adds beyond
# the dials before it); every dial starts applied at 1.00 and moves only on a weight the ledger wrote. The Fed dial
# sits outside the cap: the cap holds the product of the other dials, the Fed multiplier stacks on top.
FED_DIAL, RELEASE_DIAL, CLOSING_DIAL = "fed_window", "release_window", "closing_window"
ACCELERATION_DIAL, OPTIONS_VS_TAPE_DIAL = "acceleration", "options_vs_tape"
WIDTH_DIALS = (FED_DIAL, RELEASE_DIAL, CLOSING_DIAL, ACCELERATION_DIAL, OPTIONS_VS_TAPE_DIAL)
ALL_DIALS = "all_dials"
RELEASE_WINDOW_MULTIPLIER = 1.4
RELEASE_FIXED_HORIZON = "5_minutes"
RELEASE_TIER_PREFIX = "data"
FED_EVENT_PREFIXES = ("FOMC", "FED_")
CLOSING_WINDOW_MINUTES = 15
OPTIONS_VS_TAPE_POWER = 0.4
CAP_PORTION = 0.95
NO_EFFECT = 1.0
WIDTH_DIAL_MEANING = {
    FED_DIAL: "wider from a Fed decision to the close, Will's owner rule",
    RELEASE_DIAL: "a fitted ratio, either way, when a timed economic release lands inside the window",
    CLOSING_DIAL: "wider for unclipped windows ending in the last 15 minutes",
    ACCELERATION_DIAL: "fast speed over slow speed, the pace picking up or fading",
    OPTIONS_VS_TAPE_DIAL: "the options-implied swing against the tape swing",
}
WIDTH_DIAL_PARAMETERS = {
    FED_DIAL: {"multiplier": FED_OWNER_RULE_MULTIPLIER, "event_kinds": FED_DECISION_EVENT_KINDS, "capped": False},
    RELEASE_DIAL: {f"multiplier_{RELEASE_FIXED_HORIZON}": RELEASE_WINDOW_MULTIPLIER, "other_horizons": "fitted ratio",
                   "tier_prefix": RELEASE_TIER_PREFIX, "never": FED_EVENT_PREFIXES},
    CLOSING_DIAL: {"last_minutes": CLOSING_WINDOW_MINUTES, "clipped": False},
    ACCELERATION_DIAL: {"input": "speed_fast_over_slow_ratio"},
    OPTIONS_VS_TAPE_DIAL: {"input": "options_over_tape_swing_ratio", "power": OPTIONS_VS_TAPE_POWER},
}
# Phase 4: the push readings (which way), in the order the ledger scores them, each a lean: the chance of up if price
# moves, 0 to 1. Share readings lean by their share; signed readings by even + tilt x tanh(value / scale).
PRICE_TREND, STRAIGHTNESS, BREADTH = "price_trend", "straightness", "breadth"
BIG_TECH_VS_REST, OPTIONS_BUYERS, YIELD_CHANGE, MIRAI_CLAUDE = "big_tech_vs_rest", "options_buyers", "yield_change", \
    "mirai_claude"
PUSH_READINGS = (PRICE_TREND, STRAIGHTNESS, BREADTH, BIG_TECH_VS_REST, OPTIONS_BUYERS, YIELD_CHANGE, MIRAI_CLAUDE)
LEAN_TILT = 0.2
LEAN_SCALE = {PRICE_TREND: 10.0, BIG_TECH_VS_REST: 0.5, YIELD_CHANGE: 5.0}
STRAIGHTNESS_WINDOW_MINUTES = 30
BIG_TECH_SYMBOL, REST_SYMBOL = "QQQ", "RSP"
CLAUDE_LEAN_HORIZON = "30_minutes"
PUSH_READING_MEANING = {
    PRICE_TREND: "SPX move since the prior close (and since the open), in points",
    STRAIGHTNESS: "net move over total path in the last 30 minutes, signed by the net move",
    BREADTH: "NYSE advancers over advancers plus decliners",
    BIG_TECH_VS_REST: "QQQ change less RSP change since the prior close, in percentage points",
    OPTIONS_BUYERS: "share of sided same-day option premium that was bullish in the last 30 minutes",
    YIELD_CHANGE: "10-year yield change since its prior close, in basis points",
    MIRAI_CLAUDE: "Mirai's Claude 30-minute forecast, up over up plus down",
}
PUSH_READING_PARAMETERS = {
    PRICE_TREND: {"tilt": LEAN_TILT, "scale_points": LEAN_SCALE[PRICE_TREND]},
    STRAIGHTNESS: {"tilt": LEAN_TILT, "window_minutes": STRAIGHTNESS_WINDOW_MINUTES},
    BREADTH: {},
    BIG_TECH_VS_REST: {"tilt": LEAN_TILT, "scale_pct_points": LEAN_SCALE[BIG_TECH_VS_REST],
                       "symbols": (BIG_TECH_SYMBOL, REST_SYMBOL)},
    OPTIONS_BUYERS: {},
    YIELD_CHANGE: {"tilt": LEAN_TILT, "scale_basis_points": LEAN_SCALE[YIELD_CHANGE]},
    MIRAI_CLAUDE: {"horizon": CLAUDE_LEAN_HORIZON},
}
DIRECTION_TEXT_UNTIL_EARNED = "coin flip until earned"
PUSH_UNTESTED, PUSH_EARNED = "untested", "earned"
PUSH_STATUSES = (PUSH_UNTESTED, PUSH_EARNED)
RANGE_RECIPE_PARAMETERS = {
    "speed_half_life_minutes": SPEED_HALF_LIFE_MINUTES, "fast_half_life_minutes": FAST_SPEED_HALF_LIFE_MINUTES,
    "slow_half_life_minutes": SLOW_SPEED_HALF_LIFE_MINUTES, "horizon_minutes": HORIZON_MINUTES,
    "rung_points": RUNG_POINTS, "band_portion": BAND_PORTION, "grade_start_rule": GRADE_START_RULE,
    "fit_lookback_sessions": FIT_LOOKBACK_SESSIONS, "full_session_rule": FULL_SESSION_RULE,
    "fit_first_window_minute": FIT_FIRST_WINDOW_MINUTE, "fit_window_step_minutes": FIT_WINDOW_STEP_MINUTES,
    "stretch_recipe": STRETCH_RECIPE, "fed_owner_rule_multiplier": FED_OWNER_RULE_MULTIPLIER,
    "width_dials": WIDTH_DIALS, "uncapped_dial": FED_DIAL, "cap_portion": CAP_PORTION,
    "even_chance": EVEN_CHANCE, "push_readings": PUSH_READINGS, "lean_tilt": LEAN_TILT,
}
# Phase 4: the trust ledger. Two tests fixed in advance, their verdict words, and what a verdict needs.
TRUST_WEIGHT_KIND, SKILL_METRIC_KIND = "trust_weight", "skill_metric"
EVALUATION_KINDS = (TRUST_WEIGHT_KIND, SKILL_METRIC_KIND)
PUSH_READING_KIND, WIDTH_DIAL_KIND = "push_reading", "width_dial"
EVALUATED_KINDS = (PUSH_READING_KIND, WIDTH_DIAL_KIND)
# The dial's and the reading's trust is keyed by the measure that carries the applied weight; its lane and overlap
# group are read off that measure's registry row.
WEIGHTED_MEASURE = {PUSH_READING_KIND: "push_lean_probability", WIDTH_DIAL_KIND: "width_multiplier"}
VERDICT_COLUMN = "metric_text"
VERDICT_PASSED, VERDICT_HELD, VERDICT_HELD_DOWN, VERDICT_FAILED = "passed", "held", "held_down", "failed"
VERDICTS = (VERDICT_PASSED, VERDICT_HELD, VERDICT_HELD_DOWN, VERDICT_FAILED, TOO_FEW_SESSIONS)
EARNED_VERDICTS = (VERDICT_PASSED, VERDICT_HELD_DOWN)
BOOTSTRAP_BY_DAY = "bootstrap_by_day"
INTERVAL_METHODS = (BOOTSTRAP_BY_DAY,)
EARNED_LEANS, UNDIALED_BAND = "earned_leans", "undialed_band"
BASELINES = (EARNED_LEANS, UNDIALED_BAND)
DIAL_ON, DIAL_OFF, FAKE_BASELINE = "dial_on", "dial_off", "fake_baseline"
LEAN_TEST = ("lean_brier_test", COMPONENT_VERSION)
DIAL_TEST = ("dial_band_test", "2")
TRUST_MINIMUM_SESSIONS = 20
FED_TEST_MINIMUM = {"windows": 20, "sessions": 5}
HOLD_RUNS = 3
FAKE_PERMUTATIONS = 200
FAKE_PERCENTILE = 95
FAKE_SEED = 1
LEAN_MOVED_RUNG_SHARE = 0.5
INTERVAL_SCORE_ALPHA = 1 - BAND_PORTION
TRUST_TEST_SHARED = {
    "sample": "live first-built base decisions whose grade counts", "replay_counts": False,
    "minimum_sessions": TRUST_MINIMUM_SESSIONS, "interval": BOOTSTRAP_BY_DAY, "interval_percent": 95,
    "hold_runs": HOLD_RUNS, "hold_down_runs": HOLD_RUNS, "hold_down_unless": "the interval sits below 0",
    "fake_permutations": FAKE_PERMUTATIONS, "fake_percentile": FAKE_PERCENTILE, "fake_seed": FAKE_SEED,
}
LEAN_TEST_PARAMETERS = {
    **TRUST_TEST_SHARED, "order": PUSH_READINGS, "moved_rung_share": LEAN_MOVED_RUNG_SHARE,
    "baseline": EARNED_LEANS, "weight": "interval low x the room left under 1",
    "fake": "each day's leans handed to another day's windows",
}
DIAL_TEST_PARAMETERS = {
    **TRUST_TEST_SHARED, "order": WIDTH_DIALS, "baseline": UNDIALED_BAND, "alpha": INTERVAL_SCORE_ALPHA,
    "weight": 1, f"{FED_DIAL}_minimum": FED_TEST_MINIMUM,
    "fake": "each day's touched windows and raws handed whole to a same-size set of another day's windows, matched "
            "on the clipped flag",
}
# Phase 3: the rules every outcome and grade is stored under; a changed rule is a new version, never an edit.
WINDOW_RULE = ("window_from_bars", "1")
GRADE_RULE = ("band_grade", "1")
GRADE_START_RULE_PARAMETERS = {"start_price": "the read's last close", "start_bar": "the bar that close came from",
                               "window_from": "the end of the read's minute", "offset_minutes": 0}
WINDOW_RULE_PARAMETERS = {
    "bars": "collect_market_prices by minute, live rows preferred", "end_price": "close of the window's last bar",
    "average_price": "mean of the window's closes", "high_low": "the window's bar highs and lows",
    "rung_points": sorted(set(RUNG_POINTS.values())), "end_bar_missing": BARS_MISSING,
    "settled_after": "the session close",
}
GRADE_RULE_PARAMETERS = {
    "ends_inside": "|end move| <= end band half-width", "average_inside": "|average move| <= average band half-width",
    "never_left": "farthest excursion < the channel's rung", "band_miss": "points past the edge, signed, 0 inside",
    "band_used": "excursion on each side over the end band half-width, floored at 0, above 1 when it broke out",
    "grade_start": f"{GRADE_START_RULE}@{GRADE_START_RULE_VERSION}",
}


def measure_row(name, meaning, unit, value_type, lane=None, group=None, subjects="whole", feed=None,
                blank="never blank in session", table="collect_observations", pillar="collect", words=None, low=None,
                high=None, overlap=None, leans="no_direction", neutral=None, history=False, carry=None, twin=None,
                available_from=None, written_since=REGISTRY_DATE):
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
        "available_since_trade_date": written_since, "available_from_minutes_after_open": available_from,
        "defined_at": DEFINED_AT,
    }


PUSH_LANE_DEFAULTS = dict(lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.0)
DECISION_VALUE = dict(table="reads_decision_values", pillar="reads")
OUTCOME_VALUE = dict(table="results_outcome_values", pillar="results")
EVALUATION_VALUE = dict(table="results_evaluations", pillar="results")
NO_END_BAR = f"{BARS_MISSING} when the window's last bar never reached the store"
NO_START = f"{NOT_APPLICABLE} when the read had no price; " + NO_END_BAR
NO_WINDOW_MOVE = f"{NOT_APPLICABLE} when the read had no price to measure from; " + NO_END_BAR
NO_GRADE = "the band's own reason when the decision was blind; " + NO_WINDOW_MOVE
# A measure born on the snapshot (or the fit) and copied onto every decision that used it.
BOTH_TABLES = "collect_observations|reads_decision_values"
FIT_BLANK_MEANING = f"{TOO_FEW_SESSIONS} when no fit was known at the cut"
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
                "pull", "session_clock", low=0, high=390),
    measure_row("minutes_until_session_close", "minutes from the cut to the close", "minutes", "whole_number",
                "pull", "session_clock", low=0, high=390),
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
    measure_row("range_so_far_points", "session high minus session low", "points", "number", "pull", "spx_move_size",
                feed="spx_bars", blank=BARS_MISSING_MEANING, low=0, high=2000),
    measure_row("move_points", "SPX close now minus its close at the since point (signed)", "points", "number",
                group="spx_move_sign", subjects="since", feed="spx_bars",
                blank="not_formed_yet before the since point exists today", low=-2000, high=2000, **PUSH_LANE_DEFAULTS),
    # options
    measure_row("same_day_straddle_mid_points",
                "at-the-money same-day call plus put at mid, from the scan ruler (scan value / 0.85)", "points",
                "number", "pull", "options_premium_size", feed="options_scan",
                blank="not_priceable when the scan ruler is not ok", low=0, high=500),
    measure_row("opening_straddle_mid_points", "the same straddle as it stood at the open", "points", "number", "pull",
                "options_premium_size", feed="options_scan", blank="not_priceable when the scan ruler is not ok",
                low=0, high=500),
    measure_row("atm_option_mid_points", "newest tape quote mid of the at-the-money same-day option on this side",
                "points", "number", "id", "atm_option_quotes", subjects="side|strike_points", feed="options_tape",
                blank="no_activity when the strike has not traded today", low=0, high=500),
    measure_row("same_day_atm_iv_calendar_year_pct", "at-the-money implied volatility of the same-day options", "pct",
                "number", "pull", "options_implied_vol", feed="options_scan", blank=SCAN_MISSING, low=0, high=500),
    measure_row("level_price_points", "price of a dealer level from the scan", "points", "number", "id",
                "level_prices", subjects="level_type", feed="options_scan", blank=SCAN_MISSING, **POINTS_RANGE),
    measure_row("level_gamma_share_portion", "share of total gamma sitting at the level", "portion", "number", "pull",
                "dealer_gamma_shape", subjects="level_type", feed="options_scan", blank=SCAN_MISSING, low=0, high=1),
    measure_row("dealer_net_gamma_usd", "dealers' net gamma across the same-day chain", "usd", "number", "pull",
                "dealer_gamma_size", feed="options_scan", blank=SCAN_MISSING),
    measure_row("dealer_gamma_sign", "the scan's own word for the dealer gamma regime", "word", "word", "pull",
                "dealer_gamma_shape", feed="options_scan", blank=SCAN_MISSING),
    # VIX family
    measure_row("vix_calendar_year_pct", "last quote of a VIX-family index", "pct", "number", "pull",
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
                low=0, high=1, lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.5),
    # leaders, rates, dollar, oil
    measure_row("fund_change_from_prior_close_pct", "a fund's last quote against its prior close", "pct", "number",
                group="fund_change_sign", subjects="symbol", feed="watchlist_quotes", blank=SYMBOL_MISSING,
                low=-50, high=50, **PUSH_LANE_DEFAULTS),
    measure_row("sector_funds_up_count", "how many of the 11 sector funds are above their prior close", "count",
                "whole_number", group="sector_breadth", feed="watchlist_quotes",
                blank="partial_coverage until all 11 quote", low=0, high=11, lane="push",
                overlap="direction_all_sources", leans="leans_up", neutral=5.5),
    measure_row("largest_stocks_up_count", "how many of the 9 largest S&P stocks are above their prior close", "count",
                "whole_number", group="largest_stocks_breadth", feed="watchlist_quotes",
                blank="partial_coverage until all 9 quote", low=0, high=9, lane="push",
                overlap="direction_all_sources", leans="leans_up", neutral=4.5),
    measure_row("ten_year_yield_annual_pct", "10-year Treasury yield", "pct", "number", "id", "rates_level",
                feed="watchlist_quotes", blank=SYMBOL_MISSING, low=0, high=25),
    measure_row("ten_year_yield_change_basis_points", "10-year yield change from its prior close", "basis_points",
                "number", group="rates_change", feed="watchlist_quotes", blank=SYMBOL_MISSING, low=-200, high=200,
                **PUSH_LANE_DEFAULTS),
    # overnight
    measure_row("overnight_high_points", "highest /ES 1-minute bar from 18:00 to 09:30", "points", "number", "id",
                "overnight_levels", subjects="symbol", feed="futures_bars",
                blank="feed_missing when the night file is absent", **POINTS_RANGE),
    measure_row("overnight_low_points", "lowest /ES 1-minute bar from 18:00 to 09:30", "points", "number", "id",
                "overnight_levels", subjects="symbol", feed="futures_bars",
                blank="feed_missing when the night file is absent", **POINTS_RANGE),
    # events (read from the store's calendar rows as known at the cut)
    measure_row("minutes_until_next_event", "minutes from the cut to the next scheduled event today", "minutes",
                "number", "pull", "event_clock", subjects="impact", feed="event_calendar",
                blank="none_left_today after the day's last event; calendar_not_covering_day outside its span",
                low=0, high=600),
    measure_row("events_left_today_count", "scheduled events still ahead today", "count", "whole_number", "pull",
                "event_clock", feed="event_calendar", blank="calendar_not_covering_day outside the calendar's span",
                low=0, high=50),
    measure_row("next_event_kind", "the calendar's kind word for the next event today", "word", "word", "id",
                "event_kind", feed="event_calendar", blank="none_left_today after the day's last event"),
    measure_row("does_calendar_cover_day", "the event calendar's span includes this session", "yes_no", "yes_no",
                "health", "calendar_health", feed="event_calendar"),
    # options tape
    measure_row("options_trades_last_30_minutes_count", "same-day option trades in the 30 minutes to the cut", "count",
                "whole_number", "pull", "options_activity", feed="options_tape", blank=TAPE_BLANK, low=0,
                twin=TAPE_TWIN),
    measure_row("options_call_premium_last_30_minutes_usd",
                "dollars of call premium traded in the 30 minutes to the cut",
                "usd", "money_usd", "pull", "options_premium_flow", feed="options_tape", blank=TAPE_BLANK, low=0,
                twin=TAPE_TWIN),
    measure_row("options_put_premium_last_30_minutes_usd", "dollars of put premium traded in the 30 minutes to the cut",
                "usd", "money_usd", "pull", "options_premium_flow", feed="options_tape", blank=TAPE_BLANK, low=0,
                twin=TAPE_TWIN),
    measure_row("bullish_portion_of_sided_premium",
                "share of sided single-leg premium that was bullish (calls bought, puts sold)", "portion", "number",
                group="options_flow_side", feed="options_tape",
                blank="no_activity when no trade had a side; " + TAPE_BLANK,
                low=0, high=1, lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.5,
                twin=TAPE_TWIN),
    measure_row("tape_gap_markers_last_30_minutes_count", "collector gap markers inside the 30-minute window", "count",
                "whole_number", "health", "tape_health", subjects="feed", feed="options_tape", blank=TAPE_BLANK, low=0),
    measure_row("feed_coverage_last_30_minutes_portion",
                "share of the 30-minute window the feed covered: 1 minus gap markers over 30 (one per lost minute)",
                "portion", "number", "health", "feed_coverage", subjects="feed", blank=TAPE_BLANK, low=0, high=1),
    # headlines
    measure_row("headlines_last_60_minutes_count", "headlines captured in the 60 minutes to the cut", "count",
                "whole_number", "pull", "news_volume", subjects="kind", feed="news_headlines",
                blank="feed_missing when today's file is absent; feed_stale when the newest capture is too old",
                low=0, twin="feed_coverage_last_60_minutes_portion"),
    measure_row("feed_coverage_last_60_minutes_portion", "share of the 60-minute window the feed covered", "portion",
                "number", "health", "feed_coverage", subjects="feed",
                blank="not_known_at_read: the headline poller leaves no poll stamps", low=0, high=1),
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
    # Phase 2: today's speed, swings, path and the options check, written on the read's snapshot by the range engine
    measure_row("today_typical_1_minute_move_points",
                "recency-weighted mean absolute 1-minute close change, half-life 15 minutes", "points", "number",
                "pull", "spx_speed", feed="spx_bars", blank=NO_SPEED, available_from=2, low=0, table=BOTH_TABLES),
    measure_row("speed_fast_5_minute_half_life_points", "the same speed with a 5-minute half-life", "points", "number",
                "pull", "spx_speed", feed="spx_bars", blank=NO_SPEED, available_from=2, low=0),
    measure_row("speed_slow_30_minute_half_life_points", "the same speed with a 30-minute half-life", "points",
                "number", "pull", "spx_speed", feed="spx_bars", blank=NO_SPEED, available_from=2, low=0),
    measure_row("speed_fast_over_slow_ratio", "fast speed over slow speed; above 1 means the pace is picking up",
                "ratio", "number", "pull", "spx_fast_over_slow", feed="spx_bars",
                blank=NO_SPEED + "; no_activity when the slow speed is 0", available_from=2, low=0),
    measure_row("typical_swing_points", "speed times the square root of the horizon's minutes", "points", "number",
                "pull", "spx_speed", subjects="horizon", feed="spx_bars", blank=NO_SPEED, available_from=2, low=0,
                table=BOTH_TABLES),
    measure_row("options_implied_swing_points", "straddle left scaled by the square root of window over minutes left",
                "points", "number", "pull", "options_premium_size", subjects="horizon", feed="options_scan",
                blank="not_priceable when the read has no straddle", low=0),
    measure_row("options_over_tape_swing_ratio", "options-implied swing over the tape swing over the same minutes",
                "ratio", "number", "pull", "options_vs_tape_check", subjects="horizon", feed="options_scan",
                blank="not_priceable when either swing is missing", low=0),
    measure_row("path_up_points", "sum of the window's rising 1-minute changes", "points", "number", group="path_legs",
                subjects="window_minutes", feed="spx_bars", blank="not_formed_yet until the window has formed", low=0,
                lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.0),
    measure_row("path_down_points", "sum of the window's falling 1-minute changes, as a positive number", "points",
                "number", group="path_legs", subjects="window_minutes", feed="spx_bars",
                blank="not_formed_yet until the window has formed", low=0, lane="push",
                overlap="direction_all_sources", leans="leans_down", neutral=0.0),
    measure_row("trend_straightness_ratio",
                "net move over total path in the window; 1 is one way, 0 is pure back-and-forth", "ratio", "number",
                group="path_straightness", subjects="window_minutes", feed="spx_bars",
                blank="not_formed_yet until the window has formed; no_activity when the path is 0", low=0, high=1,
                lane="push", overlap="direction_all_sources", leans="no_direction", neutral=0.0),
    # Phase 2: history fits, written at nightly_history_fit cuts and copied onto the decisions that used them
    measure_row("band_multiplier", "swings that cover the band's share of past window endings on the same recipe",
                "multiplier", "number", "pull", "band_stretch", subjects="band|horizon", history=True, low=0,
                blank=FIT_BLANK_MEANING, table=BOTH_TABLES),
    measure_row("stretch_recipe_name", "the speed recipe and grading window the stretch was fitted on", "word", "word",
                "id", "band_stretch", subjects="band|horizon", table=BOTH_TABLES),
    measure_row("fit_sessions_count", "sessions the history fit used", "count", "whole_number", "health",
                "fit_sample", subjects="horizon", low=0, table=BOTH_TABLES),
    measure_row("fit_windows_count", "windows the history fit used", "count", "whole_number", "health", "fit_sample",
                subjects="horizon", low=0, table=BOTH_TABLES),
    measure_row("average_price_shrink_ratio", "average-price band half-width over end-price band half-width", "ratio",
                "number", "pull", "band_stretch", subjects="horizon", history=True, low=0, high=1,
                blank=FIT_BLANK_MEANING),
    # Phase 2: range forecast values (reads_decision_values)
    measure_row("band_centre_points", "price the band is centred on: the last close at the read", "points", "number",
                "id", "band_centre", subjects="horizon", blank="feed_missing when no bar has landed", **POINTS_RANGE,
                **DECISION_VALUE),
    measure_row("band_half_width_points", "half-width of the band, dials applied", "points", "number", "pull",
                "band_size", subjects="band|horizon", blank="the speed's or the fit's reason", low=0,
                **DECISION_VALUE),
    measure_row("band_low_points", "the band's lower edge", "points", "number", "id", "band_edges",
                subjects="band|horizon", blank="the speed's or the fit's reason", **POINTS_RANGE, **DECISION_VALUE),
    measure_row("band_high_points", "the band's upper edge", "points", "number", "id", "band_edges",
                subjects="band|horizon", blank="the speed's or the fit's reason", **POINTS_RANGE, **DECISION_VALUE),
    measure_row("chance_stays_inside_rung_probability", "chance price never leaves the rung over the horizon",
                "probability", "number", "pull", "rung_odds", subjects="horizon|rung_points", low=0, high=1,
                blank="the speed's or the fit's reason", **DECISION_VALUE),
    measure_row("width_multiplier", "a dial on the band's width, raw and applied", "multiplier", "number", "pull",
                "band_size", subjects="adjustment", overlap="width_all_adjustments", low=0,
                blank="the speed's reason when the dial reads the speed", **DECISION_VALUE),
    measure_row("fit_snapshot_id", "the nightly_history_fit cut whose multipliers the decision used", "id", "text",
                "id", "band_stretch", blank=FIT_BLANK_MEANING, **DECISION_VALUE),
    measure_row("chance_up_if_moves_probability", "chance the move is up, given it moves", "probability", "number",
                group="direction_call", low=0, high=1, lane="push", overlap="direction_all_sources", leans="leans_up",
                neutral=0.5, **DECISION_VALUE),
    # Phase 3: what happened in each decision window (market_window outcomes) and the grade start every grade carries
    measure_row("grade_start_price_points", "price the grade measured from: the read's last close", "points", "number",
                "id", "grade_start", blank=NO_START, **POINTS_RANGE, **OUTCOME_VALUE),
    measure_row("grade_start_bar_at", "start of the 1-minute bar the grade measured from", "at", "moment", "id",
                "grade_start", blank=NO_START, **OUTCOME_VALUE),
    measure_row("grade_start_rule_name", "which grading rule set the start", "word", "word", "id", "grade_start",
                words=GRADE_START_RULE_NAMES, **OUTCOME_VALUE),
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
    # Phase 3: how each range decision graded (forecast_grade outcomes)
    measure_row("was_inside_band", "the band's price (end or average) landed inside the band", "yes_no", "yes_no",
                "label", "band_grade", subjects="band", blank=NO_GRADE, **OUTCOME_VALUE),
    measure_row("band_miss_points", "points the band's price landed past the edge: above the top positive, below the "
                "bottom negative, 0 inside", "points", "number", "label", "band_grade", subjects="band",
                blank=NO_GRADE, **OUTCOME_VALUE),
    measure_row("band_used_ratio", "farthest excursion on this side over the end band's half-width; above 1 means "
                "price broke out that side", "ratio", "number", "label", "band_grade", subjects="band|side",
                blank=NO_GRADE, low=0, **OUTCOME_VALUE),
    # Phase 4: the push readings on the read's snapshot (and each lean again on every decision, with the trust applied)
    measure_row("push_lean_probability", "a reading's lean: its chance of up if price moves, were it trusted; "
                "trust 0 until earned",
                "probability", "number", group="push_lean", subjects="reading", low=0, high=1,
                blank="the input's reason: feed_missing, feed_stale, provider_refused_symbol, not_formed_yet, "
                      "no_activity, too_old", table=BOTH_TABLES, lane="push", overlap="direction_all_sources",
                leans="leans_up", neutral=0.5),
    measure_row("push_reading_text", "the reading's value in words, what the phone shows beside its lean", "text",
                "text", "id", "push_description", subjects="reading", blank="the input's reason"),
    measure_row("level_distance_from_price_points", "a dealer wall's price less the read price (above is positive)",
                "points", "number", "pull", "level_distances", subjects="level_type", feed="options_scan",
                blank=SCAN_MISSING + "; feed_missing when no bar has landed", low=-2000, high=2000),
    # Phase 4: the time-of-day ratios and the width cap, measured on the fit's history at read time
    measure_row("closing_window_multiplier", "the 80% mark of |end move| in swings over unclipped windows ending in "
                "the last 15 minutes, over the all-day mark", "multiplier", "number", "pull", "band_stretch",
                subjects="horizon", history=True, low=0, blank=FIT_BLANK_MEANING, **DECISION_VALUE),
    measure_row("release_window_multiplier", "the 80% mark of |end move| in swings over windows a timed release "
                "lands in, over the all-day mark", "multiplier", "number", "pull", "band_stretch", subjects="horizon",
                history=True, low=0, blank=f"{TOO_FEW_SESSIONS} until the history holds such a window",
                **DECISION_VALUE),
    measure_row("width_cap_multiplier", "the most the dials other than the Fed may widen the band: the history's 95% "
                "mark of |end move| in swings over its 80% mark", "multiplier", "number", "pull", "band_stretch",
                subjects="horizon", history=True, low=1, blank=FIT_BLANK_MEANING, **DECISION_VALUE),
    # Phase 4: the trust ledger's metrics (results_evaluations)
    measure_row("trust_weight_portion", "the weight a reading or dial has earned for a channel; 0 is not earned",
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


WORD_LISTS = (LANES, FEED_STATUSES, ROW_ORIGINS, VALUE_ORIGINS, FACT_KINDS, SNAPSHOT_KINDS, SNAPSHOT_STATUSES,
              SLOT_STATUSES, KNOWN_AT_RULES, LOGIN_STATES, BLANK_REASONS, STORAGE_SCOPES, RUN_KINDS, DECISION_STATUSES,
              VALUE_ROLES, OUTCOME_KINDS, LABEL_STATUSES, VALUE_STATUSES, BAND_SIDES, GRADE_WORDS, SUBJECT_KINDS,
              GRADE_START_RULE_NAMES, WIDTH_DIALS, PUSH_READINGS, EVALUATION_KINDS, EVALUATED_KINDS, VERDICTS,
              INTERVAL_METHODS, BASELINES, PUSH_STATUSES, WALL_TYPES, CLAUDE_SIDES)
WORD_SHAPE = re.compile(r"^[a-z0-9_$/]+$")


def check_registry():
    """Refuse a registry that breaks the rules: a word that is not one lowercase word (a constant rebound to prose),
    duplicates, unknown lanes, subjects or feeds, one fact in two lanes."""
    for words in WORD_LISTS:
        if len(set(words)) != len(words) or not all(WORD_SHAPE.match(word) for word in words):
            raise ValueError(f"registry word list {words} must be distinct lowercase single words")
    if len(MEASURES_BY_NAME) != len(MEASURES):
        raise ValueError("duplicate measure name in the registry")
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
        if row["signal_lane"] in ("pull", "push"):
            lanes_by_group[row["information_group_name"]].add(row["signal_lane"])
    for group, lanes in lanes_by_group.items():
        if len(lanes) > 1:
            raise ValueError(f"fact {group} reaches both pull and push")


def component_row(kind, name, meaning, parameters, version=COMPONENT_VERSION):
    """One registry_components row with the columns filled so far."""
    parameters_text = json.dumps(parameters, sort_keys=True)
    return {
        "component_kind": kind, "component_name": name, "component_version": version, "revision_number": 1,
        "plain_meaning_text": meaning, "signal_lane": "none", "parameters_json_text": parameters_text,
        "parameters_sha256": hashlib.sha256(parameters_text.encode()).hexdigest(),
        "starting_trust_weight_portion": 0.0, "evidence_status": "not_weight_bearing", "approval_status": "not_needed",
        "storage_scope": "shared", "released_at": DEFINED_AT,
    }


def component_rows():
    """Every feed, the login, the builder, the range engine and the read lanes, all at version 1."""
    rows = [component_row("feed", name, meaning, {"mirai_file": mirai_file, "stale_limit_seconds": stale_limit})
            for name, meaning, mirai_file, stale_limit in FEEDS]
    rows.extend(component_row("login", name, "the 7-day Schwab sign-in",
                              {"renew_warning_days": LOGIN_RENEW_WARNING_DAYS})
                for name in LOGIN_NAMES)
    rows.append(component_row("builder", BUILDER_NAME,
                              "reads Mirai's files as they stood at a cut and writes one snapshot",
                              {"registry_version": REGISTRY_VERSION}, version=BUILDER_VERSION))
    rows.append(component_row("forecaster", RANGE_ENGINE_NAME,
                              "today's speed times the square root of the horizon, stretched by the history fit",
                              RANGE_RECIPE_PARAMETERS, version=RANGE_ENGINE_VERSION))
    rows.extend(component_row("read_lane", name, f"reads every {cadence} minutes", {"cadence_minutes": cadence})
                for name, cadence in session_clock.READ_LANES.items())
    rows.append(component_row("grade_start_rule", GRADE_START_RULE, "the grade starts at the read's price and minute",
                              GRADE_START_RULE_PARAMETERS, version=GRADE_START_RULE_VERSION))
    rows.append(component_row("outcome_rule", WINDOW_RULE[0], "what happened in a decision window, from the store's "
                              "bars", WINDOW_RULE_PARAMETERS, version=WINDOW_RULE[1]))
    rows.append(component_row("grade_rule", GRADE_RULE[0], "a range decision graded against its bands and rung",
                              GRADE_RULE_PARAMETERS, version=GRADE_RULE[1]))
    rows.extend(component_row(PUSH_READING_KIND, name, PUSH_READING_MEANING[name], PUSH_READING_PARAMETERS[name])
                for name in PUSH_READINGS)
    rows.extend(component_row(WIDTH_DIAL_KIND, name, WIDTH_DIAL_MEANING[name], WIDTH_DIAL_PARAMETERS[name])
                for name in WIDTH_DIALS)
    rows.append(component_row("trust_test", LEAN_TEST[0], "a lean's Brier skill, added to the earned leans, on live "
                              "moved windows", LEAN_TEST_PARAMETERS, version=LEAN_TEST[1]))
    rows.append(component_row("trust_test", DIAL_TEST[0], "a dial's band interval score gain on live windows, on top "
                              "of the dials earned before it", DIAL_TEST_PARAMETERS, version=DIAL_TEST[1]))
    return rows


def feed_stale_limit_seconds(feed_name):
    """How old a feed's newest input may be at the cut before its status is stale."""
    return next(feed[3] for feed in FEEDS if feed[0] == feed_name)


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
