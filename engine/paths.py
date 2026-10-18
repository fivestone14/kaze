# Every path Kaze touches, taken from the two variables in .env: where Mirai's files are and where Kaze's data goes.
# Nothing else in the engine builds a path on its own; it asks this file.
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
REQUIRED_KEYS = ("KAZE_MIRAI_HOME", "KAZE_DATA")


def read_env_file(path=ENV_FILE):
    """Read KEY=VALUE lines from the .env file into a dict, skipping blanks and comments."""
    values = {}
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; copy .env.example to .env and fill in the two paths")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = os.path.expanduser(value.strip())
    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise KeyError(f"{path} lacks {', '.join(missing)}")
    return values


SETTINGS = read_env_file()
MIRAI_HOME = Path(SETTINGS["KAZE_MIRAI_HOME"])
KAZE_DATA = Path(SETTINGS["KAZE_DATA"])


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
