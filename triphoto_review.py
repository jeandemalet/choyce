"""The Revoir page: other ways of looking at the photos selected by the filters.

Every mode reads window.photos, so the header filters drive it, and rates through
the window, so notes, undo and session saving behave as on the Tri page.
"""
from __future__ import annotations

import json
import math
import random
import threading
import zlib
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QPointF, QRectF, QRunnable, QThreadPool, QTimer, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QColorDialog, QComboBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollBar, QSlider, QStackedWidget, QWidget)
from PIL import Image
from triphoto import PICK_NEUTRAL, PICK_REJECT, PICK_SELECT
from triphoto_theme import ACCENT, ASSETS, BACKGROUNDS, FAINT, MUTED, REJECT, SELECT, TEXT, contrast_on, draw_check, draw_cross, draw_flag, draw_stars

MODES = [('toile', 'Toile'), ('swipe', 'Swipe'), ('color', 'Couleur'), ('wall', 'Mosaïque')]
# What a swipe or a drag out of the mosaic does to a photo. Offered in both places.
ACTIONS = [('none', 'Ne rien changer'), ('select', 'Retenir'), ('reject', 'Rejeter'), ('neutral', 'Neutre'),
           ('minus', 'Retirer une étoile'), ('plus', 'Ajouter une étoile'), ('star5', '5 étoiles'), ('star4', '4 étoiles'),
           ('star3', '3 étoiles'), ('star2', '2 étoiles'), ('star1', '1 étoile'), ('star0', '0 étoile')]
STAGGERED_MODULES = json.loads((ASSETS/'staggered_modules.json').read_text(encoding='utf-8'))
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


def staggered_plan(keys, ratios, emphasis='equal'):
    """Choose a stable sequence of modules from the photographs' proportions.

    Same logic as the editorial galleries of the website: a rhythm of solos, pairs
    and diagonals, without a repeated shape. ratios are height / width.
    emphasis gives the favored orientation a solo module as often as possible.
    """
    def fingerprint(value):
        return zlib.crc32(value.encode())
    seed = fingerprint('|'.join(keys))
    plan, history = [], []
    index = since_solo = since_diagonal = balance = 0
    next_solo = 2+seed % 3
    while index < len(keys):
        ratio, remaining = ratios[index], len(keys)-index
        token = fingerprint(f'{keys[index]}:{seed}:{index}')
        favored = (emphasis == 'landscape' and ratio < .9) or (emphasis == 'portrait' and ratio >= 1)
        interval = next_solo+3 if emphasis != 'equal' else next_solo
        solo = (remaining == 1 or (index == 0 and (ratio < .9 or token % 4 == 0)) or since_solo >= interval
                or (ratio < .53 and since_solo > 0) or (favored and since_solo > 0))
        kind = ('wide' if ratio < .9 else 'solo') if solo else 'diagonal' if since_diagonal >= 6+token % 4 else 'pair'
        candidates = [m for m in STAGGERED_MODULES if m['kind'] == kind and len(m['frames']) <= remaining]
        candidates = candidates or [m for m in STAGGERED_MODULES if len(m['frames']) <= remaining]
        best, best_score = None, math.inf
        for module in candidates:
            frames = module['frames']
            score = sum(abs(math.log(ratios[index+n]/f[2])) for n, f in enumerate(frames))
            main = max(range(len(frames)), key=lambda n: frames[n][1])
            side = 0 if len(frames) == 1 else -1 if frames[main][0]+frames[main][1]/2 < .5 else 1
            shape = f"{module['kind']}:{module['mode']}:"+'|'.join(f'{round(f[0]*100)},{round(f[1]*100)}' for f in frames)
            if history and history[-1] == shape:
                score += 2.5
            if shape in history[-3:]:
                score += .8
            score += max(0, balance*side)*.3
            score += (fingerprint(f"{token}:{module['id']}") % 1000)/4000
            if score < best_score:
                best, best_score, best_shape, best_side = module, score, shape, side
        plan.append(best)
        history.append(best_shape)
        balance = max(-3, min(3, balance+best_side))
        index += len(best['frames'])
        since_solo = 0 if solo else since_solo+1
        since_diagonal = 0 if kind == 'diagonal' else since_diagonal+1
        if solo:
            next_solo = 2+token % 3
    return plan


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

    def paint_photo(self, painter, rect, photo):
        """Draw a photo contained in rect; a color block holds its place while loading."""
        image = self.window_.best_image(photo)
        if image is None:
            info = self.review.info.get(photo.relative_path)
            painter.fillRect(rect, QColor(*info['rgb']).darker(130) if info else QColor(128, 128, 128, 50))
            return
        scale = min(rect.width()/image.width(), rect.height()/image.height())
        width, height = image.width()*scale, image.height()*scale
        target = QRectF(rect.center().x()-width/2, rect.center().y()-height/2, width, height)
        image = self.review.transformed(image)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
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
    """Mosaic of photos. Couleur uses the same wall, sorted by dominant color."""

    def __init__(self, review, by_color=False):
        super().__init__(review)
        self.by_color = by_color
        settings = review.settings
        self.row_height = 210
        self.layout_kind = 'rows' if by_color else settings.value('wall_layout', 'staggered')
        self.width_percent = 100 if by_color else int(settings.value('wall_width', 62))
        self.emphasis = 'equal' if by_color else settings.value('wall_emphasis', 'equal')
        self.show_strip = settings.value('color_strip', False, type=bool)
        self.order_seed = None
        self.set_aside = set()
        self.tiles = []
        self.content_height = 0
        self.ordered = []
        self.press = None
        self.dragging = False
        self.drag_position = QPointF()
        self.bar = QScrollBar(Qt.Orientation.Vertical, self)
        self.bar.valueChanged.connect(self.on_scroll)

    def on_scroll(self, *args):
        self.update()
        self.schedule_requests()

    def rebuild(self):
        self.ordered = [p for p in self.window_.photos if p.relative_path not in self.set_aside]
        if self.by_color:
            self.ordered.sort(key=lambda p: color_key(self.review.info.get(p.relative_path)))
        elif self.order_seed is not None:
            # Hashed, so that removing one photo leaves the others where they are.
            self.ordered.sort(key=lambda p: zlib.crc32(f'{self.order_seed}|{p.relative_path}'.encode()))
        self.relayout()

    def shuffle(self):
        if not self.by_color:
            self.order_seed = random.randrange(1 << 30)
            self.bar.setValue(0)
            self.rebuild()

    def content_box(self):
        available = max(200, self.width()-2*18-10)
        width = available*self.width_percent/100
        return (self.width()-10-width)/2, width

    def relayout(self):
        # Anchor on the first visible photo, so that a late aspect ratio or a
        # note that changes the filter never throws the reader elsewhere.
        anchor = self.first_visible()
        offset = self.tiles[anchor][0].top()-self.bar.value() if anchor is not None and anchor < len(self.tiles) else 0
        anchor_key = self.tiles[anchor][1].relative_path if anchor is not None and anchor < len(self.tiles) else None
        left, width = self.content_box()
        self.tiles = []
        if self.layout_kind == 'staggered':
            bottom = self.layout_staggered(left, width)
        else:
            bottom = self.layout_rows(left, width)
        self.content_height = bottom+BOTTOM_CLEARANCE
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

    def layout_rows(self, left, width):
        gap = 6
        y = float(TOP_CLEARANCE)

        def place(row, target):
            nonlocal y
            total = sum(self.review.aspect(p) for p in row)
            height = min(target*1.15, (width-gap*(len(row)-1))/total)
            x = left
            for photo in row:
                tile = self.review.aspect(photo)*height
                self.tiles.append((QRectF(x, y, tile, height), photo))
                x += tile+gap
            y += height+gap

        def take(queue, target):
            row, total = [], 0.
            while queue and total*target < width:
                row.append(queue.pop(0))
                total += self.review.aspect(row[-1])
            return row

        if self.emphasis == 'equal':
            queues = [(list(self.ordered), self.row_height)]
        else:
            # Large rows of the favored orientation alternate with small rows of the
            # other: both stay uncropped, and the difference of size does the work.
            landscape = self.emphasis == 'landscape'
            favored = [p for p in self.ordered if (self.review.aspect(p) >= 1) == landscape]
            others = [p for p in self.ordered if (self.review.aspect(p) >= 1) != landscape]
            queues = [(favored, self.row_height*1.5), (others, self.row_height*.72)]
        while any(queue for queue, _ in queues):
            for queue, target in queues:
                if queue:
                    place(take(queue, target), target)
        return y

    def layout_staggered(self, left, width):
        if not self.ordered:
            return float(TOP_CLEARANCE)
        ratios = [1/self.review.aspect(p) for p in self.ordered]
        plan = staggered_plan([p.relative_path for p in self.ordered], ratios, self.emphasis)
        # On the website a tall photo may exceed the screen; to judge it here, it must not.
        tallest = max(240, self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE)
        cursor, index = float(TOP_CLEARANCE), 0
        for module in plan:
            cursor += module.get('before', 0)*width
            panels = []
            for n, frame in enumerate(module['frames']):
                frame_width, ratio = frame[1]*width, ratios[index+n]
                tile = min(frame_width, tallest/ratio)
                panels.append([left+frame[0]*width+(frame_width-tile)/2, tile, tile*ratio, 0.])
            if module['mode'] == 'sequence':
                offset = 0.
                order = [n for n in module.get('order', range(len(panels))) if n < len(panels)]
                for position, n in enumerate(order):
                    panels[n][3] = offset
                    offset += panels[n][2]+(module.get('gap', 0)*width if position < len(order)-1 else 0)
                row_height = offset
            else:
                row_height = max(panel[2] for panel in panels)
                if module['mode'] == 'center':
                    for panel in panels:
                        panel[3] = (row_height-panel[2])/2
            for n, panel in enumerate(panels):
                self.tiles.append((QRectF(panel[0], cursor+panel[3], panel[1], panel[2]), self.ordered[index+n]))
            cursor += row_height+module.get('after', 0)*width+(10 if not module.get('after') else 0)
            index += len(panels)
        return cursor

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
            if self.by_color and self.show_strip:
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
        if self.dragging:
            painter.resetTransform()
            self.paint_drag(painter)

    def drop_zones(self):
        left, width = self.content_box()
        side = max(96., left-6)
        return [QRectF(0, TOP_CLEARANCE, side, self.height()-TOP_CLEARANCE),
                QRectF(self.width()-side-10, TOP_CLEARANCE, side+10, self.height()-TOP_CLEARANCE),
                QRectF(0, self.height()-92, self.width(), 92)]

    def zone_at(self, position):
        return any(zone.contains(position) for zone in self.drop_zones())

    def paint_drag(self, painter):
        label = self.review.drop_label()
        hovered = self.zone_at(self.drag_position)
        painter.save()
        for zone in self.drop_zones():
            active = hovered and zone.contains(self.drag_position)
            painter.fillRect(zone, QColor(217, 184, 114, 225) if active else QColor(15, 15, 16, 190))
            painter.setPen(QColor('#131314' if active else TEXT))
            painter.drawText(zone.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, label)
        photo = self.press[0]
        image = self.window_.best_image(photo)
        if image is not None:
            ratio = self.review.aspect(photo)
            width, height = (220, 220/ratio) if ratio >= 1 else (220*ratio, 220)
            painter.setOpacity(.88)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(QRectF(self.drag_position.x()-width/2, self.drag_position.y()-height/2, width, height), self.review.transformed(image))
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
        if self.layout_kind == 'staggered':
            self.review.set_wall_width(self.width_percent*(1.06 if factor > 1 else 1/1.06))
        else:
            self.row_height = max(90, min(560, self.row_height*factor))
            self.relayout()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            photo = self.tile_at(event.position())
            if photo:
                extend = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self.window_.select_photo(photo, extend)
                self.press = (photo, event.position()) if not extend and not self.by_color else None
                self.update()

    def mouseMoveEvent(self, event):
        if self.press is None:
            return
        if not self.dragging and (event.position()-self.press[1]).manhattanLength() > 14:
            self.dragging = True
            self.review.set_bottom_visible(False)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self.dragging:
            self.drag_position = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        press, dragging = self.press, self.dragging
        self.press, self.dragging = None, False
        if dragging:
            self.unsetCursor()
            self.review.set_bottom_visible(True)
            self.update()
            if self.zone_at(event.position()):
                self.review.drop_out(press[0])

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
    """A handful of prints laid on a table, drawn from the filtered photos.

    Mélanger deals a new hand. Better-rated photos are laid out larger.
    """
    CELL_WIDTH, CELL_HEIGHT = 440, 390

    def __init__(self, review):
        super().__init__(review)
        self.positions = {}
        self.stack = []
        self.dealt = []
        self.available = set()
        self.limit = int(review.settings.value('toile_limit', 24))
        self.straight = review.settings.value('toile_straight', False, type=bool)
        self.scale_ = .6
        self.pan = QPointF()
        self.drag = None
        self.fresh = True

    def rebuild(self):
        keys = {p.relative_path for p in self.window_.photos}
        if keys != self.available:
            self.available = keys
            self.deal()
        self.update()
        self.schedule_requests()

    def photos_changed(self):
        # A note may take a photo out of the filter; the hand stays the same otherwise.
        keys = {p.relative_path for p in self.window_.photos}
        self.available = keys
        shown = {p.relative_path for p in self.stack}
        self.stack = [p for p in self.stack if p.relative_path in keys]+[
            p for p in self.dealt if p.relative_path in keys and p.relative_path not in shown]
        self.update()

    def deal(self):
        photos = self.window_.photos
        count = min(self.limit, len(photos)) if self.limit else len(photos)
        self.dealt = random.sample(photos, count)
        self.stack = list(self.dealt)
        self.positions = {}
        if count:
            ratio = max(.5, self.width()/max(1, self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE))
            columns = max(1, round(math.sqrt(count*ratio*self.CELL_HEIGHT/self.CELL_WIDTH)))
            rows = math.ceil(count/columns)
            cells = list(range(count))
            random.shuffle(cells)
            for photo, cell in zip(self.dealt, cells):
                x = (cell % columns-(columns-1)/2)*self.CELL_WIDTH+random.uniform(-55, 55)
                y = (cell//columns-(rows-1)/2)*self.CELL_HEIGHT+random.uniform(-45, 45)
                self.positions[photo.relative_path] = [x, y, random.uniform(-4, 4)]
            self.window_.select_photo(self.dealt[0])
        self.fresh = True
        self.fit()

    def fit(self):
        if not self.positions or self.width() < 10:
            return
        xs = [p[0] for p in self.positions.values()]
        ys = [p[1] for p in self.positions.values()]
        span_w, span_h = max(xs)-min(xs)+460, max(ys)-min(ys)+430
        area_h = self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE
        self.scale_ = max(.2, min(1.3, min(self.width()/span_w, area_h/span_h)))
        # Centered between the top and bottom bars rather than in the whole page.
        shift = (TOP_CLEARANCE-BOTTOM_CLEARANCE)/2
        self.pan = QPointF(-(max(xs)+min(xs))/2*self.scale_, shift-(max(ys)+min(ys))/2*self.scale_)
        self.fresh = False

    def shuffle(self):
        self.deal()
        self.update()
        self.schedule_requests()

    def set_straight(self, value):
        self.straight = value
        self.update()

    def set_limit(self, value):
        self.limit = value
        self.shuffle()

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
            if not self.straight:
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
        photos = [p for p in self.dealt if p in self.stack]
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
            self.paint_empty(painter, 'Toutes les photos sont passées.\nRetour arrière pour revenir, R pour mélanger et recommencer.')
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
            painter.setOpacity(min(1., travel*1.6))
            painter.setFont(QFont(self.font().family(), 15, QFont.Weight.DemiBold))
            box = QRectF(-110, local.top()+22, 220, 44)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(15, 15, 16, 215))
            painter.drawRoundedRect(box, 9, 9)
            painter.setPen(QColor(TEXT))
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


class NoteFilter(QFrame):
    """Quick choice of the notes shown, driving the same filter as the Tri page."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(1)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key, label in [('all', 'Toutes'), (5, '5 ★'), (4, '4 ★'), (3, '3 ★'), (2, '2 ★'), (1, '1 ★'), (0, '0 ★')]:
            button = QPushButton(label, objectName='seg', checkable=True)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(self.apply)
            self.group.addButton(button)
            self.buttons[key] = button
            row.addWidget(button)
        self.and_more = QPushButton('et +', objectName='seg', checkable=True)
        self.and_more.setToolTip('Inclure aussi les notes supérieures')
        self.and_more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.and_more.clicked.connect(self.apply)
        row.addWidget(self.and_more)
        self.sync()

    def apply(self, *args):
        key = next(key for key, button in self.buttons.items() if button.isChecked())
        if self.and_more.isChecked() and key in (1, 2, 3, 4):
            key = f'ge{key}'
        combo = self.window.stars
        combo.setCurrentIndex(combo.findData(key))

    def sync(self):
        data = self.window.stars.currentData()
        data = 'all' if data is None else data
        wider = isinstance(data, str) and data.startswith('ge')
        self.buttons[int(data[2:]) if wider else data].setChecked(True)
        if wider:
            self.and_more.setChecked(True)
        elif data in (1, 2, 3, 4):
            self.and_more.setChecked(False)


class ReviewPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName('page')
        self.window = window
        self.settings = window.settings
        self.mono = False
        self.mono_cache = {}
        self.info = {}
        self.analysis = None
        self.analysis_token = 0
        self.analysis_pool = QThreadPool(self)
        self.analysis_pool.setMaxThreadCount(1)
        self.bottom_visible = True
        self.stack = QStackedWidget(self)
        self.views = {'toile': ToileView(self), 'swipe': SwipeView(self), 'color': WallView(self, True), 'wall': WallView(self)}
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
        self.build_options()
        self.relayout_timer = QTimer(self)
        self.relayout_timer.setSingleShot(True)
        self.relayout_timer.timeout.connect(self.apply_analysis)
        self.repaint_timer = QTimer(self)
        self.repaint_timer.setSingleShot(True)
        self.repaint_timer.timeout.connect(self.update_view)
        self.sync_toolbar()

    def build_toolbar(self):
        """Top bar: what applies to every mode. Options of a mode sit at the bottom."""
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
        row.addSpacing(12)
        self.note_filter = NoteFilter(self.window)
        row.addWidget(self.note_filter)
        row.addSpacing(6)
        self.marker_filter = QComboBox()
        self.marker_filter.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.marker_filter.setFixedWidth(150)
        self.marker_filter.currentIndexChanged.connect(lambda index: self.window.flags.setCurrentIndex(index))
        row.addWidget(self.marker_filter)
        row.addSpacing(10)
        self.mono_button = QPushButton('N&&B', objectName='seg', checkable=True)
        self.mono_button.setToolTip('Noir et blanc, pour juger les contrastes')
        self.mono_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mono_button.clicked.connect(self.toggle_mono)
        row.addWidget(self.mono_button)
        row.addSpacing(8)
        self.picker = BackgroundPicker(self.window)
        row.addWidget(self.picker)

    def option_pill(self):
        pill = QFrame(self, objectName='pill')
        pill.setFixedHeight(46)
        row = QHBoxLayout(pill)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(8)
        pill.hide()
        return pill, row

    def option_combo(self, row, values, setting, default, width, slot):
        combo = QComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        combo.setFixedWidth(width)
        for key, label in values:
            combo.addItem(label, key)
        # Settings come back as text after a restart, whatever the type stored.
        saved = str(self.settings.value(setting, default))
        combo.setCurrentIndex(max(0, next((i for i in range(combo.count()) if str(combo.itemData(i)) == saved), 0)))

        def changed(*args):
            self.settings.setValue(setting, combo.currentData())
            slot(combo.currentData())
        combo.currentIndexChanged.connect(changed)
        row.addWidget(combo)
        return combo

    def option_button(self, row, text, slot, checkable=False, checked=False):
        button = QPushButton(text, objectName='seg' if checkable else 'ghost', checkable=checkable)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setChecked(checked)
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    def build_options(self):
        """Bottom bar, around the note of the photo: left and right groups per mode."""
        toile, wall, color = self.views['toile'], self.views['wall'], self.views['color']
        self.action_combos = []
        toile_pill, row = self.option_pill()
        self.option_button(row, 'Mélanger', self.shuffle)
        self.straight_button = self.option_button(row, 'Photos droites', toile.set_straight, True, toile.straight)
        self.option_combo(row, [(12, '12 photos'), (24, '24 photos'), (48, '48 photos'), (0, 'Toutes')], 'toile_limit', 24, 110,
                          lambda value: (toile.set_limit(int(value)), self.bar.update()))
        swipe_left, row = self.option_pill()
        row.addWidget(QLabel('À gauche', objectName='muted'))
        self.swipe_left = self.action_combo(row, 'swipe_left')
        swipe_right, row = self.option_pill()
        row.addWidget(QLabel('À droite', objectName='muted'))
        self.swipe_right = self.action_combo(row, 'swipe_right')
        color_pill, row = self.option_pill()
        self.strip_button = self.option_button(row, 'Bandeau de couleur', self.set_strip, True, color.show_strip)
        wall_left, row = self.option_pill()
        self.option_combo(row, [('staggered', 'Décalée'), ('rows', 'Rangées')], 'wall_layout', 'staggered', 100, self.set_wall_layout)
        self.option_combo(row, [('equal', 'Formats égaux'), ('landscape', 'Horizontales en avant'), ('portrait', 'Verticales en avant')],
                          'wall_emphasis', 'equal', 172, self.set_wall_emphasis)
        row.addWidget(QLabel('Largeur', objectName='muted'))
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(35, 100)
        self.width_slider.setFixedWidth(90)
        self.width_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.width_slider.setValue(wall.width_percent)
        self.width_slider.valueChanged.connect(self.set_wall_width)
        row.addWidget(self.width_slider)
        self.option_button(row, 'Mélanger', self.shuffle)
        wall_right, row = self.option_pill()
        row.addWidget(QLabel('Sortie', objectName='muted'))
        self.drop_action = self.action_combo(row, 'drop_action')
        self.restore_button = self.option_button(row, '', self.restore_set_aside)
        self.restore_button.setToolTip("Réaffiche les photos sorties de la mosaïque. Leurs notes ne changent pas : Ctrl+Z annule une note.")
        self.restore_button.hide()
        self.options = {'toile': (None, toile_pill), 'swipe': (swipe_left, swipe_right), 'color': (None, color_pill), 'wall': (wall_left, wall_right)}
        self.refresh_marker_options()

    def action_combo(self, row, setting):
        combo = QComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        combo.setFixedWidth(150)
        combo.currentIndexChanged.connect(lambda *_: self.settings.setValue(setting, combo.currentData()) if combo.currentData() else None)
        combo.setProperty('setting', setting)
        row.addWidget(combo)
        self.action_combos.append(combo)
        return combo

    def refresh_marker_options(self):
        """Offer the actions and filters of the current marking system."""
        picks = self.window.marker == 'pick'
        defaults = {'swipe_left': 'reject' if picks else 'minus', 'swipe_right': 'select' if picks else 'none',
                    'drop_action': 'reject' if picks else 'minus'}
        hidden = () if picks else ('reject', 'neutral')
        for combo in self.action_combos:
            setting = combo.property('setting')
            combo.blockSignals(True)
            combo.clear()
            for key, label in ACTIONS:
                if key not in hidden:
                    if key == 'select' and not picks:
                        label = 'Mettre un drapeau'
                    if key == 'none' and setting == 'drop_action':
                        label = 'Sortir de la vue seulement'
                    combo.addItem(label, key)
            wanted = combo.findData(self.settings.value(setting, defaults[setting]))
            combo.setCurrentIndex(wanted if wanted >= 0 else max(0, combo.findData(defaults[setting])))
            combo.blockSignals(False)
        self.sync_filters()
        self.bar.relayout()
        self.place_overlays()

    def sync_filters(self):
        self.note_filter.sync()
        flags = self.window.flags
        self.marker_filter.blockSignals(True)
        self.marker_filter.clear()
        for index in range(flags.count()):
            self.marker_filter.addItem(flags.itemText(index), flags.itemData(index))
        self.marker_filter.setCurrentIndex(flags.currentIndex())
        self.marker_filter.blockSignals(False)

    def sync_toolbar(self):
        self.picker.sync()
        self.pill.adjustSize()
        self.place_overlays()

    def background_menu(self):
        return self.window.viewer.background_menu(self)

    def set_bottom_visible(self, visible):
        """The bottom drop zone of the mosaic needs the room while a photo is dragged."""
        self.bottom_visible = visible
        self.place_overlays()

    def place_overlays(self):
        self.pill.move(max(8, (self.width()-self.pill.width())//2), 12)
        options = getattr(self, 'options', {})
        for mode, group in options.items():
            for pill in group:
                if pill is not None:
                    pill.setVisible(mode == self.mode and self.bottom_visible)
        self.bar.setVisible(self.bottom_visible)
        left, right = options.get(self.mode, (None, None))
        for pill in (left, right):
            if pill is not None:
                pill.adjustSize()
        y = self.height()-self.bar.height()-14
        sides = [pill.width() for pill in (left, right) if pill is not None]
        total = self.bar.width()+sum(sides)+8*len(sides)
        if total <= self.width()-24:
            x = (self.width()-total)//2
            if left is not None:
                left.move(x, y)
                x += left.width()+8
            self.bar.move(x, y)
            if right is not None:
                right.move(x+self.bar.width()+8, y)
        else:
            # Too narrow for one line: the options go just above the note.
            self.bar.move((self.width()-self.bar.width())//2, y)
            above = y-54
            width = sum(sides)+8*(len(sides)-1)
            x = max(8, (self.width()-width)//2)
            for pill in (left, right):
                if pill is not None:
                    pill.move(x, above)
                    x += pill.width()+8
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
        self.sync_filters()
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

    def shuffle(self, *args):
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

    def toggle_mono(self, *args):
        self.mono = not self.mono
        self.mono_button.setChecked(self.mono)
        self.update_view()

    def set_strip(self, value):
        self.views['color'].show_strip = value
        self.settings.setValue('color_strip', value)
        self.update_view()

    def set_wall_layout(self, value):
        self.views['wall'].layout_kind = value
        self.views['wall'].relayout()

    def set_wall_emphasis(self, value):
        self.views['wall'].emphasis = value
        self.views['wall'].relayout()

    def set_wall_width(self, value):
        wall = self.views['wall']
        wall.width_percent = max(35, min(100, int(value)))
        self.settings.setValue('wall_width', wall.width_percent)
        self.width_slider.blockSignals(True)
        self.width_slider.setValue(wall.width_percent)
        self.width_slider.blockSignals(False)
        wall.relayout()

    def apply_action(self, photo, action):
        if action == 'minus':
            self.window.change([photo], stars=max(0, photo.stars-1))
        elif action == 'plus':
            self.window.change([photo], stars=min(5, photo.stars+1))
        elif action.startswith('star'):
            self.window.change([photo], stars=int(action[4:]))
        elif action in (PICK_SELECT, PICK_REJECT, PICK_NEUTRAL):
            self.window.change([photo], pick=action)
        else:
            # Nothing to change, but still a step that Ctrl+Z can take back.
            self.window.change([photo])

    def swipe_labels(self):
        return self.swipe_left.currentText(), self.swipe_right.currentText()

    def apply_swipe(self, photo, direction):
        self.apply_action(photo, (self.swipe_right if direction > 0 else self.swipe_left).currentData())

    def drop_label(self):
        return self.drop_action.currentText()

    def drop_out(self, photo):
        """A photo dragged out of the mosaic: apply the chosen action and take it away."""
        wall = self.views['wall']
        index = next((i for i, (_, p) in enumerate(wall.tiles) if p is photo), 0)
        wall.set_aside.add(photo.relative_path)
        self.apply_action(photo, self.drop_action.currentData())
        wall.rebuild()
        if wall.tiles:
            self.window.select_photo(wall.tiles[min(index, len(wall.tiles)-1)][1])
        self.update_restore()

    def update_restore(self):
        count = len(self.views['wall'].set_aside)
        self.restore_button.setText(f'Réafficher ({count})')
        self.restore_button.setVisible(count > 0)
        self.place_overlays()

    def restore_set_aside(self, *args):
        self.views['wall'].set_aside.clear()
        self.views['wall'].rebuild()
        self.update_restore()

    def after_mark(self, pick):
        """A marker set from the keyboard is a decision in Swipe: move on."""
        if self.mode == 'swipe' and pick in (PICK_SELECT, PICK_REJECT):
            self.views['swipe'].fly(1 if pick == PICK_SELECT else -1)

    def after_undo(self, photos):
        wall = self.views['wall']
        if wall.set_aside & {photo.relative_path for photo in photos}:
            wall.set_aside -= {photo.relative_path for photo in photos}
            wall.rebuild()
            self.update_restore()
        if self.mode == 'swipe' and photos:
            self.view().show_photo(photos[0])

    def skip(self, delta):
        if self.mode == 'swipe':
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
        toile, swipe = self.views['toile'], self.views['swipe']
        toile.positions, toile.stack, toile.dealt, toile.available = {}, [], [], set()
        toile.fresh = True
        swipe.deck = []
        for wall in (self.views['wall'], self.views['color']):
            wall.ordered, wall.tiles = [], []
            wall.set_aside = set()
        self.update_restore()
