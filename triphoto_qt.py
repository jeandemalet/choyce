"""High-DPI Qt interface. Original files stay unchanged; older sessions remain readable."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import sys
import math
import time
from io import BytesIO
from pathlib import Path
from collections import OrderedDict

from PySide6.QtCore import Qt, QSize, QRectF, QPointF, Signal, Slot, QObject, QRunnable, QThreadPool, QTimer, QItemSelectionModel, QSettings
from PySide6.QtGui import QIcon, QImage, QPainter, QPainterPath, QColor, QFont, QShortcut, QKeySequence, QColorSpace, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QSplitter, QListWidget, QListWidgetItem, QFrame, QScrollArea, QButtonGroup,
    QStackedWidget, QAbstractItemView, QFileDialog, QMessageBox, QStyledItemDelegate, QStyle, QMenu, QColorDialog)
from PIL import Image, ImageCms, ImageWin
from triphoto import (PhotoSession, sort_photos, SORT_OPTIONS, load_screen_preview, original_inventory,
    export_original_copy, EXPORT_DIR_NAME, PICK_NEUTRAL, PICK_REJECT, PICK_SELECT)
from triphoto_prefs import (ALIASES, PreferencesDialog, Recents, ScanJob, age_label, path_key, shortcut_map)
from triphoto_review import BackgroundPicker, RatingBar, ReviewPage
from triphoto_theme import (ACCENT, ASSETS, BACKGROUNDS, FAINT, INK, RAISED, REJECT, SELECT, STYLE, TEXT,
    contrast_on, draw_check, draw_clear_star, draw_cross, draw_flag, draw_stars)

# Tests point this to a separate store, so that they never touch the user's settings.
SETTINGS_SCOPE = os.environ.get('TRIPHOTO_SETTINGS', 'TriPhoto')
ROW_HEIGHT = 152
EXPORT_PICKS_DIR_NAME = 'Export_Retenues'
STAR_FILTERS = {'all': 'Toutes les notes', 'ge4': '4 étoiles et plus', 'ge3': '3 étoiles et plus', 'ge2': '2 étoiles et plus',
    'ge1': '1 étoile et plus', 5: '5 étoiles', 4: '4 étoiles', 3: '3 étoiles', 2: '2 étoiles', 1: '1 étoile', 0: '0 étoile'}
FLAG_FILTERS = {'all': 'Tous les drapeaux', 'select': 'Drapeaux uniquement', 'unflagged': 'Sans drapeau'}
PICK_FILTERS = {'all': 'Tous les marqueurs', 'select': 'Retenues', 'neutral': 'Neutres', 'reject': 'Rejetées', 'unrejected': 'Sans les rejetées'}


def app_settings():
    return QSettings(SETTINGS_SCOPE, 'TriPhoto')


def stars_match(photo, key):
    if key == 'all':
        return True
    if isinstance(key, str):
        return photo.stars >= int(key[2:])
    return photo.stars == key


def marker_matches(photo, key):
    return {'all': True, 'select': photo.pick == PICK_SELECT, 'neutral': photo.pick == PICK_NEUTRAL,
            'reject': photo.pick == PICK_REJECT, 'unrejected': photo.pick != PICK_REJECT,
            'unflagged': photo.pick != PICK_SELECT}.get(key, True)


def project_label(path):
    """Display name of a project; "Output" says little, the folder above names the wedding."""
    path = Path(path)
    name = path.name or str(path)
    return f'{path.parent.name} / {name}' if name.lower() == 'output' and path.parent.name else name


def qt_image(image, profile=None):
    image = image.convert('RGB')
    result = QImage(image.tobytes(), image.width, image.height, image.width * 3,
                    QImage.Format.Format_RGB888).copy()
    result.setColorSpace(QColorSpace.fromIccProfile(profile) if profile else QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    return result


class Signals(QObject):
    ready = Signal(str, int, QImage, str, int)


class Loader(QRunnable):
    def __init__(self, path, side, cache_dir, display_profile=None, generation=0):
        super().__init__()
        self.path, self.side, self.cache_dir = path, side, cache_dir
        self.display_profile = display_profile
        self.signals = Signals()
        self.generation = generation

    def run(self):
        try:
            if self.path.stat().st_size == 0:
                raise ValueError('Fichier photo vide (0 octet). Remplacez-le par une copie complète.')
            target = ImageCms.ImageCmsProfile(BytesIO(self.display_profile)) if self.display_profile else None
            # Transform directly from the embedded profile to the monitor.
            # An intermediate sRGB JPEG would clip colors from a P3 original.
            cache_path = None
            image = None
            if self.side in (400, 1000) and self.cache_dir is not None:
                stat = self.path.stat()
                monitor = hashlib.sha256(self.display_profile or b'sRGB').hexdigest()
                token = hashlib.sha256(f'qt-direct-v1|{self.path}|{stat.st_size}|{stat.st_mtime_ns}|{monitor}'.encode()).hexdigest()
                cache_path = self.cache_dir / f'qt-{token}.png'
                if self.side == 400 and cache_path.exists():
                    # A persistent preview is far cheaper to reduce than the original.
                    try:
                        with Image.open(cache_path) as cached:
                            image = cached.convert('RGB')
                        image.thumbnail((400, 400), Image.Resampling.BILINEAR)
                    except (OSError, ValueError):
                        image = None
                elif self.side == 1000 and cache_path.exists():
                    try:
                        with Image.open(cache_path) as cached:
                            image = cached.convert('RGB')
                        os.utime(cache_path, None)
                    except (OSError, ValueError):
                        image = None
            if image is None:
                image = load_screen_preview(self.path, self.side, target)
                if cache_path is not None and self.side == 1000:
                    temporary = cache_path.with_suffix('.tmp')
                    image.save(temporary, format='PNG', compress_level=1,
                               icc_profile=self.display_profile or b'')
                    os.replace(temporary, cache_path)
                    # Bounded persistent cache; maintenance stays in the worker.
                    entries = [(p.stat().st_mtime, p.stat().st_size, p) for p in self.cache_dir.glob('qt-*.png')]
                    total = sum(size for _, size, _ in entries)
                    for _, size, path in sorted(entries):
                        if total <= 512*1024*1024:
                            break
                        try:
                            path.unlink()
                            total -= size
                        except OSError:
                            pass
            self.signals.ready.emit(str(self.path), self.side, qt_image(image, self.display_profile), '', self.generation)
        except Exception as error:
            self.signals.ready.emit(str(self.path), self.side, QImage(), str(error), self.generation)


class Viewer(QWidget):
    zoomChanged = Signal(float)
    resized = Signal()
    activity = Signal()
    backgroundChanged = Signal()

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.zoom = 1.0
        self.pan = QPointF()
        self.drag = None
        self.message = 'Ouvrez un dossier pour commencer'
        self.settings = app_settings()
        self.background = self.settings.value('background', '#808080')
        self.setMinimumSize(360, 260)
        self.setMouseTracking(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.background))
        if self.image.isNull():
            painter.setPen(QColor(contrast_on(self.background)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        fit = min(max(1, self.width()-24)/self.image.width(), max(1, self.height()-24)/self.image.height())
        width, height = self.image.width()*fit*self.zoom, self.image.height()*fit*self.zoom
        target = QRectF((self.width()-width)/2+self.pan.x(), (self.height()-height)/2+self.pan.y(), width, height)
        painter.drawImage(target, self.image)

    def resizeEvent(self, event):
        self.resized.emit()
        super().resizeEvent(event)

    def set_zoom(self, value, anchor=None):
        previous_zoom = self.zoom
        self.zoom = max(.1, min(8., value))
        if anchor is not None and not self.image.isNull():
            # Preserve the image coordinate beneath the cursor, including
            # when zooming out through the fit-to-window scale.
            center = QPointF(self.width()/2, self.height()/2)
            ratio = self.zoom / previous_zoom
            self.pan = (anchor-center)*(1-ratio) + self.pan*ratio
        elif self.zoom <= 1:
            self.pan = QPointF()
        self.zoomChanged.emit(self.zoom)
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.set_zoom(self.zoom * (1.15 if delta > 0 else 1/1.15), event.position())
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.zoom > 1:
            self.drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            self.pan += event.position()-self.drag
            self.drag = event.position()
            self.update()
        else:
            self.activity.emit()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.image.isNull():
            self.drag = None
            self.unsetCursor()
            if math.isclose(self.zoom, 2.0, abs_tol=1e-6):
                self.set_zoom(1.0)
            else:
                self.set_zoom(2.0, event.position())
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def set_background(self, color):
        self.background = color
        self.settings.setValue('background', color)
        self.update()
        self.backgroundChanged.emit()

    def background_menu(self, parent):
        menu = QMenu(parent)
        for label, color in BACKGROUNDS:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.background == color)
            action.triggered.connect(lambda checked=False, value=color: self.set_background(value))
        menu.addAction('Autre couleur…', self.pick_background)
        return menu

    def pick_background(self):
        color = QColorDialog.getColor(QColor(self.background), self, 'Couleur du fond')
        if color.isValid():
            self.set_background(color.name())

    def contextMenuEvent(self, event):
        menu = self.background_menu(self)
        menu.addSeparator()
        menu.addAction('Ajuster la photo', lambda: self.set_zoom(1))
        menu.exec(event.globalPos())


class PhotoDelegate(QStyledItemDelegate):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def sizeHint(self, option, index):
        return QSize(250, ROW_HEIGHT)

    def card(self, option):
        return QRectF(option.rect.adjusted(7, 3, -7, -3))

    def paint(self, painter, option, index):
        photo = index.data(Qt.ItemDataRole.UserRole)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = self.card(option)
        thumb = QRectF(rect.x(), rect.y(), rect.width(), rect.height()-28)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 5, 5)
        painter.fillPath(clip, QColor(INK))
        image = self.window.cache.get((str(photo.path), 400)) or self.window.best_image(photo)
        if image is not None:
            ratio = min(thumb.width()/image.width(), thumb.height()/image.height())
            width, height = image.width()*ratio, image.height()*ratio
            target = QRectF(thumb.center().x()-width/2, thumb.center().y()-height/2, width, height)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setClipPath(clip)
            painter.drawImage(target, image)
            if photo.pick == PICK_REJECT:
                painter.fillRect(target, QColor(0, 0, 0, 135))
            painter.setClipping(False)
        if selected:
            painter.setPen(QPen(QColor(ACCENT), 1.6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(thumb.adjusted(.8, .8, -.8, -.8), 5, 5)
        middle = rect.bottom()-13
        draw_clear_star(painter, rect.x()+8, middle, 5.5, TEXT if photo.stars else '#4a4a4c')
        draw_stars(painter, rect.x()+24, middle, photo.stars, 5.5, 14.0)
        painter.setFont(QFont(option.font.family(), 8))
        painter.setPen(QColor(TEXT if selected else FAINT))
        painter.drawText(QRectF(rect.right()-74, middle-10, 42, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(index.row()+1).zfill(3))
        icon = QRectF(rect.right()-19, middle-6.5, 13, 13)
        if photo.pick == PICK_REJECT:
            draw_cross(painter, icon, REJECT)
        elif self.window.marker == 'pick':
            draw_check(painter, icon, SELECT if photo.pick == PICK_SELECT else '#4a4a4c')
        else:
            draw_flag(painter, icon, ACCENT if photo.flagged else '#4a4a4c')
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == event.Type.MouseButtonRelease:
            rect = self.card(option)
            x, y = event.position().x(), event.position().y()
            if y >= rect.bottom()-26:
                if x >= rect.right()-30:
                    self.window.toggle_flag(index.row())
                    return True
                photo = index.data(Qt.ItemDataRole.UserRole)
                if rect.x() <= x < rect.x()+17:
                    if photo.stars:
                        self.window.change([photo], stars=0)
                    return True
                if rect.x()+17 <= x < rect.x()+17+5*14:
                    value = int((x-rect.x()-17)//14)+1
                    self.window.change([photo], stars=0 if photo.stars == value else value)
                    return True
        return super().editorEvent(event, model, option, index)


class CoverSignals(QObject):
    ready = Signal(str, QImage, str)


class CoverJob(QRunnable):
    def __init__(self, key, root, path, profile, signals):
        super().__init__()
        self.key, self.root, self.path, self.profile, self.signals = key, root, path, profile, signals

    def run(self):
        # Runs off the interface thread: a sleeping or unplugged disk must not freeze it.
        try:
            if not self.root.exists():
                self.signals.ready.emit(self.key, QImage(), 'missing')
                return
            target = ImageCms.ImageCmsProfile(BytesIO(self.profile)) if self.profile else None
            image = qt_image(load_screen_preview(self.path, 640, target), self.profile) if self.path else QImage()
            self.signals.ready.emit(self.key, image, '')
        except Exception:
            self.signals.ready.emit(self.key, QImage(), '')


class RecentCard(QFrame):
    def __init__(self, entry, home):
        super().__init__(objectName='card')
        self.entry, self.home = entry, home
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(208)
        self.setMinimumWidth(250)
        self.cover = QImage()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 152, 0, 8)
        layout.setSpacing(1)
        self.name = QLabel(project_label(entry['path']))
        self.name.setContentsMargins(12, 0, 12, 0)
        self.name.setToolTip(entry['path'])
        self.meta = QLabel(self.describe(), objectName='muted')
        self.meta.setContentsMargins(12, 0, 12, 0)
        layout.addWidget(self.name)
        layout.addWidget(self.meta)
        layout.addStretch()

    def describe(self):
        parts = []
        if 'count' in self.entry:
            parts.append(f"{self.entry['count']} photos")
        if self.entry.get('five'):
            parts.append(f"{self.entry['five']} à 5 étoiles")
        age = age_label(self.entry.get('opened'))
        if age:
            parts.append(age)
        return ' · '.join(parts)

    def set_missing(self):
        self.meta.setText('Dossier ou disque introuvable')

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(0, 0, self.width(), 142)
        clip = QPainterPath()
        clip.addRoundedRect(area.adjusted(0, 0, 0, 10), 10, 10)
        painter.setClipPath(clip)
        painter.fillRect(area, QColor(RAISED))
        if not self.cover.isNull():
            scale = max(area.width()/self.cover.width(), area.height()/self.cover.height())
            width, height = self.cover.width()*scale, self.cover.height()*scale
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF((area.width()-width)/2, (area.height()-height)/2, width, height), self.cover)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.home.window.open_recent(self.entry['path'])

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction('Retirer de la liste', lambda: self.home.window.forget_recent(self.entry['path']))
        menu.exec(event.globalPos())


class HomePage(QWidget):
    """Welcome screen: the recent projects, or a single centered call to action."""

    def __init__(self, window):
        super().__init__(objectName='home')
        self.window = window
        self.cards = {}
        self.columns = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll)
        self.content = QWidget(objectName='home')
        self.scroll.setWidget(self.content)
        self.column = QVBoxLayout(self.content)
        self.column.setContentsMargins(56, 44, 56, 44)
        self.column.setSpacing(0)
        self.column.addStretch(0)
        self.title = QLabel('Ajoutez des photos pour commencer', objectName='title')
        self.column.addWidget(self.title)
        self.column.addSpacing(16)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.add_button = QPushButton('Ajouter des photos', objectName='primary')
        self.add_button.clicked.connect(lambda checked=False: window.open_folder())
        self.find_button = QPushButton("Retrouver d'anciens projets", objectName='ghost')
        self.find_button.clicked.connect(lambda checked=False: window.find_projects())
        self.lead = QWidget()
        self.trail = QWidget()
        self.actions.addWidget(self.lead, 1)
        self.actions.addWidget(self.add_button)
        self.actions.addWidget(self.find_button)
        self.actions.addWidget(self.trail, 1)
        self.column.addLayout(self.actions)
        self.column.addSpacing(34)
        self.section = QLabel('RÉCENTS', objectName='section')
        self.column.addWidget(self.section)
        self.column.addSpacing(10)
        self.grid_holder = QWidget(objectName='home')
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(14)
        self.column.addWidget(self.grid_holder)
        self.column.addStretch(1)
        self.cover_signals = CoverSignals()
        self.cover_signals.ready.connect(self.cover_ready, Qt.ConnectionType.QueuedConnection)

    def refresh(self):
        entries = self.window.recents.entries()
        for card in self.cards.values():
            self.grid.removeWidget(card)
            card.deleteLater()
        self.cards = {}
        has_recents = bool(entries)
        self.section.setVisible(has_recents)
        self.grid_holder.setVisible(has_recents)
        # Centered when there is nothing else to show, left-aligned above the cards.
        self.column.setStretch(0, 0 if has_recents else 1)
        self.lead.setVisible(not has_recents)
        self.title.setText('Reprendre un projet' if has_recents else 'Ajoutez des photos pour commencer')
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft if has_recents else Qt.AlignmentFlag.AlignHCenter)
        self.add_button.setText('Ouvrir un dossier' if has_recents else 'Ajouter des photos')
        for entry in entries:
            self.cards[path_key(entry['path'])] = RecentCard(entry, self)
            root = Path(entry['path'])
            cover = root/entry['cover'] if entry.get('cover') else None
            self.window.pool.start(CoverJob(path_key(entry['path']), root, cover, self.window.display_profile, self.cover_signals), 2)
        self.columns = 0
        self.arrange()

    def arrange(self):
        columns = max(2, (self.width()-112)//290)
        if columns == self.columns:
            return
        self.columns = columns
        for index, card in enumerate(self.cards.values()):
            self.grid.addWidget(card, index//columns, index % columns)
        for column in range(10):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event):
        self.arrange()

    def cover_ready(self, key, image, error):
        card = self.cards.get(key)
        if card is None:
            return
        if error == 'missing':
            card.set_missing()
        elif not image.isNull():
            card.cover = image
            card.update()


class Window(QMainWindow):
    def __init__(self, folder=None):
        super().__init__()
        self.settings = app_settings()
        self.marker = self.settings.value('marker', 'flag')
        self.recents = Recents(self.settings)
        self.session = None
        self.project_path = None
        self.closing = False
        self.review_active = False
        self.photos = []
        self.cache = OrderedDict()
        self.pending = {}
        self.prepared = set()
        self.failed = set()
        self.undo_stack = []
        self.shortcuts = []
        self.scans = []
        self.display_profile = None
        self.display_signature = None
        self.color_generation = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.setWindowTitle('TriPhoto')
        self.resize(1440, 920)
        self.setMinimumSize(1260, 640)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)
        header = QWidget(objectName='header')
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        top = QHBoxLayout(header)
        top.setContentsMargins(16, 8, 12, 8)
        top.setSpacing(8)
        # Two sides of equal stretch keep Tri and Révision in the middle of the window.
        left, right = QHBoxLayout(), QHBoxLayout()
        left.setSpacing(8)
        right.setSpacing(8)
        left.addWidget(QLabel('TriPhoto', objectName='brand'))
        left.addSpacing(6)
        self.project_button = QPushButton('Projet', objectName='menu')
        self.project_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.project_menu = QMenu(self)
        self.project_menu.aboutToShow.connect(self.fill_project_menu)
        self.project_button.setMenu(self.project_menu)
        left.addWidget(self.project_button)
        left.addSpacing(6)
        self.folder_label = QLabel('', objectName='muted')
        self.folder_label.hide()
        self.counter = QLabel('Aucune photo')
        left.addWidget(self.counter)
        self.name_label = QLabel('', objectName='faint')
        left.addWidget(self.name_label)
        left.addSpacing(6)
        self.stars = self.combo(left, 'Notes', STAR_FILTERS)
        self.flags = self.combo(left, 'Marqueurs', FLAG_FILTERS)
        left.addStretch(1)
        right.addStretch(1)
        self.sort = self.combo(right, 'Tri', SORT_OPTIONS)
        right.addSpacing(6)
        segments = QFrame(objectName='segments')
        segment_row = QHBoxLayout(segments)
        segment_row.setContentsMargins(3, 3, 3, 3)
        segment_row.setSpacing(2)
        self.tri_button = QPushButton('Tri', objectName='mainseg', checkable=True, checked=True)
        self.review_button = QPushButton('Révision', objectName='mainseg', checkable=True)
        group = QButtonGroup(self)
        for button, active in ((self.tri_button, False), (self.review_button, True)):
            group.addButton(button)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, value=active: self.set_review(value))
            segment_row.addWidget(button)
        self.segments = segments
        right.addSpacing(6)
        self.export_button = self.button(right, 'Exporter les 5 étoiles', self.export, 'primary')
        self.prefs_button = self.button(right, 'Préférences', self.open_preferences, 'ghost')
        top.addLayout(left, 1)
        top.addWidget(segments)
        top.addLayout(right, 1)
        layout.addWidget(header)
        splitter = QSplitter()
        self.viewer = Viewer()
        self.viewer.backgroundChanged.connect(self.background_changed)
        splitter.addWidget(self.viewer)
        self.list = QListWidget()
        self.list.setMinimumWidth(250)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setItemDelegate(PhotoDelegate(self))
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        splitter.addWidget(self.list)
        splitter.setSizes([1180, 260])
        self.viewer_bar = RatingBar(self, self.viewer, show_name=False)
        self.viewer_bar.rated.connect(self.rate)
        self.viewer_bar.marked.connect(self.set_pick)
        self.viewer_bar.hide()
        self.viewer_picker = BackgroundPicker(self, self.viewer, floating=True)
        self.viewer_picker.hide()
        self.viewer_bar_timer = QTimer(self)
        self.viewer_bar_timer.setSingleShot(True)
        self.viewer_bar_timer.timeout.connect(self.hide_viewer_bar)
        self.viewer.activity.connect(self.show_viewer_bar)
        self.pages = QStackedWidget()
        self.home = HomePage(self)
        self.review = ReviewPage(self)
        self.pages.addWidget(self.home)
        self.pages.addWidget(splitter)
        self.pages.addWidget(self.review)
        layout.addWidget(self.pages, 1)
        self.toast_frame = QFrame(self, objectName='toast')
        toast_row = QHBoxLayout(self.toast_frame)
        toast_row.setContentsMargins(14, 8, 14, 8)
        self.toast_label = QLabel()
        toast_row.addWidget(self.toast_label)
        self.toast_frame.hide()
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast_frame.hide)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_session)
        self.list.currentRowChanged.connect(self.show_current)
        self.list.itemSelectionChanged.connect(self.update_counter)
        self.list.verticalScrollBar().valueChanged.connect(self.load_visible)
        self.viewer.zoomChanged.connect(self.zoom_changed)
        self.viewer.resized.connect(self.schedule_detail)
        self.viewer.resized.connect(self.place_viewer_bar)
        self.detail_timer = QTimer(self)
        self.detail_timer.setSingleShot(True)
        self.detail_timer.timeout.connect(self.load_detail)
        self.visible_timer = QTimer(self)
        self.visible_timer.timeout.connect(self.load_visible)
        self.visible_timer.start(200)
        self.preload_timer = QTimer(self)
        self.preload_timer.timeout.connect(self.preload_background)
        self.preload_timer.start(100)
        self.screen_timer = QTimer(self)
        self.screen_timer.timeout.connect(self.check_display_profile)
        self.screen_timer.start(1500)
        self.check_display_profile()
        self.install_shortcuts()
        self.apply_marker()
        self.update_project_state()
        if folder:
            self.set_folder(Path(folder))
        if not self.session or not self.session.photos:
            QTimer.singleShot(0, self.prompt_for_photos)

    def install_shortcuts(self):
        for shortcut in self.shortcuts:
            shortcut.setParent(None)
            shortcut.deleteLater()
        self.shortcuts = []
        commands = {
            'prev': lambda: self.navigate(-1), 'next': lambda: self.navigate(1),
            'flag': self.toggle_flag, 'select': lambda: self.set_pick(PICK_SELECT),
            'reject': lambda: self.set_pick(PICK_REJECT), 'neutral': lambda: self.set_pick(PICK_NEUTRAL),
            'undo': self.undo, 'fit': lambda: self.review.zoom_reset() if self.review_active else self.viewer.set_zoom(1),
            'zoom_in': lambda: self.zoom_by(1.15), 'zoom_out': lambda: self.zoom_by(1/1.15),
            'background': self.cycle_background, 'review': lambda: self.set_review(not self.review_active),
            'shuffle': lambda: self.review.shuffle() if self.review_active else None,
            'mono': lambda: self.review.toggle_mono() if self.review_active else None,
            'refresh': self.refresh, 'open': self.open_folder, 'close': self.close_project, 'preferences': self.open_preferences,
            **{f'rate{value}': (lambda stars=value: self.rate(stars)) for value in range(6)},
            **{f'mode_{mode}': (lambda value=mode: self.open_review_mode(value)) for mode in ('toile', 'swipe', 'color', 'wall')}}
        mapping = shortcut_map(self.settings)
        taken = {QKeySequence(key).toString() for keys in mapping.values() for key in keys}
        for name, keys in mapping.items():
            aliases = [key for key in ALIASES.get(name, []) if QKeySequence(key).toString() not in taken]
            for key in keys+aliases:
                self.shortcuts.append(QShortcut(QKeySequence(key), self, activated=commands[name]))
        for key, command in (('Escape', self.leave_review), ('Space', lambda: self.skip_card(1)), ('Backspace', lambda: self.skip_card(-1))):
            self.shortcuts.append(QShortcut(QKeySequence(key), self, activated=command))
        lines = [f"{' / '.join(mapping[key])} : {label}" for key, label in [('prev', 'précédente'), ('next', 'suivante')]]
        self.viewer.setToolTip('\n'.join(lines)+'\nClic droit : couleur du fond\nPréférences : tous les raccourcis')

    def open_preferences(self):
        if PreferencesDialog(self, self.settings).exec():
            self.install_shortcuts()
            marker = self.settings.value('marker', 'flag')
            if marker != self.marker:
                self.marker = marker
                self.apply_marker()
                self.apply_filters()
                self.list.viewport().update()

    def apply_marker(self):
        """Adapt the filter, the bars and the export to the chosen marking system."""
        values = PICK_FILTERS if self.marker == 'pick' else FLAG_FILTERS
        self.flags.blockSignals(True)
        self.flags.clear()
        for key, text in values.items():
            self.flags.addItem(text, key)
        self.flags.setToolTip('Marqueurs' if self.marker == 'pick' else 'Drapeaux')
        self.flags.blockSignals(False)
        self.viewer_bar.relayout()
        self.review.bar.relayout()
        self.review.place_overlays()
        menu = None
        if self.marker == 'pick':
            menu = QMenu(self)
            menu.addAction('Les 5 étoiles', lambda: self.export('stars'))
            menu.addAction('Les retenues', lambda: self.export('picks'))
        self.export_button.setMenu(menu)
        self.export_button.setText('Exporter' if menu else 'Exporter les 5 étoiles')
        self.review.refresh_marker_options()

    def toast(self, text, duration=2600):
        self.toast_label.setText(text)
        self.toast_frame.adjustSize()
        self.toast_frame.move((self.width()-self.toast_frame.width())//2, 62)
        self.toast_frame.show()
        self.toast_frame.raise_()
        self.toast_timer.start(duration)

    def show_viewer_bar(self):
        if self.viewer.image.isNull():
            return
        self.place_viewer_bar()
        self.viewer_bar.update()
        self.viewer_bar.show()
        self.viewer_picker.show()
        self.viewer_bar_timer.start(1700)

    def hide_viewer_bar(self):
        if self.viewer_bar.underMouse() or self.viewer_picker.underMouse():
            self.viewer_bar_timer.start(1000)
        else:
            self.viewer_bar.hide()
            self.viewer_picker.hide()

    def place_viewer_bar(self):
        self.viewer_picker.adjustSize()
        x = (self.viewer.width()-self.viewer_bar.width()-self.viewer_picker.width()-8)//2
        y = self.viewer.height()-self.viewer_bar.height()-16
        self.viewer_bar.move(x, y)
        self.viewer_picker.move(x+self.viewer_bar.width()+8, y)

    def set_background(self, color):
        self.viewer.set_background(color)

    def background_changed(self):
        self.viewer_picker.sync()
        self.place_viewer_bar()
        self.review.sync_toolbar()
        self.review.update_view()

    def cycle_background(self):
        colors = [color for _, color in BACKGROUNDS]
        current = colors.index(self.viewer.background) if self.viewer.background in colors else -1
        self.set_background(colors[(current+1) % len(colors)])

    def zoom_by(self, factor):
        if self.review_active:
            self.review.zoom(factor)
        else:
            self.viewer.set_zoom(self.viewer.zoom*factor)

    def set_review(self, active):
        if not (self.session and self.session.photos):
            active = False
        changed = active != self.review_active
        self.review_active = active
        self.update_project_state()
        if changed and active:
            self.review.enter()
        elif changed:
            self.show_current()

    def open_review_mode(self, mode):
        self.set_review(True)
        if self.review_active:
            self.review.set_mode(mode)

    def leave_review(self):
        if self.review_active:
            self.set_review(False)

    def skip_card(self, delta):
        if self.review_active:
            self.review.skip(delta)

    def open_in_tri(self, photo):
        self.select_photo(photo)
        self.set_review(False)
        row = self.list.currentRow()
        if row >= 0:
            self.list.scrollToItem(self.list.item(row))

    def current_photo(self):
        row = self.list.currentRow()
        return self.photos[row] if 0 <= row < len(self.photos) else None

    def selected_photos(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()]

    def target_photos(self):
        """Photos that the keys act on. Swipe and Duel show cards of their own."""
        if self.review_active:
            return self.review.targets()
        return self.selected_photos()

    def select_photo(self, photo, extend=False):
        row = next((i for i, candidate in enumerate(self.photos) if candidate is photo), None)
        if row is None:
            return
        flag = QItemSelectionModel.SelectionFlag.Toggle if extend else QItemSelectionModel.SelectionFlag.ClearAndSelect
        self.list.setCurrentRow(row, flag)
        self.review.bar.update()

    def best_image(self, photo):
        """Largest preview in memory, marked as recently used."""
        path = str(photo.path)
        for side in (6000, 5500, 5000, 4500, 4000, 3500, 3000, 2500, 2000, 1000, 400):
            key = (path, side)
            image = self.cache.get(key)
            if image is not None:
                self.cache.move_to_end(key)
                return image
        return None

    def request_for(self, photo, pixels, priority):
        """Ask for the smallest preview that stays sharp at this size on screen."""
        if pixels <= 400:
            side = 400
        elif pixels <= 1100:
            side = 1000
        else:
            side = ((min(6000, max(2000, int(pixels)))+499)//500)*500
        image = self.best_image(photo)
        if image is None or max(image.width(), image.height()) < side*.9 and (str(photo.path), side) not in self.cache:
            if side > 1000 and image is None:
                self.request(photo, 1000, priority+1)
            self.request(photo, side, priority)

    def fill_project_menu(self):
        menu = self.project_menu
        menu.clear()
        menu.addAction('Ouvrir un projet…', self.open_folder)
        recent_menu = menu.addMenu('Récents')
        entries = self.recents.entries()
        for entry in entries[:15]:
            path = entry['path']
            action = recent_menu.addAction(project_label(path).replace('&', '&&'), lambda value=path: self.open_recent(value))
            action.setToolTip(path)
        recent_menu.setToolTipsVisible(True)
        recent_menu.setEnabled(bool(entries))
        menu.addAction("Retrouver d'anciens projets…", self.find_projects)
        menu.addSeparator()
        menu.addAction('Fermer le projet', self.close_project).setEnabled(self.session is not None)

    def open_recent(self, path):
        if not Path(path).is_dir():
            QMessageBox.information(self, 'Projet introuvable', f'Ce dossier est introuvable pour le moment :\n{path}\n\nSi le disque est débranché, rebranchez-le puis réessayez.')
            return
        self.set_folder(Path(path))

    def forget_recent(self, path):
        self.recents.remove(path)
        self.home.refresh()

    def find_projects(self):
        chosen = QFileDialog.getExistingDirectory(self, "Dossier ou disque où chercher d'anciens projets")
        if not chosen:
            return
        self.toast('Recherche des projets en cours…', 60000)
        job = ScanJob(chosen)
        job.signals.done.connect(self.projects_found, Qt.ConnectionType.QueuedConnection)
        self.scans.append(job)
        self.pool.start(job, 1)

    def projects_found(self, found):
        self.scans.clear()
        added = self.recents.merge(found)
        self.toast(f'{len(found)} projets trouvés, {added} ajoutés aux récents.' if found else 'Aucun ancien projet dans ce dossier.')
        if self.pages.currentIndex() == 0:
            self.home.refresh()

    def prompt_for_photos(self):
        # With recent projects to pick from, the file dialog would only be in the way.
        if not self.closing and (not self.session or not self.session.photos) and not self.recents.entries():
            self.open_folder()

    def update_project_state(self):
        has_photos = bool(self.session and self.session.photos)
        page = (2 if self.review_active else 1) if has_photos else 0
        if page == 0:
            self.home.refresh()
        self.pages.setCurrentIndex(page)
        self.review_button.setEnabled(has_photos)
        self.tri_button.setEnabled(has_photos)
        (self.review_button if self.review_active else self.tri_button).setChecked(True)
        self.export_button.setVisible(has_photos)
        # Nothing to filter or switch on the welcome screen.
        for control in (self.sort, self.segments, self.counter):
            control.setVisible(has_photos)
        # In Révision, these two filters live in the page's own top bar.
        for control in (self.stars, self.flags):
            control.setVisible(has_photos and not self.review_active)
        name = project_label(self.project_path) if self.project_path else ''
        self.setWindowTitle(f'TriPhoto · {name}' if self.session else 'TriPhoto')

    def save_session(self):
        # Saving takes an inventory of the folder: too slow to do on each note
        # with a large gallery on an external disk, so notes are grouped.
        self.save_timer.stop()
        if not self.session:
            return
        try:
            self.session.save()
            self.review.save_state()
        except OSError as error:
            self.toast(f'Enregistrement impossible : {error}', 6000)

    def close_project(self):
        if self.session:
            self.save_session()
            self.recents.add(self.project_path, self.session.photos)
        self.review.reset()
        self.review_active = False
        self.undo_stack.clear()
        self.failed.clear()
        self.color_generation += 1
        self.pool.clear()
        self.pending.clear()
        self.detail_timer.stop()
        self.session = None
        self.project_path = None
        self.photos = []
        self.list.clear()
        self.cache.clear()
        self.prepared.clear()
        self.folder_label.clear()
        self.name_label.clear()
        for combo in (self.sort, self.stars, self.flags):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.viewer.image = QImage()
        self.viewer.pan = QPointF()
        self.viewer.zoom = 1
        self.viewer.update()
        self.counter.setToolTip('')
        self.update_counter()
        self.update_project_state()

    def button(self, layout, text, callback, name=''):
        button = QPushButton(text, objectName=name)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda checked=False: callback())
        layout.addWidget(button)
        return button

    def combo(self, layout, label, values):
        combo = QComboBox()
        combo.setToolTip(label)
        for key, text in values.items():
            combo.addItem(text, key)
        combo.setFixedWidth({'Tri': 182, 'Notes': 136, 'Marqueurs': 146}[label])
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        combo.currentIndexChanged.connect(self.filters_changed)
        layout.addWidget(combo)
        return combo

    def open_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, 'Choisir le dossier de photos')
        if chosen:
            self.set_folder(Path(chosen))

    def filters_changed(self, *args):
        self.apply_filters()
        if self.review_active:
            self.review.rebuild()

    def set_folder(self, path):
        try:
            session = PhotoSession(path)
        except Exception as error:
            QMessageBox.critical(self, 'Ouverture impossible', str(error))
            return
        self.close_project()
        self.session = session
        self.project_path = Path(path)
        self.baseline = original_inventory(path)
        if session.photos:
            self.recents.add(path, session.photos, time.time())
        self.review.start_analysis()
        self.cache_dir = session.session_dir / 'Previews'
        self.cache_dir.mkdir(exist_ok=True)
        self.cache.clear()
        self.prepared.clear()
        self.folder_label.setText(path.name)
        self.folder_label.setToolTip(str(path))
        self.sort.blockSignals(True)
        self.sort.setCurrentIndex(self.sort.findData(session.sort_mode))
        self.sort.blockSignals(False)
        self.review.restore_state()
        self.apply_filters()
        if session.photos and not self.photos:
            # The remembered filters no longer match anything: never open on an empty page.
            for combo in (self.stars, self.flags):
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
            self.apply_filters()
        self.update_project_state()

    def apply_filters(self, *args):
        if not self.session:
            return
        current = self.photos[self.list.currentRow()].relative_path if 0 <= self.list.currentRow() < len(self.photos) else None
        selected = {item.data(Qt.ItemDataRole.UserRole).relative_path for item in self.list.selectedItems()}
        self.session.sort_mode = self.sort.currentData()
        self.session.photos = sort_photos(self.session.photos, self.session.sort_mode)
        stars, flag = self.stars.currentData(), self.flags.currentData()
        filtered = [p for p in self.session.photos if stars_match(p, stars) and marker_matches(p, flag)]
        if len(filtered) == len(self.photos) and all(a is b for a, b in zip(filtered, self.photos)):
            # Ratings and flags usually change only the decoration. Rebuilding
            # the list makes Qt scroll the current item back into view.
            self.save_timer.start(500)
            self.list.viewport().update()
            self.update_counter()
            self.notify_review()
            return
        scroll = self.list.verticalScrollBar().value()
        old_row = self.list.currentRow()
        first = self.list.indexAt(self.list.viewport().rect().topLeft()).row()
        anchor = self.photos[first].relative_path if 0 <= first < len(self.photos) else None
        offset = self.list.visualItemRect(self.list.item(first)).top() if first >= 0 else 0
        self.photos = filtered
        self.list.blockSignals(True)
        self.list.clear()
        row = max(0, min(old_row, len(self.photos)-1))
        for i, photo in enumerate(self.photos):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, photo)
            item.setToolTip(photo.relative_path)
            self.list.addItem(item)
            if photo.relative_path in selected:
                item.setSelected(True)
            if photo.relative_path == current:
                row = i
        if self.photos:
            surviving = bool(self.list.selectedItems())
            self.list.setCurrentRow(row, QItemSelectionModel.SelectionFlag.NoUpdate if surviving else QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.list.doItemsLayout()
        anchor_row = next((i for i, photo in enumerate(self.photos) if photo.relative_path == anchor), None)
        self.list.verticalScrollBar().setValue(anchor_row*ROW_HEIGHT-offset if anchor_row is not None else scroll)
        self.list.blockSignals(False)
        self.save_timer.start(500)
        self.show_current()
        self.load_visible()
        self.notify_review()

    def notify_review(self):
        if self.review_active:
            self.review.photos_changed()
        self.viewer_bar.update()

    def request(self, photo, side, priority=0):
        if self.closing:
            return
        key = (str(photo.path), side)
        if key in self.cache or key in self.pending or key in self.failed:
            return
        job = Loader(photo.path, side, self.cache_dir, self.display_profile, self.color_generation)
        job.background = priority < 0
        # A bound QObject slot guarantees that UI/cache changes run on the GUI thread.
        job.signals.ready.connect(self.loaded, Qt.ConnectionType.QueuedConnection)
        self.pending[key] = job
        self.pool.start(job, priority)

    @Slot(str, int, QImage, str, int)
    def loaded(self, path, side, image, error, generation):
        if generation != self.color_generation:
            return
        job = self.pending.pop((path, side), None)
        if self.session is None or not any(str(p.path) == path for p in self.session.photos):
            return
        if error:
            # Otherwise an unreadable file is requested again forever and keeps
            # the background preloading from ever going past it.
            self.failed.add((path, side))
        if not image.isNull():
            if side == 1000:
                self.prepared.add(path)
            # Background previews only fill the disk cache: in memory they would
            # push out the images currently on screen.
            if job is None or not job.background:
                self.cache[(path, side)] = image
                self.cache.move_to_end((path, side))
                # Keep large previews bounded separately from lightweight thumbnails.
                large = [key for key in self.cache if key[1] > 1000]
                for key in large[:-6]:
                    self.cache.pop(key, None)
                # Also bound actual decoded bytes, especially on large monitors.
                total = sum(item.sizeInBytes() for item in self.cache.values())
                current = self.list.currentRow()
                current_path = str(self.photos[current].path) if 0 <= current < len(self.photos) else None
                for key in list(self.cache):
                    if total <= 256*1024*1024:
                        break
                    if key[0] != current_path:
                        total -= self.cache.pop(key).sizeInBytes()
        row = self.list.currentRow()
        if 0 <= row < len(self.photos) and str(self.photos[row].path) == path and side > 400:
            if error:
                self.toast('Lecture impossible : '+error, 5000)
                if self.viewer.image.isNull():
                    self.viewer.message = 'Aperçu indisponible : '+error
                    self.viewer.update()
            else:
                self.display_best()
        self.list.viewport().update()
        if self.review_active:
            self.review.image_ready()

    def check_display_profile(self):
        """Resolve the ICC profile for the monitor containing this window."""
        profile = None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class MonitorInfo(ctypes.Structure):
                _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                    ('work', wintypes.RECT), ('flags', wintypes.DWORD), ('device', wintypes.WCHAR*32)]
            user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
            user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
            user.MonitorFromWindow.restype = wintypes.HANDLE
            user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
            gdi.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
            gdi.CreateDCW.restype = wintypes.HDC
            gdi.DeleteDC.argtypes = [wintypes.HDC]
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            monitor = user.MonitorFromWindow(int(self.winId()), 2)
            if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                dc = gdi.CreateDCW('DISPLAY', info.device, None, None)
                if dc:
                    try:
                        detected = ImageCms.get_display_profile(ImageWin.HDC(dc))
                        if detected:
                            # Validate the transform before using it for every photo.
                            ImageCms.buildTransform(ImageCms.createProfile('sRGB'), detected, 'RGB', 'RGB')
                            profile = detected.tobytes()
                    except (OSError, ValueError, ImageCms.PyCMSError):
                        pass
                    finally:
                        gdi.DeleteDC(dc)
        signature = hashlib.sha256(profile).hexdigest() if profile else 'srgb'
        if signature != self.display_signature:
            self.display_signature = signature
            self.display_profile = profile
            self.color_generation += 1
            # Old requests cannot satisfy the new display profile. Release their
            # keys now, including jobs removed from the queue before running.
            self.pool.clear()
            self.pending.clear()
            self.cache.clear()
            self.prepared.clear()
            self.failed.clear()
            if self.session:
                self.show_current()
                self.load_visible()
                if self.review_active:
                    self.review.update_view()

    def display_best(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.photos):
            return
        path = str(self.photos[row].path)
        candidates = [(side, image) for (name, side), image in self.cache.items() if name == path and side > 400]
        if candidates:
            self.viewer.image = max(candidates, key=lambda pair: pair[0])[1]
            self.viewer.update()

    def show_current(self, *args):
        self.viewer.image = QImage()
        self.viewer.pan = QPointF()
        self.viewer.message = 'Préparation de l’aperçu…' if self.photos else 'Aucune photo pour ces filtres'
        self.viewer.update()
        row = self.list.currentRow()
        if 0 <= row < len(self.photos):
            self.display_best()
            self.request(self.photos[row], 1000, 10)
            for i in (row-1, row+1):
                if 0 <= i < len(self.photos):
                    self.request(self.photos[i], 1000, 1)
            self.schedule_detail()
        self.update_counter()

    def preload_background(self):
        if self.closing or not self.photos or any(job.background for job in self.pending.values()):
            return
        row = max(0, self.list.currentRow())
        # One task at a time, nearest photos first, then progressively outward.
        for distance in range(len(self.photos)):
            for index in ((row,) if distance == 0 else (row+distance, row-distance)):
                if 0 <= index < len(self.photos):
                    photo = self.photos[index]
                    key = (str(photo.path), 1000)
                    if str(photo.path) not in self.prepared and key not in self.pending and key not in self.failed:
                        self.request(photo, 1000, -10)
                        return

    def schedule_detail(self):
        if hasattr(self, 'detail_timer') and not self.review_active:
            self.detail_timer.start(120)

    def load_detail(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.photos):
            side = max(2000, min(6000, int(max(self.viewer.width(), self.viewer.height())*self.devicePixelRatioF()*self.viewer.zoom)))
            side = ((side+499)//500)*500
            self.request(self.photos[row], side, 20)

    def load_visible(self, *args):
        if not self.photos or self.review_active:
            return
        first = self.list.indexAt(self.list.viewport().rect().topLeft()).row()
        first = max(0, first)
        count = self.list.viewport().height()//ROW_HEIGHT+3
        for photo in self.photos[first:first+count]:
            self.request(photo, 400)

    def zoom_changed(self, zoom):
        self.schedule_detail()

    def navigate(self, delta):
        if self.review_active:
            self.review.navigate(delta)
        elif self.photos:
            row = max(0, min(len(self.photos)-1, self.list.currentRow()+delta))
            # Explicitly replace selection even if Shift remains held after rating.
            self.list.setCurrentRow(row, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.list.scrollToItem(self.list.item(row))

    def update_counter(self):
        if self.photos:
            five = sum(p.stars == 5 for p in self.photos)
            current = self.current_photo()
            if self.review_active and not self.review.targets():
                # Nothing picked on the page yet: name no photo.
                self.counter.setText(f'{len(self.photos)} photos')
                current = None
            else:
                self.counter.setText(f'{self.list.currentRow()+1} / {len(self.photos)}')
            metrics = self.name_label.fontMetrics()
            self.name_label.setText(metrics.elidedText(current.path.name, Qt.TextElideMode.ElideMiddle, 150) if current else '')
            self.counter.setToolTip(f'{len(self.list.selectedItems())} sélectionnée(s) · {five} à 5 étoiles')
        else:
            self.counter.setText('Aucune photo')
            self.name_label.clear()

    def change(self, photos, stars=None, pick=None):
        """Single entry point for notes and markers: it is what makes them undoable."""
        if not self.session or not photos:
            return
        self.undo_stack.append([(photo, photo.stars, photo.pick) for photo in photos])
        del self.undo_stack[:-300]
        for photo in photos:
            if stars is not None:
                photo.stars = stars
            if pick is not None:
                photo.pick = pick
        self.apply_filters()

    def undo(self):
        if not self.undo_stack:
            self.toast('Rien à annuler')
            return
        states = self.undo_stack.pop()
        for photo, stars, pick in states:
            photo.stars, photo.pick = stars, pick
        self.apply_filters()
        if self.review_active:
            self.review.after_undo([state[0] for state in states])
        self.toast('Note annulée', 1200)

    def rate(self, stars):
        self.change(self.target_photos(), stars=stars)

    def set_pick(self, pick):
        if self.marker != 'pick' and pick == PICK_REJECT:
            self.toast('Le rejet demande le marquage Retenue, Rejetée, Neutre, dans les Préférences.', 4000)
            return
        self.change(self.target_photos(), pick=pick)
        if self.review_active:
            self.review.after_mark(pick)

    def toggle_flag(self, row=None):
        if row is not None:
            if not 0 <= row < len(self.photos):
                return
            photos = [self.photos[row]]
        else:
            photos = self.target_photos()
        if not photos:
            return
        if self.marker == 'pick' and row is not None:
            # Clicking the mark of a thumbnail cycles through the three states.
            following = {PICK_NEUTRAL: PICK_SELECT, PICK_SELECT: PICK_REJECT, PICK_REJECT: PICK_NEUTRAL}
            self.change(photos, pick=following[photos[0].pick])
        else:
            self.change(photos, pick=PICK_NEUTRAL if photos[0].flagged else PICK_SELECT)

    def refresh(self):
        if self.session:
            self.session.refresh()
            self.failed.clear()
            self.apply_filters()
            self.update_project_state()
            self.review.start_analysis()
            if self.review_active:
                self.review.rebuild()

    def export(self, kind='stars'):
        if not self.session:
            return
        if original_inventory(self.session.root) != self.baseline:
            QMessageBox.warning(self, 'Export interrompu', 'Le dossier a changé depuis son ouverture. Rouvrez-le avant l’export.')
            return
        if kind == 'picks':
            photos = [p for p in self.photos if p.pick == PICK_SELECT]
            destination, empty = self.session.session_dir / EXPORT_PICKS_DIR_NAME, 'Aucune photo retenue dans la sélection filtrée.'
        else:
            # A rejected photo stays out of the export, whatever its stars.
            photos = [p for p in self.photos if p.stars == 5 and p.pick != PICK_REJECT]
            destination, empty = self.session.session_dir / EXPORT_DIR_NAME, 'Aucune photo à cinq étoiles dans la sélection filtrée.'
        if not photos:
            QMessageBox.information(self, 'Export', empty)
            return
        try:
            for photo in photos:
                export_original_copy(photo.path, destination/photo.relative_path)
            if original_inventory(self.session.root) != self.baseline:
                raise RuntimeError('Le dossier des originaux a changé pendant l’export.')
            QMessageBox.information(self, 'Export terminé', f'{len(photos)} originaux copiés sans conversion.\n{destination}')
        except Exception as error:
            QMessageBox.warning(self, 'Export incomplet', str(error))

    def closeEvent(self, event):
        self.closing = True
        self.detail_timer.stop()
        self.visible_timer.stop()
        self.preload_timer.stop()
        self.screen_timer.stop()
        self.save_timer.stop()
        self.review.stop_analysis()
        if self.session:
            self.save_session()
            self.recents.add(self.project_path, self.session.photos)
            self.review.save_analysis()
        self.pool.clear()
        self.pool.waitForDone()
        self.review.analysis_pool.waitForDone()
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder')
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    if sys.platform == 'win32':
        # Otherwise the taskbar shows the icon of Python.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('TriPhoto')
    app.setWindowIcon(QIcon(str(ASSETS/'triphoto.ico')))
    window = Window(args.folder)
    window.showMaximized()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
