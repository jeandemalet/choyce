"""Palette, stylesheet and vector marks shared by the Qt pages.

The chrome stays strictly neutral: a tinted interface would bias the reading of
the photos. A single warm accent is kept for stars and active states.
"""
from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

ASSETS = Path(__file__).resolve().parent / 'assets'

INK = '#131314'
BASE = '#1a1a1b'
RAISED = '#232324'
CONTROL = '#2c2c2e'
HOVER = '#363638'
LINE = '#343436'
TEXT = '#ececec'
MUTED = '#9a9a9d'
FAINT = '#69696c'
# Neutral on purpose: no touch of color may disturb the reading of a photograph.
ACCENT = '#f2f2f2'
SELECT = '#8fc493'
REJECT = '#d9766a'

BACKGROUNDS = [('Blanc', '#ffffff'), ('Gris clair', '#bfbfbf'), ('Gris moyen', '#808080'),
               ('Gris foncé', '#404040'), ('Noir', '#000000')]

STYLE = f"""
* {{ font-family: 'Segoe UI Variable Text', 'Segoe UI', 'Helvetica Neue', Arial; font-size: 13px; }}
QMainWindow, QDialog, QStackedWidget, QSplitter {{ background: {INK}; color: {TEXT}; }}
QWidget {{ color: {TEXT}; }}
QLabel {{ background: transparent; }}
QLabel#brand {{ font-size: 15px; font-weight: 600; letter-spacing: 0.3px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#faint {{ color: {FAINT}; font-size: 12px; }}
QLabel#title {{ font-size: 26px; font-weight: 300; }}
QLabel#section {{ color: {MUTED}; font-size: 12px; font-weight: 600; letter-spacing: 0.6px; }}
QWidget#header {{ background: {BASE}; border-bottom: 1px solid #262627; }}
QWidget#home, QWidget#page {{ background: {INK}; }}

QPushButton {{ background: {CONTROL}; border: none; border-radius: 7px; padding: 7px 13px; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:disabled {{ color: {FAINT}; }}
QPushButton::menu-indicator {{ image: url("{(ASSETS / 'chevron.svg').as_posix()}"); width: 10px; height: 10px;
    subcontrol-position: right center; subcontrol-origin: padding; right: 9px; }}
QPushButton#menu {{ padding-right: 25px; }}
QPushButton#ghost {{ background: transparent; color: {MUTED}; padding: 7px 9px; }}
QPushButton#ghost:hover {{ background: {CONTROL}; color: {TEXT}; }}
QPushButton#primary {{ background: {TEXT}; color: {INK}; font-weight: 600; }}
QPushButton#primary:hover {{ background: #ffffff; }}
QPushButton#primary:disabled {{ background: #3a3a3c; color: #8a8a8e; }}
QPushButton#seg {{ background: transparent; color: {MUTED}; border-radius: 6px; padding: 5px 13px; }}
QPushButton#seg:hover {{ color: {TEXT}; }}
QPushButton#seg:checked {{ background: {HOVER}; color: {TEXT}; }}
QPushButton#mainseg {{ background: transparent; color: {MUTED}; border-radius: 6px; padding: 6px 10px; font-weight: 600; }}
QPushButton#mainseg:hover {{ color: {TEXT}; }}
QPushButton#mainseg:checked {{ background: {HOVER}; color: {TEXT}; }}
QPushButton#toggle {{ background: transparent; color: {MUTED}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 12px; }}
QPushButton#toggle:hover {{ color: {TEXT}; }}
QPushButton#toggle:checked {{ background: {HOVER}; border-color: {HOVER}; color: {TEXT}; }}
QFrame#segments {{ background: {RAISED}; border-radius: 8px; }}
QFrame#customRow {{ background: {RAISED}; border-radius: 8px; }}
QFrame#customRow:hover {{ background: {CONTROL}; }}
QCheckBox::indicator {{ width: 20px; height: 20px; border: 1px solid {FAINT}; border-radius: 5px; background: transparent; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{(ASSETS / 'check.svg').as_posix()}"); }}
QCheckBox::indicator:disabled {{ border-color: {LINE}; }}
QWidget#filterScrim {{ background: rgba(0, 0, 0, 120); }}
QPushButton#state {{ background: transparent; color: {FAINT}; border-radius: 6px; padding: 5px 12px; }}
QPushButton#state:hover {{ color: {TEXT}; }}
QPushButton#state:checked {{ background: {HOVER}; color: {MUTED}; }}
QPushButton#stateOn {{ background: transparent; color: {FAINT}; border-radius: 6px; padding: 5px 12px; }}
QPushButton#stateOn:hover {{ color: {TEXT}; }}
QPushButton#stateOn:checked {{ background: {ACCENT}; color: {INK}; font-weight: 600; }}
QPushButton#stateOff {{ background: transparent; color: {FAINT}; border-radius: 6px; padding: 5px 12px; }}
QPushButton#stateOff:hover {{ color: {TEXT}; }}
QPushButton#stateOff:checked {{ background: {REJECT}; color: {INK}; font-weight: 600; }}
QFrame#pill {{ background: rgba(22, 22, 23, 232); border: 1px solid rgba(255, 255, 255, 22); border-radius: 11px; }}
QFrame#dialog {{ background: {RAISED}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#dock {{ background: {BASE}; border-left: 1px solid #262627; }}
QLabel#dialogTitle {{ font-size: 15px; font-weight: 600; }}
QProgressBar {{ background: {HOVER}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {TEXT}; border-radius: 3px; }}
QFrame#toast {{ background: rgba(22, 22, 23, 240); border: 1px solid rgba(255, 255, 255, 30); border-radius: 9px; }}

QComboBox {{ background: {CONTROL}; border: none; border-radius: 7px; padding: 7px 6px 7px 11px; }}
QComboBox:hover {{ background: {HOVER}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url("{(ASSETS / 'chevron.svg').as_posix()}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {LINE}; outline: none; padding: 4px;
    selection-background-color: {HOVER}; selection-color: {TEXT}; }}
QMenu {{ background: {RAISED}; border: 1px solid {LINE}; padding: 5px; }}
QMenu::item {{ padding: 7px 26px 7px 30px; border-radius: 5px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 9px; }}
QMenu::indicator:checked {{ image: url("{(ASSETS / 'check_light.svg').as_posix()}"); }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}

QListWidget {{ background: {BASE}; border: none; outline: none; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 3px 1px; }}
QScrollBar::handle:vertical {{ background: #4a4a4c; border-radius: 3px; min-height: 35px; }}
QScrollBar::handle:vertical:hover {{ background: #616164; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QSplitter::handle {{ background: {INK}; width: 2px; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; }}
QFrame#card {{ background: {BASE}; border-radius: 10px; }}
QFrame#card:hover {{ background: {RAISED}; }}
QFrame#featured {{ background: {BASE}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#featured:hover {{ background: {RAISED}; border-color: #57575a; }}
QTableWidget {{ background: {BASE}; border: none; gridline-color: transparent; outline: none; }}
QHeaderView::section {{ background: {BASE}; color: {MUTED}; border: none; padding: 6px; }}
QKeySequenceEdit, QLineEdit {{ background: {CONTROL}; border: none; border-radius: 5px; padding: 3px; }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 12px; height: 12px; border-radius: 7px; border: 1px solid #5a5a5d; background: transparent; }}
QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 3px; background: {HOVER}; border-radius: 1px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }}
"""


def star_polygon(center, radius):
    points = QPolygonF()
    for vertex in range(10):
        angle = -math.pi/2 + vertex*math.pi/5
        length = radius if vertex % 2 == 0 else radius*.44
        points.append(center + QPointF(math.cos(angle)*length, math.sin(angle)*length))
    return points


def draw_stars(painter, x, y, stars, radius=6.0, gap=15.0):
    """Vector stars stay sharp at every display scale. (x, y) is the first center."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for star in range(5):
        lit = star < stars
        painter.setPen(QPen(QColor(ACCENT) if lit else QColor(255, 255, 255, 120), 1.0))
        painter.setBrush(QColor(ACCENT) if lit else Qt.BrushStyle.NoBrush)
        painter.drawPolygon(star_polygon(QPointF(x+star*gap, y), radius))
    painter.restore()


def draw_clear_star(painter, x, y, radius, color):
    """Empty star struck through: the button that takes a photo back to zero stars."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(color), 1.0))
    painter.drawPolygon(star_polygon(QPointF(x, y), radius))
    painter.setPen(QPen(QColor(color), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(x-radius, y+radius), QPointF(x+radius, y-radius))
    painter.restore()


def draw_heart(painter, rect, filled):
    """White outline, filled when liked. A thin dark edge keeps it readable on a light photo."""
    r = QRectF(rect)
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    path = QPainterPath(QPointF(x+w/2, y+h))
    path.cubicTo(x-w*.2, y+h*.5, x+w*.08, y-h*.14, x+w/2, y+h*.24)
    path.cubicTo(x+w*.92, y-h*.14, x+w*1.2, y+h*.5, x+w/2, y+h)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(0, 0, 0, 110), 2.6))
    painter.drawPath(path)
    painter.setBrush(QColor(255, 255, 255) if filled else Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(255, 255, 255), 1.2))
    painter.drawPath(path)
    painter.restore()


def draw_flag(painter, rect, color):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(rect)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(r.left()+r.width()*.25, r.top()), QPointF(r.left()+r.width()*.25, r.bottom()))
    cloth = QPolygonF([QPointF(r.left()+r.width()*.25, r.top()), QPointF(r.right(), r.top()+r.height()*.27),
                       QPointF(r.left()+r.width()*.25, r.top()+r.height()*.55)])
    painter.setBrush(QColor(color))
    painter.drawPolygon(cloth)
    painter.restore()


def draw_check(painter, rect, color):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(rect)
    path = QPainterPath(QPointF(r.left(), r.top()+r.height()*.55))
    path.lineTo(r.left()+r.width()*.38, r.bottom()-r.height()*.08)
    path.lineTo(r.right(), r.top()+r.height()*.12)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(path)
    painter.restore()


def draw_bookmark(painter, rect, color, filled=False):
    """The portfolio: the same mark as the "save" of Instagram."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(rect)
    inset = r.width()*.12
    points = QPolygonF([QPointF(r.left()+inset, r.top()), QPointF(r.right()-inset, r.top()), QPointF(r.right()-inset, r.bottom()),
                        QPointF(r.center().x(), r.bottom()-r.height()*.28), QPointF(r.left()+inset, r.bottom())])
    painter.setBrush(QColor(color) if filled else Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPolygon(points)
    painter.restore()


def pastel(color):
    """A light tone of a bucket color, which stays discreet beside the photos."""
    tone = QColor(color)
    return QColor((tone.red()+2*255)//3, (tone.green()+2*255)//3, (tone.blue()+2*255)//3)


def draw_tick(painter, x, y, size, color):
    """A vector tick whose top left corner is at (x, y)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(QPointF(x, y+size*.55))
    path.lineTo(x+size*.36, y+size*.9)
    path.lineTo(x+size, y+size*.12)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(color), 1.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPath(path)
    painter.restore()


def draw_bucket_chips(painter, x, y, width, chips):
    """The buckets of a photo, as (title, color), on small dark plates. Those without room are left out."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = painter.font()
    font.setPixelSize(10)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    limit = x+width
    for title, color in chips:
        text = metrics.elidedText(title, Qt.TextElideMode.ElideRight, 90)
        chip = QRectF(x, y, metrics.horizontalAdvance(text)+12, 17)
        if chip.right() > limit:
            break
        painter.setBrush(QColor(15, 15, 16, 205))
        painter.setPen(QPen(pastel(color), 1))
        painter.drawRoundedRect(chip, 5, 5)
        painter.setPen(pastel(color))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
        x = chip.right()+4
    painter.restore()


def draw_send(painter, rect, color, filled=False):
    """À publier: the paper plane of Instagram."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(rect)
    tip = QPointF(r.right(), r.top())
    # Slanted, with a deep notch: with a flat top it reads as a funnel once small and filled.
    notch = QPointF(r.left()+r.width()*.4, r.top()+r.height()*.6)
    points = QPolygonF([QPointF(r.left(), r.top()+r.height()*.38), tip, QPointF(r.left()+r.width()*.62, r.bottom()), notch])
    painter.setBrush(QColor(color) if filled else Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawPolygon(points)
    painter.drawLine(notch, tip)
    painter.restore()


def draw_comment(painter, rect, color):
    """A speech bubble, for the likeness of an Instagram post."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(rect)
    path = QPainterPath()
    path.addEllipse(r)
    tail = QPolygonF([QPointF(r.left()+r.width()*.1, r.top()+r.height()*.68), QPointF(r.left(), r.bottom()),
                      QPointF(r.left()+r.width()*.32, r.top()+r.height()*.9)])
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(color), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    tailed = QPainterPath()
    tailed.addPolygon(tail)
    tailed.closeSubpath()
    painter.drawPath(path.united(tailed))
    painter.restore()


def draw_cross(painter, rect, color):
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(rect).adjusted(1, 1, -1, -1)
    painter.setPen(QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(r.topLeft(), r.bottomRight())
    painter.drawLine(r.topRight(), r.bottomLeft())
    painter.restore()


def contrast_on(color):
    """Readable text color over a user-chosen background."""
    return '#2a2a2a' if QColor(color).lightnessF() > .55 else '#b4b4b6'
