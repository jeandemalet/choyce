"""High-DPI Qt interface. Original files and session format stay unchanged."""
from __future__ import annotations

import argparse
import hashlib
import os
import math
from io import BytesIO
from pathlib import Path
from collections import OrderedDict

from PySide6.QtCore import Qt, QSize, QRectF, QPointF, Signal, Slot, QObject, QRunnable, QThreadPool, QTimer, QItemSelectionModel, QSettings
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor, QFont, QShortcut, QKeySequence, QColorSpace, QPolygonF, QPen
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QSplitter, QListWidget, QListWidgetItem,
    QStackedWidget, QAbstractItemView, QFileDialog, QMessageBox, QStyledItemDelegate, QStyle, QMenu)
from PIL import Image, ImageCms, ImageWin
from triphoto import (PhotoSession, sort_photos, SORT_OPTIONS, STAR_FILTER_OPTIONS,
    FLAG_FILTER_OPTIONS, load_screen_preview, load_srgb, original_inventory,
    export_original_copy, EXPORT_DIR_NAME)


STYLE = """
QWidget { background: #202124; color: #e4e5e7; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow { background: #17181a; }
QLabel#brand { font-size: 17px; font-weight: 600; }
QLabel#muted { color: #969aa2; }
QWidget#header, QWidget#footer { background: #242528; }
QPushButton { background: #303236; border: 1px solid #414348; border-radius: 6px; padding: 8px 14px; }
QPushButton:hover { background: #3b3e43; border-color: #757b84; }
QPushButton:pressed { background: #484d54; }
QPushButton#primary { background: #d5dbe2; color: #202124; border-color: #d5dbe2; font-weight: 600; }
QPushButton#primary:hover { background: #eef1f5; }
QComboBox { background: #303236; border: 1px solid #414348; border-radius: 6px; padding: 7px 10px; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #303236; selection-background-color: #4b525c; }
QListWidget { background: #242528; border: none; outline: none; }
QScrollBar:vertical { background: #242528; width: 10px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #555b64; border-radius: 4px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QSplitter::handle { background: #17181a; width: 3px; }
QToolTip { background: #34373c; color: #eee; border: 1px solid #555; padding: 6px; }
"""


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
            if self.side == 1000:
                stat = self.path.stat()
                monitor = hashlib.sha256(self.display_profile or b'sRGB').hexdigest()
                token = hashlib.sha256(f'qt-direct-v1|{self.path}|{stat.st_size}|{stat.st_mtime_ns}|{monitor}'.encode()).hexdigest()
                cache_path = self.cache_dir / f'qt-{token}.png'
                if cache_path.exists():
                    try:
                        with Image.open(cache_path) as cached:
                            image = cached.convert('RGB')
                        os.utime(cache_path, None)
                    except (OSError, ValueError):
                        image = None
            if image is None:
                image = load_screen_preview(self.path, self.side, target)
                if cache_path is not None:
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

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.zoom = 1.0
        self.pan = QPointF()
        self.drag = None
        self.message = 'Ouvrez un dossier pour commencer'
        self.settings = QSettings('TriPhoto', 'TriPhoto')
        self.background = self.settings.value('background', '#808080')
        self.setMinimumSize(360, 260)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.background))
        if self.image.isNull():
            painter.setPen(QColor('#9298a1'))
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

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        for label, color in [('Blanc', '#ffffff'), ('Gris clair', '#bfbfbf'),
                             ('Gris moyen', '#808080'), ('Gris foncé', '#404040'), ('Noir', '#000000')]:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self.background == color)
            action.triggered.connect(lambda checked=False, value=color: self.set_background(value))
        menu.addSeparator()
        menu.addAction('Ajuster la photo', lambda: self.set_zoom(1))
        menu.exec(event.globalPos())


class PhotoDelegate(QStyledItemDelegate):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def sizeHint(self, option, index):
        return QSize(260, 114)

    def paint(self, painter, option, index):
        photo = index.data(Qt.ItemDataRole.UserRole)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect.adjusted(4, 2, -3, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor('#717b88') if selected else QColor('#34363b'))
        painter.setBrush(QColor('#343940') if selected else QColor('#292b2f'))
        painter.drawRoundedRect(rect, 7, 7)
        thumb = QRectF(rect.x()+8, rect.y()+8, 112, 72)
        painter.fillRect(thumb, QColor('#17181a'))
        image = self.window.cache.get((str(photo.path), 400))
        if image is not None:
            size = image.size().scaled(QSize(112, 72)*self.window.devicePixelRatioF(), Qt.AspectRatioMode.KeepAspectRatio)
            width, height = size.width()/self.window.devicePixelRatioF(), size.height()/self.window.devicePixelRatioF()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(thumb.center().x()-width/2, thumb.center().y()-height/2, width, height), image)
        painter.setPen(QColor('#e1e4e8'))
        painter.drawText(QRectF(rect.x()+132, rect.y()+10, rect.width()-172, 24), str(index.row()+1).zfill(3))
        font = QFont('Segoe UI', 9)
        painter.setFont(font)
        name = painter.fontMetrics().elidedText(photo.path.name, Qt.TextElideMode.ElideMiddle, max(20, rect.width()-28))
        painter.setPen(QColor('#aeb4be'))
        painter.drawText(QRectF(rect.x()+10, rect.bottom()-24, rect.width()-20, 20), name)
        # Vector stars stay sharp at every display scale, with a clear gap.
        for star in range(5):
            center = QPointF(rect.x()+140+star*22, rect.y()+60)
            points = QPolygonF()
            for vertex in range(10):
                angle = -math.pi/2+vertex*math.pi/5
                radius = 8 if vertex % 2 == 0 else 3.5
                points.append(center+QPointF(math.cos(angle)*radius, math.sin(angle)*radius))
            painter.setPen(QPen(QColor('#c6b58c') if star < photo.stars else QColor('#707780'), 1.1))
            painter.setBrush(QColor('#c6b58c') if star < photo.stars else Qt.BrushStyle.NoBrush)
            painter.drawPolygon(points)
        painter.setPen(QColor('#d8c493') if photo.flagged else QColor('#7e858f'))
        painter.drawText(QRectF(rect.right()-34, rect.y()+12, 25, 24), '⚑')
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == event.Type.MouseButtonRelease and event.position().x() >= option.rect.right()-45 and event.position().y() < option.rect.top()+44:
            self.window.toggle_flag(index.row())
            return True
        return super().editorEvent(event, model, option, index)


class Window(QMainWindow):
    def __init__(self, folder=None):
        super().__init__()
        self.session = None
        self.closing = False
        self.photos = []
        self.cache = OrderedDict()
        self.pending = {}
        self.prepared = set()
        self.display_profile = None
        self.display_signature = None
        self.color_generation = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.setWindowTitle('TriPhoto')
        self.resize(1440, 920)
        self.setMinimumSize(980, 640)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)
        header = QWidget(objectName='header')
        top = QHBoxLayout(header)
        top.setContentsMargins(14, 9, 12, 9)
        top.setSpacing(12)
        top.addWidget(QLabel('TriPhoto', objectName='brand'))
        self.folder_label = QLabel('', objectName='muted')
        self.folder_label.hide()
        self.counter = QLabel('Aucune photo', objectName='muted')
        top.addWidget(self.counter)
        top.addStretch(1)
        self.sort = self.combo(top, 'Tri', SORT_OPTIONS)
        self.stars = self.combo(top, 'Notes', STAR_FILTER_OPTIONS)
        self.flags = self.combo(top, 'Drapeaux', FLAG_FILTER_OPTIONS)
        self.button(top, 'Ouvrir un projet', self.open_folder).setToolTip('Ouvrir un dossier de photos')
        self.close_project_button = self.button(top, 'Fermer le projet', self.close_project)
        self.close_project_button.setEnabled(False)
        self.button(top, 'Exporter les 5 étoiles', self.export, 'primary')
        layout.addWidget(header)
        splitter = QSplitter()
        self.viewer = Viewer()
        splitter.addWidget(self.viewer)
        self.list = QListWidget()
        self.list.setMinimumWidth(260)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setItemDelegate(PhotoDelegate(self))
        self.list.setUniformItemSizes(True)
        splitter.addWidget(self.list)
        splitter.setSizes([1180, 260])
        self.pages = QStackedWidget()
        self.empty_page = QWidget()
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.addStretch()
        empty_title = QLabel('Ajoutez des photos pour commencer')
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        self.add_photos_button = QPushButton('Ajouter des photos', objectName='primary')
        self.add_photos_button.clicked.connect(self.open_folder)
        empty_layout.addWidget(self.add_photos_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        self.pages.addWidget(self.empty_page)
        self.pages.addWidget(splitter)
        layout.addWidget(self.pages, 1)
        self.status = QLabel(self)
        self.status.hide()
        self.viewer.setToolTip('C / V : précédente / suivante\nMaj C / V : 0 / 5 étoiles\n1 à 5 : noter\nF : drapeau · 0 : ajuster\nClic droit : couleur du fond')
        self.list.currentRowChanged.connect(self.show_current)
        self.list.itemSelectionChanged.connect(self.update_counter)
        self.list.verticalScrollBar().valueChanged.connect(self.load_visible)
        self.viewer.zoomChanged.connect(self.zoom_changed)
        self.viewer.resized.connect(self.schedule_detail)
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
        for key, action in [('C', lambda: self.navigate(-1)), ('Left', lambda: self.navigate(-1)),
            ('V', lambda: self.navigate(1)), ('Right', lambda: self.navigate(1)),
            ('Shift+Left', lambda: self.navigate(-1)), ('Shift+Right', lambda: self.navigate(1)),
            ('Shift+C', lambda: self.rate(0)), ('Shift+V', lambda: self.rate(5)),
            ('0', lambda: self.viewer.set_zoom(1)), ('F', self.toggle_flag),
            ('+', lambda: self.viewer.set_zoom(self.viewer.zoom*1.15)),
            ('-', lambda: self.viewer.set_zoom(self.viewer.zoom/1.15))]:
            QShortcut(QKeySequence(key), self, activated=action)
        for stars in range(1, 6):
            QShortcut(QKeySequence(str(stars)), self, activated=lambda value=stars: self.rate(value))
        QShortcut(QKeySequence('F5'), self, activated=self.refresh)
        if folder:
            self.set_folder(Path(folder))
        if not self.session or not self.session.photos:
            QTimer.singleShot(0, self.prompt_for_photos)
        QShortcut(QKeySequence('Ctrl+O'), self, activated=self.open_folder)
        QShortcut(QKeySequence('Ctrl+W'), self, activated=self.close_project)

    def prompt_for_photos(self):
        if not self.closing and (not self.session or not self.session.photos):
            self.open_folder()

    def update_project_state(self):
        has_photos = bool(self.session and self.session.photos)
        self.pages.setCurrentIndex(1 if has_photos else 0)
        self.close_project_button.setEnabled(self.session is not None)
        self.setWindowTitle(f'TriPhoto · {self.session.root.name}' if self.session else 'TriPhoto')

    def close_project(self):
        if self.session:
            self.session.save()
        self.color_generation += 1
        self.pool.clear()
        self.pending.clear()
        self.detail_timer.stop()
        self.session = None
        self.photos = []
        self.list.clear()
        self.cache.clear()
        self.prepared.clear()
        self.folder_label.clear()
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
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def combo(self, layout, label, values):
        combo = QComboBox()
        combo.setToolTip(label)
        for key, text in values.items():
            combo.addItem(text, key)
        combo.setMaximumWidth({'Tri': 175, 'Notes': 145, 'Drapeaux': 165}[label])
        combo.currentIndexChanged.connect(self.apply_filters)
        layout.addWidget(combo)
        return combo

    def open_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, 'Choisir le dossier de photos')
        if chosen:
            self.set_folder(Path(chosen))

    def set_folder(self, path):
        try:
            session = PhotoSession(path)
        except Exception as error:
            QMessageBox.critical(self, 'Ouverture impossible', str(error))
            return
        self.close_project()
        self.session = session
        self.baseline = original_inventory(path)
        self.cache_dir = session.session_dir / 'Previews'
        self.cache_dir.mkdir(exist_ok=True)
        self.cache.clear()
        self.prepared.clear()
        self.folder_label.setText(path.name)
        self.folder_label.setToolTip(str(path))
        self.sort.blockSignals(True)
        self.sort.setCurrentIndex(self.sort.findData(session.sort_mode))
        self.sort.blockSignals(False)
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
        filtered = [p for p in self.session.photos if (stars == 'all' or p.stars == stars)
            and (flag == 'all' or p.flagged == (flag == 'flagged'))]
        if len(filtered) == len(self.photos) and all(a is b for a, b in zip(filtered, self.photos)):
            # Ratings and flags usually change only the decoration. Rebuilding
            # the list makes Qt scroll the current item back into view.
            self.session.save()
            self.list.viewport().update()
            self.update_counter()
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
        self.list.verticalScrollBar().setValue(anchor_row*114-offset if anchor_row is not None else scroll)
        self.list.blockSignals(False)
        self.session.save()
        self.show_current()
        self.load_visible()

    def request(self, photo, side, priority=0):
        if self.closing:
            return
        key = (str(photo.path), side)
        if key in self.cache or key in self.pending:
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
        self.pending.pop((path, side), None)
        if self.session is None or not any(str(p.path) == path for p in self.session.photos):
            return
        if not image.isNull():
            if side == 1000:
                self.prepared.add(path)
            self.cache[(path, side)] = image
            self.cache.move_to_end((path, side))
            while len(self.cache) > 60:
                self.cache.popitem(last=False)
            # Keep large previews bounded separately from lightweight thumbnails.
            large = [key for key in self.cache if key[1] > 1000]
            for key in large[:-4]:
                self.cache.pop(key, None)
            # Also bound actual decoded bytes, especially on large monitors.
            for key in list(self.cache):
                if sum(item.sizeInBytes() for item in self.cache.values()) <= 192*1024*1024:
                    break
                current = self.list.currentRow()
                if 0 <= current < len(self.photos) and key[0] == str(self.photos[current].path):
                    continue
                self.cache.pop(key, None)
        row = self.list.currentRow()
        if 0 <= row < len(self.photos) and str(self.photos[row].path) == path and side > 400:
            if error:
                self.status.setText('Lecture impossible : '+error)
                if self.viewer.image.isNull():
                    self.viewer.message = 'Aperçu indisponible : '+error
                    self.viewer.update()
            else:
                self.display_best()
        self.list.viewport().update()

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
            if self.session:
                self.show_current()
                self.load_visible()

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
                    if str(photo.path) not in self.prepared and (str(photo.path), 1000) not in self.pending:
                        self.request(photo, 1000, -10)
                        return

    def schedule_detail(self):
        if hasattr(self, 'detail_timer'):
            self.detail_timer.start(120)

    def load_detail(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.photos):
            side = max(2000, min(6000, int(max(self.viewer.width(), self.viewer.height())*self.devicePixelRatioF()*self.viewer.zoom)))
            side = ((side+499)//500)*500
            self.request(self.photos[row], side, 20)

    def load_visible(self, *args):
        if not self.photos:
            return
        first = self.list.indexAt(self.list.viewport().rect().topLeft()).row()
        first = max(0, first)
        count = self.list.viewport().height()//114+3
        for photo in self.photos[first:first+count]:
            self.request(photo, 400)

    def zoom_changed(self, zoom):
        self.schedule_detail()

    def navigate(self, delta):
        if self.photos:
            row = max(0, min(len(self.photos)-1, self.list.currentRow()+delta))
            # Explicitly replace selection even if Shift remains held after rating.
            self.list.setCurrentRow(row, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.list.scrollToItem(self.list.item(row))

    def update_counter(self):
        if self.photos:
            five = sum(p.stars == 5 for p in self.photos)
            self.counter.setText(f'{self.list.currentRow()+1} / {len(self.photos)}')
            self.counter.setToolTip(f'{len(self.list.selectedItems())} sélectionnée(s) · {five} à 5 étoiles')
        else:
            self.counter.setText('Aucune photo')

    def rate(self, stars):
        if not self.session:
            return
        for item in self.list.selectedItems():
            item.data(Qt.ItemDataRole.UserRole).stars = stars
        self.apply_filters()

    def toggle_flag(self, row=None):
        if row is None:
            row = self.list.currentRow()
        if 0 <= row < len(self.photos):
            self.photos[row].flagged = not self.photos[row].flagged
            self.apply_filters()

    def refresh(self):
        if self.session:
            self.session.refresh()
            self.apply_filters()
            self.update_project_state()

    def export(self):
        if not self.session:
            return
        if original_inventory(self.session.root) != self.baseline:
            QMessageBox.warning(self, 'Export interrompu', 'Le dossier a changé depuis son ouverture. Rouvrez-le avant l’export.')
            return
        photos = [p for p in self.photos if p.stars == 5]
        if not photos:
            QMessageBox.information(self, 'Export', 'Aucune photo à cinq étoiles dans la sélection filtrée.')
            return
        destination = self.session.session_dir / EXPORT_DIR_NAME
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
        if self.session:
            self.session.save()
        self.pool.clear()
        self.pool.waitForDone()
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder')
    args = parser.parse_args()
    app = QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Window(args.folder)
    window.showMaximized()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
