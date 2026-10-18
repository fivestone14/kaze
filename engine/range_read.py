# One read's range forecast: today's speed and swings from the store's bars, the bands from the newest history fit
# known at the cut, never-leaves odds and the options check from that same history, the dials and the 50/50 direction.
import math
from datetime import date, timedelta

import numpy as np

from engine import range_fit, registry, session_clock, store

DECISION_KIND = "range_forecast"
PRODUCER = ("forecaster", registry.RANGE_ENGINE_NAME, registry.RANGE_ENGINE_VERSION)
GRADED_PRICE = registry.BAND_PRICE_MEASURE[registry.END_BAND]
NO_EFFECT = 1.0
SPEED_BLANK = "not_formed_yet"
FIT_BLANK = "too_few_sessions"
BAND_FIELDS = {registry.END_BAND: ("half", "lo", "hi"), registry.AVERAGE_BAND: ("avg_half", "avg_lo", "avg_hi")}


def observed(observations, measure_name, subject_key="whole"):
    """The value of one observation in a read's list, or None when it is absent or blank."""
    for observation in observations:
        if observation["measure_name"] == measure_name and observation["subject_key"] == subject_key:
            return store.observation_value(observation)
    return None


def feed_stamp(observations, feed):
    """A feed's status and age at the cut, as the collector stamped them on the read."""
    return {"feed_status": observed(observations, "feed_status", registry.subject_key(feed=feed)),
            "feed_age_seconds": observed(observations, "feed_age_seconds", registry.subject_key(feed=feed))}


def forecast_read(conn, snapshot, observations, cut_at):
    """Work the read out from the store; returns the forecast its readings, decisions and phone block come from."""
    day = session_clock.to_eastern(cut_at).date()
    bars = store.session_bars(conn, day.isoformat(), snapshot["cut_at"])
    closes = np.array([bar["close"] for bar in bars], dtype=float)
    pace = range_fit.speed(closes)
    fast = range_fit.speed(closes, registry.FAST_SPEED_HALF_LIFE_MINUTES)
    slow = range_fit.speed(closes, registry.SLOW_SPEED_HALF_LIFE_MINUTES)
    fit_snapshot = store.newest_fit_known(conn, day.isoformat(), snapshot["cut_at"])
    fit = range_fit.fit_values(conn, fit_snapshot) if fit_snapshot else None
    history = range_fit.history_windows(conn, date.fromisoformat(fit_snapshot["trade_date"])) if fit_snapshot else None
    minutes_left = observed(observations, "minutes_until_session_close")
    bar_ends_at = cut_at.replace(second=0, microsecond=0) + timedelta(minutes=1)
    fed_at = fed_decision_at(conn, day, snapshot["cut_at"])
    forecast = {
        "price": float(closes[-1]) if closes.size else None, "cut_at": cut_at, "bar_ends_at": bar_ends_at,
        "newest_bar_at": bars[-1]["priced_at"] if bars else None,
        "mins_in": observed(observations, "minutes_since_session_open"), "mins_left": minutes_left,
        "speed": pace, "fast": fast, "slow": slow, "accel": fast / slow if slow else None,
        "legs": {minutes: legs(closes, minutes) for minutes in registry.LEG_WINDOW_MINUTES},
        "straddle": observed(observations, "same_day_straddle_mid_points"),
        "fit_id": fit_snapshot["snapshot_id"] if fit_snapshot else None,
        "fit_known_at": session_clock.parse_iso(fit_snapshot["known_at"]) if fit_snapshot else None,
        "fed_at": fed_at, "up_pct": registry.DIRECTION_UNTIL_EARNED * 100, "trust": 0.0,
        "stamps": {feed: feed_stamp(observations, feed) for feed in ("spx_bars", "options_scan")},
        "channels": {},
    }
    for name, minutes in registry.HORIZON_MINUTES.items():
        forecast["channels"][name] = channel_forecast(forecast, name, minutes, fit, history)
    return forecast


def legs(closes, minutes):
    """Up legs, down legs and straightness over the last window of minutes; None before the window has formed."""
    if closes.size <= minutes:
        return None
    changes = np.diff(closes[-minutes - 1:])
    up, down = float(changes[changes > 0].sum()), float(-changes[changes < 0].sum())
    return {"up": up, "down": down, "straight": abs(up - down) / (up + down) if up + down else None}


def fed_decision_at(conn, day, cut_at_iso):
    """When the day's Fed decision lands, from the calendar facts known at the cut, or None when there is none."""
    times = [starts_at for starts_at, kind in store.calendar_events_known(conn, day.isoformat(), cut_at_iso)
             if kind in registry.FED_DECISION_EVENT_KINDS]
    return session_clock.parse_iso(min(times)) if times else None


def channel_forecast(forecast, name, minutes, fit, history):
    """One horizon's swing, multipliers, bands, never-leaves odds, options check, dials, window and status."""
    pace, minutes_left, price = forecast["speed"], forecast["mins_left"], forecast["price"]
    swing = range_fit.swing_points(pace, minutes) if pace is not None else None
    window_minutes = min(minutes, minutes_left) if minutes_left is not None else minutes
    ends_at = forecast["bar_ends_at"] + timedelta(minutes=window_minutes)
    fed_window = forecast["fed_at"] is not None and forecast["fed_at"] < ends_at
    multipliers = fit[name] if fit else {registry.END_BAND: None, registry.AVERAGE_BAND: None}
    rung = registry.RUNG_POINTS[name]
    straddle = forecast["straddle"]
    options_swing = straddle * math.sqrt(window_minutes / minutes_left) if straddle and minutes_left else None
    channel = {
        "mins": minutes, "swing": swing, "stretch": multipliers[registry.END_BAND],
        "avg_stretch": multipliers[registry.AVERAGE_BAND], "rung": rung,
        "stay_odds": range_fit.never_leaves_share(history, name, rung, swing) if history else None,
        "opt_swing": options_swing, "opt_ratio": options_swing / swing if options_swing and swing else None,
        "fit_sessions": multipliers.get("sessions"), "fit_windows": multipliers.get("windows"),
        "capped": minutes_left is not None and minutes > minutes_left, "ends_at": ends_at, "fed_window": fed_window,
        "dials": {"fed_window": {"raw": registry.FED_OWNER_RULE_MULTIPLIER if fed_window else NO_EFFECT,
                                 "applied": NO_EFFECT},
                  "acceleration": {"raw": forecast["accel"], "applied": NO_EFFECT}},
        **band_edges(price, swing, multipliers, NO_EFFECT),
    }
    channel["owner_rule"] = (band_edges(price, swing, multipliers, registry.FED_OWNER_RULE_MULTIPLIER)
                             if fed_window else None)
    if swing is None:
        channel["status"], channel["reason"] = "blind", "speed_not_formed"
    elif channel["stretch"] is None:
        channel["status"], channel["reason"] = "blind", "no_fit_known_at_cut" if fit is None else "fit_has_no_windows"
    elif channel["capped"]:
        channel["status"], channel["reason"] = "clipped", "window_capped_at_close"
    else:
        channel["status"], channel["reason"] = "ok", None
    return channel


def band_edges(price, swing, multipliers, width_dial):
    """Half-width, low and high of the end and average bands at a width dial; None where an input is missing."""
    edges = {}
    for band, keys in BAND_FIELDS.items():
        formed = swing is not None and multipliers[band] is not None and price is not None
        half = swing * multipliers[band] * width_dial if formed else None
        edges.update(zip(keys, (half, price - half, price + half) if formed else (None, None, None)))
    return edges


def reading_rows(snapshot, forecast):
    """The read's range readings as observations on its snapshot: speeds, swings, the options check and the legs."""
    rows = []
    bars_stamp = dict(forecast["stamps"]["spx_bars"], market_at=forecast["newest_bar_at"])
    scan_stamp = forecast["stamps"]["options_scan"]

    def add(measure_name, value, stamp, blank=None, **subject):
        """One reading row, rounded, with its feed's stamp and the subject given."""
        rows.append(store.observation_row(snapshot, measure_name, store.rounded(value), registry.subject_key(**subject),
                                          blank_reason=blank, **stamp))

    add("today_typical_1_minute_move_points", forecast["speed"], bars_stamp, SPEED_BLANK)
    add("speed_fast_5_minute_half_life_points", forecast["fast"], bars_stamp, SPEED_BLANK)
    add("speed_slow_30_minute_half_life_points", forecast["slow"], bars_stamp, SPEED_BLANK)
    add("speed_fast_over_slow_ratio", forecast["accel"], bars_stamp,
        SPEED_BLANK if forecast["slow"] is None else "no_activity")
    for name, channel in forecast["channels"].items():
        add("typical_swing_points", channel["swing"], bars_stamp, SPEED_BLANK, horizon=name)
        add("options_implied_swing_points", channel["opt_swing"], scan_stamp, "not_priceable", horizon=name)
        add("options_over_tape_swing_ratio", channel["opt_ratio"], scan_stamp, "not_priceable", horizon=name)
    for minutes, window in forecast["legs"].items():
        formed = window is not None
        add("up_legs_points", window["up"] if formed else None, bars_stamp, SPEED_BLANK, window_minutes=minutes)
        add("down_legs_points", window["down"] if formed else None, bars_stamp, SPEED_BLANK, window_minutes=minutes)
        add("trend_straightness_ratio", window["straight"] if formed else None, bars_stamp,
            SPEED_BLANK if not formed else "no_activity", window_minutes=minutes)
    return rows


def decision_rows(snapshot, forecast):
    """One range_forecast decision per channel, plus the owner_rule version when the window sits in a Fed window."""
    decisions, values = [], []
    for name, channel in forecast["channels"].items():
        fields = {
            "horizon_name": name, "horizon_minutes": channel["mins"], "graded_price_measure": GRADED_PRICE,
            "grade_start_rule_version": registry.GRADE_START_RULE,
            "grade_window_starts_at": session_clock.iso_utc(forecast["bar_ends_at"]),
            "grade_window_ends_at": session_clock.iso_utc(channel["ends_at"]),
            "decision_status": channel["status"], "decision_status_reason": channel["reason"],
        }
        base = store.decision_row(snapshot, DECISION_KIND, PRODUCER, fields)
        decisions.append(base)
        values.extend(value_rows(base, forecast, name, channel, channel, NO_EFFECT))
        if channel["owner_rule"] is not None:
            variant = store.decision_row(snapshot, DECISION_KIND, PRODUCER, dict(
                fields, variant_name=registry.OWNER_RULE_VARIANT, parent_decision_id=base["decision_id"]))
            decisions.append(variant)
            values.extend(value_rows(variant, forecast, name, channel, channel["owner_rule"],
                                     registry.FED_OWNER_RULE_MULTIPLIER))
    return decisions, values


def value_rows(decision, forecast, name, channel, edges, fed_applied):
    """A decision's values: centre, speed, swing, fit provenance, both bands, rung odds, dials and direction."""
    rows = []
    band_blank = SPEED_BLANK if channel["swing"] is None else FIT_BLANK
    horizon = registry.subject_key(horizon=name)

    def add(measure_name, value, subject_key="whole", blank=None, **extra):
        """One value row of this decision, rounded."""
        rows.append(store.decision_value_row(decision, measure_name, store.rounded(value), subject_key,
                                             blank_reason=blank, **extra))

    add("band_centre_points", forecast["price"], horizon, "feed_missing")
    add("today_typical_1_minute_move_points", forecast["speed"], blank=SPEED_BLANK)
    add("typical_swing_points", channel["swing"], horizon, SPEED_BLANK)
    add("fit_snapshot_id", forecast["fit_id"], blank=FIT_BLANK)
    add("fit_sessions_count", channel["fit_sessions"], horizon, FIT_BLANK)
    add("fit_windows_count", channel["fit_windows"], horizon, FIT_BLANK)
    for band, (half_key, low_key, high_key) in BAND_FIELDS.items():
        subject = registry.subject_key(band=band, horizon=name)
        add("band_multiplier", channel["stretch" if band == registry.END_BAND else "avg_stretch"], subject, FIT_BLANK)
        add("stretch_recipe_name", registry.STRETCH_RECIPE[band], subject)
        add("band_half_width_points", edges[half_key], subject, band_blank)
        add("band_low_points", edges[low_key], subject, band_blank)
        add("band_high_points", edges[high_key], subject, band_blank)
    add("chance_stays_inside_rung_probability", channel["stay_odds"],
        registry.subject_key(horizon=name, rung_points=channel["rung"]), band_blank,
        subject_points=float(channel["rung"]))
    for dial, settings in channel["dials"].items():
        subject = registry.subject_key(adjustment=dial)
        applied = fed_applied if dial == "fed_window" else settings["applied"]
        add("width_multiplier", settings["raw"], subject, SPEED_BLANK, value_role="raw")
        add("width_multiplier", applied, subject, value_role="applied")
    add("chance_up_if_moves_probability", registry.DIRECTION_UNTIL_EARNED, trust_weight=forecast["trust"])
    return rows
