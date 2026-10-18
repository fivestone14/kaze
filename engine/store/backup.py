# The nightly's backup of the store: a consistent, compacted copy (VACUUM INTO) made and checked beside the store,
# gzipped there, then moved finished into the backup folder as a dated file, the last five kept; older copies go only
# after the new one passed its check. Nothing half-written ever lands in the backup folder. A set backup folder that
# is not there tonight (a cloud folder signed out or evicted) is one warning and the folder beside the store instead.
import gzip
import shutil
import sqlite3

from engine import paths

KEPT_COPIES = 5
COPY_NAME = "kaze-{day}.sqlite"
PACKED_SUFFIX = ".sqlite.gz"
# Only finished copies count toward the kept five; a cloud placeholder (`.kaze-<day>.sqlite.gz.icloud`, an evicted
# copy) is neither counted nor removed, the cloud's own retention looks after it.
PACKED_GLOB = COPY_NAME.format(day="*") + ".gz"


def backup_folder_tonight():
    """The folder tonight's copy goes to and a warning when it is not the set one: (folder, warning or None)."""
    folder = paths.backup_folder()
    if paths.BACKUP_DIR is None:
        return folder, f"{paths.BACKUP_KEY} is not set, so the backup sits on the store's own disk in {folder}"
    if not folder.is_dir():
        beside = paths.backup_folder_beside_store()
        return beside, f"{paths.BACKUP_KEY}={folder} is not a folder tonight; the backup goes to {beside} instead"
    return folder, None


def write_backup(conn, day):
    """Copy the store for a session, check it, gzip it, move it into the backup folder, prune to the kept count."""
    # Returns (the packed file, how many older copies were removed, a warning about the folder or None).
    folder, warning = backup_folder_tonight()
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
    older = sorted(folder.glob(PACKED_GLOB))[:-KEPT_COPIES]
    for path in older:
        path.unlink()
    return delivered, len(older), warning
