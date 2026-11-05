"""The Révision page: other ways of looking at the photos selected by the filters.

Every mode reads window.photos, so the header filters drive it, and rates through
the window, so notes, undo and session saving behave as on the Tri page.
"""
from __future__ import annotations

import json
import math
import random
import threading
import time
import zlib
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QPointF, QRectF, QRunnable, QThreadPool, QTimer, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QGridLayout, QStyle, QStyleOptionButton, QColorDialog, QComboBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollBar, QSlider, QStackedWidget, QVBoxLayout, QWidget)
from PIL import Image, ImageStat
from triphoto import PICK_NEUTRAL, PICK_REJECT, PICK_SELECT
from triphoto_prefs import MARK_TIPS
from triphoto_theme import ACCENT, ASSETS, BACKGROUNDS, FAINT, MUTED, REJECT, SELECT, TEXT, contrast_on, draw_bookmark, draw_bucket_chips, draw_check, draw_clear_star, draw_comment, draw_cross, draw_flag, draw_heart, draw_send, draw_stars

# The color view has no button: it is the grid sorted by something else than the name.
MODES = [('wall', 'Grille'), ('toile', 'Pêle-mêle'), ('swipe', 'Tindr'), ('instagram', 'Instagram')]
# What a swipe or a drag out of the grid does to a photo. Offered in both places.
ACTIONS = [('none', 'Ne rien changer'), ('select', 'Retenir'), ('reject', 'Rejeter'), ('neutral', 'Neutre'),
           ('minus', 'Retirer une étoile'), ('plus', 'Ajouter une étoile'), ('star5', '5 étoiles'), ('star4', '4 étoiles'),
           ('star3', '3 étoiles'), ('star2', '2 étoiles'), ('star1', '1 étoile'), ('star0', '0 étoile')]
STAGGERED_MODULES = json.loads((ASSETS/'staggered_modules.json').read_text(encoding='utf-8'))
TOP_CLEARANCE, BOTTOM_CLEARANCE = 16, 76
ANALYSIS_FILE = 'colors.json'
# Raised when a measure changes: what was analysed before is read once more.
ANALYSIS_VERSION = 2
STATE_FILE = 'review_state.json'
# Name and upper hue of each block of the Couleur mode, in rainbow order from the reds.
COLOR_FAMILIES = [('Rouges', 15), ('Oranges', 45), ('Jaunes', 70), ('Verts', 165), ('Turquoises', 200), ('Bleus', 255),
                  ('Violets', 300), ('Roses', 345)]
FAMILY_COLORS = {'Rouges': '#c8392f', 'Oranges': '#d9772b', 'Jaunes': '#dcc02e', 'Verts': '#3ea04a', 'Turquoises': '#2fa8a8',
                 'Bleus': '#3560c8', 'Violets': '#8a44c0', 'Roses': '#d05a98', 'Neutres': '#8c8c8c'}
ROW_HEIGHT, WALL_WIDTH = 210, 62
# Room under each photo of a staggered layout, for its stars.
CAPTION = 26
# Orders of the Couleur mode: (label, one way, the other way, what the ends of the scale are called).
# Name, the two orders, and how each one reads; then what it means, for the tooltip.
COLOR_ORDERS = [('Couleur', 'hue', 'hue_desc', 'Croissant', 'Décroissant', 'Du rouge au violet', 'Du violet au rouge'),
                ('Luminosité', 'light', 'dark', 'Décroissant', 'Croissant', 'Du clair au sombre', 'Du sombre au clair'),
                ('Contraste', 'contrast', 'flat', 'Décroissant', 'Croissant', 'Du plus fort au plus faible', 'Du plus faible au plus fort'),
                ('Saturation', 'vivid', 'dull', 'Décroissant', 'Croissant', 'De la plus vive à la plus terne', 'De la plus terne à la plus vive')]
# Decoded previews a page may hold at full sharpness; the rest of the 256 Mio stays for smaller ones.
LARGE_BUDGET = 180*1024*1024


def source_aspect(source):
    width, height = source.size
    if source.getexif().get(274, 1) in (5, 6, 7, 8):
        width, height = height, width
    return width/max(1, height)


def read_aspect(path):
    """Width over height, from the file header alone: far faster than the colors."""
    with Image.open(path) as source:
        return source_aspect(source)


def tone_contrast(image):
    """Spread of the tones against the widest one this lightness allows: 0 for a flat image.

    The spread alone follows the lightness: night photos would always sort as the flattest.
    """
    stat = ImageStat.Stat(image)
    mean = stat.mean[0]/255
    return min(1., stat.stddev[0]/255/max(math.sqrt(mean*(1-mean)), .05))


def analyse_photo(path):
    """Aspect ratio and dominant color, from a heavily reduced decode."""
    with Image.open(path) as source:
        aspect = source_aspect(source)
        try:
            source.draft('RGB', (96, 96))
        except (AttributeError, OSError):
            pass
        small = source.convert('RGB')
    small.thumbnail((48, 48))
    bins = [[0., 0., 0., 0., 0.] for _ in range(24)]
    chroma = lightness = vividness = 0.
    contrast = tone_contrast(small.convert('L'))
    pixels = list(zip(small.convert('HSV').getdata(), small.getdata()))
    for (hue, saturation, value), (red, green, blue) in pixels:
        lightness += value
        vividness += saturation
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
    vividness /= max(1, len(pixels))*255
    if chroma/max(1, len(pixels)) < .05:
        grey = int(lightness*255)
        return {'aspect': aspect, 'neutral': True, 'hue': 0, 'light': lightness, 'contrast': contrast, 'vivid': vividness, 'rgb': [grey]*3}
    best = max(bins, key=lambda slot: slot[0])
    return {'aspect': aspect, 'neutral': False, 'hue': best[1]/best[0]*360/256, 'light': lightness, 'contrast': contrast, 'vivid': vividness,
            'rgb': [int(best[2]/best[0]), int(best[3]/best[0]), int(best[4]/best[0])]}


def staggered_plan(keys, ratios):
    """Choose a stable sequence of modules from the photographs' proportions.

    Same logic as the editorial galleries of the website: a rhythm of solos, pairs
    and diagonals, without a repeated shape. ratios are height / width.
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
        solo = (remaining == 1 or (index == 0 and (ratio < .9 or token % 4 == 0)) or since_solo >= next_solo
                or (ratio < .53 and since_solo > 0))
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


def color_key(info, order='hue'):
    """Rainbow order starting from the reds, neutrals closing it, or a plain scale."""
    if not info:
        return (2, 0, 0)
    scale = {'light': ('light', -1), 'dark': ('light', 1), 'contrast': ('contrast', -1), 'flat': ('contrast', 1),
             'vivid': ('vivid', -1), 'dull': ('vivid', 1)}.get(order)
    if scale:
        return (0, scale[1]*info.get(scale[0], 0), 0)
    if info.get('neutral'):
        return (1, -info['light'], 0)
    hue = (info['hue']+15) % 360
    return (0, hue if order == 'hue' else -hue, -info.get('vivid', 0))


def order_tint(info, order):
    """The color of the cursor: only what the order sorts on varies, everything else stays fixed."""
    if not info:
        return None
    if order in ('light', 'dark'):
        return QColor.fromHsl(0, 0, int(info['light']*255))
    if order in ('contrast', 'flat'):
        # Black for a flat image, white for a hard one, whose scale stops around .6.
        return QColor.fromHsl(0, 0, int(min(1., info.get('contrast', 0)/.6)*255))
    if order in ('vivid', 'dull'):
        return QColor.fromHsl(10, int(min(1., info.get('vivid', 0)/.7)*255), 128)
    return QColor.fromHsl(0, 0, 128) if info.get('neutral') else QColor.fromHsl(int(info['hue']) % 360, 190, 128)


def color_family(info):
    """The block of the Couleur mode a photo belongs to."""
    if not info or info.get('neutral'):
        return 'Neutres'
    return next((name for name, upper in COLOR_FAMILIES if info['hue'] < upper), 'Rouges')


class AnalysisSignals(QObject):
    batch = Signal(dict, int, int, int)
    aspects = Signal(dict, int)


class AnalysisJob(QRunnable):
    def __init__(self, photos, token):
        super().__init__()
        self.photos, self.token = photos, token
        self.signals = AnalysisSignals()
        self.cancelled = threading.Event()

    def run(self):
        # Formats first and all at once: the pages lay out once, and the colors,
        # however long they take, have nothing left to move.
        aspects = {}
        for key, path, _ in self.photos:
            if self.cancelled.is_set():
                return
            try:
                aspects[key] = read_aspect(path)
            except Exception:
                pass
        self.signals.aspects.emit(aspects, self.token)
        batch = {}
        total = len(self.photos)
        for done, (key, path, signature) in enumerate(self.photos, 1):
            if self.cancelled.is_set():
                return
            try:
                batch[key] = {**analyse_photo(path), 'signature': signature, 'version': ANALYSIS_VERSION}
            except Exception:
                batch[key] = {'aspect': 1.5, 'neutral': True, 'hue': 0, 'light': 0, 'contrast': 0, 'vivid': 0, 'rgb': [40, 40, 40], 'signature': signature, 'version': ANALYSIS_VERSION}
            if len(batch) >= 16 or done == total:
                self.signals.batch.emit(batch, done, total, self.token)
                batch = {}


class RatingBar(QWidget):
    """Floating stars and marker. Fast access to notes, in every view."""
    rated = Signal(int)
    marked = Signal(str)
    portfolio = Signal()
    publish = Signal()

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
        self.setFixedSize(self.name_width+18+26+5*23+18+marks*30+6+2*30+8, 46)

    def stars_x(self):
        return self.name_width+18+26+10

    def clear_rect(self):
        return QRectF(self.name_width+8, 7, 30, 32)

    def mark_rects(self):
        x = self.stars_x()+5*23+6
        return [QRectF(x+i*30, 7, 30, 32) for i in range(2 if self.window.marker == 'pick' else 1)]

    def choice_rects(self):
        """Portfolio and À publier: independent of the note and of the marker."""
        x = self.mark_rects()[-1].right()+6
        return QRectF(x, 7, 30, 32), QRectF(x+30, 7, 30, 32)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 22)))
        painter.setBrush(QColor(22, 22, 23, 232))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 11, 11)
        photos = self.window.target_photos()
        photo = photos[0] if photos else None
        if self.show_name:
            text = f'{len(photos)} photos' if len(photos) > 1 else (self.window.photo_label(photo) if photo else '')
            painter.setPen(QColor(MUTED))
            painter.setFont(QFont(self.font().family(), 9))
            text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, self.name_width-16)
            painter.drawText(QRectF(16, 0, self.name_width-16, self.height()), Qt.AlignmentFlag.AlignVCenter, text)
        clear = self.clear_rect().center()
        draw_clear_star(painter, clear.x(), clear.y(), 8.0, TEXT if any(p.stars for p in photos) else FAINT)
        draw_stars(painter, self.stars_x(), self.height()/2, photo.stars if photo else 0, 8.0, 23.0)
        rects = self.mark_rects()
        icon = lambda r: r.adjusted(7, 8, -7, -8)
        if self.window.marker == 'pick':
            draw_check(painter, icon(rects[0]), SELECT if photo and photo.pick == PICK_SELECT else FAINT)
            draw_cross(painter, icon(rects[1]), REJECT if photo and photo.pick == PICK_REJECT else FAINT)
        else:
            draw_flag(painter, icon(rects[0]), ACCENT if photo and photo.flagged else FAINT)
        keep, send = self.choice_rects()
        kept = bool(photo and self.window.in_portfolio(photo))
        draw_bookmark(painter, keep.adjusted(8, 8, -8, -8), ACCENT if kept else FAINT, kept)
        published = bool(photo and photo.publish)
        draw_send(painter, send.adjusted(7, 8, -7, -8), ACCENT if published else FAINT, published)

    def mouseMoveEvent(self, event):
        keep, send = self.choice_rects()
        point = event.position()
        marks = self.mark_rects()
        self.setToolTip('Retirer les étoiles' if self.clear_rect().contains(point) else MARK_TIPS['portfolio'] if keep.contains(point)
                        else MARK_TIPS['publish'] if send.contains(point)
                        else (MARK_TIPS['select'] if marks[0].contains(point) else MARK_TIPS['reject'] if marks[-1].contains(point) else '') if self.window.marker == 'pick'
                        else MARK_TIPS['flag'] if marks[0].contains(point) else '')

    def mousePressEvent(self, event):
        # Otherwise the press reaches the photo under the bar, which then follows the mouse.
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        photos = self.window.target_photos()
        if not photos:
            return
        if self.clear_rect().contains(event.position()):
            if any(p.stars for p in photos):
                self.rated.emit(0)
            return
        x, y = event.position().x(), event.position().y()
        start = self.stars_x()-11.5
        if start <= x < start+5*23:
            value = int((x-start)//23)+1
            # A second click on the same star clears the note, as in Lightroom.
            self.rated.emit(0 if photos[0].stars == value else value)
            return
        keep, send = self.choice_rects()
        if keep.contains(event.position()):
            self.portfolio.emit()
            return
        if send.contains(event.position()):
            self.publish.emit()
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
    """Preset greys and any other color, shared by the Tri and Révision pages."""

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

    def mousePressEvent(self, event):
        # As for the bar: between two swatches, a click is not for the photo underneath.
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

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
        self.last_clear = 0.
        # The list always has a current row: a view only acts on it once a photo is picked here.
        self.active = False
        self.picked = None
        self.setMouseTracking(True)
        self.request_timer = QTimer(self)
        self.request_timer.setSingleShot(True)
        self.request_timer.timeout.connect(self.requests_due)

    def rebuild(self):
        self.update()

    def photos_changed(self):
        self.rebuild()

    def request_images(self):
        pass

    def requests_due(self):
        # A hidden view still gets resized and repainted: it must leave the loading of the shown page alone.
        if self.window_.review_active and self.review.view() is self:
            self.request_images()

    def navigate(self, delta):
        pass

    def shuffle(self):
        pass

    def zoom(self, factor):
        pass

    def zoom_reset(self):
        pass

    def zoom_text(self):
        return '100 %'

    def tile_at(self, position):
        """The photo under this point of the widget. A view of one photo at a time only has that one."""
        return self.current() if self.fixed_deck else None

    def badge_hit(self, position):
        """The photo whose button lies under this point of the widget, and which button."""
        return None

    def badge_at(self, rect, photo, point, margin=4):
        """Which button of a tile holds this point, in the coordinates of the tile."""
        button = self.badge_layout(rect, photo)[0]
        if button is not None and button.adjusted(-margin, -margin, margin, margin).contains(point):
            return photo, 'clear'
        heart = self.heart_rect(rect)
        if heart is not None and heart.adjusted(-margin, -margin, margin, margin).contains(point):
            return photo, 'heart'
        return None

    def badge_click(self, position):
        hit = self.badge_hit(position)
        if hit is None:
            return False
        photo, kind = hit
        photos = self.window_.with_selection(photo)
        if kind == 'heart':
            self.window_.change(photos, liked=not photo.liked)
        else:
            self.window_.change(photos, stars=0)
        self.last_clear = time.monotonic()
        return True

    def just_cleared(self):
        """Clearing fast reads as a double click, which must not leave for the Tri page."""
        return time.monotonic()-self.last_clear < QApplication.doubleClickInterval()/1000

    def hover_badge(self, position):
        hit = self.badge_hit(position)
        self.setCursor(Qt.CursorShape.PointingHandCursor) if hit else self.unsetCursor()
        self.setToolTip({'clear': 'Retirer les étoiles', 'heart': MARK_TIPS['like']}[hit[1]] if hit else '')

    def targets(self):
        return self.window_.selected_photos() if self.active else []

    def current(self):
        return self.window_.current_photo()

    def pick(self, photo, extend=False):
        extend = extend and self.active
        self.active = True
        self.picked = photo.relative_path
        self.window_.select_photo(photo, extend)
        self.update()
        self.review.sync_bar()

    def drop_lost_pick(self):
        """The photo picked has left the filter: the list moved on to a neighbour that may be anywhere on this page.

        The next key must not fall on a photo nobody is looking at.
        """
        if self.active and self.picked is not None and all(p.relative_path != self.picked for p in self.window_.photos):
            self.unpick()

    def unpick(self):
        # Otherwise a selection no longer visible here would still act back in Classique.
        self.active = False
        self.picked = None
        self.window_.select_only_current()
        self.update()
        self.review.sync_bar()

    def shown_photos(self):
        return self.window_.photos

    def select_all(self):
        if self.fixed_deck:
            self.window_.toast('Cette vue montre une photo à la fois.', 1400)
            return
        self.active = True
        self.picked = None
        self.window_.select_photos(self.shown_photos())
        self.update()
        self.review.sync_bar()

    def request_tiles(self, wanted):
        """Ask for the previews of what is on screen, (photo, pixels), within what memory can hold.

        Beyond it, each image loaded would push out another one still on screen, itself
        then asked for again, and the loading would never end.
        """
        large = [max(2000, pixels) for _, pixels in wanted if pixels > 1100]
        total = sum(pixels*pixels*2.67 for pixels in large)
        factor = min(1., math.sqrt(LARGE_BUDGET/total)) if total else 1.
        self.window_.large_keep = max(6, len(large))
        self.window_.drop_requests({str(photo.path) for photo, _ in wanted})
        for photo, pixels in wanted:
            if pixels > 1100 and max(2000, pixels)*factor < 2000:
                pixels = 1000
            elif pixels > 1100:
                pixels *= factor
            self.window_.request_for(photo, pixels, 5)

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

    def badge_layout(self, rect, photo):
        """Zero-star button, when there are stars to take away, and the plate beside it."""
        choices = self.window_.portfolio.contains(photo.path)+photo.publish
        if not photo.stars and photo.pick == PICK_NEUTRAL and not choices:
            return None, None
        width = (5*11+8 if photo.stars else 0)+(17 if photo.pick != PICK_NEUTRAL else 0)+choices*15
        # Under the photo where the layout leaves room, otherwise on it.
        below = self.captions_below()
        left, top = (rect.left(), rect.bottom()+4) if below else (rect.left()+6, rect.bottom()-25)
        button = QRectF(left, top, 19, 19) if photo.stars else None
        plate = QRectF(left+(23 if photo.stars else 0), top, width+4, 19)
        if rect.width() < plate.right()-rect.left()+4 or (rect.height() < 34 and not below):
            return None, None
        return button, plate

    def captions_below(self):
        return False

    def heart_rect(self, rect):
        if rect.width() < 44 or rect.height() < 34:
            return None
        return QRectF(rect.right()-21, rect.top()+7, 14, 13)

    def photo_rect(self, rect, photo):
        """The photo itself inside the rect it is contained in."""
        image = self.window_.best_image(photo)
        aspect = image.width()/image.height() if image is not None else self.review.aspect(photo)
        scale = min(rect.width()/aspect, rect.height())
        return QRectF(rect.center().x()-scale*aspect/2, rect.center().y()-scale/2, scale*aspect, scale)

    def pick_for_drag(self, photo, extend):
        """In a selection, pressing a photo already picked keeps the others, so that they can be dragged together."""
        if extend or not self.window_.bucket_mode or not any(other is photo for other in self.targets()):
            self.pick(photo, extend)

    def paint_badges(self, painter, rect, photo):
        """Stars and marker on a small dark plate, readable on any photo or background."""
        chips = self.window_.photo_buckets(photo)
        if chips:
            draw_bucket_chips(painter, rect.left()+6, rect.top()+6, rect.width()-34, chips)
        heart = self.heart_rect(rect)
        if heart is not None:
            draw_heart(painter, heart, photo.liked)
        button, plate = self.badge_layout(rect, photo)
        if plate is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 15, 16, 190))
        painter.drawRoundedRect(plate, 5, 5)
        if button is not None:
            painter.setBrush(QColor(15, 15, 16, 190))
            painter.drawEllipse(button)
            draw_clear_star(painter, button.center().x(), button.center().y(), 5.5, TEXT)
        x = plate.left()+9
        if photo.stars:
            draw_stars(painter, x, plate.center().y(), photo.stars, 4.5, 11.0)
            x += 5*11
        icon = QRectF(x-3, plate.top()+5, 9, 9)
        if photo.pick == PICK_SELECT:
            (draw_check if self.window_.marker == 'pick' else draw_flag)(painter, icon, SELECT if self.window_.marker == 'pick' else ACCENT)
        elif photo.pick == PICK_REJECT:
            draw_cross(painter, icon, REJECT)
        x += 15 if photo.pick != PICK_NEUTRAL else 0
        if self.window_.in_portfolio(photo):
            draw_bookmark(painter, QRectF(x-3, plate.top()+5, 9, 9), ACCENT, True)
            x += 15
        if photo.publish:
            draw_send(painter, QRectF(x-3, plate.top()+5, 9, 9), ACCENT, True)
        painter.restore()

    def paint_empty(self, painter, text):
        painter.setPen(self.hint_color())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

    def contextMenuEvent(self, event):
        menu = self.review.background_menu()
        self.window_.add_export_action(menu, self.window_.clicked_photos(self.tile_at(QPointF(event.pos()))))
        menu.exec(event.globalPos())


class WallView(Surface):
    """Grid of photos. Couleur uses the same wall, sorted by dominant color."""

    def __init__(self, review, by_color=False):
        super().__init__(review)
        self.by_color = by_color
        settings = review.settings
        self.height_setting = 'color_row_height' if by_color else 'wall_row_height'
        self.row_height = float(settings.value(self.height_setting, ROW_HEIGHT))
        self.layout_kind = settings.value('color_layout', 'rows') if by_color else settings.value('wall_layout', 'staggered')
        self.width_setting = 'color_width' if by_color else 'wall_width'
        self.width_percent = int(settings.value(self.width_setting, WALL_WIDTH))
        self.emphasis = 'equal' if by_color else settings.value('wall_emphasis', 'equal')
        # A new key: the former one may hold the lightness order, which used to be called contrast.
        self.order = settings.value('color_sort', 'hue')
        self.groups = settings.value('color_groups', False, type=bool)
        self.headings = []
        self.order_seed = None
        self.set_aside = set()
        self.tiles = []
        # Where every photo of the current layout went, shown or not: one that leaves keeps its place.
        self.placed = []
        self.bar_tint = None
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
        self.tint_bar()

    def tint_bar(self):
        """In Couleur, the cursor takes the color of the photos in the middle of the page."""
        if not self.by_color:
            return
        middle = self.bar.value()+self.height()/2
        tile = min(self.visible_tiles(), key=lambda t: abs(t[0].center().y()-middle), default=None)
        info = self.review.info.get(tile[1].relative_path) if tile else None
        color = order_tint(info, self.order)
        tint = color.name() if color else None
        if tint != self.bar_tint:
            self.bar_tint = tint
            self.bar.setStyleSheet(f'QScrollBar::handle:vertical {{ background: {tint}; border: 1px solid rgba(128, 128, 128, 120); '
                                   'border-radius: 5px; min-height: 48px; }' if tint else '')

    def shown_photos(self):
        return [p for p in self.window_.photos if p.relative_path not in self.set_aside]

    def rebuild(self):
        self.ordered = self.shown_photos()
        if self.by_color:
            self.ordered.sort(key=lambda p: color_key(self.review.info.get(p.relative_path), self.order))
        elif self.order_seed is not None:
            # Hashed, so that removing one photo leaves the others where they are.
            self.ordered.sort(key=lambda p: zlib.crc32(f'{self.order_seed}|{p.relative_path}'.encode()))
        self.relayout()
        self.review.update_restore()

    def shown_keys(self):
        return {p.relative_path for p in self.window_.photos}-self.set_aside

    def keep_shown(self):
        shown = self.shown_keys()
        self.tiles = [tile for tile in self.placed if tile[1].relative_path in shown]
        for heading in self.headings:
            heading[2] = sum(color_family(self.review.info.get(p.relative_path)) == heading[1] for _, p in self.tiles)

    def photos_changed(self):
        # In name order the next photo is the neighbour on the page; sorted otherwise it may be far away.
        if self.by_color or self.order_seed is not None:
            self.drop_lost_pick()
        # A photo leaving, or coming back with Ctrl+Z, must not move the others under the eye.
        if self.shown_keys() <= {p.relative_path for _, p in self.placed}:
            self.keep_shown()
            self.update()
            self.schedule_requests()
            self.review.update_restore()
        else:
            self.rebuild()

    def shuffle(self):
        if not self.by_color:
            def back_to(seed, position):
                def restore():
                    opposite = back_to(self.order_seed, self.bar.value())
                    self.order_seed = seed
                    self.rebuild()
                    self.bar.setValue(position)
                    self.review.sync_color_order()
                    return opposite
                return restore
            step = back_to(self.order_seed, self.bar.value())
            self.order_seed = random.randrange(1 << 30)
            self.bar.setValue(0)
            self.rebuild()
            self.window_.remember(('view', 'Mélange', step))

    def content_box(self):
        available = max(200, self.width()-2*18-10)
        width = available*(100 if self.by_color and self.layout_kind == 'rows' else self.width_percent)/100
        return (self.width()-10-width)/2, width

    def relayout(self):
        # Anchor on the first visible photo, so that a late aspect ratio or a
        # note that changes the filter never throws the reader elsewhere.
        anchor = self.first_visible()
        offset = self.tiles[anchor][0].top()-self.bar.value() if anchor is not None and anchor < len(self.tiles) else 0
        anchor_key = self.tiles[anchor][1].relative_path if anchor is not None and anchor < len(self.tiles) else None
        left, width = self.content_box()
        self.tiles = []
        self.headings = []
        bottom = self.layout_photos(left, width)
        self.placed = list(self.tiles)
        self.keep_shown()
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
        self.tint_bar()

    def layout_photos(self, left, width):
        place = self.layout_staggered if self.layout_kind == 'staggered' else self.place_rows
        y = float(TOP_CLEARANCE)
        if not (self.by_color and self.groups and self.order in ('hue', 'hue_desc')):
            return place(self.ordered, left, width, y)
        blocks = {}
        for photo in self.ordered:
            blocks.setdefault(color_family(self.review.info.get(photo.relative_path)), []).append(photo)
        names = [name for name, _ in COLOR_FAMILIES]
        for name in (names[::-1] if self.order == 'hue_desc' else names)+['Neutres']:
            if name in blocks:
                y += 40 if self.headings else 0
                self.headings.append([QRectF(left, y, width, 30), name, len(blocks[name])])
                y = place(blocks[name], left, width, y+38)
        return y

    def place_rows(self, photos, left, width, y):
        """Rows in the order of the photos, set to the height of one orientation.

        Set to the horizontals, every photo of a row has the same height: the verticals
        look small. Set to the verticals, a horizontal keeps the same long side and sits
        centered in the taller row: it looks small. Formats égaux follows the majority.
        """
        gap = 6
        reference = self.emphasis
        if reference == 'equal':
            landscapes = sum(self.review.aspect(p) >= 1 for p in photos)
            reference = 'landscape' if landscapes*2 >= len(photos) else 'portrait'

        def unit(photo):
            aspect = self.review.aspect(photo)
            return (1., 1/aspect) if reference == 'portrait' and aspect >= 1 else (aspect, 1.)

        target = self.row_height*(1.35 if reference == 'portrait' else 1)
        tallest = max(240, self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE)
        queue = list(photos)
        while queue:
            row, total = [], 0.
            while queue and total*target < width:
                row.append(queue.pop(0))
                total += unit(row[-1])[0]
            height = min(target*1.15, tallest, (width-gap*(len(row)-1))/total)
            # As high as its tallest photo: a row of horizontals keeps the same gap above as beside.
            span = max(unit(photo)[1] for photo in row)
            x = left
            for photo in row:
                tile_width, tile_height = unit(photo)
                self.tiles.append((QRectF(x, y+(span-tile_height)*height/2, tile_width*height, tile_height*height), photo))
                x += tile_width*height+gap
            y += span*height+gap
        return y

    def layout_staggered(self, photos, left, width, cursor):
        if not photos:
            return cursor
        ratios = [1/self.review.aspect(p) for p in photos]
        plan = staggered_plan([p.relative_path for p in photos], ratios)
        # On the website a tall photo may exceed the screen; to judge it here, it must not.
        tallest = max(240, self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE-CAPTION)
        index = 0
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
                    offset += panels[n][2]+CAPTION+(module.get('gap', 0)*width if position < len(order)-1 else 0)
                row_height = offset
            else:
                row_height = max(panel[2] for panel in panels)+CAPTION
                if module['mode'] == 'center':
                    for panel in panels:
                        panel[3] = (row_height-CAPTION-panel[2])/2
            for n, panel in enumerate(panels):
                self.tiles.append((QRectF(panel[0], cursor+panel[3], panel[1], panel[2]), photos[index+n]))
            cursor += row_height+module.get('after', 0)*width+(10 if not module.get('after') else 0)
            index += len(panels)
        return cursor

    def captions_below(self):
        return self.layout_kind == 'staggered'

    def visible_tiles(self):
        top, bottom = self.bar.value(), self.bar.value()+self.height()
        return [(rect, photo) for rect, photo in self.tiles if rect.bottom() >= top and rect.top() <= bottom]

    def first_visible(self):
        top = self.bar.value()
        return next((i for i, (rect, _) in enumerate(self.tiles) if rect.bottom() >= top), None) if self.tiles else None

    def request_images(self):
        ratio = self.devicePixelRatioF()
        self.request_tiles([(photo, max(rect.width(), rect.height())*ratio) for rect, photo in self.visible_tiles()])

    def resizeEvent(self, event):
        side = 14 if self.by_color else 9
        self.bar.setGeometry(self.width()-side-1, TOP_CLEARANCE, side, self.height()-TOP_CLEARANCE-8)
        self.relayout()

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_background(painter)
        if not self.tiles:
            self.paint_empty(painter, 'Aucune photo pour ces filtres')
            return
        painter.translate(0, -self.bar.value())
        self.paint_headings(painter)
        current = self.window_.current_photo()
        selected = {p.relative_path for p in self.targets()}
        for rect, photo in self.visible_tiles():
            self.paint_photo(painter, rect, photo)
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

    def paint_headings(self, painter):
        top, bottom = self.bar.value(), self.bar.value()+self.height()
        for rect, name, count in self.headings:
            if rect.bottom() < top or rect.top() > bottom:
                continue
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(FAMILY_COLORS[name]))
            painter.drawEllipse(QRectF(rect.left(), rect.center().y()-7, 14, 14))
            painter.setPen(self.hint_color())
            font = painter.font()
            font.setPointSize(14)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.drawText(rect.adjusted(24, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, f'{name}  ·  {count}')
            painter.restore()

    def drop_zones(self):
        # In a selection, a photo goes to the buckets under the view.
        if self.window_.bucket_mode:
            return []
        left, width = self.content_box()
        side = max(96., left-6)
        return [QRectF(0, TOP_CLEARANCE, side, self.height()-TOP_CLEARANCE),
                QRectF(self.width()-side-10, TOP_CLEARANCE, side+10, self.height()-TOP_CLEARANCE),
                QRectF(0, self.height()-92, self.width(), 92)]

    def zone_at(self, position):
        return any(zone.contains(position) for zone in self.drop_zones())

    def paint_drag(self, painter):
        if self.window_.bucket_mode:
            return
        label = self.review.drop_label()
        hovered = self.zone_at(self.drag_position)
        painter.save()
        for zone in self.drop_zones():
            active = hovered and zone.contains(self.drag_position)
            painter.fillRect(zone, QColor(242, 242, 242, 225) if active else QColor(15, 15, 16, 190))
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
            self.set_width(self.width_percent*(1.06 if factor > 1 else 1/1.06))
        else:
            self.set_row_height(max(90, min(560, self.row_height*factor)))

    def set_width(self, value):
        self.width_percent = max(35, min(100, int(value)))
        self.review.settings.setValue(self.width_setting, self.width_percent)
        self.relayout()
        self.review.sync_zoom()

    def set_row_height(self, value):
        self.row_height = value
        self.review.settings.setValue(self.height_setting, value)
        self.relayout()
        self.review.sync_zoom()

    def zoom_reset(self):
        if self.layout_kind == 'staggered':
            self.set_width(WALL_WIDTH)
        else:
            self.set_row_height(ROW_HEIGHT)

    def zoom_text(self):
        return f'{self.width_percent} %' if self.layout_kind == 'staggered' else f'{round(self.row_height/ROW_HEIGHT*100)} %'

    def badge_hit(self, position):
        point = position+QPointF(0, self.bar.value())
        for rect, photo in self.visible_tiles():
            hit = self.badge_at(rect, photo, point)
            if hit:
                return hit
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.badge_click(event.position()):
                return
            photo = self.tile_at(event.position())
            if photo:
                extend = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
                self.pick_for_drag(photo, extend)
                self.press = (photo, event.position()) if not extend and (not self.by_color or self.window_.bucket_mode) else None
            else:
                self.unpick()

    def mouseMoveEvent(self, event):
        if self.press is None:
            self.hover_badge(event.position())
            return
        if not self.dragging and (event.position()-self.press[1]).manhattanLength() > 14:
            self.dragging = True
            self.review.set_bottom_visible(False)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        if self.dragging:
            self.drag_position = event.position()
            self.update()
            if self.window_.bucket_mode:
                self.window_.bucket_drag(event.globalPosition().toPoint(), self.press[0])

    def mouseReleaseEvent(self, event):
        press, dragging = self.press, self.dragging
        self.press, self.dragging = None, False
        if dragging:
            self.unsetCursor()
            self.review.set_bottom_visible(True)
            self.update()
            if self.window_.bucket_mode:
                self.window_.bucket_drop(event.globalPosition().toPoint(), press[0])
            elif self.zone_at(event.position()):
                self.review.drop_out(press[0])

    def mouseDoubleClickEvent(self, event):
        if self.badge_click(event.position()) or self.just_cleared():
            return
        photo = self.tile_at(event.position())
        if photo:
            self.window_.open_in_tri(photo)

    def navigate(self, delta):
        if not self.tiles:
            return
        current = self.window_.current_photo()
        # With nothing picked yet, the keys start from what is on screen.
        index = next((i for i, (_, p) in enumerate(self.tiles) if p is current), 0)+delta if self.active else self.first_visible() or 0
        rect, photo = self.tiles[max(0, min(len(self.tiles)-1, index))]
        self.pick(photo)
        top = self.bar.value()
        if rect.top()-TOP_CLEARANCE < top:
            self.bar.setValue(int(rect.top()-TOP_CLEARANCE))
        elif rect.bottom()+BOTTOM_CLEARANCE > top+self.height():
            self.bar.setValue(int(rect.bottom()+BOTTOM_CLEARANCE-self.height()))
        self.update()


class ToileView(Surface):
    """The filtered photos laid out as prints on a table, so many across its width.

    Mélanger lays them out again. Better-rated photos are larger. They lie on an invisible
    grid: cells as wide as the widest photo, rows as high as their tallest one. The table
    keeps its width whatever the formats, and a smaller photo has more room around it.
    """
    GAP = 22

    def __init__(self, review):
        super().__init__(review)
        self.positions = {}
        self.stack = []
        self.dealt = []
        self.available = set()
        self.columns = max(2, min(16, int(review.settings.value('toile_columns', 8))))
        self.loose = {}
        self.moved = False
        self.moved_by_hand = False
        self.drag_start = None
        self.straight = review.settings.value('toile_straight', True, type=bool)
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
        self.drop_lost_pick()
        # A note may take a photo out of the filter; the hand stays the same otherwise.
        keys = {p.relative_path for p in self.window_.photos}
        self.available = keys
        shown = {p.relative_path for p in self.stack}
        self.stack = [p for p in self.stack if p.relative_path in keys]+[
            p for p in self.dealt if p.relative_path in keys and p.relative_path not in shown]
        self.update()

    def deal(self):
        photos = self.window_.photos
        self.dealt = random.sample(photos, len(photos))
        self.stack = list(self.dealt)
        self.loose = {p.relative_path: (random.uniform(-1, 1), random.uniform(-1, 1), random.uniform(-4, 4)) for p in self.dealt}
        self.active = False
        self.arrange()
        self.fresh = True
        self.fit()

    def arrange(self):
        """Same order and same slight disorder, so changing the width does not deal again."""
        self.positions = {}
        self.moved_by_hand = False
        if not self.dealt:
            return
        columns = min(self.columns, len(self.dealt))
        cell = max(self.tile_size(photo)[0] for photo in self.dealt)+self.GAP
        y = 0.
        for start in range(0, len(self.dealt), columns):
            row = self.dealt[start:start+columns]
            height = max(self.tile_size(photo)[1] for photo in row)
            for column, photo in enumerate(row):
                width, tile_height = self.tile_size(photo)
                dx, dy, angle = self.loose[photo.relative_path]
                # The disorder stays within the room left in the cell: nothing overlaps.
                self.positions[photo.relative_path] = [(column-(columns-1)/2)*cell+dx*(cell-self.GAP-width)*.4,
                                                       y+height/2+dy*(height-tile_height)*.4, angle]
            y += height+self.GAP

    def formats_known(self):
        """The formats arrive after the first deal: the rows take their true size, unless photos were moved by hand."""
        if self.dealt and not self.moved_by_hand:
            self.arrange()
            if self.fresh:
                self.fit()
            self.update()

    def bounds(self):
        box = QRectF()
        for photo in self.stack:
            box = box.united(self.tile_rect(photo))
        return box

    def fit(self):
        if not self.positions or not self.stack or self.width() < 10:
            return
        box = self.bounds()
        area_h = self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE
        # The whole width fits; what does not fit in height continues below.
        self.scale_ = max(.06, min(3., (self.width()-36)/box.width()))
        if box.height()*self.scale_ <= area_h:
            y = (TOP_CLEARANCE-BOTTOM_CLEARANCE)/2-box.center().y()*self.scale_
        else:
            y = TOP_CLEARANCE+10-self.height()/2-box.top()*self.scale_
        self.pan = QPointF(-box.center().x()*self.scale_, y)
        self.review.sync_zoom()

    def shuffle(self):
        def back_to(dealt, loose):
            def restore():
                opposite = back_to(self.dealt, self.loose)
                self.dealt, self.loose = dealt, loose
                if {p.relative_path for p in self.window_.photos}-{p.relative_path for p in self.dealt}:
                    self.deal()
                else:
                    self.stack = list(self.dealt)
                    self.arrange()
                    self.photos_changed()
                    self.fresh = True
                    self.fit()
                self.update()
                self.schedule_requests()
                return opposite
            return restore
        step = back_to(self.dealt, self.loose)
        self.deal()
        self.update()
        self.schedule_requests()
        self.window_.remember(('view', 'Mélange', step))

    def set_straight(self, value):
        self.straight = value
        self.review.settings.setValue('toile_straight', value)
        self.update()

    def set_columns(self, value):
        self.columns = value
        self.arrange()
        self.fresh = True
        self.fit()
        self.update()
        self.schedule_requests()

    def resizeEvent(self, event):
        if self.fresh and self.stack:
            self.fit()
        self.schedule_requests()

    def tile_size(self, photo):
        """The size follows the note."""
        # In a selection the notes stand aside: all the prints have the size of the best ones.
        side = 400 if self.window_.bucket_mode else 250+photo.stars*30
        ratio = self.review.aspect(photo)
        return (side, side/ratio) if ratio >= 1 else (side*ratio, side)

    def tile_rect(self, photo):
        """World rectangle, centered on the stored position."""
        x, y, _ = self.positions[photo.relative_path]
        width, height = self.tile_size(photo)
        return QRectF(x-width/2, y-height/2, width, height)

    def to_world(self, point):
        center = QPointF(self.width()/2, self.height()/2)+self.pan
        return (point-center)/self.scale_

    def visible(self):
        area = QRectF(self.to_world(QPointF(0, 0)), self.to_world(QPointF(self.width(), self.height()))).adjusted(-60, -60, 60, 60)
        return [p for p in self.stack if self.tile_rect(p).intersects(area)]

    def request_images(self):
        ratio = self.devicePixelRatioF()
        tiles = [(photo, self.tile_rect(photo)) for photo in self.visible()]
        self.request_tiles([(photo, max(rect.width(), rect.height())*self.scale_*ratio) for photo, rect in tiles])

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_background(painter)
        if not self.stack:
            self.paint_empty(painter, 'Aucune photo pour ces filtres')
            return
        painter.translate(QPointF(self.width()/2, self.height()/2)+self.pan)
        painter.scale(self.scale_, self.scale_)
        selected = {p.relative_path for p in self.targets()}
        for photo in self.visible():
            rect = self.tile_rect(photo)
            painter.save()
            painter.translate(rect.center())
            if not self.straight:
                painter.rotate(self.positions[photo.relative_path][2])
            local = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())
            self.paint_photo(painter, local, photo)
            self.paint_badges(painter, local, photo)
            if photo.relative_path in selected:
                painter.setPen(QPen(QColor(ACCENT), 2.4/self.scale_))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(local.adjusted(-2, -2, 2, 2))
            painter.restore()

    def badge_hit(self, position):
        # Tiles are tilted and scaled: work in the coordinates of each tile, top one first.
        world = self.to_world(position)
        margin = min(30., 4/self.scale_)
        for photo in reversed(self.stack):
            rect = self.tile_rect(photo)
            local = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())
            point = world-rect.center()
            if not self.straight:
                angle = math.radians(-self.positions[photo.relative_path][2])
                point = QPointF(point.x()*math.cos(angle)-point.y()*math.sin(angle), point.x()*math.sin(angle)+point.y()*math.cos(angle))
            hit = self.badge_at(local, photo, point, margin)
            if hit:
                return hit
            if local.contains(point):
                return None
        return None

    def tile_at(self, position):
        world = self.to_world(position)
        for photo in reversed(self.stack):
            if self.tile_rect(photo).contains(world):
                return photo
        return None

    def bring_front(self, photo):
        self.stack = [p for p in self.stack if p is not photo]+[photo]

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.badge_click(event.position()):
            return
        photo = self.tile_at(event.position())
        self.drag = (photo, event.position())
        self.moved = False
        self.drag_start = list(self.positions[photo.relative_path]) if photo else None
        if photo:
            self.bring_front(photo)
            self.pick_for_drag(photo, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.update()

    def mouseMoveEvent(self, event):
        if self.drag is None:
            self.hover_badge(event.position())
            return
        photo, last = self.drag
        delta = event.position()-last
        self.drag = (photo, event.position())
        self.moved = True
        if photo:
            position = self.positions[photo.relative_path]
            position[0] += delta.x()/self.scale_
            position[1] += delta.y()/self.scale_
            if self.window_.bucket_mode:
                self.window_.bucket_drag(event.globalPosition().toPoint(), photo)
        else:
            self.pan += delta
            self.fresh = False
            self.schedule_requests()
        self.update()

    def mouseReleaseEvent(self, event):
        if self.drag is not None and self.drag[0] is None and not self.moved:
            self.unpick()
        if self.drag is not None and self.drag[0] is not None and self.moved:
            if self.window_.bucket_mode:
                self.window_.bucket_drop(event.globalPosition().toPoint(), self.drag[0])
            else:
                self.remember_move(self.drag[0].relative_path, self.drag_start)
        self.drag = None
        self.unsetCursor()

    def remember_move(self, key, start):
        self.moved_by_hand = True

        def back_to(position):
            def restore():
                opposite = back_to(self.positions.get(key, position))
                if key in self.positions:
                    self.positions[key] = position
                    self.update()
                return opposite
            return restore
        self.window_.remember(('view', 'Déplacement', back_to(start)))

    def mouseDoubleClickEvent(self, event):
        if self.badge_click(event.position()) or self.just_cleared():
            return
        photo = self.tile_at(event.position())
        if photo:
            self.window_.open_in_tri(photo)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if delta:
                self.zoom(1.15 if delta > 0 else 1/1.15, event.position())
        else:
            self.pan += QPointF(0, delta*.9)
            self.keep_in_view()
            self.fresh = False
            self.update()
            self.schedule_requests()
        event.accept()

    def keep_in_view(self):
        """The wheel stops at the first and last rows, between the two bars."""
        if not self.stack:
            return
        box = self.bounds()
        lowest = self.height()-BOTTOM_CLEARANCE-self.height()/2-box.bottom()*self.scale_
        highest = TOP_CLEARANCE+10-self.height()/2-box.top()*self.scale_
        if lowest < highest:
            self.pan = QPointF(self.pan.x(), max(lowest, min(highest, self.pan.y())))

    def zoom(self, factor, anchor=None):
        anchor = anchor if anchor is not None else QPointF(self.width()/2, self.height()/2)
        world = self.to_world(anchor)
        self.scale_ = max(.06, min(4., self.scale_*factor))
        self.fresh = False
        self.pan = anchor-QPointF(self.width()/2, self.height()/2)-world*self.scale_
        self.update()
        self.schedule_requests()
        self.review.sync_zoom()

    def zoom_reset(self):
        self.fresh = True
        self.fit()
        self.update()
        self.schedule_requests()

    def zoom_text(self):
        return f'{round(self.scale_*100)} %'

    def navigate(self, delta):
        photos = [p for p in self.dealt if p in self.stack]
        if not photos:
            return
        current = self.window_.current_photo()
        index = next((i for i, p in enumerate(photos) if p is current), 0)
        photo = photos[max(0, min(len(photos)-1, index+delta))]
        self.bring_front(photo)
        self.pick(photo)
        rect = self.tile_rect(photo)
        area = QRectF(self.to_world(QPointF(0, TOP_CLEARANCE)), self.to_world(QPointF(self.width(), self.height()-BOTTOM_CLEARANCE)))
        if not area.contains(rect):
            self.pan = -rect.center()*self.scale_
            self.fresh = False
        self.update()
        self.schedule_requests()


class SwipeView(Surface):
    """One decision at a time, by dragging the photo or with the navigation keys."""
    fixed_deck = True

    def __init__(self, review):
        super().__init__(review)
        self.deck = []
        self.index = 0
        self.shuffled = False
        self.offset = QPointF()
        self.press = None
        self.flying = 0
        # As on the Tri page, zoom and framing stay the same from one photo to the next.
        self.zoom_level = 1.
        self.pan = QPointF()
        self.pan_drag = None
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(170)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.on_animation)
        self.animation.finished.connect(self.on_landed)

    def rebuild(self):
        self.finish_animation()
        photos = list(self.window_.photos)
        # Coming back to the same photos resumes where the deck was left.
        same_photos = {p.relative_path for p in photos} == {p.relative_path for p in self.deck}
        if not same_photos or (not self.shuffled and [id(p) for p in photos] != [id(p) for p in self.deck]):
            current = self.current()
            self.deck = photos
            self.shuffled = self.shuffled and same_photos
            self.index = next((i for i, p in enumerate(photos) if p is current), 0)
        self.offset = QPointF()
        self.settle()

    def photos_changed(self):
        # The deck is fixed: a decision must not reshuffle the cards still to come.
        self.update()

    def shuffle(self):
        self.finish_animation()
        def back_to(deck, index, shuffled):
            def restore():
                self.finish_animation()
                opposite = back_to(self.deck, self.index, self.shuffled)
                self.deck, self.index, self.shuffled = deck, index, shuffled
                self.settle()
                return opposite
            return restore
        step = back_to(list(self.deck), self.index, self.shuffled)
        self.deck = random.sample(self.deck, len(self.deck))
        self.index = 0
        self.shuffled = True
        self.settle()
        self.window_.remember(('view', 'Mélange', step))

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
        self.review.sync_bar()

    def request_images(self):
        ratio = self.devicePixelRatioF()
        self.window_.large_keep = 6
        photo = self.current()
        pixels = max(self.card_area().width(), self.card_area().height())*ratio*max(1., self.zoom_level)
        if photo:
            self.window_.request_for(photo, pixels, 15)
        for n, following in enumerate(self.deck[self.index+1:self.index+4]):
            self.window_.request_for(following, pixels if n == 0 and self.zoom_level > 1 else 1000, 4)

    def card_area(self):
        return QRectF(40, TOP_CLEARANCE, max(1, self.width()-80), max(1, self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE))

    def swipe_heart(self):
        """In widget coordinates, in the corner of what is seen of the photo."""
        photo = self.current()
        if photo is None:
            return None
        area = self.card_area()
        local = QRectF(-area.width()/2, -area.height()/2, area.width(), area.height())
        shown = self.photo_rect(self.framed(local), photo).translated(area.center())
        shown = shown.intersected(QRectF(0, TOP_CLEARANCE, self.width(), self.height()-TOP_CLEARANCE-BOTTOM_CLEARANCE))
        return QRectF(shown.right()-23, shown.top()+8, 15, 14)

    def on_heart(self, position):
        heart = self.swipe_heart()
        return heart is not None and heart.adjusted(-4, -4, 4, 4).contains(position)

    def framed(self, local):
        """Where a photo goes inside its card, once zoomed and moved."""
        width, height = local.width()*self.zoom_level, local.height()*self.zoom_level
        return QRectF(self.pan.x()-width/2, self.pan.y()-height/2, width, height)

    def zoom(self, factor, anchor=None):
        previous = self.zoom_level
        self.zoom_level = max(.1, min(8., previous*factor))
        ratio = self.zoom_level/previous
        if self.zoom_level <= 1:
            # Below the fitted size, dragging decides: nothing could recenter the photo.
            self.pan = QPointF()
        elif anchor is not None:
            self.pan = (anchor-self.card_area().center())*(1-ratio)+self.pan*ratio
        else:
            self.pan *= ratio
        self.update()
        self.schedule_requests()
        self.review.sync_zoom()

    def zoom_reset(self):
        self.zoom(1/self.zoom_level)

    def zoom_text(self):
        return f'{round(self.zoom_level*100)} %'

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.15 if delta > 0 else 1/1.15, event.position())
        event.accept()

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
        # The next photo only shows while the current one is leaving.
        if following and travel > 0:
            painter.save()
            painter.setOpacity(travel)
            painter.translate(area.center())
            grow = .95+.05*travel
            painter.scale(grow, grow)
            self.paint_photo(painter, self.framed(QRectF(-area.width()/2, -area.height()/2, area.width(), area.height())), following)
            painter.restore()
        painter.save()
        painter.translate(area.center()+self.offset)
        painter.rotate(self.offset.x()/max(1, self.width())*11)
        local = QRectF(-area.width()/2, -area.height()/2, area.width(), area.height())
        self.paint_photo(painter, self.framed(local), photo)
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
        if self.offset.isNull():
            draw_heart(painter, self.swipe_heart(), photo.liked)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.current():
            self.finish_animation()
            if self.on_heart(event.position()):
                self.window_.toggle_like()
                return
            # Zoomed in, dragging moves the photo as on the Tri page; the keys still decide.
            if self.zoom_level > 1:
                self.pan_drag = event.position()
            else:
                self.press = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.pan_drag is not None:
            self.pan += event.position()-self.pan_drag
            self.pan_drag = event.position()
            self.update()
        elif self.press is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor) if self.on_heart(event.position()) else self.unsetCursor()
        else:
            self.offset = QPointF(event.position().x()-self.press.x(), (event.position().y()-self.press.y())*.25)
            self.update()

    def mouseReleaseEvent(self, event):
        if self.pan_drag is not None:
            self.pan_drag = None
            self.unsetCursor()
        if self.press is None:
            return
        self.press = None
        self.unsetCursor()
        if abs(self.offset.x()) > self.width()*.16:
            self.decide(1 if self.offset.x() > 0 else -1)
        else:
            self.animate_to(QPointF(), 0)

    def mouseDoubleClickEvent(self, event):
        if self.on_heart(event.position()):
            self.window_.toggle_like()
        elif self.current():
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


class NoteButton(QPushButton):
    """A note and, very small underneath, how many photos it concerns."""

    def __init__(self, label):
        super().__init__(label, objectName='seg', checkable=True)
        self.count = ''
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(34)

    def set_count(self, value):
        if value != self.count:
            self.count = value
            self.update()

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ''
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        painter.setPen(QColor(FAINT if not self.isEnabled() else TEXT if self.isChecked() or self.underMouse() else MUTED))
        area = QRectF(self.rect())
        painter.drawText(area.adjusted(0, 3, 0, -13), Qt.AlignmentFlag.AlignCenter, self.text())
        font = painter.font()
        font.setPixelSize(9)
        painter.setFont(font)
        painter.setPen(QColor(MUTED))
        painter.drawText(area.adjusted(0, 20, 0, -2), Qt.AlignmentFlag.AlignCenter, self.count)


class NoteFilter(QFrame):
    """Quick choice of the notes shown, driving the same filter as the Tri page."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key, label in [('all', 'Toutes'), (0, '0 ★'), (1, '1 ★'), (2, '2 ★'), (3, '3 ★'), (4, '4 ★'), (5, '5 ★')]:
            button = NoteButton(label)
            button.clicked.connect(self.apply)
            self.group.addButton(button)
            self.buttons[key] = button
            grid.addWidget(button, *divmod(len(self.buttons)-1, 4))
        self.and_more = NoteButton('et +')
        self.and_more.setToolTip('Inclure aussi les notes supérieures')
        self.and_more.clicked.connect(self.apply)
        grid.addWidget(self.and_more, 1, 3)
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
        self.and_more.setChecked(wider)
        # Below 1 or above 4 it would change nothing.
        self.and_more.setEnabled(wider or data in (1, 2, 3, 4))
        counts = self.window.note_counts()
        for key, button in self.buttons.items():
            button.set_count(str(sum(counts.values()) if key == 'all' else counts[key]) if counts else '')
        # "et +" counts from the note chosen, when it applies to it.
        chosen = int(data[2:]) if wider else data
        self.and_more.set_count(str(sum(n for note, n in counts.items() if note >= chosen)) if counts and chosen in (1, 2, 3, 4) else '')


class MarkerFilter(QComboBox):
    """Choice of the markers shown, driving the same filter on both pages."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(150)
        self.currentIndexChanged.connect(lambda index: self.window.flags.setCurrentIndex(index))
        self.sync()

    def sync(self):
        flags = self.window.flags
        self.blockSignals(True)
        if [self.itemData(i) for i in range(self.count())] != [flags.itemData(i) for i in range(flags.count())]:
            self.clear()
            for index in range(flags.count()):
                self.addItem(flags.itemText(index), flags.itemData(index))
        self.setCurrentIndex(flags.currentIndex())
        self.blockSignals(False)


class ChoiceFilter(QFrame):
    """Portfolio and À publier, which add to the note and the marker, and the project of a portfolio photo."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        lines = QVBoxLayout(self)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(6)
        toggles = QFrame(objectName='segments')
        row = QHBoxLayout(toggles)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        lines.addWidget(toggles)
        self.portfolio = self.toggle(row, 'Portfolio', 'Seulement les photos du portfolio')
        self.publish = self.toggle(row, 'À publier', 'Seulement les photos à publier')
        self.liked = self.toggle(row, "J'aime", "Seulement les photos avec un j'aime")
        self.project = QComboBox()
        self.project.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.project.setToolTip('Afficher les photos de ce projet')
        self.project.currentIndexChanged.connect(lambda *_: self.window.filters_changed())
        lines.addWidget(self.project)
        self.project.hide()

    def toggle(self, row, text, tip):
        button = QPushButton(text, objectName='seg', checkable=True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setToolTip(tip)
        button.clicked.connect(lambda *_: self.window.filters_changed())
        row.addWidget(button, 1)
        return button

    def set_projects(self, names):
        """Only inside the portfolio: the same photos cannot be filtered by project elsewhere."""
        self.project.blockSignals(True)
        wanted = self.project.currentData()
        self.project.clear()
        self.project.addItem('Tous les projets', 'all')
        for name in names:
            self.project.addItem(name.replace('&', '&&'), name)
        self.project.setCurrentIndex(max(0, self.project.findData(wanted)))
        self.project.blockSignals(False)
        self.project.setVisible(bool(names))
        self.portfolio.setVisible(not names and not self.window.viewing_portfolio())

    def reset(self):
        self.portfolio.setChecked(False)
        self.publish.setChecked(False)
        self.liked.setChecked(False)
        self.project.blockSignals(True)
        self.project.setCurrentIndex(0)
        self.project.blockSignals(False)

    def set_buckets(self):
        """Inside a selection, the filter on its buckets takes the place of the choice of a project."""
        self.project.hide()
        self.portfolio.show()

    def active_labels(self):
        labels = [button.text() for button in (self.portfolio, self.publish, self.liked) if button.isChecked()]
        if self.project.currentIndex() > 0:
            labels.append(self.project.currentText().replace('&&', '&'))
        if self.window.buckets:
            labels += self.window.bucket_rule.labels()
        return labels


class InstagramView(Surface):
    """A carousel as Instagram shows it: one frame for all the photos, the post around it.

    The frame is as wide as the tallest format needs to nearly fill the height of the
    window, so a vertical takes the whole of it and a horizontal comes out smaller.
    """
    fixed_deck = True
    HEADER, ACTIONS, MARGIN = 52, 74, 8
    DARK = ('#000000', '#2b2b2c', '#f5f5f5', '#a8a8a8', '#ededee')
    LIGHT = ('#ffffff', '#dbdbdb', '#111111', '#737373', '#000000')

    def __init__(self, review):
        super().__init__(review)
        # Its own setting, apart from the background of the other views: light post on a black screen, or the reverse.
        self.light = review.settings.value('instagram_light', False, type=bool)
        self.apply_theme()
        self.deck = []
        self.index = 0
        self.zoom_level = 1.
        self.pan = QPointF()
        self.slide = 0.
        self.press = None
        self.pan_drag = None
        self.moved = False
        self.aspect_cache = (None, .8)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(190)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self.on_animation)

    def rebuild(self):
        previous = self.current() or self.window_.current_photo()
        self.deck = list(self.window_.photos)
        self.index = next((i for i, p in enumerate(self.deck) if p is previous), min(self.index, max(0, len(self.deck)-1)))
        self.settle()

    def photos_changed(self):
        # With a filter on, a photo may just have left the carousel: its neighbour takes its place.
        if [id(p) for p in self.deck] == [id(p) for p in self.window_.photos]:
            self.update()
        else:
            self.rebuild()

    def current(self):
        return self.deck[self.index] if 0 <= self.index < len(self.deck) else None

    def targets(self):
        photo = self.current()
        return [photo] if photo else []

    def settle(self):
        photo = self.current()
        if photo and self.window_.current_photo() is not photo:
            self.window_.select_photo(photo)
        self.update()
        self.schedule_requests()
        self.review.sync_bar()

    def request_images(self):
        ratio = self.devicePixelRatioF()
        self.window_.large_keep = 6
        media = self.frames()[1]
        pixels = max(media.width(), media.height())*ratio*max(1., self.zoom_level)
        photo = self.current()
        if photo:
            self.window_.request_for(photo, pixels, 15)
        for neighbour in self.deck[max(0, self.index-1):self.index]+self.deck[self.index+1:self.index+3]:
            self.window_.request_for(neighbour, pixels, 4)

    def frame_aspect(self):
        """Width over height of the frame, from the narrowest photo, within what Instagram shows."""
        key = (len(self.deck), len(self.review.info), len(self.review.aspects))
        if self.aspect_cache[0] != key:
            narrowest = min((self.review.aspect(p) for p in self.deck), default=.8)
            self.aspect_cache = (key, max(.5625, min(.8, narrowest)))
        return self.aspect_cache[1]

    def apply_theme(self):
        self.CARD, self.EDGE, self.WRITING, self.SOFT, self.SCREEN = self.LIGHT if self.light else self.DARK

    def set_light(self, value):
        self.light = value
        self.review.settings.setValue('instagram_light', value)
        self.apply_theme()
        self.update()

    def paint_background(self, painter):
        painter.fillRect(self.rect(), QColor(self.SCREEN))

    def hint_color(self):
        return QColor(contrast_on(self.SCREEN))

    def frames(self):
        """The post and, inside it, the frame of the photos. Zooming out shrinks them; zooming in stays inside the frame."""
        room = max(120., self.height()-self.MARGIN-BOTTOM_CLEARANCE+14-self.HEADER-self.ACTIONS)
        scale = min(1., self.zoom_level)
        height = room*scale
        width = min(height*self.frame_aspect(), self.width()-48.)
        # Below this, the buttons of the post no longer fit under the photo.
        width = max(width, 300.)
        top = self.MARGIN+(room-height)/2
        post = QRectF((self.width()-width)/2, top, width, self.HEADER+height+self.ACTIONS)
        return post, QRectF(post.left(), post.top()+self.HEADER, width, height)

    def action_rects(self):
        post, media = self.frames()
        y = media.bottom()+9
        return {'heart': QRectF(post.left()+14, y+1, 22, 20), 'comment': QRectF(post.left()+50, y+1, 20, 20),
                'send': QRectF(post.left()+84, y, 21, 21), 'keep': QRectF(post.right()-34, y, 20, 22)}

    def chevrons(self):
        media = self.frames()[1]
        y = media.center().y()-14
        return QRectF(media.left()+10, y, 28, 28), QRectF(media.right()-38, y, 28, 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        self.paint_background(painter)
        photo = self.current()
        if photo is None:
            self.paint_empty(painter, 'Aucune photo pour ces filtres')
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        post, media = self.frames()
        painter.setPen(QPen(QColor(self.EDGE), 1))
        painter.setBrush(QColor(self.CARD))
        painter.drawRoundedRect(post.adjusted(-.5, -.5, .5, .5), 4, 4)
        self.paint_header(painter, post, photo)
        self.paint_media(painter, media)
        self.paint_actions(painter, post, media, photo)

    def paint_header(self, painter, post, photo):
        account = self.review.instagram_account()
        ring = QRectF(post.left()+12, post.top()+10, 32, 32)
        painter.setPen(QPen(QColor(self.SOFT), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#3a3a3c'))
        painter.drawEllipse(ring.adjusted(3, 3, -3, -3))
        font = QFont(self.font().family())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self.WRITING))
        painter.drawText(ring, Qt.AlignmentFlag.AlignCenter, account[:1].upper())
        font.setPixelSize(13)
        painter.setFont(font)
        place = self.window_.photo_project(photo)
        name = QRectF(ring.right()+10, post.top()+(9 if place else 0), post.width()-110, 20 if place else self.HEADER)
        painter.drawText(name, Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(account, Qt.TextElideMode.ElideRight, int(name.width())))
        if place:
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.drawText(name.translated(0, 16), Qt.AlignmentFlag.AlignVCenter, painter.fontMetrics().elidedText(place, Qt.TextElideMode.ElideRight, int(name.width())))
        painter.setBrush(QColor(self.WRITING))
        painter.setPen(Qt.PenStyle.NoPen)
        for n in range(3):
            painter.drawEllipse(QPointF(post.right()-26+n*6, post.top()+self.HEADER/2), 1.5, 1.5)

    def paint_media(self, painter, media):
        painter.save()
        painter.setClipRect(media)
        painter.fillRect(media, QColor(self.CARD))
        local = QRectF(media)
        if self.zoom_level > 1:
            width, height = media.width()*self.zoom_level, media.height()*self.zoom_level
            local = QRectF(media.center().x()+self.pan.x()-width/2, media.center().y()+self.pan.y()-height/2, width, height)
        for position in (self.index-1, self.index, self.index+1):
            if not 0 <= position < len(self.deck):
                continue
            shift = self.slide+(position-self.index)*media.width()
            if abs(shift) < media.width():
                photo = self.deck[position]
                self.paint_photo(painter, self.photo_rect(local, photo).translated(shift, 0), photo)
        painter.restore()
        # The count and the arrows of a carousel; none with a single photo.
        if len(self.deck) < 2:
            return
        font = QFont(self.font().family())
        font.setPixelSize(12)
        painter.setFont(font)
        label = f'{self.index+1}/{len(self.deck)}'
        width = painter.fontMetrics().horizontalAdvance(label)+20
        pill = QRectF(media.right()-width-12, media.top()+12, width, 26)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(18, 18, 18, 190))
        painter.drawRoundedRect(pill, 13, 13)
        painter.setPen(QColor('#ffffff'))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)
        left, right = self.chevrons()
        for rect, shown, direction in ((left, self.index > 0, -1), (right, self.index < len(self.deck)-1, 1)):
            if not shown:
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 210))
            painter.drawEllipse(rect)
            center = rect.center()
            painter.setPen(QPen(QColor('#1a1a1a'), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPolyline([center+QPointF(-direction*3, -6), center+QPointF(direction*3, 0), center+QPointF(-direction*3, 6)])

    def paint_actions(self, painter, post, media, photo):
        rects = self.action_rects()
        draw_heart(painter, rects['heart'], photo.liked)
        draw_comment(painter, rects['comment'], self.WRITING)
        draw_send(painter, rects['send'], self.WRITING, photo.publish)
        draw_bookmark(painter, rects['keep'], self.WRITING, self.window_.in_portfolio(photo))
        # At most seven dots, the outer ones smaller when the carousel continues beyond them.
        count = len(self.deck)
        if count > 1:
            shown = min(7, count)
            first = max(0, min(self.index-shown//2, count-shown))
            y = rects['heart'].center().y()
            for n in range(shown):
                radius = 3.
                if (n == 0 and first > 0) or (n == shown-1 and first+shown < count):
                    radius = 1.8
                painter.setPen(Qt.PenStyle.NoPen)
                dot = QColor(self.WRITING)
                dot.setAlpha(255 if first+n == self.index else 80)
                painter.setBrush(dot)
                painter.drawEllipse(QPointF(post.center().x()+(n-(shown-1)/2)*10, y), radius, radius)
        font = QFont(self.font().family())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self.WRITING))
        line = QRectF(post.left()+14, media.bottom()+40, post.width()-28, 24)
        account = self.review.instagram_account()
        painter.drawText(line, Qt.AlignmentFlag.AlignVCenter, account)
        offset = painter.fontMetrics().horizontalAdvance(account)+7
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(self.SOFT))
        painter.drawText(line.adjusted(offset, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(photo.path.name, Qt.TextElideMode.ElideMiddle, int(line.width()-offset)))

    def action_at(self, position):
        return next((name for name, rect in self.action_rects().items() if name != 'comment' and rect.adjusted(-5, -5, 5, 5).contains(position)), None)

    def do_action(self, name):
        photo = self.current()
        if name == 'heart':
            self.window_.change([photo], liked=not photo.liked)
        elif name == 'send':
            self.window_.toggle_publish()
        else:
            self.window_.toggle_portfolio()
        self.last_clear = time.monotonic()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.current() is None:
            return
        self.finish_animation()
        position = event.position()
        action = self.action_at(position)
        if action:
            self.do_action(action)
            return
        left, right = self.chevrons()
        if len(self.deck) > 1 and left.contains(position):
            self.go(-1)
        elif len(self.deck) > 1 and right.contains(position):
            self.go(1)
        elif self.frames()[1].contains(position):
            self.moved = False
            if self.zoom_level > 1:
                self.pan_drag = position
            else:
                self.press = position
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        position = event.position()
        if self.pan_drag is not None:
            self.pan += position-self.pan_drag
            self.pan_drag = position
            self.update()
        elif self.press is not None:
            self.slide = position.x()-self.press.x()
            self.moved = self.moved or abs(self.slide) > 6
            self.update()
        else:
            left, right = self.chevrons()
            near = self.action_at(position) or left.contains(position) or right.contains(position)
            self.setCursor(Qt.CursorShape.PointingHandCursor) if near else self.unsetCursor()
            self.setToolTip({'send': MARK_TIPS['publish'], 'keep': MARK_TIPS['portfolio'], 'heart': MARK_TIPS['like']}.get(self.action_at(position), ''))

    def mouseReleaseEvent(self, event):
        self.pan_drag = None
        self.unsetCursor()
        if self.press is None:
            return
        self.press = None
        media = self.frames()[1]
        direction = -1 if self.slide > 0 else 1
        if abs(self.slide) > media.width()*.18 and 0 <= self.index+direction < len(self.deck):
            self.go(direction)
        else:
            self.animate_slide()

    def mouseDoubleClickEvent(self, event):
        if not self.action_at(event.position()) and not self.just_cleared() and self.current() and self.frames()[1].contains(event.position()):
            left, right = self.chevrons()
            if not left.contains(event.position()) and not right.contains(event.position()):
                self.window_.open_in_tri(self.current())

    def animate_slide(self):
        self.animation.stop()
        self.slide_from = self.slide
        self.animation.setStartValue(1.)
        self.animation.setEndValue(0.)
        self.animation.start()

    def on_animation(self, value):
        self.slide = self.slide_from*value
        self.update()

    def finish_animation(self):
        self.animation.stop()
        self.slide = 0.

    def go(self, delta):
        target = max(0, min(len(self.deck)-1, self.index+delta))
        if target == self.index:
            return
        media = self.frames()[1]
        # The photo that arrives comes from the side, wherever a drag had left it.
        self.slide += (target-self.index)*media.width()
        self.index = target
        self.pan = QPointF()
        self.animate_slide()
        self.settle()

    def navigate(self, delta):
        self.go(delta)

    def skip(self, delta=1):
        self.go(delta)

    def zoom(self, factor, anchor=None):
        previous = self.zoom_level
        self.zoom_level = max(.3, min(8., previous*factor))
        if self.zoom_level <= 1:
            self.pan = QPointF()
        else:
            ratio = self.zoom_level/previous
            center = self.frames()[1].center()
            self.pan = (anchor-center)*(1-ratio)+self.pan*ratio if anchor is not None else self.pan*ratio
        self.update()
        self.schedule_requests()
        self.review.sync_zoom()

    def zoom_reset(self):
        self.zoom(1/self.zoom_level)

    def zoom_text(self):
        return f'{round(self.zoom_level*100)} %'

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.12 if delta > 0 else 1/1.12, event.position())
        event.accept()

    def resizeEvent(self, event):
        self.schedule_requests()


class ReviewPage(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName('page')
        self.window = window
        self.settings = window.settings
        self.mono = False
        self.mono_cache = {}
        self.info = {}
        self.aspects = {}
        self.analysis = None
        self.analysis_token = 0
        self.analysis_pool = QThreadPool(self)
        self.analysis_pool.setMaxThreadCount(1)
        self.bottom_visible = True
        self.stack = QStackedWidget(self)
        self.views = {'toile': ToileView(self), 'swipe': SwipeView(self), 'color': WallView(self, True), 'wall': WallView(self),
                      'instagram': InstagramView(self)}
        for view in self.views.values():
            self.stack.addWidget(view)
        saved = self.settings.value('review_mode', 'wall')
        self.mode = saved if saved in self.views else 'wall'
        self.stack.setCurrentWidget(self.views[self.mode])
        self.bar = RatingBar(window, self)
        self.bar.rated.connect(window.rate)
        self.bar.marked.connect(window.set_pick)
        self.bar.portfolio.connect(window.toggle_portfolio)
        self.bar.publish.connect(window.toggle_publish)
        self.analysis_left = 0
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
        """What the other views share, in the side panel. Options of a view sit at the bottom."""
        self.note_filter = self.window.note_filter
        self.marker_filter = self.window.marker_filter
        self.tools = QWidget()
        lines = QVBoxLayout(self.tools)
        lines.setContentsMargins(0, 0, 0, 0)
        lines.addWidget(QLabel('Affichage', objectName='muted'))
        lines.setContentsMargins(0, 0, 0, 0)
        lines.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(4)
        lines.addLayout(row)
        mono_frame = QFrame(objectName='segments')
        mono_row = QHBoxLayout(mono_frame)
        mono_row.setContentsMargins(3, 3, 3, 3)
        self.mono_button = QPushButton('N&&B', objectName='seg', checkable=True)
        self.mono_button.setToolTip('Noir et blanc, pour juger les contrastes')
        self.mono_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mono_button.clicked.connect(self.toggle_mono)
        mono_row.addWidget(self.mono_button)
        row.addWidget(mono_frame)
        row.addStretch(1)
        self.option_button(row, '−', lambda *_: self.zoom(1/1.15)).setToolTip('Dézoomer')
        self.zoom_label = self.option_button(row, '100 %', self.zoom_reset)
        self.zoom_label.setFixedWidth(58)
        self.zoom_label.setToolTip('Revenir au zoom par défaut')
        self.option_button(row, '+', lambda *_: self.zoom(1.15)).setToolTip('Zoomer')
        self.picker = BackgroundPicker(self.window)
        lines.addWidget(self.picker)
        # Instagram has its own screen and post: there the background has no say.
        self.theme_row = QFrame(objectName='segments')
        row = QHBoxLayout(self.theme_row)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        group = QButtonGroup(self)
        self.light_button, self.dark_button = (self.theme_choice(row, group, text, value) for text, value in (('Mode clair', True), ('Mode sombre', False)))
        lines.addWidget(self.theme_row)

    def theme_choice(self, row, group, text, light):
        button = QPushButton(text, objectName='mainseg', checkable=True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda *_: self.views['instagram'].set_light(light))
        group.addButton(button)
        row.addWidget(button, 1)
        return button

    def option_pill(self):
        """A group of settings, stacked in the panel of the view."""
        pill = QFrame(self)
        row = QVBoxLayout(pill)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(5)
        pill.hide()
        return pill, row

    def option_combo(self, row, values, setting, default, width, slot):
        combo = QComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        """The settings of each view, shown in its panel: two groups per mode."""
        toile, wall, color = self.views['toile'], self.views['wall'], self.views['color']
        self.action_combos = []
        toile_pill, row = self.option_pill()
        self.option_button(row, 'Mélanger', self.shuffle)
        self.straight_button = self.option_button(row, 'Photos droites', toile.set_straight, True, toile.straight)
        row.addWidget(QLabel('En largeur', objectName='muted'))
        self.columns_slider = QSlider(Qt.Orientation.Horizontal)
        self.columns_slider.setRange(2, 16)
        self.columns_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.columns_slider.setValue(toile.columns)
        self.columns_slider.valueChanged.connect(self.set_columns)
        self.columns_label = QLabel(str(toile.columns))
        self.columns_label.setFixedWidth(18)
        line = QHBoxLayout()
        line.addWidget(self.columns_slider, 1)
        line.addWidget(self.columns_label)
        row.addLayout(line)
        swipe_left, row = self.option_pill()
        row.addWidget(QLabel('À gauche', objectName='muted'))
        self.swipe_left = self.action_combo(row, 'swipe_left')
        swipe_right, row = self.option_pill()
        row.addWidget(QLabel('À droite', objectName='muted'))
        self.swipe_right = self.action_combo(row, 'swipe_right')
        # In a selection, a swipe decides about one bucket.
        self.swipe_bucket_pill, row = self.option_pill()
        row.addWidget(QLabel('Bucket', objectName='muted'))
        self.swipe_bucket = QComboBox()
        self.swipe_bucket.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        row.addWidget(self.swipe_bucket)
        hint = QLabel('À gauche : retirer. À droite : ajouter.', objectName='muted')
        hint.setWordWrap(True)
        row.addWidget(hint)
        self.swipe_bucket_pill.hide()
        color_left, row = self.option_pill()
        row.addWidget(QLabel('Disposition', objectName='muted'))
        self.option_combo(row, [('staggered', 'Décalée'), ('rows', 'Rangées')], 'color_layout', 'rows', 100, self.set_color_layout)
        self.groups_button = self.option_button(row, 'Blocs de couleur', self.set_color_groups, True, color.groups)
        color_pill, row = self.option_pill()
        self.order_combo, self.order_button = self.sort_controls(row)
        self.close_color = self.option_button(row, 'Resserrer', self.close_gaps)
        self.close_color.setToolTip('Referme les vides laissés par les photos retirées')
        self.close_color.hide()
        wall_left, row = self.option_pill()
        row.addWidget(QLabel('Disposition', objectName='muted'))
        self.option_combo(row, [('staggered', 'Décalée'), ('rows', 'Rangées')], 'wall_layout', 'staggered', 100, self.set_wall_layout)
        # The staggered layout follows its modules: this choice only applies to rows.
        self.emphasis_combo = self.option_combo(row, [('equal', 'Formats égaux'), ('landscape', 'Horizontales en avant'), ('portrait', 'Verticales en avant')],
                                                'wall_emphasis', 'equal', 172, self.set_wall_emphasis)
        self.emphasis_combo.setVisible(wall.layout_kind == 'rows')
        row.addWidget(QLabel('Largeur', objectName='muted'))
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(35, 100)
        self.width_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.width_slider.setValue(wall.width_percent)
        self.width_slider.valueChanged.connect(self.set_wall_width)
        row.addWidget(self.width_slider)
        self.option_button(row, 'Mélanger', self.shuffle)
        self.wall_sort, self.wall_sort_button = self.sort_controls(row, shuffled=True)
        wall_right, row = self.option_pill()
        row.addWidget(QLabel('Sortie', objectName='muted'))
        self.drop_action = self.action_combo(row, 'drop_action')
        self.restore_button = self.option_button(row, '', self.restore_set_aside)
        self.restore_button.setToolTip("Réaffiche les photos sorties de la grille. Leurs notes ne changent pas : Ctrl+Z annule une note.")
        self.restore_button.hide()
        self.close_wall = self.option_button(row, 'Resserrer', self.close_gaps)
        self.close_wall.setToolTip('Referme les vides laissés par les photos retirées')
        self.close_wall.hide()
        insta_left, row = self.option_pill()
        row.addWidget(QLabel('Compte', objectName='muted'))
        self.account_edit = QLineEdit(self.instagram_account())
        self.account_edit.textEdited.connect(self.set_instagram_account)
        self.account_edit.returnPressed.connect(self.account_edit.clearFocus)
        row.addWidget(self.account_edit)
        self.sync_color_order()
        self.options = {'toile': (None, toile_pill), 'swipe': (swipe_left, swipe_right), 'color': (color_left, color_pill), 'wall': (wall_left, wall_right),
                        'instagram': (insta_left, None)}
        self.refresh_marker_options()

    def sort_controls(self, row, shuffled=False):
        """Trier par: the name is the plain grid, anything else its colored form. Offered in both."""
        row.addWidget(QLabel('Trier par', objectName='muted'))
        combo = QComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        combo.addItem('Nom', 'name')
        combo.addItem('Date', 'date')
        for label, one_way, *_ in COLOR_ORDERS:
            combo.addItem(label, one_way)
        if shuffled:
            combo.addItem('Mélangé', 'shuffled')
        combo.currentIndexChanged.connect(lambda *_: self.set_sort(combo.currentData()))
        row.addWidget(combo)
        button = self.option_button(row, '', self.flip_sort)
        return combo, button

    def set_sort(self, value):
        if value == 'shuffled':
            self.shuffle()
        elif value in ('name', 'date'):
            # Back to the plain order, after Mélanger too.
            self.views['wall'].order_seed = None
            sort = self.window.sort
            way = sort.currentData().split('_')[1]
            sort.setCurrentIndex(max(0, sort.findData(f'{value}_{way}')))
            self.set_mode('wall')
        else:
            self.set_color_order(value)
            if self.mode != 'color':
                self.set_mode('color')
        self.sync_color_order()

    def flip_sort(self, *args):
        if self.mode == 'color':
            self.flip_color_order()
        else:
            self.views['wall'].order_seed = None
            sort = self.window.sort
            kind, way = sort.currentData().split('_')
            sort.setCurrentIndex(max(0, sort.findData(f"{kind}_{'asc' if way == 'desc' else 'desc'}")))
        self.sync_color_order()

    def sync_swipe_bucket(self):
        selection = self.window.buckets
        if not selection:
            return
        wanted = self.swipe_bucket.currentData()
        rule = selection.rule()
        if len(rule['include']) == 1:
            wanted = rule['include'][0]
        self.swipe_bucket.blockSignals(True)
        self.swipe_bucket.clear()
        for bucket in selection.buckets:
            self.swipe_bucket.addItem(bucket['title'].replace('&', '&&'), bucket['id'])
        self.swipe_bucket.setCurrentIndex(max(0, self.swipe_bucket.findData(wanted)))
        self.swipe_bucket.blockSignals(False)

    def action_combo(self, row, setting):
        combo = QComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self.marker_filter.sync()

    def instagram_account(self):
        return self.settings.value('instagram_account', 'votre_compte') or 'votre_compte'

    def set_instagram_account(self, value):
        self.settings.setValue('instagram_account', value.strip())
        self.view().update()

    def sync_toolbar(self):
        self.picker.sync()
        self.picker.setVisible(self.mode != 'instagram')
        self.theme_row.setVisible(self.mode == 'instagram')
        (self.light_button if self.views['instagram'].light else self.dark_button).setChecked(True)
        self.sync_zoom()
        self.place_overlays()

    def background_menu(self):
        return self.window.viewer.background_menu(self)

    def set_bottom_visible(self, visible):
        """The bottom drop zone of the grid needs the room while a photo is dragged."""
        self.bottom_visible = visible
        self.place_overlays()

    def place_overlays(self):
        options = getattr(self, 'options', {})
        # In a selection, a photo dragged out of the grid goes to a bucket: no action to choose.
        selection = self.window.bucket_mode
        current = self.mode if self.window.review_active else None
        no_exit = options['wall'][1] if selection and 'wall' in options else None
        swiping = options.get('swipe', (None, None)) if selection else (None, None)
        for mode, group in options.items():
            for pill in group:
                if pill is not None:
                    pill.setVisible(mode == current and pill is not no_exit and pill not in swiping)
        if 'swipe' in options:
            self.swipe_bucket_pill.setVisible(selection and current == 'swipe')
        show_bar = self.bottom_visible and bool(self.targets())
        self.bar.setVisible(show_bar)
        self.bar.move((self.width()-self.bar.width())//2, self.height()-self.bar.height()-14)

    def resizeEvent(self, event):
        self.stack.setGeometry(self.rect())
        self.place_overlays()

    def view(self):
        return self.views[self.mode]

    def set_mode(self, mode):
        self.mode = mode
        self.settings.setValue('review_mode', mode)
        self.window.sync_view_buttons()
        self.stack.setCurrentWidget(self.views[mode])
        self.view().active = False
        self.sync_swipe_bucket()
        self.window.update_bucket_bars()
        self.sync_toolbar()
        self.view().rebuild()
        self.sync_bar()

    def enter(self):
        """Arriving from the Tri page: its current photo is not a choice made here."""
        self.view().active = False
        self.rebuild()
        self.sync_bar()

    def sync_bar(self):
        """The note and the name of a photo only show once there is a photo to name."""
        self.place_overlays()
        self.bar.update()
        self.window.update_counter()

    def rebuild(self):
        self.mono_cache.clear()
        self.sync_filters()
        self.view().rebuild()
        self.bar.relayout()
        self.place_overlays()
        self.bar.update()

    def photos_changed(self):
        self.view().photos_changed()
        self.sync_bar()

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
        self.sync_color_order()
        self.sync_bar()

    def zoom(self, factor):
        self.view().zoom(factor)

    def zoom_reset(self, *args):
        self.view().zoom_reset()

    def sync_zoom(self):
        self.zoom_label.setText(self.view().zoom_text())
        self.width_slider.blockSignals(True)
        self.width_slider.setValue(self.views['wall'].width_percent)
        self.width_slider.blockSignals(False)

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

    def set_columns(self, value):
        self.settings.setValue('toile_columns', value)
        self.columns_label.setText(str(value))
        self.views['toile'].set_columns(value)

    def set_color_layout(self, value):
        self.views['color'].layout_kind = value
        self.views['color'].relayout()
        self.sync_zoom()

    def sync_color_order(self):
        order = self.views['color'].order
        index = next((i for i, entry in enumerate(COLOR_ORDERS) if order in entry[1:3]), 0)
        entry = COLOR_ORDERS[index]
        other = order == entry[2]
        kind, way = self.window.sort.currentData().split('_')
        descending = way == 'desc'
        for combo, button, by_name in ((self.order_combo, self.order_button, False), (self.wall_sort, self.wall_sort_button, True)):
            combo.blockSignals(True)
            combo.setCurrentIndex((combo.findData('shuffled') if self.views['wall'].order_seed is not None else combo.findData(kind)) if by_name else combo.findData(entry[1]))
            combo.blockSignals(False)
            button.setText(('Décroissant' if descending else 'Croissant') if by_name else entry[4 if other else 3])
            plain = ('Du plus récent au plus ancien' if descending else 'Du plus ancien au plus récent') if kind == 'date' else 'De Z à A' if descending else 'De A à Z'
            button.setToolTip(plain if by_name else entry[6 if other else 5])
        # The blocks name colors: they mean nothing along another scale.
        self.groups_button.setVisible(index == 0)

    def set_color_order(self, value):
        self.views['color'].order = value
        self.settings.setValue('color_sort', value)
        self.sync_color_order()
        self.views['color'].rebuild()
        self.views['color'].bar.setValue(0)
        self.place_overlays()

    def flip_color_order(self, *args):
        order = self.views['color'].order
        entry = next(entry for entry in COLOR_ORDERS if order in entry[1:3])
        self.set_color_order(entry[2] if order == entry[1] else entry[1])

    def set_color_groups(self, value):
        self.views['color'].groups = value
        self.settings.setValue('color_groups', value)
        self.views['color'].relayout()
        self.views['color'].bar.setValue(0)

    def set_wall_layout(self, value):
        self.views['wall'].layout_kind = value
        self.views['wall'].relayout()
        self.emphasis_combo.setVisible(value == 'rows')
        self.place_overlays()
        self.sync_zoom()

    def set_wall_emphasis(self, value):
        self.views['wall'].emphasis = value
        self.views['wall'].relayout()

    def set_wall_width(self, value):
        self.views['wall'].set_width(value)

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
        if self.window.bucket_mode:
            title = self.swipe_bucket.currentText().replace('&&', '&')
            return f'Retirer de {title}', f'Ajouter à {title}'
        return self.swipe_left.currentText(), self.swipe_right.currentText()

    def apply_swipe(self, photo, direction):
        if self.window.bucket_mode:
            self.window.swipe_to_bucket(photo, self.swipe_bucket.currentData(), direction > 0)
            return
        self.apply_action(photo, (self.swipe_right if direction > 0 else self.swipe_left).currentData())

    def drop_label(self):
        return self.drop_action.currentText()

    def drop_out(self, photo):
        """A photo dragged out of the grid: apply the chosen action and take it away."""
        wall = self.views['wall']
        index = next((i for i, (_, p) in enumerate(wall.tiles) if p is photo), 0)
        wall.set_aside.add(photo.relative_path)
        self.apply_action(photo, self.drop_action.currentData())
        wall.photos_changed()
        if wall.tiles:
            self.window.select_photo(wall.tiles[min(index, len(wall.tiles)-1)][1])
        self.update_restore()

    def update_restore(self):
        count = len(self.views['wall'].set_aside)
        self.restore_button.setText(f'Réafficher ({count})')
        self.restore_button.setVisible(count > 0)
        for view, button in ((self.views['wall'], self.close_wall), (self.views['color'], self.close_color)):
            button.setVisible(len(view.placed) > len(view.tiles))
        self.place_overlays()

    def close_gaps(self, *args):
        self.view().rebuild()

    def restore_set_aside(self, *args):
        self.views['wall'].set_aside.clear()
        self.views['wall'].photos_changed()
        self.update_restore()
        self.window.save_timer.start(500)

    def after_mark(self, pick):
        """A marker set from the keyboard is a decision in Swipe: move on."""
        if self.mode == 'swipe' and pick in (PICK_SELECT, PICK_REJECT):
            self.views['swipe'].fly(1 if pick == PICK_SELECT else -1)

    def set_aside_again(self, photos, aside):
        """Out of the grid again or back in it, when a note that took a photo out is redone or undone."""
        wall = self.views['wall']
        keys = {photo.relative_path for photo in photos}
        current = wall.set_aside & keys
        if current != aside:
            wall.set_aside = (wall.set_aside-keys)|aside
            wall.photos_changed()
            self.update_restore()
        return current

    def after_undo(self, photos):
        if self.mode == 'swipe' and photos:
            self.view().show_photo(photos[0])
        elif self.mode in ('toile', 'wall', 'color') and len(photos) == 1 and any(photo is photos[0] for photo in self.window.photos):
            # Back under the eye, and picked again, so that the next key is for it.
            self.view().pick(photos[0])
            if self.mode == 'toile':
                self.view().bring_front(photos[0])

    def skip(self, delta):
        if self.mode in ('swipe', 'instagram'):
            self.view().skip(delta)
            self.bar.update()

    # What was chosen in this project, kept next to the session like the analysis.
    def state_path(self):
        return self.window.session.session_dir/STATE_FILE

    def save_state(self):
        choices = self.window.choices
        state = {'stars': self.window.stars.currentData(), 'flags': self.window.flags.currentData(),
                 'portfolio': choices.portfolio.isChecked(), 'publish': choices.publish.isChecked(), 'liked': choices.liked.isChecked(), 'project': choices.project.currentData(),
                 'set_aside': sorted(self.views['wall'].set_aside), 'view': self.mode if self.window.review_active else 'classic',
                 'photo': self.window.current_photo().relative_path if self.window.current_photo() else None}
        self.state_path().write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

    def restore_state(self):
        try:
            state = json.loads(self.state_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        for combo, key in ((self.window.stars, 'stars'), (self.window.flags, 'flags')):
            index = combo.findData(state.get(key)) if state.get(key) is not None else -1
            if index >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        choices = self.window.choices
        choices.portfolio.setChecked(bool(state.get('portfolio')))
        choices.publish.setChecked(bool(state.get('publish')))
        choices.liked.setChecked(bool(state.get('liked')))
        project = choices.project.findData(state.get('project'))
        if project >= 0:
            choices.project.blockSignals(True)
            choices.project.setCurrentIndex(project)
            choices.project.blockSignals(False)
        known = {photo.relative_path for photo in self.window.session.photos}
        self.views['wall'].set_aside = known & set(state.get('set_aside', []))
        self.update_restore()
        return state

    # Analysis cache, kept next to the session so that it travels with the project.
    def analysis_path(self):
        return self.window.session.session_dir/ANALYSIS_FILE

    def aspect(self, photo):
        info = self.info.get(photo.relative_path)
        aspect = info['aspect'] if info else self.aspects.get(photo.relative_path, 1.5)
        return max(.3, min(4., aspect))

    def start_analysis(self):
        self.stop_analysis()
        session = self.window.session
        try:
            self.info = json.loads(self.analysis_path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.info = {}
        self.aspects = {}
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
                known = self.info.get(photo.relative_path, {})
                if known.get('signature') != signature or known.get('version') != ANALYSIS_VERSION:
                    todo.append((photo.relative_path, photo.path, signature))
            if not todo:
                return
            self.analysis_left = len(todo)
            self.analysis = AnalysisJob(todo, token)
            self.analysis.signals.batch.connect(self.on_batch, Qt.ConnectionType.QueuedConnection)
            self.analysis.signals.aspects.connect(self.on_aspects, Qt.ConnectionType.QueuedConnection)
            self.analysis_pool.start(self.analysis, -20)
        QTimer.singleShot(0, launch)

    def stop_analysis(self):
        self.analysis_token += 1
        if self.analysis:
            self.analysis.cancelled.set()
            self.analysis = None
        self.analysis_left = 0

    def on_aspects(self, aspects, token):
        if token != self.analysis_token or self.window.session is None:
            return
        self.aspects = aspects
        self.views['toile'].formats_known()
        if self.window.review_active and self.mode in ('wall', 'color'):
            self.view().relayout()
        self.update_view()

    def on_batch(self, batch, done, total, token):
        if token != self.analysis_token or self.window.session is None:
            return
        self.info.update(batch)
        self.analysis_left = total-done
        if done >= total:
            self.analysis = None
            self.save_analysis()
        elif done % 160 == 0:
            self.save_analysis()
        self.update_view()
        # Only Couleur depends on the colors, and it must not reorder under a reader.
        color = self.views['color']
        if self.window.review_active and self.mode == 'color' and (done >= total or color.bar.value() == 0) and not self.relayout_timer.isActive():
            self.relayout_timer.start(700)

    def apply_analysis(self):
        if self.window.review_active and self.mode == 'color':
            self.view().rebuild()

    def save_analysis(self):
        try:
            self.analysis_path().write_text(json.dumps(self.info), encoding='utf-8')
        except OSError:
            pass

    def reset(self):
        self.stop_analysis()
        self.info = {}
        self.aspects = {}
        self.mono_cache.clear()
        toile, swipe = self.views['toile'], self.views['swipe']
        toile.positions, toile.stack, toile.dealt, toile.available = {}, [], [], set()
        toile.fresh = True
        swipe.deck, swipe.index, swipe.shuffled = [], 0, False
        swipe.zoom_level, swipe.pan = 1., QPointF()
        instagram = self.views['instagram']
        instagram.deck, instagram.index, instagram.zoom_level, instagram.pan, instagram.slide = [], 0, 1., QPointF(), 0.
        for wall in (self.views['wall'], self.views['color']):
            wall.ordered, wall.tiles, wall.placed = [], [], []
            wall.set_aside = set()
        self.update_restore()
