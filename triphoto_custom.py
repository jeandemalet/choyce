"""Custom: sessions that gather photos from several galleries and sort them into named buckets.

Each session is a small file in the data folder of the program. It holds where its photos
are and which bucket they went to: nothing moves on disk until the selection is exported,
as copies, in one folder per bucket. A photo may sit in several buckets, for several contests.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRectF, QRunnable, QSize, QStandardPaths, QThreadPool, QTimer, QVariantAnimation, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)
from triphoto import SESSION_DIR_NAME, SESSION_FILE_NAME, Photo, PhotoSession, export_folder_name, write_json_atomic
from triphoto_portfolio import PortfolioSession, move_to_trash, source_key
from triphoto_prefs import ask, inform
from triphoto_review import BackgroundPicker
from triphoto_theme import CONTROL, FAINT, INK, MUTED, RAISED, TEXT, contrast_on, draw_tick, pastel

PREVIEW_SIDE = 2000
DETAIL_SIDE = 6000
PREVIEWS_KEPT = 12
ALL, UNSORTED = 'all', 'none'
BUCKET_COLORS = ['#ff3b30', '#0a84ff', '#ffd60a', '#30d158', '#ff2d92', '#ff9500', '#00d4d8', '#bf5af2', '#a4e000', '#5e5ce6']


class Sorting:
    """One session: its photos, its buckets, and what went where."""

    def __init__(self, path, data):
        self.path, self.data = Path(path), data
        self.undo_stack = []

    @classmethod
    def create(cls, folder, name):
        key = uuid.uuid4().hex
        return cls(Path(folder)/f'{key}.json', {'version': 1, 'id': key, 'name': name, 'created': time.time(), 'updated': time.time(),
                                                'sources': [], 'photos': [], 'buckets': [], 'assigned': {}, 'position': ''})

    @classmethod
    def load(cls, path):
        """From its file or, if that one cannot be read, from the copy of its previous state."""
        try:
            session = cls(path, cls.read(path))
        except (OSError, ValueError, KeyError, TypeError):
            session = cls(path, cls.read(Path(str(path)+'.bak')))
        session.tidy()
        return session

    @staticmethod
    def read(path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        for key, kind in (('name', str), ('photos', list), ('buckets', list), ('assigned', dict)):
            if not isinstance(data[key], kind):
                raise ValueError(key)
        return data

    def tidy(self):
        """What an older version or a damaged file may have left: entries that lead nowhere go, the rest stays."""
        self.data['photos'] = [photo for photo in self.photos if isinstance(photo, dict) and photo.get('path')]
        self.data['buckets'] = [bucket for bucket in self.buckets if isinstance(bucket, dict) and bucket.get('id') and bucket.get('title')]
        known = {bucket['id'] for bucket in self.buckets}
        paths = {photo['path'] for photo in self.photos}
        assigned = {}
        for path, keys in self.data['assigned'].items():
            kept = [key for key in keys if key in known] if isinstance(keys, list) else []
            if kept and path in paths:
                assigned[path] = kept
        self.data['assigned'] = assigned
        # Buckets made before they had colors.
        for bucket in self.buckets:
            if not bucket.get('color'):
                bucket['color'] = self.free_color()

    def remove_photos(self, paths):
        """Out of the selection for good, buckets included, even if a gallery still holds them. The files are not touched."""
        paths = set(paths)
        self.data['photos'] = [photo for photo in self.photos if photo['path'] not in paths]
        self.data['assigned'] = {path: keys for path, keys in self.data['assigned'].items() if path not in paths}
        self.data['removed'] = sorted(set(self.data.get('removed', []))|paths)
        self.undo_stack.clear()

    def free_color(self, ignored=None):
        """The first color no other bucket has; with more buckets than colors, the least used."""
        used = [bucket.get('color') for bucket in self.buckets if bucket['id'] != ignored]
        return min(BUCKET_COLORS, key=used.count)

    def save(self):
        self.data['updated'] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, str(self.path)+'.bak')
        write_json_atomic(self.path, self.data)

    @property
    def name(self):
        return self.data['name']

    @property
    def buckets(self):
        return self.data['buckets']

    @property
    def photos(self):
        return self.data['photos']

    def bucket(self, key):
        return next((bucket for bucket in self.buckets if bucket['id'] == key), None)

    def buckets_of(self, path):
        return self.data['assigned'].get(path, [])

    def count(self, key):
        return sum(key in keys for keys in self.data['assigned'].values())

    def cover(self):
        """The photo of its card: the first one sorted, or the first one."""
        return next((photo['path'] for photo in self.photos if self.buckets_of(photo['path'])), self.photos[0]['path'] if self.photos else None)

    def order(self, key=ALL):
        """All the photos, or those of one bucket."""
        return self.photos if key == ALL else [photo for photo in self.photos if key in self.buckets_of(photo['path'])]

    def rule(self):
        """The filter on buckets: those required, those refused, whether one or all of the first are enough, or no bucket at all."""
        return {'include': [], 'exclude': [], 'all': False, 'none': False, **self.data.get('filter', {})}

    def set_rule(self, **changes):
        self.data['filter'] = {**self.rule(), **changes}

    def matches(self, path):
        rule = self.rule()
        keys = set(self.buckets_of(path))
        if rule['none']:
            return not keys
        if keys & set(rule['exclude']):
            return False
        required = set(rule['include'])
        return not required or (required <= keys if rule['all'] else bool(required & keys))

    def set_photos(self, photos):
        """A new choice of galleries. A photo already sorted stays: the work done is never lost to a filter."""
        chosen = {source_key(photo['path']) for photo in photos}
        sorted_before = [photo for photo in self.photos if self.buckets_of(photo['path']) and source_key(photo['path']) not in chosen]
        self.data['photos'] = photos+sorted_before

    def repoint(self, moves):
        """New places of photos, as a function from an old path to a new one or None. Nothing sorted is lost."""
        changed = {}
        for photo in self.photos:
            new = moves(photo['path'])
            if new:
                changed[photo['path']] = new
                photo['path'] = new
        if not changed:
            return 0
        self.data['assigned'] = {changed.get(path, path): keys for path, keys in self.data['assigned'].items()}
        self.data['position'] = changed.get(self.data.get('position'), self.data.get('position'))
        self.undo_stack.clear()
        # The notes of its views follow the same keys.
        notes = self.path.with_suffix('')/SESSION_DIR_NAME/SESSION_FILE_NAME
        try:
            saved = json.loads(notes.read_text(encoding='utf-8'))
            for item in saved.get('photos', []):
                item['relative_path'] = changed.get(item.get('relative_path'), item.get('relative_path'))
            write_json_atomic(notes, saved)
        except (OSError, ValueError):
            pass
        self.save()
        return len(changed)

    def _set(self, path, keys):
        self.undo_stack.append((path, list(self.buckets_of(path))))
        del self.undo_stack[:-300]
        if keys:
            self.data['assigned'][path] = keys
        else:
            self.data['assigned'].pop(path, None)

    def add(self, path, key):
        if key not in self.buckets_of(path):
            self._set(path, [*self.buckets_of(path), key])

    def toggle(self, path, key):
        keys = self.buckets_of(path)
        self._set(path, [other for other in keys if other != key] if key in keys else [*keys, key])

    def clear(self, path):
        if self.buckets_of(path):
            self._set(path, [])

    def move(self, path, key, source=None, duplicate=False):
        """To another bucket, out of the one it is seen in or out of all of them. Key None only takes it out."""
        if duplicate:
            self.add(path, key)
            return
        keys = self.buckets_of(path)
        kept = [other for other in keys if source and other != source]
        wanted = kept+([key] if key and key not in kept else [])
        if wanted != keys:
            self._set(path, wanted)

    def undo(self):
        """The photo it concerned, or None with nothing to take back."""
        if not self.undo_stack:
            return None
        path, keys = self.undo_stack.pop()
        if keys:
            self.data['assigned'][path] = keys
        else:
            self.data['assigned'].pop(path, None)
        return path

    def remove_bucket(self, key):
        self.data['buckets'] = [bucket for bucket in self.buckets if bucket['id'] != key]
        for path, keys in list(self.data['assigned'].items()):
            left = [other for other in keys if other != key]
            if left:
                self.data['assigned'][path] = left
            else:
                del self.data['assigned'][path]
        self.undo_stack.clear()
        rule = self.rule()
        self.set_rule(include=[other for other in rule['include'] if other != key], exclude=[other for other in rule['exclude'] if other != key])

    def export_plan(self):
        """(folder of the bucket, name of the file, path of the original). Two galleries may hold the same file name."""
        known = {photo['path'] for photo in self.photos}
        plan = []
        for bucket in self.buckets:
            used = set()
            for path, keys in self.data['assigned'].items():
                if bucket['id'] in keys and path in known:
                    stem, suffix = os.path.splitext(Path(path).name)
                    name, number = stem+suffix, 2
                    while name.lower() in used:
                        name, number = f'{stem} ({number}){suffix}', number+1
                    used.add(name.lower())
                    plan.append((export_folder_name(bucket['title']), name, path))
        return plan


class SelectionSession(PhotoSession):
    """A selection opened like a project, so that every view works on it. Its own folder only holds what the views keep."""

    def __init__(self, sorting):
        self.sorting = sorting
        self.absent = set()
        root = sorting.path.with_suffix('')
        root.mkdir(parents=True, exist_ok=True)
        # Shared between selections: the same photo is often in several.
        self.preview_dir = sorting.path.parent/'Previews'
        super().__init__(root)

    def discover(self):
        found, self.absent = [], set()
        for photo in self.sorting.photos:
            path = Path(photo['path'])
            if path.is_file():
                found.append(Photo(path=path, relative_path=photo['path']))
            else:
                self.absent.add(photo['path'])
        return found

    def out_of_reach(self, relative_path):
        return relative_path in self.absent


class BucketStrip(QWidget):
    """Under the views of a selection: a click puts the photo in a bucket or takes it out, a photo dragged here moves it or adds it to another one."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.dragging = False
        self.hovered = None
        self.held = set()
        self.mouse = None
        self.setMouseTracking(True)
        self.setFixedHeight(66)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hide()

    def keys(self):
        return [bucket['id'] for bucket in self.window.selection.buckets] if self.window.selection else []

    def rects(self):
        keys = self.keys()
        width = (self.width()-24-8*(len(keys)-1))/max(1, len(keys))
        return [(key, QRectF(12+index*(width+8), 8, width, self.height()-16)) for index, key in enumerate(keys)]

    def hit(self, point):
        """(bucket, what happens there): a click toggles; a drop moves, adds, or takes out of a bucket the photo is already in."""
        for key, rect in self.rects():
            if rect.contains(point):
                if not self.dragging:
                    return key, 'toggle'
                if key in self.held:
                    return key, 'remove'
                return key, 'add' if point.x() > rect.center().x() else 'move'
        return None

    def drag_to(self, point, photo):
        self.dragging = True
        self.held = set(self.window.selection.buckets_of(photo.relative_path))
        self.hovered = self.hit(QPointF(self.mapFromGlobal(point)))
        self.update()

    def finish_drag(self, point):
        hit = self.hit(QPointF(self.mapFromGlobal(point)))
        self.dragging, self.hovered = False, None
        self.update()
        return hit

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(INK))
        selection = self.window.selection
        if not selection:
            return
        targets = self.window.target_photos()
        held = set(selection.buckets_of(targets[0].relative_path)) if targets else set()
        font = painter.font()
        font.setPixelSize(13)
        painter.setFont(font)
        for key, rect in self.rects():
            bucket = selection.bucket(key) if key else None
            tone = pastel(bucket['color']) if bucket else QColor(MUTED)
            shown = key in held
            fill = QColor(tone)
            fill.setAlpha(64 if shown else 0)
            outline = QColor(tone)
            outline.setAlpha(255 if shown or (self.hovered and self.hovered[0] == key) else 90)
            painter.setBrush(QColor(RAISED))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 9, 9)
            painter.setBrush(fill)
            painter.setPen(QPen(outline, 2 if shown else 1.2))
            painter.drawRoundedRect(rect, 9, 9)
            inner = rect.adjusted(12, 0, -12, 0)
            under = self.mouse is not None and rect.contains(self.mouse)
            count = selection.count(key) if key else len(selection.photos)
            title = bucket['title'] if bucket else ''
            if self.dragging:
                self.paint_drop(painter, key, rect, tone, title)
            else:
                if shown:
                    draw_tick(painter, inner.left(), inner.center().y()-6, 12, TEXT)
                    inner = inner.adjusted(18, 0, 0, 0)
                hint = ('Retirer' if shown else 'Ajouter') if under else str(count)
                room = painter.fontMetrics().horizontalAdvance(hint)+10
                painter.setPen(QColor(TEXT if shown or under else MUTED))
                painter.drawText(inner.adjusted(0, 0, -room, 0), Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(inner.width()-room)))
                painter.setPen(QColor(TEXT if under else MUTED))
                painter.drawText(inner, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, hint)

    def paint_drop(self, painter, key, rect, tone, title):
        """Its name stays in place. Below it, what a drop does there, according to where the photo already is."""
        font = painter.font()
        painter.setPen(QColor(TEXT))
        painter.drawText(rect.adjusted(8, 5, -8, 0), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(rect.width()-16)))
        font.setPixelSize(12)
        painter.setFont(font)
        if key in self.held:
            parts = [(rect, 'Retirer du bucket', 'remove')]
        else:
            middle = rect.center().x()
            parts = [(QRectF(rect.left(), rect.top(), middle-rect.left(), rect.height()), 'Déplacer', 'move'),
                     (QRectF(middle, rect.top(), rect.right()-middle, rect.height()), 'Ajouter aussi', 'add')]
        for part, text, mode in parts:
            lit = self.hovered == (key, mode)
            if lit:
                veil = QColor(tone)
                veil.setAlpha(110)
                painter.setBrush(veil)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(part, 9, 9)
            painter.setPen(QColor(TEXT if lit else MUTED))
            painter.drawText(part.adjusted(6, 0, -6, -5), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, text)
        font.setPixelSize(13)
        painter.setFont(font)

    def mouseMoveEvent(self, event):
        self.mouse = event.position()
        self.update()

    def leaveEvent(self, event):
        self.mouse = None
        self.update()

    def mouseReleaseEvent(self, event):
        hit = self.hit(event.position())
        if event.button() == Qt.MouseButton.LeftButton and hit and hit[0]:
            self.window.toggle_bucket(hit[0])


class ReviewTabs(QFrame):
    """Which bucket is being reviewed, in one click, in every view of a selection."""

    def __init__(self, window):
        super().__init__(objectName='home')
        self.window = window
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(12, 6, 12, 0)
        self.row.setSpacing(2)
        self.tabs = {}
        self.hide()

    def rebuild(self):
        while self.row.count():
            widget = self.row.takeAt(0).widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.tabs = {}
        selection = self.window.selection
        if not selection:
            return
        self.row.addWidget(QLabel('Revoir', objectName='muted'))
        self.row.addSpacing(6)
        for key in [ALL]+[bucket['id'] for bucket in selection.buckets]+[UNSORTED]:
            button = QPushButton(objectName='seg', checkable=True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, value=key: self.show_only(value))
            self.row.addWidget(button)
            self.tabs[key] = button
        self.row.addStretch(1)
        self.sync()

    def sync(self):
        selection = self.window.selection
        if not selection:
            return
        # A bucket just added or removed.
        if set(self.tabs) != {ALL, UNSORTED}|{bucket['id'] for bucket in selection.buckets}:
            self.rebuild()
            return
        rule = selection.rule()
        simple = not rule['exclude'] and len(rule['include']) <= 1
        current = UNSORTED if rule['none'] else (rule['include'][0] if rule['include'] else ALL) if simple else None
        counts = {ALL: len(selection.photos), UNSORTED: sum(not selection.buckets_of(photo['path']) for photo in selection.photos)}
        for key, button in self.tabs.items():
            title = {ALL: 'Toutes', UNSORTED: 'Sans bucket'}.get(key) or selection.bucket(key)['title']
            count = counts[key] if key in counts else selection.count(key)
            button.setText(f"{title.replace('&', '&&')}  {count}")
            button.setChecked(key == current)

    def show_only(self, key):
        self.window.selection.set_rule(include=[] if key in (ALL, UNSORTED) else [key], exclude=[], none=key == UNSORTED, all=False)
        self.window.custom.save_timer.start()
        self.window.bucket_rule.sync()
        self.window.filters_changed()


class BucketFilter(QFrame):
    """The buckets in the Filtre panel: for each one, plainly, whether its photos are left out, kept in, or whether it does not matter."""
    STATES = (('exclude', 'Exclure', 'stateOff'), ('include', 'Inclure', 'stateOn'), ('', 'Peu importe', 'state'))

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.lines = QVBoxLayout(self)
        self.lines.setContentsMargins(0, 0, 0, 0)
        self.lines.setSpacing(4)
        self.buttons = {}
        self.counts = {}
        self.groups = []
        self.any_button = self.all_button = self.options = None
        self.hide()

    def clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.layout():
                self.clear_layout(item.layout())
            elif item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def rebuild(self):
        """Only for another list of buckets: a click on a choice changes it in place."""
        self.clear_layout(self.lines)
        for group in self.groups:
            group.deleteLater()
        self.buttons, self.counts, self.groups = {}, {}, []
        self.options = None
        selection = self.window.selection
        if not selection:
            return
        if not selection.buckets:
            self.lines.addWidget(QLabel('Aucun bucket pour le moment.', objectName='muted'))
            return
        for bucket in selection.buckets:
            key = bucket['id']
            head = QHBoxLayout()
            title = QLabel(bucket['title'])
            title.setWordWrap(True)
            title.setStyleSheet(f'color: {pastel(bucket["color"]).name()};')
            head.addWidget(title, 1)
            self.counts[key] = QLabel(objectName='faint')
            head.addWidget(self.counts[key])
            self.lines.addLayout(head)
            row = QHBoxLayout()
            row.setSpacing(2)
            self.buttons[key] = {}
            group = QButtonGroup(self)
            self.groups.append(group)
            for value, label, name in self.STATES:
                button = QPushButton(label, objectName=name, checkable=True)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                group.addButton(button)
                button.clicked.connect(lambda checked=False, k=key, v=value: self.set_state(k, v))
                row.addWidget(button)
                self.buttons[key][value] = button
            row.addStretch(1)
            self.lines.addLayout(row)
            self.lines.addSpacing(6)
        self.options = QWidget()
        lines = QVBoxLayout(self.options)
        lines.setContentsMargins(0, 2, 0, 0)
        lines.setSpacing(4)
        hint = QLabel('Avec plusieurs buckets inclus, afficher les photos', objectName='muted')
        hint.setWordWrap(True)
        lines.addWidget(hint)
        row = QHBoxLayout()
        row.setSpacing(4)
        lines.addLayout(row)
        self.any_button = self.choice(row, "dans l'un d'eux", False)
        self.all_button = self.choice(row, 'dans tous à la fois', True)
        row.addStretch(1)
        self.lines.addWidget(self.options)
        self.sync()

    def choice(self, row, text, value):
        button = QPushButton(text, objectName='seg', checkable=True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda *_: self.change(all=value))
        row.addWidget(button)
        return button

    def sync(self):
        selection = self.window.selection
        if not selection or not self.options:
            return
        rule = selection.rule()
        for key, buttons in self.buttons.items():
            state = 'include' if key in rule['include'] else 'exclude' if key in rule['exclude'] else ''
            buttons[state].setChecked(True)
            self.counts[key].setText(str(selection.count(key)))
        self.any_button.setChecked(not rule['all'])
        self.all_button.setChecked(bool(rule['all']))
        self.options.setVisible(len(rule['include']) > 1)

    def set_state(self, key, value):
        rule = self.window.selection.rule()
        include = [other for other in rule['include'] if other != key]
        exclude = [other for other in rule['exclude'] if other != key]
        if value:
            (include if value == 'include' else exclude).append(key)
        self.change(include=include, exclude=exclude, none=False)

    def change(self, **changes):
        self.window.selection.set_rule(**changes)
        self.window.custom.save_timer.start()
        self.window.filters_changed()
        self.sync()

    def labels(self):
        selection = self.window.selection
        rule = selection.rule()
        if rule['none']:
            return ['Sans bucket']
        title = lambda key: selection.bucket(key)['title']
        joined = (' et ' if rule['all'] else ' ou ').join(title(key) for key in rule['include'])
        return ([joined] if joined else [])+[f'sans {title(key)}' for key in rule['exclude']]


class Store:
    """The sessions saved on this computer."""

    def __init__(self, scope):
        base = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation) or Path.home())
        self.folder = base/'Vision Board'/scope/'custom'

    def sessions(self):
        found = []
        for path in self.folder.glob('*.json'):
            try:
                found.append(Sorting.load(path))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
        return sorted(found, key=lambda session: session.data.get('updated', 0), reverse=True)

    def create(self, name):
        return Sorting.create(self.folder, name)


class ExportSignals(QObject):
    progress = Signal(int, int)
    checking = Signal()
    finished = Signal(str, str)


class ExportJob(QRunnable):
    """Copies in a work folder first, so that an interrupted export leaves nothing behind. Never over an earlier export."""

    def __init__(self, plan, destination):
        super().__init__()
        self.plan, self.destination = plan, destination
        self.signals = ExportSignals()
        self.cancelled = threading.Event()
        # What could not be copied: one bad file must not cost all the others.
        self.skipped = []

    def run(self):
        work = self.destination.with_name(self.destination.name+'.en_cours')
        try:
            shutil.rmtree(work, ignore_errors=True)
            total = len(self.plan)
            for done, (folder, name, source) in enumerate(self.plan):
                if self.cancelled.is_set():
                    shutil.rmtree(work, ignore_errors=True)
                    self.signals.finished.emit('', '')
                    return
                self.signals.progress.emit(done, total)
                try:
                    if not os.path.getsize(source):
                        raise OSError('fichier vide')
                    (work/folder).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, work/folder/name)
                except OSError:
                    self.skipped.append(source)
            self.signals.progress.emit(total, total)
            if not work.exists():
                raise RuntimeError('Aucune photo n’a pu être copiée.')
            os.replace(work, self.destination)
            self.signals.finished.emit(str(self.destination), '')
        except Exception as error:
            shutil.rmtree(work, ignore_errors=True)
            self.signals.finished.emit('', str(error))


def photo_problem(path):
    """Why a photo cannot be used, in a few words, or None. Decodes it small, which also finds a truncated file."""
    try:
        if not os.path.getsize(path):
            return 'fichier vide'
        with Image.open(path) as image:
            image.draft('RGB', (64, 64))
            image.load()
    except FileNotFoundError:
        return 'introuvable'
    except OSError as error:
        return 'introuvable' if not os.path.exists(path) else 'illisible'
    except Exception:
        return 'illisible'
    return None


class CheckSignals(QObject):
    progress = Signal(int)
    finished = Signal(dict)


class CheckJob(QRunnable):
    """Goes through the photos of a selection, off the interface."""

    def __init__(self, paths):
        super().__init__()
        self.paths = paths
        self.signals = CheckSignals()
        self.cancelled = threading.Event()

    def run(self):
        problems = {}
        for index, path in enumerate(self.paths):
            if self.cancelled.is_set():
                return
            problem = photo_problem(path)
            if problem:
                problems[path] = problem
            if index % 8 == 0:
                self.signals.progress.emit(len(self.paths)-index)
        self.signals.finished.emit(problems)


class SourceSignals(QObject):
    done = Signal(str, object)


class SourceJob(QRunnable):
    """Reads a gallery off the interface thread: a sleeping or unplugged disk must not freeze it."""

    def __init__(self, key, path, portfolio, signals):
        super().__init__()
        self.key, self.path, self.portfolio, self.signals = key, path, portfolio, signals

    def run(self):
        try:
            session = PortfolioSession(self.path, self.portfolio) if self.portfolio else PhotoSession(self.path)
            self.signals.done.emit(self.key, session.photos)
        except Exception:
            self.signals.done.emit(self.key, None)


class SourceRow(QFrame):
    """A gallery with its filters. Greyed out while it reads, and for good when its disk is away."""
    changed = Signal()

    def __init__(self, page, key, title, place, portfolio=False):
        super().__init__(objectName='customRow')
        self.page, self.key, self.title, self.portfolio = page, key, title, portfolio
        self.photos = None
        self.missing = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
        self.check = QCheckBox()
        self.check.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.check.setFixedWidth(26)
        self.check.toggled.connect(lambda *_: self.changed.emit())
        row.addWidget(self.check)
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(QLabel(title))
        where = QLabel(place, objectName='faint')
        where.setMaximumWidth(460)
        names.addWidget(where)
        row.addLayout(names, 1)
        window = page.window
        self.stars = page.copy_combo(window.stars)
        self.marker = page.copy_combo(window.flags)
        for combo in (self.stars, self.marker):
            combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
            row.addWidget(combo)
        self.publish = QPushButton('À publier', objectName='seg', checkable=True)
        self.publish.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.publish.clicked.connect(lambda *_: self.changed.emit())
        row.addWidget(self.publish)
        self.count = QLabel('Lecture…', objectName='muted')
        self.count.setFixedWidth(116)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.count)
        self.repair = QPushButton('Retrouver…', objectName='ghost')
        self.repair.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.repair.clicked.connect(lambda *_: page.setup.repair_row(self))
        self.repair.hide()
        row.addWidget(self.repair)

    def mouseReleaseEvent(self, event):
        # Anywhere on the row: the filters keep their own clicks.
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()) and not self.missing:
            self.check.toggle()

    def apply(self, source):
        self.check.setChecked(True)
        self.stars.setCurrentIndex(max(0, self.stars.findData(source.get('stars', 'all'))))
        self.marker.setCurrentIndex(max(0, self.marker.findData(source.get('marker', 'all'))))
        self.publish.setChecked(bool(source.get('publish')))

    def source(self):
        return {'key': self.key, 'stars': self.stars.currentData(), 'marker': self.marker.currentData(), 'publish': self.publish.isChecked()}

    def matching(self):
        return [photo for photo in self.photos or [] if self.page.window.matches_filters(photo, self.stars.currentData(), self.marker.currentData(), self.publish.isChecked())]

    def loaded(self, photos):
        self.photos = photos
        self.missing = photos is None
        if self.missing:
            self.check.setChecked(False)
        self.check.setEnabled(not self.missing)
        self.repair.setVisible(self.missing and not self.portfolio)
        self.refresh()
        self.changed.emit()

    def refresh(self):
        if self.missing:
            self.count.setText('Introuvable')
        elif self.photos is not None:
            matching, total = len(self.matching()), len(self.photos)
            self.count.setText(f'{matching} sur {total}' if matching != total else f"{total} photo{'s' if total > 1 else ''}")


class SetupPage(QWidget):
    """Which galleries a session takes its photos from, and through which filters."""
    finished = Signal(object)

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.window = page.window
        self.session = None
        self.rows = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = SourceSignals()
        self.signals.done.connect(self.source_read, Qt.ConnectionType.QueuedConnection)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 26, 40, 22)
        layout.setSpacing(12)
        self.title = QLabel(objectName='dialogTitle')
        layout.addWidget(self.title)
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText('Nom de la sélection, par exemple Concours 2027')
        self.name.textChanged.connect(lambda *_: self.refresh())
        layout.addWidget(self.name)
        quick = QHBoxLayout()
        quick.setSpacing(4)
        quick.addWidget(QLabel('Galeries', objectName='section'))
        quick.addSpacing(8)
        for text, action in (('Toutes les galeries', self.select_all), ('Seulement le portfolio', self.select_portfolio), ('Aucune', self.select_none)):
            button = QPushButton(text, objectName='ghost')
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, run=action: run())
            quick.addWidget(button)
        quick.addStretch(1)
        add = QPushButton('Ajouter un dossier…', objectName='ghost')
        add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        add.clicked.connect(lambda *_: self.add_folder())
        quick.addWidget(add)
        layout.addLayout(quick)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget(objectName='home')
        self.list = QVBoxLayout(holder)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(4)
        self.list.addStretch(1)
        area.setWidget(holder)
        layout.addWidget(area, 1)
        bottom = QHBoxLayout()
        self.total = QLabel(objectName='muted')
        bottom.addWidget(self.total, 1)
        cancel = QPushButton('Annuler', objectName='ghost')
        cancel.clicked.connect(lambda *_: self.page.show_after_setup(None))
        bottom.addWidget(cancel)
        self.go = QPushButton(objectName='primary')
        self.go.clicked.connect(lambda *_: self.confirm())
        bottom.addWidget(self.go)
        layout.addLayout(bottom)

    def scanning(self):
        return sum(row.photos is None and not row.missing for row in self.rows.values())

    def add_row(self, key, title, place, portfolio=False):
        row = SourceRow(self.page, key, title, place, portfolio)
        row.changed.connect(lambda: (row.refresh(), self.refresh()))
        self.rows[key] = row
        self.list.insertWidget(self.list.count()-1, row)
        self.pool.start(SourceJob(key, Path(key), self.window.portfolio if portfolio else None, self.signals))
        return row

    def open(self, session):
        """Every gallery known on this computer. A new session starts with none of them chosen."""
        self.session = session
        for row in self.rows.values():
            self.list.removeWidget(row)
            row.deleteLater()
        self.rows = {}
        self.title.setText('Nouvelle sélection custom' if session is None else 'Modifier la sélection')
        self.go.setText('Créer la sélection' if session is None else 'Enregistrer')
        self.name.setText('' if session is None else session.name)
        portfolio = self.window.portfolio
        if portfolio.available():
            self.add_row(str(portfolio.folder), 'Portfolio', 'Vos meilleures photos, tous projets confondus', True)
        for entry in self.window.recents.entries():
            if not self.window.is_portfolio_path(entry['path']):
                self.add_row(entry['path'], self.page.project_label(entry['path']), entry['path'])
        if session is not None:
            for source in session.data['sources']:
                row = self.rows.get(source['key']) or self.add_row(source['key'], self.page.project_label(source['key']), source['key'])
                row.apply(source)
        self.refresh()

    def source_read(self, key, photos):
        if key in self.rows:
            self.rows[key].loaded(photos)

    def select_all(self):
        for row in self.rows.values():
            row.check.setChecked(not row.missing)

    def select_none(self):
        for row in self.rows.values():
            row.check.setChecked(False)

    def select_portfolio(self):
        for row in self.rows.values():
            row.check.setChecked(row.portfolio and not row.missing)

    def repair_row(self, row):
        if self.window.repair_recent(row.key):
            self.open(self.session)

    def add_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, 'Choisir un dossier de photos')
        if chosen and str(Path(chosen)) not in self.rows:
            self.add_row(str(Path(chosen)), self.page.project_label(chosen), str(Path(chosen))).check.setChecked(True)

    def chosen(self):
        """The photos of the checked galleries through their filters, each one once even when two galleries hold it."""
        seen, photos = set(), []
        items = self.window.portfolio.items
        removed = set(self.session.data.get('removed', [])) if self.session else set()
        for row in self.rows.values():
            if row.check.isChecked():
                for photo in row.matching():
                    # A copy of the portfolio is the same photo as its original.
                    origin = items.get(photo.relative_path, {}).get('source') if row.portfolio else None
                    key = source_key(origin or photo.path)
                    if key not in seen and str(photo.path) not in removed:
                        seen.add(key)
                        photos.append({'path': str(photo.path), 'gallery': row.title, 'relative': photo.relative_path})
        return photos

    def refresh(self):
        reading = any(row.check.isChecked() and row.photos is None and not row.missing for row in self.rows.values())
        count = len(self.chosen())
        self.total.setText('Lecture des galeries…' if reading else f"{count} photo{'s' if count > 1 else ''} sélectionnée{'s' if count > 1 else ''}")
        self.go.setEnabled(not reading and bool(count or self.session) and bool(self.name.text().strip()))

    def confirm(self):
        session = self.session or self.page.store.create(self.name.text().strip())
        session.data['name'] = self.name.text().strip()
        session.data['sources'] = [row.source() for row in self.rows.values() if row.check.isChecked()]
        session.set_photos(self.chosen())
        session.save()
        self.page.show_after_setup(session)


class ColorChoice(QPushButton):
    def __init__(self, color):
        super().__init__(objectName='colorChoice', checkable=True, autoExclusive=True)
        self.color = color
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect())
        if self.isChecked():
            painter.setPen(QPen(QColor(TEXT), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(area.adjusted(1, 1, -1, -1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pastel(self.color))
        painter.drawEllipse(area.adjusted(6, 6, -6, -6))


class BucketDialog(QDialog):
    """A title, which names the export folder, a description if it helps, and its color."""

    def __init__(self, parent, taken, title='', subtitle='', color=BUCKET_COLORS[0]):
        super().__init__(parent)
        self.taken = taken
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(objectName='dialog')
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)
        layout.addWidget(QLabel('Modifier le bucket' if title else 'Nouveau bucket', objectName='dialogTitle'))
        self.title = QLineEdit(title)
        self.title.setMaxLength(80)
        self.title.setMinimumWidth(400)
        self.title.setPlaceholderText('Titre, par exemple Concours Fearless')
        self.title.textChanged.connect(self.refresh)
        layout.addWidget(self.title)
        self.subtitle = QLineEdit(subtitle)
        self.subtitle.setMaxLength(160)
        self.subtitle.setPlaceholderText('Description, facultative')
        layout.addWidget(self.subtitle)
        colors = QHBoxLayout()
        colors.setSpacing(2)
        self.colors = [ColorChoice(value) for value in BUCKET_COLORS]
        for choice in self.colors:
            choice.setChecked(choice.color == color)
            colors.addWidget(choice)
        colors.addStretch(1)
        layout.addLayout(colors)
        self.note = QLabel(objectName='faint')
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('Annuler', objectName='ghost')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.go = QPushButton('Enregistrer', objectName='primary')
        self.go.clicked.connect(self.accept)
        buttons.addWidget(self.go)
        layout.addLayout(buttons)
        self.refresh()

    def color(self):
        return next(choice.color for choice in self.colors if choice.isChecked())

    def refresh(self, *args):
        folder = export_folder_name(self.title.text())
        clash = folder.lower() in self.taken
        self.note.setText('Un autre bucket porte déjà ce nom.' if clash else f"Dossier à l'export : {folder}" if folder else "Son titre nomme son dossier à l'export.")
        self.go.setEnabled(bool(folder) and not clash)


class BucketCard(QWidget):
    """A large drop zone, in the color of its bucket. Without a key, it offers to create the first one."""

    def __init__(self, page, key):
        super().__init__()
        self.page, self.key = page, key
        self.hovered = False
        self.setMinimumSize(190, 118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def bucket(self):
        return self.page.session.bucket(self.key) if self.key else None

    def dots(self):
        return QRectF(self.width()-40, 6, 34, 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bucket = self.bucket()
        holds = bool(self.key and self.key in self.page.current_buckets())
        tone = pastel(bucket['color']) if bucket else QColor(FAINT)
        painter.setBrush(QColor(RAISED if bucket else CONTROL))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(area, 10, 10)
        # Only the outline at rest. The photo being in the bucket, or over it, adds a veil of the same tone.
        fill = QColor(tone)
        fill.setAlpha(70 if self.hovered else 56 if holds else 0)
        outline = QColor(tone)
        outline.setAlpha(255 if self.hovered or holds else 120)
        painter.setBrush(fill)
        painter.setPen(QPen(outline, 2 if self.hovered or holds else 1.2, Qt.PenStyle.SolidLine if bucket else Qt.PenStyle.DashLine))
        painter.drawRoundedRect(area, 10, 10)
        if bucket:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(MUTED))
            for offset in (-6, 0, 6):
                painter.drawEllipse(self.dots().center()+QPointF(offset, 0), 1.7, 1.7)
        title = bucket['title'] if bucket else 'Ajouter un bucket'
        subtitle = bucket.get('subtitle', '') if bucket else 'Les buckets se placent ici : glissez-y les photos.'
        inner = area.adjusted(14, 12, -14, -10)
        heading = inner.adjusted(0, 0, -28, 0)
        if holds:
            draw_tick(painter, heading.left(), heading.top()+3, 12, TEXT)
            heading = heading.adjusted(18, 0, 0, 0)
        font = painter.font()
        font.setPixelSize(15)
        font.setWeight(font.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(TEXT))
        painter.drawText(heading, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, int(heading.width())))
        font.setPixelSize(12)
        font.setWeight(font.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(MUTED))
        painter.drawText(inner.adjusted(0, 24, 0, -22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, subtitle)
        painter.setPen(QColor(FAINT))
        if self.key:
            count = self.page.session.count(self.key)
            number = self.page.bucket_number(self.key)
            painter.drawText(inner, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, f"{count} photo{'s' if count > 1 else ''}")
            if number:
                painter.drawText(inner, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, f'Touche {number}')

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self.rect().contains(event.position().toPoint()):
            return
        if not self.key:
            self.page.add_bucket()
        elif self.dots().contains(event.position()):
            self.show_menu(self.mapToGlobal(self.dots().topLeft().toPoint()))
        else:
            self.page.clicked_card(self.key)

    def show_menu(self, where):
        if self.key:
            key, page = self.key, self.page
            PopupMenu(page.window, [('Voir les photos…', lambda: page.show_bucket(key)), ('Modifier…', lambda: page.edit_bucket(key)),
                                    ('Supprimer…', lambda: page.delete_bucket(key))]).open_at(where)

    def contextMenuEvent(self, event):
        self.show_menu(event.globalPos())


class Stage(QWidget):
    """The ribbon: one photo in the center, the next one sliding in from the side."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.press = None
        self.dragging = False
        self.leaving = None
        self.direction = 0
        self.progress = 1.
        # They stay from one photo to the next, as in the other views.
        self.zoom = 1.
        self.pan = QPointF()
        self.hover = False
        self.setMouseTracking(True)
        self.slide = QVariantAnimation(self)
        self.slide.setDuration(170)
        self.slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.slide.setStartValue(0.)
        self.slide.setEndValue(1.)
        self.slide.valueChanged.connect(self.slid)

    def slid(self, value):
        self.progress = value
        self.update()

    def move_to(self, leaving, direction):
        self.leaving, self.direction = leaving, direction
        self.progress = 0.
        self.slide.stop()
        self.slide.start()

    def zoom_by(self, factor):
        """What is in the middle of the view stays there, wherever the mouse is."""
        previous = self.zoom
        self.zoom = max(.1, min(8., previous*factor))
        self.pan *= self.zoom/previous
        self.page.zoom_changed()
        self.update()

    def zoom_reset(self):
        self.zoom, self.pan = 1., QPointF()
        self.page.zoom_changed()
        self.update()

    def wheelEvent(self, event):
        self.zoom_by(1.12 if event.angleDelta().y() > 0 else 1/1.12)
        event.accept()

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def contextMenuEvent(self, event):
        self.page.window.viewer.background_menu(self).exec(event.globalPos())

    def arrows(self):
        return QRectF(14, self.height()/2-22, 44, 44), QRectF(self.width()-58, self.height()/2-22, 44, 44)

    def room(self):
        return QRectF(self.rect()).adjusted(12, 12, -12, -12)

    def fitted(self, image, room):
        scale = min(room.width()/image.width(), room.height()/image.height())*self.zoom
        width, height = image.width()*scale, image.height()*scale
        return QRectF(room.center().x()-width/2+self.pan.x(), room.center().y()-height/2+self.pan.y(), width, height)

    def photo_rect(self):
        image = self.page.current_image()
        return self.fitted(image, self.room()) if image is not None else self.room()

    def paint_image(self, painter, image, shift):
        target = self.fitted(image, self.room()).translated(shift, 0)
        painter.drawImage(target, image)

    def paintEvent(self, event):
        painter = QPainter(self)
        background = self.page.window.viewer.background
        painter.fillRect(self.rect(), QColor(background))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hint = QColor(contrast_on(background))
        photo = self.page.current()
        if photo is None:
            painter.setPen(hint)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.page.empty_text())
            return
        travel = self.width()*(1-self.progress)*self.direction
        if self.leaving is not None and self.progress < 1:
            self.paint_image(painter, self.leaving, travel-self.width()*self.direction)
        image = self.page.current_image()
        if image is not None and not self.dragging:
            self.paint_image(painter, image, travel)
        elif image is None:
            painter.setPen(hint)
            problem = self.page.failed.get(photo['path'])
            painter.drawText(self.room(), Qt.AlignmentFlag.AlignCenter, 'Fichier introuvable pour le moment' if problem is not None else 'Chargement…')
        if not self.hover or self.dragging:
            return
        position, total = self.page.position()
        for rect, shown, sign in zip(self.arrows(), (position > 0, position < total-1), (-1, 1)):
            if shown:
                # Discreet, and only under the mouse: the same dark plate as the other controls on a photo.
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(15, 15, 16, 150))
                painter.drawEllipse(rect)
                path = QPainterPath(QPointF(rect.center().x()-sign*4, rect.center().y()-8))
                path.lineTo(rect.center().x()+sign*4, rect.center().y())
                path.lineTo(rect.center().x()-sign*4, rect.center().y()+8)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(TEXT), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawPath(path)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        back, forward = self.arrows()
        if back.contains(event.position()):
            self.page.navigate(-1)
        elif forward.contains(event.position()):
            self.page.navigate(1)
        elif self.page.current_image() is not None and self.photo_rect().contains(event.position()):
            self.press = event.position()

    def mouseMoveEvent(self, event):
        if self.press is None:
            over_photo = self.page.current_image() is not None and self.photo_rect().contains(event.position())
            self.setCursor(Qt.CursorShape.OpenHandCursor if over_photo else Qt.CursorShape.ArrowCursor)
            return
        if self.zoom > 1 and not self.dragging and event.position().y() < self.height():
            # Zoomed in, the hand moves the photo; it takes it to a bucket on leaving the stage downward.
            self.pan += event.position()-self.press
            self.press = event.position()
            self.update()
            return
        if not self.dragging and (event.position()-self.press).manhattanLength() > 10:
            self.dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.page.start_drag()
            self.update()
        if self.dragging:
            self.page.drag_to(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event):
        dragging, self.dragging, self.press = self.dragging, False, None
        if dragging:
            self.unsetCursor()
            self.page.drop_at(event.globalPosition().toPoint())
            self.update()


class PopupMenu(QFrame):
    """A short list of actions in the design of the program, rather than the system's menu."""

    def __init__(self, parent, actions):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(objectName='dialog')
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(1)
        for text, action in actions:
            button = QPushButton(text, objectName='ghost')
            button.setStyleSheet('text-align: left; padding: 8px 18px 8px 10px;')
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, run=action: self.choose(run))
            layout.addWidget(button)

    def choose(self, action):
        self.close()
        # Once this list is gone: the action may open a dialog.
        QTimer.singleShot(0, action)

    def open_at(self, anchor):
        """Above the point when there is room, as the buckets sit at the bottom of the window, and always inside it."""
        self.adjustSize()
        area = self.parentWidget().frameGeometry()
        y = anchor.y()-self.height()-4
        x = max(area.left()+8, min(anchor.x(), area.right()-self.width()-8))
        self.move(x, y if y >= area.top() else anchor.y()+4)
        self.show()


class Tile(QWidget):
    """A photo of a bucket, with what takes it out. Asks for its image once it is on screen."""
    SIZE = QSize(226, 196)

    def __init__(self, sheet, photo):
        super().__init__()
        self.sheet, self.photo = sheet, photo
        self.setFixedSize(self.SIZE)
        self.setMouseTracking(True)

    def photo_area(self):
        """Where the photo itself lies, so that its cross stays on it whatever its format."""
        area = QRectF(0, 0, self.width(), self.height()-24)
        image = self.sheet.images.get(self.photo['path'])
        if image is None:
            return area
        scale = min(area.width()/image.width(), area.height()/image.height())
        width, height = image.width()*scale, image.height()*scale
        return QRectF((area.width()-width)/2, (area.height()-height)/2, width, height)

    def remove_rect(self):
        area = self.photo_area()
        return QRectF(area.right()-34, area.top()+6, 28, 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = QRectF(0, 0, self.width(), self.height()-24)
        clip = QPainterPath()
        clip.addRoundedRect(area, 7, 7)
        painter.fillPath(clip, QColor(RAISED))
        image = self.sheet.image(self.photo['path'])
        if self.photo['path'] in self.sheet.broken:
            painter.setPen(QColor(MUTED))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, 'Fichier introuvable\nou illisible')
        if image is not None:
            painter.setClipPath(clip)
            painter.drawImage(self.photo_area(), image)
            painter.setClipping(False)
        painter.setPen(QColor(MUTED))
        painter.drawText(QRectF(2, area.bottom()+4, self.width()-4, 18), Qt.AlignmentFlag.AlignLeft, painter.fontMetrics().elidedText(Path(self.photo['path']).name, Qt.TextElideMode.ElideMiddle, self.width()-4))
        button = self.remove_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 15, 16, 205))
        painter.drawEllipse(button)
        painter.setPen(QPen(QColor(TEXT), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        center = button.center()
        for x, y in ((-4.5, -4.5), (-4.5, 4.5)):
            painter.drawLine(center+QPointF(x, y), center-QPointF(x, y))

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.remove_rect().contains(event.position()) else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.remove_rect().contains(event.position()):
            self.sheet.remove(self.photo['path'])


class BucketSheet(QDialog):
    """All the photos of a bucket, large, to take out those that went there by mistake. Nothing moves on disk."""

    def __init__(self, page, key):
        super().__init__(page.window)
        self.page, self.key = page, key
        self.session = page.session
        self.images = {}
        self.asked = set()
        self.broken = set()
        self.removed = []
        self.tiles = []
        self.columns = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        window = page.window
        self.resize(int(window.width()*.9), int(window.height()*.9))
        self.move(window.mapToGlobal(window.rect().center())-self.rect().center())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(objectName='dialog')
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)
        bucket = self.session.bucket(key)
        top = QHBoxLayout()
        top.addWidget(QLabel(bucket['title'], objectName='dialogTitle'))
        self.count = QLabel(objectName='muted')
        top.addSpacing(8)
        top.addWidget(self.count)
        top.addStretch(1)
        self.undo_button = QPushButton('Rétablir la dernière', objectName='ghost')
        self.undo_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.undo_button.clicked.connect(lambda *_: self.restore())
        top.addWidget(self.undo_button)
        close = QPushButton('Fermer', objectName='primary')
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.clicked.connect(self.accept)
        top.addWidget(close)
        layout.addLayout(top)
        if bucket.get('subtitle'):
            layout.addWidget(QLabel(bucket['subtitle'], objectName='muted'))
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget(objectName='dialogBody')
        holder.setStyleSheet('background: transparent;')
        self.grid = QGridLayout(holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.area.setWidget(holder)
        layout.addWidget(self.area, 1)
        self.rebuild()

    def rebuild(self):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            widget.setParent(None)
            widget.deleteLater()
        self.tiles = [Tile(self, photo) for photo in self.session.order(self.key)]
        self.columns = 0
        self.arrange()
        count = len(self.tiles)
        self.count.setText(f"{count} photo{'s' if count > 1 else ''}")
        self.undo_button.setVisible(bool(self.removed))

    def arrange(self):
        columns = max(1, (self.area.viewport().width()+12)//(Tile.SIZE.width()+12))
        if columns != self.columns:
            self.columns = columns
            for index, tile in enumerate(self.tiles):
                self.grid.addWidget(tile, index//columns, index % columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()

    def showEvent(self, event):
        super().showEvent(event)
        self.arrange()

    def image(self, path):
        image = self.images.get(path)
        if image is None and path not in self.asked:
            self.asked.add(path)
            job = self.page.window.custom_loader(Path(path), 400)
            job.signals.ready.connect(self.loaded, Qt.ConnectionType.QueuedConnection)
            self.pool.start(job)
        return image

    @Slot(str, int, QImage, str, int)
    def loaded(self, path, side, image, error, generation):
        if image.isNull():
            self.broken.add(path)
        else:
            self.images[path] = image
        for tile in self.tiles:
            if tile.photo['path'] == path:
                tile.update()

    def remove(self, path):
        self.session.toggle(path, self.key)
        self.removed.append(path)
        self.rebuild()
        self.page.changed()

    def restore(self):
        if self.removed:
            self.session.add(self.removed.pop(), self.key)
            self.rebuild()
            self.page.changed()

    def done(self, result):
        self.pool.clear()
        self.pool.waitForDone()
        super().done(result)


class CustomPage(QStackedWidget):
    """The sessions, the choice of galleries, then the sorting itself."""

    def __init__(self, window, scope, project_label):
        super().__init__()
        self.window = window
        self.project_label = project_label
        self.store = Store(scope)
        self.session = None
        self.index = 0
        self.images = OrderedDict()
        self.detailed = set()
        self.pending = {}
        self.failed = {}
        self.cards = {}
        self.checking = None
        self.check_left = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(self.save)
        self.cached_order = None
        self.setup = SetupPage(self)
        self.work = self.build_work()
        for page in (self.setup, self.work):
            self.addWidget(page)
        self.ghost = QLabel(self)
        self.ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.ghost.hide()

    def copy_combo(self, source):
        combo = QComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for index in range(source.count()):
            combo.addItem(source.itemText(index), source.itemData(index))
        return combo

    def plain_button(self, row, text, action, name='ghost'):
        button = QPushButton(text, objectName=name)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda checked=False: action())
        row.addWidget(button)
        return button

    def build_work(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(8)
        self.work_title = QLabel(objectName='section')
        bar.addWidget(self.work_title)
        bar.addSpacing(12)
        self.counter = QLabel(objectName='muted')
        bar.addWidget(self.counter)
        bar.addSpacing(8)
        self.photo_name = QLabel(objectName='muted')
        bar.addWidget(self.photo_name, 1)
        self.zoom_button = self.plain_button(bar, '100 %', lambda: self.stage.zoom_reset())
        self.zoom_button.setToolTip('Revenir à la photo ajustée (touche 0)')
        self.picker = BackgroundPicker(self.window, page)
        bar.addWidget(self.picker)
        bar.addSpacing(8)
        self.plain_button(bar, 'Modifier la sélection', self.edit_selection)
        self.plain_button(bar, 'Ajouter un bucket', self.add_bucket)
        self.export_button = self.plain_button(bar, 'Exporter la sélection…', self.export, 'primary')
        layout.addLayout(bar)
        self.stage = Stage(self)
        layout.addWidget(self.stage, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFixedHeight(146)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget(objectName='home')
        self.card_row = QHBoxLayout(holder)
        self.card_row.setContentsMargins(12, 10, 12, 10)
        self.card_row.setSpacing(10)
        scroll.setWidget(holder)
        layout.addWidget(scroll)
        return page

    # Opening and leaving. The list of the selections is on the welcome screen.
    def new_session(self):
        self.session = None
        self.setup.open(None)
        self.setCurrentWidget(self.setup)

    def show_after_setup(self, session):
        if session is not None and session is self.session:
            self.window.selection_changed()
            self.setCurrentWidget(self.work)
            self.show_photo()
        elif session is not None:
            self.window.open_custom(session)
        elif self.session is not None:
            self.setCurrentWidget(self.work)
        else:
            self.window.leave_custom()

    def close_session(self):
        self.save()
        if self.checking:
            self.checking.cancelled.set()
            self.checking = None
            self.check_left = 0
        self.session = None
        self.cached_order = None
        self.images.clear()
        self.failed.clear()
        self.detailed.clear()
        self.pool.clear()
        self.pending.clear()

    def delete_session(self, session):
        if ask(self.window, 'Supprimer la sélection', f'« {session.name} » ira à la corbeille, avec ses buckets. Les photos ne bougent pas.',
               [('cancel', 'Annuler', False), ('delete', 'Supprimer', True)]) == 'delete':
            move_to_trash(session.path)
            if session.path.with_suffix('').exists():
                move_to_trash(session.path.with_suffix(''))
            self.window.home.refresh()

    def open_session(self, session):
        self.session = session
        self.images.clear()
        self.failed.clear()
        self.work_title.setText(session.name.upper())
        self.cached_order = None
        self.rebuild_cards()
        position = session.data.get('position')
        order = self.order()
        self.index = next((i for i, photo in enumerate(order) if photo['path'] == position), 0)
        self.setCurrentWidget(self.work)
        self.show_photo()

    def back(self):
        """Up one level: from the choice of galleries to the sorting, from the sorting to the welcome screen."""
        if self.currentWidget() is self.setup:
            self.show_after_setup(None)
        else:
            self.window.go_home()

    def edit_selection(self):
        self.save()
        self.setup.open(self.session)
        self.setCurrentWidget(self.setup)

    def save(self):
        self.save_timer.stop()
        if self.session is not None:
            current = self.current()
            self.session.data['position'] = current['path'] if current else ''
            try:
                self.session.save()
            except OSError as error:
                self.window.toast(f'Session non enregistrée : {error}', 6000)

    def changed(self):
        self.save_timer.start()
        self.window.bucket_strip.update()
        self.window.review_tabs.sync()
        # A photo may now pass the filter, or no longer.
        if self.window.selection is self.session:
            self.window.apply_filters()
        self.update_counter()
        for card in self.cards.values():
            card.update()

    # The sorting.
    def order(self):
        """The photos the filters of the window let through, in its order: the same ones in every view."""
        if self.session is None:
            return []
        if self.cached_order is None:
            known = {photo['path']: photo for photo in self.session.photos}
            self.cached_order = [known[photo.relative_path] for photo in self.window.photos if photo.relative_path in known]
        return self.cached_order

    def filters_changed(self):
        """Stays on the same photo if it is still there. Otherwise the next one has taken its place."""
        if self.session is None or self.window.selection is not self.session:
            return
        current = self.current()
        self.cached_order = None
        order = self.order()
        if current:
            self.index = next((i for i, photo in enumerate(order) if photo['path'] == current['path']), self.index)
        if self.currentWidget() is self.work:
            self.show_photo()

    def current(self):
        order = self.order()
        return order[self.index] if 0 <= self.index < len(order) else None

    def position(self):
        return self.index, len(self.order())

    def current_buckets(self):
        photo = self.current()
        return self.session.buckets_of(photo['path']) if photo else []

    def current_image(self):
        photo = self.current()
        return self.images.get(photo['path']) if photo else None

    def empty_text(self):
        if not self.session.photos:
            return 'Aucune photo : « Modifier la sélection » choisit les galeries et les filtres.'
        return 'Aucune photo pour ces filtres.'

    def bucket_number(self, key):
        index = next(i for i, bucket in enumerate(self.session.buckets) if bucket['id'] == key)
        return index+1 if index < 5 else None

    def update_counter(self):
        index, total = self.position()
        self.counter.setText(f'{min(index+1, total)} / {total}' if total else '0 photo')
        photo = self.current()
        text = f"{photo['gallery']} · {Path(photo['path']).name}" if photo else ''
        self.photo_name.setText(self.photo_name.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, max(120, self.photo_name.width())))
        self.photo_name.setToolTip(photo['path'] if photo else '')

    def zoom_changed(self):
        self.zoom_button.setText(f'{round(self.stage.zoom*100)} %')
        self.request_images()

    def background_changed(self):
        self.picker.sync()
        self.stage.update()

    def show_photo(self):
        self.index = max(0, min(self.index, len(self.order())-1))
        self.request_images()
        self.update_counter()
        self.stage.update()
        for card in self.cards.values():
            card.update()

    def request_images(self):
        order = self.order()
        wanted = [order[i]['path'] for i in (self.index, self.index+1, self.index+2, self.index+3, self.index-1) if 0 <= i < len(order)]
        for path in list(self.pending):
            if path not in wanted and self.pool.tryTake(self.pending[path]):
                del self.pending[path]
        for rank, path in enumerate(wanted):
            if path not in self.images and path not in self.pending and path not in self.failed:
                self.start_loading(path, PREVIEW_SIDE, -rank)
        # Sharper once zoomed in, for the photo shown only.
        if wanted and self.stage.zoom > 1.2:
            path = order[self.index]['path']
            if path in self.images and path not in self.detailed and path not in self.pending:
                self.detailed.add(path)
                self.start_loading(path, DETAIL_SIDE, 1)

    def start_loading(self, path, side, priority):
        job = self.window.custom_loader(Path(path), side)
        job.setAutoDelete(False)
        job.signals.ready.connect(self.loaded, Qt.ConnectionType.QueuedConnection)
        self.pending[path] = job
        self.pool.start(job, priority)

    @Slot(str, int, QImage, str, int)
    def loaded(self, path, side, image, error, generation):
        self.pending.pop(path, None)
        if error or image.isNull():
            self.failed[path] = error
        else:
            self.images[path] = image
            self.images.move_to_end(path)
            # The large ones weigh more: fewer of them.
            while len(self.images) > (PREVIEWS_KEPT if self.stage.zoom <= 1.2 else 6):
                self.detailed.discard(self.images.popitem(last=False)[0])
        current = self.current()
        if current and current['path'] == path:
            self.stage.update()

    def navigate(self, delta, slide=True):
        total = len(self.order())
        target = self.index+delta
        if self.session is None or self.currentWidget() is not self.work or not 0 <= target < total:
            return
        leaving = self.current_image()
        self.index = target
        if slide and leaving is not None:
            self.stage.move_to(leaving, 1 if delta > 0 else -1)
        self.show_photo()
        self.save_timer.start()

    def advance(self, path):
        """On to the next photo. If the one just sorted left this view, the next one has taken its place."""
        order = self.order()
        leaving = self.images.get(path)
        still_here = any(photo['path'] == path for photo in order)
        if still_here:
            self.index = min(self.index+1, len(order)-1)
        if leaving is not None and self.current() is not None and self.current()['path'] != path:
            self.stage.move_to(leaving, 1)
        self.show_photo()

    def assign(self, key):
        """Key None clears the photo of its buckets. Either way, on to the next one."""
        photo = self.current()
        if photo is None:
            return
        path = photo['path']
        if key:
            self.session.add(path, key)
            self.window.toast(f"Rangée dans {self.session.bucket(key)['title']}", 1000)
        else:
            self.session.clear(path)
        self.changed()
        self.advance(path)

    def clicked_card(self, key):
        """A click changes the photo in place, which also takes it out of a bucket. The drag moves on."""
        photo = self.current()
        if photo is None:
            return
        if key:
            self.session.toggle(photo['path'], key)
            self.window.toast(f"{'Ajoutée à' if key in self.session.buckets_of(photo['path']) else 'Retirée de'} {self.session.bucket(key)['title']}", 1000)
        else:
            self.session.clear(photo['path'])
        self.changed()
        self.show_photo()

    def key_bucket(self, value):
        """The keys of the notes pick the buckets here: 1 for the first one, the zero star for none."""
        if self.session is None or self.currentWidget() is not self.work:
            return
        if not value:
            self.assign(None)
        elif value <= len(self.session.buckets):
            self.assign(self.session.buckets[value-1]['id'])

    def undo(self):
        path = self.session.undo() if self.session else None
        if path is None:
            self.window.toast('Rien à annuler')
            return
        order = self.order()
        self.index = next((i for i, photo in enumerate(order) if photo['path'] == path), self.index)
        self.changed()
        self.show_photo()
        self.window.toast('Rangement annulé', 1200)

    # The drag from the ribbon to the buckets.
    def start_drag(self):
        image = self.current_image()
        pixmap = QPixmap.fromImage(image).scaled(QSize(220, 220), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.ghost.setPixmap(pixmap)
        self.ghost.adjustSize()
        self.ghost.show()
        self.ghost.raise_()

    def card_at(self, point):
        for card in self.cards.values():
            if card.key and card.isVisible() and card.rect().contains(card.mapFromGlobal(point)):
                return card
        return None

    def drag_to(self, point):
        local = self.mapFromGlobal(point)
        self.ghost.move(local.x()-self.ghost.width()//2, local.y()-self.ghost.height()//2)
        under = self.card_at(point)
        for card in self.cards.values():
            if card.hovered != (card is under):
                card.hovered = card is under
                card.update()

    def drop_at(self, point):
        self.ghost.hide()
        under = self.card_at(point)
        for card in self.cards.values():
            card.hovered = False
            card.update()
        if under is not None:
            self.assign(under.key)

    # The buckets.
    def rebuild_cards(self):
        while self.card_row.count():
            widget = self.card_row.takeAt(0).widget()
            widget.setParent(None)
            widget.deleteLater()
        self.cards = {bucket['id']: BucketCard(self, bucket['id']) for bucket in self.session.buckets} or {None: BucketCard(self, None)}
        for card in self.cards.values():
            self.card_row.addWidget(card)

    def taken(self, ignored=None):
        return {export_folder_name(bucket['title']).lower() for bucket in self.session.buckets if bucket['id'] != ignored}

    def ask_bucket(self, ignored=None, title='', subtitle='', color=None):
        dialog = BucketDialog(self.window, self.taken(ignored), title, subtitle, color or self.session.free_color())
        return (dialog.title.text().strip(), dialog.subtitle.text().strip(), dialog.color()) if dialog.exec() else None

    def buckets_changed(self):
        self.rebuild_cards()
        self.changed()
        self.show_photo()
        self.window.selection_changed()

    def show_path(self, path):
        """Back from another view, on the photo left there."""
        self.index = next((i for i, photo in enumerate(self.order()) if photo['path'] == path), self.index)
        self.show_photo()

    def add_bucket(self):
        answer = self.ask_bucket()
        if answer:
            self.session.buckets.append({'id': uuid.uuid4().hex, 'title': answer[0], 'subtitle': answer[1], 'color': answer[2]})
            self.buckets_changed()

    def edit_bucket(self, key):
        bucket = self.session.bucket(key)
        answer = self.ask_bucket(key, bucket['title'], bucket.get('subtitle', ''), bucket.get('color'))
        if answer:
            bucket['title'], bucket['subtitle'], bucket['color'] = answer
            self.buckets_changed()

    def show_bucket(self, key):
        BucketSheet(self, key).exec()
        self.show_photo()

    def delete_bucket(self, key):
        bucket = self.session.bucket(key)
        count = self.session.count(key)
        if not count or ask(self.window, 'Supprimer le bucket', f"« {bucket['title']} » contient {count} photo{'s' if count > 1 else ''}. Elles en sortent, sans bouger sur le disque.",
                            [('cancel', 'Annuler', False), ('delete', 'Supprimer', True)]) == 'delete':
            self.session.remove_bucket(key)
            self.buckets_changed()

    # The export.
    def ask_export_folder(self):
        return QFileDialog.getExistingDirectory(self.window, 'Dossier où exporter la sélection', self.session.data.get('export_folder', ''))

    def export(self):
        window = self.window
        if window.export_job or window.relocating or window.update_job:
            return
        plan = self.session.export_plan()
        if not plan:
            inform(window, 'Rien à exporter', "Aucune photo n'est encore rangée dans un bucket.")
            return
        present = [step for step in plan if Path(step[2]).is_file()]
        absent = sorted({step[2] for step in plan if not Path(step[2]).is_file()})
        if absent:
            choice = ask(window, 'Originaux introuvables', f"{len(absent)} photo{'s' if len(absent) > 1 else ''} de ces buckets {'sont introuvables' if len(absent) > 1 else 'est introuvable'} : disque débranché ou fichier déplacé.\n\n"
                         'Vous pouvez les chercher dans un dossier, les retirer de la sélection pour ne plus les voir, ou exporter le reste.',
                         [('cancel', 'Annuler', False), ('find', 'Retrouver…', False), ('remove', 'Retirer', False), ('go', 'Exporter le reste', True)])
            if choice == 'find':
                window.repair_photos()
                return
            if choice == 'remove':
                self.remove_photos(absent)
                plan = self.session.export_plan()
                present = plan
            elif choice != 'go':
                return
            if not present:
                return
        chosen = self.ask_export_folder()
        if not chosen:
            return
        self.session.data['export_folder'] = chosen
        name = export_folder_name(self.session.name) or 'Custom'
        destination = Path(chosen)/name
        if destination.exists():
            # An earlier export stays as it is, whatever it has become since.
            stamp, suffix = time.strftime('%Y-%m-%d_%Hh%M'), 2
            destination = Path(chosen)/f'{name} {stamp}'
            while destination.exists():
                destination, suffix = Path(chosen)/f'{name} {stamp}-{suffix}', suffix+1
        window.start_custom_export(ExportJob(present, destination), len(present))

    def remove_photos(self, paths):
        self.session.remove_photos(paths)
        self.save()
        self.window.selection_changed()
        self.window.toast(f"{len(paths)} photo{'s' if len(paths) > 1 else ''} retirée{'s' if len(paths) > 1 else ''} de la sélection. Les fichiers n'ont pas bougé.", 3000)

    def after_export(self, job):
        """The files that could not be copied are named, with the way to stop meeting them."""
        if job.skipped and self.session and ask(self.window, 'Photos non copiées', f"{len(job.skipped)} photo{'s' if len(job.skipped) > 1 else ''} n'{'ont' if len(job.skipped) > 1 else 'a'} pas pu être copiée{'s' if len(job.skipped) > 1 else ''} :\n"
                                                +self.name_list(job.skipped), [('keep', 'Garder', False), ('remove', 'Retirer de la sélection', True)]) == 'remove':
            self.remove_photos(sorted(set(job.skipped)))

    def name_list(self, paths, problems=None):
        names = [Path(path).name+(f' ({problems[path]})' if problems else '') for path in paths[:8]]
        return '\n'.join(names)+(f'\net {len(paths)-8} autres' if len(paths) > 8 else '')

    # The health of the photos.
    def check_photos(self):
        if self.session is None or self.checking:
            return
        paths = [photo['path'] for photo in self.session.photos]
        self.checking = CheckJob(paths)
        self.check_left = len(paths)
        self.checking.signals.progress.connect(self.check_progress, Qt.ConnectionType.QueuedConnection)
        self.checking.signals.finished.connect(self.check_done, Qt.ConnectionType.QueuedConnection)
        self.pool.start(self.checking)
        self.window.toast('Vérification des photos en cours…', 1800)

    def check_progress(self, left):
        self.check_left = left

    def check_done(self, problems):
        self.checking = None
        self.check_left = 0
        if self.session is None:
            return
        if not problems:
            self.window.toast('Toutes les photos de la sélection sont saines.', 3000)
            return
        paths = sorted(problems)
        missing = [path for path in paths if problems[path] == 'introuvable']
        buttons = [('close', 'Fermer', False)]+([('find', 'Retrouver…', False)] if missing else [])+[('remove', 'Retirer de la sélection', True)]
        choice = ask(self.window, 'Photos à problème', f"{len(paths)} photo{'s' if len(paths) > 1 else ''} sur {len(self.session.photos)} :\n{self.name_list(paths, problems)}\n\nLes retirer les enlève de la sélection et de ses buckets, sans toucher aux fichiers.", buttons)
        if choice == 'remove':
            self.remove_photos(paths)
        elif choice == 'find':
            self.window.repair_photos()

    def shutdown(self):
        self.save()
        self.pool.clear()
        self.setup.pool.clear()
        self.pool.waitForDone()
        self.setup.pool.waitForDone()
