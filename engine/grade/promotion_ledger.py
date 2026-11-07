# The promotion ledger: every push/pull reading scored nightly on the days Kaze read live, on the average grade from
# publish time + 1 minute (the first a reader could act on), under the reading's recipe. Every read stamps each
# reading's recipe hash, so a day counts for a reading on its live reads when they carry the reading's current recipe,
# else on the day's rebuilt reads when they do (the day re-scored under the current code; the nightly rebuilds the
# days a recipe change left unscored), else not at all until it is re-scored: a change to one reading's own math
# re-scores its days instead of throwing them away, and a change elsewhere leaves them standing.
# Each day's score is the mean of strength × grade (in swings) over the day's reads; the evidence runs as an e-value,
# the product over days of 1 + bet × clip(score ÷ spread, ±1), which under no skill never grows in expectation.
# 140-to-1 with enough days promotes a reading (its group leaves the young cap); the same evidence for the reversed
# sign reaching 10-to-1 demotes it; both standings stick (the running peak counts) while the recipe stands. The e-value
# is a valid test under no skill only as far as a day's score is symmetric about 0 given the past; the clip at a level
# set from earlier days keeps it so under symmetry, which is the honest reading of "no skill" here.
# The live weight the earned blend uses is a least-squares fit of grade on strength (a + c·tn for the Trend) with its
# day-clustered standard error, the noise the blend discounts it by (overlapping reads share a day, so the day is the
# unit). Readings of one family (reading_specs: the price family) are fitted together, so each is credited only with
# what the others do not already explain. A reading the wider view may boost has its betas fitted on its own reads as
# strength × input terms, shrunk by their noise the same way, and divided by its own weight: the learned boosters.
# Written as results_evaluations rows the reads look up at their cut.
import hashlib
import math

import numpy as np

from engine import session_clock
from engine.push_pull import context_boosters as ctx, reading_specs, same_clock_baseline as baseline, strength_shapes
from engine.push_pull.earned_weights import signal_share
from engine.store import components, registry, store, store_queries

RUN_ID_PREFIX = "promotion_ledger"
HORIZON = components.HEADLINE_HORIZON
# A day score's spread floor in swings, so one quiet day cannot make the next look huge.
SPREAD_FLOOR = 0.02
MINIMUM_DAYS_FOR_FIT = 2
# A fit's noise is measured from five days on; before that its live days count for nothing (earned_weights).
MINIMUM_DAYS_FOR_NOISE = 5
# A learned beta is held to ±1 (the booster itself is held to ×0.5–×2), and is learned only while the reading's own
# weight is at least half signal: a booster scales the weight, so it means nothing on a weight that is mostly noise.
BETA_LIMIT, BETA_MINIMUM_SIGNAL = 1.0, 0.5
ORIGIN_ORDER = ("live_frozen", "history_rebuilt")


def weight_specs():
    """The specs grouped by the weight they are scored under: {weight name: [specs]}; logged readings are scored too,
    they just never join the net and never lift a group's cap."""
    groups = {}
    for spec in reading_specs.SPECS:
        groups.setdefault(spec.weight_name, []).append(spec)
    return groups


def recipe_hash(specs):
    """The current recipe of a weight: its reading's hash, or the hash of the pair's hashes."""
    hashes = reading_specs.recipe_hashes()
    parts = [hashes[spec.name] for spec in specs]
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


def current_on(reads, specs):
    """True when every read carries the current recipe hash of every spec."""
    hashes = reading_specs.recipe_hashes()
    return bool(reads) and all(read["recipes"].get(spec.name) == hashes[spec.name] for read in reads for spec in specs)


def reads_by_day(sample, specs):
    """Per day, the reads the specs are scored on: the live reads when they carry the specs' current recipes, else the
    day's rebuilt reads when they do; a day with neither is left out until it is re-scored. {day: (origin, reads)}."""
    days = {}
    for read in sample:
        days.setdefault(read["trade_date"], {}).setdefault(read["origin"], []).append(read)
    chosen = {}
    for day, origins in sorted(days.items()):
        origin = next((origin for origin in ORIGIN_ORDER if current_on(origins.get(origin, []), specs)), None)
        if origin is not None:
            chosen[day] = (origin, [read for read in origins[origin] if read["swing"]])
    return chosen


def days_to_rescore(conn, through_day):
    """The days read live whose reads, live or rebuilt, do not carry some reading's current recipe, oldest first (so
    each re-scored day's baseline finds the days before it already under the current recipe): the days the nightly
    rebuilds so every reading is scored on all of them."""
    hashes = reading_specs.recipe_hashes()
    stamps = store_queries.recipe_stamps_by_day(conn, through_day.isoformat())
    return sorted((day for day, origins in stamps.items()
                   if any(not any(origins.get(origin, {}).get(name) == {current} for origin in ORIGIN_ORDER)
                          for name, current in hashes.items())))


def sized(read, spec, boosted):
    """A read's strength for the spec times its booster: the stored booster when boosted (the score: the reading as
    the read used it) or for a fixed clock booster; else the bare strength (the fits learn the boosters' terms)."""
    strength = read["strengths"].get(spec.name)
    if strength is None:
        return None
    return strength * (read["boosters"].get(spec.name, 1.0) if boosted or spec.fixed_booster else 1.0)


def day_scores(chosen, specs):
    """Per day, the reads with their (summed) strength × booster, the grade in swings and the day's trend-ness; the
    score of a read is that sized strength × grade turned the way the reading claims to push. A day counts only when
    the reading spoke (some sized strength not 0), so a reading's live days, its e-value and its promotion minimum
    count the days it said something (the events, for an event reading)."""
    days = {}
    for day, (origin, reads) in chosen.items():
        for read in reads:
            strengths = [sized(read, spec, True) for spec in specs]
            if any(s is None for s in strengths):
                continue
            days.setdefault(day, (origin, []))[1].append(
                (sum(strengths), read["average_from_publish"] / read["swing"], read["trend_ness"],
                 claimed_sign(specs[0], read["trend_ness"])))
    return {day: entry for day, entry in days.items() if any(s for s, _, _, _ in entry[1])}


def e_values(scores):
    """The e-value for skill and for the reversed sign over the day scores, in day order, each as its running
    peak: once the evidence has reached a standing, a later slip does not undo it (the recipe does)."""
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


def clustered_fit(columns, grades, days):
    """Least squares of the grades on the columns plus an intercept, with the coefficients' day-clustered covariance
    (the sandwich over day sums × G ÷ (G − 1)): ({column: coefficient}, {column: noise}); a column that never moves
    gets None for both, and the noise is None under MINIMUM_DAYS_FOR_NOISE days."""
    names = list(columns)
    kept = [name for name in names if np.ptp(columns[name]) > 0] if len(grades) else []
    empty = ({name: None for name in names}, {name: None for name in names})
    if not kept or len(grades) <= len(kept) + 1:
        return empty
    design = np.column_stack([np.ones(len(grades)), *[columns[name] for name in kept]])
    y = np.asarray(grades)
    # numpy 2.0 on macOS's Accelerate warns falsely inside matmul; a result that is not finite is refused below.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        gram = design.T @ design
        if not np.all(np.isfinite(gram)) or np.linalg.cond(gram) > 1e12:
            return empty
        inverse = np.linalg.inv(gram)
        coefficients = inverse @ design.T @ y
        residuals = y - design @ coefficients
        groups = sorted(set(days))
        covariance = None
        if len(groups) >= MINIMUM_DAYS_FOR_NOISE:
            day_index = np.array([groups.index(day) for day in days])
            sums = np.array([design[day_index == g].T @ residuals[day_index == g] for g in range(len(groups))])
            covariance = inverse @ (sums.T @ sums) @ inverse * len(groups) / (len(groups) - 1)
    if not np.all(np.isfinite(coefficients)) or (covariance is not None and not np.all(np.isfinite(covariance))):
        return empty
    fits, noise = {name: None for name in names}, {name: None for name in names}
    fits.update({name: float(coefficients[i + 1]) for i, name in enumerate(kept)})
    if covariance is not None:
        noise.update({name: float(math.sqrt(max(covariance[i + 1, i + 1], 0.0))) for i, name in enumerate(kept)})
    return fits, noise


def weight_columns(read, weight_name, specs):
    """A weight's columns at one read: its summed bare strength (0 when the reading did not speak), and for the Trend
    that times trend-ness too; None when the read has no trend-ness for the Trend. A catch-up reading's points are
    its strength (in points) × weight with no swing, so its strength is put in swings here, like the grade, and its
    slope comes out in points per point, the weight its points use."""
    strength = sum((sized(read, spec, False) or 0.0)
                   / (read["swing"] if spec.shape == strength_shapes.CATCH_UP else 1.0) for spec in specs)
    columns = {weight_name: strength}
    if specs[0].weight_rule == reading_specs.DAY_TYPE:
        if read["trend_ness"] is None:
            return None
        columns[weight_name + "|tn"] = strength * read["trend_ness"]
    return columns


def family_fits(sample, groups):
    """The joint fit of each family's in-the-net weights on the reads of days all of them are scored on (the first
    origin current for all): {weight name: (fits, noise)} over each weight's columns."""
    families = {}
    for name, specs in groups.items():
        if specs[0].family and all(spec.in_the_net for spec in specs):
            families.setdefault(specs[0].family, []).append(name)
    results = {}
    for members in families.values():
        member_specs = [spec for name in members for spec in groups[name]]
        columns, grades, days = {}, [], []
        for day, (_, reads) in reads_by_day(sample, member_specs).items():
            for read in reads:
                row = {}
                for name in members:
                    cells = weight_columns(read, name, groups[name])
                    if cells is None:
                        break
                    row.update(cells)
                else:
                    for key, value in row.items():
                        columns.setdefault(key, []).append(value)
                    grades.append(read["average_from_publish"] / read["swing"])
                    days.append(day)
        if len(set(days)) < MINIMUM_DAYS_FOR_FIT:
            continue
        fits, noise = clustered_fit({key: np.asarray(values) for key, values in columns.items()}, grades, days)
        for name in members:
            results[name] = (fits, noise)
    return results


def own_fit(chosen, weight_name, specs):
    """A weight fitted on its own scored reads, with a strength × input term for each booster its spec names:
    (fits, noise) keyed by column; None under MINIMUM_DAYS_FOR_FIT days."""
    inputs = sorted(specs[0].boosters) if len(specs) == 1 else []
    columns, grades, days = {}, [], []
    for day, (_, reads) in chosen.items():
        for read in reads:
            cells = weight_columns(read, weight_name, specs)
            if cells is None or any(read["strengths"].get(spec.name) is None for spec in specs):
                continue
            normalised = ctx.normalised_inputs(read["context"])
            for name in inputs:
                cells[f"{weight_name}|{name}"] = cells[weight_name] * normalised[name]
            for key, value in cells.items():
                columns.setdefault(key, []).append(value)
            grades.append(read["average_from_publish"] / read["swing"])
            days.append(day)
    if len(set(days)) < MINIMUM_DAYS_FOR_FIT:
        return None
    return clustered_fit({key: np.asarray(values) for key, values in columns.items()}, grades, days)


def learned_betas(fit, weight_name, spec):
    """Each booster input's beta: its strength × input term shrunk by its noise, over the reading's own weight, held
    to ±1; None while that weight is under half signal or has no noise measured yet. The Trend learns none: its weight
    a + c·tn changes sign with trend-ness, so one multiplier cannot carry a term fitted at the sample's mean."""
    if fit is None or not spec.boosters:
        return {}
    if spec.weight_rule == reading_specs.DAY_TYPE:
        return {name: None for name in sorted(spec.boosters)}
    fits, noise = fit
    weight = fits.get(weight_name)
    usable = weight is not None and signal_share(weight, noise.get(weight_name)) >= BETA_MINIMUM_SIGNAL
    betas = {}
    for name in sorted(spec.boosters):
        term = fits.get(f"{weight_name}|{name}")
        shrunk = term * signal_share(term, noise.get(f"{weight_name}|{name}")) if usable and term is not None else None
        betas[name] = max(-BETA_LIMIT, min(BETA_LIMIT, shrunk / weight)) if shrunk is not None else None
    return betas


def ledger_rows(conn, through_day, in_force_day, written_at):
    """Every weight's rows: the live fit and its noise, the learned betas, the day counts, the e-values with the
    verdict word, under the recipe hash."""
    written_iso = session_clock.iso_utc(written_at)
    run = {"trade_date": in_force_day.isoformat(), "data_through_trade_date": through_day.isoformat(),
           "applies_from_at": session_clock.iso_utc(session_clock.session_open(in_force_day)),
           "known_at": written_iso, "written_at": written_iso}
    sample = store_queries.push_pull_sample(conn, HORIZON, through_day.isoformat())
    groups = weight_specs()
    joint = family_fits(sample, groups)
    evaluation_id = f"{RUN_ID_PREFIX}|{through_day}|{components.PROMOTION_LEDGER[0]}@{components.PROMOTION_LEDGER[1]}"
    rows = []
    for weight_name, specs in groups.items():
        chosen = reads_by_day(sample, specs)
        days = day_scores(chosen, specs)
        reads = [read for _, day_reads in days.values() for read in day_reads]
        scores = [sum(sign * s * g for s, g, _, sign in day_reads) / len(day_reads) for _, day_reads in days.values()]
        e, reversed_e = e_values(scores)
        live_rows = sum(len(day_reads) for origin, day_reads in days.values() if origin == "live_frozen")
        counts = {"sessions_count": len(days), "independent_windows_count": len(days), "rows_count": len(reads),
                  "live_frozen_rows_count": live_rows}
        judged = (components.PUSH_PULL_FACTOR_KIND, weight_name, components.PUSH_PULL_RECIPE_VERSION)
        word = verdict(e, reversed_e, len(days), specs[0]) if days else None
        recipe = recipe_hash(specs)
        add = lambda metric, value, **extra: rows.append(store.evaluation_row(
            evaluation_id, judged, components.PROMOTION_LEDGER, HORIZON, kind=components.SKILL_METRIC_KIND,
            metric_name=metric, value=value, baseline=components.NO_SKILL, counts=counts, run=run, detail=recipe,
            family_size=len(groups), **extra))
        own = own_fit({day: chosen[day] for day in days}, weight_name, specs) if len(days) >= MINIMUM_DAYS_FOR_FIT \
            else None
        joint_fit = joint.get(weight_name)
        # A family fit with no coefficient for this weight (too few reads, or a column that never moved) falls back
        # to the weight's own fit instead of leaving it blank.
        if joint_fit is not None and joint_fit[0].get(weight_name) is None:
            joint_fit = None
        fits, noise = joint_fit or own or ({}, {})
        if specs[0].weight_rule == reading_specs.DAY_TYPE:
            a, c = fits.get(weight_name), fits.get(weight_name + "|tn")
            add("live_weight_fit", a + c * 0.5 if a is not None and c is not None else None)
            add("live_day_type_a_fit", a)
            add("live_day_type_c_fit", c)
            add("live_day_type_a_noise", noise.get(weight_name))
            add("live_day_type_c_noise", noise.get(weight_name + "|tn"))
        else:
            add("live_weight_fit", fits.get(weight_name))
            add("live_weight_noise", noise.get(weight_name))
        for name, beta in learned_betas(own, weight_name, specs[0]).items():
            add("live_booster_beta", beta, slice_key=registry.subject_key(kind=name))
        add("live_days_count", float(len(days)))
        add("rescored_days_count", float(sum(1 for origin, _ in days.values() if origin != "live_frozen")))
        add("promotion_e_value", e if days else None, verdict=word)
        add("reversed_e_value", reversed_e if days else None)
    return rows


def verdict(e, reversed_e, days, spec):
    """promoted at 140-to-1 with enough days (events for an event reading), demoted when the reversed sign reaches
    10-to-1, else earning."""
    minimum = components.PROMOTE_MINIMUM_EVENTS if spec.counts == "events" else components.PROMOTE_MINIMUM_DAYS
    if reversed_e >= components.DEMOTE_AT:
        return components.DEMOTED
    if e >= components.PROMOTE_AT and days >= minimum:
        return components.PROMOTED
    return components.EARNING_WORD


def write_ledger(conn, through_day, in_force_day, written_at):
    """Score every reading and write the ledger for the next session; returns a phrase for the nightly's health line."""
    rows = ledger_rows(conn, through_day, in_force_day, written_at)
    written = store.write_evaluations(conn, rows)
    by_name = {}
    for row in rows:
        by_name.setdefault(row["evaluated_name"], {})[row["metric_name"]] = row
    words = []
    for name, metrics in by_name.items():
        e, rescored = metrics["promotion_e_value"], metrics["rescored_days_count"]["metric_value"]
        words.append(f"{name} {e[components.VERDICT_COLUMN]} (e {e['metric_value']:.1f}, {e['sessions_count']} d, "
                     f"{rescored:.0f} re-scored)" if e["metric_value"] is not None else f"{name} unscored")
    return f"promotion ledger {written} rows written for {in_force_day}: {', '.join(words) or 'no readings'}"


def ledger_lines(conn):
    """The newest promotion ledger per reading weight: days, live fit and its noise, e-values, standing."""
    rows = [row for row in store_queries.newest_evaluations(conn)
            if row["judge_name"] == components.PROMOTION_LEDGER[0]]
    if not rows:
        return ["no promotion ledger yet: the nightly writes it after its first run"]
    cells = {}
    for row in rows:
        if row["slice_key"] == registry.subject_key():
            cells.setdefault(row["evaluated_name"], {})[row["metric_name"]] = row
    lines = [f"promotion ledger ({components.PROMOTION_LEDGER[0]}@{components.PROMOTION_LEDGER[1]}): promote at "
             f"e ≥ {components.PROMOTE_AT:.0f} with ≥ {components.PROMOTE_MINIMUM_DAYS} days, demote at reversed e ≥ "
             f"{components.DEMOTE_AT:.0f}; grade = average from publish + 1 min on {HORIZON}",
             "{:<24} {:>6} {:>9} {:>10} {:>9} {:>9} {:>11} {}".format(
                 "reading weight", "days", "re-scored", "live fit", "noise", "e-value", "reversed", "standing")]
    for name, metrics in sorted(cells.items()):
        e, reversed_e = metrics.get("promotion_e_value"), metrics.get("reversed_e_value")
        fit, days = metrics.get("live_weight_fit"), metrics.get("live_days_count")
        noise = metrics.get("live_weight_noise") or metrics.get("live_day_type_a_noise")
        rescored = metrics.get("rescored_days_count")
        number = lambda row: "-" if row is None or row["metric_value"] is None else f"{row['metric_value']:.3f}"
        whole = lambda row: int(row["metric_value"]) if row and row["metric_value"] is not None else 0
        lines.append(f"{name:<24} {whole(days):>6} {whole(rescored):>9} {number(fit):>10} {number(noise):>9} "
                     f"{number(e):>9} {number(reversed_e):>11} "
                     f"{(e[components.VERDICT_COLUMN] if e else None) or 'unscored'}")
    return lines
