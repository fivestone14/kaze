# One read's range forecast: today's speed and swings from the store's bars, the bands from the newest history fit
# known at the cut, never-leaves share and the options check from that same history, the dials and the 50/50
# direction. The forecast it builds is the phone's range block as well as the source of the stored readings and
# decisions.
import math
from datetime import date, timedelta

import numpy as np

from engine import range_fit, registry, session_clock, store

DECISION_KIND = "range_forecast"
PRODUCER = ("forecaster", registry.RANGE_ENGINE_NAME, registry.RANGE_ENGINE_VERSION)
GRADED_PRICE = registry.BAND_PRICE_MEASURE[registry.BAND]
NO_EFFECT = 1.0
BAND_EDGES = ("half_width", "low", "high")


def value_of(observations, measure_name, subject_key="whole"):
    """The value of one observation in a read's list, or None when it is absent or blank."""
    for observation in observations:
        if observation["measure_name"] == measure_name and observation["subject_key"] == subject_key:
            return store.observation_value(observation)
    return None


def stored_fit(conn, fit_snapshot):
    """A stored fit's multipliers and counts by horizon, in the shape multipliers_from_history returns."""
    fits = {name: range_fit.horizon_fit(None, None, None, None) for name in registry.HORIZON_MINUTES}
    for observation in store.snapshot_observations(conn, fit_snapshot["snapshot_id"]):
        subject = registry.subject_parts(observation["subject_key"])
        fit = fits[subject["horizon"]]
        if observation["measure_name"] == "band_multiplier":
            fit[subject["band"]] = observation["value_number"]
        elif observation["measure_name"] == "average_price_shrink_ratio":
            fit["shrink"] = observation["value_number"]
        elif observation["measure_name"] == "fit_sessions_count":
            fit["sessions"] = observation["value_number"]
        elif observation["measure_name"] == "fit_windows_count":
            fit["windows"] = observation["value_number"]
    return fits


def forecast_read(conn, snapshot, observations, cut_at):
    """Work the read out from the store; returns the forecast its readings, decisions and phone block come from."""
    day = session_clock.to_eastern(cut_at).date()
    bars = store.session_bars(conn, day.isoformat(), snapshot["cut_at"])
    closes = range_fit.minute_series(bars, day.isoformat())[0]
    speed = range_fit.speed(closes)
    speed_fast = range_fit.speed(closes, registry.FAST_SPEED_HALF_LIFE_MINUTES)
    speed_slow = range_fit.speed(closes, registry.SLOW_SPEED_HALF_LIFE_MINUTES)
    fit_snapshot = store.newest_fit_known(conn, day.isoformat(), snapshot["cut_at"])
    fits = stored_fit(conn, fit_snapshot) if fit_snapshot else None
    history = range_fit.history_before(conn, date.fromisoformat(fit_snapshot["trade_date"])) if fit_snapshot else None
    forecast = {
        "price": float(closes[-1]) if closes.size else None, "read_at": cut_at,
        "window_from": cut_at.replace(second=0, microsecond=0) + timedelta(seconds=session_clock.BAR_LENGTH_SECONDS),
        "newest_bar_at": session_clock.parse_iso(bars[-1]["priced_at"]) if bars else None,
        "minutes_in": value_of(observations, "minutes_since_session_open"),
        "minutes_left": value_of(observations, "minutes_until_session_close"),
        "speed": speed, "speed_fast": speed_fast, "speed_slow": speed_slow,
        "fast_over_slow": speed_fast / speed_slow if speed_slow else None,
        "path": {minutes: path_moves(closes, minutes) for minutes in registry.PATH_WINDOW_MINUTES},
        "straddle": value_of(observations, "same_day_straddle_mid_points"),
        "fit": {"id": fit_snapshot["snapshot_id"], "known_at": session_clock.parse_iso(fit_snapshot["known_at"]),
                "status": fit_snapshot["snapshot_status"]} if fit_snapshot else None,
        "fed_decision_at": fed_decision_at(conn, day, snapshot["cut_at"]),
        "chance_up_if_moves": registry.DIRECTION_UNTIL_EARNED, "trust": 0.0,
        "channels": {},
    }
    for name, minutes in registry.HORIZON_MINUTES.items():
        forecast["channels"][name] = channel_forecast(forecast, name, minutes, fits, history)
    return forecast


def path_moves(closes, minutes):
    """Up moves, down moves and straightness over the last window of minutes; None before the window has formed."""
    if closes.size <= minutes:
        return None
    changes = np.diff(closes[-minutes - 1:])
    changes = changes[~np.isnan(changes)]
    up, down = float(changes[changes > 0].sum()), float(-changes[changes < 0].sum())
    return {"up": up, "down": down, "straightness": abs(up - down) / (up + down) if up + down else None}


def fed_decision_at(conn, day, cut_at_iso):
    """When the day's Fed decision lands, from the calendar facts known at the cut, or None when there is none."""
    times = [starts_at for starts_at, kind in store.calendar_events_known(conn, day.isoformat(), cut_at_iso)
             if kind in registry.FED_DECISION_EVENT_KINDS]
    return session_clock.parse_iso(min(times)) if times else None


def channel_forecast(forecast, name, minutes, fits, history):
    """One horizon's swing, multipliers, bands, never-leaves share, options check, dials, window and status."""
    speed, minutes_left, price = forecast["speed"], forecast["minutes_left"], forecast["price"]
    fit = fits[name] if fits else range_fit.horizon_fit(None, None, None, None)
    swing = range_fit.swing_points(speed, minutes) if speed is not None else None
    window_minutes = min(minutes, minutes_left) if minutes_left is not None else minutes
    ends_at = forecast["window_from"] + timedelta(minutes=window_minutes)
    in_fed_window = forecast["fed_decision_at"] is not None and forecast["fed_decision_at"] < ends_at
    straddle = forecast["straddle"]
    options_swing = straddle * math.sqrt(window_minutes / minutes_left) if straddle and minutes_left else None
    window_swing = range_fit.swing_points(speed, window_minutes) if speed else None
    dial_raw = {registry.OWNER_RULE_DIAL: registry.FED_OWNER_RULE_MULTIPLIER if in_fed_window else NO_EFFECT,
                "fast_over_slow": forecast["fast_over_slow"]}
    channel = {
        "minutes": minutes, "swing": swing, "stretch": fit[registry.BAND],
        "average_stretch": fit[registry.AVERAGE_BAND],
        "fit_sessions": fit["sessions"], "fit_windows": fit["windows"], "rung_points": registry.RUNG_POINTS[name],
        "stays_inside_share": range_fit.never_leaves_share(history, name, registry.RUNG_POINTS[name], swing)
        if history else None,
        "options_swing": options_swing,
        "options_over_tape": options_swing / window_swing if options_swing and window_swing else None,
        "clipped": minutes_left is not None and minutes > minutes_left, "ends_at": ends_at,
        "in_fed_window": in_fed_window,
        "dials": {dial: {"raw": dial_raw[dial], "applied": NO_EFFECT} for dial in registry.WIDTH_DIALS},
        "bands": band_edges(price, swing, fit, NO_EFFECT),
        "owner_rule_bands": (band_edges(price, swing, fit, registry.FED_OWNER_RULE_MULTIPLIER)
                            if in_fed_window else None),
    }
    if swing is None:
        channel["status"], channel["reason"] = "blind", "speed_not_formed"
    elif channel["stretch"] is None:
        channel["status"], channel["reason"] = "blind", "no_fit_known_at_cut"
    elif channel["clipped"]:
        channel["status"], channel["reason"] = "clipped", "window_capped_at_close"
    else:
        channel["status"], channel["reason"] = "ok", None
    return channel


def band_edges(price, swing, fit, width_dial):
    """Per band, half-width, low and high at a width dial; None where an input is missing."""
    bands = {}
    for band in registry.BAND_PRICE_MEASURE:
        formed = swing is not None and fit[band] is not None and price is not None
        half = swing * fit[band] * width_dial if formed else None
        bands[band] = dict(zip(BAND_EDGES, (half, price - half, price + half) if formed else (None, None, None)))
    return bands


def feed_stamp(observations, feed):
    """A feed's status and age at the cut, as the collector stamped them on the read."""
    return {"feed_status": value_of(observations, "feed_status", registry.subject_key(feed=feed)),
            "feed_age_seconds": value_of(observations, "feed_age_seconds", registry.subject_key(feed=feed))}


def reading_rows(snapshot, observations, forecast):
    """The read's range readings as observations on its snapshot: speeds, swings, the options check and the path."""
    rows = []
    stamps = {feed: feed_stamp(observations, feed) for feed in registry.RANGE_INPUT_FEEDS}
    bars_stamp = dict(stamps["spx_bars"],
                      market_at=forecast["newest_bar_at"] and session_clock.iso_utc(forecast["newest_bar_at"]))
    scan_stamp = stamps["options_scan"]
    not_formed = registry.NOT_FORMED_REASON

    def add(measure_name, value, stamp, blank=None, **subject):
        """One reading row, rounded, with its feed's stamp and the subject given."""
        rows.append(store.observation_row(snapshot, measure_name, store.rounded(value), registry.subject_key(**subject),
                                          blank_reason=blank, **stamp))

    add("today_typical_1_minute_move_points", forecast["speed"], bars_stamp, not_formed)
    add("speed_fast_5_minute_half_life_points", forecast["speed_fast"], bars_stamp, not_formed)
    add("speed_slow_30_minute_half_life_points", forecast["speed_slow"], bars_stamp, not_formed)
    add("speed_fast_over_slow_ratio", forecast["fast_over_slow"], bars_stamp,
        not_formed if forecast["speed_slow"] is None else "no_activity")
    for name, channel in forecast["channels"].items():
        add("typical_swing_points", channel["swing"], bars_stamp, not_formed, horizon=name)
        add("options_implied_swing_points", channel["options_swing"], scan_stamp, "not_priceable", horizon=name)
        add("options_over_tape_swing_ratio", channel["options_over_tape"], scan_stamp, "not_priceable", horizon=name)
    for minutes, path in forecast["path"].items():
        formed = path is not None
        add("path_up_points", path["up"] if formed else None, bars_stamp, not_formed, window_minutes=minutes)
        add("path_down_points", path["down"] if formed else None, bars_stamp, not_formed, window_minutes=minutes)
        add("trend_straightness_ratio", path["straightness"] if formed else None, bars_stamp,
            not_formed if not formed else "no_activity", window_minutes=minutes)
    return rows


def decision_rows(snapshot, forecast):
    """One range_forecast decision per channel, plus the owner_rule version when the window sits in a Fed window."""
    decisions, values = [], []
    for name, channel in forecast["channels"].items():
        fields = {
            "horizon_name": name, "horizon_minutes": channel["minutes"], "graded_price_measure": GRADED_PRICE,
            "grade_start_rule_version": registry.GRADE_START_RULE,
            "grade_window_starts_at": session_clock.iso_utc(forecast["window_from"]),
            "grade_window_ends_at": session_clock.iso_utc(channel["ends_at"]),
            "decision_status": channel["status"], "decision_status_reason": channel["reason"],
        }
        base = store.decision_row(snapshot, DECISION_KIND, PRODUCER, fields)
        decisions.append(base)
        values.extend(value_rows(base, forecast, name, None))
        if channel["owner_rule_bands"] is not None:
            variant = store.decision_row(snapshot, DECISION_KIND, PRODUCER, dict(
                fields, variant_name=registry.OWNER_RULE_VARIANT, parent_decision_id=base["decision_id"]))
            decisions.append(variant)
            values.extend(value_rows(variant, forecast, name, registry.OWNER_RULE_VARIANT))
    return decisions, values


def value_rows(decision, forecast, name, variant):
    """A decision's values: centre, speed, swing, fit, bands (Fed-widened on the owner_rule), rung share, dials, up."""
    rows = []
    channel = forecast["channels"][name]
    bands = channel["owner_rule_bands"] if variant == registry.OWNER_RULE_VARIANT else channel["bands"]
    not_formed, no_fit = registry.NOT_FORMED_REASON, registry.NO_FIT_REASON
    band_blank = not_formed if channel["swing"] is None else no_fit
    horizon = registry.subject_key(horizon=name)

    def add(measure_name, value, subject_key="whole", blank=None, **extra):
        """One value row of this decision, rounded."""
        rows.append(store.decision_value_row(decision, measure_name, store.rounded(value), subject_key,
                                             blank_reason=blank, **extra))

    add("band_centre_points", forecast["price"], horizon, "feed_missing")
    add("today_typical_1_minute_move_points", forecast["speed"], blank=not_formed)
    add("typical_swing_points", channel["swing"], horizon, not_formed)
    add("fit_snapshot_id", forecast["fit"] and forecast["fit"]["id"], blank=no_fit)
    add("fit_sessions_count", channel["fit_sessions"], horizon, no_fit)
    add("fit_windows_count", channel["fit_windows"], horizon, no_fit)
    for band, edges in bands.items():
        subject = registry.subject_key(band=band, horizon=name)
        add("band_multiplier", channel["stretch" if band == registry.BAND else "average_stretch"], subject, no_fit)
        add("stretch_recipe_name", registry.STRETCH_RECIPE[band], subject)
        add("band_half_width_points", edges["half_width"], subject, band_blank)
        add("band_low_points", edges["low"], subject, band_blank)
        add("band_high_points", edges["high"], subject, band_blank)
    add("chance_stays_inside_rung_probability", channel["stays_inside_share"],
        registry.subject_key(horizon=name, rung_points=channel["rung_points"]), band_blank,
        subject_points=float(channel["rung_points"]))
    for dial, setting in channel["dials"].items():
        subject = registry.subject_key(adjustment=dial)
        applied = setting["raw"] if variant == registry.OWNER_RULE_VARIANT and dial == registry.OWNER_RULE_DIAL \
            else setting["applied"]
        add("width_multiplier", setting["raw"], subject, not_formed, value_role="raw")
        add("width_multiplier", applied, subject, value_role="applied")
    add("chance_up_if_moves_probability", forecast["chance_up_if_moves"], trust_weight=forecast["trust"])
    return rows
