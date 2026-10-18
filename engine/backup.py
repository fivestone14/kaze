# The nightly's backup of the store: a consistent, compacted copy (VACUUM INTO) made and checked beside the store,
# gzipped there, then moved finished into the backup folder as a dated file, the last five kept; older copies go only
# after the new one passed its check. Nothing half-written ever lands in the backup folder.
import gzip
import shutil
import sqlite3

from engine import paths

KEPT_COPIES = 5
COPY_NAME = "kaze-{day}.sqlite"
PACKED_SUFFIX = ".sqlite.gz"


def write_backup(conn, day):
    """Copy the store for a session, check it, gzip it, move it into the backup folder, prune to the kept count;
    returns (the packed file, how many older copies were removed, whether the folder shares the store's disk)."""
    folder = paths.backup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    raw = paths.backup_work_folder() / COPY_NAME.format(day=day)
    raw.parent.mkdir(parents=True, exist_ok=True)
    packed = raw.with_suffix(PACKED_SUFFIX)
    for leftover in (raw, packed):
        if leftover.exists():
            leftover.unlink()
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
    delivered = folder / packed.name
    shutil.move(str(packed), str(delivered))
    older = sorted(folder.glob(COPY_NAME.format(day="*") + ".gz"))[:-KEPT_COPIES]
    for path in older:
        path.unlink()
    return delivered, len(older), paths.BACKUP_DIR is None
