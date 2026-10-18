# The trust ledger: every push reading and width dial scored per channel, nightly, on live graded windows only
# (replayed history never counts) with a test fixed in advance in the registry, and the sample, score, interval,
# verdict and weight written as results_evaluations rows; the read job applies only weights the ledger holds.
from collections import defaultdict

import numpy as np

from engine import range_fit, range_read, registry, session_clock, store

LEDGER = "trust_ledger"
EVEN_BRIER = (1 - registry.DIRECTION_UNTIL_EARNED) ** 2
PRINT_COLUMNS = ("reading or dial", "channel", "sessions", "windows", "score", "95% interval", "verdict", "weight")


# ---- The sample: live windows with what each decision stored ------------------------------------------------------

def scored_windows(conn, horizon_name):
    """The channel's live, first-built base decisions whose window counts, oldest first, each with its day, its
    window's end move, its undialed half-width (swing x stretch), cap, every dial's raw value and every lean."""
    values = defaultdict(dict)
    for row in store.live_scored_decision_values(conn, range_read.DECISION_KIND, horizon_name, registry.GRADE_RULE):
        key = (row["measure_name"], row["subject_key"], row["value_role"])
        values[row["decision_rowid"]][key] = row["value_number"]
    horizon = registry.subject_key(horizon=horizon_name)
    band = registry.subject_key(band=registry.BAND, horizon=horizon_name)
    windows = []
    for decision in store.live_scored_decisions(conn, range_read.DECISION_KIND, horizon_name, registry.GRADE_RULE):
        stored = values[decision["decision_rowid"]]
        swing = stored.get(("typical_swing_points", horizon, registry.VALUE_ROLE))
        stretch = stored.get(("band_multiplier", band, registry.VALUE_ROLE))
        if swing is None or stretch is None:
            continue
        windows.append({
            "day": decision["trade_date"], "origin": decision["row_origin"], "move": decision["end_move"],
            "base": swing * stretch, "cap": stored.get(("width_cap_multiplier", horizon, registry.VALUE_ROLE)),
            "raw": {dial: stored.get(("width_multiplier", registry.subject_key(adjustment=dial), registry.RAW_ROLE))
                    for dial in registry.WIDTH_DIALS},
            "leans": {reading: stored.get(("push_lean_probability", registry.subject_key(reading=reading),
                                           registry.VALUE_ROLE)) for reading in registry.PUSH_READINGS},
        })
    return windows


def day_means(scores_by_day):
    """Each day's mean score, in day order."""
    return [float(np.mean(scores)) for day, scores in sorted(scores_by_day.items())]


def sample_counts(windows, horizon_name):
    """The sample's sessions, independent (non-overlapping) windows, rows and first-built live rows."""
    minutes = registry.HORIZON_MINUTES[horizon_name]
    return {"sessions_count": len({window["day"] for window in windows}),
            "independent_windows_count": len(windows) * session_clock.SLOT_MINUTES // minutes,
            "rows_count": len(windows),
            "live_frozen_rows_count": sum(1 for window in windows if window["origin"] == "live_frozen"),
            "data_from_trade_date": min((window["day"] for window in windows), default=None)}


# ---- The two tests -------------------------------------------------------------------------------------------------

def lean_verdict(windows, reading, horizon_name):
    """The lean test on one reading and channel: Brier skill over even odds on the moved windows with a lean, its
    day-resampled interval, the verdict and the weight (the interval's low end, floored at 0)."""
    threshold = registry.LEAN_MOVED_RUNG_SHARE * registry.RUNG_POINTS[horizon_name]
    scored = [window for window in windows
              if abs(window["move"]) > threshold and window["leans"][reading] is not None]
    skills = defaultdict(list)
    for window in scored:
        up = 1.0 if window["move"] > 0 else 0.0
        skills[window["day"]].append(1 - (window["leans"][reading] - up) ** 2 / EVEN_BRIER)
    return verdict_of(scored, horizon_name, day_means(skills), lambda low: max(0.0, low))


def dial_verdict(windows, dial, earned, horizon_name):
    """The dial test on one dial and channel, on top of the dials earned before it: the band's interval score with
    the dial's raw against without, over the windows the dial touches; coverage on and off comes back beside it."""
    scored = [window for window in windows if window["raw"][dial] not in (None, registry.NO_EFFECT)]
    gains, inside = defaultdict(list), {registry.DIAL_ON: [], registry.DIAL_OFF: []}
    for window in scored:
        stack = float(np.prod([window["raw"][other] for other in earned if window["raw"][other] is not None]))
        off = window["base"] * range_read.capped(stack, window["cap"])
        on = window["base"] * range_read.capped(stack * window["raw"][dial], window["cap"])
        gains[window["day"]].append(interval_score(off, window["move"]) - interval_score(on, window["move"]))
        inside[registry.DIAL_ON].append(abs(window["move"]) <= on)
        inside[registry.DIAL_OFF].append(abs(window["move"]) <= off)
    verdict = verdict_of(scored, horizon_name, day_means(gains), lambda low: 1.0 if low > 0 else 0.0)
    verdict["coverage"] = {key: float(np.mean(flags)) if flags else None for key, flags in inside.items()}
    return verdict


def interval_score(half_width, move):
    """The proper score of a central band: its full width plus the penalty for a move landing outside it."""
    return 2 * half_width + 2 / registry.INTERVAL_SCORE_ALPHA * max(0.0, abs(move) - half_width)


def verdict_of(scored, horizon_name, means, weight_rule):
    """A test's result: the sample counts, then, with enough sessions, the mean score, its day-resampled interval, the
    verdict and the weight the rule gives the interval's low end; short of sessions, the too-few verdict."""
    result = {"counts": sample_counts(scored, horizon_name), "score": None, "interval": None, "weight": None,
              "verdict": registry.TOO_FEW_SESSIONS}
    if len(means) < registry.TRUST_MINIMUM_SESSIONS:
        return result
    mean, low, high = range_fit.bootstrap_interval(means)
    result.update({"score": mean, "interval": (low, high), "weight": weight_rule(low),
                   "verdict": registry.PASSED if low > 0 else registry.FAILED_VERDICT})
    return result


# ---- Writing the ledger (nightly) ------------------------------------------------------------------------------------

def ledger_rows(conn, through_day, in_force_day, written_at):
    """Every reading's and dial's rows per channel: the weight row and its skill rows, from the live windows."""
    written_iso = session_clock.iso_utc(written_at)
    shared = {"trade_date": in_force_day.isoformat(), "data_through_trade_date": through_day.isoformat(),
              "applies_from_at": session_clock.iso_utc(session_clock.session_open(in_force_day)),
              "known_at": written_iso, "written_at": written_iso}
    rows = []
    for horizon_name in registry.HORIZON_MINUTES:
        windows = scored_windows(conn, horizon_name)
        for reading in registry.PUSH_READINGS:
            result = lean_verdict(windows, reading, horizon_name)
            evaluated = (registry.PUSH_READING_KIND, reading, "1")
            rows.extend(result_rows(through_day, registry.LEAN_TEST, evaluated, horizon_name, result,
                                    "brier_skill_portion", registry.EVEN_ODDS, 0.0, shared))
        earned = []
        for dial in registry.WIDTH_DIALS:
            result = dial_verdict(windows, dial, earned, horizon_name)
            evaluated = (registry.WIDTH_DIAL_KIND, dial, "1")
            detail = f"scored on top of: {', '.join(earned) or 'no earned dial'}"
            rows.extend(result_rows(through_day, registry.DIAL_TEST, evaluated, horizon_name, result,
                                    "interval_score_gain_points", registry.UNDIALED_BAND, 0.0, shared, detail))
            evaluation_id = f"{LEDGER}|{through_day}|{registry.DIAL_TEST[0]}@{registry.DIAL_TEST[1]}"
            for slice_key, coverage in result["coverage"].items():
                rows.append(store.evaluation_row(
                    evaluation_id, registry.SKILL_METRIC_KIND, evaluated, registry.DIAL_TEST, horizon_name,
                    "band_coverage_portion", coverage if result["weight"] is not None else None,
                    blank_reason=registry.NO_FIT_REASON, baseline=registry.UNDIALED_BAND, slice_key=slice_key,
                    **result["counts"], **shared))
            if result["weight"]:
                earned.append(dial)
    return rows


def result_rows(through_day, judge, evaluated, horizon_name, result, score_measure, baseline, baseline_value, shared,
                detail=None):
    """A test result as its two ledger rows: the trust weight and the score with its interval."""
    evaluation_id = f"{LEDGER}|{through_day}|{judge[0]}@{judge[1]}"
    too_few = result["weight"] is None
    note = f"{registry.NOT_ENOUGH_LIVE_SESSIONS}: {result['counts']['sessions_count']} of " \
           f"{registry.TRUST_MINIMUM_SESSIONS}" if too_few else detail
    common = dict(horizon_name=horizon_name, verdict=result["verdict"], blank_reason=registry.NO_FIT_REASON,
                  baseline=baseline, detail=note, **result["counts"], **shared)
    return [store.evaluation_row(evaluation_id, registry.TRUST_WEIGHT_KIND, evaluated, judge,
                                 metric_name="trust_weight_portion", value=result["weight"], **common),
            store.evaluation_row(evaluation_id, registry.SKILL_METRIC_KIND, evaluated, judge,
                                 metric_name=score_measure, value=result["score"], interval=result["interval"],
                                 baseline_value=baseline_value if not too_few else None, **common)]


def write_ledger(conn, through_day, in_force_day, written_at):
    """Score everything and write the ledger for the next session; returns a phrase for the nightly's health line."""
    rows = ledger_rows(conn, through_day, in_force_day, written_at)
    written = store.write_evaluations(conn, rows)
    weights = [row for row in rows if row["evaluation_kind"] == registry.TRUST_WEIGHT_KIND]
    earned = [f"{row['evaluated_name']} {row['horizon_name']} {row['metric_value']}" for row in weights
              if row["metric_value"]]
    short = sum(1 for row in weights if row["metric_text"] == registry.TOO_FEW_SESSIONS)
    return (f"trust ledger {written} rows written for {in_force_day}: {len(weights)} weights, {short} with "
            f"{registry.NOT_ENOUGH_LIVE_SESSIONS}, earned {', '.join(earned) or 'none'}")


# ---- Printing the ledger ---------------------------------------------------------------------------------------------

def ledger_lines(conn):
    """The newest ledger per reading, dial and channel: sample, score, interval, verdict, weight."""
    rows = store.newest_evaluations(conn)
    if not rows:
        return ["no trust ledger yet: the nightly writes it after the first live session"]
    by_component = defaultdict(dict)
    for row in rows:
        by_component[(row["evaluated_kind"], row["evaluated_name"], row["horizon_name"])][
            (row["evaluation_kind"], row["slice_key"])] = row
    order = {name: place for place, name in enumerate(registry.PUSH_READINGS + registry.WIDTH_DIALS)}
    newest = max(row["trade_date"] for row in rows)
    through = max(row["data_through_trade_date"] for row in rows)
    lines = [f"trust ledger ({registry.LEAN_TEST[0]}@{registry.LEAN_TEST[1]} on leans, {registry.DIAL_TEST[0]}@"
             f"{registry.DIAL_TEST[1]} on dials): weights in force for {newest}, live data through {through}",
             f"minimum {registry.TRUST_MINIMUM_SESSIONS} live sessions; a weight above 0 needs the interval above 0",
             "{:<18} {:<11} {:>8} {:>8} {:>9} {:>20} {:<16} {}".format(*PRINT_COLUMNS)]
    for (kind, name, horizon), cells in sorted(by_component.items(), key=lambda item: (order[item[0][1]],
                                                                               registry.HORIZON_MINUTES[item[0][2]])):
        weight = cells[(registry.TRUST_WEIGHT_KIND, store.WHOLE)]
        score = cells[(registry.SKILL_METRIC_KIND, store.WHOLE)]
        lines.append(ledger_line(name, horizon, weight, score))
        if kind == registry.WIDTH_DIAL_KIND and score["metric_value"] is not None:
            on, off = (cells[(registry.SKILL_METRIC_KIND, key)]["metric_value"] for key in (registry.DIAL_ON,
                                                                                              registry.DIAL_OFF))
            lines.append(f"{'':<18} {'':<11} coverage with the dial {on:.3f}, without {off:.3f}")
    return lines


def ledger_line(name, horizon, weight, score):
    """One printed row of the ledger."""
    blank = "-"
    interval = f"{score['metric_interval_low']:+.4f} to {score['metric_interval_high']:+.4f}" \
        if score["metric_interval_low"] is not None else blank
    value = f"{score['metric_value']:+.4f}" if score["metric_value"] is not None else blank
    earned = f"{weight['metric_value']:.4f}" if weight["metric_value"] is not None else f"0 ({weight['detail_text']})"
    return (f"{name:<18} {horizon:<11} {weight['sessions_count']:>8} {weight['rows_count']:>8} {value:>9} "
            f"{interval:>20} {weight['metric_text']:<16} {earned}")
