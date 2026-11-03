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

from PySide6.QtCore import Qt, QPoint, QSize, QRectF, QPointF, Signal, Slot, QObject, QRunnable, QThreadPool, QTimer, QItemSelection, QItemSelectionModel, QSettings, QStandardPaths, QUrl
from PySide6.QtGui import QPixmap, QCursor, QDesktopServices, QIcon, QImage, QPainter, QPainterPath, QColor, QFont, QShortcut, QKeySequence, QColorSpace, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QSplitter, QListWidget, QListWidgetItem, QFrame, QScrollArea, QButtonGroup,
    QStackedWidget, QAbstractItemView, QDialog, QFileDialog, QLineEdit, QProgressBar, QStyledItemDelegate, QStyle, QMenu, QColorDialog)
from PIL import Image, ImageCms, ImageWin
from triphoto import (export_folder_name, APP_NAME, APP_VERSION, PhotoSession, sort_photos, SORT_OPTIONS, load_screen_preview, original_inventory,
    export_original_copy, EXPORT_DIR_NAME, PICK_NEUTRAL, PICK_REJECT, PICK_SELECT, SESSION_DIR_NAME)
from triphoto_custom import BucketFilter, BucketStrip, CustomPage, PopupMenu, ReviewTabs, SelectionSession
from triphoto_portfolio import MANIFEST_FILE, Portfolio, PortfolioSession, RelocateJob, UNSORTED
from triphoto_prefs import (ALIASES, PreferencesDialog, Recents, ScanJob, age_label, ask, inform, locate_photos, locate_project, moved_path, path_key, shortcut_map)
from triphoto_review import MODES, BackgroundPicker, ChoiceFilter, MarkerFilter, NoteFilter, RatingBar, ReviewPage
from triphoto_update import UpdateCheck, UpdateDownload, can_install, launch_installer, write_installer
from triphoto_theme import (ACCENT, ASSETS, BACKGROUNDS, FAINT, INK, RAISED, REJECT, SELECT, STYLE, TEXT,
    contrast_on, draw_bookmark, draw_bucket_chips, draw_check, draw_clear_star, draw_cross, draw_flag, draw_heart, draw_send, draw_stars)

# Tests point this to a separate store, so that they never touch the user's settings.
SETTINGS_SCOPE = os.environ.get('TRIPHOTO_SETTINGS', 'TriPhoto')
ROW_HEIGHT = 152
# One subfolder per export, named after what it holds. Earlier galleries keep their
# Export_5_Etoiles and Export_Retenues where they are: nothing moves them.
EXPORT_ROOT_NAME = 'Exportation'
OLD_EXPORT_DIRS = (EXPORT_DIR_NAME, 'Export_Retenues')
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
        # Beside the exports rather than among them: no export name can land on it.
        work = self.base.parent.parent/EXPORT_WORK_DIR_NAME
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


# Fitted, a photo stops above the floating bar instead of lying under it.
BAR_ROOM = 66


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
        fit = min(max(1, self.width()-24)/self.image.width(), max(1, self.height()-12-BAR_ROOM)/self.image.height())
        width, height = self.image.width()*fit*self.zoom, self.image.height()*fit*self.zoom
        center = self.fit_center()
        return QRectF(center.x()-width/2+self.pan.x(), center.y()-height/2+self.pan.y(), width, height)

    def fit_center(self):
        return QPointF(self.width()/2, (self.height()+12-BAR_ROOM)/2)

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
            center = self.fit_center()
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

    def bookmark(self, thumb):
        return QRectF(thumb.left()+7, thumb.top()+6, 11, 13)

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
        chips = self.window.photo_buckets(photo)
        if chips:
            draw_bucket_chips(painter, thumb.left()+6, thumb.top()+24, thumb.width()-12, chips)
        # Always there, to be clicked. A thin dark edge keeps it readable on a light photo, like the heart.
        keep = self.bookmark(thumb)
        draw_bookmark(painter, keep.adjusted(-.8, -.8, .8, .8), QColor(0, 0, 0, 110))
        draw_bookmark(painter, keep, '#ffffff', self.window.in_portfolio(photo))
        if photo.publish:
            draw_send(painter, QRectF(keep.left()+16, thumb.top()+6, 13, 13), '#ffffff', True)
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
            photo = index.data(Qt.ItemDataRole.UserRole)
            if self.bookmark(rect).adjusted(-4, -4, 4, 4).contains(event.position()):
                self.window.list.on_control = True
                self.window.toggle_portfolio(photo)
                return True
            if self.heart(rect).adjusted(-4, -4, 4, 4).contains(event.position()):
                self.window.list.on_control = True
                self.window.change(self.window.with_selection(photo), liked=not photo.liked)
                return True
            if y >= rect.bottom()-26:
                if x >= rect.right()-30:
                    self.window.list.on_control = True
                    self.window.toggle_flag(index.row())
                    return True
                if rect.x() <= x < rect.x()+17:
                    self.window.list.on_control = True
                    if photo.stars:
                        self.window.change(self.window.with_selection(photo), stars=0)
                    return True
                if rect.x()+17 <= x < rect.x()+17+5*14:
                    value = int((x-rect.x()-17)//14)+1
                    self.window.list.on_control = True
                    self.window.change(self.window.with_selection(photo), stars=0 if photo.stars == value else value)
                    return True
        return super().editorEvent(event, model, option, index)


class PhotoList(QListWidget):
    """The ribbon. A click on the note of a thumbnail leaves the selection as it is."""
    on_control = False
    owner = None
    pressed = None
    bucket_dragging = False

    def selectionCommand(self, index, event=None):
        if self.on_control:
            return QItemSelectionModel.SelectionFlag.NoUpdate
        return super().selectionCommand(index, event)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        self.pressed = (item.data(Qt.ItemDataRole.UserRole), event.position()) if item and event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # In a selection, a thumbnail goes to the buckets under the view.
        if self.pressed and self.owner.selection and (self.bucket_dragging or (event.position()-self.pressed[1]).manhattanLength() > 14):
            self.bucket_dragging = True
            self.owner.bucket_drag(event.globalPosition().toPoint(), self.pressed[0])
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        dragged = self.pressed[0] if self.pressed and self.bucket_dragging else None
        self.pressed, self.bucket_dragging = None, False
        if dragged:
            self.on_control = True
        super().mouseReleaseEvent(event)
        self.on_control = False
        if dragged:
            self.owner.bucket_drop(event.globalPosition().toPoint(), dragged)


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
        menu.addAction('Retrouver le dossier…', lambda: self.home.window.repair_recent(self.entry['path'], True))
        menu.addAction('Retirer de la liste', lambda: self.home.window.forget_recent(self.entry['path']))
        menu.exec(event.globalPos())


class CustomCard(RecentCard):
    """A saved selection: its name and one of its photos."""

    def __init__(self, session, home):
        super().__init__({'path': session.name}, home)
        self.session = session
        photos, buckets = len(session.photos), len(session.buckets)
        self.name.setText(session.name)
        self.name.setToolTip(session.name)
        self.meta.setText(f"{photos} photo{'s' if photos > 1 else ''} · {buckets} bucket{'s' if buckets > 1 else ''} · {age_label(session.data.get('updated'))}")
        self.more = QPushButton('•••', self, objectName='ghost')
        self.more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.more.setFixedSize(38, 26)
        self.more.setStyleSheet('background: rgba(15, 15, 16, 190); color: #ececec; padding: 0; border-radius: 7px;')
        self.more.clicked.connect(lambda *_: self.show_menu(self.more.mapToGlobal(self.more.rect().topLeft())+QPoint(0, 34)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.more.move(self.width()-self.more.width()-8, 8)

    def show_menu(self, where):
        custom = self.home.window.custom
        session = self.session
        PopupMenu(self.home.window, [('Supprimer la sélection…', lambda: custom.delete_session(session))]).open_at(where)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.home.window.open_custom(self.session)

    def contextMenuEvent(self, event):
        self.show_menu(event.globalPos())


class PortfolioCard(QFrame):
    """The portfolio, ahead of the projects and wider than they are."""
    HEIGHT = 176

    def __init__(self, home):
        super().__init__(objectName='featured')
        self.home = home
        self.covers = {}
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(self.HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(4)
        layout.addStretch(1)
        self.title = QLabel('Portfolio', objectName='title')
        layout.addWidget(self.title)
        self.meta = QLabel(objectName='muted')
        layout.addWidget(self.meta)
        self.place = QLabel(objectName='faint')
        layout.addWidget(self.place)
        self.text_width = 300

    def refresh(self):
        portfolio = self.home.window.portfolio
        self.covers = {}
        folder = portfolio.folder
        if folder is None:
            self.meta.setText('Vos meilleures photos, tous projets confondus')
            self.place.setText('Cliquez pour choisir son dossier')
        elif not portfolio.available():
            self.meta.setText('Dossier ou disque introuvable')
            self.place.setText(str(folder))
        else:
            photos, projects = portfolio.summary()
            self.meta.setText(f"{photos} photo{'s' if photos > 1 else ''} · {projects} projet{'s' if projects > 1 else ''}" if photos
                              else 'Vide pour le moment : touche K, ou le signet sous une photo')
            self.place.setText(self.place.fontMetrics().elidedText(str(folder), Qt.TextElideMode.ElideMiddle, self.text_width))
            for index, relative in enumerate(portfolio.covers()):
                self.home.window.pool.start(CoverJob(f'portfolio:{index}', folder, portfolio.file_of(relative), self.home.window.display_profile, self.home.cover_signals), 2)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.covers:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 11, 11)
        painter.setClipPath(clip)
        # From the right edge, as many of the latest photos as the width allows.
        x, height = float(self.width()), float(self.height()-2)
        for index in sorted(self.covers):
            cover = self.covers[index]
            width = height*cover.width()/cover.height()
            x -= width+2
            if x < self.text_width+60:
                break
            painter.drawImage(QRectF(x, 1, width, height), cover)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.home.window.open_portfolio()

    def contextMenuEvent(self, event):
        window = self.home.window
        menu = QMenu(self)
        menu.addAction("Choisir l'emplacement…", window.choose_portfolio_folder)
        menu.addAction('Ouvrir le dossier', window.reveal_portfolio).setEnabled(window.portfolio.available())
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
        self.portfolio_card = PortfolioCard(self)
        self.column.addWidget(self.portfolio_card)
        self.column.addSpacing(30)
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
        self.column.addSpacing(34)
        custom_top = QHBoxLayout()
        custom_top.addWidget(QLabel('SÉLECTION CUSTOM', objectName='section'))
        custom_top.addStretch(1)
        self.custom_button = QPushButton('Nouvelle sélection', objectName='ghost')
        self.custom_button.clicked.connect(lambda checked=False: window.open_custom())
        custom_top.addWidget(self.custom_button)
        self.column.addLayout(custom_top)
        self.column.addSpacing(10)
        self.custom_holder = QWidget(objectName='home')
        self.custom_grid = QGridLayout(self.custom_holder)
        self.custom_grid.setContentsMargins(0, 0, 0, 0)
        self.custom_grid.setSpacing(14)
        self.column.addWidget(self.custom_holder)
        self.custom_cards = {}
        self.column.addStretch(1)
        self.cover_signals = CoverSignals()
        self.cover_signals.ready.connect(self.cover_ready, Qt.ConnectionType.QueuedConnection)

    def refresh(self):
        # The portfolio has its own place: never a second time among the projects.
        entries = [entry for entry in self.window.recents.entries() if not self.window.is_portfolio_path(entry['path'])]
        self.portfolio_card.refresh()
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
        for card in self.custom_cards.values():
            self.custom_grid.removeWidget(card)
            card.deleteLater()
        self.custom_cards = {}
        for session in self.window.custom.store.sessions():
            key = f"custom:{session.data['id']}"
            self.custom_cards[key] = CustomCard(session, self)
            cover = session.cover()
            if cover:
                self.window.pool.start(CoverJob(key, Path(cover).parent, Path(cover), self.window.display_profile, self.cover_signals), 2)
        self.custom_holder.setVisible(bool(self.custom_cards))
        self.columns = 0
        self.arrange()

    def arrange(self):
        columns = max(2, (self.width()-112)//290)
        if columns == self.columns:
            return
        self.columns = columns
        for index, card in enumerate(self.cards.values()):
            self.grid.addWidget(card, index//columns, index % columns)
        for index, card in enumerate(self.custom_cards.values()):
            self.custom_grid.addWidget(card, index//columns, index % columns)
        for column in range(10):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
            self.custom_grid.setColumnStretch(column, 1 if column < columns else 0)

    def resizeEvent(self, event):
        self.arrange()

    def cover_ready(self, key, image, error):
        if key.startswith('portfolio:'):
            if not image.isNull():
                self.portfolio_card.covers[int(key[10:])] = image
                self.portfolio_card.update()
            return
        card = self.cards.get(key) or self.custom_cards.get(key)
        if card is None:
            return
        if error == 'missing':
            card.set_missing()
        elif not image.isNull():
            card.cover = image
            card.update()


class ExportDialog(QDialog):
    """Which photos of the gallery to export, and the folder they go to. Starts from the filters in use."""

    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        self.named_by_hand = False
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(objectName='dialog')
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(10)
        layout.addWidget(QLabel('Exporter des photos', objectName='dialogTitle'))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        self.stars = self.copy_combo(window.stars)
        self.marker = self.copy_combo(window.flags)
        grid.addWidget(QLabel('Notes', objectName='muted'), 0, 0)
        grid.addWidget(self.stars, 0, 1)
        grid.addWidget(QLabel('Marqueur', objectName='muted'), 1, 0)
        grid.addWidget(self.marker, 1, 1)
        choices = window.choices
        toggles = QHBoxLayout()
        toggles.setSpacing(2)
        self.portfolio = self.toggle(toggles, 'Portfolio', choices.portfolio.isChecked())
        self.portfolio.setVisible(not window.viewing_portfolio())
        self.publish = self.toggle(toggles, 'À publier', choices.publish.isChecked())
        toggles.addStretch(1)
        grid.addWidget(QLabel('Seulement', objectName='muted'), 2, 0)
        grid.addLayout(toggles, 2, 1)
        self.project = None
        row = 3
        if window.viewing_portfolio():
            self.project = self.copy_combo(choices.project)
            grid.addWidget(QLabel('Projet', objectName='muted'), row, 0)
            grid.addWidget(self.project, row, 1)
            row += 1
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.textEdited.connect(self.renamed)
        grid.addWidget(QLabel('Dossier', objectName='muted'), row, 0)
        grid.addWidget(self.name, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.count = QLabel()
        layout.addWidget(self.count)
        self.place = QLabel(objectName='faint')
        self.place.setWordWrap(True)
        self.place.setFixedWidth(440)
        layout.addWidget(self.place)
        old = [name for name in OLD_EXPORT_DIRS if (window.session.session_dir/name).is_dir()]
        if old:
            note = QLabel(f"Les exports déjà faits restent à leur place, dans {' et '.join(old)}.", objectName='faint')
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('Annuler', objectName='ghost')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.go = QPushButton('Exporter', objectName='primary')
        self.go.clicked.connect(self.accept)
        buttons.addWidget(self.go)
        layout.addLayout(buttons)
        absent = len(getattr(window.session, 'absent', ()))
        if absent:
            note = QLabel(f"{absent} photo{'s' if absent > 1 else ''} du portfolio introuvable{'s' if absent > 1 else ''} pour le moment, disque débranché ou fichier déplacé : {'elles ne seront pas exportées' if absent > 1 else 'elle ne sera pas exportée'}.", objectName='faint')
            note.setWordWrap(True)
            note.setFixedWidth(440)
            layout.insertWidget(layout.count()-2, note)
        # The portfolio is what one exports whole; elsewhere, the usual export is the 5 stars.
        if not window.viewing_portfolio() and not (window.stars.currentIndex() > 0 or window.flags.currentIndex() > 0 or choices.active_labels()):
            # No filter in use: the usual export, which one click leaves as it is.
            self.stars.setCurrentIndex(self.stars.findData(5))
        for combo in (self.stars, self.marker, self.project):
            if combo is not None:
                combo.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def copy_combo(self, source):
        combo = QComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for index in range(source.count()):
            combo.addItem(source.itemText(index), source.itemData(index))
        combo.setCurrentIndex(source.currentIndex())
        return combo

    def toggle(self, row, text, checked):
        button = QPushButton(text, objectName='seg', checkable=True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setChecked(checked)
        button.clicked.connect(self.refresh)
        row.addWidget(button)
        return button

    def renamed(self, *args):
        self.named_by_hand = True
        self.refresh()

    def photos(self):
        return self.owner.export_selection(self.stars.currentData(), self.marker.currentData(), self.portfolio.isChecked(),
                                           self.publish.isChecked(), self.project.currentData() if self.project else 'all')

    def suggested_name(self):
        """Named after its settings: "5 étoiles", "4 étoiles et plus, À publier"."""
        if self.owner.review_active and self.owner.review.mode == 'instagram':
            return 'Instagram'
        parts = []
        if self.stars.currentIndex() > 0:
            parts.append(self.stars.currentText())
        if self.marker.currentIndex() > 0:
            parts.append(self.marker.currentText().replace(' uniquement', ''))
        parts += [button.text() for button in (self.portfolio, self.publish) if button.isChecked()]
        if self.project and self.project.currentIndex() > 0:
            parts.append(self.project.currentData())
        return ', '.join(parts) or ('Portfolio' if self.owner.viewing_portfolio() else 'Toutes les photos')

    def folder_name(self):
        return export_folder_name(self.name.text())

    def refresh(self, *args):
        if not self.named_by_hand:
            self.name.setText(self.suggested_name())
        count = len(self.photos())
        self.count.setText(f"{count} photo{'s' if count > 1 else ''} à copier" if count else 'Aucune photo pour ces réglages')
        name = self.folder_name()
        self.place.setText(str(self.owner.session.session_dir/EXPORT_ROOT_NAME/name).replace('\\', '\\\u200b'))
        self.go.setEnabled(bool(count and name))


class ActivityBar(QFrame):
    """What is working in the background, at the bottom of the window. Short tasks stay silent: a flash at every photo would only distract."""
    DELAY = .7

    def __init__(self, window):
        super().__init__(window, objectName='toast')
        self.window_ = window
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(10)
        self.label = QLabel()
        row.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setFixedSize(130, 5)
        self.bar.setTextVisible(False)
        row.addWidget(self.bar)
        self.peaks = {}
        self.since = None
        self.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def place(self):
        self.adjustSize()
        # In the other views, above the settings along the bottom.
        strip = self.window_.bucket_strip.height()+36 if self.window_.bucket_strip.isVisible() else 0
        self.move(12, self.window_.height()-self.height()-strip-(168 if self.window_.custom_active else 68 if self.window_.review_active else 12))
        self.raise_()
        self.show()

    def announce(self, text):
        """For what blocks the interface: shown at once, with no progress to tell."""
        self.label.setText(text)
        self.bar.setRange(0, 0)
        self.place()
        self.repaint()

    def poll(self):
        tasks = self.window_.background_tasks()
        self.peaks = {label: max(self.peaks.get(label, 0), left) for label, left in tasks}
        if not tasks:
            self.since = None
            self.hide()
            return
        now = time.monotonic()
        self.since = self.since or now
        if now-self.since < self.DELAY:
            return
        # The largest work: a preview loaded on the way must not take its place for an instant.
        label, left = max(tasks, key=lambda task: self.peaks[task[0]])
        peak = self.peaks[label]
        self.label.setText(f'{label} : {peak-left} sur {peak}')
        self.bar.setRange(0, peak)
        self.bar.setValue(peak-left)
        self.place()


class Window(QMainWindow):
    def __init__(self, folder=None):
        super().__init__()
        self.settings = app_settings()
        self.marker = self.settings.value('marker', 'flag')
        self.recents = Recents(self.settings)
        # The custom selection open like a project, if any.
        self.selection = None
        self.portfolio = Portfolio(self.settings)
        self.portfolio.failed.connect(self.portfolio_failed)
        self.portfolio.changed.connect(self.portfolio_changed)
        self.relocating = None
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
        self.home_button = self.button(left, '‹ Accueil', self.go_home, 'ghost')
        self.home_button.setToolTip('Revenir à l’accueil. Vos notes et vos rangements sont enregistrés.')
        self.home_button.hide()
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
        for key, label in [('buckets', 'Buckets'), ('classic', 'Classique'), *MODES]:
            button = QPushButton(label, objectName='mainseg', checkable=True)
            button.setMinimumWidth(button.sizeHint().width())
            group.addButton(button)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, value=key: self.set_view(value))
            segment_row.addWidget(button)
            self.view_buttons[key] = button
        self.view_buttons['classic'].setChecked(True)
        self.view_buttons['buckets'].hide()
        self.segments = segments
        self.filter_chip = HoverButton('Filtre  ▾', objectName='ghost')
        self.filter_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.filter_chip.setToolTip('Notes, marqueurs et réglages de la vue')
        self.filter_chip.clicked.connect(self.show_filter_panel)
        right.addSpacing(6)
        self.export_button = self.button(right, 'Exporter…', self.export, 'primary')
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
        self.list = PhotoList()
        self.list.owner = self
        self.list.setMinimumWidth(250)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setItemDelegate(PhotoDelegate(self))
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(self.list)
        splitter.setSizes([1180, 260])
        self.viewer_bar = RatingBar(self, self.viewer, show_name=False)
        self.viewer_bar.rated.connect(self.rate)
        self.viewer_bar.marked.connect(self.set_pick)
        self.viewer_bar.portfolio.connect(self.toggle_portfolio)
        self.viewer_bar.publish.connect(self.toggle_publish)
        self.viewer_bar.hide()
        self.viewer_picker = BackgroundPicker(self, self.viewer, floating=True)
        self.viewer_picker.hide()
        self.note_filter = NoteFilter(self)
        self.marker_filter = MarkerFilter(self)
        self.choices = ChoiceFilter(self)
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
        self.custom = CustomPage(self, SETTINGS_SCOPE, project_label)
        self.custom_active = False
        self.pages.addWidget(self.custom)
        self.bucket_strip = BucketStrip(self)
        self.review_tabs = ReviewTabs(self)
        self.bucket_ghost = QLabel(self)
        self.bucket_ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.bucket_ghost.hide()
        # Large and in the center of the window, every filter in view; a click beside it closes it.
        self.filter_scrim = QPushButton(self, objectName='filterScrim', flat=True)
        self.filter_scrim.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.filter_scrim.setStyleSheet('QPushButton#filterScrim { background: rgba(0, 0, 0, 120); border: none; border-radius: 0; }')
        self.filter_scrim.clicked.connect(lambda *_: self.hide_filter_panel())
        self.filter_scrim.hide()
        self.filter_panel = QFrame(self, objectName='dialog')
        filter_lines = QVBoxLayout(self.filter_panel)
        filter_lines.setContentsMargins(26, 22, 26, 20)
        filter_lines.setSpacing(6)
        filter_top = QHBoxLayout()
        filter_top.addWidget(QLabel('Filtres', objectName='dialogTitle'))
        filter_top.addSpacing(10)
        self.filter_count = QLabel(objectName='muted')
        filter_top.addWidget(self.filter_count)
        filter_top.addStretch(1)
        self.button(filter_top, 'Tout effacer', self.clear_filters, 'ghost')
        self.button(filter_top, 'Fermer', self.hide_filter_panel, 'primary')
        filter_lines.addLayout(filter_top)
        def section(title, *widgets):
            holder = QWidget()
            lines = QVBoxLayout(holder)
            lines.setContentsMargins(0, 12, 0, 0)
            lines.setSpacing(6)
            lines.addWidget(QLabel(title, objectName='section'))
            for widget in widgets:
                lines.addWidget(widget)
            filter_lines.addWidget(holder)
            return holder
        self.bucket_rule = BucketFilter(self)
        self.bucket_area = QScrollArea()
        self.bucket_area.setWidgetResizable(True)
        self.bucket_area.setFrameShape(QFrame.Shape.NoFrame)
        self.bucket_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bucket_area.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }')
        self.bucket_area.setWidget(self.bucket_rule)
        self.bucket_section = section('BUCKETS', self.bucket_area)
        section('NOTE', self.note_filter)
        section('MARQUEUR', self.marker_filter)
        section('SEULEMENT', self.choices)
        self.tools_section = section('AFFICHAGE', self.review.tools)
        self.review.tools.layout().setContentsMargins(0, 0, 0, 0)
        self.filter_panel.setMinimumWidth(620)
        self.filter_panel.hide()
        layout.addWidget(self.pages, 1)
        layout.addWidget(self.review_tabs)
        layout.addWidget(self.bucket_strip)
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
        self.activity = ActivityBar(self)
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
            'flag': self.toggle_flag, 'like': self.toggle_like, 'portfolio': self.toggle_portfolio, 'publish': self.toggle_publish, 'select': lambda: self.set_pick(PICK_SELECT),
            'reject': lambda: self.set_pick(PICK_REJECT), 'neutral': lambda: self.set_pick(PICK_NEUTRAL),
            'select_all': self.select_all, 'deselect': self.deselect, 'undo': self.undo, 'fit': self.fit,
            'zoom_in': lambda: self.zoom_by(1.15), 'zoom_out': lambda: self.zoom_by(1/1.15),
            'background': self.cycle_background, 'review': lambda: self.set_review(not self.review_active),
            'shuffle': lambda: self.review.shuffle() if self.review_active else None,
            'mono': lambda: self.review.toggle_mono() if self.review_active else None,
            'refresh': self.refresh, 'open': self.open_folder, 'close': self.close_project, 'preferences': self.open_preferences,
            **{f'rate{value}': (lambda stars=value: self.rate_key(stars)) for value in range(6)},
            **{f'mode_{mode}': (lambda value=mode: self.set_view(value)) for mode in ('classic', 'toile', 'swipe', 'color', 'wall', 'instagram')},
            'custom_home': self.show_custom_home}
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
        """Adapt the filter and the bars to the chosen marking system."""
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
        self.review.refresh_marker_options()

    def toast(self, text, duration=2600):
        self.toast_label.setText(text)
        self.toast_frame.adjustSize()
        self.toast_frame.move((self.width()-self.toast_frame.width())//2, 62)
        self.toast_frame.show()
        self.toast_frame.raise_()
        self.toast_timer.start(duration)

    def place_filter_panel(self):
        self.filter_scrim.setGeometry(self.rect())
        # The buckets at full size, and scrolling beyond what the window can show.
        panel_layout = self.filter_panel.layout()
        self.bucket_rule.layout().invalidate()
        self.bucket_rule.layout().activate()
        wanted = self.bucket_rule.sizeHint()
        self.bucket_area.setMinimumWidth(wanted.width()+14)
        self.bucket_area.setFixedHeight(0)
        panel_layout.invalidate()
        others = panel_layout.sizeHint().height()
        self.bucket_area.setFixedHeight(max(80, min(wanted.height()+4, self.height()-16-others)))
        panel_layout.invalidate()
        panel_layout.activate()
        self.filter_panel.resize(panel_layout.sizeHint())
        self.filter_panel.move((self.width()-self.filter_panel.width())//2, max(8, (self.height()-self.filter_panel.height())//2))
        self.filter_scrim.raise_()
        self.filter_panel.raise_()

    def show_filter_panel(self, *args):
        self.bucket_rule.rebuild()
        self.update_filter_chip()
        self.filter_scrim.show()
        self.filter_panel.show()
        self.place_filter_panel()
        # Once its new buttons have taken their style, and so their true size.
        QTimer.singleShot(0, self.place_filter_panel)

    def hide_filter_panel(self):
        self.filter_panel.hide()
        self.filter_scrim.hide()

    def clear_filters(self):
        for combo in (self.stars, self.flags):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.choices.reset()
        if self.selection:
            self.selection.set_rule(include=[], exclude=[], none=False, all=False)
            self.bucket_rule.rebuild()
        self.filters_changed()

    def update_filter_chip(self):
        parts = [combo.currentText() for combo in (self.stars, self.flags) if combo.currentIndex() > 0]+self.choices.active_labels()
        # Elided: a long filter must not push the views out of the header.
        text = self.filter_chip.fontMetrics().elidedText(' · '.join(parts), Qt.TextElideMode.ElideRight, 100)
        self.filter_chip.setText(f'Filtre : {text}  ▾' if parts else 'Filtre  ▾')
        self.filter_chip.setToolTip(' · '.join(parts) or 'Notes, marqueurs et réglages de la vue')
        self.review_tabs.sync()
        count = len(self.photos)
        self.filter_count.setText(f"{count} photo{'s' if count > 1 else ''} affichée{'s' if count > 1 else ''}")

    def fit(self):
        if self.custom_active:
            self.custom.stage.zoom_reset()
        elif self.review_active:
            self.review.zoom_reset()
        else:
            self.viewer.set_zoom(1)

    def set_view(self, key):
        if key == 'buckets':
            self.show_buckets()
        elif key == 'classic':
            self.set_review(False)
        else:
            self.open_review_mode(key)

    def sync_view_buttons(self):
        self.view_buttons['buckets' if self.custom_active else self.review.mode if self.review_active else 'classic'].setChecked(True)

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
        self.custom.background_changed()

    def cycle_background(self):
        colors = [color for _, color in BACKGROUNDS]
        current = colors.index(self.viewer.background) if self.viewer.background in colors else -1
        self.set_background(colors[(current+1) % len(colors)])

    def zoom_by(self, factor):
        if self.custom_active:
            self.custom.stage.zoom_by(factor)
        elif self.review_active:
            self.review.zoom(factor)
        else:
            self.viewer.zoom_from_center(factor)

    def open_custom(self, session=None):
        """A saved selection, opened like a project on its buckets, or the screen that creates one."""
        if session is None:
            self.set_review(False)
            self.custom_active = True
            self.update_project_state()
            self.custom.new_session()
            return
        self.set_folder(session.path.with_suffix(''), SelectionSession(session))
        if self.selection is session:
            self.custom_active = True
            self.custom.open_session(session)
            self.update_project_state()

    def leave_custom(self):
        self.custom.close_session()
        self.custom_active = False
        self.update_project_state()

    def go_home(self):
        if self.session:
            self.close_project()
        elif self.custom_active:
            self.leave_custom()

    def show_buckets(self):
        current = self.current_photo()
        self.set_review(False)
        self.custom_active = True
        self.update_project_state()
        if current:
            self.custom.show_path(current.relative_path)

    def selection_changed(self):
        """Other photos or other buckets: what shows them follows."""
        self.session.refresh()
        self.choices.set_buckets()
        self.review_tabs.rebuild()
        self.bucket_strip.update()
        self.update_project_state()
        self.filters_changed()

    def photo_buckets(self, photo):
        """The buckets of a photo as (title, color); nothing outside a selection."""
        if not self.selection:
            return []
        buckets = [self.selection.bucket(key) for key in self.selection.buckets_of(photo.relative_path)]
        return [(bucket['title'], bucket['color']) for bucket in buckets if bucket]

    def bucket_drag(self, point, photo):
        if self.bucket_ghost.isHidden():
            image = self.best_image(photo)
            if image is not None:
                self.bucket_ghost.setPixmap(QPixmap.fromImage(image).scaled(QSize(150, 150), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.bucket_ghost.adjustSize()
                self.bucket_ghost.show()
                self.bucket_ghost.raise_()
        local = self.mapFromGlobal(point)
        # Above the hand, so as to leave the zone under it readable.
        self.bucket_ghost.move(local.x()-self.bucket_ghost.width()//2, local.y()-self.bucket_ghost.height()-12)
        self.bucket_strip.drag_to(point)

    def bucket_drop(self, point, photo):
        self.bucket_ghost.hide()
        hit = self.bucket_strip.finish_drag(point)
        if hit:
            self.move_to_bucket(photo, *hit)

    def move_to_bucket(self, photo, key, duplicate=False):
        """The photo, and the others picked with it. Seen through a bucket, it leaves that one and keeps its others."""
        rule = self.selection.rule()
        source = rule['include'][0] if len(rule['include']) == 1 else None
        before = len(self.selection.undo_stack)
        for target in self.with_selection(photo):
            self.selection.move(target.relative_path, key, source, duplicate)
        self.buckets_done(before)

    def rate_key(self, value):
        """In a selection, the keys of the notes are those of its buckets: 1 for the first one, the zero star for none."""
        if self.custom_active:
            self.custom.key_bucket(value)
        elif not self.selection:
            self.rate(value)
        elif not value:
            before = len(self.selection.undo_stack)
            for photo in self.target_photos():
                self.selection.clear(photo.relative_path)
            self.buckets_done(before)
        elif value <= len(self.selection.buckets):
            self.toggle_bucket(self.selection.buckets[value-1]['id'])

    def toggle_bucket(self, key):
        """In the bucket or out of it, on the photo shown and those picked with it."""
        photos = self.target_photos()
        if not photos:
            self.toast('Choisissez d’abord une photo.', 1600)
            return
        before = len(self.selection.undo_stack)
        adding = key not in self.selection.buckets_of(photos[0].relative_path)
        count = f'{len(photos)} photos ' if len(photos) > 1 else ''
        self.toast(f"{count}{'Ajoutée' if adding and not count else 'ajoutées' if adding else 'Retirée' if not count else 'retirées'} {'à' if adding else 'de'} {self.selection.bucket(key)['title']}", 1000)
        for photo in photos:
            if adding:
                self.selection.add(photo.relative_path, key)
            elif key in self.selection.buckets_of(photo.relative_path):
                self.selection.toggle(photo.relative_path, key)
        self.buckets_done(before)

    def buckets_done(self, before):
        changed = len(self.selection.undo_stack)-before
        if changed:
            self.undo_stack.append(('buckets', changed))
            del self.undo_stack[:-300]
            self.custom.changed()

    def show_custom_home(self):
        """The selections are on the welcome screen, which shows once no project is open."""
        if self.custom_active:
            self.leave_custom()
        if self.session:
            self.close_project()

    def custom_loader(self, path, side):
        return Loader(path, side, None, self.display_profile, self.color_generation)

    def matches_filters(self, photo, stars, marker, publish):
        """The rule of the export: a rejected photo only leaves when asked for."""
        return (stars_match(photo, stars) and marker_matches(photo, marker) and (photo.pick != PICK_REJECT or marker == 'reject')
                and (not publish or photo.publish))

    def start_custom_export(self, job, count):
        def finished(folder, error):
            self.export_done(job, folder, error, count-len(job.skipped))
            self.custom.after_export(job)
        job.signals.progress.connect(self.export_progress, Qt.ConnectionType.QueuedConnection)
        job.signals.finished.connect(finished, Qt.ConnectionType.QueuedConnection)
        self.export_job = job
        self.export_checking()
        self.export_label.setText('Préparation de l’export…')
        self.export_pool.start(job)

    def set_review(self, active):
        self.custom_active = False
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
        if self.filter_panel.isVisible():
            self.hide_filter_panel()
        elif self.custom_active:
            self.custom.back()
        elif self.review_active:
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
        """The photo on display comes first: the bars show its note and a toggle starts from it."""
        current = self.current_photo()
        return sorted((item.data(Qt.ItemDataRole.UserRole) for item in self.list.selectedItems()), key=lambda photo: photo is not current)

    def target_photos(self):
        """Photos that the keys act on. Swipe and Duel show cards of their own, and Custom leaves the open project alone."""
        if self.custom_active:
            return []
        if self.review_active:
            return self.review.targets()
        return self.selected_photos()

    def with_selection(self, photo):
        """What a click on one photo acts on: that photo first, then the selection it belongs to."""
        targets = self.target_photos()
        others = [candidate for candidate in targets if candidate is not photo]
        return [photo]+others if len(others) < len(targets) else [photo]

    def select_all(self):
        if self.custom_active:
            return
        if self.review_active:
            self.review.view().select_all()
        else:
            self.select_photos(self.photos)

    def deselect(self):
        if self.review_active:
            self.review.view().unpick()
        else:
            self.select_only_current()

    def select_photos(self, photos):
        """Select these photos at once. The one on display stays so if it is among them."""
        chosen = {id(photo) for photo in photos}
        rows = [row for row, photo in enumerate(self.photos) if id(photo) in chosen]
        if not rows:
            return
        if self.list.currentRow() not in rows:
            self.list.setCurrentRow(rows[0], QItemSelectionModel.SelectionFlag.NoUpdate)
        self.select_rows(rows)
        self.toast(f"{len(rows)} photo{'s' if len(rows) > 1 else ''} sélectionnée{'s' if len(rows) > 1 else ''}", 1400)

    def select_rows(self, rows):
        """In one go, by runs of rows: one at a time takes seconds on a large gallery."""
        selection = QItemSelection()
        model = self.list.model()
        first = previous = None
        for row in rows+[None]:
            if previous is None or row != previous+1:
                if previous is not None:
                    selection.select(model.index(first), model.index(previous))
                first = row
            previous = row
        self.list.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def select_only_current(self):
        """Classique always acts on a photo: the one on display stays selected."""
        row = self.list.currentRow()
        if row >= 0:
            self.list.setCurrentRow(row, QItemSelectionModel.SelectionFlag.ClearAndSelect)

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
        absent = len(getattr(self.session, 'absent', ()))
        menu.addAction(f'Retrouver les photos manquantes ({absent})…' if absent else 'Retrouver les photos manquantes…', self.repair_photos).setEnabled(bool(absent))
        if self.selection:
            menu.addAction('Vérifier les photos de la sélection…', self.custom.check_photos)
        menu.addSeparator()
        menu.addAction('Sélections custom', self.show_custom_home)
        menu.addAction('Portfolio', self.open_portfolio)
        menu.addAction('Ouvrir le dossier du portfolio', self.reveal_portfolio).setEnabled(self.portfolio.available())
        menu.addAction("Emplacement du portfolio…", self.choose_portfolio_folder)
        menu.addSeparator()
        menu.addAction('Fermer le projet', self.close_project).setEnabled(self.session is not None)

    def open_recent(self, path):
        if not Path(path).is_dir():
            if ask(self, 'Projet introuvable', f'Ce dossier est introuvable :\n{path}\n\nSi le disque est débranché, rebranchez-le puis réessayez. S\'il a été déplacé ou renommé, indiquez où il se trouve maintenant.',
                   [('close', 'Fermer', False), ('find', 'Retrouver le dossier…', True)]) == 'find':
                self.repair_recent(path)
            return
        self.set_folder(Path(path))

    def ask_folder(self, title):
        return QFileDialog.getExistingDirectory(self, title)

    def repair_recent(self, path, opening=True):
        """Ask where a lost project is now, look there, and bring along everything that refers to it."""
        chosen = self.ask_folder(f'Où se trouve maintenant « {Path(path).name} » ?')
        if not chosen:
            return False
        self.activity.announce('Recherche du projet…')
        found, how = locate_project(path, chosen)
        if how == 'several':
            inform(self, 'Plusieurs projets', 'Ce dossier contient plusieurs projets. Choisissez directement le dossier du bon projet.')
            return False
        if how == 'empty':
            inform(self, 'Aucune photo ici', f'Ce dossier ne contient ni projet ni photo :\n{chosen}')
            return False
        if how == 'photos' and ask(self, 'Pas de notes ici', f'Ce dossier contient des photos, mais pas le dossier {SESSION_DIR_NAME} qui garde les notes du projet :\n{found}\n\nLes notes de ce projet restent introuvables, mais le portfolio et les sélections pointeront vers ces photos.',
                                   [('cancel', 'Annuler', False), ('use', 'Utiliser ce dossier', True)]) != 'use':
            return False
        moved = self.follow_move(path, found)
        self.recents.relocate(path, found)
        self.toast(f"Projet retrouvé : {found}"+(f" · {moved} lien{'s' if moved > 1 else ''} réparé{'s' if moved > 1 else ''}" if moved else ''), 4000)
        if opening:
            self.set_folder(Path(found))
        else:
            self.home.refresh()
        return True

    def follow_move(self, old, new):
        """What the portfolio and the selections know of a folder that moved."""
        return self.repoint(lambda path: moved_path(path, old, new))

    def repoint(self, moves):
        changed = self.portfolio.repoint(moves)
        for session in self.custom.store.sessions():
            changed += session.repoint(moves)
        return changed

    def repair_photos(self):
        """Photos of the portfolio or of a selection whose file is lost: looked for by name in a folder chosen by hand."""
        absent = getattr(self.session, 'absent', set())
        if not absent:
            return
        items = self.portfolio.items
        missing = [items[key]['source'] for key in absent if key in items] if self.viewing_portfolio() else list(absent)
        chosen = self.ask_folder('Dossier où chercher les photos manquantes')
        if not chosen:
            return
        self.activity.announce('Recherche des photos…')
        found = locate_photos(missing, chosen)
        selection = self.selection
        if found:
            self.custom.save()
            self.portfolio.repoint(found.get)
            if selection:
                selection.repoint(found.get)
            for session in self.custom.store.sessions():
                if not selection or session.path != selection.path:
                    session.repoint(found.get)
            if selection:
                self.selection_changed()
            else:
                self.refresh()
        self.toast(f'{len(found)} photo{"s" if len(found) > 1 else ""} retrouvée{"s" if len(found) > 1 else ""} sur {len(missing)}.', 4000)

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
        found = [entry for entry in found if not self.is_portfolio_path(entry['path'])]
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
        page = 3 if self.custom_active else (2 if self.review_active else 1) if has_photos else 0
        self.home_button.setVisible(bool(self.session) or self.custom_active)
        self.view_buttons['buckets'].setVisible(bool(self.selection))
        self.bucket_strip.setVisible(bool(self.selection and self.selection.buckets) and has_photos and not self.custom_active)
        self.review_tabs.setVisible(bool(self.selection and self.selection.buckets) and bool(self.session))
        if page == 0:
            self.home.refresh()
        self.pages.setCurrentIndex(page)
        self.sync_view_buttons()
        in_project = has_photos and not self.custom_active
        self.export_button.setVisible(in_project)
        # Nothing to filter or switch on the welcome screen. Custom has its own controls, and its own export.
        for control in (self.sort, self.counter):
            control.setVisible(in_project)
        self.segments.setVisible(has_photos)
        # In a selection, the buckets filter too.
        with_filter = in_project or bool(self.selection and has_photos)
        self.filter_chip.setVisible(with_filter)
        if not with_filter:
            self.hide_filter_panel()
        # Black and white, background and zoom of the panel are those of the other views.
        self.tools_section.setVisible(self.review_active)
        self.bucket_section.setVisible(bool(self.selection))
        if self.filter_panel.isVisible():
            self.place_filter_panel()
        name = self.selection.name if self.selection else project_label(self.project_path) if self.project_path else ''
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
        self.custom_active = False
        self.stop_export()
        if self.session:
            self.save_session()
            self.remember_project()
        if self.selection:
            self.custom.close_session()
            self.selection = None
            self.bucket_rule.hide()
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
        self.choices.reset()
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
        combo.setFixedWidth({'Tri': 132, 'Notes': 136, 'Marqueurs': 146}[label])
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

    def background_tasks(self):
        """Long work under way, as (label, how many left)."""
        tasks = [('Vérification des photos', self.custom.check_left), ('Lecture des galeries', self.custom.setup.scanning()), ('Corbeille du portfolio', len(self.portfolio.removed)),
                 ('Chargement des aperçus', len(self.pending)+len(self.custom.pending)),
                 ('Analyse des couleurs', self.review.analysis_left)]
        return [task for task in tasks if task[1]]

    def set_folder(self, path, session=None):
        self.activity.announce('Ouverture du projet…')
        try:
            session = session or (PortfolioSession(path, self.portfolio) if self.is_portfolio_path(path) else PhotoSession(path))
        except Exception as error:
            inform(self, 'Ouverture impossible', str(error))
            return
        self.close_project()
        self.session = session
        self.selection = getattr(session, 'sorting', None)
        self.project_path = Path(path)
        self.baseline = original_inventory(path)
        self.portfolio.reconcile()
        if self.viewing_portfolio():
            self.portfolio.apply_seeds(session.photos)
        if self.selection:
            self.choices.set_buckets()
            self.review_tabs.rebuild()
        else:
            self.choices.set_projects(sorted({self.portfolio.project_of(p.relative_path) for p in session.photos}) if self.viewing_portfolio() else [])
        if session.photos:
            self.remember_project(time.time())
        absent = len(getattr(session, 'absent', ()))
        if absent:
            place = 'la sélection' if self.selection else 'le portfolio'
            self.toast(f"{absent} photo{'s' if absent > 1 else ''} introuvable{'s' if absent > 1 else ''} : disque débranché ou fichier déplacé. {'Elles restent' if absent > 1 else 'Elle reste'} dans {place}.", 5000)
        self.review.start_analysis()
        self.cache_dir = getattr(session, 'preview_dir', session.session_dir/'Previews')
        self.cache_dir.mkdir(exist_ok=True)
        self.cache.clear()
        self.prepared.clear()
        self.folder_label.setText(self.selection.name if self.selection else path.name)
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
            self.choices.reset()
            if self.selection:
                self.selection.set_rule(include=[], exclude=[], none=False)
                self.bucket_rule.rebuild()
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
            if marker_matches(photo, flag) and self.choices_match(photo):
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
        filtered = [p for p in self.session.photos if stars_match(p, stars) and marker_matches(p, flag) and self.choices_match(p)]
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
        surviving = []
        for i, photo in enumerate(self.photos):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, photo)
            item.setToolTip(photo.relative_path)
            self.list.addItem(item)
            if photo.relative_path in selected:
                surviving.append(i)
            if photo.relative_path == current:
                row = i
        if self.photos:
            self.select_rows(surviving)
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
        self.custom.filters_changed()
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
        if self.custom_active:
            self.custom.navigate(delta)
        elif self.review_active:
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
            self.bucket_strip.update()
            if self.review_active and not self.review.targets():
                # Nothing picked on the page yet: name no photo.
                self.counter.setText(f'{len(self.photos)} photos')
                current = None
            else:
                self.counter.setText(f'{self.list.currentRow()+1} / {len(self.photos)}')
            metrics = self.name_label.fontMetrics()
            # A selection has more views in the header: its ribbon and its bars name the photo.
            self.name_label.setText(metrics.elidedText(current.path.name, Qt.TextElideMode.ElideMiddle, 150) if current and not self.selection else '')
            self.counter.setToolTip(f'{len(self.list.selectedItems())} sélectionnée(s) · {five} à 5 étoiles')
        else:
            self.counter.setText('Aucune photo')
            self.name_label.clear()

    def change(self, photos, stars=None, pick=None, liked=None, publish=None):
        """Single entry point for notes and markers: it is what makes them undoable."""
        if not self.session or not photos:
            return
        self.undo_stack.append([(photo, photo.stars, photo.pick, photo.liked, photo.publish) for photo in photos])
        del self.undo_stack[:-300]
        for photo in photos:
            if stars is not None:
                photo.stars = stars
            if pick is not None:
                photo.pick = pick
            if liked is not None:
                photo.liked = liked
            if publish is not None:
                photo.publish = publish
        self.apply_filters()

    def undo(self):
        if self.custom_active:
            self.custom.undo()
            return
        if not self.undo_stack:
            self.toast('Rien à annuler')
            return
        states = self.undo_stack.pop()
        if states[0] == 'buckets':
            for _ in range(states[1]):
                self.selection.undo()
            self.custom.changed()
            self.toast('Rangement annulé', 1200)
            return
        if states[0] == 'portfolio':
            _, photos, added = states
            self.send_to_portfolio(photos, not added)
            self.toast('Portfolio : retour en arrière', 1200)
            return
        for photo, stars, pick, liked, publish in states:
            photo.stars, photo.pick, photo.liked, photo.publish = stars, pick, liked, publish
        self.apply_filters()
        if self.review_active:
            self.review.after_undo([state[0] for state in states])
        self.toast('Note annulée', 1200)

    def rate(self, stars):
        if self.custom_active:
            self.custom.key_bucket(stars)
            return
        self.change(self.target_photos(), stars=stars)

    def toggle_like(self):
        photos = self.target_photos()
        if photos:
            self.change(photos, liked=not photos[0].liked)

    def toggle_publish(self):
        photos = self.target_photos()
        if photos:
            self.change(photos, publish=not photos[0].publish)

    # Portfolio. Inside it, every photo belongs to it and taking one out sends the file to the bin.
    def is_portfolio_path(self, path):
        return self.portfolio.folder is not None and path_key(path) == path_key(self.portfolio.folder)

    def viewing_portfolio(self):
        return self.project_path is not None and self.is_portfolio_path(self.project_path)

    def in_portfolio(self, photo):
        return self.viewing_portfolio() or self.portfolio.contains(photo.path)

    def photo_project(self, photo):
        """The project a photo of the portfolio comes from; nothing to say elsewhere."""
        if not self.viewing_portfolio():
            return ''
        project = self.portfolio.project_of(photo.relative_path)
        return '' if project == UNSORTED else project

    def photo_label(self, photo):
        project = self.photo_project(photo)
        return f'{project} · {photo.path.name}' if project else photo.path.name

    def choices_match(self, photo):
        choices = self.choices
        if choices.portfolio.isChecked() and not self.in_portfolio(photo):
            return False
        if choices.publish.isChecked() and not photo.publish:
            return False
        if self.selection:
            return self.selection.matches(photo.relative_path)
        if choices.project.currentIndex() <= 0:
            return True
        return self.portfolio.project_of(photo.relative_path) == choices.project.currentData()

    def remember_project(self, opened=None):
        if not self.viewing_portfolio() and not self.selection:
            self.recents.add(self.project_path, self.session.photos, opened)

    def toggle_portfolio(self, photo=None):
        photos = self.with_selection(photo) if photo else self.target_photos()
        if not photos:
            return
        if self.viewing_portfolio():
            if len(photos) == 1 or ask(self, 'Retirer du portfolio', f"{len(photos)} photos quitteront le portfolio. Les fichiers d'origine ne bougent pas.",
                                       [('cancel', 'Annuler', False), ('remove', 'Retirer', True)]) == 'remove':
                self.take_out_of_portfolio(photos)
            return
        adding = not self.portfolio.contains(photos[0].path)
        if adding and not self.ready_for_portfolio():
            return
        # Only those that change, so that Ctrl+Z leaves the others where they were.
        photos = [photo for photo in photos if self.portfolio.contains(photo.path) != adding]
        self.send_to_portfolio(photos, adding)
        self.undo_stack.append(('portfolio', photos, adding))
        del self.undo_stack[:-300]
        count = len(photos)
        self.toast(f"{count} photo{'s' if count > 1 else ''} {'ajoutée' if adding else 'retirée'}{'s' if count > 1 else ''} {'au' if adding else 'du'} portfolio", 1400)

    def send_to_portfolio(self, photos, adding):
        for photo in photos:
            if adding:
                self.portfolio.add(photo, Path(self.selection.name) if self.selection else self.project_path)
            else:
                self.portfolio.remove_source(photo.path)
        self.portfolio.save()
        self.apply_filters()

    def take_out_of_portfolio(self, photos):
        leaving = {id(photo) for photo in photos}
        for photo in photos:
            self.portfolio.remove_relative(photo.relative_path)
        self.portfolio.save()
        self.session.photos = [photo for photo in self.session.photos if id(photo) not in leaving]
        self.toast('Retirée du portfolio. Le fichier d\'origine n\'a pas bougé.', 2600)
        if self.session.photos:
            self.choices.set_projects(sorted({self.portfolio.project_of(p.relative_path) for p in self.session.photos}))
            self.apply_filters()
        else:
            self.close_project()

    def ready_for_portfolio(self):
        if self.relocating:
            self.toast('Le portfolio est en cours de copie vers son nouvel emplacement.')
            return False
        if self.portfolio.folder is None and not self.choose_portfolio_folder():
            return False
        if not self.portfolio.available():
            inform(self, 'Portfolio introuvable', f'Ce dossier est introuvable pour le moment :\n{self.portfolio.folder}\n\nRebranchez le disque ou choisissez un autre emplacement.')
            return False
        return True

    def choose_portfolio_folder(self):
        """Pick where the portfolio lives. Copies follow to an empty place; the former folder stays as it is."""
        if self.relocating:
            return False
        start = str(self.portfolio.folder or QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation))
        chosen = QFileDialog.getExistingDirectory(self, 'Choisir le dossier du portfolio', start)
        if not chosen:
            return False
        new = Path(chosen)
        old = self.portfolio.folder
        if old is not None and path_key(old) == path_key(new):
            return True
        if self.session and not self.viewing_portfolio() and path_key(new).startswith(path_key(self.session.root)+os.sep):
            inform(self, 'Autre dossier nécessaire', 'Le portfolio ne peut pas se trouver dans le dossier du projet ouvert : ses copies y seraient comptées comme des photos du projet.')
            return False
        if path_key(new) == path_key(self.session.root if self.session else ''):
            inform(self, 'Autre dossier nécessaire', "Ce dossier est celui du projet ouvert.")
            return False
        if self.viewing_portfolio():
            self.close_project()
        self.portfolio.wait()
        adopted = (new/SESSION_DIR_NAME/MANIFEST_FILE).exists()
        if old is not None and self.portfolio.items and old.is_dir() and not adopted:
            choice = ask(self, 'Déplacer le portfolio', f'Reporter ses {len(self.portfolio.items)} photos dans ce nouveau dossier ?\n{new}\n\nL\'ancien dossier reste tel quel, à vous de le supprimer si vous le souhaitez.',
                         [('cancel', 'Annuler', False), ('empty', 'Repartir de zéro', False), ('copy', 'Reporter', True)])
            if choice not in ('copy', 'empty'):
                return False
            if choice == 'copy':
                self.relocate_portfolio(old, new)
                return False
        self.portfolio.set_folder(new)
        return True

    def relocate_portfolio(self, old, new):
        job = RelocateJob(old, new)
        job.signals.progress.connect(self.relocate_progress, Qt.ConnectionType.QueuedConnection)
        job.signals.finished.connect(self.relocate_done, Qt.ConnectionType.QueuedConnection)
        self.relocating = job
        self.export_cancel.hide()
        self.export_checking()
        self.export_label.setText('Copie du portfolio…')
        self.export_pool.start(job)

    def relocate_progress(self, done, total):
        self.export_label.setText(f'Portfolio : {done} sur {total} fichiers')
        self.export_bar.setRange(0, total)
        self.export_bar.setValue(done)

    def relocate_done(self, folder, error):
        self.relocating = None
        self.export_panel.hide()
        self.export_cancel.show()
        if error:
            inform(self, 'Copie du portfolio incomplète', f'{error}\n\nLe portfolio reste à son ancien emplacement.')
        else:
            self.portfolio.set_folder(Path(folder))
            self.toast('Portfolio copié vers son nouvel emplacement.')

    def open_portfolio(self):
        if self.relocating:
            return
        if self.portfolio.folder is None and not self.choose_portfolio_folder():
            return
        if not self.portfolio.available():
            inform(self, 'Portfolio introuvable', f'Ce dossier est introuvable pour le moment :\n{self.portfolio.folder}\n\nRebranchez le disque ou choisissez un autre emplacement.')
            return
        self.portfolio.wait()
        self.portfolio.reconcile()
        self.set_folder(self.portfolio.folder)
        if not self.session.photos:
            self.close_project()
            self.toast('Le portfolio est vide : ajoutez-y des photos avec la touche K ou le signet.', 3500)

    def reveal_portfolio(self):
        if self.portfolio.available():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.portfolio.folder)))

    def portfolio_failed(self, text):
        self.toast(text, 6000)
        # A photo the bin refused is still in the folder: it comes back on the page.
        if self.viewing_portfolio():
            self.refresh()

    def portfolio_changed(self):
        if self.pages.currentIndex() == 0:
            self.home.refresh()
        self.list.viewport().update()
        if self.review_active:
            self.review.update_view()
            self.review.bar.update()
        self.viewer_bar.update()

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
            photos = self.with_selection(self.photos[row])
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

    def export(self):
        if self.selection:
            self.custom.export()
            return
        if not self.session or self.export_job or self.relocating:
            return
        dialog = ExportDialog(self)
        if dialog.exec():
            self.run_export(dialog.photos(), dialog.folder_name())

    def export_selection(self, stars, marker, portfolio, publish, project):
        """Among the whole gallery, whatever is on screen. A rejected photo only leaves when asked for."""
        return [p for p in self.session.photos if stars_match(p, stars) and marker_matches(p, marker)
                and (p.pick != PICK_REJECT or marker == 'reject') and (not portfolio or self.in_portfolio(p))
                and (not publish or p.publish) and (project == 'all' or self.portfolio.project_of(p.relative_path) == project)]

    def run_export(self, photos, name):
        if self.viewing_portfolio():
            # This folder changes as photos come and go: only a change during the export matters.
            self.baseline = original_inventory(self.session.root)
        destination = self.session.session_dir/EXPORT_ROOT_NAME/name
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
        self.export_panel.move((self.width()-self.export_panel.width())//2, self.height()-self.export_panel.height()-(168 if self.custom_active else 84))
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
        self.custom.shutdown()
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
            self.remember_project()
            self.review.save_analysis()
        self.pool.clear()
        self.pool.waitForDone()
        # Copies under way finish: they were asked for, and a partial file is never visible.
        self.portfolio.wait()
        self.export_pool.waitForDone()
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
