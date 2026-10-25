# The fixed numbers and words of every component the registry_components table describes: the range recipe, the
# band and grade words, the width dials, the push/pull factors, the trust tests and the outcome rules. One home for
# every number the range, push/pull, grade and trust code shares; nothing here is read from a file or a store, so this
# file imports nothing and every group may import it.

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

# ---- The push/pull readings ----------------------------------------------------------------------------------------
# The readings (reading_specs.py) are the forces on price; each one's strength × weight × booster × swing is its push
# in points, positive pushing price up and negative pulling it down; the channel's net is their sum under the caps.
# Which side a reading is on, from its points: push (up), pull (down) or even at 0.
PUSH_SIDE, PULL_SIDE, EVEN_SIDE = "push", "pull", "even"
# The wind is how hard the forces blow: the net over the swing, named on a wind scale (each level holds strengths
# below its mark) with a dot for each step reached. The phone's push/pull card draws all of it.
WIND_LEVELS = (("calm", 0.03), ("light_air", 0.10), ("breeze", 0.20), ("strong_breeze", None))
WIND_DOT_STEPS = (0.03, 0.10, 0.20, 0.30)
# The reading recipes' version: a changed recipe is a new revision of its registry_components row (its hash is the
# promotion ledger's frozen recipe), a changed version a fresh start.
PUSH_PULL_RECIPE_VERSION = "1"
RANGE_RECIPE_PARAMETERS = {
    "speed_half_life_minutes": SPEED_HALF_LIFE_MINUTES, "fast_half_life_minutes": FAST_SPEED_HALF_LIFE_MINUTES,
    "slow_half_life_minutes": SLOW_SPEED_HALF_LIFE_MINUTES, "horizon_minutes": HORIZON_MINUTES,
    "rung_points": RUNG_POINTS, "band_portion": BAND_PORTION, "grade_start_rule": GRADE_START_RULE,
    "fit_lookback_sessions": FIT_LOOKBACK_SESSIONS, "full_session_rule": FULL_SESSION_RULE,
    "fit_first_window_minute": FIT_FIRST_WINDOW_MINUTE, "fit_window_step_minutes": FIT_WINDOW_STEP_MINUTES,
    "stretch_recipe": STRETCH_RECIPE_LABEL, "fed_owner_rule_multiplier": FED_OWNER_RULE_MULTIPLIER,
    "width_dials": WIDTH_DIALS, "uncapped_dial": FED_DIAL, "cap_portion": CAP_PORTION,
    "even_chance": EVEN_CHANCE,
}

# ---- The trust ledger: the dial test fixed in advance, its verdict words, and what a verdict needs -------------------

TRUST_WEIGHT_KIND, SKILL_METRIC_KIND = "trust_weight", "skill_metric"
EVALUATION_KINDS = (TRUST_WEIGHT_KIND, SKILL_METRIC_KIND)
PUSH_PULL_FACTOR_KIND, WIDTH_DIAL_KIND = "push_pull_factor", "width_dial"
EVALUATED_KINDS = (PUSH_PULL_FACTOR_KIND, WIDTH_DIAL_KIND)
# The dial's and the factor's trust is keyed by the measure that carries the applied weight; its lane and overlap
# group are read off that measure's registry row.
WEIGHTED_MEASURE = {PUSH_PULL_FACTOR_KIND: "push_pull_points", WIDTH_DIAL_KIND: "width_multiplier"}
VERDICT_COLUMN = "metric_text"
VERDICT_PASSED, VERDICT_HELD, VERDICT_HELD_DOWN, VERDICT_FAILED = "passed", "held", "held_down", "failed"
TOO_FEW_SESSIONS = "too_few_sessions"
# The promotion ledger's words for a push/pull reading sit in the same column: promoted, earning, demoted.
PROMOTED, EARNING_WORD, DEMOTED = "promoted", "earning", "demoted"
PROMOTION_WORDS = (PROMOTED, EARNING_WORD, DEMOTED)
VERDICTS = (VERDICT_PASSED, VERDICT_HELD, VERDICT_HELD_DOWN, VERDICT_FAILED, TOO_FEW_SESSIONS, *PROMOTION_WORDS)
EARNED_VERDICTS = (VERDICT_PASSED, VERDICT_HELD_DOWN)
BOOTSTRAP_BY_DAY = "bootstrap_by_day"
INTERVAL_METHODS = (BOOTSTRAP_BY_DAY,)
# The one interval every verdict and the pass test use: the day-resampled 95% interval of the day means.
INTERVAL_PERCENT = 95
UNDIALED_BAND, NO_SKILL = "undialed_band", "no_skill"
BASELINES = (UNDIALED_BAND, NO_SKILL)
DIAL_ON, DIAL_OFF, FAKE_BASELINE = "dial_on", "dial_off", "fake_baseline"
DIAL_TEST = ("dial_band_test", "2")
# The promotion ledger judges the push/pull readings: a daily score per reading, an e-value, promotion at 20-to-1
# with enough days, demotion at 10-to-1 (grade/promotion_ledger.py).
PROMOTION_LEDGER = ("promotion_ledger", COMPONENT_VERSION)
PROMOTE_AT, DEMOTE_AT = 20.0, 10.0
PROMOTE_MINIMUM_DAYS, PROMOTE_MINIMUM_EVENTS = 30, 15
E_VALUE_BET = 0.25
PROMOTION_LEDGER_PARAMETERS = {
    "sample": "live first-built base decisions on the headline channel whose grade counts",
    "score": "the day's mean of strength × average grade from publish + 1 min, in swings",
    "e_value": f"product over days of 1 + {E_VALUE_BET} × clip(score ÷ spread, ±1); the reversed sign likewise",
    "promote": f"e ≥ {PROMOTE_AT} with ≥ {PROMOTE_MINIMUM_DAYS} days ({PROMOTE_MINIMUM_EVENTS} events)",
    "demote": f"reversed e ≥ {DEMOTE_AT}", "recipe": "frozen by hash; a change restarts the clock",
}
TRUST_MINIMUM_SESSIONS = 20
FED_TEST_MINIMUM = {"windows": 20, "sessions": 5}
HOLD_RUNS = 3
FAKE_PERMUTATIONS = 200
FAKE_PERCENTILE = 95
FAKE_SEED = 1
INTERVAL_SCORE_ALPHA = 1 - BAND_PORTION
TRUST_TEST_SHARED = {
    "sample": "live first-built base decisions whose grade counts", "replay_counts": False,
    "minimum_sessions": TRUST_MINIMUM_SESSIONS, "interval": BOOTSTRAP_BY_DAY, "interval_percent": INTERVAL_PERCENT,
    "hold_runs": HOLD_RUNS, "hold_down_runs": HOLD_RUNS, "hold_down_unless": "the interval sits below 0",
    "fake_permutations": FAKE_PERMUTATIONS, "fake_percentile": FAKE_PERCENTILE, "fake_seed": FAKE_SEED,
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
