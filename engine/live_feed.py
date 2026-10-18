# Writes the small JSON file the phone reads after every live read: the snapshot's values, each feed's status (live,
# partial or blind) and age, and the Schwab login expiry. This file is the phone's whole data contract with the store.
import json
import os

from engine import paths, session_clock, store

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


def write_live_feed(snapshot, observations, feed_states):
    """Write latest.json atomically from one snapshot's observations and feed states; returns the file path."""
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
        "login": login, "values": values, "blanks": blanks,
    }
    path = paths.live_feed_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=1))
    os.replace(temporary, path)
    return path
