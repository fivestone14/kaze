# The fixed numbers and words of every component the registry_components table describes: the range recipe, the
# band and grade words, the width dials, the push readings, the trust tests and the outcome rules. One home for every
# number the range, grade and trust code shares; nothing here is read from a file or a store, so this file imports
# nothing and every group may import it.

# ---- The range recipe ------------------------------------------------------------------------------------------------

RANGE_ENGINE_NAME = "range_engine"
RANGE_ENGINE_VERSION = "1"
COMPONENT_VERSION = "1"
SPEED_HALF_LIFE_MINUTES = 15
FAST_SPEED_HALF_LIFE_MINUTES = 5
SLOW_SPEED_HALF_LIFE_MINUTES = 30
# A channel is one horizon's block: its swing, bands, dials, direction call and windows.
HORIZON_MINUTES = {"5_minutes": 5, "30_minutes": 30, "60_minutes": 60}
HEADLINE_HORIZON = "30_minutes"
RANGE_INPUT_FEEDS = ("spx_bars", "options_scan")
PATH_WINDOW_MINUTES = (15, 30, 60)
RUNG_POINTS = {"5_minutes": 5, "30_minutes": 10, "60_minutes": 15}
BAND_PORTION = 0.8
# The band words: the 80% band of the end price, and the 80% band of the average price over the window.
BAND = "80"
AVERAGE_BAND = "average_80"
BAND_PRICE_MEASURE = {BAND: "end_price", AVERAGE_BAND: "average_price"}
GRADE_START_RULE = "at_read"
GRADE_START_RULE_VERSION = "1"
GRADE_START_RULE_NAMES = (GRADE_START_RULE,)
FIT_LOOKBACK_SESSIONS = 60
FULL_SESSION_RULE = "every_minute_held"
FIT_FIRST_WINDOW_MINUTE = 30
FIT_WINDOW_STEP_MINUTES = 5
# The label each band's stretch is fitted under: the speed recipe, the grade start and the window grid.
STRETCH_RECIPE_LABEL = {band: f"speed_half_life_{SPEED_HALF_LIFE_MINUTES}_close_changes|{GRADE_START_RULE}|{price}"
                        f"|every_{FIT_WINDOW_STEP_MINUTES}_minutes_from_{FIT_FIRST_WINDOW_MINUTE}"
                        for band, price in BAND_PRICE_MEASURE.items()}
FED_DECISION_EVENT_KINDS = ("FOMC",)
FED_OWNER_RULE_MULTIPLIER = 2.6
OWNER_RULE_VARIANT = "owner_rule"
BASE_VARIANT = "base"
EVEN_CHANCE = 0.5
RANGE_DECISION_KIND = "range_forecast"
WALL_TYPES = ("call_wall", "put_wall")
CLAUDE_SIDES = ("up", "flat", "down")
# The options cross-check's verdict (shown, never added in): agree when the options-implied swing sits within this
# ratio of the tape swing either way.
OPTIONS_AGREE_RATIO = 1.25
OPTIONS_CHECK_AGREE, OPTIONS_CHECK_DIFFER = "agree", "differ"
OPTIONS_CHECK_WORDS = (OPTIONS_CHECK_AGREE, OPTIONS_CHECK_DIFFER)
CAP_PORTION = 0.95

# ---- The width dials -------------------------------------------------------------------------------------------------
# In the order the trust ledger scores them (each earns weight only for what it adds beyond the dials before it);
# every dial starts applied at 1.00 and moves only on a weight the ledger wrote. The Fed dial sits outside the cap:
# the cap holds the product of the other dials, the Fed multiplier stacks on top.

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
ACCELERATION_MEASURE = "speed_acceleration_ratio"
WIDTH_DIAL_MEANING = {
    FED_DIAL: "wider from a Fed decision to the close, the owner's rule",
    RELEASE_DIAL: "a fitted ratio, either way, when a timed economic release lands inside the window",
    CLOSING_DIAL: "wider for unclipped windows ending in the last 15 minutes",
    ACCELERATION_DIAL: "fast speed over slow speed: above 1 the market is speeding up, below 1 slowing",
    OPTIONS_VS_TAPE_DIAL: "the options-implied swing against the tape swing",
}
WIDTH_DIAL_PARAMETERS = {
    FED_DIAL: {"multiplier": FED_OWNER_RULE_MULTIPLIER, "event_kinds": FED_DECISION_EVENT_KINDS, "capped": False},
    RELEASE_DIAL: {f"multiplier_{RELEASE_FIXED_HORIZON}": RELEASE_WINDOW_MULTIPLIER, "other_horizons": "fitted ratio",
                   "tier_prefix": RELEASE_TIER_PREFIX, "never": FED_EVENT_PREFIXES},
    CLOSING_DIAL: {"last_minutes": CLOSING_WINDOW_MINUTES, "clipped": False},
    ACCELERATION_DIAL: {"input": ACCELERATION_MEASURE},
    OPTIONS_VS_TAPE_DIAL: {"input": "options_over_tape_swing_ratio", "power": OPTIONS_VS_TAPE_POWER},
}

# ---- The push readings -----------------------------------------------------------------------------------------------
# Which way, in the order the ledger scores them, each a lean: the chance of up if price moves, 0 to 1. Share
# readings lean by their share; signed readings by even + tilt x tanh(value / scale).

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
# What the phone shows for each reading: a short question and a short answer from the reading's fixed words. A lean
# within EVEN_LEAN_ZONE of even (0.47 to 0.53) answers Flat or Even (Choppy for the path), else the sign of the reading
# picks up or down; a path leaning one way is Steady at STEADY_STRAIGHTNESS or more, else Drifting (a random 30-minute
# walk scores about 0.18, one way is 1). A blank input has no answer. Breadth answers with its share alone.
EVEN_LEAN_ZONE = 0.03
STEADY_STRAIGHTNESS = 0.4
PUSH_READING_LABEL = {PRICE_TREND: "Since Yesterday", STRAIGHTNESS: "Path", BREADTH: "Stocks Rising",
                      BIG_TECH_VS_REST: "Big Tech", OPTIONS_BUYERS: "Options Bets", YIELD_CHANGE: "Bond Rates",
                      MIRAI_CLAUDE: "Mirai's Claude"}
# Each reading's answers as (up, down, even); {points} is the move in whole points, {share} a percent.
PUSH_ANSWER_WORDS = {
    PRICE_TREND: ("Up {points} Pts", "Down {points} Pts", "Flat"),
    STRAIGHTNESS: ("Steady Up", "Steady Down", "Choppy", "Drifting Up", "Drifting Down"),
    BREADTH: ("{share}",),
    BIG_TECH_VS_REST: ("Ahead", "Behind", "Even"),
    OPTIONS_BUYERS: ("More Calls", "More Puts", "Even"),
    YIELD_CHANGE: ("Rising", "Falling", "Flat"),
    MIRAI_CLAUDE: ("Leans Up", "Leans Down", "Even"),
}
PUSH_READING_PARAMETERS = {
    PRICE_TREND: {"tilt": LEAN_TILT, "scale_points": LEAN_SCALE[PRICE_TREND], "even_lean_zone": EVEN_LEAN_ZONE},
    STRAIGHTNESS: {"tilt": LEAN_TILT, "window_minutes": STRAIGHTNESS_WINDOW_MINUTES,
                   "steady_straightness": STEADY_STRAIGHTNESS},
    BREADTH: {},
    BIG_TECH_VS_REST: {"tilt": LEAN_TILT, "scale_pct_points": LEAN_SCALE[BIG_TECH_VS_REST],
                       "symbols": (BIG_TECH_SYMBOL, REST_SYMBOL), "even_lean_zone": EVEN_LEAN_ZONE},
    OPTIONS_BUYERS: {"even_lean_zone": EVEN_LEAN_ZONE},
    YIELD_CHANGE: {"tilt": LEAN_TILT, "scale_basis_points": LEAN_SCALE[YIELD_CHANGE], "even_lean_zone": EVEN_LEAN_ZONE},
    MIRAI_CLAUDE: {"horizon": CLAUDE_LEAN_HORIZON, "even_lean_zone": EVEN_LEAN_ZONE},
}
DIRECTION_TEXT_UNTIL_EARNED = "coin flip until earned"
RANGE_RECIPE_PARAMETERS = {
    "speed_half_life_minutes": SPEED_HALF_LIFE_MINUTES, "fast_half_life_minutes": FAST_SPEED_HALF_LIFE_MINUTES,
    "slow_half_life_minutes": SLOW_SPEED_HALF_LIFE_MINUTES, "horizon_minutes": HORIZON_MINUTES,
    "rung_points": RUNG_POINTS, "band_portion": BAND_PORTION, "grade_start_rule": GRADE_START_RULE,
    "fit_lookback_sessions": FIT_LOOKBACK_SESSIONS, "full_session_rule": FULL_SESSION_RULE,
    "fit_first_window_minute": FIT_FIRST_WINDOW_MINUTE, "fit_window_step_minutes": FIT_WINDOW_STEP_MINUTES,
    "stretch_recipe": STRETCH_RECIPE_LABEL, "fed_owner_rule_multiplier": FED_OWNER_RULE_MULTIPLIER,
    "width_dials": WIDTH_DIALS, "uncapped_dial": FED_DIAL, "cap_portion": CAP_PORTION,
    "even_chance": EVEN_CHANCE, "push_readings": PUSH_READINGS, "lean_tilt": LEAN_TILT,
}

# ---- The trust ledger: two tests fixed in advance, their verdict words, and what a verdict needs --------------------

TRUST_WEIGHT_KIND, SKILL_METRIC_KIND = "trust_weight", "skill_metric"
EVALUATION_KINDS = (TRUST_WEIGHT_KIND, SKILL_METRIC_KIND)
PUSH_READING_KIND, WIDTH_DIAL_KIND = "push_reading", "width_dial"
EVALUATED_KINDS = (PUSH_READING_KIND, WIDTH_DIAL_KIND)
# The dial's and the reading's trust is keyed by the measure that carries the applied weight; its lane and overlap
# group are read off that measure's registry row.
WEIGHTED_MEASURE = {PUSH_READING_KIND: "push_lean_probability", WIDTH_DIAL_KIND: "width_multiplier"}
VERDICT_COLUMN = "metric_text"
VERDICT_PASSED, VERDICT_HELD, VERDICT_HELD_DOWN, VERDICT_FAILED = "passed", "held", "held_down", "failed"
TOO_FEW_SESSIONS = "too_few_sessions"
VERDICTS = (VERDICT_PASSED, VERDICT_HELD, VERDICT_HELD_DOWN, VERDICT_FAILED, TOO_FEW_SESSIONS)
EARNED_VERDICTS = (VERDICT_PASSED, VERDICT_HELD_DOWN)
BOOTSTRAP_BY_DAY = "bootstrap_by_day"
INTERVAL_METHODS = (BOOTSTRAP_BY_DAY,)
# The one interval every verdict and the pass test use: the day-resampled 95% interval of the day means.
INTERVAL_PERCENT = 95
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
    "minimum_sessions": TRUST_MINIMUM_SESSIONS, "interval": BOOTSTRAP_BY_DAY, "interval_percent": INTERVAL_PERCENT,
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

# ---- The outcome and grade rules: a changed rule is a new version, never an edit -----------------------------------

WINDOW_RULE = ("window_from_bars", "1")
GRADE_RULE = ("band_grade", "1")
BARS_MISSING = "bars_missing"
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
