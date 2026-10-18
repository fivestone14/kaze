# The range engine's history side: the speed recipe, the windows of past sessions measured on that same recipe, the
# nightly fit of the band multipliers (one nightly_history_fit cut per session served) and the Phase 2 pass test.
import math
from datetime import date, timedelta

import numpy as np

from engine import collect, registry, session_clock, store

WINDOW_MEASURES = ("end", "average", "farthest")
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 1
PASS_INTERVAL_PORTIONS = (2.5, 97.5)
PASS_BAND_BOUNDS = (0.75, 0.85)
INFORMATION_LOOKBACK_SESSIONS = 20
WINDOWS_CACHE = {}


def speed(closes, half_life_minutes=registry.SPEED_HALF_LIFE_MINUTES):
    """Recency-weighted mean absolute 1-minute close change: the newest change weighs 1, halving every half-life."""
    changes = np.abs(np.diff(np.asarray(closes, dtype=float)))
    if changes.size == 0:
        return None
    weights = 0.5 ** (np.arange(changes.size - 1, -1, -1) / half_life_minutes)
    return float((changes * weights).sum() / weights.sum())


def swing_points(pace, minutes):
    """The typical move over a horizon: speed times the square root of its minutes."""
    return pace * math.sqrt(minutes)


def session_columns(bars):
    """A session's closes, highs and lows as arrays."""
    return tuple(np.array([bar[column] for bar in bars], dtype=float) for column in ("close", "high", "low"))


def history_windows(conn, before_day, lookback=registry.FIT_LOOKBACK_SESSIONS):
    """Past windows before a day on the recipe, cached: per horizon the sorted end, average and farthest moves."""
    key = (before_day, lookback)
    if key not in WINDOWS_CACHE:
        WINDOWS_CACHE[key] = build_history_windows(conn, before_day, lookback)
    return WINDOWS_CACHE[key]


def build_history_windows(conn, before_day, lookback):
    """history_windows without the cache: every window of every full prior session, measured in swings."""
    sessions = [day for day in store.sessions_with_bars(conn) if day < before_day.isoformat()][-lookback:]
    table = {name: {measure: [] for measure in WINDOW_MEASURES} for name in registry.HORIZON_MINUTES}
    used, last_bar_at = [], None
    longest = max(registry.HORIZON_MINUTES.values())
    for day in sessions:
        bars = store.session_bars(conn, day)
        if len(bars) < registry.FIT_MIN_BARS_PER_SESSION:
            continue
        used.append(day)
        last_bar_at = bars[-1]["priced_at"]
        closes, highs, lows = session_columns(bars)
        for start in range(registry.FIT_FIRST_WINDOW_MINUTE, len(bars) - longest - 1, registry.FIT_WINDOW_STEP_MINUTES):
            pace = speed(closes[:start + 1])
            if not pace:
                continue
            for name, minutes in registry.HORIZON_MINUTES.items():
                swing = swing_points(pace, minutes)
                ahead = slice(start + 1, start + 1 + minutes)
                table[name]["end"].append(abs(closes[start + minutes] - closes[start]) / swing)
                table[name]["average"].append(abs(closes[ahead].mean() - closes[start]) / swing)
                table[name]["farthest"].append(max(highs[ahead].max() - closes[start],
                                                   closes[start] - lows[ahead].min()) / swing)
    sorted_table = {name: {measure: np.sort(np.array(values)) for measure, values in measures.items()}
                    for name, measures in table.items()}
    return {"sessions": used, "last_bar_at": last_bar_at, "windows": sorted_table}


def band_multiplier(sorted_ratios):
    """The swings that cover the band's share of past windows: the 80% mark of the sorted ratios, or None if empty."""
    if sorted_ratios.size == 0:
        return None
    return float(sorted_ratios[int(registry.BAND_PORTION * sorted_ratios.size)])


def fit_multipliers(history):
    """Per horizon: the end and average band multipliers, the shrink ratio and the window count from the history."""
    fits = {}
    for name, measures in history["windows"].items():
        end, average = band_multiplier(measures["end"]), band_multiplier(measures["average"])
        fits[name] = {registry.END_BAND: end, registry.AVERAGE_BAND: average,
                      "shrink": average / end if end else None, "windows": int(measures["end"].size)}
    return fits


def never_leaves_share(history, horizon_name, rung_points, swing):
    """Share of the history's windows whose farthest excursion stayed inside the rung at today's swing."""
    farthest = history["windows"][horizon_name]["farthest"]
    if farthest.size == 0 or not swing:
        return None
    return float(np.searchsorted(farthest, rung_points / swing, side="left") / farthest.size)


def fit_rows(conn, served_day, row_origin, written_at):
    """The nightly_history_fit snapshot serving a session and its observations, or None when no prior session has
    a full day of bars; a rebuilt fit is known when its last bar landed, a live one when it was written."""
    history = history_windows(conn, served_day)
    if not history["sessions"]:
        return None
    last_bar_ends_at = session_clock.parse_iso(history["last_bar_at"]) + timedelta(seconds=collect.BAR_LENGTH_SECONDS)
    commit, dirty = collect.git_head()
    snapshot = store.base_snapshot_row("nightly_history_fit", session_clock.iso_utc(last_bar_ends_at),
                                       served_day.isoformat(), row_origin, session_clock.iso_utc(written_at), commit,
                                       dirty, builder=(registry.RANGE_ENGINE_NAME, registry.RANGE_ENGINE_VERSION))
    if row_origin == "history_rebuilt":
        landed = last_bar_ends_at + timedelta(seconds=registry.BAR_ARRIVAL_SECONDS)
        snapshot.update({"known_at": session_clock.iso_utc(landed), "known_at_rule": registry.BAR_ARRIVAL_RULE})
    observations = []
    for name, fit in fit_multipliers(history).items():
        horizon = {"horizon": name}
        for band in (registry.END_BAND, registry.AVERAGE_BAND):
            subject = registry.subject_key(band=band, **horizon)
            observations.append(store.observation_row(snapshot, "band_multiplier", store.rounded(fit[band]), subject,
                                                      blank_reason="too_few_sessions"))
            observations.append(store.observation_row(snapshot, "stretch_recipe_name", registry.STRETCH_RECIPE[band],
                                                      subject))
        subject = registry.subject_key(**horizon)
        observations.append(store.observation_row(snapshot, "average_price_shrink_ratio", store.rounded(fit["shrink"]),
                                                  subject, blank_reason="too_few_sessions"))
        observations.append(store.observation_row(snapshot, "fit_sessions_count", len(history["sessions"]), subject))
        observations.append(store.observation_row(snapshot, "fit_windows_count", fit["windows"], subject))
    return snapshot, observations


def write_fits(conn, next_session, written_at):
    """A rebuilt fit for every stored session without one, then the live fit for the next session; returns counts."""
    served = store.sessions_served_by_a_fit(conn)
    rebuilt = 0
    for day in store.sessions_with_bars(conn):
        if day in served:
            continue
        rows = fit_rows(conn, date.fromisoformat(day), "history_rebuilt", written_at)
        if rows is not None:
            store.write_snapshot(conn, *rows)
            rebuilt += 1
    rows = fit_rows(conn, next_session, "live_frozen", written_at)
    if rows is not None:
        store.write_snapshot(conn, *rows)
    return rebuilt, int(rows is not None)


def fit_values(conn, fit_snapshot):
    """A stored fit's multipliers and counts by horizon, in the shape fit_multipliers returns."""
    fits = {name: {registry.END_BAND: None, registry.AVERAGE_BAND: None, "sessions": None, "windows": None}
            for name in registry.HORIZON_MINUTES}
    for observation in store.snapshot_observations(conn, fit_snapshot["snapshot_id"]):
        subject = dict(part.split("=", 1) for part in observation["subject_key"].split("&"))
        fit = fits[subject["horizon"]]
        if observation["measure_name"] == "band_multiplier":
            fit[subject["band"]] = observation["value_number"]
        elif observation["measure_name"] == "fit_windows_count":
            fit["windows"] = observation["value_number"]
        elif observation["measure_name"] == "fit_sessions_count":
            fit["sessions"] = observation["value_number"]
    return fits


def day_coverage(bars, fits):
    """Per channel and band, the share of a session's 5-minute reads whose window ended inside the read's band."""
    closes = session_columns(bars)[0]
    inside = {(name, band): [] for name in registry.HORIZON_MINUTES for band in registry.BAND_PRICE_MEASURE}
    for start in range(session_clock.FIRST_SLOT_MINUTES_AFTER_OPEN, len(bars), session_clock.SLOT_MINUTES):
        pace = speed(closes[:start + 1])
        if pace is None:
            continue
        for name, minutes in registry.HORIZON_MINUTES.items():
            swing = swing_points(pace, minutes)
            last = min(start + minutes, len(bars) - 1)
            moves = {registry.END_BAND: abs(closes[last] - closes[start]),
                     registry.AVERAGE_BAND: abs(closes[start + 1:last + 1].mean() - closes[start])}
            for band, move in moves.items():
                if fits[name][band] is not None:
                    inside[(name, band)].append(move <= swing * fits[name][band])
    return {key: float(np.mean(values)) for key, values in inside.items() if values}


def pass_test(conn, lookback):
    """Walk-forward coverage of every session with a full lookback behind it; returns per-day coverage by channel."""
    sessions = store.sessions_with_bars(conn)
    coverage = {}
    for index, day in enumerate(sessions):
        if index < lookback:
            continue
        history = history_windows(conn, date.fromisoformat(day), lookback)
        if len(history["sessions"]) < lookback:
            continue
        coverage[day] = day_coverage(store.session_bars(conn, day), fit_multipliers(history))
    return coverage


def bootstrap_interval(values):
    """Mean coverage and its day-resampled interval: the mean of each draw's days, at the interval's percentiles."""
    draws = np.random.default_rng(BOOTSTRAP_SEED).choice(values, size=(BOOTSTRAP_DRAWS, len(values)), replace=True)
    low, high = np.percentile(draws.mean(axis=1), PASS_INTERVAL_PORTIONS)
    return float(np.mean(values)), float(low), float(high)


def pass_verdict(low, high):
    """PASS when the interval holds the band's share and sits inside the allowed bounds."""
    inside = low <= registry.BAND_PORTION <= high
    bounded = PASS_BAND_BOUNDS[0] <= low and high <= PASS_BAND_BOUNDS[1]
    return "PASS" if inside and bounded else "FAIL"


def pass_test_lines(conn, lookback, heading):
    """The pass test printed: per-day coverage per channel and band, then mean, interval and verdict per column."""
    coverage = pass_test(conn, lookback)
    columns = [(name, band) for band in registry.BAND_PRICE_MEASURE for name in registry.HORIZON_MINUTES]
    labels = {registry.END_BAND: "end", registry.AVERAGE_BAND: "avg"}
    lines = [f"{heading}: {len(coverage)} test days, {lookback}-session lookback",
             "day         " + " ".join(f"{labels[band]}{registry.HORIZON_MINUTES[name]}".rjust(8)
                                       for name, band in columns)]
    for day, shares in coverage.items():
        lines.append(f"{day}  " + " ".join(f"{shares.get(column, float('nan')):8.3f}" for column in columns))
    for column in columns:
        values = [shares[column] for shares in coverage.values() if column in shares]
        if not values:
            continue
        mean, low, high = bootstrap_interval(values)
        lines.append(f"{labels[column[1]]} band {column[0]}: mean {mean:.4f}, 95% interval {low:.4f} to {high:.4f}, "
                     f"{pass_verdict(low, high)}")
    return lines
