"""Non-regression tests, run with: py -3 tests/test_triphoto.py

They work on synthetic images in a temporary folder, with separate settings, and
never touch real galleries or the user's preferences.
"""
import time
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

os.environ['TRIPHOTO_SETTINGS'] = 'TriPhotoTests'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QEventLoop, QPoint, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

import triphoto
import triphoto_custom
import triphoto_portfolio
import triphoto_prefs
import triphoto_qt
import triphoto_review
import triphoto_update

COLORS = [(200, 40, 40), (40, 180, 60), (40, 60, 200), (220, 200, 40), (128, 128, 128),
          (200, 90, 30), (30, 170, 170), (150, 40, 170)]


def make_project(root, count=24):
    root.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        size = (900, 600) if index % 4 else (600, 900)
        Image.new('RGB', size, COLORS[index % len(COLORS)]).save(root/f'photo_{index:03d}.jpg', quality=85)
    return root


def wait(ms=150):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def settle(window, condition, timeout=8000):
    waited = 0
    while not condition() and waited < timeout:
        wait(50)
        waited += 50
    assert condition(), 'condition not reached in time'


def clear_settings():
    triphoto_qt.app_settings().clear()
    shutil.rmtree(triphoto_custom.Store(triphoto_qt.SETTINGS_SCOPE).folder, ignore_errors=True)


def legacy_session(root, flagged=(), stars=None):
    """A session.json exactly as the version of 2 October 2026 wrote it."""
    photos = sorted(p.name for p in root.glob('*.jpg'))
    stars = stars or {}
    payload = {'application': 'TriPhoto', 'version': 1, 'created_at': '2026-10-02T15:07:51+0200',
               'updated_at': '2026-10-02T16:22:38+0200', 'root': str(root).lower(), 'sort_mode': 'name_desc',
               'color_management': 'x', 'originals_policy': 'x', 'inventory': {},
               'photos': [{'relative_path': name, 'stars': stars.get(name, 0), 'flagged': name in flagged} for name in photos]}
    directory = root/'TriPhoto_Session'
    directory.mkdir(exist_ok=True)
    (directory/'session.json').write_text(json.dumps(payload), encoding='utf-8')
    return photos


def test_legacy_session(base):
    root = make_project(base/'legacy', 6)
    names = legacy_session(root, flagged={'photo_001.jpg'}, stars={'photo_002.jpg': 5})
    before = (root/'photo_000.jpg').read_bytes()
    session = triphoto.PhotoSession(root)
    by_name = {p.relative_path: p for p in session.photos}
    assert session.sort_mode == 'name_desc'
    assert by_name['photo_001.jpg'].pick == triphoto.PICK_SELECT and by_name['photo_001.jpg'].flagged
    assert by_name['photo_002.jpg'].stars == 5 and by_name['photo_002.jpg'].pick == triphoto.PICK_NEUTRAL
    assert (root/'TriPhoto_Session'/'session.v1.bak.json').exists()
    by_name['photo_003.jpg'].pick = triphoto.PICK_REJECT
    session.save()
    data = json.loads((root/'TriPhoto_Session'/'session.json').read_text(encoding='utf-8'))
    items = {p['relative_path']: p for p in data['photos']}
    assert data['version'] == triphoto.SESSION_VERSION and len(items) == len(names)
    # Older versions read "flagged": it must stay accurate.
    assert items['photo_001.jpg']['flagged'] is True and items['photo_003.jpg']['flagged'] is False
    assert items['photo_003.jpg']['pick'] == 'reject'
    restored = {p.relative_path: p for p in triphoto.PhotoSession(root).photos}
    assert restored['photo_003.jpg'].pick == triphoto.PICK_REJECT and restored['photo_001.jpg'].flagged
    assert (root/'photo_000.jpg').read_bytes() == before


def test_nested_session_ignored(base):
    root = make_project(base/'nested', 2)
    export = root/'Output'/'TriPhoto_Session'/'Export_5_Etoiles'
    export.mkdir(parents=True)
    Image.new('RGB', (10, 10)).save(export/'copy.jpg')
    assert len(triphoto.PhotoSession(root).photos) == 2


def test_dominant_color(base):
    path = base/'red.jpg'
    Image.new('RGB', (800, 500), (210, 30, 30)).save(path)
    info = triphoto_review.analyse_photo(path)
    assert not info['neutral'] and abs(info['aspect']-1.6) < .01 and info['rgb'][0] > 150
    grey = base/'grey.jpg'
    Image.new('RGB', (800, 500), (128, 128, 128)).save(grey)
    assert triphoto_review.analyse_photo(grey)['neutral']
    assert triphoto_review.color_key(info) < triphoto_review.color_key(triphoto_review.analyse_photo(grey))


def test_recents(base):
    clear_settings()
    recents = triphoto_prefs.Recents(triphoto_qt.app_settings())
    first, second = base/'recent_a', base/'recent_b'
    recents.add(first, [], 100)
    recents.add(second, [], 200)
    recents.add(first, [], 300)
    assert [Path(e['path']) for e in recents.entries()] == [first, second]
    recents.remove(second)
    assert len(recents.entries()) == 1
    # An unavailable project stays listed: it may be on an unplugged disk.
    assert recents.merge([{'path': 'Z:/absent', 'opened': 50}]) == 1
    assert recents.merge([{'path': 'Z:/absent', 'opened': 50}]) == 0
    root = make_project(base/'scan'/'mariage'/'Output', 3)
    legacy_session(root, stars={'photo_000.jpg': 5})
    found = triphoto_prefs.find_sessions(base/'scan')
    assert len(found) == 1 and Path(found[0]['path']) == root and found[0]['count'] == 3 and found[0]['five'] == 1
    assert found[0]['cover'] == 'photo_000.jpg'
    clear_settings()


def test_shortcuts():
    clear_settings()
    settings = triphoto_qt.app_settings()
    mapping = triphoto_prefs.shortcut_map(settings)
    assert not triphoto_prefs.conflicts(mapping), triphoto_prefs.conflicts(mapping)
    mapping['select'] = ['F']
    assert 'F' in triphoto_prefs.conflicts(mapping)
    mapping = triphoto_prefs.shortcut_map(settings)
    mapping['rate5'] = ['K']
    triphoto_prefs.save_shortcut_map(settings, mapping)
    assert triphoto_prefs.shortcut_map(settings)['rate5'] == ['K']
    assert triphoto_prefs.shortcut_map(settings)['prev'] == ['C', 'Left']
    clear_settings()


def open_window(root):
    window = triphoto_qt.Window(root)
    window.resize(1400, 900)
    window.show()
    settle(window, lambda: window.current_photo() is not None)
    return window


def test_window_rating_and_undo(base):
    clear_settings()
    root = make_project(base/'rating')
    window = open_window(root)
    QTest.qWait(50)
    photo = window.current_photo()
    window.rate(4)
    assert photo.stars == 4
    window.set_pick(triphoto.PICK_REJECT)
    assert photo.pick == triphoto.PICK_NEUTRAL, 'reject needs the three-state marker'
    window.undo()
    assert photo.stars == 0
    window.toggle_flag()
    assert photo.flagged
    window.marker = 'pick'
    window.apply_marker()
    window.set_pick(triphoto.PICK_REJECT)
    assert photo.pick == triphoto.PICK_REJECT
    window.flags.setCurrentIndex(window.flags.findData('unrejected'))
    assert photo not in window.photos and len(window.photos) == 23
    window.flags.setCurrentIndex(0)
    window.stars.setCurrentIndex(window.stars.findData('ge3'))
    assert not window.photos
    window.stars.setCurrentIndex(0)
    window.close()
    data = json.loads((root/'TriPhoto_Session'/'session.json').read_text(encoding='utf-8'))
    assert sum(p['pick'] == 'reject' for p in data['photos']) == 1
    clear_settings()


def test_review_modes(base):
    clear_settings()
    root = make_project(base/'review')
    window = open_window(root)
    window.marker = 'pick'
    window.apply_marker()
    window.set_review(True)
    review = window.review
    assert window.pages.currentIndex() == 2
    settle(window, lambda: len(review.info) == 24)
    assert (root/'TriPhoto_Session'/'colors.json').exists()
    for mode, _ in triphoto_review.MODES:
        review.set_mode(mode)
        wait(60)
        assert review.view().grab().width() > 0
    # Color wall: neutrals last, hue order first.
    review.set_mode('color')
    settle(window, lambda: len(review.view().tiles) == 24)
    names = [photo.relative_path for _, photo in review.view().tiles]
    assert review.info[names[-1]]['neutral'] and not review.info[names[0]]['neutral']
    # Filters drive the review, stars first.
    window.photos[0].stars = 5
    window.stars.setCurrentIndex(window.stars.findData('ge4'))
    assert len(review.view().tiles) == 1
    window.stars.setCurrentIndex(0)
    # The review's own filter bar drives the same filter.
    review.note_filter.buttons[5].click()
    assert window.stars.currentData() == 5 and len(window.photos) == 1
    review.note_filter.buttons['all'].click()
    assert len(window.photos) == 24
    # Swipe keeps its deck through decisions, and undo returns to the card.
    review.set_mode('swipe')
    swipe = review.view()
    review.swipe_left.setCurrentIndex(review.swipe_left.findData('reject'))
    review.swipe_right.setCurrentIndex(review.swipe_right.findData('select'))
    window.flags.setCurrentIndex(window.flags.findData('unrejected'))
    deck = list(swipe.deck)
    first = swipe.current()
    window.navigate(-1)
    settle(window, lambda: swipe.index == 1)
    assert first.pick == triphoto.PICK_REJECT and swipe.current() is deck[1] and first not in window.photos
    assert len(swipe.deck) == len(deck)
    window.rate(3)
    assert deck[1].stars == 3 and first.stars == 5
    window.undo()
    window.undo()
    settle(window, lambda: swipe.index == 0)
    assert first.pick == triphoto.PICK_NEUTRAL
    window.flags.setCurrentIndex(0)
    # Dragging far enough decides, a short drag springs back. Actions are free to choose.
    swipe = review.view()
    swipe.resize(1200, 800)
    start = swipe.index
    swipe.press = QPointF(600, 400)
    swipe.offset = QPointF(40, 0)
    swipe.mouseReleaseEvent(_Event())
    settle(window, lambda: swipe.offset.x() == 0, 3000)
    assert swipe.index == start and swipe.offset.x() == 0
    review.swipe_right.setCurrentIndex(review.swipe_right.findData('star4'))
    target = swipe.current()
    swipe.press = QPointF(600, 400)
    swipe.offset = QPointF(500, 0)
    swipe.mouseReleaseEvent(_Event())
    settle(window, lambda: swipe.index == start+1)
    assert target.stars == 4
    # Toile: a limited hand; a note keeps it in place, shuffle deals again, straight is a display choice.
    review.set_mode('toile')
    toile = review.view()
    toile.set_columns(4)
    assert len(toile.stack) == 24 and len(toile.positions) == 24
    # All the photos, so many across: the width fits, the rest continues below.
    # An invisible grid: each photo in its cell, however narrow it is, and nothing overlaps.
    cell = max(toile.tile_size(p)[0] for p in toile.dealt)+toile.GAP
    for index, photo in enumerate(toile.dealt):
        center = (index % 4-1.5)*cell
        assert center-cell/2 < toile.tile_rect(photo).left() and toile.tile_rect(photo).right() < center+cell/2
    assert toile.bounds().width() >= cell*4-toile.GAP-cell, 'the table keeps its width'
    assert toile.tile_rect(toile.dealt[4]).top() > toile.tile_rect(toile.dealt[0]).top(), 'four across, then the next row'
    assert not review.bar.isVisible() and window.rate(5) is None and not toile.targets()
    key = toile.stack[0].relative_path
    position = list(toile.positions[key])
    toile.pick(toile.stack[0])
    assert review.bar.isVisible()
    window.rate(2)
    assert toile.positions[key] == position and len(toile.stack) == 24
    toile.set_straight(True)
    wait(40)
    toile.grab()
    review.shuffle()
    assert toile.positions.get(key) != position
    # Background and black and white.
    window.set_background('#ffffff')
    assert window.viewer.background == '#ffffff' and any(s.isChecked() for s in review.picker.swatches)
    review.toggle_mono()
    wait(80)
    toile.grab()
    review.toggle_mono()
    window.set_review(False)
    assert window.pages.currentIndex() == 1
    window.close()
    clear_settings()


class _Move:
    def __init__(self, position, widget):
        self._position, self._global = position, widget.mapToGlobal(position)

    def position(self):
        return self._position

    def globalPosition(self):
        return QPointF(self._global)


class _Event:
    def position(self):
        return QPointF(0, 0)


def test_grid(base):
    clear_settings()
    root = make_project(base/'grid', 40)
    window = open_window(root)
    window.set_review(True)
    review = window.review
    settle(window, lambda: len(review.info) == 40)
    review.set_mode('wall')
    wall = review.view()
    wall.resize(1400, 850)
    def no_overlap():
        rects = [rect for rect, _ in wall.tiles]
        return all(not a.adjusted(1, 1, -1, -1).intersects(b.adjusted(1, 1, -1, -1)) for i, a in enumerate(rects) for b in rects[i+1:])
    for layout in ('staggered', 'rows'):
        for emphasis in ('equal', 'landscape', 'portrait'):
            review.set_wall_layout(layout)
            review.set_wall_emphasis(emphasis)
            assert len(wall.tiles) == 40 and len({p.relative_path for _, p in wall.tiles}) == 40
            assert no_overlap(), (layout, emphasis)
            assert all(r.height() <= wall.height() for r, _ in wall.tiles)
            ratio = {p.relative_path: r.width()/r.height() for r, p in wall.tiles}
            assert all(abs(ratio[p.relative_path]-review.aspect(p)) < .02 for p in window.photos), 'photos must not be cropped'
    review.set_wall_layout('rows')
    review.set_wall_emphasis('landscape')
    sizes = {o: sum(r.width()*r.height() for r, p in wall.tiles if (review.aspect(p) >= 1) == o) / max(1, sum((review.aspect(p) >= 1) == o for _, p in wall.tiles)) for o in (True, False)}
    assert sizes[True] > sizes[False]*1.5
    # Rows keep the order of the photos, and are set to the height of one orientation.
    assert [p.relative_path for _, p in wall.tiles] == [p.relative_path for p in wall.ordered]
    def row_heights(kind):
        tops = {}
        for rect, photo in wall.tiles:
            tops.setdefault(round(rect.center().y()), []).append((rect, photo))
        return all(max(r.height() for r, p in row if (review.aspect(p) >= 1) == kind) >= max(r.height() for r, _ in row)-.5
                   for row in tops.values() if any((review.aspect(p) >= 1) == kind for _, p in row))
    assert row_heights(True)
    review.set_wall_emphasis('portrait')
    assert row_heights(False)
    assert all(abs(max(r.width(), r.height())-max(q.width(), q.height())) < .5 for r, _ in wall.tiles[:2] for q, _ in wall.tiles[:2])
    # This make-up of photos is mostly horizontal: equal formats follow the horizontals.
    review.set_wall_emphasis('landscape')
    landscape = [tuple(r.getRect()) for r, _ in wall.tiles]
    review.set_wall_emphasis('equal')
    assert [tuple(r.getRect()) for r, _ in wall.tiles] == landscape
    assert review.emphasis_combo.isVisibleTo(review.options['wall'][0])
    review.set_wall_layout('staggered')
    assert not review.emphasis_combo.isVisibleTo(review.options['wall'][0])
    review.set_wall_layout('rows')
    review.set_wall_emphasis('landscape')
    # Narrowing the central panel leaves margins, and the layout follows.
    review.set_wall_layout('staggered')
    review.set_wall_emphasis('equal')
    review.set_wall_width(50)
    assert max(r.right() for r, _ in wall.tiles)-min(r.left() for r, _ in wall.tiles) <= wall.width()*.52
    assert review.width_slider.value() == 50
    # Shuffle changes the order, without losing a photo.
    before = [p.relative_path for _, p in wall.tiles]
    review.shuffle()
    after = [p.relative_path for _, p in wall.tiles]
    assert before != after and sorted(before) == sorted(after)
    # Dragging a photo out applies the chosen action and takes it out of the grid.
    review.drop_action.setCurrentIndex(review.drop_action.findData('star1'))
    wall.bar.setValue(0)
    rect, photo = wall.visible_tiles()[0]
    center = rect.center().toPoint()-wall.bar.value()*QPointF(0, 1).toPoint()
    QTest.mousePress(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.mouseMove(wall, center+QPointF(40, 0).toPoint())
    assert wall.dragging and not review.bar.isVisible()
    wall.grab()
    QTest.mouseMove(wall, QPointF(10, 400).toPoint())
    QTest.mouseRelease(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPointF(10, 400).toPoint())
    assert not wall.dragging and review.bar.isVisible()
    # A click in the empty margin takes the selection back, and with it the note.
    assert photo.stars == 1 and len(wall.tiles) == 39 and photo.relative_path in wall.set_aside
    # The others do not move: the place stays empty until asked to close.
    places = {p.relative_path: tuple(r.getRect()) for r, p in wall.placed}
    assert all(places[p.relative_path] == tuple(r.getRect()) for r, p in wall.tiles)
    assert review.close_wall.isVisibleTo(review.options['wall'][1])
    assert review.restore_button.isVisibleTo(review.options['wall'][1])
    window.undo()
    assert photo.stars == 0 and len(wall.tiles) == 40
    # Released inside the grid, nothing happens. Undo may have moved the scroll position.
    wall.bar.setValue(0)
    rect, photo = wall.visible_tiles()[0]
    center = rect.center().toPoint()
    QTest.mousePress(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    QTest.mouseMove(wall, center+QPointF(30, 30).toPoint())
    QTest.mouseRelease(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center+QPointF(30, 30).toPoint())
    assert len(wall.tiles) == 40 and photo.stars == 0
    # Closing the gaps relays everything out, without the photos set aside.
    rect, photo = wall.visible_tiles()[0]
    window.change([photo], stars=5)
    window.stars.setCurrentIndex(window.stars.findData(5))
    assert len(wall.tiles) == 1
    window.change([photo], stars=0)
    assert not wall.tiles and review.close_wall.isVisibleTo(review.options['wall'][1])
    window.undo()
    assert len(wall.tiles) == 1
    window.stars.setCurrentIndex(0)
    assert not review.close_wall.isVisibleTo(review.options['wall'][1])
    # Options belong to their mode, at the bottom.
    assert review.options['wall'][0].isVisibleTo(review) and not review.options['toile'][1].isVisibleTo(review)
    review.set_mode('color')
    assert review.options['color'][1].isVisibleTo(review)
    # Order and blocks of the Couleur mode.
    color = review.view()
    color.resize(1400, 850)
    review.set_color_groups(True)
    assert color.headings and sum(count for _, _, count in color.headings) == 40
    assert color.headings[-1][1] == 'Neutres' and color.headings[0][1] == 'Rouges'
    assert all(a[0].bottom() < b[0].top() for a, b in zip(color.headings, color.headings[1:]))
    color.grab()
    review.set_color_order('hue_desc')
    assert color.headings[0][1] != 'Rouges' and color.headings[-1][1] == 'Neutres'
    # The order is a criterion and a direction.
    review.flip_color_order()
    assert color.order == 'hue' and review.order_button.text() == 'Croissant' and review.order_button.toolTip() == 'Du rouge au violet'
    review.order_combo.setCurrentIndex(review.order_combo.findData('light'))
    assert color.order == 'light' and review.order_button.text() == 'Décroissant'
    lights = [review.info[p.relative_path]['light'] for p in color.ordered]
    assert lights == sorted(lights, reverse=True)
    review.set_color_order('hue')
    # The blocks also work in the staggered layout.
    review.set_color_layout('staggered')
    assert sum(count for _, _, count in color.headings) == 40 and len(color.tiles) == 40
    color.grab()
    review.set_color_layout('rows')
    # The cursor takes the color of what is in the middle of the page.
    color.bar.setValue(0)
    first = color.bar.styleSheet()
    color.bar.setValue(color.bar.maximum())
    assert first and color.bar.styleSheet() != first
    review.set_color_groups(False)
    assert not color.headings
    window.close()
    clear_settings()


class _Wheel:
    def __init__(self, delta, modifiers=Qt.KeyboardModifier.NoModifier):
        self.delta, self.keys = delta, modifiers

    def angleDelta(self):
        return QPointF(0, self.delta).toPoint()

    def modifiers(self):
        return self.keys

    def position(self):
        return QPointF(300, 300)

    def accept(self):
        pass


def test_layout_stable_during_analysis(base):
    clear_settings()
    root = base/'stable'
    root.mkdir()
    for index in range(60):
        size = (900, 600) if (index*7) % 3 else (600, 900)
        Image.new('RGB', size, COLORS[index % len(COLORS)]).save(root/f'photo_{index:03d}.jpg', quality=70)
    real = triphoto_review.analyse_photo
    gate = threading.Event()

    def held(path):
        # The colors stay on hold until the page has been read for a while.
        gate.wait(20)
        return real(path)
    triphoto_review.analyse_photo = held
    try:
        window = open_window(root)
        window.set_review(True)
        review = window.review
        settle(window, lambda: len(review.aspects) == 60)
        assert not review.info
        assert review.aspect(next(p for p in window.photos if p.relative_path == 'photo_000.jpg')) < 1
        review.set_mode('wall')
        wall = review.view()
        wall.resize(1400, 850)
        wall.bar.setValue(wall.bar.maximum()//2)
        top = wall.bar.value()
        places = {p.relative_path: tuple(r.getRect()) for r, p in wall.placed}
        gate.set()
        settle(window, lambda: len(review.info) == 60)
        wait(900)
        # All the colors have arrived: nothing has moved under the reader.
        assert wall.bar.value() == top
        assert {p.relative_path: tuple(r.getRect()) for r, p in wall.placed} == places
    finally:
        gate.set()
        triphoto_review.analyse_photo = real
    window.close()
    clear_settings()


def test_toile_loading(base):
    clear_settings()
    root = base/'toile_loading'
    root.mkdir()
    for index in range(20):
        Image.new('RGB', (2600, 1733) if index % 4 else (1733, 2600), COLORS[index % len(COLORS)]).save(root/f'photo_{index:03d}.jpg', quality=80)
    window = open_window(root)
    window.resize(4000, 2400)
    wait(100)
    for photo in window.session.photos:
        photo.stars = 5
    window.apply_filters()
    window.set_review(True)
    review = window.review
    review.set_mode('toile')
    toile = review.view()
    settle(window, lambda: len(review.info) == 20)
    review.set_columns(4)
    toile.zoom(3/toile.scale_)
    assert len(toile.visible()) > 6
    # Many large photos at once: the loading ends, with every photo on screen shown.
    settle(window, lambda: not window.pending and all(window.best_image(p) is not None for p in toile.visible()), 20000)
    # Only the page counts here, not the preloading of the disk cache in the background.
    window.preload_timer.stop()
    settle(window, lambda: not window.pending, 20000)
    started = []
    real = triphoto_qt.Loader

    class Counting(real):
        def __init__(self, *args):
            started.append(args[0])
            super().__init__(*args)
    triphoto_qt.Loader = Counting
    try:
        toile.request_images()
        settle(window, lambda: not window.pending, 20000)
        started.clear()
        for _ in range(4):
            toile.update()
            toile.request_images()
            wait(200)
        assert not started, started
    finally:
        triphoto_qt.Loader = real
    # A job finished but not yet received must not stop the requests that follow.
    window.request(window.photos[0], 500, 1)
    window.pool.waitForDone()
    window.drop_requests(set())
    wait(50)
    # What was queued for photos out of sight gives way to those on screen.
    window.pool.setMaxThreadCount(1)
    far = [p for p in window.photos if not any(k[0] == str(p.path) and k[1] == 3000 for k in window.cache)][:6]
    for photo in far:
        window.request(photo, 3000, 1)
    window.drop_requests({str(far[0].path)})
    window.pool.setMaxThreadCount(3)
    assert not any(key[0] == str(far[-1].path) for key in window.pending)
    settle(window, lambda: not window.pending, 20000)
    window.close()
    clear_settings()


AHEAD_FORWARD = triphoto_qt.AHEAD_MAX


def test_anticipation(base):
    clear_settings()
    root = base/'anticipation'
    root.mkdir()
    for index in range(30):
        Image.new('RGB', (2600, 1733), COLORS[index % len(COLORS)]).save(root/f'photo_{index:03d}.jpg', quality=80)
    window = open_window(root)
    side = window.detail_side()
    ready = lambda rows: all(window.has_sharp(window.photos[r], side) for r in rows)
    # Without touching anything, the next photos become sharp, far ahead of the next press.
    settle(window, lambda: ready(range(1, 6)), 30000)
    settle(window, lambda: ready(window.anticipated_rows()) and not any(k[1] > 1000 for k in window.pending), 30000)
    assert len(window.anticipated_rows()) == AHEAD_FORWARD
    # Arriving on the next photo shows it sharp at once, and the range moves on by itself.
    window.navigate(1)
    assert max(window.viewer.image.width(), window.viewer.image.height()) > 1000
    far = window.anticipated_rows()[AHEAD_FORWARD-1]
    settle(window, lambda: window.has_sharp(window.photos[far], side), 30000)
    # On the Tri page, the hidden views of Révision leave its loading alone.
    calls = []
    real_drop = window.drop_requests
    window.drop_requests = lambda wanted, min_side=0: calls.append(min_side)
    window.resize(window.width()+40, window.height()+40)
    window.review.update_view()
    wait(150)
    window.drop_requests = real_drop
    assert 0 not in calls, calls
    # Going back turns the range around.
    window.navigate(-1)
    assert window.direction == -1
    window.list.setCurrentRow(20)
    window.navigate(-1)
    assert window.anticipated_rows()[0] == 18
    # Photos in the range are never pushed out to make room, wherever they were loaded from.
    protected = window.protected_paths()
    settle(window, lambda: ready(window.anticipated_rows()), 30000)
    assert all(any(k[0] == path and k[1] > 1000 for k in window.cache) for path in protected)
    # A burst of presses leaves no queue behind, and the last photo is shown as soon as it can be.
    for _ in range(6):
        window.navigate(-1)
        wait(30)
    wanted = window.protected_paths()
    assert not [key for key, job in window.pending.items() if key[1] > 1000 and key[0] not in wanted and window.pool.tryTake(job)]
    window.close()
    clear_settings()


def test_request_moves_up(base):
    clear_settings()
    root = make_project(base/'moves_up', 6)
    window = open_window(root)
    settle(window, lambda: not window.pending, 20000)
    window.preload_timer.stop()
    window.detail_timer.stop()
    # All the threads busy, so that what follows stays in the queue.
    window.pool.setMaxThreadCount(1)
    gate = threading.Event()

    class Blocker(triphoto_qt.QRunnable):
        def run(self):
            gate.wait(20)
    blocker = Blocker()
    window.pool.start(blocker, 100)
    wait(50)
    photo = window.photos[3]
    window.cache.pop((str(photo.path), 777), None)
    window.request(photo, 777, 1)
    key = (str(photo.path), 777)
    first = window.pending[key]
    window.request(photo, 777, 0)
    assert window.pending[key] is first
    window.request(photo, 777, 30)
    assert window.pending[key] is not first and window.pending[key].priority == 30
    gate.set()
    window.pool.setMaxThreadCount(4)
    settle(window, lambda: key in window.cache, 20000)
    window.close()
    clear_settings()


def test_preview_limit(base):
    clear_settings()
    root = make_project(base/'preview_limit', 2)
    window = open_window(root)
    window.session.photos = window.session.photos*600
    window.request(window.session.photos[0], 777)
    job = window.pending[(str(window.session.photos[0].path), 777)]
    assert job.preview_limit == int(1200*1.2)*1024*1024
    settle(window, lambda: not window.pending, 20000)
    window.session.photos = window.session.photos[:2]
    window.close()
    clear_settings()


def test_tri_zoom(base):
    clear_settings()
    root = make_project(base/'tri_zoom', 4)
    window = open_window(root)
    viewer = window.viewer
    settle(window, lambda: not viewer.image.isNull())
    # The wheel zooms from the center of the view, wherever the mouse is.
    viewer.wheelEvent(_Wheel(120))
    assert viewer.zoom > 1 and viewer.pan == QPointF()
    # The image can be grabbed at any zoom, even when it fits or is smaller.
    for factor in (1, 1/1.15, 1/1.15):
        viewer.zoom_from_center(factor)
        start = QPointF(viewer.pan)
        QTest.mousePress(viewer, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPointF(300, 300).toPoint())
        QTest.mouseMove(viewer, QPointF(340, 310).toPoint())
        QTest.mouseRelease(viewer, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPointF(340, 310).toPoint())
        assert viewer.pan == start+QPointF(40, 10)
    assert viewer.zoom < 1
    # Zooming keeps what was moved in the same place of the view.
    pan, zoom = QPointF(viewer.pan), viewer.zoom
    viewer.wheelEvent(_Wheel(120))
    assert abs(viewer.pan.x()-pan.x()*viewer.zoom/zoom) < .01
    # The next photo keeps both the zoom and the framing.
    pan, zoom = QPointF(viewer.pan), viewer.zoom
    window.navigate(1)
    settle(window, lambda: not viewer.image.isNull())
    assert viewer.pan == pan and viewer.zoom == zoom
    # Fit recenters.
    viewer.set_zoom(1)
    assert viewer.zoom == 1 and viewer.pan == QPointF()
    window.close()
    clear_settings()


def test_like(base):
    clear_settings()
    root = make_project(base/'like', 8)
    window = open_window(root)
    window.set_review(True)
    review = window.review
    settle(window, lambda: len(review.info) == 8)
    review.set_mode('wall')
    wall = review.view()
    wall.resize(1400, 850)
    rect, photo = wall.visible_tiles()[0]
    QTest.mouseClick(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, wall.heart_rect(rect).center().toPoint())
    assert photo.liked and not wall.targets()
    window.undo()
    assert not photo.liked
    # In Swipe, on the photo itself, and from the keyboard.
    review.set_mode('swipe')
    swipe = review.view()
    swipe.resize(1200, 800)
    card = swipe.current()
    QTest.mouseClick(swipe, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, swipe.swipe_heart().center().toPoint())
    assert card.liked and swipe.current() is card
    swipe.grab()
    window.toggle_like()
    assert not card.liked
    window.toggle_like()
    # On the Tri page.
    window.set_review(False)
    viewer = window.viewer
    settle(window, lambda: not viewer.image.isNull())
    current = window.current_photo()
    state = current.liked
    QTest.mouseClick(viewer, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, viewer.heart_rect().center().toPoint())
    assert current.liked != state
    viewer.grab()
    if not current.liked:
        window.toggle_like()
    # It survives closing the project; older sessions simply have none.
    liked = {p.relative_path for p in window.session.photos if p.liked}
    assert liked
    window.close()
    window = open_window(root)
    assert {p.relative_path for p in window.session.photos if p.liked} == liked
    window.close()
    clear_settings()


def test_export(base):
    clear_settings()
    root = make_project(base/'export', 6)
    window = open_window(root)
    calls = []
    answers = []
    real_ask, real_inform = triphoto_qt.ask, triphoto_qt.inform
    def fake_ask(parent, title, text, buttons):
        calls.append(title)
        return answers.pop(0)
    triphoto_qt.ask, triphoto_qt.inform = fake_ask, lambda parent, title, text: calls.append(title)
    try:
        folder = root/'TriPhoto_Session'/'Exportation'/'5 étoiles'
        def run():
            window.run_export(window.export_selection(5, 'all', False, False, 'all'), '5 étoiles')
        for photo in window.photos[:2]:
            photo.stars = 5
        # A first export asks nothing but where it ended, and shows its progress meanwhile.
        answers[:] = ['close']
        run()
        assert window.export_panel.isVisible() and not window.export_button.isEnabled()
        settle(window, lambda: window.export_job is None)
        assert calls == ['Export terminé'] and len(list(folder.iterdir())) == 2 and not window.export_panel.isVisible()
        assert window.export_button.isEnabled()
        calls.clear()
        # A new subfolder keeps the previous export.
        window.photos[2].stars = 5
        answers[:] = ['new', 'close']
        run()
        settle(window, lambda: window.export_job is None)
        dated = [child for child in folder.iterdir() if child.is_dir()]
        assert calls == ['Un export existe déjà', 'Export terminé']
        assert len(dated) == 1 and len(list(dated[0].iterdir())) == 3 and len(list(folder.glob('*.jpg'))) == 2
        # Replacing refreshes the main export and leaves dated ones alone.
        answers[:] = ['replace', 'close']
        window.photos[0].stars = 0
        run()
        settle(window, lambda: window.export_job is None)
        assert len(list(folder.glob('*.jpg'))) == 2 and not (folder/window.photos[0].relative_path).exists()
        assert len(list(dated[0].iterdir())) == 3
        # Declining the choice exports nothing.
        answers[:] = [None]
        run()
        assert window.export_job is None
        # Cancelling leaves the previous export and no work folder.
        before = sorted(p.name for p in folder.glob('*.jpg'))
        answers[:] = ['replace']
        run()
        window.cancel_export()
        settle(window, lambda: window.export_job is None)
        assert sorted(p.name for p in folder.glob('*.jpg')) == before
        assert not (root/'TriPhoto_Session'/triphoto_qt.EXPORT_WORK_DIR_NAME).exists()
    finally:
        triphoto_qt.ask, triphoto_qt.inform = real_ask, real_inform
    window.close()
    clear_settings()


def test_export_dialog(base):
    clear_settings()
    root = make_project(base/'export dialog', 6)
    old = root/'TriPhoto_Session'/'Export_5_Etoiles'
    old.mkdir(parents=True)
    (old/'ancienne.jpg').write_bytes(b'ancien')
    window = open_window(root)
    place = base/'export dialog portfolio'
    place.mkdir()
    window.portfolio.set_folder(place)
    photos = window.session.photos
    photos[0].stars, photos[1].stars, photos[2].stars = 5, 5, 4
    photos[1].publish = photos[2].publish = True
    window.marker = 'pick'
    window.apply_marker()
    photos[3].stars, photos[3].pick = 5, triphoto.PICK_REJECT
    # With no filter in use: the five stars of the whole gallery, rejected ones left out.
    dialog = triphoto_qt.ExportDialog(window)
    assert dialog.name.text() == '5 étoiles' and dialog.photos() == photos[:2] and dialog.go.isEnabled()
    assert '2 photos' in dialog.count.text()
    # Each setting narrows the choice and names the folder.
    dialog.publish.click()
    assert dialog.name.text() == '5 étoiles, À publier' and dialog.photos() == [photos[1]]
    dialog.stars.setCurrentIndex(dialog.stars.findData('ge4'))
    assert dialog.name.text() == '4 étoiles et plus, À publier' and dialog.photos() == photos[1:3]
    dialog.publish.click()
    dialog.marker.setCurrentIndex(dialog.marker.findData('reject'))
    assert dialog.photos() == [photos[3]] and 'Rejetées' in dialog.name.text()
    # Hearts combine with the rest, and name the folder.
    dialog.marker.setCurrentIndex(0)
    photos[0].liked = photos[2].liked = photos[3].liked = True
    dialog.stars.setCurrentIndex(0)
    dialog.liked.click()
    assert dialog.name.text() == "J'aime" and dialog.photos() == [photos[0], photos[2]], 'a rejected photo stays out'
    dialog.stars.setCurrentIndex(dialog.stars.findData(5))
    assert dialog.name.text() == "5 étoiles, J'aime" and dialog.photos() == [photos[0]]
    dialog.stars.setCurrentIndex(dialog.stars.findData('ge3'))
    assert dialog.name.text() == "3 étoiles et plus, J'aime" and dialog.photos() == [photos[0], photos[2]]
    dialog.liked.click()
    window.choices.liked.click()
    started = triphoto_qt.ExportDialog(window)
    assert started.liked.isChecked() and started.stars.currentIndex() == 0 and started.photos() == [photos[0], photos[2]], 'the filter in use comes along'
    window.choices.liked.click()
    photos[0].liked = photos[2].liked = photos[3].liked = False
    # Nothing to export: the button says so by refusing.
    dialog.marker.setCurrentIndex(0)
    dialog.stars.setCurrentIndex(dialog.stars.findData(1))
    assert not dialog.go.isEnabled()
    # A name given by hand stays, cleaned of what Windows refuses.
    dialog.stars.setCurrentIndex(dialog.stars.findData(5))
    dialog.name.setText('Inst?a/gram.')
    dialog.renamed()
    dialog.publish.click()
    assert dialog.folder_name() == 'Inst-a-gram' and dialog.name.text() == 'Inst?a/gram.'
    # Starting from the filters in use, and from the Instagram view.
    window.choices.publish.click()
    window.set_view('instagram')
    dialog = triphoto_qt.ExportDialog(window)
    assert dialog.publish.isChecked() and dialog.name.text() == 'Instagram' and dialog.stars.currentIndex() == 0
    assert dialog.photos() == photos[1:3]
    window.choices.publish.click()
    window.set_view('classic')
    # The copies land in their subfolder, and the former export stays untouched.
    real_ask = triphoto_qt.ask
    triphoto_qt.ask = lambda *args: 'close'
    try:
        window.run_export(photos[1:3], 'Instagram')
        settle(window, lambda: window.export_job is None)
    finally:
        triphoto_qt.ask = real_ask
    target = root/'TriPhoto_Session'/'Exportation'/'Instagram'
    assert sorted(p.name for p in target.iterdir()) == sorted(p.relative_path for p in photos[1:3])
    assert (old/'ancienne.jpg').read_bytes() == b'ancien' and not (root/'TriPhoto_Session'/'Export_en_cours').exists()
    # Inside the portfolio, the project of origin is one more setting.
    window.select_photo(photos[0])
    window.toggle_portfolio()
    window.open_portfolio()
    dialog = triphoto_qt.ExportDialog(window)
    assert dialog.project is not None and not dialog.portfolio.isVisibleTo(dialog)
    dialog.project.setCurrentIndex(dialog.project.findData('export dialog'))
    assert dialog.name.text().endswith('export dialog') and len(dialog.photos()) == 1
    window.close()
    clear_settings()


def right_click(widget, position, label=None):
    """The menu of a right click on this widget: its labels, after triggering the one named."""
    labels = []
    def read():
        menu = QApplication.activePopupWidget()
        labels.extend(action.text() for action in menu.actions())
        chosen = next((action for action in menu.actions() if action.text() == label), None)
        menu.close()
        if chosen:
            chosen.trigger()
    QTimer.singleShot(50, read)
    QApplication.sendEvent(widget, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position, widget.mapToGlobal(position)))
    return labels


def test_quick_export(base):
    clear_settings()
    root = make_project(base/'export rapide', 6)
    window = open_window(root)
    window.show()
    photos = window.session.photos
    answers = []
    real_ask = triphoto_qt.ask
    triphoto_qt.ask = lambda parent, title, text, buttons: answers.append(title) or 'close'
    exports = root/'TriPhoto_Session'/'Exportation'
    def exported(action, widget, position):
        before = set(exports.iterdir()) if exports.exists() else set()
        count = len(answers)
        right_click(widget, position, action)
        settle(window, lambda: window.export_job is None and len(answers) > count)
        assert answers[-1] == 'Export terminé', answers
        folder = (set(exports.iterdir())-before).pop()
        return sorted(p.name for p in folder.iterdir())
    try:
        # The ribbon: the photo under the mouse, alone or with those selected.
        window.set_view('classic')
        window.list.setCurrentRow(0)
        window.select_photos(photos[:3])
        first = window.list.visualItemRect(window.list.item(1)).center()
        assert exported('Exporter les 3 photos sélectionnées', window.list.viewport(), first) == sorted(p.relative_path for p in photos[:3])
        # The selection is what goes, a rejected photo included.
        window.change([photos[1]], pick=triphoto.PICK_REJECT)
        assert len(exported('Exporter les 3 photos sélectionnées', window.viewer, QPoint(50, 50))) == 3
        window.change([photos[1]], pick=triphoto.PICK_NEUTRAL)
        # A view of a whole wall: on a photo outside the selection, that photo only.
        window.set_view('wall')
        view = window.review.view()
        settle(window, lambda: view.visible_tiles())
        view.pick(photos[0])
        view.pick(photos[1], True)
        rects = {photo.relative_path: rect for rect, photo in view.visible_tiles()}
        place = lambda photo: QPoint(int(rects[photo.relative_path].center().x()), int(rects[photo.relative_path].center().y()-view.bar.value()))
        assert exported('Exporter les 2 photos sélectionnées', view, place(photos[1])) == sorted(p.relative_path for p in photos[:2])
        assert exported('Exporter cette photo', view, place(photos[3])) == [photos[3].relative_path]
        # With nothing picked, an empty spot only offers what it did.
        view.unpick()
        assert not any(label.startswith('Exporter') for label in right_click(view, QPoint(view.width()-3, view.height()-3)))
        # A view of one photo at a time: that one.
        window.set_view('swipe')
        assert 'Exporter cette photo' in right_click(window.review.view(), QPoint(50, 50))
        # Inside a selection: side by side, in the folder of its exports, whichever view.
        window.close_project()
        paths = [str(photo.path) for photo in photos]
        sorting = window.custom.store.create('Concours')
        sorting.set_photos([{'path': path, 'gallery': 'export rapide', 'relative': Path(path).name} for path in paths])
        sorting.buckets.append({'id': 'a', 'title': 'AG', 'subtitle': '', 'color': '#ff3b30'})
        sorting.add(paths[0], 'a')
        place_of_exports = base/'exports du concours'
        place_of_exports.mkdir()
        sorting.data['export_folder'] = str(place_of_exports)
        sorting.save()
        window.open_custom(sorting)
        window.set_view('wall')
        view = window.review.view()
        settle(window, lambda: view.visible_tiles())
        chosen = [photo for _, photo in view.visible_tiles()][:2]
        view.pick(chosen[0])
        view.pick(chosen[1], True)
        count = len(answers)
        right_click(view, QPoint(view.width()-3, view.height()-3), 'Exporter les 2 photos sélectionnées')
        settle(window, lambda: window.export_job is None and len(answers) > count)
        folder = place_of_exports/'Concours - photos choisies'
        assert sorted(p.name for p in folder.iterdir()) == sorted(photo.path.name for photo in chosen)
    finally:
        triphoto_qt.ask = real_ask
    window.close()
    clear_settings()


def test_export_by_its_button(base):
    # The usual case: a gallery's Output folder, the button, the dialog, nothing copied before the click.
    clear_settings()
    root = make_project(base/'mariage bouton'/'Output', 6)
    window = open_window(root)
    window.show()
    photos = window.session.photos
    for photo in photos[:2]:
        window.change([photo], stars=5)
    window.change([photos[2]], stars=3, publish=True)
    session = root/'TriPhoto_Session'
    assert not (session/'Exportation').exists(), 'copied while sorting'
    answers = []
    real_ask = triphoto_qt.ask
    triphoto_qt.ask = lambda parent, title, text, buttons: answers.append(title) or 'close'
    def confirm(prepare=None):
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, triphoto_qt.ExportDialog)
        if prepare:
            prepare(dialog)
        dialog.go.click()
    try:
        QTimer.singleShot(150, confirm)
        window.export_button.click()
        settle(window, lambda: window.export_job is None and answers)
        folder = session/'Exportation'/'5 étoiles'
        assert sorted(p.name for p in folder.iterdir()) == sorted(p.relative_path for p in photos[:2])
        assert all((folder/p.relative_path).read_bytes() == (root/p.relative_path).read_bytes() for p in photos[:2])
        # Other settings, another folder, and the first export stays.
        answers.clear()
        def to_publish(dialog):
            dialog.stars.setCurrentIndex(0)
            dialog.publish.click()
        QTimer.singleShot(150, lambda: confirm(to_publish))
        window.export_button.click()
        settle(window, lambda: window.export_job is None and answers)
        assert [p.name for p in (session/'Exportation'/'À publier').iterdir()] == [photos[2].relative_path]
        assert len(list(folder.iterdir())) == 2
        # Cancelling copies nothing.
        answers.clear()
        QTimer.singleShot(150, lambda: QApplication.activeModalWidget().reject())
        window.export_button.click()
        wait(100)
        assert window.export_job is None and not answers and len(list((session/'Exportation').iterdir())) == 2
    finally:
        triphoto_qt.ask = real_ask
    window.close()
    clear_settings()


def test_themed_dialog(base):
    dialog = triphoto_prefs.ThemedDialog(None, 'Titre', 'Texte\nC:\\un\\chemin', [('a', 'Non', False), ('b', 'Oui', True)])
    buttons = dialog.findChildren(triphoto_prefs.QPushButton)
    assert [b.text() for b in buttons] == ['Non', 'Oui']
    buttons[1].click()
    assert dialog.choice == 'b'


def test_update(base):
    is_newer = triphoto_update.is_newer
    assert is_newer('v1.0.0', '0.9.0') and is_newer('0.10.0', '0.9.9') and is_newer('1.0.1', '1.0')
    assert not is_newer('v0.9.0', '0.9.0') and not is_newer('0.8.9', '0.9.0') and not is_newer('nightly', '0.9.0')
    assert triphoto_update.parse_version(triphoto.APP_VERSION), 'the version must stay readable by the check'
    assert not triphoto_update.can_install(), 'a source folder never replaces itself'
    # A release is taken only if it is whole and intact.
    folder = base/'update'
    folder.mkdir()
    pack = folder/'source.zip'
    with zipfile.ZipFile(pack, 'w') as content:
        content.writestr('VisionBoard/VisionBoard.exe', 'nouveau')
        content.writestr('VisionBoard/_internal/lib.pyd', 'nouveau')
    url = pack.as_uri()
    digest = 'sha256:'+hashlib.sha256(pack.read_bytes()).hexdigest()
    results = []
    job = triphoto_update.UpdateDownload(url, digest)
    job.signals.ready.connect(lambda source, error: results.append((source, error)))
    job.run()
    QApplication.processEvents()
    source, error = results[-1]
    assert not error and Path(source, 'VisionBoard.exe').read_text() == 'nouveau'
    job = triphoto_update.UpdateDownload(url, 'sha256:'+'0'*64)
    job.signals.ready.connect(lambda source, error: results.append((source, error)))
    job.run()
    QApplication.processEvents()
    assert results[-1][0] == '' and 'ne correspond pas' in results[-1][1]
    # The installer swaps the program, spares what lies beside it and removes its work folder.
    work = Path(tempfile.mkdtemp(dir=folder))
    (work/'contenu'/'VisionBoard'/'_internal').mkdir(parents=True)
    (work/'contenu'/'VisionBoard'/'VisionBoard.exe').write_text('nouveau')
    (work/'contenu'/'VisionBoard'/'_internal'/'lib.pyd').write_text('nouveau')
    target = folder/'installé à côté'
    (target/'_internal').mkdir(parents=True)
    (target/'VisionBoard.exe').write_text('ancien')
    (target/'_internal'/'ancienne.pyd').write_text('ancien')
    (target/'mes notes.txt').write_text('garder')
    script = triphoto_update.write_installer(work/'contenu'/'VisionBoard', target, 999999)
    text = script.read_text(encoding='utf-8').replace('start "" ', 'rem ')
    script.write_text(text, encoding='utf-8')
    subprocess.run(['cmd', '/c', str(script)], timeout=60, capture_output=True)
    assert (target/'VisionBoard.exe').read_text() == 'nouveau' and (target/'_internal'/'lib.pyd').exists()
    assert not (target/'_internal'/'ancienne.pyd').exists() and (target/'mes notes.txt').read_text() == 'garder'
    assert not work.exists()
    # From the sources, it only points to the page; declined, nothing happens.
    clear_settings()
    root = make_project(base/'update_window', 2)
    window = open_window(root)
    calls = []
    real_ask = triphoto_qt.ask
    triphoto_qt.ask = lambda parent, title, text, buttons: calls.append((title, [b[0] for b in buttons])) or 'later'
    try:
        window.update_found({'version': '9.9.9', 'page': 'https://example.invalid', 'url': 'x', 'digest': ''})
    finally:
        triphoto_qt.ask = real_ask
    assert calls == [('Vision Board 9.9.9 est disponible', ['later', 'page'])] and window.update_job is None
    window.close()
    clear_settings()


def test_keys_undo_and_ribbon(base):
    clear_settings()
    root = make_project(base/'keys', 8)
    window = open_window(root)
    window.activateWindow()
    settle(window, window.isActiveWindow)
    review = window.review
    # A press on the bar is not for the photo under it: nothing follows the mouse, and a double click does not zoom.
    window.show_viewer_bar()
    bar = window.viewer_bar
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(int(bar.stars_x()+23), 23))
    assert window.viewer.drag is None and window.current_photo().stars == 2
    QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(int(bar.stars_x()+46), 23))
    assert window.viewer.zoom == 1
    # The Annuler button does what Ctrl+Z does, and says when there is nothing left.
    assert window.undo_button.isVisible() and window.undo_button.isEnabled()
    assert not window.redo_button.isEnabled()
    while window.can_undo():
        window.undo_button.click()
    assert window.current_photo().stars == 0 and not window.undo_button.isEnabled() and window.redo_button.isEnabled()
    # Ctrl+Y takes them up again, and a new action ends what could be redone.
    QTest.keyClick(window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert window.current_photo().stars != 0 and window.undo_button.isEnabled()
    stars = window.current_photo().stars
    window.undo()
    assert window.current_photo().stars == 0
    window.rate(1)
    assert not window.redo_button.isEnabled()
    window.undo()
    assert stars != 1 and window.current_photo().stars == 0
    # The thumbnails follow the width of the ribbon, the first one stays where it was.
    ribbon = window.list
    row = ribbon.visualItemRect(ribbon.item(0)).height()
    wide = ribbon.findChild(type(ribbon.verticalScrollBar())) and ribbon.width()
    ribbon.setFixedWidth(wide+200)
    wait(30)
    assert ribbon.visualItemRect(ribbon.item(0)).height() >= row+90
    assert ribbon.visualItemRect(ribbon.item(1)).top() == ribbon.visualItemRect(ribbon.item(0)).bottom()+1
    ribbon.setMinimumWidth(250)
    ribbon.setMaximumWidth(600)
    for photo in window.session.photos:
        photo.stars = 5
    window.note_filter.buttons[5].click()
    window.set_view('toile')
    toile = review.view()
    # Nothing picked: a key says so instead of looking broken.
    QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ShiftModifier)
    assert window.toast_frame.isVisible() and 'Cliquez' in window.toast_label.text() and len(window.photos) == 8
    # A photo leaves the filter: the next key does not fall on another one, somewhere on the table.
    photo = toile.stack[3]
    toile.pick(photo)
    QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ShiftModifier)
    assert photo.stars == 0 and not toile.targets() and not review.bar.isVisible()
    QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ShiftModifier)
    assert sum(p.stars == 0 for p in window.session.photos) == 1
    # Ctrl+Z brings it back, picked again.
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert photo.stars == 5 and photo in toile.stack and toile.targets() == [photo]
    # A photo moved by hand and a new deal are steps too.
    key, start = photo.relative_path, list(toile.positions[photo.relative_path])
    toile.drag, toile.moved, toile.drag_start = (photo, QPointF()), True, list(start)
    toile.positions[key][0] += 300
    toile.mouseReleaseEvent(type('E', (), {'globalPosition': lambda self: QPointF()})())
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert toile.positions[key] == start
    moved = start[0]+300
    QTest.keyClick(window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert toile.positions[key][0] == moved
    window.undo()
    dealt = list(toile.dealt)
    review.shuffle()
    shuffled = list(toile.dealt)
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert toile.dealt == dealt and toile.positions[key][:2] == start[:2] and len(toile.stack) == 8
    window.redo()
    assert toile.dealt == shuffled and 'rétabli' in window.toast_label.text()
    window.undo()
    # In the grid, Mélanger is a state of the sort, and the names come back.
    window.set_view('wall')
    wall = review.view()
    names = [p.relative_path for p in wall.ordered]
    review.shuffle()
    assert review.wall_sort.currentData() == 'shuffled'
    review.wall_sort.setCurrentIndex(0)
    assert wall.order_seed is None and [p.relative_path for p in wall.ordered] == names
    # Tindr follows the order of the names, until shuffled.
    window.set_view('swipe')
    swipe = review.view()
    window.sort.setCurrentIndex(window.sort.findData('name_desc'))
    assert swipe.deck[0].relative_path == max(names)
    window.sort.setCurrentIndex(window.sort.findData('name_asc'))
    # A photo sent out of the grid leaves again when the step is redone.
    window.set_view('wall')
    wall = review.view()
    out = wall.ordered[0]
    review.drop_out(out)
    assert out.relative_path in wall.set_aside
    window.undo()
    assert out.relative_path not in wall.set_aside
    window.redo()
    assert out.relative_path in wall.set_aside
    window.undo()
    window.set_view('classic')
    # "et +" only where it means something.
    window.note_filter.buttons['all'].click()
    assert not window.note_filter.and_more.isEnabled()
    window.note_filter.buttons[3].click()
    assert window.note_filter.and_more.isEnabled()
    window.note_filter.and_more.click()
    assert window.stars.currentData() == 'ge3'
    window.note_filter.buttons[5].click()
    assert window.stars.currentData() == 5 and not window.note_filter.and_more.isChecked()
    window.close()
    clear_settings()


def test_views_and_filter_panel(base):
    clear_settings()
    root = make_project(base/'views', 6)
    window = open_window(root)
    review = window.review
    # One row of views: Classique is the page with the ribbon, the others follow.
    assert list(window.view_buttons) == ['classic', 'wall', 'toile', 'swipe', 'instagram']
    # In the grid, Trier par leads to the sorts by color and back to the name.
    window.set_view('wall')
    review.wall_sort.setCurrentIndex(review.wall_sort.findData('contrast'))
    assert review.mode == 'color' and review.views['color'].order == 'contrast' and window.view_buttons['wall'].isChecked()
    assert review.order_combo.currentData() == 'contrast'
    review.order_combo.setCurrentIndex(0)
    assert review.mode == 'wall' and review.wall_sort_button.text() == 'Croissant'
    review.wall_sort_button.click()
    assert window.sort.currentData() == 'name_desc' and review.wall_sort_button.text() == 'Décroissant'
    review.wall_sort_button.click()
    window.set_view('classic')
    assert not window.bucket_strip.isVisible() and not window.bucket_mode
    assert window.home_button.isVisible()
    assert [window.view_buttons[key].text() for key in ('toile', 'swipe', 'instagram')] == ['Pêle-mêle', 'Tindr', 'Instagram']
    assert window.view_buttons['classic'].isChecked() and window.pages.currentIndex() == 1
    window.view_buttons['toile'].click()
    assert window.review_active and review.mode == 'toile' and window.pages.currentIndex() == 2
    assert window.view_buttons['toile'].isChecked()
    window.view_buttons['classic'].click()
    assert not window.review_active and window.view_buttons['classic'].isChecked()
    window.set_view('color')
    assert window.view_buttons['wall'].isChecked(), 'the grid sorted by color'
    review.set_mode('wall')
    assert window.view_buttons['wall'].isChecked()
    # The same filter everywhere.
    assert review.note_filter is window.note_filter and review.marker_filter is window.marker_filter
    # The panel stands on the right of the views, the photos beside it, and stays.
    panel = window.filter_panel
    wait(30)
    assert panel.isVisible() and review.tools.isVisible() and window.filter_chip.isChecked()
    assert panel.geometry().right() == window.width()-1 and review.width() == window.width()-panel.width()-window.view_panel.width()
    window.note_filter.buttons[5].click()
    assert panel.isVisible() and 'affichée' in window.filter_count.text()
    window.clear_filters()
    assert window.stars.currentIndex() == 0
    # Filtre closes it, for every view.
    window.filter_chip.click()
    wait(30)
    assert not panel.isVisible() and not window.filter_chip.isChecked() and review.width() == window.width()-window.view_panel.width()
    window.set_view('toile')
    assert not panel.isVisible()
    window.filter_chip.click()
    assert panel.isVisible()
    # Classique keeps its ribbon on the right: closed until asked for, and without the tools of the other views.
    window.set_view('classic')
    assert not panel.isVisible() and window.filter_chip.isVisible()
    window.filter_chip.click()
    assert panel.isVisible() and not review.tools.isVisibleTo(panel)
    window.filter_chip.click()
    window.note_filter.buttons[0].click()
    assert '0 étoile' in window.filter_chip.toolTip()
    window.note_filter.buttons['all'].click()
    assert window.filter_chip.text() == 'Filtre'
    # Up and down browse like left and right, except where moving on is a decision.
    keys = {shortcut.key().toString() for shortcut in window.shortcuts}
    assert {'Up', 'Down', 'Shift+Up', 'Shift+Down'} <= keys
    window.list.setCurrentRow(2)
    window.browse(1)
    assert window.list.currentRow() == 3
    window.browse(-1)
    assert window.list.currentRow() == 2
    window.set_view('swipe')
    card = review.view().current()
    window.browse(1)
    assert review.view().current() is card and card.stars == 0 and review.view().index == review.view().deck.index(card)
    window.set_view('classic')
    # The welcome screen has nothing to filter.
    window.close_project()
    assert not window.filter_chip.isVisibleTo(window) and not window.filter_panel.isVisible()
    window.close()
    clear_settings()


def test_tri_filters(base):
    clear_settings()
    root = make_project(base/'tri_filters', 6)
    window = open_window(root)
    assert list(window.note_filter.buttons)[:3] == ['all', 0, 1]
    # The capsule of the Tri page and that of Révision drive the same filter.
    for photo in window.photos[:2]:
        photo.stars = 5
    window.apply_filters()
    # Each note shows how many photos it concerns, on both pages.
    for notes in (window.note_filter, window.review.note_filter):
        assert notes.buttons['all'].count == '6' and notes.buttons[5].count == '2' and notes.buttons[0].count == '4' and notes.buttons[3].count == '0'
    window.note_filter.buttons[5].click()
    assert window.stars.currentData() == 5 and len(window.photos) == 2
    assert window.note_filter.buttons['all'].count == '6', 'the counts do not depend on the note chosen'
    window.grab()
    assert window.review.note_filter.buttons[5].isChecked()
    window.note_filter.buttons[0].click()
    assert len(window.photos) == 4
    # A change of note moves the counts, and "et +" counts from the note chosen.
    window.change([window.photos[0]], stars=4)
    assert window.note_filter.buttons[4].count == '1' and window.note_filter.buttons[0].count == '3'
    window.note_filter.buttons[3].click()
    assert window.note_filter.and_more.count == '3'
    window.undo()
    window.note_filter.buttons[0].click()
    assert len(window.photos) == 4
    window.review.note_filter.buttons[3].click()
    assert not window.photos and window.note_filter.buttons[3].isChecked()
    # With no photo left, the filter stays within reach, and says what hides them.
    assert window.filter_chip.isVisible() and '3 étoiles' in window.filter_chip.toolTip()
    window.note_filter.buttons['all'].click()
    assert len(window.photos) == 6
    window.close()
    clear_settings()


def test_clear_stars(base):
    clear_settings()
    root = make_project(base/'clear', 12)
    window = open_window(root)
    window.set_review(True)
    review = window.review
    settle(window, lambda: len(review.info) == 12)
    for photo in window.photos:
        photo.stars = 5
    window.apply_filters()
    window.stars.setCurrentIndex(window.stars.findData(5))
    for mode in ('wall', 'color'):
        review.set_mode(mode)
        wall = review.view()
        wall.resize(1400, 850)
        wall.bar.setValue(0)
        wall.relayout()
        rect, photo = wall.visible_tiles()[0]
        button = wall.badge_layout(rect, photo)[0]
        click = button.center().toPoint()
        QTest.mouseClick(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, click)
        # The photo leaves the five stars at once, and Ctrl+Z brings it back.
        assert photo.stars == 0 and photo not in window.photos
        assert all(p is not photo for _, p in wall.tiles)
        window.undo()
        assert photo.stars == 5 and photo in window.photos
    # A click beside the button only selects.
    rect, photo = wall.visible_tiles()[0]
    QTest.mouseClick(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center().toPoint())
    assert photo.stars == 5
    # Clearing several photos quickly never opens the Tri page.
    rect, photo = wall.visible_tiles()[0]
    click = wall.badge_layout(rect, photo)[0].center().toPoint()
    QTest.mouseClick(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, click)
    QTest.mouseDClick(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, click)
    assert window.review_active and photo.stars == 0
    window.undo()
    window.undo()
    # On the tilted prints of the Toile as well.
    review.set_mode('toile')
    toile = review.view()
    toile.resize(1400, 850)
    toile.set_columns(4)
    # The wheel goes up and down without zooming, and stops at both ends.
    scale, pan = toile.scale_, QPointF(toile.pan)
    toile.wheelEvent(_Wheel(-240))
    assert toile.scale_ == scale and toile.pan.y() < pan.y() and toile.pan.x() == pan.x()
    toile.wheelEvent(_Wheel(24000))
    assert toile.pan == pan
    toile.wheelEvent(_Wheel(120, Qt.KeyboardModifier.ControlModifier))
    assert toile.scale_ > scale
    toile.zoom_reset()
    top = toile.stack[-1]
    rect = toile.tile_rect(top)
    button = toile.badge_layout(QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height()), top)[0]
    angle = math.radians(toile.positions[top.relative_path][2])
    center = button.center()
    world = rect.center()+QPointF(center.x()*math.cos(angle)-center.y()*math.sin(angle), center.x()*math.sin(angle)+center.y()*math.cos(angle))
    screen = QPointF(toile.width()/2, toile.height()/2)+toile.pan+world*toile.scale_
    assert toile.badge_hit(screen) == (top, 'clear')
    # The note bar has the same button, on the Tri page and in Revoir.
    review.set_mode('swipe')
    swipe = review.view()
    bar = review.bar
    target = swipe.current()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, bar.clear_rect().center().toPoint())
    assert target.stars == 0
    window.undo()
    star = QPointF(bar.stars_x()+2*23, 23).toPoint()
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, star)
    assert target.stars == 3
    window.close()
    clear_settings()


def test_swipe_zoom(base):
    clear_settings()
    root = make_project(base/'swipe_zoom', 8)
    window = open_window(root)
    window.set_review(True)
    review = window.review
    settle(window, lambda: len(review.info) == 8)
    review.set_mode('swipe')
    swipe = review.view()
    swipe.resize(1200, 800)
    review.zoom(1/1.15)
    review.zoom(1/1.15)
    level = swipe.zoom_level
    assert level < 1 and review.zoom_label.text() == f'{round(level*100)} %'
    # Dezoomed, a drag still decides, and the zoom stays for the next photo.
    review.swipe_right.setCurrentIndex(review.swipe_right.findData('star4'))
    target = swipe.current()
    swipe.press = QPointF(600, 400)
    swipe.offset = QPointF(500, 0)
    swipe.mouseReleaseEvent(_Event())
    settle(window, lambda: swipe.current() is not target)
    assert target.stars == 4 and swipe.zoom_level == level
    # Zoomed in, a drag moves the photo, and the framing stays too.
    swipe.zoom(2.5, QPointF(300, 200))
    assert swipe.zoom_level > 1
    pan = QPointF(swipe.pan)
    current = swipe.current()
    QTest.mousePress(swipe, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPointF(600, 400).toPoint())
    QTest.mouseMove(swipe, QPointF(650, 420).toPoint())
    QTest.mouseRelease(swipe, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPointF(650, 420).toPoint())
    assert swipe.current() is current and swipe.pan == pan+QPointF(50, 20)
    pan = QPointF(swipe.pan)
    swipe.skip()
    swipe.grab()
    assert swipe.pan == pan and swipe.zoom_level > 1
    # Going to the Tri page and back resumes on the same card.
    index = swipe.index
    window.set_review(False)
    window.set_review(True)
    assert swipe.index == index and swipe.zoom_level > 1
    review.zoom_reset()
    assert swipe.zoom_level == 1 and swipe.pan == QPointF() and review.zoom_label.text() == '100 %'
    # Grid sizes come back after a restart.
    review.set_mode('wall')
    wall = review.view()
    review.set_wall_layout('rows')
    review.zoom(1.15)
    assert review.zoom_label.text() == '115 %'
    height = wall.row_height
    window.close()
    window = open_window(root)
    assert window.review.views['wall'].row_height == height
    window.close()
    clear_settings()


def test_review_state_remembered(base):
    clear_settings()
    root = make_project(base/'state', 8)
    window = open_window(root)
    review = window.review
    for photo in window.photos[:4]:
        photo.stars = 5
    aside = window.photos[0].relative_path
    review.views['wall'].set_aside.add(aside)
    window.stars.setCurrentIndex(window.stars.findData(5))
    assert len(window.photos) == 4
    window.close()
    window = open_window(root)
    review = window.review
    assert window.stars.currentData() == 5 and len(window.photos) == 4
    assert all(photo.stars == 5 for photo in window.photos)
    assert review.views['wall'].set_aside == {aside} and review.restore_button.text() == 'Réafficher (1)'
    # A filter that no longer matches anything never opens on an empty page.
    for photo in window.session.photos:
        photo.stars = 0
    window.close()
    window = open_window(root)
    assert window.stars.currentIndex() == 0 and len(window.photos) == 8
    window.close()
    clear_settings()


def test_background_color(base):
    clear_settings()
    root = make_project(base/'background', 4)
    window = open_window(root)
    # Presets and free color, from the Tri page and from the Revoir page.
    window.viewer_picker.swatches[0].click()
    assert window.viewer.background == '#ffffff' and window.review.picker.swatches[0].isChecked()
    original = triphoto_review.QColorDialog.getColor
    triphoto_review.QColorDialog.getColor = staticmethod(lambda *args, **kwargs: triphoto_review.QColor('#7a3b52'))
    try:
        window.viewer_picker.custom.click()
        assert window.viewer.background == '#7a3b52'
        assert window.viewer_picker.custom.isChecked() and window.review.picker.custom.color == '#7a3b52'
        window.set_review(True)
        window.review.picker.swatches[4].click()
        assert window.viewer.background == '#000000' and not window.review.picker.custom.color
        window.review.picker.custom.click()
        assert window.viewer.background == '#7a3b52'
    finally:
        triphoto_review.QColorDialog.getColor = original
    # The painted surfaces read the same color, and it survives a restart.
    window.review.set_mode('wall')
    wait(60)
    assert window.review.view().grab().toImage().pixelColor(4, 4).name() == '#7a3b52'
    window.close()
    assert triphoto_qt.Viewer().background == '#7a3b52'
    clear_settings()


def test_broken_file_does_not_stop_preload(base):
    clear_settings()
    root = make_project(base/'broken', 8)
    (root/'photo_000.jpg').write_bytes(b'')
    window = open_window(root)
    settle(window, lambda: len(window.prepared) == 7)
    assert window.failed
    window.close()
    clear_settings()


def test_recent_project_cycle(base):
    clear_settings()
    root = make_project(base/'cycle', 5)
    other = make_project(base/'cycle2', 3)
    window = open_window(root)
    window.rate(5)
    window.set_folder(other)
    assert [Path(e['path']) for e in window.recents.entries()] == [other, root]
    assert window.recents.entries()[1]['five'] == 1
    window.close_project()
    assert window.pages.currentIndex() == 0 and len(window.home.cards) == 2
    window.open_recent(str(root))
    assert window.session is not None and sum(p.stars == 5 for p in window.session.photos) == 1
    window.fill_project_menu()
    window.close()
    clear_settings()


def test_portfolio(base):
    clear_settings()
    project = make_project(base/'mariage'/'Output', 8)
    place = base/'mon portfolio'
    place.mkdir()
    before = {p.name: p.read_bytes() for p in project.glob('*.jpg')}
    binned = []
    original_trash = triphoto_portfolio.move_to_trash
    def fake_trash(path):
        binned.append(Path(path).name)
        Path(path).unlink()
        return True
    triphoto_portfolio.move_to_trash = fake_trash
    try:
        triphoto_qt.app_settings().setValue('portfolio_folder', str(place))
        window = open_window(project)
        first, second, third = window.session.photos[:3]
        first.stars, first.publish = 5, True
        # In the background, in a subfolder named after the wedding rather than "Output".
        window.select_photo(first)
        window.toggle_portfolio()
        window.select_photo(second)
        window.toggle_portfolio()
        assert window.portfolio.contains(first.path) and window.in_portfolio(second)
        # Nothing is copied: a list, and no photo written anywhere.
        window.portfolio.wait()
        assert not [p for p in place.rglob('*') if p.suffix.lower() == '.jpg']
        # Portfolio, À publier and the note filter independently of each other.
        window.choices.portfolio.click()
        assert {p.relative_path for p in window.photos} == {first.relative_path, second.relative_path}
        window.choices.publish.click()
        assert [p.relative_path for p in window.photos] == [first.relative_path]
        assert window.filter_chip.toolTip().count('À publier') == 1
        window.choices.publish.click()
        window.choices.portfolio.click()
        # Undo takes a photo out again, and a publish mark comes back with the session.
        window.select_photo(third)
        window.toggle_publish()
        window.toggle_portfolio()
        assert window.portfolio.contains(third.path)
        window.undo()
        assert not window.portfolio.contains(third.path) and third.publish
        assert not binned
        window.save_session()
        window.close_project()
        # The home page shows it apart from the recent projects.
        window.home.refresh()
        assert not any(window.is_portfolio_path(e['path']) for e in window.recents.entries())
        assert 'photos' in window.home.portfolio_card.meta.text()
        reloaded = triphoto.PhotoSession(project)
        assert next(p for p in reloaded.photos if p.relative_path == third.relative_path).publish
        # A second project; opening the portfolio lists both, with notes carried along.
        other = make_project(base/'bapteme', 3)
        window.set_folder(other)
        window.select_photo(window.session.photos[0])
        window.toggle_portfolio()
        window.portfolio.wait()
        wait(100)
        window.open_portfolio()
        assert window.viewing_portfolio() and len(window.session.photos) == 3
        names = [window.choices.project.itemData(i) for i in range(window.choices.project.count())]
        assert names == ['all', 'bapteme', 'mariage']
        assert not any(window.is_portfolio_path(e['path']) for e in window.recents.entries())
        copy = next(p for p in window.session.photos if p.relative_path == f'mariage/{first.path.name}')
        assert copy.stars == 5 and copy.publish and window.in_portfolio(copy)
        assert triphoto.norm_path(copy.path) == triphoto.norm_path(first.path), 'read from the original'
        assert window.photo_project(copy) == 'mariage'
        window.choices.project.setCurrentIndex(names.index('bapteme'))
        assert [window.portfolio.project_of(p.relative_path) for p in window.photos] == ['bapteme']
        window.choices.project.setCurrentIndex(0)
        # Sorted again here without touching the project, then taken out: the original stays, nothing goes to the bin.
        window.change([copy], stars=2)
        window.select_photo(copy)
        window.toggle_portfolio()
        window.portfolio.wait()
        wait(50)
        assert copy.path.exists() and not binned and len(window.session.photos) == 2
        window.close_project()
        window.set_folder(project)
        assert next(p for p in window.session.photos if p.relative_path == first.relative_path).stars == 5
        assert not window.portfolio.contains(first.path) and window.portfolio.contains(second.path)
        assert {p.name: p.read_bytes() for p in project.glob('*.jpg')} == before, 'originals changed'
        window.close()
    finally:
        triphoto_portfolio.move_to_trash = original_trash


def test_real_trash_answer(base):
    # The other tests replace the bin: here its real answer, on a file that does not exist.
    assert triphoto_portfolio.move_to_trash(base/'absent.jpg') is False


def test_trash_refused_brings_photo_back(base):
    clear_settings()
    project = make_project(base/'refus', 2)
    place = base/'refus portfolio'
    place.mkdir()
    triphoto_qt.app_settings().setValue('portfolio_folder', str(place))
    original, naps = triphoto_portfolio.move_to_trash, triphoto_portfolio.time.sleep
    try:
        # A copy made by an earlier version, the only thing that ever goes to the bin.
        for photo in triphoto.discover_photos(project):
            (place/'refus').mkdir(exist_ok=True)
            shutil.copy2(photo.path, place/'refus'/photo.path.name)
        (place/triphoto.SESSION_DIR_NAME).mkdir()
        (place/triphoto.SESSION_DIR_NAME/triphoto_portfolio.MANIFEST_FILE).write_text(json.dumps({'version': 1, 'items': {
            f'refus/{photo.path.name}': {'source': str(photo.path), 'project': 'refus'} for photo in triphoto.discover_photos(project)}}), encoding='utf-8')
        window = open_window(project)
        window.open_portfolio()
        photo = window.session.photos[0]
        triphoto_portfolio.move_to_trash = lambda path: False
        triphoto_portfolio.time.sleep = lambda seconds: None
        window.select_photo(photo)
        window.toggle_portfolio()
        settle(window, lambda: not window.portfolio.removed and len(window.session.photos) == 2)
        assert photo.path.exists() and len(window.portfolio.items) == 2
        window.close()
    finally:
        triphoto_portfolio.move_to_trash = original
        triphoto_portfolio.time.sleep = naps


def test_portfolio_relocate(base):
    clear_settings()
    project = make_project(base/'reloc', 3)
    old, new = base/'ancien', base/'nouveau'
    old.mkdir()
    new.mkdir()
    triphoto_qt.app_settings().setValue('portfolio_folder', str(old))
    window = open_window(project)
    window.select_photo(window.session.photos[0])
    window.toggle_portfolio()
    window.portfolio.wait()
    name = window.session.photos[0].path.name
    window.relocate_portfolio(old, new)
    settle(window, lambda: window.relocating is None)
    assert window.portfolio.folder == new and window.portfolio.contains(window.session.photos[0].path)
    assert not (new/'reloc').exists(), 'nothing to copy but the list'
    window.close()


def test_instagram(base):
    clear_settings()
    project = make_project(base/'insta', 8)
    triphoto_qt.app_settings().setValue('portfolio_folder', str(base/'insta portfolio'))
    (base/'insta portfolio').mkdir()
    window = open_window(project)
    window.resize(1500, 1000)
    window.show()
    review = window.review
    window.set_view('instagram')
    view = review.views['instagram']
    settle(window, lambda: len(review.info) == 8)
    review.rebuild()
    wait(100)
    assert view.current() is not None and review.bar.isVisible()
    post, media = view.frames()
    # A vertical nearly fills the height, and a horizontal, same width, comes out smaller.
    assert media.height() > view.height()*.7
    assert abs(media.width()/media.height()-2/3) < .01
    vertical = next(p for p in window.photos if review.aspect(p) < 1)
    horizontal = next(p for p in window.photos if review.aspect(p) > 1)
    assert abs(view.photo_rect(media, vertical).height()-media.height()) < 2
    assert view.photo_rect(media, horizontal).height() < media.height()*.5
    assert media.bottom() < post.bottom() and post.bottom() < review.bar.geometry().top()+1
    # The buttons of the post act on the photo shown.
    photo = view.current()
    actions = view.action_rects()
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=actions['send'].center().toPoint())
    assert photo.publish
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=actions['keep'].center().toPoint())
    assert window.portfolio.contains(photo.path)
    # The arrow and the keys move through the carousel; the first photo has no way back.
    index = view.index
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=view.chevrons()[1].center().toPoint())
    assert view.index == index+1 and window.current_photo() is view.current()
    view.navigate(-1)
    assert view.index == index
    # Zoom in stays inside the frame, zoom out shrinks the post.
    height = media.height()
    view.zoom(1.5)
    assert abs(view.frames()[1].height()-height) < 1
    view.zoom_reset()
    view.zoom(.5)
    assert view.frames()[1].height() < height*.6
    view.zoom_reset()
    # À publier as a filter of the carousel, from its own bar.
    window.choices.publish.click()
    assert [p.relative_path for p in view.deck] == [photo.relative_path] and window.choices.publish.isChecked()
    window.choices.publish.click()
    assert len(view.deck) == 8
    window.close()
    window.portfolio.wait()


def test_color_orders(base):
    clear_settings()
    project = base/'orders'
    project.mkdir()
    Image.new('L', (300, 200), 128).convert('RGB').save(project/'flat.jpg', quality=95)
    half = Image.new('RGB', (300, 200), (0, 0, 0))
    half.paste((255, 255, 255), (150, 0, 300, 200))
    half.save(project/'hard.jpg', quality=95)
    Image.new('RGB', (300, 200), (230, 30, 30)).save(project/'vivid.jpg', quality=95)
    window = open_window(project)
    review = window.review
    window.set_view('color')
    color = review.views['color']
    assert color.order == 'hue' and review.order_combo.currentText() == 'Couleur'
    settle(window, lambda: len(review.info) == 3)
    names = lambda: [p.path.name for p in color.ordered]
    review.set_color_order('contrast')
    assert names()[0] == 'hard.jpg' and review.order_button.text() == 'Décroissant'
    assert not review.groups_button.isVisibleTo(review.options['color'][0])
    review.flip_color_order()
    assert color.order == 'flat' and names()[-1] == 'hard.jpg'
    review.set_color_order('vivid')
    assert names()[0] == 'vivid.jpg' and review.order_combo.currentText() == 'Saturation'
    review.set_color_order('light')
    # The cursor only varies along what is sorted: same grey scale, or the same strength of any hue.
    assert triphoto_review.order_tint({'light': 0}, 'light').name() == '#000000'
    assert triphoto_review.order_tint({'light': 1}, 'light').name() == '#ffffff'
    a = triphoto_review.order_tint({'hue': 20, 'light': .1, 'vivid': .1}, 'hue')
    b = triphoto_review.order_tint({'hue': 200, 'light': .9, 'vivid': .9}, 'hue')
    assert a.hslSaturation() == b.hslSaturation() and a.lightness() == b.lightness() and a.hslHue() != b.hslHue()
    # An older analysis, made with another measure, is done again.
    review.set_color_order('hue')
    data = json.loads(review.analysis_path().read_text(encoding='utf-8')) if review.analysis_path().exists() else dict(review.info)
    for entry in data.values():
        entry.pop('version', None)
    window.close_project()
    (project/'TriPhoto_Session'/'colors.json').write_text(json.dumps(data), encoding='utf-8')
    window.set_folder(project)
    settle(window, lambda: all(review.info.get(p.relative_path, {}).get('version') == triphoto_review.ANALYSIS_VERSION for p in window.session.photos))
    window.close()


def test_rows_gap_and_captions(base):
    clear_settings()
    project = base/'rows'
    project.mkdir()
    for index in range(12):
        Image.new('RGB', (900, 600), COLORS[index % 8]).save(project/f'h_{index:02d}.jpg')
    window = open_window(project)
    window.resize(1500, 1000)
    window.show()
    review = window.review
    window.set_view('wall')
    wall = review.views['wall']
    settle(window, lambda: len(review.info) == 12)
    review.set_wall_layout('rows')
    review.set_wall_emphasis('portrait')
    rows = {}
    for rect, _ in wall.tiles:
        rows.setdefault(round(rect.top()), []).append(rect)
    tops = sorted(rows)
    assert len(tops) > 1
    # Between two rows of horizontals, the same gap as between two photos.
    assert abs(tops[1]-rows[tops[0]][0].bottom()-6) < 1
    assert abs(rows[tops[0]][1].left()-rows[tops[0]][0].right()-6) < 1
    # Staggered: the stars go under the photo, in room of their own. In rows they stay on it.
    photo = wall.tiles[0][1]
    photo.stars = 3
    rect = wall.tiles[0][0]
    assert wall.badge_layout(rect, photo)[1].top() < rect.bottom()
    review.set_wall_layout('staggered')
    rect, photo = next(t for t in wall.tiles if t[1] is photo)
    plate = wall.badge_layout(rect, photo)[1]
    assert plate.top() > rect.bottom()
    below = [other for other, p in wall.tiles if other.top() >= rect.bottom() and other.left() < rect.right() and other.right() > rect.left()]
    assert all(other.top() >= plate.bottom() for other in below)
    # The button under the photo still works.
    review.sync_bar()
    button = wall.badge_layout(rect, photo)[0]
    wall.bar.setValue(0)
    assert wall.badge_hit(button.center()-QPointF(0, 0)) == (photo, 'clear')
    window.close()


def test_select_all(base):
    clear_settings()
    root = make_project(base/'select_all', 12)
    window = open_window(root)
    fired = lambda key: next(s for s in window.shortcuts if s.key().toString() == key).activated.emit()
    names = lambda photos: {p.relative_path for p in photos}
    # Only what the filter lets through, whatever the rest of the gallery holds.
    for photo in window.photos[:5]:
        photo.stars = 5
    window.note_filter.buttons[5].click()
    assert len(window.photos) == 5 and len(window.session.photos) == 12
    fired('Ctrl+A')
    assert names(window.target_photos()) == names(window.photos) and len(window.target_photos()) == 5
    # A key acts on all of them, and the photo on display decides a toggle.
    window.list.setCurrentRow(2, triphoto_qt.QItemSelectionModel.SelectionFlag.NoUpdate)
    displayed = window.current_photo()
    assert window.target_photos()[0] is displayed
    displayed.liked = True
    window.toggle_like()
    assert not any(p.liked for p in window.photos)
    window.undo()
    assert displayed.liked and sum(p.liked for p in window.photos) == 1
    displayed.liked = False
    # So does a click on the note of one thumbnail of the selection, which stays selected.
    window.note_filter.buttons['all'].click()
    assert len(window.target_photos()) == 5, 'the selection survives a wider filter'
    assert names(window.target_photos()) == {p.relative_path for p in window.photos if p.stars == 5}
    fired('Ctrl+A')
    assert len(window.target_photos()) == 12
    window.show()
    window.list.doItemsLayout()
    wait()
    delegate, row = window.list.itemDelegate(), 1
    option = QStyleOptionViewItem()
    option.rect = window.list.visualItemRect(window.list.item(row))
    card = delegate.card(option)
    star = QPointF(card.x()+17+2.5*14, card.bottom()-10).toPoint()
    QTest.mouseClick(window.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, star)
    assert {p.stars for p in window.session.photos} == {3}
    assert len(window.target_photos()) == 12
    QTest.mouseClick(window.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, delegate.heart(card).center().toPoint())
    assert all(p.liked for p in window.session.photos) and len(window.target_photos()) == 12
    window.undo()
    assert not any(p.liked for p in window.session.photos)
    # A click on the body of a thumbnail still collapses the selection, as before.
    QTest.mouseClick(window.list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, card.center().toPoint())
    assert len(window.target_photos()) == 1
    # Deselecting in Classique keeps the photo on display as the one the keys act on.
    fired('Ctrl+A')
    fired('Ctrl+D')
    assert window.target_photos() == [window.current_photo()]
    # A note clicked on a photo outside the selection only changes that photo.
    window.select_photos(window.photos[:3])
    outsider = window.photos[5]
    assert window.with_selection(outsider) == [outsider]
    assert window.with_selection(window.photos[1])[0] is window.photos[1] and len(window.with_selection(window.photos[1])) == 3
    # In the Grille, what has been set aside stays out; Ctrl+D leaves nothing behind for Classique.
    window.set_review(True)
    review = window.review
    settle(window, lambda: len(review.info) == 12)
    review.set_mode('wall')
    wall = review.view()
    wall.resize(1400, 850)
    assert not wall.targets()
    aside = window.photos[0]
    wall.set_aside.add(aside.relative_path)
    wall.photos_changed()
    fired('Ctrl+A')
    assert wall.active and len(wall.targets()) == 11 and aside not in wall.targets()
    window.rate(4)
    assert aside.stars == 3 and sum(p.stars == 4 for p in window.photos) == 11
    window.undo()
    rect, photo = next(t for t in wall.visible_tiles() if t[1] in wall.targets())
    QTest.mouseClick(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, wall.heart_rect(rect).center().toPoint())
    assert sum(p.liked for p in window.photos) == 11 and len(wall.targets()) == 11
    window.undo()
    fired('Ctrl+D')
    assert not wall.targets() and len(window.selected_photos()) == 1
    # A click in the void does the same.
    fired('Ctrl+A')
    wall.unpick()
    assert len(window.selected_photos()) == 1
    # One photo at a time in Tindr and Instagram: nothing to select.
    for mode in ('swipe', 'instagram'):
        review.set_mode(mode)
        fired('Ctrl+A')
        assert len(review.targets()) == 1 and len(window.selected_photos()) == 1, mode
    window.set_review(False)
    # Taking many copies out of the portfolio sends them to the bin: it is asked first.
    answers = []
    real_ask, real_viewing, real_take = triphoto_qt.ask, window.viewing_portfolio, window.take_out_of_portfolio
    taken = []
    window.viewing_portfolio, window.take_out_of_portfolio = (lambda: True), taken.append
    fired('Ctrl+A')
    triphoto_qt.ask = lambda parent, title, text, buttons: answers.append(text) or 'cancel'
    try:
        window.toggle_portfolio()
        assert len(answers) == 1 and '12 photos' in answers[0] and not taken
        triphoto_qt.ask = lambda parent, title, text, buttons: 'remove'
        window.toggle_portfolio()
        assert len(taken) == 1 and len(taken[0]) == 12
        fired('Ctrl+D')
        triphoto_qt.ask = lambda *args: answers.append('asked')
        window.toggle_portfolio()
        assert len(answers) == 1 and len(taken) == 2 and len(taken[1]) == 1
    finally:
        triphoto_qt.ask = real_ask
        window.viewing_portfolio, window.take_out_of_portfolio = real_viewing, real_take
    # Both keys are customizable, and the former ones then do nothing.
    settings = triphoto_qt.app_settings()
    mapping = triphoto_prefs.shortcut_map(settings)
    assert mapping['select_all'] == ['Ctrl+A'] and mapping['deselect'] == ['Ctrl+D']
    mapping['select_all'], mapping['deselect'] = ['Ctrl+Shift+T'], ['Ctrl+Shift+Y']
    triphoto_prefs.save_shortcut_map(settings, mapping)
    window.install_shortcuts()
    keys = {s.key().toString() for s in window.shortcuts}
    assert 'Ctrl+Shift+T' in keys and 'Ctrl+Shift+Y' in keys and 'Ctrl+A' not in keys and 'Ctrl+D' not in keys
    fired('Ctrl+Shift+T')
    assert len(window.target_photos()) == 12
    fired('Ctrl+Shift+Y')
    assert len(window.target_photos()) == 1
    dialog = triphoto_prefs.PreferencesDialog(window, settings)
    assert dialog.mapping()['select_all'] == ['Ctrl+Shift+T']
    dialog.reset()
    assert dialog.mapping()['select_all'] == ['Ctrl+A'] and not triphoto_prefs.conflicts(dialog.mapping())
    window.close()
    clear_settings()


def test_ribbon_portfolio_button(base):
    clear_settings()
    place = base/'ribbon portfolio'
    place.mkdir()
    triphoto_qt.app_settings().setValue('portfolio_folder', str(place))
    window = open_window(make_project(base/'ribbon', 6))
    window.show()
    window.list.doItemsLayout()
    wait()
    viewport = window.list.viewport()
    delegate = window.list.itemDelegate()
    def button(row):
        option = QStyleOptionViewItem()
        option.rect = window.list.visualItemRect(window.list.item(row))
        return delegate.bookmark(delegate.card(option)).center().toPoint()
    click = lambda row: QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, button(row))
    contains = lambda photo: window.portfolio.contains(photo.path)
    # On, then off, on the thumbnail clicked even if another photo is on display.
    window.list.setCurrentRow(0)
    other = window.photos[1]
    click(1)
    assert contains(other) and not contains(window.photos[0])
    window.grab()
    click(1)
    assert not contains(other)
    # Ctrl+Z takes the click back.
    click(1)
    window.undo()
    assert not contains(other)
    # A click on a photo of the selection acts on all of it, and keeps it; the clicked photo decides.
    window.change(window.photos, stars=0)
    window.select_all()
    click(2)
    assert all(contains(p) for p in window.photos) and len(window.target_photos()) == 6
    click(2)
    assert not any(contains(p) for p in window.photos) and len(window.target_photos()) == 6
    window.close()
    clear_settings()


def test_portfolio_without_copies(base):
    clear_settings()
    project = make_project(base/'noces'/'Output', 6)
    place = base/'sans copies'
    place.mkdir()
    before = {p.name: p.read_bytes() for p in project.glob('*.jpg')}
    binned = []
    original_trash = triphoto_portfolio.move_to_trash
    def fake_trash(path):
        binned.append(Path(path).name)
        Path(path).unlink()
        return True
    triphoto_portfolio.move_to_trash = fake_trash
    triphoto_qt.app_settings().setValue('portfolio_folder', str(place))
    answers = []
    real_ask = triphoto_qt.ask
    triphoto_qt.ask = lambda parent, title, text, buttons: answers.append(title) or 'close'
    try:
        window = open_window(project)
        # Adding many photos saves the list once, and at once.
        saves = []
        real_save = window.portfolio.save
        window.portfolio.save = lambda: saves.append(1) or real_save()
        window.select_all()
        window.toggle_portfolio()
        assert len(saves) == 1 and len(window.portfolio.items) == 6
        window.portfolio.save = real_save
        window.session.photos[0].stars = 5
        assert not [p for p in place.rglob('*') if p.suffix.lower() == '.jpg']
        window.close_project()
        # The manifest holds the references across a restart.
        window.close()
        window = open_window(project)
        assert all(window.portfolio.contains(p.path) for p in window.session.photos)
        window.open_portfolio()
        assert window.viewing_portfolio() and len(window.session.photos) == 6
        # Exporting: everything by default, not the 5 stars, in a folder per project.
        dialog = triphoto_qt.ExportDialog(window)
        assert len(dialog.photos()) == 6 and dialog.folder_name() == 'Portfolio'
        window.run_export(dialog.photos(), dialog.folder_name())
        settle(window, lambda: window.export_job is None, 15000)
        exported = window.session.session_dir/triphoto_qt.EXPORT_ROOT_NAME/'Portfolio'/'noces'
        assert {p.name: p.read_bytes() for p in exported.glob('*.jpg')} == before
        # An original out of reach: its photo leaves the view, not the portfolio, and keeps its notes.
        marked = window.session.photos[0]
        window.change([marked], stars=4)
        relative, source = marked.relative_path, marked.path
        def quiet():
            # Windows refuses to move a file being read.
            settle(window, lambda: not window.background_tasks(), 15000)
            window.pool.waitForDone()
            window.review.analysis_pool.waitForDone()
        window.close_project()
        quiet()
        hidden = source.with_name(source.name+'.away')
        os.replace(source, hidden)
        window.open_portfolio()
        assert len(window.session.photos) == 5 and relative in window.portfolio.items
        assert relative in window.session.absent
        dialog = triphoto_qt.ExportDialog(window)
        assert len(dialog.photos()) == 5
        window.close_project()
        os.replace(hidden, source)
        window.open_portfolio()
        back = next(p for p in window.session.photos if p.relative_path == relative)
        assert back.stars == 4 and not window.session.absent, 'the notes come back with the file'
        # It comes back too through a refresh, without closing the project.
        quiet()
        os.replace(source, hidden)
        window.refresh()
        assert len(window.session.photos) == 5
        os.replace(hidden, source)
        window.refresh()
        assert next(p for p in window.session.photos if p.relative_path == relative).stars == 4
        # Taking a reference out never sends anything to the bin.
        window.select_all()
        triphoto_qt.ask = lambda parent, title, text, buttons: 'remove'
        window.toggle_portfolio()
        window.portfolio.wait()
        wait(50)
        assert not binned and not window.portfolio.items
        assert {p.name: p.read_bytes() for p in project.glob('*.jpg')} == before, 'originals changed'
        window.close()
    finally:
        triphoto_portfolio.move_to_trash = original_trash
        triphoto_qt.ask = real_ask
    clear_settings()


def test_portfolio_older_copies(base):
    # A portfolio made by an earlier version keeps working, on its copies.
    clear_settings()
    project = make_project(base/'ancien projet', 3)
    place = base/'ancien portfolio'
    (place/'ancien projet').mkdir(parents=True)
    (place/triphoto.SESSION_DIR_NAME).mkdir()
    items = {}
    for photo in triphoto.discover_photos(project):
        shutil.copy2(photo.path, place/'ancien projet'/photo.path.name)
        items[f'ancien projet/{photo.path.name}'] = {'source': str(photo.path), 'project': 'ancien projet', 'seed': {'stars': 5, 'pick': 'neutral', 'liked': False, 'publish': False}}
    (place/triphoto.SESSION_DIR_NAME/triphoto_portfolio.MANIFEST_FILE).write_text(json.dumps({'version': 1, 'items': items}), encoding='utf-8')
    binned = []
    original_trash = triphoto_portfolio.move_to_trash
    def fake_trash(path):
        binned.append(Path(path).name)
        Path(path).unlink()
        return True
    triphoto_portfolio.move_to_trash = fake_trash
    triphoto_qt.app_settings().setValue('portfolio_folder', str(place))
    try:
        window = open_window(project)
        assert all(window.portfolio.contains(p.path) for p in window.session.photos)
        window.open_portfolio()
        assert len(window.session.photos) == 3 and all(p.stars == 5 for p in window.session.photos)
        assert all(triphoto.is_inside(p.path, place) for p in window.session.photos), 'read from the copies'
        # A photo added now sits beside them as a reference.
        window.close_project()
        window.set_folder(project)
        extra = project/'nouvelle.jpg'
        shutil.copy2(window.session.photos[0].path, extra)
        window.refresh()
        fresh = next(p for p in window.session.photos if p.path.name == 'nouvelle.jpg')
        window.select_photo(fresh)
        window.toggle_portfolio()
        assert not (place/'ancien projet'/'nouvelle.jpg').exists()
        window.close_project()
        window.open_portfolio()
        assert len(window.session.photos) == 4
        # Taken out, a copy goes to the bin, its original stays.
        copy = next(p for p in window.session.photos if p.path.name != 'nouvelle.jpg')
        name = copy.path.name
        window.select_photo(copy)
        window.toggle_portfolio()
        settle(window, lambda: not window.portfolio.removed)
        assert binned == [name] and (project/name).exists() and len(window.session.photos) == 3
        # A copy deleted by hand leaves the portfolio; an absent original would not.
        window.close_project()
        window.pool.waitForDone()
        other = next(p.name for p in (place/'ancien projet').glob('*.jpg'))
        (place/'ancien projet'/other).unlink()
        window.set_folder(project)
        assert not window.portfolio.contains(project/other)
        assert window.portfolio.contains(extra)
        window.close()
    finally:
        triphoto_portfolio.move_to_trash = original_trash
    clear_settings()


def test_activity_bar(base):
    clear_settings()
    root = make_project(base/'activite', 4)
    window = open_window(root)
    window.show()
    activity = window.activity
    settle(window, lambda: not window.background_tasks() and window.review.analysis is None, 15000)
    activity.poll()
    assert not activity.isVisible()
    # Silent at first, for what ends quickly, then with its figures; gone with the work.
    review = window.review
    review.analysis_left = 40
    activity.poll()
    assert not activity.isVisible()
    activity.since -= activity.DELAY+.1
    activity.poll()
    assert activity.isVisible() and 'Analyse' in activity.label.text() and '0 sur 40' in activity.label.text()
    review.analysis_left = 10
    activity.poll()
    assert '30 sur 40' in activity.label.text() and activity.bar.value() == 30
    # A preview loading on the way does not take the place of the larger work, which keeps its count.
    real_tasks = window.background_tasks
    window.background_tasks = lambda: [('Chargement des aperçus', 1), ('Analyse des couleurs', 10)]
    activity.poll()
    assert '30 sur 40' in activity.label.text()
    window.background_tasks = real_tasks
    assert activity.geometry().bottom() <= window.height() and activity.x() == window.view_panel.width()+12
    # Above the note of the other views, which it must not cover.
    window.set_view('wall')
    activity.poll()
    wait(50)
    assert activity.geometry().bottom() < review.mapTo(window, review.bar.geometry().topLeft()).y()
    window.set_view('classic')
    review.analysis_left = 0
    activity.poll()
    assert not activity.isVisible() and not activity.peaks
    # It never takes a click from what lies under it.
    assert activity.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    # What blocks the interface is said at once, and the next look clears it.
    activity.announce('Ouverture du projet…')
    assert activity.isVisible()
    activity.poll()
    assert not activity.isVisible()
    window.close()
    clear_settings()


def test_custom_sorting(base):
    # The logic alone: what went where, and what an export would write.
    session = triphoto_custom.Sorting.create(base/'tri seul', 'Concours')
    photos = [{'path': str(base/'a'/f'{i}.jpg'), 'gallery': 'a', 'relative': f'{i}.jpg'} for i in range(3)]+[{'path': str(base/'b'/'0.jpg'), 'gallery': 'b', 'relative': '0.jpg'}]
    session.set_photos(photos)
    session.buckets.append({'id': 'x', 'title': 'Concours : Fearless', 'subtitle': '', 'color': session.free_color()})
    session.buckets.append({'id': 'y', 'title': 'Livre', 'subtitle': 'Sélection', 'color': session.free_color()})
    assert session.bucket('x')['color'] != session.bucket('y')['color'], 'a different color by default'
    assert session.free_color('y') == session.bucket('y')['color'], 'its own color is free for the bucket being edited'
    a0, a1, a2, b0 = [photo['path'] for photo in photos]
    session.add(a0, 'x')
    session.add(a0, 'y')
    session.add(b0, 'x')
    session.add(a0, 'x')
    assert session.buckets_of(a0) == ['x', 'y'] and session.count('x') == 2
    assert [p['path'] for p in session.order('y')] == [a0] and len(session.order()) == 4
    # One bucket, both at once, one or the other, one but not the other, none.
    passing = lambda: {path for path in (a0, a1, a2, b0) if session.matches(path)}
    assert passing() == {a0, a1, a2, b0}
    session.set_rule(include=['x'])
    assert passing() == {a0, b0}
    session.set_rule(include=['x', 'y'], all=True)
    assert passing() == {a0}
    session.set_rule(all=False)
    assert passing() == {a0, b0}
    session.set_rule(include=['x'], exclude=['y'])
    assert passing() == {b0}
    session.set_rule(include=[], exclude=[], none=True)
    assert passing() == {a1, a2}
    session.set_rule(none=False)
    # The same file name from two galleries, a title Windows would refuse.
    plan = session.export_plan()
    assert ('Concours - Fearless', '0.jpg', a0) in plan and ('Concours - Fearless', '0 (2).jpg', b0) in plan and ('Livre', '0.jpg', a0) in plan
    assert session.undo() == b0 and not session.buckets_of(b0)
    session.add(b0, 'x')
    assert session.undo() == b0 and session.undo() == a0 and session.buckets_of(a0) == ['x']
    session.add(b0, 'x')
    # A narrower choice keeps what has been sorted, and leaves the rest.
    session.set_photos([photos[1]])
    assert {p['path'] for p in session.photos} == {a1, a0, b0}
    session.remove_bucket('x')
    assert session.buckets_of(a0) == [] and len(session.buckets) == 1
    session.save()
    assert triphoto_custom.Sorting.load(session.path).data == session.data


def test_custom_view(base):
    clear_settings()
    wedding = make_project(base/'custom'/'mariage', 6)
    baptism = make_project(base/'custom'/'bapteme', 4)
    place = base/'custom portfolio'
    place.mkdir()
    triphoto_qt.app_settings().setValue('portfolio_folder', str(place))
    before = {str(p): p.read_bytes() for root in (wedding, baptism) for p in root.glob('*.jpg')}
    window = open_window(baptism)
    window.session.photos[0].stars = 5
    window.select_photo(window.session.photos[1])
    window.toggle_portfolio()
    window.close_project()
    window.set_folder(wedding)
    for photo in window.session.photos[:2]:
        photo.stars = 5
    window.session.photos[5].stars, window.session.photos[5].pick = 5, 'reject'
    project_photos = list(window.session.photos)
    window.save_session()
    window.close()
    baptism_notes = triphoto.PhotoSession(baptism)
    baptism_notes.photos[0].stars = 5
    baptism_notes.save()
    window = open_window(wedding)
    window.resize(1300, 800)
    window.show()
    custom = window.custom
    fired = lambda key: next(s for s in window.shortcuts if s.key().toString() == key).activated.emit()
    # On the welcome screen, under the recent projects, whatever the project was.
    window.close_project()
    assert window.pages.currentIndex() == 0 and not window.home.custom_cards
    window.home.custom_button.click()
    assert window.custom_active and window.pages.currentIndex() == 3 and not window.export_button.isVisible()
    setup = custom.setup
    settle(window, lambda: not setup.scanning(), 10000)
    assert not setup.go.isEnabled(), 'it needs a name'
    setup.name.setText('Concours 2027')
    rows = {row.title: row for row in setup.rows.values()}
    assert set(rows) == {'Portfolio', 'mariage', 'bapteme'}
    # None chosen at first, and it needs at least one.
    assert not any(row.check.isChecked() for row in rows.values()) and not setup.chosen() and not setup.go.isEnabled()
    # A click anywhere on the row, as far from the box as can be, and not on its filters.
    wait(50)
    row = rows['mariage']
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, row.rect().center())
    assert row.check.isChecked() and setup.go.isEnabled()
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, row.rect().center())
    assert not row.check.isChecked()
    setup.select_all()
    # Six and four photos, the one of the portfolio being one of the four.
    assert len(setup.chosen()) == 9, len(setup.chosen())
    setup.select_portfolio()
    assert len(setup.chosen()) == 1 and rows['Portfolio'].check.isChecked() and not rows['mariage'].check.isChecked()
    # A filter per gallery. The rejected photo stays out, as in an export.
    setup.select_none()
    rows['mariage'].check.setChecked(True)
    rows['mariage'].stars.setCurrentIndex(rows['mariage'].stars.findData(5))
    assert '2 sur 6' in rows['mariage'].count.text()
    rows['bapteme'].check.setChecked(True)
    assert len(setup.chosen()) == 6
    rows['bapteme'].stars.setCurrentIndex(rows['bapteme'].stars.findData(5))
    assert len(setup.chosen()) == 3
    setup.go.click()
    assert custom.currentWidget() is custom.work and custom.session.name == 'Concours 2027'
    session = custom.session
    assert session.path.exists() and len(session.photos) == 3
    settle(window, lambda: custom.current_image() is not None, 10000)
    wait(50)
    # Buckets: a title, a description, and the same name only once.
    dialog = triphoto_custom.BucketDialog(window, {'fearless'})
    dialog.title.setText('Fearless')
    assert not dialog.go.isEnabled()
    dialog.title.setText('Fearless 2027')
    assert dialog.go.isEnabled() and dialog.color() == triphoto_custom.BUCKET_COLORS[0]
    dialog.colors[3].click()
    assert dialog.color() == triphoto_custom.BUCKET_COLORS[3]
    # With no bucket yet, the row offers to create one and takes no drop.
    assert set(custom.cards) == {None} and custom.card_at(custom.cards[None].mapToGlobal(custom.cards[None].rect().center())) is None
    offered = []
    custom.ask_bucket = lambda *args: offered.append(1)
    wait(50)
    QTest.mouseClick(custom.cards[None], Qt.MouseButton.LeftButton)
    assert offered
    answers = [('Fearless', 'Mariage, mars', '#ff3b30'), ('Livre', '', '#0a84ff')]
    custom.ask_bucket = lambda *args: answers.pop(0)
    custom.add_bucket()
    custom.add_bucket()
    first, second = [bucket['id'] for bucket in session.buckets]
    assert session.bucket(first)['subtitle'] == 'Mariage, mars' and set(custom.cards) == {first, second, None}
    assert session.bucket(second)['color'] == '#0a84ff'
    wait(50)
    # The menu of the bucket at the far right stays inside the window.
    menu = triphoto_custom.PopupMenu(window, [('Voir les photos…', lambda: None)])
    menu.open_at(window.frameGeometry().topRight()+QPoint(0, 300))
    assert menu.frameGeometry().right() <= window.frameGeometry().right() and menu.frameGeometry().top() >= window.frameGeometry().top()
    menu.close()
    # The three dots open the menu, and do not put the photo in the bucket.
    menus = []
    card = custom.cards[first]
    card.show_menu = lambda where: menus.append(where)
    QTest.mouseClick(card, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, card.dots().center().toPoint())
    assert menus and first not in custom.current_buckets()
    # The keys of the ribbon, in both directions, at the ends too.
    assert custom.position() == (0, 3)
    fired('V')
    assert custom.position() == (1, 3)
    fired('Right')
    assert custom.position() == (2, 3)
    fired('V')
    assert custom.position() == (2, 3)
    fired('C')
    fired('Left')
    assert custom.position() == (0, 3)
    settle(window, lambda: custom.current_image() is not None, 10000)
    wait(250)
    window.grab()
    # The zoom stays from one photo to the next, and the name of the photo is in the bar.
    stage = custom.stage
    assert custom.photo_name.text()
    width = stage.photo_rect().width()
    fired('+')
    assert stage.zoom > 1 and stage.photo_rect().width() > width and custom.zoom_button.text() != '100 %'
    zoom = stage.zoom
    fired('V')
    assert stage.zoom == zoom
    fired('C')
    settle(window, lambda: custom.current_image() is not None, 10000)
    fired('0')
    assert stage.zoom == 1 and custom.zoom_button.text() == '100 %'
    # The background is that of the other views.
    window.set_background('#ffffff')
    assert window.viewer_picker.swatches[0].isChecked()
    # A real drag from the photo to a bucket: it goes there, and the next photo comes.
    stage = custom.stage
    dragged = custom.current()['path']
    start = stage.photo_rect().center().toPoint()
    card = custom.cards[first]
    target = card.mapToGlobal(card.rect().center())
    QTest.mousePress(stage, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    stage.mouseMoveEvent(_Move(QPointF(start)+QPointF(30, 30), stage))
    assert stage.dragging and custom.ghost.isVisible()
    stage.mouseMoveEvent(_Move(QPointF(stage.mapFromGlobal(target)), stage))
    assert card.hovered
    stage.mouseReleaseEvent(_Move(QPointF(stage.mapFromGlobal(target)), stage))
    assert not custom.ghost.isVisible() and not card.hovered
    assert session.buckets_of(dragged) == [first] and custom.position() == (1, 3)
    # Dropped outside a zone, nothing happens.
    custom.drop_at(custom.work.mapToGlobal(custom.work.rect().topLeft()))
    assert custom.position() == (1, 3)
    # By key too, and Ctrl+Z takes it back.
    fired('2')
    second_photo = next(p['path'] for p in session.photos if second in session.buckets_of(p['path']))
    assert custom.position() == (2, 3)
    fired('Ctrl+Z')
    assert not session.buckets_of(second_photo) and custom.position() == (1, 3)
    # The key of the zero star leaves the photo without a bucket and moves on; a click changes it in place.
    custom.key_bucket(0)
    assert custom.position() == (2, 3) and not session.buckets_of(second_photo)
    held = custom.current()['path']
    custom.clicked_card(first)
    custom.clicked_card(second)
    assert session.buckets_of(held) == [first, second] and custom.position() == (2, 3)
    custom.clicked_card(second)
    assert session.buckets_of(held) == [first]
    QTest.mouseClick(custom.cards[second], Qt.MouseButton.LeftButton)
    assert second in session.buckets_of(held)
    # Every photo of a bucket, large, to take out those that are there by mistake.
    sheet = triphoto_custom.BucketSheet(custom, second)
    assert len(sheet.tiles) == 1 and sheet.tiles[0].photo['path'] == held
    sheet.remove(held)
    assert second not in session.buckets_of(held) and not sheet.tiles
    sheet.restore()
    assert second in session.buckets_of(held) and len(sheet.tiles) == 1
    sheet.done(0)
    # Back to the photo for the views: sorted photos leave "Sans bucket" as they are sorted.
    window.selection.set_rule(none=True)
    window.filters_changed()
    assert custom.position()[1] == 1
    custom.key_bucket(1)
    assert custom.position()[1] == 0 and custom.current() is None and session.count(first) == 3
    window.grab()
    window.selection.set_rule(none=False, include=[first])
    window.filters_changed()
    assert custom.position()[1] == 3
    window.selection.set_rule(include=[])
    window.filters_changed()
    # It is all saved, and several sessions live side by side.
    custom.save()
    assert triphoto_custom.Sorting.load(session.path).count(first) == 3
    fired('Esc')
    assert not window.custom_active and window.pages.currentIndex() == 0 and custom.session is None
    assert len(window.home.custom_cards) == 1
    card = next(iter(window.home.custom_cards.values()))
    assert card.name.text() == 'Concours 2027'
    window.open_custom()
    settle(window, lambda: not setup.scanning(), 10000)
    setup.name.setText('Autre')
    setup.select_portfolio()
    setup.go.click()
    assert len(custom.session.photos) == 1 and custom.session.path != session.path
    fired('Esc')
    assert len(window.home.custom_cards) == 2
    reopened = next(found for found in custom.store.sessions() if found.name == 'Concours 2027')
    window.open_custom(reopened)
    assert reopened.count(first) == 3 and window.project_button.text() == 'Concours 2027'
    # Modifying the selection brings back the former choice, and keeps the sorted photos.
    custom.edit_selection()
    settle(window, lambda: not setup.scanning(), 10000)
    rows = {row.title: row for row in setup.rows.values()}
    assert rows['mariage'].check.isChecked() and rows['mariage'].stars.currentData() == 5 and not rows['Portfolio'].check.isChecked()
    assert len(setup.chosen()) == 3
    setup.select_portfolio()
    setup.go.click()
    # The one of the portfolio joins the three already sorted, which stay.
    assert len(custom.session.photos) == 4 and custom.session.count(first) == 3
    # The export: a folder per bucket, under their titles as typed, originals untouched.
    out = base/'custom export'
    out.mkdir()
    custom.ask_export_folder = lambda: str(out)
    # The message at the end of an export waits for an answer.
    real_window_ask = triphoto_qt.ask
    triphoto_qt.ask = lambda *args: 'close'
    custom.export()
    settle(window, lambda: window.export_job is None, 15000)
    finished = out/'Concours 2027'
    assert {p.name for p in finished.iterdir()} == {'Fearless', 'Livre'}
    assert len(list((finished/'Fearless').iterdir())) == 3 and len(list((finished/'Livre').iterdir())) == 1
    # Two photos of the same name from two galleries both arrive.
    assert {p.read_bytes() for p in (finished/'Fearless').iterdir()} <= set(before.values())
    # A second export takes a dated name and leaves the first one.
    custom.export()
    settle(window, lambda: window.export_job is None, 15000)
    assert len(list(out.iterdir())) == 2 and not [p for p in out.iterdir() if p.name.endswith('.en_cours')]
    triphoto_qt.ask = real_window_ask
    # Cancelled, it leaves nothing.
    job = triphoto_custom.ExportJob(custom.session.export_plan(), out/'annule')
    job.cancelled.set()
    job.run()
    assert not (out/'annule').exists() and not (out/'annule.en_cours').exists()
    assert {str(p): p.read_bytes() for root in (wedding, baptism) for p in root.glob('*.jpg')} == before, 'originals changed'
    # A bucket removed takes no photo with it.
    real_ask = triphoto_custom.ask
    triphoto_custom.ask = lambda *args: 'delete'
    try:
        custom.delete_bucket(first)
        assert custom.session.bucket(first) is None and len(custom.session.photos) == 4
        # And a session goes to the bin, from the list.
        moved = []
        real_trash = triphoto_custom.move_to_trash
        triphoto_custom.move_to_trash = lambda path: moved.append(path) or (shutil.rmtree(path) if Path(path).is_dir() else Path(path).unlink())
        doomed = custom.session
        window.go_home()
        custom.delete_session(doomed)
        triphoto_custom.move_to_trash = real_trash
        assert moved == [doomed.path, doomed.path.with_suffix('')] and len(window.home.custom_cards) == 1
    finally:
        triphoto_custom.ask = real_ask
    window.close()
    clear_settings()


def test_selection_views(base):
    clear_settings()
    root = make_project(base/'vues custom', 6)
    window = open_window(root)
    window.resize(1400, 900)
    window.show()
    paths = [str(photo.path) for photo in window.session.photos]
    window.close_project()
    assert not window.home_button.isVisible()
    custom = window.custom
    sorting = custom.store.create('Revue')
    sorting.set_photos([{'path': path, 'gallery': 'vues custom', 'relative': Path(path).name} for path in paths])
    sorting.buckets.extend([{'id': 'a', 'title': 'AG', 'subtitle': '', 'color': '#ff3b30'}, {'id': 'b', 'title': 'WPJA', 'subtitle': '', 'color': '#0a84ff'}])
    sorting.add(paths[0], 'a')
    sorting.add(paths[1], 'a')
    sorting.add(paths[2], 'b')
    sorting.add(paths[3], 'a')
    sorting.add(paths[3], 'b')
    sorting.save()
    # It opens on its buckets, and the other views are there, with the way back.
    window.open_custom(sorting)
    assert window.custom_active and window.pages.currentIndex() == 3 and window.home_button.isVisible()
    assert window.view_buttons['classic'].isChecked() and window.bucket_button.isChecked()
    assert not window.bucket_strip.isVisible() and len(window.session.photos) == 6
    window.set_view('wall')
    review = window.review
    assert window.review_active and not window.custom_active and window.bucket_strip.isVisible()
    settle(window, lambda: len(review.info) == 6, 15000)
    wall = review.view()
    wall.resize(1400, 760)
    assert not wall.drop_zones() and window.photo_buckets(next(p for p in window.photos if str(p.path) == paths[3])) == [('AG', '#ff3b30'), ('WPJA', '#0a84ff')]
    window.grab()
    # The strip holds the buckets only, and a click puts the picked photo in one or takes it out.
    strip = window.bucket_strip
    wait(50)
    rects = dict(strip.rects())
    assert set(rects) == {'a', 'b'}
    free = next(p for p in window.photos if str(p.path) == paths[4])
    wall.pick(free)
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rects['b'].center().toPoint())
    assert sorting.buckets_of(paths[4]) == ['b']
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rects['b'].center().toPoint())
    assert not sorting.buckets_of(paths[4])
    # The panel of the filter: one click requires a bucket, a second one refuses it, a third forgets it.
    rule = window.bucket_rule
    assert rule.isVisibleTo(window.filter_panel)
    def state(key, value):
        rule.buttons[key][value].click()
        wait(10)
    assert rule.buttons['a'][''].isChecked()
    state('a', 'include')
    assert rule.buttons['a']['include'].isChecked() and not rule.buttons['a'][''].isChecked()
    assert {str(p.path) for p in window.photos} == {paths[0], paths[1], paths[3]} and 'AG' in window.filter_chip.toolTip()
    state('b', 'include')
    assert {str(p.path) for p in window.photos} == {paths[0], paths[1], paths[2], paths[3]}
    assert rule.options.isVisibleTo(rule) and 'AG ou WPJA' in window.filter_chip.toolTip()
    assert window.filter_chip.text().startswith('Filtre :'), 'it says which bucket is shown'
    # Two of them included stay so, each with its own state.
    assert rule.buttons['a']['include'].isChecked() and rule.buttons['b']['include'].isChecked()
    rule.all_button.click()
    wait(10)
    assert {str(p.path) for p in window.photos} == {paths[3]} and 'AG et WPJA' in window.filter_chip.toolTip()
    rule.any_button.click()
    wait(10)
    state('b', 'exclude')
    assert {str(p.path) for p in window.photos} == {paths[0], paths[1]} and 'sans WPJA' in window.filter_chip.toolTip()
    window.bucket_rule.none_button.click()
    assert {str(p.path) for p in window.photos} == {paths[4], paths[5]}
    window.clear_filters()
    assert len(window.photos) == 6 and not sorting.rule()['include']
    window.grab()
    # The Buckets view shows what the filter lets through, and the filter is at hand there.
    state('b', 'include')
    window.set_view('classic')
    assert custom.position()[1] == 2 and window.filter_chip.isVisible()
    window.set_view('wall')
    state('b', '')
    assert len(window.photos) == 6
    # Seen through a single bucket, a drop moves the photo out of it and leaves its other buckets.
    state('a', 'include')
    both = next(p for p in window.photos if str(p.path) == paths[3])
    window.move_to_bucket(both, 'b')
    assert sorting.buckets_of(paths[3]) == ['b'] and both not in window.photos
    window.undo()
    assert sorting.buckets_of(paths[3]) == ['a', 'b'] and any(p is both for p in window.photos)
    # Seen through all of them, it replaces them; the narrow part of a zone keeps it where it is.
    state('a', '')
    assert len(window.photos) == 6
    only = next(p for p in window.photos if str(p.path) == paths[0])
    window.move_to_bucket(only, 'b', 'add')
    assert sorting.buckets_of(paths[0]) == ['a', 'b']
    window.undo()
    window.move_to_bucket(only, 'b')
    assert sorting.buckets_of(paths[0]) == ['b']
    window.undo()
    assert sorting.buckets_of(paths[0]) == ['a']
    # A real drag from the grid to the strip, on each part of a zone.
    wall.resize(1400, 760)
    wall.bar.setValue(0)
    wall.relayout()
    rect, dragged = next(tile for tile in wall.visible_tiles() if len(sorting.buckets_of(str(tile[1].path))) == 1 and sorting.buckets_of(str(tile[1].path)) != ['b'])
    moving = str(dragged.path)
    home = sorting.buckets_of(moving)
    start = rect.center().toPoint()
    def drag_to(where):
        QTest.mousePress(wall, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        wall.mouseMoveEvent(_Move(QPointF(start)+QPointF(40, 40), wall))
        assert wall.dragging and strip.dragging
        global_point = strip.mapToGlobal(where(dict(strip.rects())).toPoint())
        local = QPointF(wall.mapFromGlobal(global_point))
        wall.mouseMoveEvent(_Move(local, wall))
        assert strip.hovered is not None and window.bucket_ghost.isVisible()
        window.grab()
        wall.mouseReleaseEvent(_Move(local, wall))
        assert not strip.dragging and not window.bucket_ghost.isVisible()
    drag_to(lambda rects: QPointF(rects['b'].right()-10, rects['b'].center().y()))
    assert sorting.buckets_of(moving) == [*home, 'b']
    window.undo()
    drag_to(lambda rects: QPointF(rects['b'].left()+10, rects['b'].center().y()))
    assert sorting.buckets_of(moving) == ['b']
    window.undo()
    # On the bucket it is already in, a drop takes it out, wherever it falls.
    drag_to(lambda rects: QPointF(rects[home[0]].right()-10, rects[home[0]].center().y()))
    assert not sorting.buckets_of(moving)
    window.undo()
    assert sorting.buckets_of(moving) == home
    # Everything picked goes together, and the keys of the notes pick the buckets.
    wall.pick(window.photos[0])
    wall.select_all()
    window.rate_key(2)
    assert all('b' in sorting.buckets_of(path) for path in paths)
    window.undo()
    assert not sorting.buckets_of(paths[4])
    # The table too, and the views without a drag keep their keys.
    window.set_view('toile')
    toile = review.view()
    toile.resize(1400, 760)
    wait(50)
    window.grab()
    # On the table, all the prints have the size of the best ones.
    assert max(toile.tile_rect(window.photos[0]).width(), toile.tile_rect(window.photos[0]).height()) == 400
    window.set_view('color')
    wait(50)
    window.grab()
    # Tindr decides about one bucket, without the strip.
    window.set_view('swipe')
    swipe = review.view()
    assert not strip.isVisible() and review.swipe_bucket_pill.isVisible() and not review.swipe_left.parentWidget().isVisible()
    review.swipe_bucket.setCurrentIndex(review.swipe_bucket.findData('b'))
    assert review.swipe_labels() == ('Retirer de WPJA', 'Ajouter à WPJA')
    card = swipe.current()
    keys = list(sorting.buckets_of(card.relative_path))
    review.apply_swipe(card, 1)
    assert 'b' in sorting.buckets_of(card.relative_path)
    window.undo()
    assert sorting.buckets_of(card.relative_path) == keys
    review.apply_swipe(card, -1)
    assert 'b' not in sorting.buckets_of(card.relative_path)
    window.undo()
    assert sorting.buckets_of(card.relative_path) == keys
    # Ctrl+Y puts it back in the bucket, here as on the Buckets page.
    review.apply_swipe(card, 1)
    window.undo()
    assert 'b' not in sorting.buckets_of(card.relative_path) and window.redo_button.isEnabled()
    window.redo()
    assert 'b' in sorting.buckets_of(card.relative_path) and not window.redo_button.isEnabled()
    window.show_buckets()
    window.undo()
    assert 'b' not in sorting.buckets_of(card.relative_path) and window.redo_button.isEnabled()
    window.redo()
    assert 'b' in sorting.buckets_of(card.relative_path)
    window.set_view('swipe')
    window.undo()
    assert sorting.buckets_of(card.relative_path) == keys
    # Instagram has its own light and dark, apart from the background of the other views.
    window.set_view('instagram')
    insta = review.view()
    background = window.viewer.background
    was = insta.light
    (review.dark_button if was else review.light_button).click()
    assert insta.light != was and window.viewer.background == background
    assert review.theme_row.isVisible() and not review.picker.isVisible()
    insta.set_light(True)
    assert insta.CARD == '#ffffff' and insta.SCREEN == '#000000'
    window.grab()
    insta.set_light(False)
    assert insta.CARD == '#000000' and insta.SCREEN != '#000000'
    window.grab()
    assert strip.isVisible()
    window.set_review(False)
    window.grab()
    assert window.bucket_strip.isVisible() and not window.review_active
    # Back on the buckets, on the photo left in the other view.
    current = window.current_photo()
    window.set_view('classic')
    assert window.custom_active and custom.current()['path'] == current.relative_path
    # The way back, from every mode, and the project list knows nothing of it.
    window.home_button.click()
    assert window.pages.currentIndex() == 0 and window.selection is None and not window.home_button.isVisible()
    assert not window.bucket_strip.isVisible()
    assert not any(entry['path'] == str(sorting.path.with_suffix('')) for entry in window.recents.entries())
    assert triphoto_custom.Sorting.load(sorting.path).buckets_of(paths[1]) == ['a']
    window.close()
    clear_settings()


def test_repair_links(base):
    clear_settings()
    old = make_project(base/'liens'/'avant'/'Mariage', 4)
    place = base/'liens portfolio'
    place.mkdir()
    triphoto_qt.app_settings().setValue('portfolio_folder', str(place))
    answers = []
    real_ask, real_inform = triphoto_qt.ask, triphoto_qt.inform
    triphoto_qt.ask = lambda parent, title, text, buttons: answers.append(title) or 'use'
    triphoto_qt.inform = lambda parent, title, text: answers.append(title)
    try:
        window = open_window(old)
        photos = list(window.session.photos)
        names = [p.path.name for p in photos]
        photos[0].stars = 5
        window.select_photo(photos[1])
        window.toggle_portfolio()
        window.save_session()
        sorting = window.custom.store.create('Liens')
        sorting.set_photos([{'path': str(p.path), 'gallery': 'Mariage', 'relative': p.path.name} for p in photos])
        sorting.buckets.append({'id': 'a', 'title': 'AG', 'subtitle': '', 'color': '#ff3b30'})
        sorting.add(str(photos[0].path), 'a')
        sorting.save()
        window.close_project()
        window.pool.waitForDone()
        window.review.analysis_pool.waitForDone()
        # The project moved somewhere else, deeper: the parent folder is enough to find it.
        new_parent = base/'liens'/'apres'/'disque'
        new_parent.mkdir(parents=True)
        moved = new_parent/'Mariage'
        os.replace(old, moved)
        window.ask_folder = lambda title: str(base/'liens'/'apres')
        recorded = next(e['path'] for e in window.recents.entries() if Path(e['path']).name.lower() == 'mariage')
        assert window.repair_recent(recorded)
        assert triphoto.norm_path(window.session.root) == triphoto.norm_path(moved) and len(window.session.photos) == 4
        assert next(p for p in window.session.photos if p.path.name == names[0]).stars == 5, 'its notes moved with it'
        # The recent project, the portfolio and the selections follow.
        assert [e for e in window.recents.entries() if triphoto.norm_path(e['path']) == triphoto.norm_path(moved)]
        assert not [e for e in window.recents.entries() if e['path'] == recorded]
        assert Path(next(iter(window.portfolio.items.values()))['source']).is_file()
        window.close_project()
        again = next(found for found in window.custom.store.sessions() if found.name == 'Liens')
        assert all(Path(photo['path']).is_file() for photo in again.photos)
        assert again.count('a') == 1 and Path(next(iter(again.data['assigned']))).is_file()
        # An empty folder says so, and changes nothing.
        empty = base/'liens'/'vide'
        empty.mkdir()
        window.ask_folder = lambda title: str(empty)
        assert not window.repair_recent(str(moved)) and answers[-1] == 'Aucune photo ici'
        # Two projects inside: a closer choice is asked.
        make_project(base/'liens'/'deux'/'Un', 1)
        make_project(base/'liens'/'deux'/'Deux', 1)
        for name in ('Un', 'Deux'):
            triphoto.PhotoSession(base/'liens'/'deux'/name).save()
        window.ask_folder = lambda title: str(base/'liens'/'deux')
        assert not window.repair_recent(str(moved)) and answers[-1] == 'Plusieurs projets'
        # Photos alone, without the notes: it says so, and the links are repaired on request.
        loose = base/'liens'/'en vrac'/'sous dossier'
        loose.mkdir(parents=True)
        window.pool.waitForDone()
        for name in names:
            os.replace(moved/name, loose/name)
        window.ask_folder = lambda title: str(base/'liens'/'en vrac')
        assert window.repair_recent(str(moved), False) and answers[-1] == 'Pas de notes ici'
        # Photos lost one by one in a selection: found by their name in a folder.
        window.open_custom(next(found for found in window.custom.store.sessions() if found.name == 'Liens'))
        assert len(window.session.absent) == 4 and not window.session.photos
        window.repair_photos()
        assert not window.session.absent and len(window.session.photos) == 4
        assert window.selection.count('a') == 1 and Path(next(iter(window.selection.data['assigned']))).parent == loose
        window.close()
    finally:
        triphoto_qt.ask, triphoto_qt.inform = real_ask, real_inform
    clear_settings()


def test_buckets_in_a_project(base):
    clear_settings()
    root = make_project(base/'seaux', 6)
    window = open_window(root)
    custom, strip = window.custom, window.bucket_strip
    names = [p.relative_path for p in window.photos]
    # Outside bucket mode: the notes, and nothing written beside them.
    assert not window.bucket_mode and not strip.isVisible() and window.bucket_button.isVisible() and window.bucket_section.isHidden()
    window.rate_key(4)
    assert window.current_photo().stars == 4
    window.save_session()
    custom.save()
    assert not (window.session.session_dir/triphoto_custom.BUCKETS_FILE).exists()
    # In bucket mode Classique holds the photo in large, and the keys fill the buckets.
    window.bucket_button.click()
    assert window.bucket_mode and window.custom_active and window.view_buttons['classic'].isChecked() and window.view_panel.isVisible()
    assert None in custom.cards and not window.bucket_section.isHidden()
    window.buckets.buckets.append({'id': 'a', 'title': 'Livre', 'subtitle': '', 'color': triphoto_custom.BUCKET_COLORS[0]})
    custom.buckets_changed()
    assert 'a' in custom.cards and None in custom.cards
    settle(window, lambda: custom.current_image() is not None, 8000)
    first = custom.current()['path']
    assert first == window.current_photo().relative_path
    window.rate_key(1)
    assert window.buckets.buckets_of(first) == ['a'] and custom.current()['path'] != first, 'put in the bucket, and on to the next one'
    assert window.photos[0].stars == 4, 'its note stays'
    # The strip, in the other views, and the same keys.
    window.set_view('wall')
    assert strip.isVisible() and not window.custom_active
    wall = window.review.view()
    assert window.photo_buckets(window.photos[0]) == [('Livre', triphoto_custom.BUCKET_COLORS[0])]
    # The filter, in or out of the mode.
    window.bucket_rule.buttons['a']['include'].click()
    assert [p.relative_path for p in window.photos] == [first]
    window.clear_filters()
    window.bucket_button.click()
    assert not window.bucket_mode and not strip.isVisible() and window.review_active and not window.bucket_section.isHidden()
    assert window.photo_buckets(window.photos[0]), 'still shown on the photos'
    window.set_view('classic')
    assert window.pages.currentIndex() == 1
    # Back later, or after the folder moved: the buckets follow, by relative path.
    window.close_project()
    window.pool.waitForDone()
    window.review.analysis_pool.waitForDone()
    moved = base/'seaux ailleurs'
    os.replace(root, moved)
    window.set_folder(moved)
    settle(window, lambda: window.current_photo() is not None)
    assert window.buckets.buckets_of(first) == ['a'] and window.buckets.file_of(first) == moved/first
    # The export offers one folder per bucket.
    destination = base/'seaux export'
    destination.mkdir()
    real_ask = triphoto_qt.ask
    triphoto_qt.ask = lambda parent, title, text, buttons: 'buckets'
    custom.ask_export_folder = lambda: str(destination)
    real_inform = triphoto_qt.inform
    triphoto_qt.inform = lambda *args: None
    real_custom_inform = triphoto_custom.inform
    triphoto_custom.inform = lambda *args: None
    try:
        window.export()
        settle(window, lambda: window.export_job is None, 8000)
        assert any(found.name == first for found in destination.rglob('*.jpg'))
    finally:
        triphoto_qt.ask, triphoto_qt.inform, triphoto_custom.inform = real_ask, real_inform, real_custom_inform
    window.close()
    clear_settings()


def test_same_words_everywhere(base):
    clear_settings()
    root = make_project(base/'mots', 4)
    window = open_window(root)
    photos = window.photos
    # Each marking system lists its own commands only.
    dialog = triphoto_prefs.PreferencesDialog(window, window.settings)
    rows = {name: row for row, (name, _, _) in enumerate(triphoto_prefs.ACTIONS)}
    assert not dialog.table.isRowHidden(rows['flag']) and dialog.table.isRowHidden(rows['reject'])
    next(button for button in dialog.marker.buttons() if button.property('key') == 'pick').setChecked(True)
    assert dialog.table.isRowHidden(rows['flag']) and not dialog.table.isRowHidden(rows['reject'])
    dialog.reject()
    # The export dialog names the filter like the panel does.
    export = triphoto_qt.ExportDialog(window)
    assert [label.text() for label in export.findChildren(triphoto_qt.QLabel) if label.text() in ('Note', 'Drapeau', 'Notes', 'Marqueur')] == ['Note', 'Drapeau']
    export.reject()
    # A selection shows the signet of the portfolio, so it can filter on it too.
    window.portfolio.set_folder(base/'mots copies') if (base/'mots copies').mkdir() is None else None
    window.toggle_portfolio()
    window.close_project()
    window.pool.waitForDone()
    window.review.analysis_pool.waitForDone()
    sorting = window.custom.store.create('Mots')
    sorting.data['photos'] = [{'path': str(p.path), 'gallery': 'x', 'relative': p.path.name} for p in photos]
    sorting.save()
    window.open_custom(sorting)
    window.set_view('wall')
    settle(window, lambda: len(window.photos) == 4)
    assert not window.choices.portfolio.isHidden()
    window.choices.portfolio.click()
    settle(window, lambda: len(window.photos) == 1)
    window.choices.portfolio.click()
    window.close()
    clear_settings()


def test_search_links(base):
    clear_settings()
    root = make_project(base/'recherche'/'avant'/'Temp Mariage', 6)
    triphoto.PhotoSession(root).save()
    answers = []
    real_inform = triphoto_qt.inform
    triphoto_qt.inform = lambda parent, title, text: answers.append(text)
    try:
        window = open_window(root)
        paths = [str(p.path) for p in window.session.photos]
        (base/'recherche'/'copies').mkdir()
        window.portfolio.set_folder(base/'recherche'/'copies')
        window.close_project()
        window.pool.waitForDone()
        window.review.analysis_pool.waitForDone()
        sorting = window.custom.store.create('Recherche')
        kept = base/'recherche'/'ailleurs'
        kept.mkdir()
        shutil.copy2(paths[0], kept/Path(paths[0]).name)
        sorting.data['sources'] = [{'key': str(root), 'stars': 'all', 'marker': 'all', 'publish': False}]
        sorting.data['buckets'] = [{'id': 'a', 'title': 'A', 'subtitle': '', 'color': '#30d158'}, {'id': 'b', 'title': 'B', 'subtitle': '', 'color': '#0a84ff'}]
        sorting.data['photos'] = [{'path': path, 'gallery': 'x', 'relative': Path(path).name} for path in paths]
        sorting.data['assigned'] = {paths[1]: ['a']}
        sorting.save()
        # The project moved far from its old place, and one of its photos has a stray copy.
        disk = base/'recherche'/'disque'
        (disk/'jeux'/'profond').mkdir(parents=True)
        (disk/'Préselection').mkdir()
        moved = disk/'Préselection'/'Temp Mariage'
        os.replace(root, moved)
        (disk/'jeux'/'profond'/Path(paths[1]).name).write_bytes(b'copie')
        # A lost copy of a photo the selection also holds elsewhere becomes that photo, with its buckets.
        twin = next(photo for photo in moved.iterdir() if photo.name == Path(paths[0]).name)
        again = next(session for session in window.custom.store.sessions() if session.name == 'Recherche')
        lost_twin = str(base/'recherche'/'parti'/'Mariage'/'autre.jpg')
        shutil.copy2(twin, moved/'autre.jpg')
        again.data['photos'] += [{'path': str(moved/'autre.jpg'), 'gallery': 'x', 'relative': 'autre.jpg'}, {'path': lost_twin, 'gallery': 'x', 'relative': 'autre.jpg'}]
        again.data['assigned'][lost_twin] = ['b']
        again.data['assigned'][str(moved/'autre.jpg')] = ['a']
        again.save()
        window.ask_folder = lambda title: str(disk)
        window.search_links()
        window.pool.waitForDone()
        wait(100)
        found = next(session for session in window.custom.store.sessions() if session.name == 'Recherche')
        assert all(Path(photo['path']).is_file() for photo in found.photos)
        assert all(Path(photo['path']).parent == moved for photo in found.photos), 'the folder holding most of them wins over a stray copy'
        assert len(found.photos) == 7, 'the two copies of a photo became one'
        assert set(found.data['assigned'][str(moved/'autre.jpg')]) == {'a', 'b'}, 'buckets merged'
        assert found.data['assigned'][str(moved/Path(paths[1]).name)] == ['a']
        assert found.data['sources'][0]['key'] == str(moved)
        assert [e for e in window.recents.entries() if triphoto.norm_path(e['path']) == triphoto.norm_path(moved)]
        assert answers[-1].startswith('7 photos retrouvées sur 7.') and 'introuvable' not in answers[-1], answers
        # Nothing lost: nothing to do, and it says so.
        window.search_links()
        window.pool.waitForDone()
        wait(100)
        assert answers[-1].startswith('Rien de perdu')
        window.close()
    finally:
        triphoto_qt.inform = real_inform
    clear_settings()
    # The walk stops once everything is found: what lies deeper is never read.
    deep = base/'arret'
    make_project(deep/'Temp', 6)
    make_project(deep/'a'/'b'/'c'/'Temp', 6)
    lost = [str(base/'perdu'/'Temp'/f'photo_{index:03d}.jpg') for index in range(6)]
    assert all(Path(new).parent == deep/'Temp' for new in triphoto_prefs.search_links(set(lost), {}, deep).values())
    # A folder named Output says nothing by itself; the one above decides.
    make_project(deep/'Un'/'Output', 1)
    triphoto.PhotoSession(deep/'Un'/'Output').save()
    assert triphoto_prefs.search_links(set(), {str(base/'perdu'/'Deux'/'Output'): ''}, deep) == {}
    assert triphoto_prefs.search_links(set(), {str(base/'perdu'/'Un'/'Output'): ''}, deep) == {str(base/'perdu'/'Un'/'Output'): str(deep/'Un'/'Output')}


def test_selection_review_and_health(base):
    clear_settings()
    root = make_project(base/'sante', 6)
    answers = []
    real_ask, real_custom_ask = triphoto_qt.ask, triphoto_custom.ask
    def fake_ask(parent, title, text, buttons):
        answers.append(title)
        return reply.pop(0) if reply else buttons[0][0]
    reply = []
    triphoto_qt.ask = triphoto_custom.ask = fake_ask
    try:
        window = open_window(root)
        window.resize(1400, 900)
        window.show()
        paths = [str(p.path) for p in window.session.photos]
        window.close_project()
        custom = window.custom
        sorting = custom.store.create('Sante')
        sorting.set_photos([{'path': path, 'gallery': 'sante', 'relative': Path(path).name} for path in paths])
        sorting.buckets.extend([{'id': 'a', 'title': 'AG', 'subtitle': '', 'color': '#ff3b30'}, {'id': 'b', 'title': 'WPJA', 'subtitle': '', 'color': '#0a84ff'}])
        for path in paths[:3]:
            sorting.add(path, 'a')
        sorting.add(paths[3], 'b')
        sorting.save()
        window.open_custom(sorting)
        # One click reviews a bucket, from every view, and says which one is shown.
        rule = window.bucket_rule
        assert set(rule.buttons) == {'a', 'b'} and rule.buttons['a'][''].isChecked()
        assert rule.counts['a'].text() == '3'
        rule.buttons['a']['include'].click()
        assert custom.position()[1] == 3 and rule.buttons['a']['include'].isChecked()
        window.set_review(False)
        assert len(window.photos) == 3 and window.bucket_strip.isVisible()
        # In Classique too, a thumbnail goes to another bucket, and is seen no more in this one.
        window.show()
        window.list.doItemsLayout()
        wait(50)
        viewport = window.list.viewport()
        start = window.list.visualItemRect(window.list.item(0)).center()
        moved = window.photos[0]
        strip = window.bucket_strip
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        target = dict(strip.rects())['b']
        class Drag:
            def __init__(self, point):
                self.point = point
            def position(self):
                return QPointF(viewport.mapFromGlobal(self.point))
            def globalPosition(self):
                return QPointF(self.point)
            def button(self):
                return Qt.MouseButton.LeftButton
            def buttons(self):
                return Qt.MouseButton.LeftButton
        first = viewport.mapToGlobal(start+QPoint(30, 30))
        window.list.mouseMoveEvent(Drag(first))
        assert strip.dragging and window.bucket_ghost.isVisible()
        over = strip.mapToGlobal(QPointF(target.left()+10, target.center().y()).toPoint())
        window.list.mouseMoveEvent(Drag(over))
        assert strip.hovered == ('b', 'move')
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, viewport.mapFromGlobal(over))
        assert sorting.buckets_of(moved.relative_path) == ['b'] and len(window.photos) == 2 and not strip.dragging
        window.undo()
        assert sorting.buckets_of(moved.relative_path) == ['a'] and len(window.photos) == 3
        rule.none_button.click()
        assert {p.relative_path for p in window.photos} == set(paths[4:])
        window.clear_filters()
        assert len(window.photos) == 6
        # The check finds what is empty, cut short or gone.
        window.close_project()
        window.pool.waitForDone()
        window.review.analysis_pool.waitForDone()
        Path(paths[1]).write_bytes(b'')
        data = Path(paths[2]).read_bytes()
        Path(paths[2]).write_bytes(data[:len(data)//4])
        Path(paths[3]).unlink()
        assert triphoto_custom.photo_problem(paths[0]) is None
        assert triphoto_custom.photo_problem(paths[1]) == 'fichier vide'
        assert triphoto_custom.photo_problem(paths[2]) == 'illisible'
        assert triphoto_custom.photo_problem(paths[3]) == 'introuvable'
        window.open_custom(custom.store.sessions()[0])
        sorting = window.selection
        assert paths[3] in window.session.absent
        # An export goes on past a file it cannot copy, and leaves nothing half done.
        out = base/'sante export'
        out.mkdir()
        custom.ask_export_folder = lambda: str(out)
        reply[:] = ['go', 'close', 'keep']
        custom.export()
        settle(window, lambda: window.export_job is None, 15000)
        wait(50)
        assert answers[-3:] == ['Originaux introuvables', 'Export terminé', 'Photos non copiées'], answers
        assert {p.name for p in (out/'Sante'/'AG').iterdir()} >= {Path(paths[0]).name} and not (out/'Sante'/'AG'/Path(paths[1]).name).exists()
        # From the check, the bad ones leave the selection and its buckets for good; the files stay.
        reply[:] = ['remove']
        custom.check_photos()
        settle(window, lambda: custom.checking is None, 15000)
        wait(50)
        assert answers[-1] == 'Photos à problème'
        assert {photo['path'] for photo in sorting.photos} == {paths[0], paths[4], paths[5]}
        assert sorting.count('b') == 0 and sorting.count('a') == 1
        assert Path(paths[1]).exists() and Path(paths[2]).exists()
        # They do not come back with a new choice of galleries.
        window.custom.edit_selection()
        setup = custom.setup
        settle(window, lambda: not setup.scanning(), 10000)
        for row in setup.rows.values():
            row.check.setChecked(row.title == 'sante')
        assert paths[1] not in {photo['path'] for photo in setup.chosen()}
        setup.go.click()
        # All sound: no more error at export.
        count = len(answers)
        reply[:] = []
        custom.check_photos()
        settle(window, lambda: custom.checking is None, 15000)
        assert len(answers) == count
        # A damaged file falls back on its previous state; what leads nowhere is tidied away.
        custom.save()
        custom.save()
        path = sorting.path
        window.close_project()
        path.write_text('{"name": "Sante", "photos"', encoding='utf-8')
        again = triphoto_custom.Sorting.load(path)
        assert again.name == 'Sante' and len(again.photos) == 3
        again.data['assigned'] = {paths[0]: ['a', 'disparu'], 'ailleurs.jpg': ['a'], paths[4]: 'x'}
        again.save()
        tidied = triphoto_custom.Sorting.load(path)
        assert tidied.data['assigned'] == {paths[0]: ['a']}
        window.close()
    finally:
        triphoto_qt.ask, triphoto_custom.ask = real_ask, real_custom_ask
    clear_settings()


def test_defaults_and_viewer_room(base):
    clear_settings()
    project = make_project(base/'defaults', 4)
    window = open_window(project)
    assert window.review.views['toile'].straight and window.review.views['wall'].layout_kind == 'staggered'
    window.resize(1500, 1000)
    window.show()
    settle(window, lambda: not window.viewer.image.isNull())
    # Fitted, the photo stops above the floating bar.
    window.show_viewer_bar()
    assert window.viewer.image_rect().bottom() <= window.viewer_bar.geometry().top()+1
    window.close()


def test_user_journey(base):
    """From a folder of photos to the sorted buckets, and back: each step leads to the next without a detour."""
    clear_settings()
    project = make_project(base/'journey'/'Mariage', 12)
    place = base/'journey'/'Portfolio'
    place.mkdir()
    window = triphoto_qt.Window(project)
    window.resize(1400, 900)
    window.show()
    settle(window, lambda: window.current_photo() is not None)
    window.portfolio.set_folder(place)
    assert window.project_button.text() == 'Mariage'
    # First sort, then what the filter keeps goes further in one click.
    for photo in window.session.photos[:6]:
        photo.stars = 5
    window.set_view('wall')
    window.note_filter.buttons[5].click()
    assert len(window.photos) == 6 and window.kept_section.isVisible()
    window.kept_portfolio.click()
    assert sum(window.portfolio.contains(photo.path) for photo in window.session.photos) == 6
    window.undo()
    assert not any(window.portfolio.contains(photo.path) for photo in window.session.photos)
    window.redo()
    assert sum(window.portfolio.contains(photo.path) for photo in window.session.photos) == 6
    # Never two sorts on screen, and the grid has the date too.
    assert not window.sort.isVisible()
    window.review.wall_sort.setCurrentIndex(window.review.wall_sort.findData('date'))
    assert window.sort.currentData() == 'date_asc'
    window.review.wall_sort_button.click()
    assert window.sort.currentData() == 'date_desc' and window.review.wall_sort.currentData() == 'date'
    window.review.wall_sort.setCurrentIndex(0)
    assert window.sort.currentData() == 'name_desc'
    window.sort.setCurrentIndex(window.sort.findData('name_asc'))
    window.set_view('classic')
    assert window.sort.isVisible()
    window.set_view('wall')
    # A selection from these photos: the project is checked with its filters, and cancelling comes back here.
    window.kept_selection.click()
    setup = window.custom.setup
    assert window.choosing_photos() and not window.segments.isVisible() and not window.export_button.isVisible()
    settle(window, lambda: not setup.scanning(), 10000)
    row = next(row for row in setup.rows.values() if row.title == 'Mariage')
    assert row.check.isChecked() and row.stars.currentData() == 5 and len(setup.chosen()) == 6
    assert not setup.go.isEnabled()
    window.custom.show_after_setup(None)
    assert window.review_active and window.pages.currentIndex() == 2 and window.review.mode == 'wall' and len(window.photos) == 6
    # Back on the project: same view, same photo, same filter.
    window.review.view().pick(window.photos[2])
    wanted = window.current_photo().relative_path
    window.go_home()
    assert window.pages.currentIndex() == 0 and window.project_button.text() == 'Ouvrir'
    home = window.home
    wait()
    assert home.grid_holder.y() < home.portfolio_card.y() < home.custom_button.y()
    window.set_folder(project)
    assert window.review_active and window.review.mode == 'wall' and window.current_photo().relative_path == wanted and len(window.photos) == 6
    window.kept_selection.click()
    settle(window, lambda: not setup.scanning(), 10000)
    setup.name.setText('Concours')
    setup.go.click()
    selection = window.selection
    assert selection and len(selection.photos) == 6 and window.project_button.text() == 'Concours'
    # The export and the counter stay in their usual place, and the menu leads anywhere.
    assert window.export_button.isVisible() and window.counter.text() == '1 / 6' and not window.kept_section.isVisible()
    window.custom.session.data['buckets'].append({'id': 'a', 'title': 'Sony', 'subtitle': '', 'color': triphoto_custom.BUCKET_COLORS[0]})
    window.custom.buckets_changed()
    wait()
    window.set_view('wall')
    assert window.pages.y() < window.bucket_strip.y()
    window.fill_project_menu()
    labels = [action.text() for action in window.project_menu.actions()]
    assert 'Modifier la sélection…' in labels and 'Sélections' in labels and 'Portfolio' in labels
    window.edit_selection()
    assert window.choosing_photos() and window.pages.currentIndex() == 3
    window.custom.show_after_setup(None)
    window.go_home()
    # Many projects leave the portfolio and the selections in sight.
    for index in range(14):
        make_project(base/'journey'/f'Projet {index}', 1)
        window.recents.add(base/'journey'/f'Projet {index}', [], time.time())
    window.home.refresh()
    window.home.resize(1400, 800)
    window.home.columns = 0
    window.home.arrange()
    assert not window.home.more_button.isHidden() and sum(not card.isHidden() for card in window.home.cards.values()) < 15
    window.home.more_button.click()
    assert all(not card.isHidden() for card in window.home.cards.values())
    window.close()


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(triphoto_qt.STYLE)
    base = Path(tempfile.mkdtemp(prefix='triphoto-tests-'))
    tests = [test_legacy_session, test_nested_session_ignored, test_dominant_color, test_recents, test_shortcuts,
             test_window_rating_and_undo, test_review_modes, test_grid, test_anticipation, test_request_moves_up, test_preview_limit, test_layout_stable_during_analysis, test_toile_loading, test_tri_zoom, test_like, test_export, test_export_dialog, test_quick_export, test_export_by_its_button, test_themed_dialog, test_update, test_keys_undo_and_ribbon, test_views_and_filter_panel, test_tri_filters, test_clear_stars, test_swipe_zoom, test_review_state_remembered, test_background_color, test_broken_file_does_not_stop_preload, test_recent_project_cycle, test_portfolio, test_real_trash_answer, test_trash_refused_brings_photo_back, test_portfolio_relocate, test_instagram, test_color_orders, test_rows_gap_and_captions, test_select_all, test_ribbon_portfolio_button, test_portfolio_without_copies, test_portfolio_older_copies, test_activity_bar, test_custom_sorting, test_custom_view, test_selection_views, test_repair_links, test_buckets_in_a_project, test_same_words_everywhere, test_search_links, test_selection_review_and_health, test_defaults_and_viewer_room, test_user_journey]
    failures = 0
    try:
        for test in tests:
            try:
                test(base) if test.__code__.co_argcount else test()
                print('ok  ', test.__name__)
            except Exception:
                failures += 1
                import traceback
                print('FAIL', test.__name__)
                traceback.print_exc()
    finally:
        clear_settings()
        shutil.rmtree(base, ignore_errors=True)
    print('tous les tests sont passés' if not failures else f'{failures} échec(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
