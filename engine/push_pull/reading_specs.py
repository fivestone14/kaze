# One row per push/pull reading: what it measures (a function in forces/), the mechanism group it belongs to, its
# unit, shape, baseline rule and floor, when it speaks, its history-tested size and how much that history is worth,
# its status, the context inputs that may boost it, the family it is fitted with, and its evidence. A force only says
# what it measures; everything else about a reading is data here, so adding a reading is one function and one row.
# Each reading's recipe (its hash decides which of its days the promotion ledger may count) is what it measures and
# how: its own row, its force module's code and constants and the shared facts, baseline, shape and booster steps,
# never another group's force module nor the weights and caps, so an unrelated change leaves its live days standing.
import ast
import functools
import hashlib
import inspect
import json

from engine import session_clock
from engine.push_pull import context_boosters as ctx, read_facts, strength_shapes as shapes
from engine.push_pull import same_clock_baseline as baseline
from engine.push_pull.forces import catch_up, dealers, heavyweights, option_bets, outside_view, own_path

# Mechanism groups, by who is trading: letter, name and what it means.
GROUPS = {
    "A": ("own_path", "SPX's own moves"),
    "H": ("heavyweights", "big stocks pulling SPX"),
    "B": ("dealers", "forced hedging"),
    "C": ("catch_up", "another market leads SPX"),
    "D": ("option_bets", "who bets which way"),
    "F": ("outside_view", "Claude beyond its base rate"),
}
# Status: live readings count in the net from a third of their history size; earning ones count from 0 and grow
# only as live days vouch for them; logged ones are measured and stored but never join the net.
LIVE, EARNING, LOGGED = "live", "earning", "logged"
STATUSES = (LIVE, EARNING, LOGGED)
# How much history is worth when a reading launches at a third of its tested size: twenty live days.
HISTORY_DAYS_FOR_THIRD = 20
# Weight rules: one fixed history size, or the day-type pair a + c·tn (tn = trend-ness) fitted jointly.
FIXED, DAY_TYPE = "fixed", "day_type"
# Fit families: readings built from the same input, whose live weights the promotion ledger fits together so each
# earns only what the others do not already explain. The price family: SPX's own path and the grind that tracks it.
# The options family: the readings built from the same-day option tape.
PRICE_FAMILY = "price"
OPTIONS_FAMILY = "options"


class Speaks:
    """When a reading speaks: from a minute after the open (or from minutes before the close) until minutes before
    the close; a half day either shortens the window by the clock or sits the reading out."""

    def __init__(self, from_minute=0, from_minutes_before_close=None, until_minutes_before_close=0,
                 sits_out_half_days=False):
        self.from_minute, self.from_minutes_before_close = from_minute, from_minutes_before_close
        self.until_minutes_before_close, self.sits_out_half_days = until_minutes_before_close, sits_out_half_days

    def is_open(self, minute, session_length, half_day):
        """True when the read's minute of the session falls inside the speak window."""
        if half_day and self.sits_out_half_days:
            return False
        starts = session_length - self.from_minutes_before_close if self.from_minutes_before_close is not None \
            else self.from_minute
        return starts <= minute <= session_length - self.until_minutes_before_close

    def words(self):
        """The window in clock words for a full day."""
        start = session_clock.FULL_SESSION_MINUTES - self.from_minutes_before_close \
            if self.from_minutes_before_close is not None else self.from_minute
        end = session_clock.FULL_SESSION_MINUTES - self.until_minutes_before_close
        return f"{clock_words(start)}–{clock_words(end)}"


def clock_words(minute):
    """A session minute as a wall clock, 09:30 + minute."""
    hour, mins = divmod(9 * 60 + 30 + minute, 60)
    return f"{hour:02d}:{mins:02d}"


class Spec:
    """One reading's row."""

    def __init__(self, name, label, group, measure, unit, shape, speaks, history_size, status, *, usual=baseline.USUAL_ZERO,
                 spread_floor=0.0, half_life_sessions=30, weight_rule=FIXED, day_type=None, history_days=None,
                 boosters=(), fixed_booster=None, shared_weight=None, gated=False, whole_day_baseline=False,
                 counts="days", family=None, evidence=""):
        self.name, self.label, self.group, self.measure, self.unit = name, label, group, measure, unit
        self.shape, self.speaks, self.history_size, self.status = shape, speaks, history_size, status
        self.usual, self.spread_floor, self.half_life_sessions = usual, spread_floor, half_life_sessions
        self.weight_rule, self.day_type = weight_rule, day_type
        # History is worth twenty days for a live reading (a third of its size on day one), nothing otherwise.
        self.history_days = history_days if history_days is not None else \
            (HISTORY_DAYS_FOR_THIRD if status == LIVE else 0)
        self.boosters, self.fixed_booster, self.shared_weight = dict(boosters), fixed_booster, shared_weight
        # A gated reading's zeros (gate shut) stay out of its baseline; an event reading counts events, not days.
        self.gated, self.counts, self.family, self.evidence = gated, counts, family, evidence
        # A rarely firing gated reading pools its baseline across the whole session (same_clock_baseline).
        self.whole_day_baseline = whole_day_baseline
        if shape not in shapes.SHAPES or usual not in baseline.USUAL_RULES or status not in STATUSES \
                or group not in GROUPS:
            raise ValueError(f"reading {name} has a word outside its lists")

    @property
    def weight_name(self):
        """The name the reading's weight is earned and promoted under (shared across a pair when so)."""
        return self.shared_weight or self.name

    @property
    def in_the_net(self):
        return self.status != LOGGED


SPECS = (
    # ---- A · own path ------------------------------------------------------------------------------------------------
    Spec("gap", "Gap", "A", own_path.gap, "points", shapes.STEADY, Speaks(from_minute=35, until_minutes_before_close=20),
         +0.047, EARNING, spread_floor=8.0, family=PRICE_FAMILY,
         evidence="w +0.047 (p 0.15); the printed open shows ~77% of the true gap"),
    Spec("trend_since_open", "Trend Since Open", "A", own_path.trend_since_open, "points", shapes.STEADY,
         Speaks(from_minute=30, until_minutes_before_close=20), -0.37, EARNING, spread_floor=4.0,
         weight_rule=DAY_TYPE, day_type=(-0.37, 0.59), boosters={ctx.PATH_STRAIGHTNESS: 0.0, ctx.DEALER_GAMMA: 0.0},
         family=PRICE_FAMILY,
         evidence="w = −0.37 + 0.59·tn (joint fit): carries on trend days, fades on range days; fails Holm"),
    Spec("give_back", "Give-Back", "A", own_path.give_back, "points", shapes.CLIPPED,
         Speaks(from_minutes_before_close=15, sits_out_half_days=True), -0.111, EARNING, spread_floor=4.0,
         family=PRICE_FAMILY,
         evidence="β −0.111 (p 0.004 counting overlapping reads as separate; ~0.1 with the day as the unit) on "
                  "clip(z of the trend, ±3); earning since 2026-11-04"),
    Spec("fresh_burst", "Fresh Burst", "A", own_path.fresh_burst, "points", shapes.STEADY,
         Speaks(from_minute=30, until_minutes_before_close=0), -0.008, EARNING, spread_floor=2.0,
         family=PRICE_FAMILY, evidence="w −0.008: the newest 15 minutes beyond the day's pace say little"),
    Spec("fade_15_00", "15:00 Fade", "A", own_path.fade_15_00, "points", shapes.REVERSING,
         Speaks(from_minute=330, until_minutes_before_close=15), +0.16, EARNING, spread_floor=2.0,
         family=PRICE_FAMILY,
         evidence="fade size 0.16 (p 0.33): a 14:30→15:00 burst tends to fade (reversing: the weight is the fade's size)"),
    Spec("gap_across_average", "Gap Across Average", "A", own_path.gap_across_average, "averages",
         shapes.STEADY, Speaks(from_minute=5, until_minutes_before_close=360), +0.07, EARNING, spread_floor=1.0,
         counts="events", evidence="22 events, 77% faded; rare (one day in nine); slope +0.07 (t 0.2) on the 8 event "
                                   "days of 2026-07-06→10-23; logged until 2026-11-06, earning since 2026-11-09"),
    # ---- H · heavyweights ---------------------------------------------------------------------------------------------
    Spec("leader_surge_fade", "Leader Surge", "H", heavyweights.leader_surge_fade, "pct_points",
         shapes.REVERSING, Speaks(from_minute=60, until_minutes_before_close=20), +0.10, EARNING,
         spread_floor=0.10, half_life_sessions=20, boosters={ctx.TIME_OF_DAY: 0.0}, gated=True,
         evidence="IC +0.09 (p 0.02) raw; 0 near the day's high or low (reversing: the weight is the fade's size)"),
    Spec("grind_at_extreme", "Grind At Extreme", "H", heavyweights.grind_at_extreme, "issues", shapes.ONE_SIDED,
         Speaks(from_minute=60, until_minutes_before_close=20), +0.39, EARNING, spread_floor=60.0,
         half_life_sessions=20, gated=True, whole_day_baseline=True, family=PRICE_FAMILY,
         evidence="IC 0.06; the conditional form of Trend (r 0.81 when it fires); joint fit +0.39"),
    # ---- B · dealers --------------------------------------------------------------------------------------------------
    Spec("pin_to_centre", "Pin To Centre", "B", dealers.pin_to_centre, "swings", shapes.STEADY,
         Speaks(from_minutes_before_close=45, until_minutes_before_close=20), +0.525, EARNING, spread_floor=0.15,
         shared_weight="pin_and_dealer_drift",
         evidence="pin + drift IC +0.37 (day-shuffle p 0.0008) on the trading-year clock; earning since 2026-11-04: "
                  "the clock fix changed its inputs, its book is the overnight open interest, and live it reversed"),
    Spec("dealer_drift", "Dealer Drift", "B", dealers.dealer_drift, "bn_usd", shapes.STEADY,
         Speaks(from_minutes_before_close=45, until_minutes_before_close=20), +0.525, EARNING,
         usual=baseline.USUAL_SAME_CLOCK, spread_floor=0.2, shared_weight="pin_and_dealer_drift",
         evidence="the hedge need has a usual positive level (~+0.4bn): usual = same-clock median"),
    Spec("option_wall_untested", "Option Wall", "B", dealers.option_wall_untested, "share_per_swing",
         shapes.STEADY, Speaks(from_minute=30, until_minutes_before_close=50), +0.06, EARNING, spread_floor=0.01,
         gated=True, whole_day_baseline=True, fixed_booster=lambda read: ctx.wall_clock_booster(read.minutes_left),
         evidence="IC +0.060 (Holm 0.27); fragile to a one-strike tolerance; clock booster √(195 ÷ min left)"),
    # ---- C · catch-up --------------------------------------------------------------------------------------------------
    Spec("spy_basis", "SPY Basis", "C", catch_up.spy_basis, "points", shapes.CATCH_UP,
         Speaks(from_minute=10, until_minutes_before_close=15), +0.215, EARNING, spread_floor=0.1,
         evidence="IC +0.074 at the bar → 0.027 at publish + 1–2 min on SPY bars; rebuilt reads 2026-07-06→10-23: "
                  "+0.215 points per point (t 0.9, 39 days); logged until 2026-11-06, earning since 2026-11-09"),
    # ---- D · option bets -----------------------------------------------------------------------------------------------
    Spec("urgent_orders", "Urgent Orders", "D", option_bets.urgent_orders, "orders", shapes.STEADY,
         Speaks(from_minute=30, until_minutes_before_close=0), -0.25, EARNING, spread_floor=8.0,
         boosters={ctx.DOWN_DAY: 0.0}, family=OPTIONS_FAMILY,
         evidence="w −0.25 (urgent buying is given back) at p 0.002 counting overlapping reads as separate, ~0.2 with "
                  "the day as the unit; live it reversed (+0.20); logged 2026-11-04, earning since 2026-11-09"),
    Spec("burst_tilt", "Burst Tilt", "D", option_bets.burst_tilt, "orders", shapes.STEADY,
         Speaks(from_minute=30, until_minutes_before_close=0), +0.28, EARNING, spread_floor=15.0,
         family=OPTIONS_FAMILY, evidence="w +0.28 in history; near- vs far-money urgency"),
    Spec("option_sentiment", "Option Sentiment", "D", option_bets.option_sentiment, "share", shapes.STEADY,
         Speaks(from_minute=30, until_minutes_before_close=0), -0.138, EARNING, usual=baseline.USUAL_SAME_CLOCK,
         spread_floor=0.002, family=OPTIONS_FAMILY,
         evidence="as a share of sided contracts: w −0.138 (t −0.8) alone, −0.075 in the options joint fit, 20 days "
                  "2026-09-23→10-23: weak, faintly contrarian"),
    Spec("sentiment_shift", "Sentiment Shift", "D", option_bets.sentiment_shift, "share", shapes.STEADY,
         Speaks(from_minute=30, until_minutes_before_close=0), -0.063, EARNING, spread_floor=0.005,
         family=OPTIONS_FAMILY,
         evidence="as a share of sided contracts: w −0.063 (t −0.8) alone, ~0 in the options joint fit, 20 days "
                  "2026-09-23→10-23: the newest half hour's bets lean to being given back"),
    Spec("strike_reach", "Strike Reach", "D", option_bets.strike_reach, "share", shapes.STEADY,
         Speaks(from_minute=30, until_minutes_before_close=0), +0.095, EARNING, usual=baseline.USUAL_SAME_CLOCK,
         spread_floor=0.02, family=OPTIONS_FAMILY,
         evidence="w +0.095 (t 1.2) alone and in the options joint fit, 20 days 2026-09-23→10-23: calls reaching "
                  "further out than puts lean up"),
    # ---- F · outside view ----------------------------------------------------------------------------------------------
    Spec("claude_beyond_base", "Claude Beyond Base", "F", outside_view.claude_beyond_base, "share",
         shapes.STEADY, Speaks(from_minute=30, until_minutes_before_close=0), +0.06, EARNING, spread_floor=0.05,
         evidence="IC +0.06 (p 0.58) on 9 days; held ≤ 40 min, fading with age"),
)
SPECS_BY_NAME = {spec.name: spec for spec in SPECS}
if len(SPECS_BY_NAME) != len(SPECS):
    raise ValueError("two readings share a name")


# The steps every reading's strength passes through besides its force: the facts a force may look at, the same-clock
# baseline, the shapes and the boosters. The weights and the caps (earned_weights, net_and_odds) size what a reading
# measured, so they are not part of its recipe.
SHARED_STEPS = {"read_facts": read_facts, "strength_shapes": shapes, "same_clock_baseline": baseline,
                "context_boosters": ctx}


def module_constants(module):
    """A module's upper-case numbers and words: the constants a recipe rests on."""
    return {key: value for key, value in vars(module).items()
            if key.isupper() and isinstance(value, (int, float, str, tuple))}


def code_sha256(module):
    """The hash of a module's syntax tree with every docstring left out: every function and helper in it counts, a
    comment or docstring edit does not."""
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and body \
                and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def recipe_parameters(spec):
    """What a reading measures and how, everything its recipe hash freezes: its row's measuring words and numbers, the
    way its history claims it pushes (the Trend's whole pair), its force module's code and constants and the shared
    steps' code and constants. Its label, group, status, history size and evidence may change without restarting its
    clock; so may another group's force module."""
    force = inspect.getmodule(spec.measure)
    claims = spec.day_type if spec.weight_rule == DAY_TYPE else "pull" if spec.history_size < 0 else "push"
    return {"measure": spec.measure.__name__, "unit": spec.unit, "shape": spec.shape, "usual": spec.usual,
            "speaks": spec.speaks.words(), "sits_out_half_days": spec.speaks.sits_out_half_days,
            "spread_floor": spec.spread_floor, "half_life_sessions": spec.half_life_sessions, "gated": spec.gated,
            "whole_day_baseline": spec.whole_day_baseline, "weight_rule": spec.weight_rule, "claims": claims,
            "boosters": sorted(spec.boosters), "fixed_booster": spec.fixed_booster is not None,
            "shared_weight": spec.shared_weight, "counts": spec.counts,
            "force": {"module": force.__name__.rsplit(".", 1)[-1], "constants": module_constants(force),
                      "code_sha256": code_sha256(force)},
            "steps": {name: {"constants": module_constants(module), "code_sha256": code_sha256(module)}
                      for name, module in SHARED_STEPS.items()}}


@functools.lru_cache(maxsize=None)
def recipe_hashes():
    """Each reading's recipe hash, {name: sha256 of its recipe parameters}: the registry's component row carries the
    same hash, every read stamps it beside the reading's strength, and the promotion ledger counts a read for a
    reading only under the reading's current hash."""
    return {spec.name: hashlib.sha256(json.dumps(recipe_parameters(spec), sort_keys=True).encode()).hexdigest()
            for spec in SPECS}


def spec_rows():
    """Every spec as a plain row for the registry's component rows: its name and evidence, and its recipe (the
    parameters the component row hashes)."""
    return [{"name": spec.name, "evidence": spec.evidence, **recipe_parameters(spec)} for spec in SPECS]
