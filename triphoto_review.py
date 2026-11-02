"""The Revoir page: other ways of looking at the photos selected by the filters.

Every mode reads window.photos, so the header filters drive it, and rates through
the window, so notes, undo and session saving behave as on the Tri page.
"""
from __future__ import annotations

import json
import math
import random
import threading

from PySide6.QtCore import Qt, QObject, QPointF, QRectF, QRunnable, QThreadPool, QTimer, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QColorDialog, QComboBox, QFrame, QHBoxLayout, QLabel,
    QMenu, QPushButton, QScrollBar, QStackedWidget, QWidget)
from PIL import Image
from triphoto import PICK_NEUTRAL, PICK_REJECT, PICK_SELECT
from triphoto_theme import ACCENT, BACKGROUNDS, FAINT, MUTED, REJECT, SELECT, TEXT, contrast_on, draw_check, draw_cross, draw_flag, draw_stars

MODES = [('toile', 'Toile'), ('swipe', 'Swipe'), ('color', 'Couleur'), ('wall', 'Mosaïque'), ('duel', 'Duel')]
SWIPE_RULES = {'pick': 'Droite : retenir · Gauche : rejeter', 'stars': 'Droite : +1 étoile · Gauche : -1 étoile',
               'five': 'Droite : 5 étoiles · Gauche : 0 étoile'}
TOP_CLEARANCE, BOTTOM_CLEARANCE = 66, 76
ANALYSIS_FILE = 'colors.json'


def analyse_photo(path):
    """Aspect ratio and dominant color, from a heavily reduced decode."""
    with Image.open(path) as source:
        width, height = source.size
        if source.getexif().get(274, 1) in (5, 6, 7, 8):
            width, height = height, width
        try:
            source.draft('RGB', (96, 96))
        except (AttributeError, OSError):
            pass
        small = source.convert('RGB')
    small.thumbnail((48, 48))
    bins = [[0., 0., 0., 0., 0.] for _ in range(24)]
    chroma = lightness = 0.
    pixels = list(zip(small.convert('HSV').getdata(), small.getdata()))
    for (hue, saturation, value), (red, green, blue) in pixels:
        lightness += value
        if saturation < 40 or value < 28:
            continue
        # Vivid and bright pixels weigh more than the near-grey majority.
        weight = (saturation/255)*(value/255)
        chroma += weight
        slot = bins[hue*24//256]
        slot[0] += weight
        slot[1] += hue*weight
        slot[2] += red*weight
        slot[3] += green*weight
        slot[4] += blue*weight
    lightness /= max(1, len(pixels))*255
    if chroma/max(1, len(pixels)) < .05:
        grey = int(lightness*255)
        return {'aspect': width/max(1, height), 'neutral': True, 'hue': 0, 'light': lightness, 'rgb': [grey]*3}
    best = max(bins, key=lambda slot: slot[0])
    return {'aspect': width/max(1, height), 'neutral': False, 'hue': best[1]/best[0]*360/256, 'light': lightness,
            'rgb': [int(best[2]/best[0]), int(best[3]/best[0]), int(best[4]/best[0])]}


def color_key(info):
    """Rainbow order starting just before the reds; neutrals close the sequence."""
    if not info:
        return (2, 0, 0)
    if info.get('neutral'):
        return (1, -info['light'], 0)
    return (0, (info['hue']+25) % 360, -info['light'])


class AnalysisSignals(QObject):
    batch = Signal(dict, int, int, int)


class AnalysisJob(QRunnable):
    def __init__(self, photos, token):
        super().__init__()
        self.photos, self.token = photos, token
        self.signals = AnalysisSignals()
        self.cancelled = threading.Event()

    def run(self):
        batch = {}
        total = len(self.photos)
        for done, (key, path, signature) in enumerate(self.photos, 1):
            if self.cancelled.is_set():
                return
            try:
                batch[key] = {**analyse_photo(path), 'signature': signature}
            except Exception:
                batch[key] = {'aspect': 1.5, 'neutral': True, 'hue': 0, 'light': 0, 'rgb': [40, 40, 40], 'signature': signature}
            if len(batch) >= 16 or done == total:
                self.signals.batch.emit(batch, done, total, self.token)
                batch = {}


class RatingBar(QWidget):
    """Floating stars and marker. Fast access to notes, in every view."""
    rated = Signal(int)
    marked = Signal(str)

    def __init__(self, window, parent, show_name=True):
        super().__init__(parent)
        self.window = window
        self.show_name = show_name
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.relayout()

    def relayout(self):
        self.name_width = 210 if self.show_name else 0
        marks = 2 if self.window.marker == 'pick' else 1
        self.setFixedSize(self.name_width+18+5*23+18+marks*30+8, 46)

    def stars_x(self):
        return self.name_width+18+10

    def mark_rects(self):
        x = self.stars_x()+5*23+6
        return [QRectF(x+i*30, 7, 30, 32) for i in range(2 if self.window.marker == 'pick' else 1)]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 22)))
        painter.setBrush(QColor(22, 22, 23, 232))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 11, 11)
        photos = self.window.target_photos()
        photo = photos[0] if photos else None
        if self.show_name:
            text = f'{len(photos)} photos' if len(photos) > 1 else (photo.path.name if photo else '')
            painter.setPen(QColor(MUTED))
            painter.setFont(QFont(self.font().family(), 9))
            text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, self.name_width-16)
            painter.drawText(QRectF(16, 0, self.name_width-16, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
        draw_stars(painter, self.stars_x(), self.height()/2, photo.stars if photo else 0, 8.0, 23.0)
        rects = self.mark_rects()
        icon = lambda r: r.adjusted(7, 8, -7, -8)
        if self.window.marker == 'pick':
            draw_check(painter, icon(rects[0]), SELECT if photo and photo.pick == PICK_SELECT else FAINT)
            draw_cross(painter, icon(rects[1]), REJECT if photo and photo.pick == PICK_REJECT else FAINT)
        else:
            draw_flag(painter, icon(rects[0]), ACCENT if photo and photo.flagged else FAINT)

    def mouseReleaseEvent(self, event):
        photos = self.window.target_photos()
        if not photos:
            return
        x, y = event.position().x(), event.position().y()
        start = self.stars_x()-11.5
        if start <= x < start+5*23:
            value = int((x-start)//23)+1
            # A second click on the same star clears the note, as in Lightroom.
            self.rated.emit(0 if photos[0].stars == value else value)
            return
        rects = self.mark_rects()
        if rects[0].contains(event.position()):
            self.marked.emit(PICK_NEUTRAL if photos[0].pick == PICK_SELECT else PICK_SELECT)
        elif len(rects) > 1 and rects[1].contains(event.position()):
            self.marked.emit(PICK_NEUTRAL if photos[0].pick == PICK_REJECT else PICK_REJECT)


class Swatch(QAbstractButton):
    def __init__(self, color, label, parent=None):
        super().__init__(parent)
        self.color = color
        self.setToolTip(label)
        self.setCheckable(True)
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(TEXT if self.isChecked() else '#5a5a5d'), 1.6 if self.isChecked() else 1))
        painter.setBrush(QColor(self.color) if self.color else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(2, 2, 16, 16))
        if not self.color:
            # The free choice shows the plus sign until a custom color is active.
            painter.setPen(QPen(QColor(MUTED), 1.3))
            painter.drawLine(QPointF(10, 6.5), QPointF(10, 13.5))
            painter.drawLine(QPointF(6.5, 10), QPointF(13.5, 10))


class BackgroundPicker(QFrame):
    """Preset greys and any other color, shared by the Tri and Revoir pages."""

    def __init__(self, window, parent=None, floating=False):
        super().__init__(parent)
        self.window = window
        self.custom_tip = 'Autre couleur…'
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0) if floating else row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        if floating:
            self.setObjectName('pill')
            self.setFixedHeight(46)
        self.swatches = []
        for label, color in BACKGROUNDS:
            swatch = Swatch(color, label)
            swatch.clicked.connect(lambda checked=False, value=color: self.window.set_background(value))
            self.swatches.append(swatch)
            row.addWidget(swatch)
        self.custom = Swatch('', self.custom_tip)
        self.custom.clicked.connect(self.pick_custom)
        row.addWidget(self.custom)
        self.sync()

    def sync(self):
        background = self.window.viewer.background
        presets = [swatch.color for swatch in self.swatches]
        for swatch in self.swatches:
            swatch.setChecked(swatch.color == background)
        self.custom.color = '' if background in presets else background
        self.custom.setChecked(background not in presets)
        self.custom.update()

    def pick_custom(self):
        color = QColorDialog.getColor(QColor(self.window.viewer.background), self.window, 'Couleur du fond')
        if color.isValid():
            self.window.set_background(color.name())
        self.sync()


class Surface(QWidget):
    """Shared drawing and selection for one review mode."""
    fixed_deck = False

    def __init__(self, review):
        super().__init__(review)
        self.review = review
        self.window_ = review.window
        self.setMouseTracking(True)
        self.request_timer = QTimer(self)
        self.request_timer.setSingleShot(True)
        self.request_timer.timeout.connect(self.request_images)

    def rebuild(self):
        self.update()

    def photos_changed(self):
        self.rebuild()

    def request_images(self):
        pass

    def navigate(self, delta):
        pass

    def shuffle(self):
        pass

    def zoom(self, factor):
        pass

    def targets(self):
        return self.window_.selected_photos()

    def current(self):
        return self.window_.current_photo()

    def schedule_requests(self):
        if not self.request_timer.isActive():
            self.request_timer.start(50)

    def paint_background(self, painter):
        painter.fillRect(self.rect(), QColor(self.window_.viewer.background))

    def hint_color(self):
        return QColor(contrast_on(self.window_.viewer.background))

    def paint_photo(self, painter, rect, photo, align=0):
        """Draw a photo contained in rect; a color block holds its place while loading.

        align pushes it against the right (1) or left (-1) side of rect.
        """
        image = self.window_.best_image(photo)
        if image is None:
            info = self.review.info.get(photo.relative_path)
            painter.fillRect(rect, QColor(*info['rgb']).darker(130) if info else QColor(128, 128, 128, 50))
            return
        scale = min(rect.width()/image.width(), rect.height()/image.height())
        width, height = image.width()*scale, image.height()*scale
        x = {0: rect.center().x()-width/2, 1: rect.right()-width, -1: rect.left()}[align]
        target = QRectF(x, rect.center().y()-height/2, width, height)
        image = self.review.transformed(image)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.review.mirror:
            painter.translate(target.center().x()*2, 0)
            painter.scale(-1, 1)
        painter.drawImage(target, image)
        painter.restore()
        if photo.pick == PICK_REJECT:
            painter.fillRect(target, QColor(0, 0, 0, 120))

    def paint_badges(self, painter, rect, photo):
        """Stars and marker on a small dark plate, readable on any photo or background."""
        if not photo.stars and photo.pick == PICK_NEUTRAL:
            return
        width = (5*11+8 if photo.stars else 0)+(17 if photo.pick != PICK_NEUTRAL else 0)
        plate = QRectF(rect.left()+6, rect.bottom()-25, width+4, 19)
        if rect.width() < width+14 or rect.height() < 34:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 15, 16, 190))
        painter.drawRoundedRect(plate, 5, 5)
        x = plate.left()+9
        if photo.stars:
            draw_stars(painter, x, plate.center().y(), photo.stars, 4.5, 11.0, '#6a6a6d')
            x += 5*11
        icon = QRectF(x-3, plate.top()+5, 9, 9)
        if photo.pick == PICK_SELECT:
            (draw_check if self.window_.marker == 'pick' else draw_flag)(painter, icon, SELECT if self.window_.marker == 'pick' else ACCENT)
        elif photo.pick == PICK_REJECT:
            draw_cross(painter, icon, REJECT)
        painter.restore()

    def paint_empty(self, painter, text):
        painter.setPen(self.hint_color())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def contextMenuEvent(self, event):
        self.review.background_menu().exec(event.globalPos())


class WallView(Surface):
    """Justified rows. Couleur uses the same wall, sorted by dominant color."""

    def __init__(self, review, by_color=False):
        super().__init__(review)
        self.by_color = by_color
        self.row_height = 210
        self.tiles = []
        self.content_height = 0
        self.ordered = []
        self.bar = QScrollBar(Qt.Orientation.Vertical, self)
        self.bar.valueChanged.connect(self.on_scroll)

    def on_scroll(self, *args):
        self.update()
        self.schedule_requests()

    def rebuild(self):
        self.ordered = list(self.window_.photos)
        if self.by_color:
            self.ordered.sort(key=lambda p: color_key(self.review.info.get(p.relative_path)))
        self.relayout()

    def relayout(self):
        # Anchor on the first visible photo, so that a late aspect ratio or a
        # note that changes the filter never throws the reader elsewhere.
        anchor = self.first_visible()
        offset = self.tiles[anchor][0].top()-self.bar.value() if anchor is not None and anchor < len(self.tiles) else 0
        anchor_key = self.tiles[anchor][1].relative_path if anchor is not None and anchor < len(self.tiles) else None
        margin, gap = 18, 6
        width = max(200, self.width()-2*margin-10)
        self.tiles = []
        row, row_ratio, y = [], 0., float(TOP_CLEARANCE)

        def flush(last):
            nonlocal row, row_ratio, y
            height = min(self.row_height*1.15, (width-gap*(len(row)-1))/row_ratio) if not last else min(self.row_height, (width-gap*(len(row)-1))/row_ratio)
            x = float(margin)
            for photo, ratio in row:
                self.tiles.append((QRectF(x, y, ratio*height, height), photo))
                x += ratio*height+gap
            y += height+gap
            row, row_ratio = [], 0.

        for photo in self.ordered:
            ratio = self.review.aspect(photo)
            row.append((photo, ratio))
            row_ratio += ratio
            if row_ratio*self.row_height >= width:
                flush(False)
        if row:
            flush(True)
        self.content_height = y+BOTTOM_CLEARANCE
        self.bar.setPageStep(self.height())
        self.bar.setSingleStep(90)
        self.bar.setRange(0, max(0, int(self.content_height-self.height())))
        if anchor_key is not None:
            for rect, photo in self.tiles:
                if photo.relative_path == anchor_key:
                    self.bar.setValue(int(rect.top()-offset))
                    break
        self.update()
        self.schedule_requests()

    def visible_tiles(self):
        top, bottom = self.bar.value(), self.bar.value()+self.height()
        return [(rect, photo) for rect, photo in self.tiles if rect.bottom() >= top and rect.top() <= bottom]

    def first_visible(self):
        top = self.bar.value()
        return next((i for i, (rect, _) in enumerate(self.tiles) if rect.bottom() >= top), None) if self.tiles else None

    def request_images(self):
        ratio = self.devicePixelRatioF()
        for rect, photo in self.visible_tiles():
            self.window_.request_for(photo, max(rect.width(), rect.height())*ratio, 5)

    def resizeEvent(self, event):
        self.bar.setGeometry(self.width()-10, TOP_CLEARANCE, 9, self.height()-TOP_CLEARANCE-8)
        self.relayout()

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_background(painter)
        if not self.tiles:
            self.paint_empty(painter, 'Aucune photo pour ces filtres')
            return
        painter.translate(0, -self.bar.value())
        current = self.window_.current_photo()
        selected = {p.relative_path for p in self.window_.selected_photos()}
        for rect, photo in self.visible_tiles():
            self.paint_photo(painter, rect, photo)
            if self.by_color:
                info = self.review.info.get(photo.relative_path)
                if info:
                    painter.fillRect(QRectF(rect.left(), rect.bottom()-4, rect.width(), 4), QColor(*info['rgb']))
            self.paint_badges(painter, rect, photo)
            if photo.relative_path in selected:
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(QColor(ACCENT), 2.5 if photo is current else 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(rect.adjusted(-1.5, -1.5, 1.5, 1.5))
                painter.restore()

    def tile_at(self, position):
        point = position+QPointF(0, self.bar.value())
        return next((photo for rect, photo in self.visible_tiles() if rect.contains(point)), None)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.12 if event.angleDelta().y() > 0 else 1/1.12)
        else:
            self.bar.setValue(self.bar.value()-int(event.angleDelta().y()*.9))
        event.accept()

    def zoom(self, factor):
        self.row_height = max(90, min(560, self.row_height*factor))
        self.relayout()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            photo = self.tile_at(event.position())
            if photo:
                self.window_.select_photo(photo, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
                self.update()

    def mouseDoubleClickEvent(self, event):
        photo = self.tile_at(event.position())
        if photo:
            self.window_.open_in_tri(photo)

    def navigate(self, delta):
        if not self.tiles:
            return
        current = self.window_.current_photo()
        index = next((i for i, (_, p) in enumerate(self.tiles) if p is current), 0)
        rect, photo = self.tiles[max(0, min(len(self.tiles)-1, index+delta))]
        self.window_.select_photo(photo)
        top = self.bar.value()
        if rect.top()-TOP_CLEARANCE < top:
            self.bar.setValue(int(rect.top()-TOP_CLEARANCE))
        elif rect.bottom()+BOTTOM_CLEARANCE > top+self.height():
            self.bar.setValue(int(rect.bottom()+BOTTOM_CLEARANCE-self.height()))
        self.update()


class ToileView(Surface):
    """Prints scattered on a table. Better-rated photos are laid out larger."""

    def __init__(self, review):
        super().__init__(review)
        self.positions = {}
        self.stack = []
        self.scale_ = .6
        self.pan = QPointF()
        self.drag = None
        self.seed = random.randrange(1 << 30)
        self.fresh = True

    def rebuild(self):
        photos = self.window_.photos
        keys = {p.relative_path for p in photos}
        self.stack = [p for p in self.stack if p.relative_path in keys]
        known = {p.relative_path for p in self.stack}
        new = [p for p in photos if p.relative_path not in known]
        if new:
            self.place(new)
        self.update()
        self.schedule_requests()

    def place(self, photos):
        rng = random.Random(self.seed)
        total = len(self.stack)+len(photos)
        columns = max(2, math.ceil(math.sqrt(total*1.5)))
        cell_w, cell_h = 380, 300
        taken = len(self.positions)
        cells = list(range(max(total, taken+len(photos))))
        rng.shuffle(cells)
        free = [c for c in cells if c >= taken] or cells
        for index, photo in enumerate(photos):
            cell = free[index % len(free)]
            x = (cell % columns-columns/2+.5)*cell_w+rng.uniform(-95, 95)
            y = (cell//columns-(total//columns+1)/2+.5)*cell_h+rng.uniform(-80, 80)
            self.positions.setdefault(photo.relative_path, [x, y, rng.uniform(-3.5, 3.5)])
            self.stack.append(photo)
        rng.shuffle(self.stack)
        if self.fresh and self.width() > 10:
            self.fit()

    def fit(self):
        if not self.positions:
            return
        xs = [p[0] for p in self.positions.values()]
        ys = [p[1] for p in self.positions.values()]
        span_w, span_h = max(xs)-min(xs)+460, max(ys)-min(ys)+420
        # Very large sets start readable rather than fully visible: panning does the rest.
        self.scale_ = max(.3, min(1., min(self.width()/span_w, (self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE)/span_h)))
        self.pan = QPointF(-(max(xs)+min(xs))/2*self.scale_, -(max(ys)+min(ys))/2*self.scale_)
        self.fresh = False

    def shuffle(self):
        self.seed = random.randrange(1 << 30)
        self.positions.clear()
        self.stack.clear()
        self.fresh = True
        self.rebuild()

    def resizeEvent(self, event):
        if self.fresh and self.stack:
            self.fit()
        self.schedule_requests()

    def tile_rect(self, photo):
        """World rectangle, centered on the stored position; size follows the note."""
        x, y, _ = self.positions[photo.relative_path]
        side = 250+photo.stars*30
        ratio = self.review.aspect(photo)
        width, height = (side, side/ratio) if ratio >= 1 else (side*ratio, side)
        return QRectF(x-width/2, y-height/2, width, height)

    def to_world(self, point):
        center = QPointF(self.width()/2, self.height()/2)+self.pan
        return (point-center)/self.scale_

    def visible(self):
        area = QRectF(self.to_world(QPointF(0, 0)), self.to_world(QPointF(self.width(), self.height()))).adjusted(-60, -60, 60, 60)
        return [p for p in self.stack if self.tile_rect(p).intersects(area)]

    def request_images(self):
        ratio = self.devicePixelRatioF()
        for photo in self.visible():
            rect = self.tile_rect(photo)
            self.window_.request_for(photo, max(rect.width(), rect.height())*self.scale_*ratio, 5)

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_background(painter)
        if not self.stack:
            self.paint_empty(painter, 'Aucune photo pour ces filtres')
            return
        painter.translate(QPointF(self.width()/2, self.height()/2)+self.pan)
        painter.scale(self.scale_, self.scale_)
        selected = {p.relative_path for p in self.window_.selected_photos()}
        for photo in self.visible():
            rect = self.tile_rect(photo)
            painter.save()
            painter.translate(rect.center())
            painter.rotate(self.positions[photo.relative_path][2])
            local = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())
            for grow, alpha in ((7, 14), (4, 22), (2, 34)):
                painter.fillRect(local.adjusted(-grow+2, -grow+4, grow+2, grow+5), QColor(0, 0, 0, alpha))
            self.paint_photo(painter, local, photo)
            self.paint_badges(painter, local, photo)
            if photo.relative_path in selected:
                painter.setPen(QPen(QColor(ACCENT), 2.4/self.scale_))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(local.adjusted(-2, -2, 2, 2))
            painter.restore()

    def tile_at(self, position):
        world = self.to_world(position)
        for photo in reversed(self.stack):
            if self.tile_rect(photo).contains(world):
                return photo
        return None

    def bring_front(self, photo):
        self.stack = [p for p in self.stack if p is not photo]+[photo]

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        photo = self.tile_at(event.position())
        self.drag = (photo, event.position())
        if photo:
            self.bring_front(photo)
            self.window_.select_photo(photo, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseMoveEvent(self, event):
        if self.drag is None:
            return
        photo, last = self.drag
        delta = event.position()-last
        self.drag = (photo, event.position())
        if photo:
            position = self.positions[photo.relative_path]
            position[0] += delta.x()/self.scale_
            position[1] += delta.y()/self.scale_
        else:
            self.pan += delta
            self.schedule_requests()
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        photo = self.tile_at(event.position())
        if photo:
            self.window_.open_in_tri(photo)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1/1.15
        self.zoom(factor, event.position())
        event.accept()

    def zoom(self, factor, anchor=None):
        anchor = anchor if anchor is not None else QPointF(self.width()/2, self.height()/2)
        world = self.to_world(anchor)
        self.scale_ = max(.06, min(4., self.scale_*factor))
        self.pan = anchor-QPointF(self.width()/2, self.height()/2)-world*self.scale_
        self.update()
        self.schedule_requests()

    def navigate(self, delta):
        photos = self.window_.photos
        if not photos:
            return
        current = self.window_.current_photo()
        index = next((i for i, p in enumerate(photos) if p is current), 0)
        photo = photos[max(0, min(len(photos)-1, index+delta))]
        self.bring_front(photo)
        self.window_.select_photo(photo)
        rect = self.tile_rect(photo)
        area = QRectF(self.to_world(QPointF(0, TOP_CLEARANCE)), self.to_world(QPointF(self.width(), self.height()-BOTTOM_CLEARANCE)))
        if not area.contains(rect):
            self.pan = -rect.center()*self.scale_
        self.update()
        self.schedule_requests()


class SwipeView(Surface):
    """One decision at a time, by dragging the photo or with the navigation keys."""
    fixed_deck = True

    def __init__(self, review):
        super().__init__(review)
        self.deck = []
        self.index = 0
        self.offset = QPointF()
        self.press = None
        self.flying = 0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(170)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.on_animation)
        self.animation.finished.connect(self.on_landed)

    def rebuild(self):
        self.finish_animation()
        self.deck = list(self.window_.photos)
        self.index = 0
        self.offset = QPointF()
        self.settle()

    def photos_changed(self):
        # The deck is fixed: a decision must not reshuffle the cards still to come.
        self.update()

    def shuffle(self):
        self.finish_animation()
        random.shuffle(self.deck)
        self.index = 0
        self.settle()

    def current(self):
        return self.deck[self.index] if 0 <= self.index < len(self.deck) else None

    def targets(self):
        photo = self.current()
        return [photo] if photo else []

    def settle(self):
        photo = self.current()
        if photo:
            self.window_.select_photo(photo)
        self.update()
        self.request_images()
        self.review.bar.update()

    def request_images(self):
        ratio = self.devicePixelRatioF()
        photo = self.current()
        if photo:
            self.window_.request_for(photo, max(self.card_area().width(), self.card_area().height())*ratio, 15)
        for following in self.deck[self.index+1:self.index+4]:
            self.window_.request_for(following, 1000, 4)

    def card_area(self):
        return QRectF(40, TOP_CLEARANCE, max(1, self.width()-80), max(1, self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE))

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_background(painter)
        if not self.deck:
            self.paint_empty(painter, 'Aucune photo pour ces filtres')
            return
        photo = self.current()
        if photo is None:
            kept = sum(p.pick == PICK_SELECT for p in self.deck)
            dropped = sum(p.pick == PICK_REJECT for p in self.deck)
            self.paint_empty(painter, f'Toutes les photos sont passées : {kept} retenues, {dropped} rejetées.\n'
                                      'Retour arrière pour revenir, R pour mélanger et recommencer.')
            return
        area = self.card_area()
        following = self.deck[self.index+1] if self.index+1 < len(self.deck) else None
        travel = min(1., abs(self.offset.x())/max(1, self.width()*.35))
        if following:
            painter.save()
            painter.setOpacity(.35+.65*travel)
            painter.translate(area.center())
            grow = .95+.05*travel
            painter.scale(grow, grow)
            self.paint_photo(painter, QRectF(-area.width()/2, -area.height()/2, area.width(), area.height()), following)
            painter.restore()
        painter.save()
        painter.translate(area.center()+self.offset)
        painter.rotate(self.offset.x()/max(1, self.width())*11)
        local = QRectF(-area.width()/2, -area.height()/2, area.width(), area.height())
        self.paint_photo(painter, local, photo)
        label = self.review.swipe_labels()[1 if self.offset.x() > 0 else 0]
        if travel > .06:
            color = QColor(SELECT if self.offset.x() > 0 else REJECT)
            painter.setOpacity(min(1., travel*1.6))
            painter.setFont(QFont(self.font().family(), 15, QFont.Weight.DemiBold))
            box = QRectF(-110, local.top()+22, 220, 44)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 15, 16, 215))
            painter.drawRoundedRect(box, 9, 9)
            painter.setPen(color)
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()
        painter.setPen(self.hint_color())
        painter.setFont(QFont(self.font().family(), 9))
        painter.drawText(QRectF(18, self.height()-44, 120, 24), Qt.AlignmentFlag.AlignVCenter, f'{self.index+1} / {len(self.deck)}')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.current():
            self.finish_animation()
            self.press = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.press is not None:
            self.offset = QPointF(event.position().x()-self.press.x(), (event.position().y()-self.press.y())*.25)
            self.update()

    def mouseReleaseEvent(self, event):
        if self.press is None:
            return
        self.press = None
        self.unsetCursor()
        if abs(self.offset.x()) > self.width()*.16:
            self.decide(1 if self.offset.x() > 0 else -1)
        else:
            self.animate_to(QPointF(), 0)

    def mouseDoubleClickEvent(self, event):
        if self.current():
            self.window_.open_in_tri(self.current())

    def animate_to(self, destination, flying):
        self.flying = flying
        self.animation.stop()
        self.start_offset, self.end_offset = QPointF(self.offset), destination
        self.animation.setStartValue(0.)
        self.animation.setEndValue(1.)
        self.animation.start()

    def on_animation(self, value):
        self.offset = self.start_offset+(self.end_offset-self.start_offset)*value
        self.update()

    def on_landed(self):
        if self.flying:
            self.flying = 0
            self.index += 1
            self.offset = QPointF()
            self.settle()

    def finish_animation(self):
        if self.animation.state() == QVariantAnimation.State.Running:
            self.animation.stop()
            self.on_landed()
            self.offset = QPointF()

    def decide(self, direction):
        self.finish_animation()
        photo = self.current()
        if photo:
            self.review.apply_swipe(photo, direction)
            self.fly(direction)

    def fly(self, direction):
        self.animate_to(QPointF(direction*self.width()*1.15, self.offset.y()), direction)

    def navigate(self, delta):
        self.decide(1 if delta > 0 else -1)

    def skip(self, delta=1):
        self.finish_animation()
        self.index = max(0, min(len(self.deck), self.index+delta))
        self.offset = QPointF()
        self.settle()

    def show_photo(self, photo):
        self.finish_animation()
        for index, candidate in enumerate(self.deck):
            if candidate is photo:
                self.index = index
                break
        self.offset = QPointF()
        self.settle()


class DuelView(Surface):
    """Two photos at once: the preferred one stays and meets the next."""
    fixed_deck = True

    def __init__(self, review):
        super().__init__(review)
        self.deck = []
        self.champion = 0
        self.challenger = 1
        self.history = []
        self.hover = 0

    def rebuild(self):
        self.deck = list(self.window_.photos)
        self.restart()

    def photos_changed(self):
        self.update()

    def restart(self):
        self.champion, self.challenger, self.history = 0, 1, []
        self.hover = 0
        self.update()
        self.request_images()

    def shuffle(self):
        random.shuffle(self.deck)
        self.restart()

    def finished(self):
        return self.challenger >= len(self.deck)

    def current(self):
        if not self.deck:
            return None
        return self.deck[self.challenger if self.hover == 1 and not self.finished() else self.champion]

    def targets(self):
        photo = self.current()
        return [photo] if photo else []

    def panels(self):
        area = QRectF(16, TOP_CLEARANCE, max(1, self.width()-32), max(1, self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE))
        if self.finished():
            return [area]
        half = (area.width()-14)/2
        return [QRectF(area.left(), area.top(), half, area.height()), QRectF(area.left()+half+14, area.top(), half, area.height())]

    def shown(self):
        return [self.deck[self.champion]] if self.finished() else [self.deck[self.champion], self.deck[self.challenger]]

    def request_images(self):
        if not self.deck:
            return
        ratio = self.devicePixelRatioF()
        for photo, panel in zip(self.shown(), self.panels()):
            self.window_.request_for(photo, max(panel.width(), panel.height())*ratio, 15)
        for following in self.deck[self.challenger+1:self.challenger+3]:
            self.window_.request_for(following, 1000, 4)

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_background(painter)
        if len(self.deck) < 2:
            self.paint_empty(painter, 'Il faut au moins deux photos pour ces filtres')
            return
        panels = self.panels()
        # Side by side and touching the gap: the eye compares without travelling.
        for photo, panel, align in zip(self.shown(), panels, (0, -1) if self.finished() else (1, -1)):
            self.paint_photo(painter, panel, photo, align)
        painter.setPen(self.hint_color())
        painter.setFont(QFont(self.font().family(), 9))
        if self.finished():
            text = 'Photo gagnante. R pour relancer un duel.'
        else:
            text = f'Duel {self.challenger} sur {len(self.deck)-1} · cliquez sur la photo à garder'
        painter.drawText(QRectF(18, self.height()-44, max(0, self.width()/2-self.review.bar.width()/2-30), 24), Qt.AlignmentFlag.AlignVCenter, text)

    def side_at(self, position):
        for side, panel in enumerate(self.panels()):
            if panel.contains(position):
                return side
        return None

    def mouseMoveEvent(self, event):
        side = self.side_at(event.position())
        if side is not None and side != self.hover:
            self.hover = side
            self.window_.select_photo(self.current())
            self.review.bar.update()

    def choose(self, side):
        if len(self.deck) < 2 or self.finished():
            return
        self.history.append((self.champion, self.challenger))
        if side == 1:
            self.champion = self.challenger
        self.challenger += 1
        self.hover = 0
        self.window_.select_photo(self.current())
        self.review.bar.update()
        self.update()
        self.request_images()

    def mouseReleaseEvent(self, event):
        side = self.side_at(event.position())
        if event.button() == Qt.MouseButton.LeftButton and side is not None:
            self.choose(side)

    def navigate(self, delta):
        self.choose(1 if delta > 0 else 0)

    def skip(self, delta=1):
        if delta < 0 and self.history:
            self.champion, self.challenger = self.history.pop()
            self.update()
            self.request_images()
        elif delta > 0 and not self.finished():
            self.challenger += 1
            self.update()
            self.request_images()


class ReviewPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName('page')
        self.window = window
        self.settings = window.settings
        self.mirror = False
        self.mono = False
        self.mono_cache = {}
        self.info = {}
        self.analysis = None
        self.analysis_token = 0
        self.analysis_pool = QThreadPool(self)
        self.analysis_pool.setMaxThreadCount(1)
        self.stack = QStackedWidget(self)
        self.views = {'toile': ToileView(self), 'swipe': SwipeView(self), 'color': WallView(self, True),
                      'wall': WallView(self), 'duel': DuelView(self)}
        for view in self.views.values():
            self.stack.addWidget(view)
        saved = self.settings.value('review_mode', 'toile')
        self.mode = saved if saved in self.views else 'toile'
        self.stack.setCurrentWidget(self.views[self.mode])
        self.bar = RatingBar(window, self)
        self.bar.rated.connect(window.rate)
        self.bar.marked.connect(window.set_pick)
        self.progress = QLabel(self, objectName='faint')
        self.progress.hide()
        self.build_toolbar()
        self.relayout_timer = QTimer(self)
        self.relayout_timer.setSingleShot(True)
        self.relayout_timer.timeout.connect(self.apply_analysis)
        self.repaint_timer = QTimer(self)
        self.repaint_timer.setSingleShot(True)
        self.repaint_timer.timeout.connect(self.update_view)

    def build_toolbar(self):
        self.pill = QFrame(self, objectName='pill')
        row = QHBoxLayout(self.pill)
        row.setContentsMargins(8, 6, 12, 6)
        row.setSpacing(4)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for key, label in MODES:
            button = QPushButton(label, objectName='seg', checkable=True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setChecked(key == self.mode)
            button.clicked.connect(lambda checked=False, value=key: self.set_mode(value))
            self.mode_group.addButton(button)
            self.mode_buttons[key] = button
            row.addWidget(button)
        row.addSpacing(10)
        self.swipe_rule = QComboBox()
        for key, label in SWIPE_RULES.items():
            self.swipe_rule.addItem(label, key)
        self.apply_default_swipe_rule()
        self.swipe_rule.currentIndexChanged.connect(lambda *_: self.settings.setValue('swipe_rule', self.swipe_rule.currentData()))
        self.swipe_rule.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.swipe_rule)
        self.shuffle_button = QPushButton('Mélanger', objectName='ghost')
        self.shuffle_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.shuffle_button.clicked.connect(self.shuffle)
        row.addWidget(self.shuffle_button)
        self.mirror_button = QPushButton('Miroir', objectName='seg', checkable=True)
        self.mono_button = QPushButton('N&&B', objectName='seg', checkable=True)
        for button, slot in ((self.mirror_button, self.toggle_mirror), (self.mono_button, self.toggle_mono)):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addSpacing(8)
        self.picker = BackgroundPicker(self.window)
        row.addWidget(self.picker)
        self.sync_toolbar()

    def apply_default_swipe_rule(self):
        # Without an explicit choice, the rule follows the marking system.
        default = 'pick' if self.window.marker == 'pick' else 'stars'
        self.swipe_rule.blockSignals(True)
        self.swipe_rule.setCurrentIndex(max(0, self.swipe_rule.findData(self.settings.value('swipe_rule', default))))
        self.swipe_rule.blockSignals(False)

    def sync_toolbar(self):
        self.swipe_rule.setVisible(self.mode == 'swipe')
        self.shuffle_button.setVisible(self.mode in ('toile', 'swipe', 'duel'))
        self.picker.sync()
        self.pill.adjustSize()
        self.place_overlays()

    def background_menu(self):
        return self.window.viewer.background_menu(self)

    def place_overlays(self):
        self.pill.move(max(8, (self.width()-self.pill.width())//2), 12)
        self.bar.move((self.width()-self.bar.width())//2, self.height()-self.bar.height()-14)
        self.progress.adjustSize()
        self.progress.move(self.width()-self.progress.width()-22, self.height()-34)

    def resizeEvent(self, event):
        self.stack.setGeometry(self.rect())
        self.place_overlays()

    def view(self):
        return self.views[self.mode]

    def set_mode(self, mode):
        self.mode = mode
        self.settings.setValue('review_mode', mode)
        self.mode_buttons[mode].setChecked(True)
        self.stack.setCurrentWidget(self.views[mode])
        self.sync_toolbar()
        self.view().rebuild()
        self.bar.update()

    def rebuild(self):
        self.mono_cache.clear()
        self.view().rebuild()
        self.bar.relayout()
        self.place_overlays()
        self.bar.update()

    def photos_changed(self):
        self.view().photos_changed()
        self.bar.update()

    def image_ready(self):
        if not self.repaint_timer.isActive():
            self.repaint_timer.start(40)

    def update_view(self):
        self.view().update()
        self.view().schedule_requests()

    def targets(self):
        return self.view().targets()

    def current(self):
        return self.view().current()

    def navigate(self, delta):
        self.view().navigate(delta)
        self.bar.update()

    def shuffle(self):
        self.view().shuffle()
        self.bar.update()

    def zoom(self, factor):
        self.view().zoom(factor)

    def transformed(self, image):
        if not self.mono:
            return image
        key = image.cacheKey()
        grey = self.mono_cache.get(key)
        if grey is None:
            if len(self.mono_cache) > 160:
                self.mono_cache.clear()
            grey = image.convertToFormat(QImage.Format.Format_Grayscale8)
            self.mono_cache[key] = grey
        return grey

    def toggle_mirror(self):
        self.mirror = not self.mirror
        self.mirror_button.setChecked(self.mirror)
        self.update_view()

    def toggle_mono(self):
        self.mono = not self.mono
        self.mono_button.setChecked(self.mono)
        self.update_view()

    def swipe_labels(self):
        return {'pick': ('Rejetée', 'Retenue'), 'stars': ('-1 étoile', '+1 étoile'), 'five': ('0 étoile', '5 étoiles')}[self.swipe_rule.currentData()]

    def apply_swipe(self, photo, direction):
        rule = self.swipe_rule.currentData()
        if rule == 'pick':
            self.window.change([photo], pick=PICK_SELECT if direction > 0 else PICK_REJECT)
        elif rule == 'five':
            self.window.change([photo], stars=5 if direction > 0 else 0)
        else:
            self.window.change([photo], stars=max(0, min(5, photo.stars+direction)))

    def after_mark(self, pick):
        """A marker set from the keyboard is a decision in Swipe: move on."""
        if self.mode == 'swipe' and pick in (PICK_SELECT, PICK_REJECT):
            self.views['swipe'].fly(1 if pick == PICK_SELECT else -1)

    def after_undo(self, photos):
        view = self.view()
        if self.mode == 'swipe' and photos:
            view.show_photo(photos[0])

    def skip(self, delta):
        if self.mode in ('swipe', 'duel'):
            self.view().skip(delta)
            self.bar.update()

    # Analysis cache, kept next to the session so that it travels with the project.
    def analysis_path(self):
        return self.window.session.session_dir/ANALYSIS_FILE

    def aspect(self, photo):
        info = self.info.get(photo.relative_path)
        return max(.3, min(4., info['aspect'])) if info else 1.5

    def start_analysis(self):
        self.stop_analysis()
        session = self.window.session
        try:
            self.info = json.loads(self.analysis_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.info = {}
        self.analysis_token += 1
        self.analysis_started = False
        token = self.analysis_token

        def launch():
            if token != self.analysis_token or self.window.session is not session:
                return
            todo = []
            for photo in session.photos:
                try:
                    stat = photo.path.stat()
                except OSError:
                    continue
                signature = f'{stat.st_size}|{stat.st_mtime_ns}'
                if self.info.get(photo.relative_path, {}).get('signature') != signature:
                    todo.append((photo.relative_path, photo.path, signature))
            if not todo:
                return
            self.analysis = AnalysisJob(todo, token)
            self.analysis.signals.batch.connect(self.on_batch, Qt.ConnectionType.QueuedConnection)
            self.analysis_pool.start(self.analysis, -20)
        QTimer.singleShot(0, launch)

    def stop_analysis(self):
        self.analysis_token += 1
        if self.analysis:
            self.analysis.cancelled.set()
            self.analysis = None
        self.progress.hide()

    def on_batch(self, batch, done, total, token):
        if token != self.analysis_token or self.window.session is None:
            return
        self.info.update(batch)
        if done >= total:
            self.progress.hide()
            self.analysis = None
            self.save_analysis()
        else:
            self.progress.setText(f'Analyse des formats et des couleurs : {done} sur {total}')
            self.progress.show()
            self.place_overlays()
            if done % 160 == 0:
                self.save_analysis()
        if not self.relayout_timer.isActive():
            self.relayout_timer.start(700)

    def apply_analysis(self):
        if self.window.review_active and self.mode in ('color', 'wall', 'toile'):
            self.view().rebuild()

    def save_analysis(self):
        try:
            self.analysis_path().write_text(json.dumps(self.info), encoding='utf-8')
        except OSError:
            pass

    def reset(self):
        self.stop_analysis()
        self.info = {}
        self.mono_cache.clear()
        for view in self.views.values():
            if isinstance(view, ToileView):
                view.positions.clear()
                view.stack.clear()
                view.fresh = True
            elif hasattr(view, 'deck'):
                view.deck = []
            elif isinstance(view, WallView):
                view.ordered, view.tiles = [], []
