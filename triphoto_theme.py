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
ACCENT = '#d9b872'
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
QPushButton#seg {{ background: transparent; color: {MUTED}; border-radius: 6px; padding: 5px 13px; }}
QPushButton#seg:hover {{ color: {TEXT}; }}
QPushButton#seg:checked {{ background: {HOVER}; color: {TEXT}; }}
QFrame#segments {{ background: {RAISED}; border-radius: 8px; }}
QFrame#pill {{ background: rgba(22, 22, 23, 232); border: 1px solid rgba(255, 255, 255, 22); border-radius: 11px; }}
QFrame#toast {{ background: rgba(22, 22, 23, 240); border: 1px solid rgba(255, 255, 255, 30); border-radius: 9px; }}

QComboBox {{ background: {CONTROL}; border: none; border-radius: 7px; padding: 7px 6px 7px 11px; }}
QComboBox:hover {{ background: {HOVER}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url("{(ASSETS / 'chevron.svg').as_posix()}"); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {LINE}; outline: none; padding: 4px;
    selection-background-color: {HOVER}; selection-color: {TEXT}; }}
QMenu {{ background: {RAISED}; border: 1px solid {LINE}; padding: 5px; }}
QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: 5px; }}
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


def draw_stars(painter, x, y, stars, radius=6.0, gap=15.0, off=FAINT):
    """Vector stars stay sharp at every display scale. (x, y) is the first center."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for star in range(5):
        lit = star < stars
        painter.setPen(QPen(QColor(ACCENT if lit else off), 1.0))
        painter.setBrush(QColor(ACCENT) if lit else Qt.BrushStyle.NoBrush)
        painter.drawPolygon(star_polygon(QPointF(x+star*gap, y), radius))
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
