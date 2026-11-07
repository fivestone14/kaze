# The call ledger: the push/pull call judged as a whole, nightly, on each channel over the last sessions' live reads.
# Each read's call was graded when its window ended (grade.py): the side the window's average landed on, the odds the
# call gave that side and the odds a call with no push would have. Here they are pooled: the share of leaning calls
# that landed the side they leaned (flat landings left out), the mean log score gain over no push (above 0 the
# readings' odds beat a coin weighted by the flat zone), and per band of the chance of up the share that landed up,
# so overconfident odds show as a band whose share sits below its chance. Reads every 5 minutes overlap their
# neighbours, so the independent windows count only one read per window length. It measures; it moves nothing.
import math
from datetime import date

from engine import session_clock
from engine.store import components, registry, store, store_queries

RUN_ID_PREFIX = "call_ledger"
# The smallest odds a log score takes, so one call that gave a side no chance cannot sink the mean on its own.
ODDS_FLOOR = 1e-4


def band_key(chance):
    """The odds band a chance of up falls in, as a slice key (odds_band=20_30 for 0.2–0.3)."""
    bands = components.CALL_UP_BANDS
    low = max(edge for edge in bands[:-1] if chance >= edge)
    high = bands[bands.index(low) + 1]
    return registry.subject_key(odds_band=f"{low * 100:.0f}_{high * 100:.0f}")


def independent_windows(grades, horizon_name):
    """One read per window length per session: the windows that do not overlap."""
    minutes = components.HORIZON_MINUTES[horizon_name]
    return len({(grade["trade_date"], session_clock.minute_of_session(
        session_clock.parse_iso(grade["cut_at"]), date.fromisoformat(grade["trade_date"])) // minutes)
        for grade in grades})


def call_metrics(grades):
    """The pooled numbers: side-right share over leaning calls that landed up or down, the mean log score gain, and per
    odds band (share landed up, mean chance of up, count)."""
    leaning = [grade for grade in grades if grade["net"] and grade["side"] != "flat"]
    right = sum(1 for grade in leaning if (grade["side"] == "up") == (grade["net"] > 0))
    gains = [math.log(max(grade["given"], ODDS_FLOOR)) - math.log(max(grade["no_push"], ODDS_FLOOR))
             for grade in grades if grade["given"] is not None and grade["no_push"] is not None]
    bands = {}
    for grade in grades:
        if grade["up"] is not None:
            bands.setdefault(band_key(grade["up"]), []).append(grade)
    calibration = {key: (sum(1 for g in rows if g["side"] == "up") / len(rows), sum(g["up"] for g in rows) / len(rows),
                         len(rows)) for key, rows in bands.items()}
    return {"side_right": right / len(leaning) if leaning else None, "leaning": len(leaning),
            "log_gain": sum(gains) / len(gains) if gains else None, "calibration": calibration}


def ledger_rows(conn, through_day, in_force_day, written_at):
    """Every channel's call rows for the run: side right, log score gain, and the share up per odds band."""
    written_iso = session_clock.iso_utc(written_at)
    run = {"trade_date": in_force_day.isoformat(), "data_through_trade_date": through_day.isoformat(),
           "applies_from_at": session_clock.iso_utc(session_clock.session_open(in_force_day)),
           "known_at": written_iso, "written_at": written_iso}
    evaluation_id = f"{RUN_ID_PREFIX}|{through_day}|{components.CALL_LEDGER[0]}@{components.CALL_LEDGER[1]}"
    judged = (components.PUSH_PULL_CALL_KIND, components.CALL_EVALUATED_NAME, components.PUSH_PULL_RECIPE_VERSION)
    rows = []
    for horizon_name in components.HORIZON_MINUTES:
        grades = store_queries.call_grades(conn, horizon_name, through_day.isoformat(),
                                           components.CALL_LEDGER_SESSIONS)
        metrics = call_metrics(grades)
        counts = {"sessions_count": len({grade["trade_date"] for grade in grades}),
                  "independent_windows_count": independent_windows(grades, horizon_name), "rows_count": len(grades),
                  "live_frozen_rows_count": len(grades)}
        add = lambda metric, value, **extra: rows.append(store.evaluation_row(
            evaluation_id, judged, components.CALL_LEDGER, horizon_name, kind=components.SKILL_METRIC_KIND,
            metric_name=metric, value=value, baseline=components.NO_SKILL, counts=counts, run=run,
            family_size=len(components.HORIZON_MINUTES), **extra))
        add("call_side_right_portion", metrics["side_right"], baseline_value=0.5)
        add("call_log_score_gain", metrics["log_gain"], baseline_value=0.0)
        for key, (share, chance, _) in sorted(metrics["calibration"].items()):
            add("call_up_share_portion", share, slice_key=key, baseline_value=chance)
    return rows


def write_ledger(conn, through_day, in_force_day, written_at):
    """Grade the call on every channel and write the run; returns a phrase for the nightly's health line."""
    rows = ledger_rows(conn, through_day, in_force_day, written_at)
    written = store.write_evaluations(conn, rows)
    words = []
    for row in rows:
        if row["metric_name"] == "call_side_right_portion":
            value = "-" if row["metric_value"] is None else f"{row['metric_value']:.0%}"
            words.append(f"{row['horizon_name']} side right {value} of {row['rows_count']} reads")
    return f"call ledger {written} rows written: {', '.join(words)}"


def ledger_lines(conn):
    """The newest call ledger per channel: reads, side right, log score gain, and the share up per odds band."""
    rows = [row for row in store_queries.newest_evaluations(conn) if row["judge_name"] == components.CALL_LEDGER[0]]
    if not rows:
        return ["no call ledger yet: the nightly writes it after its first run"]
    lines = [f"call ledger ({components.CALL_LEDGER[0]}@{components.CALL_LEDGER[1]}): the push/pull call on the "
             f"window's average, last {components.CALL_LEDGER_SESSIONS} sessions' live reads"]
    number = lambda value, form: "-" if value is None else format(value, form)
    for horizon_name in components.HORIZON_MINUTES:
        cells = [row for row in rows if row["horizon_name"] == horizon_name]
        right = next((row for row in cells if row["metric_name"] == "call_side_right_portion"), None)
        gain = next((row for row in cells if row["metric_name"] == "call_log_score_gain"), None)
        if right is None:
            continue
        lines.append(f"{horizon_name:<11} {right['rows_count']:>5} reads ({right['independent_windows_count']} "
                     f"windows, {right['sessions_count']} sessions): side right "
                     f"{number(right['metric_value'], '.1%')}, log score gain "
                     f"{number(gain['metric_value'] if gain else None, '+.4f')}")
        for row in sorted((row for row in cells if row["metric_name"] == "call_up_share_portion"),
                          key=lambda row: row["slice_key"]):
            lines.append(f"{'':<11} chance of up {registry.subject_parts(row['slice_key'])['odds_band']:>6}%: "
                         f"landed up {number(row['metric_value'], '.0%')} against "
                         f"{number(row['baseline_metric_value'], '.0%')} called")
    return lines
