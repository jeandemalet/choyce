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

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QRectF, QRunnable, QSize, QStandardPaths, QThreadPool, QTimer, QVariantAnimation, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)
from triphoto import PhotoSession, export_folder_name, write_json_atomic
from triphoto_portfolio import PortfolioSession, move_to_trash, source_key
from triphoto_prefs import ask, inform
from triphoto_review import BackgroundPicker
from triphoto_theme import CONTROL, FAINT, MUTED, RAISED, TEXT, contrast_on

PREVIEW_SIDE = 2000
DETAIL_SIDE = 6000
PREVIEWS_KEPT = 12
ALL, UNSORTED = 'all', 'none'
BUCKET_COLORS = ['#ff3b30', '#0a84ff', '#ffd60a', '#30d158', '#ff2d92', '#ff9500', '#00d4d8', '#bf5af2', '#a4e000', '#5e5ce6']


def pastel(color):
    """A light tone of a bucket color, which stays discreet beside the photos."""
    tone = QColor(color)
    return QColor((tone.red()+2*255)//3, (tone.green()+2*255)//3, (tone.blue()+2*255)//3)


class Sorting:
    """One session: its photos, its buckets, and what went where."""

    def __init__(self, path, data):
        self.path, self.data = Path(path), data
        self.undo_stack = []

    @classmethod
    def create(cls, folder, name):
        key = uuid.uuid4().hex
        return cls(Path(folder)/f'{key}.json', {'version': 1, 'id': key, 'name': name, 'created': time.time(), 'updated': time.time(),
                                                'sources': [], 'photos': [], 'buckets': [], 'assigned': {}, 'position': '', 'view': ALL})

    @classmethod
    def load(cls, path):
        session = cls(path, json.loads(Path(path).read_text(encoding='utf-8')))
        # Buckets made before they had colors.
        for bucket in session.buckets:
            if not bucket.get('color'):
                bucket['color'] = session.free_color()
        return session

    def free_color(self, ignored=None):
        """The first color no other bucket has; with more buckets than colors, the least used."""
        used = [bucket.get('color') for bucket in self.buckets if bucket['id'] != ignored]
        return min(BUCKET_COLORS, key=used.count)

    def save(self):
        self.data['updated'] = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
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

    def order(self, view=None):
        view = view or self.data.get('view', ALL)
        if view == ALL:
            return self.photos
        if view == UNSORTED:
            return [photo for photo in self.photos if not self.buckets_of(photo['path'])]
        return [photo for photo in self.photos if view in self.buckets_of(photo['path'])]

    def set_photos(self, photos):
        """A new choice of galleries. A photo already sorted stays: the work done is never lost to a filter."""
        chosen = {source_key(photo['path']) for photo in photos}
        sorted_before = [photo for photo in self.photos if self.buckets_of(photo['path']) and source_key(photo['path']) not in chosen]
        self.data['photos'] = photos+sorted_before

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
        if self.data.get('view') == key:
            self.data['view'] = ALL

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
            except (OSError, ValueError, KeyError):
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
                (work/folder).mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, work/folder/name)
            self.signals.progress.emit(total, total)
            os.replace(work, self.destination)
            self.signals.finished.emit(str(self.destination), '')
        except Exception as error:
            shutil.rmtree(work, ignore_errors=True)
            self.signals.finished.emit('', str(error))


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

    def add_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, 'Choisir un dossier de photos')
        if chosen and str(Path(chosen)) not in self.rows:
            self.add_row(str(Path(chosen)), self.page.project_label(chosen), str(Path(chosen))).check.setChecked(True)

    def chosen(self):
        """The photos of the checked galleries through their filters, each one once even when two galleries hold it."""
        seen, photos = set(), []
        items = self.window.portfolio.items
        for row in self.rows.values():
            if row.check.isChecked():
                for photo in row.matching():
                    # A copy of the portfolio is the same photo as its original.
                    origin = items.get(photo.relative_path, {}).get('source') if row.portfolio else None
                    key = source_key(origin or photo.path)
                    if key not in seen:
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
        fill.setAlpha(46 if self.hovered else 20 if holds else 0)
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
        if not image.isNull():
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
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(self.save)
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
        self.plain_button(bar, '‹ Retour', self.back)
        self.work_title = QLabel(objectName='section')
        bar.addWidget(self.work_title)
        bar.addSpacing(12)
        self.view_combo = QComboBox()
        self.view_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view_combo.setMinimumWidth(180)
        self.view_combo.currentIndexChanged.connect(self.view_changed)
        bar.addWidget(self.view_combo)
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
        if session is not None:
            self.open_session(session)
        elif self.session is not None:
            self.setCurrentWidget(self.work)
        else:
            self.window.leave_custom()

    def close_session(self):
        self.save()
        self.session = None
        self.images.clear()
        self.failed.clear()
        self.detailed.clear()
        self.pool.clear()
        self.pending.clear()

    def delete_session(self, session):
        if ask(self.window, 'Supprimer la sélection', f'« {session.name} » ira à la corbeille, avec ses buckets. Les photos ne bougent pas.',
               [('cancel', 'Annuler', False), ('delete', 'Supprimer', True)]) == 'delete':
            move_to_trash(session.path)
            self.window.home.refresh()

    def open_session(self, session):
        self.session = session
        self.images.clear()
        self.failed.clear()
        self.work_title.setText(session.name.upper())
        self.rebuild_cards()
        self.sync_views()
        position = session.data.get('position')
        order = session.order()
        self.index = next((i for i, photo in enumerate(order) if photo['path'] == position), 0)
        self.setCurrentWidget(self.work)
        self.show_photo()

    def back(self):
        """Up one level: from the choice of galleries to the sorting, from the sorting out of Custom."""
        if self.currentWidget() is self.setup:
            self.show_after_setup(None)
        else:
            self.window.leave_custom()

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
        self.update_counter()
        for card in self.cards.values():
            card.update()

    # The sorting.
    def order(self):
        return self.session.order() if self.session else []

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
        return 'Aucune photo dans cette vue.'

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

    def sync_views(self):
        self.view_combo.blockSignals(True)
        self.view_combo.clear()
        self.view_combo.addItem('Toutes les photos', ALL)
        self.view_combo.addItem('Sans bucket', UNSORTED)
        for bucket in self.session.buckets:
            self.view_combo.addItem(bucket['title'], bucket['id'])
        self.view_combo.setCurrentIndex(max(0, self.view_combo.findData(self.session.data.get('view', ALL))))
        self.view_combo.blockSignals(False)

    def view_changed(self, *args):
        if self.session is None:
            return
        current = self.current()
        self.session.data['view'] = self.view_combo.currentData()
        order = self.order()
        self.index = next((i for i, photo in enumerate(order) if current and photo['path'] == current['path']), 0)
        self.show_photo()
        self.save_timer.start()

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
        self.sync_views()
        self.changed()
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
        self.sync_views()
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
        absent = len(plan)-len(present)
        if not present:
            inform(window, 'Originaux introuvables', 'Les photos de ces buckets sont introuvables pour le moment. Rebranchez leur disque.')
            return
        if absent and ask(window, 'Originaux introuvables', f"{absent} copie{'s' if absent > 1 else ''} sur {len(plan)} ne {'peuvent' if absent > 1 else 'peut'} pas être faite{'s' if absent > 1 else ''} : disque débranché ou fichier déplacé. Exporter le reste ?",
                          [('cancel', 'Annuler', False), ('go', 'Exporter le reste', True)]) != 'go':
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

    def shutdown(self):
        self.save()
        self.pool.clear()
        self.setup.pool.clear()
        self.pool.waitForDone()
        self.setup.pool.waitForDone()
