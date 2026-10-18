# Every path Kaze touches, taken from the two variables in .env: where Mirai's files are and where Kaze's data goes.
# Importing this file reads .env and raises when it is missing, on purpose: nothing runs without the two paths.
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
REQUIRED_KEYS = ("KAZE_MIRAI_HOME", "KAZE_DATA")
BACKUP_KEY = "KAZE_BACKUP_DIR"


def read_env_file(path=ENV_FILE):
    """Read KEY=VALUE lines from the .env file into a dict, skipping blanks and comments."""
    values = {}
    if not path.exists():
        raise SystemExit(f"kaze: {path} is missing; copy .env.example to .env and fill in the two paths")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = os.path.expanduser(value.strip())
    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise SystemExit(f"kaze: {path} lacks {', '.join(missing)}")
    for key in REQUIRED_KEYS + ((BACKUP_KEY,) if values.get(BACKUP_KEY) else ()):
        if not Path(values[key]).is_dir():
            raise SystemExit(f"kaze: {key}={values[key]} in {path} is not a folder that exists")
    return values


ENV = read_env_file()
MIRAI_HOME = Path(ENV["KAZE_MIRAI_HOME"])
KAZE_DATA = Path(ENV["KAZE_DATA"])
BACKUP_DIR = Path(ENV[BACKUP_KEY]) if ENV.get(BACKUP_KEY) else None


def mirai_data(*parts):
    """A path under Mirai's data folder (read-only for Kaze)."""
    return MIRAI_HOME.joinpath("data", *parts)


def mirai_settings(*parts):
    """A path under Mirai's settings folder (the hand-kept event calendar lives there)."""
    return MIRAI_HOME.joinpath("settings", *parts)


def store_file():
    """The one SQLite file that holds every Kaze table."""
    return KAZE_DATA / "kaze.sqlite"


def live_feed_file():
    """The small JSON file the phone reads after every read."""
    return KAZE_DATA / "live" / "latest.json"


def logs_folder():
    """Where the jobs write their one-line logs."""
    return KAZE_DATA / "logs"


def backup_folder():
    """Where the nightly puts its dated copies of the store: KAZE_BACKUP_DIR, or backups/ beside the store if unset."""
    return BACKUP_DIR if BACKUP_DIR is not None else KAZE_DATA / "backups"


def backup_work_folder():
    """Where a backup is copied, checked and gzipped before the finished file moves to the backup folder."""
    return KAZE_DATA / "backup_in_progress"


def nightly_done_file():
    """The one-line file that says which session the nightly last finished."""
    return KAZE_DATA / "nightly_done.txt"
