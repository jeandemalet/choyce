"""Silent upkeep of the preview caches: nothing to do for the user, nothing lost.

Previews are only copies made for the screen. Those left unused for a long time (a project
not reopened, a former screen, a photo since retouched) are deleted, and Choyce makes them
again by itself the next time it needs them. Only files that Choyce itself names are
touched, and only inside a Previews folder: never an original, a note or an export.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from triphoto import SESSION_DIR_NAME

DAY = 24*3600
# A preview left untouched this long belongs to a project, a screen or a version of a photo no longer looked at.
MAX_AGE = 60*DAY
# A write cut short leaves a .tmp behind.
TMP_AGE = DAY
# Shared by every selection: no single selection gives it its size.
SHARED_LIMIT = 4*1024**3
# Previews of the Qt views, and those of the former Tk interface.
OWN_NAMES = (re.compile(r'qt-[0-9a-f]{64}\.(png|tmp)'), re.compile(r'[0-9a-f]{40}\.jpg'))


def preview_dir(root):
    return Path(root)/SESSION_DIR_NAME/'Previews'


def own_files(folder):
    try:
        children = list(Path(folder).iterdir())
    except OSError:
        return []
    found = []
    for path in children:
        if any(pattern.fullmatch(path.name) for pattern in OWN_NAMES):
            try:
                stat = path.stat()
            except OSError:
                continue
            found.append((stat.st_mtime, stat.st_size, path))
    return found


def clean_folder(folder, now=None, limit=None):
    """Delete the stale previews of one folder, then the oldest above the limit. Returns the bytes freed."""
    now = time.time() if now is None else now
    freed = 0
    kept = []
    for mtime, size, path in own_files(folder):
        old = now-mtime > (TMP_AGE if path.suffix == '.tmp' else MAX_AGE)
        if old:
            try:
                path.unlink()
                freed += size
                continue
            except OSError:
                pass
        kept.append((mtime, size, path))
    if limit is not None:
        total = sum(size for _, size, _ in kept)
        for mtime, size, path in sorted(kept):
            if total <= limit:
                break
            try:
                path.unlink()
                total -= size
                freed += size
            except OSError:
                pass
    return freed


def clean_caches(project_roots, shared_folder=None, now=None):
    """Every cache Choyce knows of. A disk that is not plugged in is simply skipped, and caught up next time."""
    freed = 0
    seen = set()
    for root in project_roots:
        folder = preview_dir(root)
        key = str(folder).lower()
        if key in seen or not folder.is_dir():
            continue
        seen.add(key)
        freed += clean_folder(folder, now)
    if shared_folder is not None and Path(shared_folder).is_dir():
        freed += clean_folder(shared_folder, now, SHARED_LIMIT)
    return freed
