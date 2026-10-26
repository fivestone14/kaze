# The promotion ledger: every push/pull reading scored nightly on live days only, on the average grade from publish
# time + 1 minute (the first a reader could act on), under a recipe frozen by its hash. Each day's score is the mean
# of strength × grade (in swings) over the day's live reads; the evidence runs as an e-value, the product over days of
# 1 + bet × clip(score ÷ spread, ±1), which under no skill never grows in expectation. 20-to-1 with enough days
# promotes a reading (its group leaves the young cap); the same evidence for the reversed sign reaching 10-to-1
# demotes it; both standings stick (the running peak counts) until the recipe changes. The live weight the earned
# blend uses is the slope of grade on strength over those days (a + c·tn for the Trend). A recipe change restarts the
# clock. Written as results_evaluations rows the reads look up at their cut. The e-value is a valid test under no
# skill only as far as a day's score is symmetric about 0 given the past; the clip at a level set from earlier days
# keeps it so under symmetry, which is the honest reading of "no skill" here.
import hashlib
import math

from engine import session_clock
from engine.push_pull import reading_specs, same_clock_baseline as baseline
from engine.store import components, store, store_queries

RUN_ID_PREFIX = "promotion_ledger"
HORIZON = components.HEADLINE_HORIZON
# A day score's spread floor in swings, so one quiet day cannot make the next look huge.
SPREAD_FLOOR = 0.02
MINIMUM_DAYS_FOR_FIT = 2


def weight_specs():
    """The specs grouped by the weight they are scored under: {weight name: [specs]}; logged readings are scored too,
    they just never join the net and never lift a group's cap."""
    groups = {}
    for spec in reading_specs.SPECS:
        groups.setdefault(spec.weight_name, []).append(spec)
    return groups


def recipe_hash(specs, hashes):
    """The frozen recipe of a weight: its reading's hash, or the hash of the pair's hashes."""
    parts = [hashes.get(spec.name, "") for spec in specs]
    return parts[0] if len(parts) == 1 else hashlib.sha256("|".join(sorted(parts)).encode()).hexdigest()


def claimed_sign(spec, trend_ness):
    """The way the reading claims to push per unit strength: the sign of its history weight (at today's trend-ness
    for a day-type reading); +1 when history is silent. Skill is grade moving that way."""
    if spec.weight_rule == reading_specs.DAY_TYPE:
        a, c = spec.day_type
        size = a + c * (trend_ness if trend_ness is not None else 0.5)
    else:
        size = spec.history_size
    return -1.0 if size < 0 else 1.0


def day_scores(sample, specs, first_day):
    """Per day (after first_day), the reads with their (summed) strength × booster, the grade in swings and the
    day's trend-ness; the score of a read is that sized strength × grade turned the way the reading claims to push.
    A day counts only when the reading spoke (some sized strength not 0), so a reading's live days, its e-value and
    its promotion minimum count the days it said something (the events, for an event reading)."""
    days = {}
    for read in sample:
        if first_day and read["trade_date"] <= first_day or not read["swing"]:
            continue
        strengths = [read["strengths"].get(spec.name) for spec in specs]
        if any(s is None for s in strengths):
            continue
        sized = sum(s * read.get("boosters", {}).get(spec.name, 1.0) for s, spec in zip(strengths, specs))
        grade = read["average_from_publish"] / read["swing"]
        days.setdefault(read["trade_date"], []).append((sized, grade, read["trend_ness"],
                                                        claimed_sign(specs[0], read["trend_ness"])))
    return {day: reads for day, reads in sorted(days.items()) if any(s for s, _, _, _ in reads)}


def e_values(scores):
    """The e-value for skill and for the reversed sign over the day scores, in day order, each as its running
    peak: once the evidence has reached a standing, a later slip does not undo it (the recipe clock does)."""
    e, reversed_e, seen = 1.0, 1.0, []
    peak, reversed_peak = 1.0, 1.0
    for score in scores:
        spread = max(baseline.MAD_TO_SPREAD * baseline.weighted_median([abs(s) for s in seen], [1.0] * len(seen)),
                     SPREAD_FLOOR) if seen else None
        if spread is not None:
            x = max(-1.0, min(1.0, score / spread))
            e *= 1 + components.E_VALUE_BET * x
            reversed_e *= 1 - components.E_VALUE_BET * x
            peak, reversed_peak = max(peak, e), max(reversed_peak, reversed_e)
        seen.append(score)
    return peak, reversed_peak


def slope(xs, ys):
    """The least-squares slope of ys on xs (with an intercept), or None when xs do not vary."""
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    var = sum((x - mean_x) ** 2 for x in xs)
    if var <= 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var


def day_type_fit(reads):
    """a and c in grade = s·(a + c·tn), by least squares on [s, s·tn]; None when the design is flat."""
    rows = [(s, s * tn, g) for s, g, tn, _ in reads if tn is not None]
    if len(rows) < 3:
        return None
    # normal equations for two coefficients, no intercept
    s11 = sum(a * a for a, _, _ in rows)
    s12 = sum(a * b for a, b, _ in rows)
    s22 = sum(b * b for _, b, _ in rows)
    t1 = sum(a * g for a, _, g in rows)
    t2 = sum(b * g for _, b, g in rows)
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12:
        return None
    return (s22 * t1 - s12 * t2) / det, (s11 * t2 - s12 * t1) / det


def verdict(e, reversed_e, days, spec):
    """promoted at 20-to-1 with enough days (events for an event reading), demoted when the reversed sign reaches
    10-to-1, else earning."""
    minimum = components.PROMOTE_MINIMUM_EVENTS if spec.counts == "events" else components.PROMOTE_MINIMUM_DAYS
    if reversed_e >= components.DEMOTE_AT:
        return components.DEMOTED
    if e >= components.PROMOTE_AT and days >= minimum:
        return components.PROMOTED
    return components.EARNING_WORD


def ledger_rows(conn, through_day, in_force_day, written_at):
    """Every weight's rows: the live fit, the day count, the e-values with the verdict word, under the recipe hash."""
    written_iso = session_clock.iso_utc(written_at)
    run = {"trade_date": in_force_day.isoformat(), "data_through_trade_date": through_day.isoformat(),
           "applies_from_at": session_clock.iso_utc(session_clock.session_open(in_force_day)),
           "known_at": written_iso, "written_at": written_iso}
    sample = store_queries.push_pull_live_sample(conn, HORIZON, through_day.isoformat())
    hashes = store_queries.newest_reading_recipes(conn)
    evaluation_id = f"{RUN_ID_PREFIX}|{through_day}|{components.PROMOTION_LEDGER[0]}@{components.PROMOTION_LEDGER[1]}"
    rows = []
    for weight_name, specs in weight_specs().items():
        recipe = recipe_hash(specs, hashes)
        days = day_scores(sample, specs, store_queries.ledger_clock_start(conn, weight_name, recipe))
        reads = [read for day_reads in days.values() for read in day_reads]
        scores = [sum(sign * s * g for s, g, _, sign in day_reads) / len(day_reads) for day_reads in days.values()]
        e, reversed_e = e_values(scores)
        counts = {"sessions_count": len(days), "independent_windows_count": len(days), "rows_count": len(reads),
                  "live_frozen_rows_count": len(reads)}
        judged = (components.PUSH_PULL_FACTOR_KIND, weight_name, components.PUSH_PULL_RECIPE_VERSION)
        word = verdict(e, reversed_e, len(days), specs[0]) if days else None
        add = lambda metric, value, **extra: rows.append(store.evaluation_row(
            evaluation_id, judged, components.PROMOTION_LEDGER, HORIZON, kind=components.SKILL_METRIC_KIND,
            metric_name=metric, value=value, baseline=components.NO_SKILL, counts=counts, run=run, detail=recipe,
            family_size=len(weight_specs()), **extra))
        fit = slope([s for s, _, _, _ in reads], [g for _, g, _, _ in reads]) if len(days) >= MINIMUM_DAYS_FOR_FIT else None
        add("live_weight_fit", fit)
        if specs[0].weight_rule == reading_specs.DAY_TYPE:
            pair = day_type_fit(reads) if len(days) >= MINIMUM_DAYS_FOR_FIT else None
            add("live_day_type_a_fit", pair[0] if pair else None)
            add("live_day_type_c_fit", pair[1] if pair else None)
        add("live_days_count", float(len(days)))
        add("promotion_e_value", e if days else None, verdict=word)
        add("reversed_e_value", reversed_e if days else None)
    return rows


def write_ledger(conn, through_day, in_force_day, written_at):
    """Score every reading and write the ledger for the next session; returns a phrase for the nightly's health line."""
    rows = ledger_rows(conn, through_day, in_force_day, written_at)
    written = store.write_evaluations(conn, rows)
    standings = [(row["evaluated_name"], row[components.VERDICT_COLUMN], row["metric_value"], row["sessions_count"])
                 for row in rows if row["metric_name"] == "promotion_e_value"]
    words = ", ".join(f"{name} {word or 'unscored'} (e {value:.1f}, {days} d)" if value is not None
                      else f"{name} unscored" for name, word, value, days in standings)
    return f"promotion ledger {written} rows written for {in_force_day}: {words or 'no readings'}"


def ledger_lines(conn):
    """The newest promotion ledger per reading weight: days, live fit, e-values, standing."""
    rows = [row for row in store_queries.newest_evaluations(conn)
            if row["judge_name"] == components.PROMOTION_LEDGER[0]]
    if not rows:
        return ["no promotion ledger yet: the nightly writes it after its first run"]
    cells = {}
    for row in rows:
        cells.setdefault(row["evaluated_name"], {})[row["metric_name"]] = row
    lines = [f"promotion ledger ({components.PROMOTION_LEDGER[0]}@{components.PROMOTION_LEDGER[1]}): promote at "
             f"e ≥ {components.PROMOTE_AT:.0f} with ≥ {components.PROMOTE_MINIMUM_DAYS} days, demote at reversed e ≥ "
             f"{components.DEMOTE_AT:.0f}; grade = average from publish + 1 min on {HORIZON}",
             "{:<24} {:>6} {:>10} {:>9} {:>11} {}".format("reading weight", "days", "live fit", "e-value", "reversed",
                                                        "standing")]
    for name, metrics in sorted(cells.items()):
        e, reversed_e = metrics.get("promotion_e_value"), metrics.get("reversed_e_value")
        fit, days = metrics.get("live_weight_fit"), metrics.get("live_days_count")
        number = lambda row: "-" if row is None or row["metric_value"] is None else f"{row['metric_value']:.3f}"
        lines.append(f"{name:<24} {int(days['metric_value']) if days and days['metric_value'] is not None else 0:>6} "
                     f"{number(fit):>10} {number(e):>9} {number(reversed_e):>11} "
                     f"{(e[components.VERDICT_COLUMN] if e else None) or 'unscored'}")
    return lines
