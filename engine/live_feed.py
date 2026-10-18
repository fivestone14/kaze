# Writes the small JSON file the phone reads after every live read: the snapshot's values, each feed's status (live,
# partial or blind) and age, the Schwab login expiry and the range forecast. This file is the phone's whole contract.
import json
import os

from engine import paths, session_clock, store

CHANNEL_KEYS = ("mins", "swing", "stretch", "half", "lo", "hi", "avg_stretch", "avg_half", "avg_lo", "avg_hi", "rung",
                "stay_odds", "opt_swing", "opt_ratio", "capped", "fed_window", "dials", "owner_rule", "status")

PHONE_WORD_FOR_FEED_STATUS = {"ok": "live", "late": "partial", "stale": "partial", "frozen_feed": "partial",
                              "stand_in": "partial", "missing": "blind", "market_closed": "blind"}
LOGIN_FIELD_SHORT_NAMES = {"login_state": "state", "login_hours_until_expiry": "hours_left",
                           "login_expires_at": "expires_at"}


def split_values_and_blanks(observations):
    """{key: value} and {key: blank reason}; a key is the measure name, plus |subject when there is one."""
    values, blanks = {}, {}
    for observation in observations:
        key = observation["measure_name"]
        if observation["subject_key"] != "whole":
            key += "|" + observation["subject_key"]
        value = store.observation_value(observation)
        if value is None:
            blanks[key] = observation["blank_reason"]
        else:
            values[key] = value
    return values, blanks


def rounded_tree(value):
    """A value, dict or list with every float at the store's precision."""
    if isinstance(value, dict):
        return {key: rounded_tree(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [rounded_tree(inner) for inner in value]
    return store.rounded(value)


def range_block(forecast):
    """The range forecast for the phone: the read price and clock, speeds, fit, Fed flag, direction, each channel."""
    channels = {}
    for name, channel in forecast["channels"].items():
        channels[name] = rounded_tree({key: channel[key] for key in CHANNEL_KEYS})
        channels[name]["ends_at"] = session_clock.iso_eastern(channel["ends_at"])
    return {
        "price": forecast["price"], "read_at": session_clock.iso_eastern(forecast["cut_at"]),
        "window_from": session_clock.iso_eastern(forecast["bar_ends_at"]), "mins_in": forecast["mins_in"],
        "mins_left": forecast["mins_left"], "speed": store.rounded(forecast["speed"]),
        "fast": store.rounded(forecast["fast"]), "slow": store.rounded(forecast["slow"]),
        "accel": store.rounded(forecast["accel"]), "straddle": forecast["straddle"],
        "fit": forecast["fit_id"] and {"id": forecast["fit_id"],
                                       "known_at": session_clock.iso_eastern(forecast["fit_known_at"])},
        "fed": forecast["fed_at"] is not None,
        "fed_at": forecast["fed_at"] and session_clock.iso_eastern(forecast["fed_at"]),
        "up_pct": forecast["up_pct"], "trust": forecast["trust"], "channels": channels,
    }


def write_live_feed(snapshot, observations, feed_states, forecast):
    """Write latest.json atomically from one snapshot's observations, feed states and forecast; returns the path."""
    values, blanks = split_values_and_blanks(observations)
    login = {short: values.pop(f"{name}|login=schwab", None) for name, short in LOGIN_FIELD_SHORT_NAMES.items()}
    document = {
        "read_id": snapshot["read_id"], "lane": snapshot["read_lane"], "slot": snapshot["scheduled_slot_eastern"],
        "origin": snapshot["row_origin"], "status": snapshot["snapshot_status"],
        "status_reason": snapshot["snapshot_status_reason"],
        "cut_at": session_clock.iso_eastern(session_clock.parse_iso(snapshot["cut_at"])),
        "known_at": session_clock.iso_eastern(session_clock.parse_iso(snapshot["known_at"])),
        "feeds": {name: {"status": PHONE_WORD_FOR_FEED_STATUS[state["status"]], "age_s": state["age"]}
                  for name, state in feed_states.items()},
        "login": login, "range": range_block(forecast), "values": values, "blanks": blanks,
    }
    path = paths.live_feed_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=1))
    os.replace(temporary, path)
    return path
