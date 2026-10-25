# Every path Kaze touches, from the keys in .env: where Mirai's files are (KAZE_MIRAI_HOME), where Kaze's data goes
# (KAZE_DATA), where the nightly puts its backups (KAZE_BACKUP_DIR, optional; checked by the nightly alone, so a
# missing cloud folder never stops a read) and the phone server's port and token. Importing this file reads .env and
# raises when it is missing, on purpose: nothing runs without the two folders.
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"
REQUIRED_KEYS = ("KAZE_MIRAI_HOME", "KAZE_DATA")
BACKUP_KEY = "KAZE_BACKUP_DIR"
# Mirai's files as the feeds name them: templates under Mirai's data folder ({day} is the session, {symbol} a daily
# file's instrument), the live price
# Mirai's Schwab stream rewrites about once a second, the auth watch's log, and the event calendar under Mirai's
# settings folder. mirai_files.py reads them; registry.py lists them on the feed rows.
MIRAI_FILES = {
    "spx_live_price": "market/spx_live_price_latest.json",
    "spx_bars": "market/spx_price_bars_by_minute/{day}.jsonl",
    "watchlist_quotes": "market/watchlist_quotes_by_minute/{day}.jsonl",
    "watchlist_bars": "market/watchlist_price_bars_by_minute/{day}.jsonl",
    "daily_bars": "market/spx_vix_rates_price_bars_by_day/{symbol}.jsonl",
    "futures_rolls": "market/futures_contracts_and_past_rolls_latest.json",
    "options_scan": "spx/scanner/spx_options_scanner_results_by_scan/{day}.jsonl",
    "options_tape": "market/spx_same_day_options_raw_day_folders/{day}/tape.jsonl",
    "futures_bars": "market/futures_price_bars_by_minute/{day}.jsonl",
    "claude_forecast": "spx/claude_forecast/claude_forecast_answers_by_read/{day}.jsonl",
    "news_headlines": "market/news_headlines_by_article/{day}.jsonl",
    "schwab_login": "operations/logs/auth_watch_output_log.txt",
}
EVENT_CALENDAR_FILE = "market_event_calendar_by_event.json"


def read_env_file(path=ENV_FILE):
    """Read KEY=VALUE lines from the .env file into a dict, skipping blanks and comments; refuse missing folders."""
    values = {}
    if not path.exists():
        raise SystemExit(f"kaze: {path} is missing; copy .env.example to .env and fill it in")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = os.path.expanduser(value.strip())
    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise SystemExit(f"kaze: {path} lacks {', '.join(missing)}")
    for key in REQUIRED_KEYS:
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
    """Where the nightly puts its dated copies of the store: KAZE_BACKUP_DIR, else the folder beside the store."""
    return BACKUP_DIR if BACKUP_DIR is not None else backup_folder_beside_store()


def backup_folder_beside_store():
    """The backup folder on the store's own disk: the default, and the fallback for a night the set folder is gone."""
    return KAZE_DATA / "backups"


def backup_work_folder():
    """Where a backup is copied, checked and gzipped before the finished file moves to the backup folder."""
    return KAZE_DATA / "backup_in_progress"


def nightly_done_file():
    """The one-line file that says which session the nightly last finished."""
    return KAZE_DATA / "nightly_done.txt"
