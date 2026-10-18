# One log file per job under $KAZE_DATA/logs, one timestamped line per event, rotated daily into logs/older/<day>/
# as gzip and kept 14 days. Jobs write here and nowhere else, so launchd's capture file only holds what Python missed.
import gzip
import shutil
import threading
from datetime import date, timedelta

from engine import paths, session_clock

KEPT_DAYS = 14
OLDER_FOLDER = "older"
WRITE_LOCK = threading.Lock()


def log_file(job):
    """The job's current log file."""
    return paths.logs_folder() / f"{job}_log.txt"


def write_line(job, line):
    """Append one New York-stamped line to the job's log, rotating first when the file is from before today."""
    # The day is the New York date, like every stamp in the file, whatever zone the box runs in; one lock serialises
    # the threads of the phone's server, so a midnight rotation happens once.
    folder = paths.logs_folder()
    folder.mkdir(parents=True, exist_ok=True)
    path = log_file(job)
    today = session_clock.today_eastern()
    with WRITE_LOCK:
        if path.exists():
            last_written = session_clock.to_eastern(session_clock.file_modified_at(path)).date()
            if last_written < today:
                rotate(path, last_written)
                prune_older(folder, today)
        with open(path, "a") as handle:
            handle.write(f"{session_clock.iso_eastern(session_clock.now_utc())} {line}\n")


def rotate(path, day):
    """Move a log into older/<day>/ gzipped."""
    target_folder = path.parent / OLDER_FOLDER / day.isoformat()
    target_folder.mkdir(parents=True, exist_ok=True)
    with open(path, "rb") as source, gzip.open(target_folder / (path.name + ".gz"), "wb") as target:
        shutil.copyfileobj(source, target)
    path.unlink()


def prune_older(folder, today):
    """Delete rotated day folders older than the keep window."""
    older = folder / OLDER_FOLDER
    if not older.exists():
        return
    for day_folder in older.iterdir():
        try:
            day = date.fromisoformat(day_folder.name)
        except ValueError:
            continue
        if today - day > timedelta(days=KEPT_DAYS):
            shutil.rmtree(day_folder)
