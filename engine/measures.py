# The registry: every measure Kaze may store (name, meaning, unit, lane, fact group, subjects, source, blank rule)
# and every versioned component (feeds, the login, the builder, the read lanes). The store writer refuses anything not listed here.
import hashlib
import json
from collections import defaultdict

REGISTRY_VERSION = "1"
BUILDER_NAME = "kaze_collector"
BUILDER_VERSION = "1"
DEFINED_AT = "2026-10-17T00:00:00+00:00"
SUBJECT_KEY_FORMAT = "kind=value pairs sorted by kind and joined with &; whole when there is no subject"

LANES = ("pull", "push", "trade", "label", "id", "health")
VALUE_COLUMNS = {"number": "value_number", "whole_number": "value_number", "money_usd": "value_number",
                 "yes_no": "value_bool", "word": "value_text", "text": "value_text",
                 "moment": "value_at", "date": "value_at"}
FEED_STATUSES = ("ok", "stale", "frozen_feed", "market_closed", "missing", "stand_in")
BLANK_REASONS = (
    "feed_missing", "feed_stale", "frozen_feed", "market_closed", "market_halted", "before_feed_existed",
    "not_formed_yet", "partial_coverage", "feed_coverage_below_limit", "provider_refused_symbol", "no_activity",
    "not_known_at_read", "known_wrong_same_day", "known_wrong_recipe", "calendar_not_covering_day", "gate_unchecked",
    "bars_missing", "not_priceable", "not_applicable", "none_left_today", "not_yet_resolved", "not_touched",
    "too_old", "too_few_sessions", "receipt_time_unknown")
SUBJECT_KINDS = (
    "adjustment", "band", "bucket", "builder", "cited_order", "claim", "consumer", "exit_rule", "expiry",
    "fake_shift_points", "feed", "gate", "group", "horizon", "impact", "index", "kind", "leg", "level_type", "login",
    "measure", "metric_bin", "minute", "query_role", "quantile", "read_lane", "reading", "request", "rung_points",
    "scope", "segment", "shown_order", "side", "since", "strike_points", "symbol", "table_column", "whole",
    "window_minutes")

# Feeds: name, what it is, Mirai's file, how old its newest input may be before the feed counts as stale, receipt stamp.
FEEDS = (
    ("spx_bars", "SPX 1-minute bars", "market/spx_price_bars_by_minute/{day}.jsonl", 180, False),
    ("watchlist_quotes", "one minute's quotes for the watchlist (SPX, VIX family, funds, stocks, rates)",
     "market/watchlist_quotes_by_minute/{day}.jsonl", 180, True),
    ("nyse_breadth", "NYSE advancers and decliners carried in the quotes rows' bars field",
     "market/watchlist_quotes_by_minute/{day}.jsonl", 240, True),
    ("options_scan", "the same-day SPX options scan: walls, VWAP, straddle ruler, dealer gamma",
     "spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl", 300, True),
    ("options_tape", "every same-day SPX option trade with its quote at the time",
     "market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl", 180, False),
    ("futures_bars", "/ES 1-minute bars through the night", "market/futures_price_bars_by_minute/{day}.jsonl", 86400, True),
    ("event_calendar", "the hand-kept calendar of scheduled market events",
     "settings/market_event_calendar_by_event.json", 100 * 86400, True),
    ("claude_forecast", "Mirai's 30-minute Claude forecast, the newest final answer",
     "spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl", 2400, True),
    ("news_headlines", "headlines captured from the news feeds", "market/news_headlines_by_article/{day}.jsonl", 1800, True),
    ("schwab_login", "the Schwab sign-in that every Schwab feed depends on (7-day login)",
     "operations/logs/auth_watch_output_log.txt", 2 * 86400, True),
)
FEED_NAMES = tuple(feed[0] for feed in FEEDS)
LOGIN_NAMES = ("schwab",)
READ_LANES = (("headline_30", 30, "the 30-minute reads at :05 and :35"),
              ("range_every_5_minutes", 5, "the 5-minute reads for the 5-minute channel and outcomes"))
CONSUMERS = {"range_engine": ("spx_bars", "watchlist_quotes")}


def measure(name, meaning, unit, value_type, lane=None, group=None, subjects="whole", feed=None, blank="never blank in session",
            table="collect_observations", pillar="collect", words=None, low=None, high=None, overlap=None,
            leans="no_direction", neutral=None, history=False, divisor=None, carry=None, twin=None,
            available_from=None, written_since="2026-10-16"):
    """One registry row; the arguments are the design's R1 columns in plain words."""
    return {
        "measure_name": name, "measure_version": 1, "plain_meaning_text": meaning, "pillar_name": pillar,
        "stored_in_table_name": table, "subject_kinds_text": "|".join(sorted(subjects.split("|"))),
        "subject_key_format": SUBJECT_KEY_FORMAT, "value_type": value_type, "value_column": VALUE_COLUMNS[value_type],
        "unit": unit, "allowed_words_text": "|".join(words) if words else None,
        "allowed_minimum_number": low, "allowed_maximum_number": high, "signal_lane": lane,
        "information_group_name": group, "overlap_group_name": overlap, "positive_value_means": leans,
        "neutral_value_number": neutral, "is_history_derived": int(history), "history_divisor_measure_name": divisor,
        "raw_source_fields_text": None, "source_feed_name": feed, "recipe_component_name": BUILDER_NAME,
        "blank_means_text": blank, "max_carry_forward_seconds": carry, "coverage_twin_measure_name": twin,
        "wide_column_template": None, "available_since_trade_date": written_since,
        "available_from_minutes_after_open": available_from, "history_complete_since_date": None,
        "is_cut_input": 0, "cut_reason_text": None, "is_station_only": 0, "legacy_columns_text": None,
        "defined_at": DEFINED_AT, "retired_at": None,
    }


PUSH = dict(lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.0)
LATER = dict(written_since=None)

MEASURES = [
    # clock
    measure("minutes_since_session_open", "whole session minutes finished by the cut", "minutes", "whole_number", "pull",
            "session_clock", low=0, high=390),
    measure("minutes_until_session_close", "minutes from the cut to the close", "minutes", "whole_number", "pull",
            "session_clock", low=0, high=390),
    # price
    measure("spx_price_points", "SPX close of the last finished 1-minute bar", "points", "number", "id", "spx_level",
            feed="spx_bars", blank="feed_missing when no bar has landed today", low=1000, high=20000),
    measure("prior_close_points", "SPX official close of the prior session, from the quotes feed", "points", "number", "id",
            "spx_level", feed="watchlist_quotes", blank="feed_missing when no quotes row has landed today", low=1000, high=20000),
    measure("today_open_points", "the first 1-minute bar's open (may be a stale first print)", "points", "number", "id",
            "spx_level", feed="spx_bars", blank="feed_missing when no bar has landed today", low=1000, high=20000),
    measure("session_high_points", "highest bar high so far, the first bar's stale print ignored", "points", "number", "id",
            "spx_level", feed="spx_bars", blank="feed_missing when no bar has landed today", low=1000, high=20000),
    measure("session_low_points", "lowest bar low so far, the first bar's stale print ignored", "points", "number", "id",
            "spx_level", feed="spx_bars", blank="feed_missing when no bar has landed today", low=1000, high=20000),
    measure("vwap_points", "volume-weighted average price from the options scan", "points", "number", "id", "spx_level",
            feed="options_scan", blank="feed_missing when no native scan has landed today", low=1000, high=20000),
    measure("range_so_far_points", "session high minus session low", "points", "number", "pull", "spx_move_size",
            feed="spx_bars", blank="feed_missing when no bar has landed today", low=0, high=2000),
    measure("move_points", "SPX close now minus its close at the since point (signed)", "points", "number", group="spx_move_sign",
            subjects="since", feed="spx_bars", blank="not_formed_yet before the since point exists today", low=-2000, high=2000, **PUSH),
    # options
    measure("same_day_straddle_mid_points", "at-the-money same-day call plus put at mid, from the scan ruler (scan value / 0.85)",
            "points", "number", "pull", "options_premium_size", feed="options_scan",
            blank="not_priceable when the scan ruler is not ok", low=0, high=500),
    measure("opening_straddle_mid_points", "the same straddle as it stood at the open", "points", "number", "pull",
            "options_premium_size", feed="options_scan", blank="not_priceable when the scan ruler is not ok", low=0, high=500),
    measure("atm_option_mid_points", "newest tape quote mid of the at-the-money same-day option on this side", "points",
            "number", "id", "atm_option_quotes", subjects="side|strike_points", feed="options_tape",
            blank="no_activity when the strike has not traded today", low=0, high=500),
    measure("same_day_atm_iv_calendar_year_pct", "at-the-money implied volatility of the same-day options", "pct", "number",
            "pull", "options_implied_vol", feed="options_scan", blank="feed_missing when no native scan has landed", low=0, high=500),
    measure("level_price_points", "price of a dealer level from the scan", "points", "number", "id", "level_prices",
            subjects="level_type", feed="options_scan", blank="feed_missing when no native scan has landed", low=1000, high=20000),
    measure("level_gamma_share_portion", "share of total gamma sitting at the level", "portion", "number", "pull",
            "dealer_gamma_shape", subjects="level_type", feed="options_scan", blank="feed_missing when no native scan has landed",
            low=0, high=1),
    measure("dealer_net_gamma_usd", "dealers' net gamma across the same-day chain", "usd", "number", "pull", "dealer_gamma_size",
            feed="options_scan", blank="feed_missing when no native scan has landed"),
    measure("dealer_gamma_sign", "the scan's word for the dealer gamma regime", "word", "word", "pull", "dealer_gamma_shape",
            feed="options_scan", blank="feed_missing when no native scan has landed"),
    # VIX family
    measure("vix_calendar_year_pct", "last quote of a VIX-family index", "pct", "number", "pull", "implied_vol_level",
            subjects="symbol", feed="watchlist_quotes", blank="provider_refused_symbol when the quote is missing", low=0, high=500),
    measure("vix_prior_close_calendar_year_pct", "prior close of a VIX-family index", "pct", "number", "id",
            "implied_vol_prior_close", subjects="symbol", feed="watchlist_quotes",
            blank="provider_refused_symbol when the quote is missing", low=0, high=500),
    # breadth
    measure("nyse_advancing_count", "NYSE advancing issues at the newest breadth bar", "count", "whole_number", "id",
            "breadth_counts", feed="nyse_breadth", blank="feed_missing when no breadth bar has landed", low=0, high=10000),
    measure("nyse_declining_count", "NYSE declining issues at the newest breadth bar", "count", "whole_number", "id",
            "breadth_counts", feed="nyse_breadth", blank="feed_missing when no breadth bar has landed", low=0, high=10000),
    measure("nyse_advancing_portion", "advancers over advancers plus decliners", "portion", "number", group="breadth_side",
            feed="nyse_breadth", blank="feed_missing when no breadth bar has landed", low=0, high=1,
            lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.5),
    # leaders, rates, dollar, oil
    measure("fund_change_from_prior_close_pct", "a fund's last quote against its prior close", "pct", "number",
            group="fund_change_sign", subjects="symbol", feed="watchlist_quotes", blank="provider_refused_symbol when the quote is missing",
            low=-50, high=50, **PUSH),
    measure("sector_funds_up_count", "how many of the 11 sector funds are above their prior close", "count", "whole_number",
            group="sector_breadth", feed="watchlist_quotes", blank="partial_coverage until all 11 quote", low=0, high=11,
            lane="push", overlap="direction_all_sources", leans="leans_up", neutral=5.5),
    measure("largest_stocks_up_count", "how many of the 9 largest S&P stocks are above their prior close", "count",
            "whole_number", group="largest_stocks_breadth", feed="watchlist_quotes", blank="partial_coverage until all 9 quote",
            low=0, high=9, lane="push", overlap="direction_all_sources", leans="leans_up", neutral=4.5),
    measure("ten_year_yield_annual_pct", "10-year Treasury yield", "pct", "number", "id", "rates_level",
            feed="watchlist_quotes", blank="provider_refused_symbol when the quote is missing", low=0, high=25),
    measure("ten_year_yield_change_basis_points", "10-year yield change from its prior close", "basis_points", "number",
            group="rates_change", feed="watchlist_quotes", blank="provider_refused_symbol when the quote is missing",
            low=-200, high=200, **PUSH),
    # overnight
    measure("overnight_high_points", "highest /ES 1-minute bar from 18:00 to 09:30", "points", "number", "id",
            "overnight_levels", subjects="symbol", feed="futures_bars", blank="feed_missing when the night file is absent",
            low=1000, high=20000),
    measure("overnight_low_points", "lowest /ES 1-minute bar from 18:00 to 09:30", "points", "number", "id",
            "overnight_levels", subjects="symbol", feed="futures_bars", blank="feed_missing when the night file is absent",
            low=1000, high=20000),
    # events
    measure("minutes_until_next_event", "minutes from the cut to the next scheduled event today", "minutes", "number",
            "pull", "event_clock", subjects="impact", feed="event_calendar",
            blank="none_left_today after the day's last event; calendar_not_covering_day outside the calendar's span",
            low=0, high=600),
    measure("events_left_today_count", "scheduled events still ahead today", "count", "whole_number", "pull", "event_clock",
            feed="event_calendar", blank="calendar_not_covering_day outside the calendar's span", low=0, high=50),
    measure("next_event_kind", "the calendar's kind word for the next event today", "word", "word", "id", "event_kind",
            feed="event_calendar", blank="none_left_today after the day's last event"),
    measure("does_calendar_cover_day", "the event calendar's span includes this session", "yes_no", "yes_no", "health",
            "calendar_health", feed="event_calendar"),
    # options tape
    measure("options_trades_last_30_minutes_count", "same-day option trades in the 30 minutes to the cut", "count",
            "whole_number", "pull", "options_activity", feed="options_tape", blank="feed_missing when the tape is absent",
            low=0, twin="feed_coverage_last_30_minutes_portion"),
    measure("options_call_premium_last_30_minutes_usd", "dollars of call premium traded in the 30 minutes to the cut", "usd",
            "money_usd", "pull", "options_premium_flow", feed="options_tape", blank="feed_missing when the tape is absent",
            low=0, twin="feed_coverage_last_30_minutes_portion"),
    measure("options_put_premium_last_30_minutes_usd", "dollars of put premium traded in the 30 minutes to the cut", "usd",
            "money_usd", "pull", "options_premium_flow", feed="options_tape", blank="feed_missing when the tape is absent",
            low=0, twin="feed_coverage_last_30_minutes_portion"),
    measure("bullish_portion_of_sided_premium", "share of sided single-leg premium that was bullish (calls bought, puts sold)",
            "portion", "number", group="options_flow_side", feed="options_tape", blank="no_activity when no trade had a side",
            low=0, high=1, lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.5,
            twin="feed_coverage_last_30_minutes_portion"),
    measure("tape_gap_markers_last_30_minutes_count", "collector gap markers inside the 30-minute window", "count",
            "whole_number", "health", "tape_health", subjects="feed", feed="options_tape", low=0),
    measure("feed_coverage_last_30_minutes_portion", "share of the 30-minute window the feed covered", "portion", "number",
            "health", "feed_coverage", subjects="feed", low=0, high=1),
    # headlines
    measure("headlines_last_60_minutes_count", "headlines captured in the 60 minutes to the cut", "count", "whole_number",
            "pull", "news_volume", subjects="kind", feed="news_headlines", blank="feed_missing when today's file is absent", low=0,
            twin="feed_coverage_last_60_minutes_portion"),
    measure("feed_coverage_last_60_minutes_portion", "share of the 60-minute window the feed covered", "portion", "number",
            "health", "feed_coverage", subjects="feed", blank="not_known_at_read for feeds with no poll stamps", low=0, high=1),
    # Mirai's Claude forecast, carried as a raw input
    measure("mirai_claude_forecast_pct", "Mirai's Claude forecast for the horizon, chance of this side", "pct", "number", "id",
            "mirai_claude_forecast", subjects="horizon|side", feed="claude_forecast",
            blank="feed_missing before the day's first final forecast; too_old past the carry limit", low=0, high=100, carry=2400),
    measure("mirai_claude_base_rate_pct", "the base rate Claude was shown for that side", "pct", "number", "id",
            "mirai_claude_forecast", subjects="horizon|side", feed="claude_forecast",
            blank="feed_missing before the day's first final forecast", low=0, high=100, carry=2400),
    measure("mirai_claude_read_at", "the moment of the Claude read the forecast belongs to", "at", "moment", "id",
            "mirai_claude_forecast", feed="claude_forecast", blank="feed_missing before the day's first final forecast", carry=2400),
    # feed, login and consumer health
    measure("feed_status", "the feed's state at the cut", "word", "word", "health", "feed_health", subjects="feed",
            words=FEED_STATUSES),
    measure("feed_age_seconds", "seconds from the feed's newest input to the cut", "seconds", "number", "health",
            "feed_health", subjects="feed", blank="feed_missing when nothing has landed today", low=0),
    measure("rows_received_today_count", "rows the feed has landed today by the cut", "count", "whole_number", "health",
            "feed_health", subjects="feed", low=0),
    measure("login_state", "the Schwab login's state as Mirai's auth watch last reported it", "word", "word", "health",
            "login_health", subjects="login", feed="schwab_login", blank="not_known_at_read on rebuilt rows",
            words=("ok", "renew_soon", "expired", "unknown")),
    measure("login_hours_until_expiry", "hours from the cut to the Schwab login's expiry", "hours", "number", "health",
            "login_health", subjects="login", feed="schwab_login", blank="not_known_at_read on rebuilt rows", low=-240, high=240),
    measure("login_expires_at", "when the Schwab login expires", "at", "moment", "health", "login_health", subjects="login",
            feed="schwab_login", blank="not_known_at_read on rebuilt rows"),
    measure("are_needed_feeds_all_ok", "every feed the consumer needs is ok at the cut", "yes_no", "yes_no", "health",
            "consumer_health", subjects="consumer"),
    # nightly checks
    measure("rebuild_identical_portion", "share of live values the rebuild reproduced exactly", "portion", "number", "health",
            "rebuild_parity", subjects="table_column", low=0, high=1),
    measure("reads_compared_count", "reads with both a live and a rebuilt row", "count", "whole_number", "health",
            "rebuild_parity", low=0),
    measure("values_mismatched_count", "live values the rebuild did not reproduce", "count", "whole_number", "health",
            "rebuild_parity", low=0),
    # Phase 2: speed, swing, legs (written by the range engine)
    measure("today_typical_1_minute_move_points", "recency-weighted mean absolute 1-minute close change, half-life 15 minutes",
            "points", "number", "pull", "spx_speed", feed="spx_bars", available_from=2, low=0, **LATER),
    measure("speed_fast_5_minute_half_life_points", "the same speed with a 5-minute half-life", "points", "number", "pull",
            "spx_speed", feed="spx_bars", available_from=2, low=0, **LATER),
    measure("speed_slow_30_minute_half_life_points", "the same speed with a 30-minute half-life", "points", "number", "pull",
            "spx_speed", feed="spx_bars", available_from=2, low=0, **LATER),
    measure("speed_fast_over_slow_ratio", "fast speed over slow speed; above 1 means the pace is picking up", "ratio", "number",
            "pull", "spx_acceleration", feed="spx_bars", available_from=2, low=0, **LATER),
    measure("typical_swing_points", "speed times the square root of the horizon's minutes", "points", "number", "pull",
            "spx_speed", subjects="horizon", feed="spx_bars", available_from=2, low=0, **LATER),
    measure("options_implied_swing_points", "straddle left scaled by the square root of horizon over minutes left", "points",
            "number", "pull", "options_premium_size", subjects="horizon", feed="options_scan", low=0, **LATER),
    measure("up_legs_points", "sum of the window's rising 1-minute changes", "points", "number", group="path_legs", subjects="window_minutes",
            feed="spx_bars", low=0, lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.0, **LATER),
    measure("down_legs_points", "sum of the window's falling 1-minute changes, as a positive number", "points", "number",
            group="path_legs", subjects="window_minutes", feed="spx_bars", low=0, lane="push", overlap="direction_all_sources",
            leans="leans_down", neutral=0.0, **LATER),
    measure("trend_straightness_ratio", "net move over total path in the window; 1 is one way, 0 is pure back-and-forth",
            "ratio", "number", group="path_straightness", subjects="window_minutes", feed="spx_bars", low=0, high=1, lane="push",
            overlap="direction_all_sources", leans="no_direction", neutral=0.0, **LATER),
    # Phase 2: history fits (nightly_history_fit cuts)
    measure("band_multiplier", "swings that cover the band's share of past window endings on the same recipe", "multiplier",
            "number", "pull", "band_stretch", subjects="band|horizon", history=True, low=0, **LATER),
    measure("stretch_recipe_name", "the speed recipe and grading window the stretch was fitted on", "word", "word", "id",
            "band_stretch", subjects="band|horizon", **LATER),
    measure("fit_sessions_count", "sessions the history fit used", "count", "whole_number", "health", "fit_sample",
            subjects="horizon", **LATER),
    measure("fit_windows_count", "windows the history fit used", "count", "whole_number", "health", "fit_sample",
            subjects="horizon", **LATER),
    measure("average_price_shrink_ratio", "average-price band half-width over end-price band half-width", "ratio", "number",
            "pull", "band_stretch", subjects="horizon", history=True, low=0, high=1, **LATER),
    measure("never_leaves_rung_portion", "share of past windows whose high and low stayed inside the rung", "portion", "number",
            "pull", "rung_odds", subjects="horizon|rung_points", history=True, low=0, high=1, **LATER),
    # Phase 2: range forecast values (reads_decision_values)
    measure("band_centre_points", "price the band is centred on", "points", "number", "id", "band_centre",
            table="reads_decision_values", pillar="reads", subjects="horizon", low=1000, high=20000, **LATER),
    measure("band_half_width_points", "half-width of the end-price band", "points", "number", "pull", "band_size",
            table="reads_decision_values", pillar="reads", subjects="band|horizon", low=0, **LATER),
    measure("average_band_half_width_points", "half-width of the average-price band", "points", "number", "pull", "band_size",
            table="reads_decision_values", pillar="reads", subjects="band|horizon", low=0, **LATER),
    measure("chance_stays_inside_rung_probability", "chance price never leaves the rung over the horizon", "probability",
            "number", "pull", "rung_odds", table="reads_decision_values", pillar="reads", subjects="horizon|rung_points",
            low=0, high=1, **LATER),
    measure("width_multiplier", "a dial on the band's width, raw and applied", "multiplier", "number", "pull", "band_size",
            table="reads_decision_values", pillar="reads", subjects="adjustment", overlap="width_all_adjustments", low=0, **LATER),
    measure("chance_up_if_moves_probability", "chance the move is up, given it moves", "probability", "number",
            group="direction_call", table="reads_decision_values", pillar="reads", low=0, high=1,
            lane="push", overlap="direction_all_sources", leans="leans_up", neutral=0.5, **LATER),
    # Phase 3: outcomes and grades (results_outcome_values)
    measure("grade_start_price_points", "price the grade measured from", "points", "number", "id", "grade_start",
            table="results_outcome_values", pillar="results", low=1000, high=20000, **LATER),
    measure("grade_start_bar_at", "start of the 1-minute bar the grade measured from", "at", "moment", "id", "grade_start",
            table="results_outcome_values", pillar="results", **LATER),
    measure("grade_start_rule_name", "which grading rule set the start", "word", "word", "id", "grade_start",
            table="results_outcome_values", pillar="results", words=("at_the_read", "read_plus_7_minutes"), **LATER),
    measure("end_price_points", "price at the window's end", "points", "number", "label", "window_result",
            table="results_outcome_values", pillar="results", low=1000, high=20000, **LATER),
    measure("end_move_points", "end price minus grade-start price", "points", "number", "label", "window_result",
            table="results_outcome_values", pillar="results", **LATER),
    measure("average_move_points", "average price over the window minus grade-start price", "points", "number", "label",
            "window_result", table="results_outcome_values", pillar="results", **LATER),
    measure("high_move_points", "window high minus grade-start price", "points", "number", "label", "window_result",
            table="results_outcome_values", pillar="results", **LATER),
    measure("low_move_points", "window low minus grade-start price", "points", "number", "label", "window_result",
            table="results_outcome_values", pillar="results", **LATER),
    measure("was_inside_band", "the end price landed inside the band", "yes_no", "yes_no", "label", "band_grade",
            table="results_outcome_values", pillar="results", subjects="band", **LATER),
    measure("did_leave_rung", "price touched either edge of the rung during the window", "yes_no", "yes_no", "label",
            "rung_grade", table="results_outcome_values", pillar="results", subjects="rung_points", **LATER),
    measure("official_close_points", "the session's official close", "points", "number", "label", "window_result",
            table="results_outcome_values", pillar="results", low=1000, high=20000, **LATER),
]

MEASURES_BY_NAME = {row["measure_name"]: row for row in MEASURES}


def check_registry():
    """Refuse a registry that breaks the design's rules: duplicate names, unknown lanes or subjects, one fact in two lanes."""
    if len(MEASURES_BY_NAME) != len(MEASURES):
        raise ValueError("duplicate measure name in the registry")
    lanes_by_group = defaultdict(set)
    for row in MEASURES:
        name = row["measure_name"]
        if len(name) > 40 or name != name.lower():
            raise ValueError(f"{name}: names are lowercase and 40 characters or fewer")
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


def registry_component_rows():
    """The R2 rows: every feed, the login, the builder and the read lanes, all at version 1."""
    rows = []
    for name, meaning, mirai_file, stale_limit, has_stamp in FEEDS:
        rows.append(component("feed", name, meaning,
                              {"mirai_file": mirai_file, "stale_limit_seconds": stale_limit, "has_receipt_stamp": has_stamp}))
    for name in LOGIN_NAMES:
        rows.append(component("login", name, "the 7-day Schwab sign-in", {"renew_warning_days": 2}))
    rows.append(component("builder", BUILDER_NAME, "reads Mirai's files as they stood at a cut and writes one snapshot",
                          {"registry_version": REGISTRY_VERSION}, version=BUILDER_VERSION))
    for name, cadence, meaning in READ_LANES:
        rows.append(component("read_lane", name, meaning, {"cadence_minutes": cadence}))
    return rows


def component(kind, name, meaning, parameters, version="1"):
    """One R2 row with the columns Phase 1 can fill; the rest stay blank until a later phase needs them."""
    parameters_text = json.dumps(parameters, sort_keys=True)
    return {
        "component_kind": kind, "component_name": name, "component_version": version, "revision_number": 1,
        "version_sequence_number": 1, "previous_component_version": None, "parent_components_text": None,
        "plain_meaning_text": meaning, "signal_lane": "none", "overlap_group_name": None, "input_measures_text": None,
        "output_measures_text": None, "parameters_json_text": parameters_text,
        "parameters_sha256": hashlib.sha256(parameters_text.encode()).hexdigest(), "code_commit": None,
        "source_sha256": None, "environment_sha256": None, "golden_output_sha256": None,
        "does_change_output_values": 0, "does_change_meaning": 0, "tuned_from_trade_date": None,
        "tuned_through_trade_date": None, "starting_trust_weight_portion": 0.0, "evidence_status": "not_weight_bearing",
        "proven_on_date": None, "trust_gate_rule_version": None, "approval_status": "not_needed",
        "storage_scope": "shared", "artifact_s3_key": None, "artifact_sha256": None, "released_at": DEFINED_AT,
        "retired_at": None, "written_at": None,
    }


def feed_stale_limit_seconds(feed_name):
    """How old a feed's newest input may be at the cut before its status is stale."""
    return next(feed[3] for feed in FEEDS if feed[0] == feed_name)


def subject_key(**kinds):
    """The canonical subject key: kind=value pairs sorted by kind and joined with &; 'whole' when there are none."""
    for kind in kinds:
        if kind not in SUBJECT_KINDS:
            raise ValueError(f"unknown subject kind {kind}")
    if not kinds:
        return "whole"
    return "&".join(f"{kind}={value}" for kind, value in sorted(kinds.items()))


check_registry()
