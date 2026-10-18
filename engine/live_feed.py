# Writes the small JSON file the phone reads after every read: the snapshot's values, each feed's status (live, partial
# or blind) and age, and the Schwab login expiry. Nothing else reads the store on the phone's behalf.
import json
import os

from engine import paths, session_clock, store

FEED_WORDS = {"ok": "live", "stale": "partial", "frozen_feed": "partial", "stand_in": "partial",
              "missing": "blind", "market_closed": "blind"}
LOGIN_KEYS = {"login_state": "state", "login_hours_until_expiry": "hours_left", "login_expires_at": "expires_at"}


def feed_values(observations):
    """Split observations into {key: value} and {key: blank reason}; a key is the measure, plus |subject when there is one."""
    values, blanks = {}, {}
    for observation in observations:
        key = observation["measure_name"]
        if observation["subject_key"] != "whole":
            key += "|" + observation["subject_key"]
        value = next((observation[column] for column in store.VALUE_COLUMNS if observation[column] is not None), None)
        if value is None:
            blanks[key] = observation["blank_reason"]
        else:
            values[key] = value
    return values, blanks


def write_live_feed(snapshot, observations, feeds):
    """Write latest.json atomically from one snapshot's observations and feed states; returns the file path."""
    values, blanks = feed_values(observations)
    login = {short: values.pop(f"{name}|login=schwab", None) for name, short in LOGIN_KEYS.items()}
    document = {
        "read_id": snapshot["read_id"], "lane": snapshot["read_lane"], "slot": snapshot["scheduled_slot_eastern"],
        "origin": snapshot["row_origin"], "status": snapshot["snapshot_status"],
        "cut_at": session_clock.iso_eastern(session_clock.parse_iso(snapshot["cut_at"])),
        "known_at": session_clock.iso_eastern(session_clock.parse_iso(snapshot["known_at"])),
        "feeds": {name: {"status": FEED_WORDS[state["status"]], "age_s": state["age"]} for name, state in feeds.items()},
        "login": login, "values": values, "blanks": blanks,
    }
    path = paths.live_feed_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, indent=1, sort_keys=False))
    os.replace(temporary, path)
    return path
