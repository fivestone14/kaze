# The nightly's backup of the store: a consistent, compacted copy (VACUUM INTO) checked whole before it is gzipped
# into a dated file in the backup folder, the last five kept; older copies go only after the new one passed its check.
import gzip
import shutil
import sqlite3

from engine import paths

KEPT_COPIES = 5
COPY_NAME = "kaze-{day}.sqlite"
PACKED_SUFFIX = ".sqlite.gz"


def write_backup(conn, day):
    """Copy the store for a day into the backup folder, check it, gzip it, prune to the kept count; returns
    (the packed file, how many older copies were removed, whether the folder shares the store's disk)."""
    folder = paths.backup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / COPY_NAME.format(day=day)
    packed = raw.with_suffix(PACKED_SUFFIX)
    if raw.exists():
        raw.unlink()
    conn.execute("VACUUM INTO ?", (str(raw),))
    copy = sqlite3.connect(str(raw))
    verdict = copy.execute("PRAGMA integrity_check").fetchone()[0]
    copy.close()
    if verdict != "ok":
        raw.unlink()
        raise RuntimeError(f"the backup copy failed integrity_check: {verdict}")
    with open(raw, "rb") as source, gzip.open(packed, "wb") as target:
        shutil.copyfileobj(source, target)
    raw.unlink()
    older = sorted(folder.glob(COPY_NAME.format(day="*") + ".gz"))[:-KEPT_COPIES]
    for path in older:
        path.unlink()
    return packed, len(older), paths.BACKUP_DIR is None
