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
import random
import zlib
from io import BytesIO
from pathlib import Path
from collections import OrderedDict

from PySide6.QtCore import Qt, QPoint, QSize, QRectF, QPointF, Signal, Slot, QObject, QRunnable, QThreadPool, QTimer, QItemSelection, QItemSelectionModel, QSettings, QStandardPaths, QUrl
from PySide6.QtGui import QAction, QPixmap, QCursor, QDesktopServices, QIcon, QImage, QPainter, QPainterPath, QColor, QFont, QShortcut, QKeySequence, QColorSpace, QPen
from PySide6.QtWidgets import (QToolTip, QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QSplitter, QListWidget, QListWidgetItem, QFrame, QScrollArea, QButtonGroup,
    QStackedWidget, QAbstractItemView, QDialog, QFileDialog, QLineEdit, QProgressBar, QStyledItemDelegate, QStyle, QMenu, QColorDialog)
from PIL import Image, ImageCms, ImageWin
from triphoto import (export_folder_name, shooting_info, APP_NAME, APP_VERSION, PhotoSession, sort_photos, SORT_OPTIONS, load_screen_preview, original_inventory,
    export_original_copy, EXPORT_DIR_NAME, PICK_NEUTRAL, PICK_REJECT, PICK_SELECT, SESSION_DIR_NAME)
from triphoto_custom import ProjectBuckets, BucketFilter, BucketStrip, CustomPage, PopupMenu, SelectionSession, Store
from triphoto_cache import DAY, SHARED_LIMIT, clean_caches
from triphoto_portfolio import MANIFEST_FILE, Portfolio, PortfolioSession, RelocateJob, UNSORTED, move_to_trash
from triphoto_prefs import (ALIASES, LinkSearchJob, MARK_TIPS, PreferencesDialog, Recents, age_label, ask, inform, known_links, locate_photos, locate_project, moved_path, path_key, shortcut_map)
from triphoto_review import COLOR_ORDERS, MODES, color_key, swatch_icon, ChoiceFilter, MarkerFilter, NoteFilter, RatingBar, ReviewPage
from triphoto_blip import BLIP_STORES, blip_executable, send_with_blip
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
                        # Seen in the ribbon is in use: the upkeep of the caches keeps it.
                        os.utime(cache_path, None)
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


class CacheJob(QRunnable):
    """The upkeep of the preview caches, in the background: see triphoto_cache."""

    def __init__(self, roots, store):
        super().__init__()
        self.roots, self.store = roots, store

    def run(self):
        try:
            clean_caches(self.roots, self.store/'Previews')
            # What a deleted selection left behind: its backup copy goes to the recycle bin as well.
            for backup in self.store.glob('*.json.bak'):
                if not backup.with_suffix('').exists() and time.time()-backup.stat().st_mtime > DAY:
                    move_to_trash(backup)
        except OSError:
            pass


class HoverButton(QPushButton):
    entered = Signal()

    def enterEvent(self, event):
        self.entered.emit()
        super().enterEvent(event)


VIEW_WIDTH = 230
FILTER_WIDTH = 318
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
        # Black and white, shared with the other views.
        self.transform = lambda image: image
        self.liked = lambda: None
        # Shooting settings under the photo, empty when the file says nothing.
        self.caption = lambda: ''
        self.add_export_action = lambda menu: None
        self.zoom = 1.0
        self.pan = QPointF()
        self.drag = None
        self.message = 'Ajoutez des photos pour commencer'
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
        painter.drawImage(self.image_rect(), self.transform(self.image))
        liked = self.liked()
        if liked is not None:
            draw_heart(painter, self.heart_rect(), liked)
        caption = self.caption()
        if caption:
            shown = self.image_rect().intersected(QRectF(self.rect()))
            top = min(shown.bottom()+4, self.height()-16)
            painter.setFont(QFont(self.font().family(), 7))
            painter.setPen(QColor('#000000' if QColor(self.background).lightnessF() > .35 else contrast_on(self.background)))
            painter.drawText(QRectF(shown.left(), top, max(shown.width(), 240), 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, caption)

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
        if self.drag is not None and not event.buttons() & Qt.MouseButton.LeftButton:
            self.drag = None
            self.unsetCursor()
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
            action = menu.addAction(swatch_icon(color), label)
            action.setCheckable(True)
            action.setChecked(self.background == color)
            action.triggered.connect(lambda checked=False, value=color: self.set_background(value))
        custom = menu.addAction('Autre couleur…', self.pick_background)
        if self.background not in [color for _, color in BACKGROUNDS]:
            custom.setIcon(swatch_icon(self.background))
        return menu

    def pick_background(self):
        color = QColorDialog.getColor(QColor(self.background), self, 'Couleur du fond')
        if color.isValid():
            self.set_background(color.name())

    def contextMenuEvent(self, event):
        menu = self.background_menu(self)
        menu.addSeparator()
        menu.addAction('Ajuster la photo', lambda: self.set_zoom(1))
        self.add_export_action(menu)
        menu.exec(event.globalPos())


class PhotoDelegate(QStyledItemDelegate):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def helpEvent(self, event, view, option, index):
        """The icons of a thumbnail say what they are, in the same words as everywhere else."""
        rect = self.card(option)
        thumb = QRectF(rect.x(), rect.y(), rect.width(), rect.height()-28)
        keep = self.bookmark(thumb)
        point = QPointF(event.pos())
        tip = MARK_TIPS['portfolio'] if keep.adjusted(-3, -3, 3, 3).contains(point) else MARK_TIPS['like'] if self.heart(thumb).adjusted(-3, -3, 3, 3).contains(point) else None
        if tip is None and index.data(Qt.ItemDataRole.UserRole).publish and QRectF(keep.left()+16, thumb.top()+6, 13, 13).contains(point):
            tip = MARK_TIPS['publish']
        if tip:
            QToolTip.showText(event.globalPos(), tip, view)
            return True
        return super().helpEvent(event, view, option, index)

    def sizeHint(self, option, index):
        return QSize(250, self.window.list.row_height())

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
    known_height = ROW_HEIGHT

    def row_height(self):
        """The thumbnails follow the width of the ribbon; the line of the note under them stays the same."""
        return max(ROW_HEIGHT, (self.viewport().width()-14)//2+34)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.row_height()
        if height != self.known_height:
            # The photo at the top stays at the top.
            position = self.verticalScrollBar().value()/self.known_height
            self.known_height = height
            self.doItemsLayout()
            self.verticalScrollBar().setValue(round(position*height))

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

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if item:
            menu = QMenu(self)
            self.owner.add_export_action(menu, self.owner.clicked_photos(item.data(Qt.ItemDataRole.UserRole)))
            menu.exec(event.globalPos())


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
        self.expanded = False
        self.title = QLabel('Ajoutez des photos pour commencer', objectName='title')
        self.column.addWidget(self.title)
        self.title_gap = QWidget()
        self.title_gap.setFixedHeight(16)
        self.column.addWidget(self.title_gap)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.section = QLabel('PROJETS', objectName='section')
        self.actions.addWidget(self.section)
        self.add_button = QPushButton('Ajouter des photos', objectName='primary')
        self.add_button.clicked.connect(lambda checked=False: window.open_folder())
        self.find_button = QPushButton('Rechercher sur un disque', objectName='ghost')
        self.find_button.clicked.connect(lambda checked=False: window.search_links())
        self.lead = QWidget()
        self.trail = QWidget()
        self.actions.addWidget(self.lead, 1)
        self.actions.addWidget(self.find_button)
        self.actions.addWidget(self.add_button)
        self.actions.addWidget(self.trail, 1)
        self.column.addLayout(self.actions)
        self.project_hint = QLabel('Un dossier de photos forme un projet : le premier tri s’y fait, avec les notes et les filtres.', objectName='muted')
        self.column.addWidget(self.project_hint)
        self.column.addSpacing(12)
        self.grid_holder = QWidget(objectName='home')
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(14)
        self.column.addWidget(self.grid_holder)
        self.more_button = QPushButton(objectName='ghost')
        self.more_button.clicked.connect(lambda checked=False: self.show_all_projects())
        self.column.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.column.addSpacing(30)
        self.portfolio_card = PortfolioCard(self)
        self.column.addWidget(self.portfolio_card)
        self.column.addSpacing(30)
        custom_top = QHBoxLayout()
        custom_top.addWidget(QLabel('SÉLECTIONS', objectName='section'))
        custom_top.addStretch(1)
        self.custom_button = QPushButton('Nouvelle sélection', objectName='ghost')
        self.custom_button.clicked.connect(lambda checked=False: window.open_custom())
        custom_top.addWidget(self.custom_button)
        self.column.addLayout(custom_top)
        self.column.addWidget(QLabel('Des photos de plusieurs projets, rangées dans des buckets : un concours, les réseaux sociaux, une commande.', objectName='muted'))
        self.column.addSpacing(12)
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
        self.project_hint.setVisible(has_recents)
        self.grid_holder.setVisible(has_recents)
        # Centered when there is nothing else to show, left-aligned above the cards.
        self.column.setStretch(0, 0 if has_recents else 1)
        self.title.setVisible(not has_recents)
        self.title_gap.setVisible(not has_recents)
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.trail.setVisible(not has_recents)
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
        # Two rows are enough to pick up where one was: the portfolio and the selections stay in sight.
        shown = len(self.cards) if self.expanded else columns*2
        for index, card in enumerate(self.cards.values()):
            self.grid.addWidget(card, index//columns, index % columns)
            card.setVisible(index < shown)
        self.more_button.setText(f'Voir les {len(self.cards)} projets')
        self.more_button.setVisible(len(self.cards) > shown)
        for index, card in enumerate(self.custom_cards.values()):
            self.custom_grid.addWidget(card, index//columns, index % columns)
        for column in range(10):
            self.grid.setColumnStretch(column, 1 if column < columns else 0)
            self.custom_grid.setColumnStretch(column, 1 if column < columns else 0)

    def show_all_projects(self):
        self.expanded = True
        self.columns = 0
        self.arrange()

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
        grid.addWidget(QLabel('Note', objectName='muted'), 0, 0)
        grid.addWidget(self.stars, 0, 1)
        grid.addWidget(QLabel(window.marker_section.title.text(), objectName='muted'), 1, 0)
        grid.addWidget(self.marker, 1, 1)
        choices = window.choices
        # The same capsule as in the filter panel.
        capsule = QFrame(objectName='segments')
        toggles = QHBoxLayout(capsule)
        toggles.setContentsMargins(3, 3, 3, 3)
        toggles.setSpacing(2)
        self.portfolio = self.toggle(toggles, 'Portfolio', choices.portfolio.isChecked())
        self.portfolio.setVisible(not window.viewing_portfolio())
        self.publish = self.toggle(toggles, 'À publier', choices.publish.isChecked())
        self.liked = self.toggle(toggles, "J'aime", choices.liked.isChecked())
        grid.addWidget(QLabel('Seulement', objectName='muted'), 2, 0)
        grid.addWidget(capsule, 2, 1)
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
                                           self.publish.isChecked(), self.project.currentData() if self.project else 'all', self.liked.isChecked())

    def suggested_name(self):
        """Named after its settings: "5 étoiles", "4 étoiles et plus, À publier"."""
        if self.owner.review_active and self.owner.review.mode == 'instagram':
            return 'Instagram'
        parts = []
        if self.stars.currentIndex() > 0:
            parts.append(self.stars.currentText())
        if self.marker.currentIndex() > 0:
            parts.append(self.marker.currentText().replace(' uniquement', ''))
        parts += [button.text() for button in (self.portfolio, self.publish, self.liked) if button.isChecked()]
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


class AddToDialog(QDialog):
    """Where photos picked by a right click go: the portfolio, a selection, or a new one named here."""

    def __init__(self, window, photos):
        super().__init__(window)
        self.owner, self.photos = window, photos
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(objectName='dialog')
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(8)
        count = len(photos)
        layout.addWidget(QLabel('Ajouter cette photo à' if count == 1 else f'Ajouter les {count} photos à', objectName='dialogTitle'))
        layout.addSpacing(4)
        self.targets = []
        if not window.viewing_portfolio():
            self.target(layout, 'Portfolio', 'portfolio')
        current = window.selection.path if window.selection else None
        sessions = [session for session in window.custom.store.sessions() if session.path != current]
        if sessions:
            layout.addWidget(QLabel('Sélections', objectName='muted'))
            for session in sessions:
                self.target(layout, f"{session.name}   ·   {len(session.photos)} photo{'s' if len(session.photos) > 1 else ''}", session)
        layout.addWidget(QLabel('Nouvelle sélection', objectName='muted'))
        row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText('Nom de la sélection')
        self.name.textChanged.connect(lambda text: self.create.setEnabled(bool(text.strip())))
        self.name.returnPressed.connect(lambda: self.create.isEnabled() and self.choose('new'))
        row.addWidget(self.name, 1)
        self.create = QPushButton('Créer', objectName='primary')
        self.create.setEnabled(False)
        self.create.clicked.connect(lambda: self.choose('new'))
        row.addWidget(self.create)
        layout.addLayout(row)
        layout.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton('Annuler', objectName='ghost')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.setMinimumWidth(380)
        self.chosen = None

    def target(self, layout, text, value):
        button = QPushButton(text, objectName='ghost')
        button.setStyleSheet('text-align: left; padding: 8px 12px;')
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda checked=False: self.choose(value))
        layout.addWidget(button)
        self.targets.append((button, value))
        return button

    def choose(self, value):
        self.chosen = value
        self.accept()


class BlipDialog(QDialog):
    """Two QR codes, for an iPhone and for an Android phone, that open the store page of Blip."""

    def __init__(self, window, missing_on_computer=False):
        super().__init__(window)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(objectName='dialog')
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(8)
        layout.addWidget(QLabel('Installer Blip sur le téléphone', objectName='dialogTitle'))
        text = QLabel("Scannez le code de votre téléphone avec son appareil photo : il ouvre Blip dans l'App Store ou sur Google Play. "
                      "Connectez-vous ensuite avec le même compte que sur l'ordinateur.", objectName='muted')
        text.setWordWrap(True)
        text.setFixedWidth(440)
        layout.addWidget(text)
        layout.addSpacing(8)
        codes = QHBoxLayout()
        codes.setSpacing(24)
        for name, url in BLIP_STORES:
            column = QVBoxLayout()
            code = QLabel(alignment=Qt.AlignmentFlag.AlignHCenter)
            code.setPixmap(qr_pixmap(url, 190))
            column.addWidget(code)
            column.addWidget(QLabel(name, alignment=Qt.AlignmentFlag.AlignHCenter))
            codes.addLayout(column)
        layout.addLayout(codes)
        if missing_on_computer:
            note = QLabel('Blip manque aussi sur cet ordinateur.', objectName='faint')
            layout.addWidget(note)
        layout.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        if missing_on_computer:
            get = QPushButton("Installer Blip sur l'ordinateur", objectName='ghost')
            get.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://blip.net/download/windows')))
            buttons.addWidget(get)
        done = QPushButton('Fermer', objectName='primary')
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        layout.addLayout(buttons)


def qr_pixmap(text, side):
    """Black on white with a quiet margin, as every phone camera expects."""
    import qrcode
    code = qrcode.QRCode(border=3, error_correction=qrcode.constants.ERROR_CORRECT_M)
    code.add_data(text)
    code.make(fit=True)
    matrix = code.get_matrix()
    cells = len(matrix)
    scale = max(1, side//cells)
    image = QImage(cells*scale, cells*scale, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    for y, line in enumerate(matrix):
        for x, dark in enumerate(line):
            if dark:
                painter.fillRect(x*scale, y*scale, scale, scale, QColor('black'))
    painter.end()
    return QPixmap.fromImage(image)


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
        # Beside the panel of the view, and above the note along the bottom.
        strip = self.window_.bucket_strip.height()+36 if self.window_.bucket_strip.isVisible() else 0
        panel = self.window_.view_panel.width() if self.window_.view_panel.isVisible() else 0
        self.move(panel+12, self.window_.height()-self.height()-strip-(168 if self.window_.custom_active else 68 if self.window_.review_active else 12))
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
        # The buckets of what is open, whether a selection, a project or the portfolio.
        self.buckets = None
        self.portfolio = Portfolio(self.settings)
        self.portfolio.failed.connect(self.portfolio_failed)
        self.portfolio.changed.connect(self.portfolio_changed)
        self.relocating = None
        self.session = None
        self.project_path = None
        self.closing = False
        self.review_active = False
        order = self.settings.value('classic_sort', '') or None
        self.classic_order = order if order == 'shuffled' or any(order in entry[1:3] for entry in COLOR_ORDERS) else None
        self.classic_seed = random.randrange(1 << 30)
        self.photos = []
        self.cache = OrderedDict()
        self.pending = {}
        self.prepared = set()
        self.failed = set()
        self.undo_stack = []
        self.redo_stack = []
        self.shortcuts = []
        self.link_search = None
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
        self.brand = QLabel(APP_NAME, objectName='brand')
        self.brand.setToolTip(f'Version {APP_VERSION}')
        left.addWidget(self.brand)
        left.addSpacing(6)
        self.home_button = self.button(left, '‹ Accueil', self.go_home, 'ghost')
        self.home_button.setToolTip('Revenir à l’accueil. Vos notes et vos rangements sont enregistrés.')
        self.home_button.hide()
        self.project_button = QPushButton('Ouvrir', objectName='menu')
        self.project_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.project_menu = QMenu(self)
        self.project_menu.aboutToShow.connect(self.fill_project_menu)
        self.project_button.setMenu(self.project_menu)
        left.addWidget(self.project_button)
        self.undo_button = self.button(left, '↶ Annuler', self.undo, 'ghost')
        self.undo_button.hide()
        self.redo_button = self.button(left, '↷ Rétablir', self.redo, 'ghost')
        self.redo_button.hide()
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
        self.sort = self.combo(QHBoxLayout(), 'Tri', SORT_OPTIONS)
        segments = QFrame(objectName='segments')
        segment_row = QHBoxLayout(segments)
        segment_row.setContentsMargins(3, 3, 3, 3)
        segment_row.setSpacing(2)
        group = QButtonGroup(self)
        self.view_buttons = {}
        for key, label in [('classic', 'Classique'), *MODES]:
            button = QPushButton(label, objectName='mainseg', checkable=True)
            button.setMinimumWidth(button.sizeHint().width())
            group.addButton(button)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, value=key: self.set_view(value))
            segment_row.addWidget(button)
            self.view_buttons[key] = button
        self.view_buttons['classic'].setChecked(True)
        self.segments = segments
        self.filter_chip = HoverButton('Filtre', objectName='toggle', checkable=True)
        self.filter_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.filter_chip.clicked.connect(self.toggle_filter_panel)
        right.addSpacing(6)
        self.export_button = self.button(right, 'Exporter…', self.export, 'primary')
        self.prefs_button = self.button(right, 'Préférences', self.open_preferences, 'ghost')
        top.addLayout(left, 1)
        top.addWidget(segments)
        top.addWidget(self.filter_chip)
        top.addSpacing(6)
        self.bucket_button = QPushButton('Buckets', objectName='toggle', checkable=True)
        self.bucket_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bucket_button.setToolTip('Mode buckets : ils s’affichent en bas et les touches 1 à 5 les remplissent, au lieu de noter.')
        self.bucket_button.clicked.connect(self.toggle_bucket_mode)
        top.addWidget(self.bucket_button)
        top.addLayout(right, 1)
        self.header = header
        layout.addWidget(header)
        splitter = QSplitter()
        self.viewer = Viewer()
        self.viewer.backgroundChanged.connect(self.background_changed)
        self.viewer.liked = lambda: self.current_photo().liked if self.current_photo() else None
        self.shooting = {}
        self.viewer.caption = self.current_shooting_info
        self.viewer.add_export_action = lambda menu: self.add_export_action(menu, self.clicked_photos())
        self.viewer.heartClicked.connect(self.toggle_like)
        self.viewer.transform = lambda image: self.review.transformed(image)
        splitter.addWidget(self.viewer)
        self.list = PhotoList()
        self.list.owner = self
        self.list.setMinimumWidth(250)
        # Beyond this, the thumbnails of 400 pixels would turn blurry.
        self.list.setMaximumWidth(600)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setItemDelegate(PhotoDelegate(self))
        self.list.setUniformItemSizes(True)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(self.list)
        splitter.setSizes([1180, int(self.settings.value('ribbon_width', 260))])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.splitterMoved.connect(lambda *_: self.settings.setValue('ribbon_width', self.list.width()))
        # The same bar as the other views, always there under the photo, with its name.
        self.viewer_bar = RatingBar(self, self.viewer)
        self.viewer_bar.rated.connect(self.rate)
        self.viewer_bar.marked.connect(self.set_pick)
        self.viewer_bar.portfolio.connect(self.toggle_portfolio)
        self.viewer_bar.publish.connect(self.toggle_publish)
        self.viewer_bar.hide()
        self.note_filter = NoteFilter(self)
        self.marker_filter = MarkerFilter(self)
        self.choices = ChoiceFilter(self)
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
        self.bucket_ghost = QLabel(self)
        self.bucket_ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.bucket_ghost.hide()
        # On the right of every view, open or closed for good: the photos answer each filter beside it.
        self.filter_open = self.settings.value('filter_open', self.settings.value('filter_open_views', True, type=bool), type=bool)
        self.filter_panel = QFrame(objectName='dock')
        self.filter_panel.setFixedWidth(FILTER_WIDTH)
        panel_outer = QVBoxLayout(self.filter_panel)
        panel_outer.setContentsMargins(0, 0, 0, 0)
        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        panel_scroll.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }')
        panel_outer.addWidget(panel_scroll)
        panel_body = QWidget()
        panel_scroll.setWidget(panel_body)
        filter_lines = QVBoxLayout(panel_body)
        filter_lines.setContentsMargins(16, 14, 14, 16)
        filter_lines.setSpacing(6)
        filter_top = QHBoxLayout()
        filter_top.addWidget(QLabel('Filtre', objectName='dialogTitle'))
        filter_top.addStretch(1)
        self.button(filter_top, 'Tout effacer', self.clear_filters, 'ghost')
        filter_lines.addLayout(filter_top)
        self.filter_count = QLabel(objectName='muted')
        filter_lines.addWidget(self.filter_count)
        filter_lines.addSpacing(8)
        def section(title, widget):
            box = QWidget()
            lines = QVBoxLayout(box)
            lines.setContentsMargins(0, 0, 0, 10)
            lines.setSpacing(5)
            box.title = QLabel(title, objectName='muted')
            lines.addWidget(box.title)
            lines.addWidget(widget)
            filter_lines.addWidget(box)
            return box
        self.bucket_rule = BucketFilter(self)
        section('Note', self.note_filter)
        self.marker_section = section('Marqueur', self.marker_filter)
        section('Seulement', self.choices)
        self.bucket_section = section('Buckets', self.bucket_rule)
        # The next step of the journey, from what the filters have kept.
        kept = QWidget()
        kept_lines = QVBoxLayout(kept)
        kept_lines.setContentsMargins(0, 0, 0, 0)
        kept_lines.setSpacing(4)
        self.kept_portfolio = self.button(kept_lines, 'Ajouter au portfolio', self.add_filtered_to_portfolio, '')
        self.kept_selection = self.button(kept_lines, 'Créer une sélection…', self.select_filtered, '')
        self.kept_section = section('Avec ces photos', kept)
        filter_lines.addStretch(1)
        self.build_view_panel()
        self.custom.currentChanged.connect(lambda *_: self.update_project_state())
        content = QHBoxLayout()
        content.setSpacing(0)
        column = QVBoxLayout()
        column.setSpacing(0)
        # Below every view, in bucket mode, where the photos are sent.
        column.addWidget(self.pages, 1)
        column.addWidget(self.bucket_strip)
        # Above, how the photos are looked at; on the left, the settings of that view; on the right, which photos.
        content.addWidget(self.view_panel)
        content.addLayout(column, 1)
        content.addWidget(self.filter_panel)
        layout.addLayout(content, 1)
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
            'bucket_mode': self.toggle_bucket_mode, 'flag': self.toggle_flag, 'like': self.toggle_like, 'portfolio': self.toggle_portfolio, 'publish': self.toggle_publish, 'select': lambda: self.set_pick(PICK_SELECT),
            'reject': lambda: self.set_pick(PICK_REJECT), 'neutral': lambda: self.set_pick(PICK_NEUTRAL),
            'select_all': self.select_all, 'deselect': self.deselect, 'undo': self.undo, 'redo': self.redo, 'fit': self.fit,
            'zoom_in': lambda: self.zoom_by(1.15), 'zoom_out': lambda: self.zoom_by(1/1.15),
            'background': self.cycle_background, 'review': lambda: self.set_view('classic') if self.review_active else self.set_review(True),
            'shuffle': self.shuffle_photos,
            'mono': self.review.toggle_mono,
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
        self.undo_button.setToolTip(f"Annuler la dernière action ({' / '.join(mapping['undo'])})")
        self.redo_button.setToolTip(f"Rétablir ce qui vient d’être annulé ({' / '.join(mapping['redo'])})")
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
        self.marker_section.title.setText('Marqueur' if self.marker == 'pick' else 'Drapeau')
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

    def toggle_filter_panel(self, *args):
        """One choice for every view: the panel does not come and go when the view changes."""
        self.filter_open = not self.filter_open
        self.settings.setValue('filter_open', self.filter_open)
        self.sync_filter_panel()

    def sync_filter_panel(self):
        has_photos = bool(self.session and self.session.photos)
        # In a selection, the buckets filter too. Choosing its galleries is not looking at photos.
        with_filter = has_photos and not self.choosing_photos()
        self.filter_chip.setVisible(with_filter)
        shown = with_filter and self.filter_open
        self.filter_chip.setChecked(shown)
        self.filter_panel.setVisible(shown)
        self.bucket_section.setVisible(bool(self.buckets and (self.buckets.buckets or self.bucket_mode)))
        self.kept_section.setVisible(not self.selection)
        self.kept_portfolio.setVisible(not self.viewing_portfolio())
        if shown:
            self.bucket_rule.sync()
            self.update_filter_chip()

    def clear_filters(self):
        for combo in (self.stars, self.flags):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.choices.reset()
        if self.buckets:
            self.buckets.set_rule(include=[], exclude=[], none=False, all=False)
            self.bucket_rule.sync()
        self.filters_changed()

    def update_filter_chip(self):
        parts = [combo.currentText() for combo in (self.stars, self.flags) if combo.currentIndex() > 0]+self.choices.active_labels()
        # Elided: a long filter must not push the views out of the header. In a selection, Afficher already tells.
        text = self.filter_chip.fontMetrics().elidedText(' · '.join(parts), Qt.TextElideMode.ElideRight, 100)
        self.filter_chip.setText(f'Filtre : {text}' if parts else 'Filtre')
        self.filter_chip.setToolTip(' · '.join(parts) or 'Afficher ou masquer le panneau des filtres')
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
        if key == 'classic':
            self.set_review(False)
            return
        if key == 'color' and not self.color_sorted():
            # Ctrl+3: the Grille sorted by color, from the last color order chosen.
            self.set_classic_sort(self.review.views['color'].order)
        if key in ('wall', 'color'):
            key = 'color' if self.color_sorted() else 'wall'
        self.open_review_mode(key)

    def build_view_panel(self):
        """The settings of the view on screen, and nothing else."""
        self.view_panel = QFrame(objectName='dock')
        self.view_panel.setFixedWidth(VIEW_WIDTH)
        outer = QVBoxLayout(self.view_panel)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }')
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lines = QVBoxLayout(body)
        lines.setContentsMargins(14, 14, 14, 16)
        lines.setSpacing(14)
        self.view_title = QLabel(objectName='dialogTitle')
        lines.addWidget(self.view_title)
        def group(title, widget):
            box = QWidget()
            inner = QVBoxLayout(box)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(5)
            inner.addWidget(QLabel(title, objectName='muted'))
            inner.addWidget(widget)
            lines.addWidget(box)
            return box
        # The same choices as the grid: what to sort on, then which way.
        sorting = QWidget()
        sort_lines = QVBoxLayout(sorting)
        sort_lines.setContentsMargins(0, 0, 0, 0)
        sort_lines.setSpacing(5)
        self.classic_sort = QComboBox()
        self.classic_sort.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.classic_sort.addItem('Nom', 'name')
        self.classic_sort.addItem('Date', 'date')
        for label, one_way, *_ in COLOR_ORDERS:
            self.classic_sort.addItem(label, one_way)
        self.classic_sort.addItem('Mélangé', 'shuffled')
        self.classic_sort.currentIndexChanged.connect(lambda *_: self.set_classic_sort(self.classic_sort.currentData()))
        sort_lines.addWidget(self.classic_sort)
        self.classic_sort_button = self.button(sort_lines, '', self.flip_classic_sort)
        # It still holds the name or date order the grid shares.
        sort_lines.addWidget(self.sort)
        self.sort.hide()
        self.sort_section = group('Trier par', sorting)
        self.sync_classic_sort()
        # Black and white, zoom and background: the same section in every view.
        lines.addWidget(self.review.tools)
        stage = QWidget()
        stage_lines = QVBoxLayout(stage)
        stage_lines.setContentsMargins(0, 0, 0, 0)
        stage_lines.setSpacing(5)
        self.custom.photo_name.setWordWrap(True)
        stage_lines.addWidget(self.custom.photo_name)
        stage_lines.addWidget(self.custom.zoom_button)
        self.stage_section = group('Photo', stage)
        self.selection_section = QWidget()
        selection_lines = QVBoxLayout(self.selection_section)
        selection_lines.setContentsMargins(0, 0, 0, 0)
        add_more = self.button(selection_lines, "Ajouter des photos d'autres projets…", self.edit_selection)
        add_more.setToolTip('Cocher d’autres projets ou le portfolio, avec leurs filtres. Les buckets déjà remplis restent.')
        lines.addWidget(self.selection_section)
        # Then what belongs to the view on screen only.
        for pills in (*self.review.options.values(), (self.review.swipe_bucket_pill,)):
            for pill in pills:
                if pill is not None:
                    lines.addWidget(pill)
        lines.addStretch(1)

    def color_sorted(self):
        return self.classic_order not in (None, 'shuffled')

    def remember_sort(self):
        """Ctrl+Z puts the previous order back, as for a note."""
        order, seed, sort = self.classic_order, self.classic_seed, self.sort.currentIndex()

        def restore():
            opposite = self.remember_sort()
            self.classic_order, self.classic_seed = order, seed
            self.settings.setValue('classic_sort', order or '')
            self.sort.blockSignals(True)
            self.sort.setCurrentIndex(sort)
            self.sort.blockSignals(False)
            self.sort_changed()
            return opposite
        return restore

    def shuffle_photos(self):
        """R: Pêle-mêle deals its table again; elsewhere the photos take a new random order."""
        if self.review_active and self.review.mode == 'toile':
            self.review.shuffle()
        elif self.session and self.session.photos:
            step = self.remember_sort()
            self.classic_order = 'shuffled'
            self.classic_seed = random.randrange(1 << 30)
            self.settings.setValue('classic_sort', 'shuffled')
            self.sort_changed()
            self.remember(('view', 'Mélange', step))

    def sort_changed(self):
        """One order for Classique, the Grille, Tindr and Instagram: the Grille takes its colored form when sorted on a color."""
        self.sync_classic_sort()
        wall, color = self.review.views['wall'], self.review.views['color']
        wall.order_seed = None
        if self.color_sorted():
            color.order = self.classic_order
            self.settings.setValue('color_sort', self.classic_order)
            color.bar.setValue(0)
        self.review.sync_color_order()
        if self.review_active and self.review.mode in ('wall', 'color'):
            wanted = 'color' if self.color_sorted() else 'wall'
            if self.review.mode != wanted:
                self.review.set_mode(wanted)
        self.filters_changed()

    def set_classic_sort(self, value):
        if value in ('name', 'date'):
            self.classic_order = None
            way = self.sort.currentData().split('_')[1]
            self.sort.blockSignals(True)
            self.sort.setCurrentIndex(max(0, self.sort.findData(f'{value}_{way}')))
            self.sort.blockSignals(False)
        elif value == 'shuffled':
            self.classic_order = 'shuffled'
            self.classic_seed = random.randrange(1 << 30)
        else:
            self.classic_order = value
        self.settings.setValue('classic_sort', self.classic_order or '')
        self.sort_changed()

    def flip_classic_sort(self):
        order = self.classic_order
        if order == 'shuffled':
            self.classic_seed = random.randrange(1 << 30)
        elif order:
            entry = next(entry for entry in COLOR_ORDERS if order in entry[1:3])
            self.classic_order = entry[2] if order == entry[1] else entry[1]
            self.settings.setValue('classic_sort', self.classic_order)
        else:
            kind, way = self.sort.currentData().split('_')
            self.sort.blockSignals(True)
            self.sort.setCurrentIndex(max(0, self.sort.findData(f"{kind}_{'asc' if way == 'desc' else 'desc'}")))
            self.sort.blockSignals(False)
        self.sort_changed()

    def sync_classic_sort(self):
        if not hasattr(self, 'classic_sort'):
            return
        order = self.classic_order
        kind, way = self.sort.currentData().split('_')
        if order == 'shuffled':
            data, text, tip = 'shuffled', 'Remélanger', 'Un autre tirage au hasard'
        elif order:
            entry = next(entry for entry in COLOR_ORDERS if order in entry[1:3])
            other = order == entry[2]
            data, text, tip = entry[1], entry[4 if other else 3], entry[6 if other else 5]
        else:
            descending = way == 'desc'
            data, text = kind, 'Décroissant' if descending else 'Croissant'
            tip = ('Du plus récent au plus ancien' if descending else 'Du plus ancien au plus récent') if kind == 'date' else 'De Z à A' if descending else 'De A à Z'
        self.classic_sort.blockSignals(True)
        self.classic_sort.setCurrentIndex(max(0, self.classic_sort.findData(data)))
        self.classic_sort.blockSignals(False)
        self.classic_sort_button.setText(text)
        self.classic_sort_button.setToolTip(tip)

    def classic_sorted(self, photos):
        """The order of every view but Pêle-mêle, which deals at random."""
        if self.classic_order == 'shuffled':
            # Hashed, so that a photo leaving leaves the others where they are.
            return sorted(photos, key=lambda p: zlib.crc32(f'{self.classic_seed}|{p.relative_path}'.encode()))
        info = self.review.info
        return sorted(photos, key=lambda p: color_key(info.get(p.relative_path), self.classic_order))

    def sync_view_panel(self):
        has_photos = bool(self.session and self.session.photos)
        self.view_panel.setVisible(has_photos and not self.choosing_photos() or (bool(self.selection) and self.custom_active and not self.choosing_photos()))
        mode = 'wall' if self.review.mode == 'color' else self.review.mode
        self.view_title.setText(self.view_buttons[mode if self.review_active else 'classic'].text())
        # Pêle-mêle deals at random: no order to choose there.
        self.sort_section.setVisible(not self.custom_active and not (self.review_active and mode == 'toile'))
        self.stage_section.setVisible(self.custom_active)
        self.selection_section.setVisible(bool(self.selection))
        self.review.tools.setVisible(not self.custom_active)
        self.review.sync_toolbar()
        self.review.place_overlays()

    def sync_view_buttons(self):
        mode = 'wall' if self.review.mode == 'color' else self.review.mode
        self.view_buttons[mode if self.review_active else 'classic'].setChecked(True)
        self.sync_view_panel()
        self.sync_filter_panel()

    def show_viewer_bar(self):
        self.place_viewer_bar()
        # Tied to the photo, not to its preview: no blink while the next one loads.
        self.viewer_bar.setVisible(self.current_photo() is not None)
        self.viewer_bar.update()

    def hide_viewer_bar(self):
        """The bar stays, as in the other views: it only goes when there is no photo."""
        self.show_viewer_bar()

    def place_viewer_bar(self):
        self.viewer_bar.move((self.viewer.width()-self.viewer_bar.width())//2, self.viewer.height()-self.viewer_bar.height()-16)

    def set_background(self, color):
        self.viewer.set_background(color)

    def background_changed(self):
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
            # The choice of projects reads them from the disk: the latest notes must be there.
            self.save_session()
            self.custom_active = True
            self.update_project_state()
            self.custom.new_session()
            return
        self.set_folder(session.path.with_suffix(''), SelectionSession(session))
        if self.selection is session:
            # Classique keeps its ribbon and the filter beside the buckets. Only an empty selection says how to fill it.
            self.custom_active = not self.session.photos
            self.custom.open_session(session)
            self.update_project_state()

    def select_filtered(self):
        """A new selection that starts from this project and its filters: only the name is left to give."""
        key = str(self.portfolio.folder) if self.viewing_portfolio() else str(self.project_path)
        source = {'key': key, 'stars': self.stars.currentData(), 'marker': self.flags.currentData(),
                  'publish': self.choices.publish.isChecked(), 'liked': self.choices.liked.isChecked()}
        self.open_custom()
        self.custom.setup.start_from(source)

    def add_filtered_to_portfolio(self):
        self.add_photos_to_portfolio(self.photos)

    def add_photos_to_portfolio(self, photos):
        if not photos or not self.ready_for_portfolio():
            return
        photos = [photo for photo in photos if not self.portfolio.contains(photo.path)]
        if not photos:
            self.toast('Ces photos sont déjà dans le portfolio.')
            return
        self.send_to_portfolio(photos, True)
        self.remember(('portfolio', photos, True))
        self.toast(f"{len(photos)} photo{'s' if len(photos) > 1 else ''} ajoutée{'s' if len(photos) > 1 else ''} au portfolio")

    def leave_custom(self):
        self.custom.close_session()
        self.custom_active = False
        self.update_project_state()

    def go_home(self):
        if self.session:
            self.close_project()
        elif self.custom_active:
            self.leave_custom()

    @property
    def bucket_mode(self):
        """The buckets at the bottom and the keys 1 to 5 for them. A selection is made for that, a project rather for its notes."""
        return bool(self.buckets and self.buckets.data.get('strip', self.selection is not None))

    def toggle_bucket_mode(self, *args):
        if not self.buckets:
            return
        self.buckets.data['strip'] = not self.bucket_mode
        self.custom.save_timer.start()
        if not self.review_active:
            self.set_review(False)
        self.review.sync_swipe_bucket()
        self.review.place_overlays()
        self.review.view().update()
        self.update_project_state()

    def update_bucket_bars(self):
        """The strip is for the views that drag or click to a bucket. Tindr decides by swiping, about one of them."""
        swiping = self.review_active and self.review.mode == 'swipe'
        self.bucket_strip.setVisible(self.bucket_mode and bool(self.session and self.session.photos) and not self.custom_active and not swiping)
        self.bucket_button.setChecked(self.bucket_mode)
        self.bucket_button.setVisible(bool(self.buckets) and not self.choosing_photos() and (bool(self.session.photos) or bool(self.selection)))

    def swipe_to_bucket(self, photo, key, adding):
        if key:
            before = len(self.buckets.undo_stack)
            if adding:
                self.buckets.add(photo.relative_path, key)
            elif key in self.buckets.buckets_of(photo.relative_path):
                self.buckets.toggle(photo.relative_path, key)
            self.buckets_done(before)

    def choosing_photos(self):
        """On the screen that picks the projects of a selection, which has its own way out."""
        return self.custom_active and self.custom.currentWidget() is self.custom.setup

    def edit_selection(self):
        self.set_review(False)
        self.custom_active = True
        self.custom.edit_selection()
        self.update_project_state()

    def close_setup(self):
        """Back from the choice of projects to the photos, in Classique with its ribbon, or on the empty selection."""
        self.custom_active = not (self.session and self.session.photos)
        self.update_project_state()
        if self.custom_active:
            self.custom.show_photo()

    def show_buckets(self):
        """Classique, with the strip of the buckets under it."""
        self.set_review(False)

    def selection_changed(self):
        """Other photos or other buckets: what shows them follows."""
        if self.selection:
            self.session.refresh()
            self.choices.set_buckets()
        self.bucket_rule.rebuild()
        self.review.sync_swipe_bucket()
        self.bucket_strip.update()
        self.update_project_state()
        self.filters_changed()

    def photo_buckets(self, photo):
        """The buckets of a photo as (title, color)."""
        if not self.buckets:
            return []
        buckets = [self.buckets.bucket(key) for key in self.buckets.buckets_of(photo.relative_path)]
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
        self.bucket_strip.drag_to(point, photo)

    def bucket_drop(self, point, photo):
        self.bucket_ghost.hide()
        hit = self.bucket_strip.finish_drag(point)
        if hit:
            self.move_to_bucket(photo, *hit)

    def move_to_bucket(self, photo, key, mode='move'):
        """The photo, and the others picked with it. Seen through a bucket, it leaves that one and keeps its others."""
        rule = self.buckets.rule()
        source = rule['include'][0] if len(rule['include']) == 1 else None
        before = len(self.buckets.undo_stack)
        for target in self.with_selection(photo):
            if mode == 'remove':
                if key in self.buckets.buckets_of(target.relative_path):
                    self.buckets.toggle(target.relative_path, key)
            else:
                self.buckets.move(target.relative_path, key, source, mode == 'add')
        title = self.buckets.bucket(key)['title']
        self.toast({'remove': f'Retirée de {title}', 'add': f'Ajoutée aussi à {title}', 'move': f'Déplacée vers {title}'}[mode], 1000)
        self.buckets_done(before)

    def rate_key(self, value):
        """In bucket mode, the keys of the notes are those of the buckets: 1 for the first one, the zero star for none."""
        if self.custom_active:
            self.custom.key_bucket(value)
        elif not self.bucket_mode:
            self.rate(value)
        elif not value:
            before = len(self.buckets.undo_stack)
            for photo in self.target_photos():
                self.buckets.clear(photo.relative_path)
            self.buckets_done(before)
        elif value <= len(self.buckets.buckets):
            key = self.buckets.buckets[value-1]['id']
            photos = self.target_photos()
            self.toggle_bucket(key)
            # In Classique, one photo put in a bucket: on to the next one.
            if not self.review_active and len(photos) == 1 and key in self.buckets.buckets_of(photos[0].relative_path):
                self.navigate(1)

    def toggle_bucket(self, key):
        """In the bucket or out of it, on the photo shown and those picked with it."""
        photos = self.target_photos()
        if not photos:
            self.toast('Choisissez d’abord une photo.', 1600)
            return
        before = len(self.buckets.undo_stack)
        adding = key not in self.buckets.buckets_of(photos[0].relative_path)
        count = f'{len(photos)} photos ' if len(photos) > 1 else ''
        self.toast(f"{count}{'Ajoutée' if adding and not count else 'ajoutées' if adding else 'Retirée' if not count else 'retirées'} {'à' if adding else 'de'} {self.buckets.bucket(key)['title']}", 1000)
        for photo in photos:
            if adding:
                self.buckets.add(photo.relative_path, key)
            elif key in self.buckets.buckets_of(photo.relative_path):
                self.buckets.toggle(photo.relative_path, key)
        self.buckets_done(before)

    def buckets_done(self, before):
        changed = len(self.buckets.undo_stack)-before
        if changed:
            self.remember(('buckets', changed))
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
        if self.custom_active:
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
        if self.selection:
            menu.addAction("Ajouter des photos d'autres projets…", self.edit_selection)
            menu.addAction('Ouvrir un autre dossier…', self.open_folder)
        else:
            menu.addAction('Ajouter des photos…', self.open_folder)
        recent_menu = menu.addMenu('Projets')
        entries = self.recents.entries()
        for entry in entries[:15]:
            path = entry['path']
            action = recent_menu.addAction(project_label(path).replace('&', '&&'), lambda value=path: self.open_recent(value))
            action.setToolTip(path)
        recent_menu.setToolTipsVisible(True)
        recent_menu.setEnabled(bool(entries))
        selections = menu.addMenu('Sélections')
        for session in self.custom.store.sessions()[:15]:
            selections.addAction(session.name.replace('&', '&&'), lambda value=session: self.open_custom(value))
        selections.addSeparator()
        selections.addAction('Nouvelle sélection…', lambda: self.open_custom())
        menu.addAction('Portfolio', self.open_portfolio)
        menu.addSeparator()
        if self.selection:
            menu.addAction('Vérifier les photos de la sélection…', self.custom.check_photos)
        absent = len(getattr(self.session, 'absent', ()))
        if absent:
            menu.addAction(f'Retrouver les photos manquantes ({absent})…', self.repair_photos)
        menu.addAction('Rechercher sur un disque…', self.search_links)
        menu.addSeparator()
        menu.addAction("Revenir à l'accueil", self.go_home).setEnabled(self.session is not None or self.custom_active)

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
        if found:
            self.apply_found(found)
        self.toast(f'{len(found)} photo{"s" if len(found) > 1 else ""} retrouvée{"s" if len(found) > 1 else ""} sur {len(missing)}.', 4000)

    def apply_found(self, found):
        """New places, as {old: new}, in the portfolio and the selections, the open one first."""
        selection = self.selection
        self.custom.save()
        changed = self.portfolio.repoint(found.get)
        if selection:
            changed += selection.repoint(found.get)
        for session in self.custom.store.sessions():
            if not selection or session.path != selection.path:
                changed += session.repoint(found.get)
        if selection:
            self.selection_changed()
        elif self.session:
            self.refresh()
        return changed

    def search_links(self):
        """Everything lost, and the projects not listed yet, looked for on a disk or in a folder."""
        if self.link_search:
            self.toast('Une recherche est déjà en cours.')
            return
        chosen = self.ask_folder('Disque ou dossier à parcourir')
        if not chosen:
            return
        self.custom.save()
        self.toast('Recherche sur le disque en cours…', 600000)
        self.link_search = LinkSearchJob(*known_links(self.portfolio, self.custom.store.sessions(), self.recents), chosen)
        self.link_search.signals.done.connect(self.links_found, Qt.ConnectionType.QueuedConnection)
        self.pool.start(self.link_search, 1)

    def links_found(self, moves, photos, folders, projects):
        self.link_search = None
        self.toast_frame.hide()
        for old, new in moves.items():
            if self.portfolio.folder is not None and str(self.portfolio.folder) == old:
                self.portfolio.set_folder(new)
            self.recents.relocate(old, new)
        if moves:
            self.apply_found(moves)
        added = self.recents.merge([entry for entry in projects if not self.is_portfolio_path(entry['path'])])
        found = sum(Path(new).is_file() for new in moves.values())
        places = len(moves)-found
        left = photos+folders-len(moves)
        lines = []
        if photos:
            lines.append(f"{found} photo{'s' if found > 1 else ''} retrouvée{'s' if found > 1 else ''} sur {photos}.")
        if folders:
            lines.append(f"{places} dossier{'s' if places > 1 else ''} retrouvé{'s' if places > 1 else ''} sur {folders}.")
        if left:
            lines.append(f"{left} restent introuvables : ils sont peut-être sur un autre disque." if left > 1 else 'Un élément reste introuvable : il est peut-être sur un autre disque.')
        if added:
            lines.append(f"{added} nouveau{'x' if added > 1 else ''} projet{'s' if added > 1 else ''} sur l'accueil.")
        inform(self, 'Recherche sur le disque', '\n'.join(lines) or 'Rien de perdu ni de nouveau : tout est déjà à sa place.')
        self.home.refresh()

    def forget_recent(self, path):
        self.recents.remove(path)
        self.home.refresh()

    def prompt_for_photos(self):
        # With recent projects to pick from, the file dialog would only be in the way.
        if not self.closing and (not self.session or not self.session.photos) and not self.recents.entries():
            self.open_folder()

    def update_project_state(self):
        has_photos = bool(self.session and self.session.photos)
        page = 3 if self.custom_active else (2 if self.review_active else 1) if has_photos else 0
        self.home_button.setVisible(bool(self.session) or self.custom_active)
        self.show_open_name()
        self.update_bucket_bars()
        if page == 0:
            self.home.refresh()
        self.pages.setCurrentIndex(page)
        self.sync_view_buttons()
        choosing = self.choosing_photos()
        # One export, in one place, whatever is open. Nothing to filter or switch on the welcome screen.
        self.export_button.setVisible(bool(self.session) and not choosing and (has_photos or bool(self.selection)))
        self.counter.setVisible((has_photos or bool(self.selection)) and not choosing)
        self.segments.setVisible(has_photos and not choosing)
        self.undo_button.setVisible(has_photos and not choosing)
        self.redo_button.setVisible(has_photos and not choosing)
        self.sync_undo_buttons()
        if self.session:
            self.update_counter()
        name = self.selection.name if self.selection else project_label(self.project_path) if self.project_path else ''
        self.setWindowTitle(f'{APP_NAME} · {name}' if self.session else APP_NAME)

    def show_open_name(self):
        """The menu of the projects says which one is open."""
        name = self.selection.name if self.selection else 'Portfolio' if self.viewing_portfolio() else project_label(self.project_path) if self.session else 'Ouvrir'
        metrics = self.project_button.fontMetrics()
        shown = metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, 170)
        self.project_button.setText(shown.replace('&', '&&'))
        self.project_button.setMinimumWidth(metrics.horizontalAdvance(shown)+58)
        # Once something is open, its name takes the room of the name of the program.
        self.brand.setVisible(not self.session and not self.custom_active)
        self.project_button.setToolTip(name if self.selection else str(self.project_path) if self.session else '')

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
        if self.buckets:
            self.custom.close_session()
            self.selection = self.buckets = None
            self.bucket_rule.hide()
        self.review.reset()
        self.review_active = False
        self.undo_stack.clear()
        self.redo_stack.clear()
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
        tasks = [('Vérification des photos', self.custom.check_left), ('Lecture des projets', self.custom.setup.scanning()), ('Corbeille du portfolio', len(self.portfolio.removed)),
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
        self.buckets = self.selection or ProjectBuckets.of(session, 'Portfolio' if self.is_portfolio_path(path) else project_label(Path(path)))
        self.project_path = Path(path)
        self.baseline = original_inventory(path)
        self.portfolio.reconcile()
        if self.viewing_portfolio():
            self.portfolio.apply_seeds(session.photos)
        if self.selection:
            self.choices.set_buckets()
        else:
            self.choices.set_projects(sorted({self.portfolio.project_of(p.relative_path) for p in session.photos}) if self.viewing_portfolio() else [])
        self.bucket_rule.rebuild()
        self.bucket_rule.show()
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
        state = self.review.restore_state() or {}
        self.apply_filters()
        if session.photos and not self.photos:
            # The remembered filters no longer match anything: never open on an empty page.
            for combo in (self.stars, self.flags):
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
            self.choices.reset()
            if self.buckets:
                self.buckets.set_rule(include=[], exclude=[], none=False)
                self.bucket_rule.rebuild()
            self.apply_filters()
        self.update_project_state()
        self.resume(state)

    def resume(self, state):
        """Back on the photo and in the view where the project was left. A selection opens on its buckets."""
        row = next((i for i, photo in enumerate(self.photos) if photo.relative_path == state.get('photo')), None)
        if row is not None:
            self.list.setCurrentRow(row)
        if not self.selection:
            self.custom.open_session(self.buckets)
        if not self.selection and self.photos and state.get('view') in ('wall', 'toile', 'color', 'swipe', 'instagram'):
            # The Grille takes the form of the order kept: no sort is forced on reopening.
            self.set_view('wall' if state['view'] == 'color' else state['view'])
        elif not self.selection and self.photos and self.bucket_mode:
            self.show_buckets()

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
        if self.classic_order:
            filtered = self.classic_sorted(filtered)
        self.sync_classic_sort()
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
        self.list.verticalScrollBar().setValue(anchor_row*self.list.row_height()-offset if anchor_row is not None else scroll)
        self.list.blockSignals(False)
        self.save_timer.start(500)
        self.show_current()
        self.load_visible()
        self.notify_review()

    def notify_review(self):
        self.custom.filters_changed()
        if self.review_active:
            self.review.photos_changed()
        self.show_viewer_bar()
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
        # The selections share one cache: no single one of them gives it its size.
        job.preview_limit = SHARED_LIMIT if self.selection else int(min(4096, max(512, count*1.2)))*1024*1024
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

    def current_shooting_info(self):
        photo = self.current_photo()
        if photo is None:
            return ''
        key = str(photo.path)
        if key not in self.shooting:
            self.shooting[key] = shooting_info(photo.path)
        return self.shooting[key]

    def display_best(self):
        row = self.list.currentRow()
        if not 0 <= row < len(self.photos):
            return
        path = str(self.photos[row].path)
        candidates = [(side, image) for (name, side), image in self.cache.items() if name == path and side > 400]
        if candidates:
            self.viewer.image = max(candidates, key=lambda pair: pair[0])[1]
            self.viewer.update()
            self.show_viewer_bar()

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
        count = self.list.viewport().height()//self.list.row_height()+3
        for photo in self.photos[first:first+count]:
            self.request(photo, 400)

    def zoom_changed(self, zoom):
        self.schedule_detail()
        self.review.sync_zoom()

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
        if self.custom_active:
            self.custom.update_counter()
        elif self.photos:
            five = sum(p.stars == 5 for p in self.photos)
            current = self.current_photo()
            self.bucket_strip.update()
            if self.review_active and not self.review.targets():
                # Nothing picked on the page yet: name no photo.
                self.counter.setText(f'{len(self.photos)} photos')
                current = None
            else:
                self.counter.setText(f'{self.list.currentRow()+1} / {len(self.photos)}')
            # A selection has more views in the header: its ribbon and its bars name the photo.
            # The bar under the photo names it, in every view: the header only counts.
            self.name_label.setText('')
            self.counter.setToolTip(f'{len(self.list.selectedItems())} sélectionnée(s) · {five} à 5 étoiles')
        else:
            self.counter.setText('Aucune photo')
            self.name_label.clear()

    def change(self, photos, stars=None, pick=None, liked=None, publish=None):
        """Single entry point for notes and markers: it is what makes them undoable."""
        if not photos:
            self.ask_for_photo()
        if not self.session or not photos:
            return
        self.remember(self.notes_step(photos, set()))
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

    def ask_for_photo(self):
        """A key that acts on nothing says so, rather than looking broken."""
        if self.session and self.photos and not self.custom_active:
            self.toast('Cliquez d’abord sur une photo.', 1600)

    def notes_step(self, photos, aside):
        return ('notes', [(photo, photo.stars, photo.pick, photo.liked, photo.publish) for photo in photos], aside)

    def remember(self, entry):
        """A step that Ctrl+Z and Annuler can take back: ('notes', states, photos set aside), ('buckets', how many),
        ('portfolio', photos, added) or ('view', name, how to restore). A new step ends what could be redone."""
        self.undo_stack.append(entry)
        del self.undo_stack[:-300]
        self.redo_stack.clear()
        if self.buckets:
            self.buckets.redo_stack.clear()
        self.sync_undo_buttons()

    def can_undo(self):
        return bool(self.undo_stack or (self.buckets and self.buckets.undo_stack))

    def can_redo(self):
        return bool(self.redo_stack or (self.buckets and self.buckets.redo_stack))

    def sync_undo_buttons(self):
        self.undo_button.setEnabled(self.can_undo())
        self.redo_button.setEnabled(self.can_redo())

    def undo(self):
        if self.custom_active:
            self.custom.undo()
        elif self.can_undo():
            # A step made on the Buckets page is taken back from any view of the selection.
            self.redo_stack.append(self.apply_step(self.undo_stack.pop() if self.undo_stack else ('buckets', 1), True))
        else:
            self.toast('Rien à annuler')
        self.sync_undo_buttons()

    def redo(self):
        if self.custom_active:
            self.custom.redo()
        elif self.can_redo():
            self.undo_stack.append(self.apply_step(self.redo_stack.pop() if self.redo_stack else ('buckets', 1), False))
        else:
            self.toast('Rien à rétablir')
        self.sync_undo_buttons()

    def apply_step(self, step, undoing):
        """Undo or redo one step; what comes back is the step that reverses it."""
        word = 'annulé' if undoing else 'rétabli'
        kind = step[0]
        if kind == 'view':
            opposite = ('view', step[1], step[2]())
            self.toast(f'{step[1]} {word}', 1200)
        elif kind == 'buckets':
            paths = [(self.buckets.undo if undoing else self.buckets.redo)() for _ in range(step[1])]
            opposite = step
            self.custom.changed()
            if self.review_active:
                self.review.after_undo([photo for photo in self.session.photos if photo.relative_path in paths])
            self.toast(f'Rangement {word}', 1200)
        elif kind == 'portfolio':
            _, photos, added = step
            self.send_to_portfolio(photos, not added)
            opposite = ('portfolio', photos, not added)
            self.toast(f'Portfolio : {word}', 1200)
        else:
            _, states, aside = step
            photos = [state[0] for state in states]
            opposite = self.notes_step(photos, set())
            for photo, stars, pick, liked, publish in states:
                photo.stars, photo.pick, photo.liked, photo.publish = stars, pick, liked, publish
            opposite = (*opposite[:2], self.review.set_aside_again(photos, aside))
            self.apply_filters()
            if self.review_active:
                self.review.after_undo(photos)
            self.toast(f'Note {word}e', 1200)
        return opposite

    def rate(self, stars):
        if self.custom_active:
            self.custom.key_bucket(stars)
            return
        self.change(self.target_photos(), stars=stars)

    def toggle_like(self):
        photos = self.target_photos()
        if photos:
            self.change(photos, liked=not photos[0].liked)
        else:
            self.ask_for_photo()

    def toggle_publish(self):
        photos = self.target_photos()
        if photos:
            self.change(photos, publish=not photos[0].publish)
        else:
            self.ask_for_photo()

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
        if choices.liked.isChecked() and not photo.liked:
            return False
        if self.buckets and not self.buckets.matches(photo.relative_path):
            return False
        if self.selection or choices.project.currentIndex() <= 0:
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
        self.remember(('portfolio', photos, adding))
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
        if self.buckets and self.buckets.data['assigned']:
            choice = ask(self, 'Exporter', 'Copier les photos rangées, avec un dossier par bucket, ou choisir les photos par leurs notes et leurs marques ?',
                         [('cancel', 'Annuler', False), ('filters', 'Par filtres', False), ('buckets', 'Un dossier par bucket', True)])
            if choice == 'buckets':
                self.custom.export()
            if choice != 'filters':
                return
        dialog = ExportDialog(self)
        if dialog.exec():
            self.run_export(dialog.photos(), dialog.folder_name())

    def export_selection(self, stars, marker, portfolio, publish, project, liked=False):
        """Among the whole gallery, whatever is on screen. A rejected photo only leaves when asked for."""
        return [p for p in self.session.photos if stars_match(p, stars) and marker_matches(p, marker) and self.buckets.matches(p.relative_path)
                and (p.pick != PICK_REJECT or marker == 'reject') and (not portfolio or self.in_portfolio(p))
                and (not publish or p.publish) and (not liked or p.liked) and (project == 'all' or self.portfolio.project_of(p.relative_path) == project)]

    def clicked_photos(self, photo=None):
        """What a right click concerns: the photo under the mouse and its selection, or the selection alone on an empty spot."""
        return self.with_selection(photo) if photo else self.target_photos()

    def add_export_action(self, menu, photos):
        """At the head of a right click's menu. Exporting one photo, or all those selected, asks nothing."""
        if not photos:
            return
        action = QAction('Exporter cette photo' if len(photos) == 1 else f'Exporter les {len(photos)} photos sélectionnées', menu)
        action.setEnabled(not (self.export_job or self.relocating or self.update_job))
        action.triggered.connect(lambda checked=False: self.quick_export(photos))
        adding = QAction('Ajouter à…', menu)
        adding.setEnabled(not self.relocating)
        adding.triggered.connect(lambda checked=False: self.add_photos_to(photos))
        first = menu.actions()[0] if menu.actions() else None
        blip = QAction('Envoyer avec Blip', menu)
        blip.triggered.connect(lambda checked=False: self.send_with_blip(photos))
        install = QAction('Installer Blip sur un téléphone…', menu)
        install.triggered.connect(lambda checked=False: BlipDialog(self, blip_executable() is None).exec())
        menu.insertAction(first, action)
        menu.insertAction(first, adding)
        menu.insertAction(first, blip)
        menu.insertAction(first, install)
        if first:
            menu.insertSeparator(first)

    def add_photos_to(self, photos, choice=None):
        """The portfolio, a saved selection or a new one, from the photos of a right click."""
        if choice is None:
            dialog = AddToDialog(self, photos)
            if dialog.exec() != QDialog.DialogCode.Accepted or dialog.chosen is None:
                return
            choice = dialog.chosen
            if choice == 'new':
                choice = dialog.name.text().strip()
        if choice == 'portfolio':
            self.add_photos_to_portfolio(photos)
            return
        if isinstance(choice, str):
            target = self.custom.store.create(choice)
        else:
            target = choice
        count = target.add_photos([self.selection_entry(photo) for photo in photos])
        target.save()
        name = target.name
        if not count:
            self.toast(f'Déjà dans « {name} ».' if len(photos) == 1 else f'Ces photos sont déjà dans « {name} ».')
        else:
            self.toast(f"{count} photo{'s' if count > 1 else ''} ajoutée{'s' if count > 1 else ''} à « {name} »")

    def send_with_blip(self, photos):
        """Blip opens on a new sending of these photos; the phone is chosen there."""
        if not photos:
            return
        if blip_executable() is None:
            BlipDialog(self, True).exec()
            return
        error = send_with_blip([photo.path for photo in photos])
        if error:
            inform(self, 'Envoi impossible', error)
        else:
            count = len(photos)
            self.toast(f"{count} photo{'s' if count > 1 else ''} dans Blip : choisissez le téléphone.")

    def selection_entry(self, photo):
        """A photo as a selection holds it: where it is, the project it comes from, and its path inside that project."""
        if self.selection:
            held = next((item for item in self.selection.photos if item['path'] == photo.relative_path), None)
            if held:
                return {'path': held['path'], 'gallery': held.get('gallery', ''), 'relative': held.get('relative', photo.path.name)}
        if self.viewing_portfolio():
            origin = self.portfolio.items.get(photo.relative_path, {}).get('source')
            entry = {'path': str(photo.path), 'gallery': self.photo_project(photo) or 'Portfolio', 'relative': photo.relative_path}
            return {**entry, 'origin': origin} if origin else entry
        return {'path': str(photo.path), 'gallery': project_label(self.project_path), 'relative': photo.relative_path}

    def quick_export(self, photos):
        """A folder of its own, named after the moment: nothing to choose and nothing replaced. A rejected photo goes too, since it was asked for."""
        if self.selection:
            self.custom.export_photos(photos)
            return
        stamp, suffix = f"Photos choisies {time.strftime('%Y-%m-%d %Hh%M')}", 2
        name = stamp
        while (self.session.session_dir/EXPORT_ROOT_NAME/name).exists():
            name, suffix = f'{stamp}-{suffix}', suffix+1
        self.run_export(photos, name)

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

    def clean_caches(self):
        """Once a day, without asking: previews unused for two months go, and are made again when needed."""
        last = float(self.settings.value('cache_cleaned', 0) or 0)
        if self.closing or time.time()-last < DAY:
            return
        self.settings.setValue('cache_cleaned', time.time())
        roots = [Path(entry['path']) for entry in self.recents.entries()]
        if self.portfolio.folder is not None:
            roots.append(Path(self.portfolio.folder))
        self.pool.start(CacheJob(roots, Store(SETTINGS_SCOPE).folder), -10)

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
        if self.link_search:
            self.link_search.cancelled = True
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
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Choyce')
    app.setWindowIcon(QIcon(str(ASSETS/'triphoto.ico')))
    window = Window(args.folder)
    window.showMaximized()
    # A few seconds later: opening the project comes first.
    QTimer.singleShot(4000, window.check_for_update)
    QTimer.singleShot(30000, window.clean_caches)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
