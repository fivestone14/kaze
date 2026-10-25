# The trust ledger: every width dial scored per channel, nightly, on live graded windows only (replayed history never
# counts) with a test fixed in advance in the registry, each on top of what has already earned weight, against a fake
# baseline, held three runs on the way up and down; written as results_evaluations rows. A dial's weight moves the
# band. The push/pull readings are scored by the promotion ledger (grade/promotion_ledger.py), not here.
import itertools
from collections import defaultdict

import numpy as np

from engine import session_clock
from engine.range import range_fit, width_dials
from engine.store import components, registry, store, store_queries

RUN_ID_PREFIX = "trust_ledger"
PRINT_COLUMNS = ("dial", "channel", "sessions", "windows", "score", "95% interval", "fake 95th",
                 "verdict", "weight")
FAMILY_SIZE = {components.DIAL_TEST: len(components.WIDTH_DIALS) * len(components.HORIZON_MINUTES)}


# ---- The sample: live windows with what each decision stored ------------------------------------------------------

def counting_live_windows(conn, horizon_name, through_day):
    """The channel's live, first-built base decisions through a day whose window counts, with what each stored."""
    # Oldest first, each with its day, its window's end move, its undialed half-width (swing x stretch), cap and
    # every dial's raw value.
    values = defaultdict(dict)
    for row in store_queries.live_counting_decision_values(conn, horizon_name, through_day, components.GRADE_RULE):
        key = (row["measure_name"], row["subject_key"], row["value_role"])
        values[row["decision_rowid"]][key] = row["value_number"]
    horizon = registry.subject_key(horizon=horizon_name)
    band = registry.subject_key(band=components.BAND, horizon=horizon_name)
    windows = []
    for decision in store_queries.live_counting_decisions(conn, horizon_name, through_day, components.GRADE_RULE):
        stored = values[decision["decision_rowid"]]
        swing = stored.get(("typical_swing_points", horizon, registry.VALUE_ROLE))
        stretch = stored.get(("stretch_multiplier", band, registry.VALUE_ROLE))
        if swing is None or stretch is None:
            continue
        windows.append({
            "day": decision["trade_date"], "origin": decision["row_origin"], "move": decision["end_move"],
            "clipped": decision["decision_status"] == registry.DECISION_CLIPPED,
            "undialed_half_width": swing * stretch,
            "cap": stored.get(("width_cap_multiplier", horizon, registry.VALUE_ROLE)),
            "raw": {dial: stored.get(("width_multiplier", registry.subject_key(adjustment=dial), registry.RAW_ROLE))
                    for dial in components.WIDTH_DIALS},
        })
    return windows


def by_day(windows):
    """The windows grouped by day, in day order."""
    days = defaultdict(list)
    for window in windows:
        days[window["day"]].append(window)
    return [days[day] for day in sorted(days)]


def sample_counts(windows, horizon_name):
    """The sample's sessions, independent (non-overlapping) windows, rows, first-built live rows and first day."""
    minutes = components.HORIZON_MINUTES[horizon_name]
    return {"sessions_count": len({window["day"] for window in windows}),
            "independent_windows_count": len(windows) * session_clock.SLOT_MINUTES // minutes,
            "rows_count": len(windows),
            "live_frozen_rows_count": sum(1 for window in windows if window["origin"] == "live_frozen"),
            "data_from_trade_date": min((window["day"] for window in windows), default=None)}


def membership_permutations(touched_days, all_days):
    """The dial fake baseline's inputs: each day's touched windows handed whole to another day, per permutation."""
    # As a same-size random set of that day's windows matched on the clipped flag, carrying the first day's raws;
    # drawn with the fixed seed.
    draw = np.random.default_rng(components.FAKE_SEED)
    pool = {day[0]["day"]: day for day in all_days}
    for _ in range(components.FAKE_PERMUTATIONS):
        fake_days = []
        for day in touched_days:
            others = [name for name in pool if name != day[0]["day"]]
            target = pool[draw.choice(others)]
            fake_day = []
            for window in day:
                matched = [other for other in target if other["clipped"] == window["clipped"]] or target
                fake_day.append(dict(matched[draw.integers(len(matched))], fake=window["dial_raw"]))
            fake_days.append(fake_day)
        yield fake_days


# ---- The two tests -------------------------------------------------------------------------------------------------

def band_scores(days, dial, earned, column="dial_raw"):
    """Per day, the mean interval-score gain of the band with the dial's raw against without, plus the coverage."""
    # On top of the earned dials (the Fed outside the cap); the column may be a permuted stand-in for the fake
    # baseline; coverage comes back with and without the dial.
    gains, inside = [], {components.DIAL_ON: [], components.DIAL_OFF: []}
    for day in days:
        day_gains = []
        for window in day:
            raw = window[column]
            others = float(np.prod([window["raw"][name] for name in earned if name != components.FED_DIAL]))
            fed = window["raw"][components.FED_DIAL] if components.FED_DIAL in earned else width_dials.NO_EFFECT
            off = width_dials.half_width(window["undialed_half_width"], others, fed, window["cap"])
            if dial == components.FED_DIAL:
                on = width_dials.half_width(window["undialed_half_width"], others, raw, window["cap"])
            else:
                on = width_dials.half_width(window["undialed_half_width"], others * raw, fed, window["cap"])
            day_gains.append(interval_score(off, window["move"]) - interval_score(on, window["move"]))
            inside[components.DIAL_ON].append(abs(window["move"]) <= on)
            inside[components.DIAL_OFF].append(abs(window["move"]) <= off)
        gains.append(float(np.mean(day_gains)))
    return gains, {key: float(np.mean(flags)) if flags else None for key, flags in inside.items()}


def dial_test(conn, windows, dial, earned, horizon_name, through_day):
    """The dial test on one dial and channel, on top of the dials earned before it."""
    # The band's interval score with the dial's raw against without over the windows the dial touches, its interval,
    # the fake baseline, the hold rule, the verdict and weight; the Fed dial needs its own minimum of windows and
    # sessions.
    scored = [dict(window, dial_raw=window["raw"][dial]) for window in windows
              if window["raw"][dial] not in (None, width_dials.NO_EFFECT)]
    days = by_day(scored)
    gains, coverage = band_scores(days, dial, earned)
    fakes = [float(np.mean(band_scores(fake_days, dial, earned, "fake")[0]))
             for fake_days in membership_permutations(days, by_day(windows))] if days and len(by_day(windows)) > 1 \
        else []
    enough = len(days) >= components.TRUST_MINIMUM_SESSIONS if dial != components.FED_DIAL else (
        len(scored) >= components.FED_TEST_MINIMUM["windows"] and len(days) >= components.FED_TEST_MINIMUM["sessions"])
    result = test_result(conn, judged=(components.WIDTH_DIAL_KIND, dial, components.COMPONENT_VERSION),
                         horizon_name=horizon_name, through_day=through_day, scored=scored, day_means=gains,
                         fakes=fakes, enough=enough, weight_rule=lambda low: 1.0)
    result["coverage"] = coverage
    result["baseline_value"] = float(np.mean([interval_score(
        width_dials.half_width(window["undialed_half_width"], 1.0, 1.0, window["cap"]), window["move"])
        for window in scored])) if scored else None
    return result


def interval_score(half_width, move):
    """The proper score of a central band: its full width plus the penalty for a move landing outside it."""
    return 2 * half_width + 2 / components.INTERVAL_SCORE_ALPHA * max(0.0, abs(move) - half_width)


def test_result(conn, *, judged, horizon_name, through_day, scored, day_means, fakes, enough, weight_rule):
    """A test's result: the sample counts and, with enough sessions, the score, interval, fake, verdict and weight."""
    # The verdict reads the runs before: a pass (the interval above 0, the mean above the fake) is held for three runs
    # before the weight moves from 0, then passed; an earned weight that fails is held_down (kept) for three runs
    # before it returns to 0, failed at once when the interval sits below 0.
    result = {"judged": judged, "counts": sample_counts(scored, horizon_name), "score": None, "interval": None,
              "fake": None, "weight": None, "verdict": components.TOO_FEW_SESSIONS,
              "detail": f"{components.TOO_FEW_SESSIONS}: {len({window['day'] for window in scored})} of "
                        f"{components.TRUST_MINIMUM_SESSIONS} live sessions"}
    if not enough:
        return result
    mean, low, high = range_fit.bootstrap_interval(day_means)
    fake = float(np.percentile(fakes, components.FAKE_PERCENTILE)) if fakes else None
    earlier = store_queries.prior_verdicts(conn, judged, horizon_name, through_day, components.HOLD_RUNS - 1)
    earned = bool(earlier) and earlier[0][0] in components.EARNED_VERDICTS
    result.update({"score": mean, "interval": (low, high), "fake": fake, "weight": 0.0,
                   "verdict": components.VERDICT_FAILED, "detail": f"interval low {low:+.4f}, fake 95th "
                   f"{fake if fake is None else round(fake, 4)}"})
    if low > 0 and fake is not None and mean > fake:
        passes = 1 + sum(1 for _ in itertools.takewhile(
            lambda run: run[0] in (components.VERDICT_PASSED, components.VERDICT_HELD), earlier))
        held = passes < components.HOLD_RUNS and not earned
        result.update({"verdict": components.VERDICT_HELD if held else components.VERDICT_PASSED,
                       "weight": 0.0 if held else weight_rule(low),
                       "detail": f"pass {passes} of {components.HOLD_RUNS} runs"})
    elif earned and high >= 0:
        fails = 1 + sum(1 for _ in itertools.takewhile(lambda run: run[0] == components.VERDICT_HELD_DOWN, earlier))
        if fails < components.HOLD_RUNS:
            result.update({"verdict": components.VERDICT_HELD_DOWN, "weight": earlier[0][1],
                           "detail": f"fail {fails} of {components.HOLD_RUNS} runs, weight kept"})
    return result


# ---- Writing the ledger (nightly) ------------------------------------------------------------------------------------

def ledger_rows(conn, through_day, in_force_day, written_at):
    """Every dial's rows per channel, in the registry's order."""
    # Each test runs on top of the components that earned weight earlier in the same pass.
    written_iso = session_clock.iso_utc(written_at)
    run = {"trade_date": in_force_day.isoformat(), "data_through_trade_date": through_day.isoformat(),
           "applies_from_at": session_clock.iso_utc(session_clock.session_open(in_force_day)),
           "known_at": written_iso, "written_at": written_iso}
    rows = []
    for horizon_name in components.HORIZON_MINUTES:
        windows = counting_live_windows(conn, horizon_name, through_day.isoformat())
        earned_dials = []
        for dial in components.WIDTH_DIALS:
            result = dial_test(conn, windows, dial, earned_dials, horizon_name, through_day.isoformat())
            rows.extend(test_rows(result, components.DIAL_TEST, horizon_name, "interval_score_gain_points",
                                  components.UNDIALED_BAND, result["baseline_value"], run))
            for slice_key, coverage in result["coverage"].items():
                rows.append(store.evaluation_row(
                    run_id(through_day, components.DIAL_TEST), result["judged"], components.DIAL_TEST, horizon_name,
                    kind=components.SKILL_METRIC_KIND, metric_name="band_coverage_portion", value=coverage,
                    baseline=components.UNDIALED_BAND, counts=result["counts"], run=run, slice_key=slice_key,
                    family_size=FAMILY_SIZE[components.DIAL_TEST]))
            if result["weight"]:
                earned_dials.append(dial)
    return rows


def run_id(through_day, judge):
    """One ledger run's id: the ledger, the data-through day and the judge."""
    return f"{RUN_ID_PREFIX}|{through_day}|{judge[0]}@{judge[1]}"


def test_rows(result, judge, horizon_name, score_measure, baseline, baseline_value, run):
    """A test result as its three ledger rows: the trust weight, the score with its interval, the fake baseline."""
    evaluation_id = run_id(run["data_through_trade_date"], judge)
    family_size = FAMILY_SIZE[judge]
    return [store.evaluation_row(evaluation_id, result["judged"], judge, horizon_name,
                                 kind=components.TRUST_WEIGHT_KIND, metric_name="trust_weight_portion",
                                 value=result["weight"], verdict=result["verdict"], detail=result["detail"],
                                 baseline=baseline, counts=result["counts"], run=run, family_size=family_size),
            store.evaluation_row(evaluation_id, result["judged"], judge, horizon_name,
                                 kind=components.SKILL_METRIC_KIND, metric_name=score_measure, value=result["score"],
                                 verdict=result["verdict"], interval=result["interval"], baseline=baseline,
                                 baseline_value=baseline_value if result["score"] is not None else None,
                                 counts=result["counts"], run=run, family_size=family_size),
            store.evaluation_row(evaluation_id, result["judged"], judge, horizon_name,
                                 kind=components.SKILL_METRIC_KIND, metric_name=score_measure, value=result["fake"],
                                 baseline=baseline, counts=result["counts"], run=run,
                                 slice_key=components.FAKE_BASELINE, family_size=family_size)]


def write_ledger(conn, through_day, in_force_day, written_at):
    """Score everything and write the ledger for the next session; returns a phrase for the nightly's health line."""
    rows = ledger_rows(conn, through_day, in_force_day, written_at)
    written = store.write_evaluations(conn, rows)
    weights = [row for row in rows if row["evaluation_kind"] == components.TRUST_WEIGHT_KIND]
    verdicts = {verdict: sum(1 for row in weights if row[components.VERDICT_COLUMN] == verdict)
                for verdict in components.VERDICTS}
    earned = [f"{row['evaluated_name']} {row['horizon_name']} {row['metric_value']}" for row in weights
              if row["metric_value"]]
    counts = ", ".join(f"{count} {verdict}" for verdict, count in verdicts.items() if count)
    return (f"trust ledger {written} rows written for {in_force_day}: {len(weights)} weights ({counts}), "
            f"earned {', '.join(earned) or 'none'}")


# ---- Printing the ledger ---------------------------------------------------------------------------------------------

def ledger_lines(conn):
    """The newest ledger per dial and channel: sample, score, interval, fake baseline, verdict, weight."""
    rows = store_queries.newest_evaluations(conn)
    if not rows:
        return ["no trust ledger yet: the nightly writes it after its first run"]
    cells = defaultdict(dict)
    for row in rows:
        cells[(row["evaluated_name"], row["horizon_name"])][(row["evaluation_kind"], row["slice_key"])] = row
    order = {name: place for place, name in enumerate(components.WIDTH_DIALS)}
    newest = max(row["trade_date"] for row in rows)
    through = max(row["data_through_trade_date"] for row in rows)
    lines = [f"trust ledger ({components.DIAL_TEST[0]}@{components.DIAL_TEST[1]} on dials): weights in force for "
             f"{newest}, live data through {through}",
             f"minimum {components.TRUST_MINIMUM_SESSIONS} live sessions (Fed dial: "
             f"{components.FED_TEST_MINIMUM['windows']} windows over {components.FED_TEST_MINIMUM['sessions']} "
             f"sessions); a weight needs the interval above 0, the score above the fake baseline's "
             f"{components.FAKE_PERCENTILE}th percentile, on {components.HOLD_RUNS} runs in a row",
             "{:<18} {:<11} {:>8} {:>8} {:>9} {:>20} {:>9} {:<16} {}".format(*PRINT_COLUMNS)]
    for (name, horizon), cell in sorted(cells.items(), key=lambda item: (order.get(item[0][0], len(order)),
                                                                          components.HORIZON_MINUTES[item[0][1]])):
        lines.append(ledger_line(name, horizon, cell[(components.TRUST_WEIGHT_KIND, store.WHOLE)],
                                 cell[(components.SKILL_METRIC_KIND, store.WHOLE)],
                                 cell[(components.SKILL_METRIC_KIND, components.FAKE_BASELINE)]))
        on, off = (cell.get((components.SKILL_METRIC_KIND, key)) for key in (components.DIAL_ON, components.DIAL_OFF))
        if on is not None and on["metric_value"] is not None:
            lines.append(f"{'':<18} {'':<11} coverage with the dial {on['metric_value']:.3f}, without "
                         f"{off['metric_value']:.3f}")
    return lines


def ledger_line(name, horizon, weight, score, fake):
    """One printed row of the ledger."""
    blank = "-"
    interval = f"{score['metric_interval_low']:+.4f} to {score['metric_interval_high']:+.4f}" \
        if score["metric_interval_low"] is not None else blank
    value = f"{score['metric_value']:+.4f}" if score["metric_value"] is not None else blank
    fake_value = f"{fake['metric_value']:+.4f}" if fake["metric_value"] is not None else blank
    earned = f"{weight['metric_value']:.4f}" if weight["metric_value"] is not None else "0"
    return (f"{name:<18} {horizon:<11} {weight['sessions_count']:>8} {weight['rows_count']:>8} {value:>9} "
            f"{interval:>20} {fake_value:>9} {weight[components.VERDICT_COLUMN]:<16} {earned} "
            f"({weight['detail_text']})")
