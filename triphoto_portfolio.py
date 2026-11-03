"""The portfolio: photos chosen across projects, kept as references to their originals.

It opens like any project, so its photos can be sorted again in every view. Nothing is
copied when a photo is added: the manifest next to its session remembers where each one
is, and files are only written by an export. Copies made by earlier versions stay in the
folder and keep being used. Originals are only ever read.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from PySide6.QtCore import QFile, QObject, QRunnable, QThreadPool, Qt, Signal
from triphoto import SESSION_DIR_NAME, Photo, PhotoSession, discover_photos, write_json_atomic

MANIFEST_FILE = 'portfolio.json'
UNSORTED = 'Sans projet'
# What a move to another location leaves behind: it is all made again when needed.
NOT_MOVED = {'previews', 'export_en_cours', 'exportation', 'export_5_etoiles', 'export_retenues'}


def source_key(path):
    """Without the drive letter: an external disk may come back under another one."""
    return os.path.normcase(os.path.splitdrive(os.path.abspath(str(path)))[1])


def project_folder_name(root):
    """Name of the subfolder of a project; "Output" says little, the folder above names the wedding."""
    root = Path(root)
    name = root.name or 'Sans nom'
    if name.lower() == 'output' and root.parent.name:
        name = root.parent.name
    return re.sub(r'[<>:"/\\|?*]', '-', name).strip(' .') or 'Sans nom'


def move_to_trash(path):
    # A plain answer or a pair with the new path, depending on the version of PySide.
    result = QFile.moveToTrash(str(path))
    return bool(result[0] if isinstance(result, tuple) else result)


def trash_patiently(path):
    """Under Windows, a preview being read holds the file for a moment."""
    for attempt in range(8):
        try:
            if move_to_trash(path):
                return
        except OSError:
            pass
        time.sleep(.4)
    raise OSError('la corbeille a refusé cette photo')


class TrashSignals(QObject):
    done = Signal(str, str)


class TrashJob(QRunnable):
    """A copy of the portfolio folder sent to the recycle bin. Never an original."""

    def __init__(self, destination, relative, signals):
        super().__init__()
        self.destination, self.relative, self.signals = destination, relative, signals

    def run(self):
        try:
            if self.destination.exists():
                trash_patiently(self.destination)
            try:
                self.destination.parent.rmdir()
            except OSError:
                pass
            self.signals.done.emit(self.relative, '')
        except Exception as error:
            self.signals.done.emit(self.relative, str(error))


class RelocateSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(str, str)


class RelocateJob(QRunnable):
    """Copy the portfolio elsewhere. The former folder stays as it is."""

    def __init__(self, old, new):
        super().__init__()
        self.old, self.new = old, new
        self.signals = RelocateSignals()

    def run(self):
        try:
            files = [path for path in self.old.rglob('*') if path.is_file() and not path.name.endswith('.part')
                     and not NOT_MOVED & {part.lower() for part in path.relative_to(self.old).parts}]
            for done, path in enumerate(files):
                self.signals.progress.emit(done, len(files))
                target = self.new/path.relative_to(self.old)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
            self.signals.finished.emit(str(self.new), '')
        except Exception as error:
            self.signals.finished.emit(str(self.new), str(error))


class PortfolioSession(PhotoSession):
    """The files of the folder, and the originals the portfolio refers to."""

    def __init__(self, root, portfolio):
        self.portfolio = portfolio
        self.absent = set()
        super().__init__(root)

    def discover(self):
        found = {photo.relative_path: photo for photo in discover_photos(self.root)}
        self.absent = set()
        for relative, item in self.portfolio.items.items():
            if relative not in found and item.get('linked'):
                source = Path(item.get('source', ''))
                if source.is_file():
                    found[relative] = Photo(path=source, relative_path=relative)
                else:
                    self.absent.add(relative)
        return list(found.values())

    def out_of_reach(self, relative_path):
        return relative_path in self.absent


class Portfolio(QObject):
    """What the portfolio holds. Adding and removing only change the list: the caller saves it once, whatever the number of photos."""
    changed = Signal()
    failed = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.items = {}
        self.sources = {}
        # Sent to the recycle bin, until it confirms.
        self.removed = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.signals = TrashSignals()
        self.signals.done.connect(self.trashed, Qt.ConnectionType.QueuedConnection)
        self.load()

    @property
    def folder(self):
        saved = self.settings.value('portfolio_folder', '')
        return Path(saved) if saved else None

    def available(self):
        return self.folder is not None and self.folder.is_dir()

    def manifest_path(self):
        return self.folder/SESSION_DIR_NAME/MANIFEST_FILE

    def set_folder(self, path):
        self.settings.setValue('portfolio_folder', str(path))
        self.load()
        self.changed.emit()

    def load(self):
        self.items = {}
        if self.folder is not None:
            try:
                data = json.loads(self.manifest_path().read_text(encoding='utf-8'))
                self.items = {key: item for key, item in data.get('items', {}).items() if isinstance(item, dict)}
            except (OSError, ValueError, AttributeError):
                pass
        self.sources = {source_key(item['source']): key for key, item in self.items.items() if item.get('source')}

    def save(self):
        try:
            self.manifest_path().parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.manifest_path(), {'version': 1, 'items': self.items})
        except OSError as error:
            self.failed.emit(f'Portfolio non enregistré : {error}')

    def reconcile(self):
        """A copy deleted by hand from the folder is no longer in the portfolio. An unplugged disk says nothing."""
        if not self.available():
            return
        gone = [key for key, item in self.items.items() if not item.get('linked') and not (self.folder/key).exists()]
        for key in gone:
            self.sources.pop(source_key(self.items.pop(key).get('source', '')), None)
        if gone:
            self.save()

    def contains(self, path):
        return source_key(path) in self.sources

    def unique_relative(self, project, name):
        stem, suffix = os.path.splitext(name)
        relative, number = f'{project}/{name}', 2
        while relative in self.items or (self.folder/relative).exists():
            relative, number = f'{project}/{stem} ({number}){suffix}', number+1
        return relative

    def add(self, photo, project_root):
        """Only a reference to the file; its notes in the portfolio start from those of the photo."""
        if self.contains(photo.path):
            return
        relative = self.unique_relative(project_folder_name(project_root), photo.path.name)
        self.items[relative] = {'source': str(photo.path), 'project': project_folder_name(project_root), 'added': time.time(), 'linked': True,
                                'seed': {'stars': photo.stars, 'pick': photo.pick, 'liked': photo.liked, 'publish': photo.publish}}
        self.sources[source_key(photo.path)] = relative

    def remove_source(self, path):
        relative = self.sources.get(source_key(path))
        if relative is not None:
            self.remove_relative(relative)

    def remove_relative(self, relative):
        """A reference is simply forgotten. A copy in the folder goes to the recycle bin: a mistake is never lost."""
        item = self.items.pop(relative, None)
        if item is not None:
            self.sources.pop(source_key(item.get('source', '')), None)
            if item.get('linked'):
                return
            self.removed[relative] = item
        self.pool.start(TrashJob(self.folder/relative, relative, self.signals))

    def trashed(self, relative, error):
        item = self.removed.pop(relative, None)
        if error:
            # Still in the folder, so still in the portfolio.
            if item is not None:
                self.items[relative] = item
                self.sources[source_key(item.get('source', ''))] = relative
                self.save()
            self.failed.emit(f'Photo laissée dans le portfolio : {error}')
        self.changed.emit()

    def project_of(self, relative):
        item = self.items.get(relative)
        if item and item.get('project'):
            return item['project']
        # Copied there by hand: the subfolder, when there is one, tells.
        parts = relative.split('/')
        return parts[0] if len(parts) > 1 else UNSORTED

    def apply_seeds(self, photos):
        """Notes brought along by the copies that are opened for the first time."""
        seeded = False
        for photo in photos:
            item = self.items.get(photo.relative_path)
            seed = item.pop('seed', None) if item else None
            if seed:
                photo.stars, photo.pick, photo.liked, photo.publish = seed['stars'], seed['pick'], seed['liked'], seed['publish']
                seeded = True
        if seeded:
            self.save()
        return seeded

    def summary(self):
        projects = {self.project_of(key) for key in self.items}
        return len(self.items), len(projects)

    def repoint(self, moves):
        """New places of originals, as a function from an old source to a new one or None."""
        changed = 0
        for item in self.items.values():
            new = moves(item.get('source', ''))
            if new:
                item['source'] = new
                changed += 1
        if changed:
            self.sources = {source_key(item['source']): key for key, item in self.items.items() if item.get('source')}
            self.save()
        return changed

    def file_of(self, relative):
        """Where a photo of the portfolio is read from. Touches no disk: one may be asleep or unplugged."""
        item = self.items.get(relative, {})
        return Path(item['source']) if item.get('linked') else self.folder/relative

    def covers(self, limit=6):
        """The latest additions."""
        return sorted(self.items, key=lambda key: self.items[key].get('added', 0), reverse=True)[:limit]

    def wait(self):
        self.pool.waitForDone()
