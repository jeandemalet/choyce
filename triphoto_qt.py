"""High-DPI Qt interface. Original files stay unchanged; older sessions remain readable."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import re
import shutil
import sys
import math
import threading
import time
from io import BytesIO
from pathlib import Path
from collections import OrderedDict

from PySide6.QtCore import Qt, QSize, QRectF, QPointF, Signal, Slot, QObject, QRunnable, QThreadPool, QTimer, QItemSelectionModel, QSettings, QUrl
from PySide6.QtGui import QCursor, QDesktopServices, QIcon, QImage, QPainter, QPainterPath, QColor, QFont, QShortcut, QKeySequence, QColorSpace, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QSplitter, QListWidget, QListWidgetItem, QFrame, QScrollArea, QButtonGroup,
    QStackedWidget, QAbstractItemView, QFileDialog, QProgressBar, QStyledItemDelegate, QStyle, QMenu, QColorDialog)
from PIL import Image, ImageCms, ImageWin
from triphoto import (APP_NAME, APP_VERSION, PhotoSession, sort_photos, SORT_OPTIONS, load_screen_preview, original_inventory,
    export_original_copy, EXPORT_DIR_NAME, PICK_NEUTRAL, PICK_REJECT, PICK_SELECT)
from triphoto_prefs import (ALIASES, PreferencesDialog, Recents, ScanJob, age_label, ask, inform, path_key, shortcut_map)
from triphoto_review import MODES, BackgroundPicker, MarkerFilter, NoteFilter, RatingBar, ReviewPage
from triphoto_update import UpdateCheck, UpdateDownload, can_install, launch_installer, write_installer
from triphoto_theme import (ACCENT, ASSETS, BACKGROUNDS, FAINT, INK, RAISED, REJECT, SELECT, STYLE, TEXT,
    contrast_on, draw_check, draw_clear_star, draw_cross, draw_flag, draw_heart, draw_stars)

# Tests point this to a separate store, so that they never touch the user's settings.
SETTINGS_SCOPE = os.environ.get('TRIPHOTO_SETTINGS', 'TriPhoto')
ROW_HEIGHT = 152
EXPORT_PICKS_DIR_NAME = 'Export_Retenues'
# Decoded previews held in memory, most of it for the sharp photos prepared ahead.
MEMORY_LIMIT = 1200*1024*1024
AHEAD_MAX, BEHIND = 10, 2
EXPORT_WORK_DIR_NAME = 'Export_en_cours'
DATED_EXPORT = re.compile(r'^Export_\d{4}-\d{2}-\d{2}_\d{2}h\d{2}(-\d+)?$')


def previous_export_count(base):
    """Files of the main export; the dated subfolders are separate exports, kept apart."""
    if not base.is_dir():
        return 0
    count = 0
    for child in base.iterdir():
        if child.is_dir():
            if not DATED_EXPORT.match(child.name):
                count += sum(1 for item in child.rglob('*') if item.is_file())
        else:
            count += 1
    return count


class ExportSignals(QObject):
    progress = Signal(int, int)
    checking = Signal()
    finished = Signal(str, str)


class ExportJob(QRunnable):
    """Copy to a work folder first: an interrupted export leaves the previous one intact."""

    def __init__(self, root, baseline, photos, base, replace):
        super().__init__()
        self.root, self.baseline, self.photos, self.base, self.replace = root, baseline, photos, base, replace
        self.signals = ExportSignals()
        self.cancelled = threading.Event()

    def publish(self, work):
        self.base.mkdir(parents=True, exist_ok=True)
        if not self.replace:
            stamp = time.strftime('%Y-%m-%d_%Hh%M')
            folder, suffix = self.base/f'Export_{stamp}', 2
            while folder.exists():
                folder, suffix = self.base/f'Export_{stamp}-{suffix}', suffix+1
            os.replace(work, folder)
            return folder
        for child in self.base.iterdir():
            if child.is_dir():
                if not DATED_EXPORT.match(child.name):
                    shutil.rmtree(child)
            else:
                child.unlink()
        for child in work.iterdir():
            os.replace(child, self.base/child.name)
        work.rmdir()
        return self.base

    def run(self):
        work = self.base.parent/EXPORT_WORK_DIR_NAME
        try:
            shutil.rmtree(work, ignore_errors=True)
            self.signals.checking.emit()
            if original_inventory(self.root) != self.baseline:
                raise RuntimeError('Le dossier a changé depuis son ouverture. Rouvrez-le avant l’export.')
            total = len(self.photos)
            for done, (path, relative) in enumerate(self.photos):
                if self.cancelled.is_set():
                    shutil.rmtree(work, ignore_errors=True)
                    self.signals.finished.emit('', '')
                    return
                self.signals.progress.emit(done, total)
                export_original_copy(path, work/relative)
            self.signals.progress.emit(total, total)
            self.signals.checking.emit()
            if original_inventory(self.root) != self.baseline:
                raise RuntimeError('Le dossier des originaux a changé pendant l’export.')
            self.signals.finished.emit(str(self.publish(work)), '')
        except Exception as error:
            shutil.rmtree(work, ignore_errors=True)
            self.signals.finished.emit('', str(error))
STAR_FILTERS = {'all': 'Toutes les notes', 'ge4': '4 étoiles et plus', 'ge3': '3 étoiles et plus', 'ge2': '2 étoiles et plus',
    'ge1': '1 étoile et plus', 5: '5 étoiles', 4: '4 étoiles', 3: '3 étoiles', 2: '2 étoiles', 1: '1 étoile', 0: '0 étoile'}
FLAG_FILTERS = {'all': 'Tous les drapeaux', 'select': 'Drapeaux uniquement', 'unflagged': 'Sans drapeau'}
PICK_FILTERS = {'all': 'Tous les marqueurs', 'select': 'Retenues', 'neutral': 'Neutres', 'reject': 'Rejetées', 'unrejected': 'Sans les rejetées'}


def app_settings():
    # Under its former name, so that recent projects and preferences stay in place.
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
        self.preview_limit = 512*1024*1024

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
                        if total <= self.preview_limit:
                            break
                        try:
                            path.unlink()
                            total -= size
                        except OSError:
                            pass
            self.signals.ready.emit(str(self.path), self.side, qt_image(image, self.display_profile), '', self.generation)
        except Exception as error:
            self.signals.ready.emit(str(self.path), self.side, QImage(), str(error), self.generation)


class HoverButton(QPushButton):
    entered = Signal()

    def enterEvent(self, event):
        self.entered.emit()
        super().enterEvent(event)


class Viewer(QWidget):
    zoomChanged = Signal(float)
    resized = Signal()
    activity = Signal()
    backgroundChanged = Signal()
    heartClicked = Signal()

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.liked = lambda: None
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
        painter.drawImage(self.image_rect(), self.image)
        liked = self.liked()
        if liked is not None:
            draw_heart(painter, self.heart_rect(), liked)

    def image_rect(self):
        fit = min(max(1, self.width()-24)/self.image.width(), max(1, self.height()-24)/self.image.height())
        width, height = self.image.width()*fit*self.zoom, self.image.height()*fit*self.zoom
        return QRectF((self.width()-width)/2+self.pan.x(), (self.height()-height)/2+self.pan.y(), width, height)

    def heart_rect(self):
        # Zoomed in, the corner of the photo may be out of sight: stay in the corner of what is seen.
        shown = self.image_rect().intersected(QRectF(self.rect()))
        return QRectF(shown.right()-23, shown.top()+8, 15, 14)

    def on_heart(self, position):
        return not self.image.isNull() and self.liked() is not None and self.heart_rect().adjusted(-4, -4, 4, 4).contains(position)

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

    def zoom_from_center(self, factor):
        """What is in the middle of the view stays there, wherever the mouse is."""
        previous = self.zoom
        self.zoom = max(.1, min(8., previous*factor))
        self.pan *= self.zoom/previous
        self.zoomChanged.emit(self.zoom)
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom_from_center(1.15 if delta > 0 else 1/1.15)
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.on_heart(event.position()):
            self.heartClicked.emit()
        elif event.button() == Qt.MouseButton.LeftButton and not self.image.isNull():
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
        if event.button() == Qt.MouseButton.LeftButton and self.on_heart(event.position()):
            self.heartClicked.emit()
        elif event.button() == Qt.MouseButton.LeftButton and not self.image.isNull():
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

    def heart(self, thumb):
        return QRectF(thumb.right()-19, thumb.top()+6, 13, 12)

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
        draw_heart(painter, self.heart(thumb), photo.liked)
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
            if self.heart(rect).adjusted(-4, -4, 4, 4).contains(event.position()):
                photo = index.data(Qt.ItemDataRole.UserRole)
                self.window.change([photo], liked=not photo.liked)
                return True
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
        self.pool.setMaxThreadCount(4)
        self.direction = 1
        self.last_row = -1
        self.setWindowTitle(APP_NAME)
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
        brand = QLabel(APP_NAME, objectName='brand')
        brand.setToolTip(f'Version {APP_VERSION}')
        left.addWidget(brand)
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
        # They hold the filters; the capsules of both pages are what the user sees.
        self.stars.hide()
        self.flags.hide()
        left.addStretch(1)
        right.addStretch(1)
        self.sort = self.combo(right, 'Tri', SORT_OPTIONS)
        right.addSpacing(6)
        segments = QFrame(objectName='segments')
        segment_row = QHBoxLayout(segments)
        segment_row.setContentsMargins(3, 3, 3, 3)
        segment_row.setSpacing(2)
        group = QButtonGroup(self)
        self.view_buttons = {}
        for key, label in [('classic', 'Classique'), *MODES]:
            button = QPushButton(label, objectName='mainseg', checkable=True)
            group.addButton(button)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, value=key: self.set_view(value))
            segment_row.addWidget(button)
            self.view_buttons[key] = button
        self.view_buttons['classic'].setChecked(True)
        self.segments = segments
        self.filter_chip = HoverButton('Filtre  ▾', objectName='ghost')
        self.filter_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.filter_chip.setToolTip('Notes, marqueurs et réglages de la vue')
        self.filter_chip.entered.connect(self.show_filter_panel)
        self.filter_chip.clicked.connect(self.show_filter_panel)
        right.addSpacing(6)
        self.export_button = self.button(right, 'Exporter les 5 étoiles', self.export, 'primary')
        self.prefs_button = self.button(right, 'Préférences', self.open_preferences, 'ghost')
        top.addLayout(left, 1)
        top.addWidget(segments)
        top.addWidget(self.filter_chip)
        top.addLayout(right, 1)
        self.header = header
        layout.addWidget(header)
        splitter = QSplitter()
        self.viewer = Viewer()
        self.viewer.backgroundChanged.connect(self.background_changed)
        self.viewer.liked = lambda: self.current_photo().liked if self.current_photo() else None
        self.viewer.heartClicked.connect(self.toggle_like)
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
        self.note_filter = NoteFilter(self)
        self.marker_filter = MarkerFilter(self)
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
        # Unfolds under the header on hover, over the photos, so as to take no room from them.
        self.filter_panel = QFrame(self, objectName='pill')
        filter_row = QHBoxLayout(self.filter_panel)
        filter_row.setContentsMargins(8, 6, 12, 6)
        filter_row.setSpacing(4)
        filter_row.addWidget(self.note_filter)
        filter_row.addSpacing(6)
        filter_row.addWidget(self.marker_filter)
        filter_row.addWidget(self.review.tools)
        self.filter_panel.hide()
        # Where the mouse is, asked regularly: the enter and leave events of a panel full
        # of buttons, lists and dialogs are not a reliable account of it.
        self.filter_timer = QTimer(self)
        self.filter_timer.setInterval(120)
        self.filter_timer.timeout.connect(self.check_filter_panel)
        self.filter_outside = 0
        layout.addWidget(self.pages, 1)
        # How many large previews stay in memory: more when a page shows more of them at once.
        self.large_keep = 6
        self.export_job = None
        self.update_check = None
        self.update_job = None
        self.export_pool = QThreadPool(self)
        self.export_pool.setMaxThreadCount(1)
        self.export_panel = QFrame(self, objectName='pill')
        export_row = QHBoxLayout(self.export_panel)
        export_row.setContentsMargins(16, 10, 10, 10)
        export_row.setSpacing(12)
        self.export_label = QLabel()
        self.export_label.setMinimumWidth(190)
        export_row.addWidget(self.export_label)
        self.export_bar = QProgressBar()
        self.export_bar.setFixedSize(260, 6)
        self.export_bar.setTextVisible(False)
        export_row.addWidget(self.export_bar)
        self.export_cancel = self.button(export_row, 'Annuler', self.cancel_export, 'ghost')
        self.export_panel.hide()
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
            'flag': self.toggle_flag, 'like': self.toggle_like, 'select': lambda: self.set_pick(PICK_SELECT),
            'reject': lambda: self.set_pick(PICK_REJECT), 'neutral': lambda: self.set_pick(PICK_NEUTRAL),
            'undo': self.undo, 'fit': lambda: self.review.zoom_reset() if self.review_active else self.viewer.set_zoom(1),
            'zoom_in': lambda: self.zoom_by(1.15), 'zoom_out': lambda: self.zoom_by(1/1.15),
            'background': self.cycle_background, 'review': lambda: self.set_review(not self.review_active),
            'shuffle': lambda: self.review.shuffle() if self.review_active else None,
            'mono': lambda: self.review.toggle_mono() if self.review_active else None,
            'refresh': self.refresh, 'open': self.open_folder, 'close': self.close_project, 'preferences': self.open_preferences,
            **{f'rate{value}': (lambda stars=value: self.rate(stars)) for value in range(6)},
            **{f'mode_{mode}': (lambda value=mode: self.set_view(value)) for mode in ('classic', 'toile', 'swipe', 'color', 'wall')}}
        mapping = shortcut_map(self.settings)
        taken = {QKeySequence(key).toString() for keys in mapping.values() for key in keys}
        for name, keys in mapping.items():
            aliases = [key for key in ALIASES.get(name, []) if QKeySequence(key).toString() not in taken]
            for key in keys+aliases:
                self.shortcuts.append(QShortcut(QKeySequence(key), self, activated=commands[name]))
        for key, command in (('Escape', self.leave_review), ('Space', lambda: self.skip_card(1)), ('Backspace', lambda: self.skip_card(-1))):
            self.shortcuts.append(QShortcut(QKeySequence(key), self, activated=command))
        # Up and down browse like left and right, unless assigned in the preferences.
        for key, delta in (('Up', -1), ('Down', 1), ('Shift+Up', -1), ('Shift+Down', 1)):
            if QKeySequence(key).toString() not in taken:
                self.shortcuts.append(QShortcut(QKeySequence(key), self, activated=lambda value=delta: self.browse(value)))
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

    def place_filter_panel(self):
        self.filter_panel.adjustSize()
        center = self.filter_chip.mapTo(self, self.filter_chip.rect().center()).x()
        x = max(8, min(self.width()-self.filter_panel.width()-8, center-self.filter_panel.width()//2))
        self.filter_panel.move(x, self.header.height()+6)
        self.filter_panel.raise_()

    def show_filter_panel(self, *args):
        self.filter_outside = 0
        self.place_filter_panel()
        self.filter_panel.show()
        self.filter_timer.start()

    def over_filter(self, position=None):
        """Over the button or the panel, or in the small gap that separates them."""
        position = self.mapFromGlobal(QCursor.pos()) if position is None else position
        chip = self.filter_chip.rect().translated(self.filter_chip.mapTo(self, self.filter_chip.rect().topLeft()))
        return chip.adjusted(-6, -6, 6, 10).contains(position) or self.filter_panel.geometry().adjusted(-10, -10, 10, 10).contains(position)

    def check_filter_panel(self):
        # A list or a color dialog opened from the panel still belongs to it.
        if self.over_filter() or self.marker_filter.view().isVisible() or QApplication.activeModalWidget() is not None:
            self.filter_outside = 0
        else:
            self.filter_outside += 1
            if self.filter_outside >= 3:
                self.filter_timer.stop()
                self.filter_panel.hide()

    def update_filter_chip(self):
        parts = [combo.currentText() for combo in (self.stars, self.flags) if combo.currentIndex() > 0]
        self.filter_chip.setText(f"Filtre : {' · '.join(parts)}  ▾" if parts else 'Filtre  ▾')

    def set_view(self, key):
        if key == 'classic':
            self.set_review(False)
        else:
            self.open_review_mode(key)

    def sync_view_buttons(self):
        self.view_buttons[self.review.mode if self.review_active else 'classic'].setChecked(True)

    def show_viewer_bar(self):
        self.viewer_bar_timer.start(1700)
        self.place_viewer_bar()
        if self.viewer.image.isNull():
            return
        self.viewer_bar.update()
        self.viewer_bar.show()
        self.viewer_picker.show()

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
            self.viewer.zoom_from_center(factor)

    def set_review(self, active):
        if not (self.session and self.session.photos):
            active = False
        changed = active != self.review_active
        self.review_active = active
        self.update_project_state()
        if changed and active:
            self.review.enter()
        elif changed:
            self.large_keep = 6
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

    def drop_requests(self, wanted, min_side=0):
        """Forget what was queued for photos no longer on screen, so that it does not delay those that are."""
        for key, job in list(self.pending.items()):
            if job.background or key[0] in wanted or key[1] < min_side:
                continue
            try:
                if self.pool.tryTake(job):
                    del self.pending[key]
            except RuntimeError:
                # Finished, with its result still on its way: loaded() will take it out.
                pass

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
            inform(self, 'Projet introuvable', f'Ce dossier est introuvable pour le moment :\n{path}\n\nSi le disque est débranché, rebranchez-le puis réessayez.')
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
        self.sync_view_buttons()
        self.export_button.setVisible(has_photos)
        # Nothing to filter or switch on the welcome screen.
        for control in (self.sort, self.segments, self.filter_chip, self.counter):
            control.setVisible(has_photos)
        if not has_photos:
            self.filter_timer.stop()
            self.filter_panel.hide()
        # Black and white, background and zoom of the panel are those of the other views.
        self.review.tools.setVisible(self.review_active)
        if self.filter_panel.isVisible():
            self.place_filter_panel()
        name = project_label(self.project_path) if self.project_path else ''
        self.setWindowTitle(f'{APP_NAME} · {name}' if self.session else APP_NAME)

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
        self.stop_export()
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
        self.note_filter.sync()
        self.marker_filter.sync()
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
        combo.setFixedWidth({'Tri': 160, 'Notes': 136, 'Marqueurs': 146}[label])
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
            inform(self, 'Ouverture impossible', str(error))
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

    def note_counts(self):
        """Photos per note, among those the marker filter lets through."""
        session = getattr(self, 'session', None)
        if not session:
            return {}
        flag = self.flags.currentData()
        counts = {note: 0 for note in range(6)}
        for photo in session.photos:
            if marker_matches(photo, flag):
                counts[photo.stars] += 1
        return counts

    def apply_filters(self, *args):
        self.note_filter.sync()
        self.marker_filter.sync()
        self.review.sync_filters()
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
        self.viewer.update()

    def request(self, photo, side, priority=0):
        if self.closing:
            return
        key = (str(photo.path), side)
        if key in self.cache or key in self.failed:
            return
        queued = self.pending.get(key)
        if queued is not None:
            # Queued long ago for later, and now wanted sooner: it must not keep its old place.
            try:
                if priority <= queued.priority or not self.pool.tryTake(queued):
                    return
            except RuntimeError:
                return
            del self.pending[key]
        job = Loader(photo.path, side, self.cache_dir, self.display_profile, self.color_generation)
        job.background = priority < 0
        job.priority = priority
        # The cache on disk follows the gallery, so that every photo keeps its preview.
        count = len(self.session.photos) if self.session else 0
        job.preview_limit = int(min(4096, max(512, count*1.2)))*1024*1024
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
            protected = self.protected_paths()
            # A sharp photo that left the anticipated range meanwhile would only push out useful ones.
            useful = self.review_active or side <= 1000 or path in protected
            if useful and (job is None or not job.background):
                self.cache[(path, side)] = image
                self.cache.move_to_end((path, side))
                # Keep large previews bounded separately from lightweight thumbnails.
                others = [key for key in self.cache if key[1] > 1000 and key[0] not in protected]
                for key in others[:-self.large_keep]:
                    self.cache.pop(key, None)
                # Also bound actual decoded bytes, especially on large monitors.
                total = sum(item.sizeInBytes() for item in self.cache.values())
                for key in list(self.cache):
                    if total <= MEMORY_LIMIT:
                        break
                    if key[0] not in protected:
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
        elif side > 1000:
            self.anticipate()

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
        self.viewer.message = 'Préparation de l’aperçu…' if self.photos else 'Aucune photo pour ces filtres'
        self.viewer.update()
        row = self.list.currentRow()
        if row != self.last_row and row >= 0 and self.last_row >= 0:
            self.direction = 1 if row > self.last_row else -1
        self.last_row = row
        if 0 <= row < len(self.photos):
            self.display_best()
            if not self.review_active:
                self.drop_requests(self.protected_paths(), 1000)
            self.request(self.photos[row], 1000, 30)
            # The small previews of the next ones are quick, and ready if the keys outrun the sharp ones.
            for i, priority in ((row+self.direction, 12), (row+2*self.direction, 11), (row+3*self.direction, 10), (row-self.direction, 9)):
                if 0 <= i < len(self.photos):
                    self.request(self.photos[i], 1000, priority)
            self.schedule_detail()
        self.update_counter()

    def preload_background(self):
        if self.closing or not self.photos or any(job.background or key[1] > 1000 for key, job in self.pending.items()):
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

    def detail_side(self):
        side = max(2000, min(6000, int(max(self.viewer.width(), self.viewer.height())*self.devicePixelRatioF()*self.viewer.zoom)))
        return ((side+499)//500)*500

    def has_sharp(self, photo, side):
        path = str(photo.path)
        return any((path, larger) in self.cache for larger in range(side, 6001, 500))

    def anticipated_rows(self):
        """The next photos in the direction of travel, nearest first, then a little behind."""
        row = self.list.currentRow()
        if not 0 <= row < len(self.photos):
            return []
        side = self.detail_side()
        ahead = max(3, min(AHEAD_MAX, int(MEMORY_LIMIT*.8/(side*side*2.67))-BEHIND-1))
        rows = [row+self.direction*n for n in range(1, ahead+1)]+[row-self.direction*n for n in range(1, BEHIND+1)]
        return [r for r in rows if 0 <= r < len(self.photos)]

    def protected_paths(self):
        row = self.list.currentRow()
        current = {str(self.photos[row].path)} if 0 <= row < len(self.photos) else set()
        if self.review_active:
            return current
        return current | {str(self.photos[r].path) for r in self.anticipated_rows()}

    def anticipate(self):
        """Keep the next photos ready, without waiting to reach them.

        One thread stays free, so that a photo reached before its turn starts at once.
        """
        if self.closing or self.review_active or not self.photos:
            return
        side = self.detail_side()
        room = self.pool.maxThreadCount()-1-sum(key[1] > 1000 for key in self.pending)
        for row in self.anticipated_rows():
            if room <= 0:
                return
            photo = self.photos[row]
            key = (str(photo.path), side)
            if not self.has_sharp(photo, side) and key not in self.pending and key not in self.failed:
                self.request(photo, side, 5)
                room -= 1

    def load_detail(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.photos):
            side = self.detail_side()
            if not self.has_sharp(self.photos[row], side):
                self.request(self.photos[row], side, 20)
            self.drop_requests(self.protected_paths(), 1000)
            self.anticipate()

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

    def browse(self, delta):
        """In Swipe, moving on is a decision: a key meant to browse must not make it."""
        if not (self.review_active and self.review.mode == 'swipe'):
            self.navigate(delta)

    def navigate(self, delta):
        if self.review_active:
            self.review.navigate(delta)
        elif self.photos:
            row = max(0, min(len(self.photos)-1, self.list.currentRow()+delta))
            # Explicitly replace selection even if Shift remains held after rating.
            self.list.setCurrentRow(row, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.list.scrollToItem(self.list.item(row))

    def update_counter(self):
        self.update_filter_chip()
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

    def change(self, photos, stars=None, pick=None, liked=None):
        """Single entry point for notes and markers: it is what makes them undoable."""
        if not self.session or not photos:
            return
        self.undo_stack.append([(photo, photo.stars, photo.pick, photo.liked) for photo in photos])
        del self.undo_stack[:-300]
        for photo in photos:
            if stars is not None:
                photo.stars = stars
            if pick is not None:
                photo.pick = pick
            if liked is not None:
                photo.liked = liked
        self.apply_filters()

    def undo(self):
        if not self.undo_stack:
            self.toast('Rien à annuler')
            return
        states = self.undo_stack.pop()
        for photo, stars, pick, liked in states:
            photo.stars, photo.pick, photo.liked = stars, pick, liked
        self.apply_filters()
        if self.review_active:
            self.review.after_undo([state[0] for state in states])
        self.toast('Note annulée', 1200)

    def rate(self, stars):
        self.change(self.target_photos(), stars=stars)

    def toggle_like(self):
        photos = self.target_photos()
        if photos:
            self.change(photos, liked=not photos[0].liked)

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
        if not self.session or self.export_job:
            return
        if kind == 'picks':
            photos = [p for p in self.photos if p.pick == PICK_SELECT]
            destination, empty = self.session.session_dir / EXPORT_PICKS_DIR_NAME, 'Aucune photo retenue dans la sélection filtrée.'
        else:
            # A rejected photo stays out of the export, whatever its stars.
            photos = [p for p in self.photos if p.stars == 5 and p.pick != PICK_REJECT]
            destination, empty = self.session.session_dir / EXPORT_DIR_NAME, 'Aucune photo à cinq étoiles dans la sélection filtrée.'
        if not photos:
            inform(self, 'Export', empty)
            return
        previous = previous_export_count(destination)
        replace = True
        if previous:
            choice = ask(self, 'Un export existe déjà',
                         f'Ce dossier contient déjà {previous} photo{"s" if previous > 1 else ""} :\n{destination}\n\n'
                         f'Remplacer efface ces photos une fois les {len(photos)} nouvelles copiées. '
                         'Un nouveau sous-dossier les garde et range cet export à part, à son nom et à sa date.',
                         [('cancel', 'Annuler', False), ('replace', 'Remplacer', False), ('new', 'Nouveau sous-dossier', True)])
            if choice not in ('replace', 'new'):
                return
            replace = choice == 'replace'
        job = ExportJob(self.session.root, self.baseline, [(p.path, p.relative_path) for p in photos], destination, replace)
        job.signals.progress.connect(self.export_progress, Qt.ConnectionType.QueuedConnection)
        job.signals.checking.connect(self.export_checking, Qt.ConnectionType.QueuedConnection)
        job.signals.finished.connect(lambda folder, error, count=len(photos): self.export_done(job, folder, error, count), Qt.ConnectionType.QueuedConnection)
        self.export_job = job
        self.export_button.setEnabled(False)
        self.export_checking()
        self.export_pool.start(job)

    def check_for_update(self):
        self.update_check = UpdateCheck(APP_VERSION)
        self.update_check.signals.found.connect(self.update_found, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self.update_check)

    def update_found(self, release):
        """A newer version on GitHub. The packaged program installs it; a source folder only says so."""
        if self.closing or self.export_job or self.update_job:
            return
        title = f"{APP_NAME} {release['version']} est disponible"
        if can_install() and release['url']:
            if ask(self, title, f'Vous utilisez la version {APP_VERSION}. La mise à jour se télécharge en arrière-plan, puis le programme redémarre. Vos projets ne sont pas touchés.',
                   [('later', 'Plus tard', False), ('update', 'Mettre à jour', True)]) == 'update':
                self.start_update(release)
        elif ask(self, title, f'Vous utilisez la version {APP_VERSION}.', [('later', 'Plus tard', False), ('page', 'Voir la version', True)]) == 'page':
            QDesktopServices.openUrl(QUrl(release['page']))

    def start_update(self, release):
        job = UpdateDownload(release['url'], release['digest'])
        job.signals.progress.connect(self.update_progress, Qt.ConnectionType.QueuedConnection)
        job.signals.ready.connect(self.update_ready, Qt.ConnectionType.QueuedConnection)
        self.update_job = job
        self.export_button.setEnabled(False)
        self.export_cancel.hide()
        self.export_checking()
        self.export_label.setText('Téléchargement de la mise à jour…')
        QThreadPool.globalInstance().start(job)

    def update_progress(self, done, total):
        self.export_label.setText(f'Mise à jour : {done//1048576} sur {total//1048576} Mo')
        self.export_bar.setRange(0, total)
        self.export_bar.setValue(done)

    def update_ready(self, source, error):
        self.update_job = None
        self.export_panel.hide()
        self.export_cancel.show()
        self.export_button.setEnabled(True)
        if error:
            inform(self, 'Mise à jour impossible', f'{error}\n\nLa version actuelle reste en place.')
        elif source and ask(self, 'Mise à jour prête', 'Le programme va se fermer et redémarrer sur la nouvelle version.',
                            [('later', 'Plus tard', False), ('restart', 'Redémarrer', True)]) == 'restart':
            launch_installer(write_installer(source, Path(sys.executable).parent, os.getpid()))
            self.close()

    def place_export_panel(self):
        self.export_panel.adjustSize()
        self.export_panel.move((self.width()-self.export_panel.width())//2, self.height()-self.export_panel.height()-84)
        self.export_panel.raise_()

    def resizeEvent(self, event):
        self.place_export_panel()
        self.place_filter_panel()
        super().resizeEvent(event)

    def export_checking(self):
        self.export_label.setText('Vérification des originaux…')
        self.export_bar.setRange(0, 0)
        self.place_export_panel()
        self.export_panel.show()

    def export_progress(self, done, total):
        self.export_label.setText(f'Export : {done} sur {total} photos')
        self.export_bar.setRange(0, total)
        self.export_bar.setValue(done)

    def cancel_export(self):
        if self.export_job:
            self.export_job.cancelled.set()
            self.export_label.setText('Annulation…')

    def stop_export(self):
        """Closing a project or the program: nothing half done stays behind."""
        if self.export_job:
            self.cancel_export()
            self.export_pool.waitForDone()
            self.export_job = None
        self.export_panel.hide()
        self.export_button.setEnabled(True)

    def export_done(self, job, folder, error, count):
        if job is not self.export_job:
            return
        self.export_job = None
        self.export_panel.hide()
        self.export_button.setEnabled(True)
        if error:
            inform(self, 'Export incomplet', f'{error}\n\nL’export précédent est resté tel quel.')
        elif folder:
            choice = ask(self, 'Export terminé', f'{count} originaux copiés sans conversion.\n{folder}',
                         [('close', 'Fermer', False), ('open', 'Ouvrir le dossier', True)])
            if choice == 'open':
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def closeEvent(self, event):
        self.closing = True
        self.stop_export()
        if self.update_job:
            self.update_job.cancelled.set()
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
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('VisionBoard')
    app.setWindowIcon(QIcon(str(ASSETS/'triphoto.ico')))
    window = Window(args.folder)
    window.showMaximized()
    # A few seconds later: opening the project comes first.
    QTimer.singleShot(4000, window.check_for_update)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
