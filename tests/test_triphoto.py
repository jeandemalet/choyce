"""Non-regression tests, run with: py -3 tests/test_triphoto.py

They work on synthetic images in a temporary folder, with separate settings, and
never touch real galleries or the user's preferences.
"""
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
from PySide6.QtCore import QEventLoop, QPointF, QRectF, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import triphoto
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
    xs = [p[0] for p in toile.positions.values()]
    assert round((max(xs)-min(xs))/toile.CELL_WIDTH) == 3
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
    assert color.order == 'hue' and review.order_button.text() == 'Rouge → violet'
    review.order_combo.setCurrentIndex(1)
    assert color.order == 'light' and review.order_button.text() == 'Clair → sombre'
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
        folder = root/'TriPhoto_Session'/triphoto.EXPORT_DIR_NAME
        window.export()
        assert calls == ['Export']
        calls.clear()
        for photo in window.photos[:2]:
            photo.stars = 5
        # A first export asks nothing but where it ended, and shows its progress meanwhile.
        answers[:] = ['close']
        window.export()
        assert window.export_panel.isVisible() and not window.export_button.isEnabled()
        settle(window, lambda: window.export_job is None)
        assert calls == ['Export terminé'] and len(list(folder.iterdir())) == 2 and not window.export_panel.isVisible()
        assert window.export_button.isEnabled()
        calls.clear()
        # A new subfolder keeps the previous export.
        window.photos[2].stars = 5
        answers[:] = ['new', 'close']
        window.export()
        settle(window, lambda: window.export_job is None)
        dated = [child for child in folder.iterdir() if child.is_dir()]
        assert calls == ['Un export existe déjà', 'Export terminé']
        assert len(dated) == 1 and len(list(dated[0].iterdir())) == 3 and len(list(folder.glob('*.jpg'))) == 2
        # Replacing refreshes the main export and leaves dated ones alone.
        answers[:] = ['replace', 'close']
        window.photos[0].stars = 0
        window.export()
        settle(window, lambda: window.export_job is None)
        assert len(list(folder.glob('*.jpg'))) == 2 and not (folder/window.photos[0].relative_path).exists()
        assert len(list(dated[0].iterdir())) == 3
        # Declining the choice exports nothing.
        answers[:] = [None]
        window.export()
        assert window.export_job is None
        # Cancelling leaves the previous export and no work folder.
        before = sorted(p.name for p in folder.glob('*.jpg'))
        answers[:] = ['replace']
        window.export()
        window.cancel_export()
        settle(window, lambda: window.export_job is None)
        assert sorted(p.name for p in folder.glob('*.jpg')) == before
        assert not (root/'TriPhoto_Session'/triphoto_qt.EXPORT_WORK_DIR_NAME).exists()
    finally:
        triphoto_qt.ask, triphoto_qt.inform = real_ask, real_inform
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


def test_views_and_filter_panel(base):
    clear_settings()
    root = make_project(base/'views', 6)
    window = open_window(root)
    review = window.review
    # One row of views: Classique is the page with the ribbon, the others follow.
    assert list(window.view_buttons) == ['classic', 'wall', 'toile', 'color', 'swipe']
    assert window.view_buttons['classic'].isChecked() and window.pages.currentIndex() == 1
    window.view_buttons['toile'].click()
    assert window.review_active and review.mode == 'toile' and window.pages.currentIndex() == 2
    assert window.view_buttons['toile'].isChecked()
    window.view_buttons['classic'].click()
    assert not window.review_active and window.view_buttons['classic'].isChecked()
    window.set_view('color')
    assert window.view_buttons['color'].isChecked()
    review.set_mode('wall')
    assert window.view_buttons['wall'].isChecked()
    # The same filter everywhere.
    assert review.note_filter is window.note_filter and review.marker_filter is window.marker_filter
    # The panel unfolds on hover, under the header, and folds back once the mouse has left.
    assert not window.filter_panel.isVisible()
    window.filter_chip.entered.emit()
    assert window.filter_panel.isVisible() and window.filter_panel.y() >= window.header.height()
    assert review.tools.isVisible()
    # Over the panel, its buttons, its list and the gap under the button, it counts as hovered.
    panel = window.filter_panel
    button = window.note_filter.buttons[5]
    chip = window.filter_chip
    assert window.over_filter(button.mapTo(window, button.rect().center()))
    assert window.over_filter(window.marker_filter.mapTo(window, window.marker_filter.rect().center()))
    assert window.over_filter(review.zoom_label.mapTo(window, review.zoom_label.rect().center()))
    assert window.over_filter(panel.geometry().topLeft()+QPointF(panel.width()/2, -4).toPoint())
    assert window.over_filter(chip.mapTo(window, chip.rect().center()))
    assert not window.over_filter(QPointF(5, window.height()-5).toPoint())
    # It stays as long as the mouse is there, however long, and goes once it has left.
    window.over_filter = lambda position=None: True
    wait(800)
    assert window.filter_panel.isVisible()
    window.over_filter = lambda position=None: False
    wait(150)
    assert window.filter_panel.isVisible(), 'a short moment outside must not close it'
    wait(500)
    assert not window.filter_panel.isVisible()
    del window.over_filter
    # In Classique, no tools of the other views.
    window.set_view('classic')
    window.show_filter_panel()
    window.filter_timer.stop()
    assert window.filter_panel.isVisible() and not review.tools.isVisible()
    window.note_filter.buttons[0].click()
    assert '0 étoile' in window.filter_chip.text()
    window.note_filter.buttons['all'].click()
    assert window.filter_chip.text() == 'Filtre  ▾'
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
    assert window.filter_chip.isVisible() and '3 étoiles' in window.filter_chip.text()
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


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(triphoto_qt.STYLE)
    base = Path(tempfile.mkdtemp(prefix='triphoto-tests-'))
    tests = [test_legacy_session, test_nested_session_ignored, test_dominant_color, test_recents, test_shortcuts,
             test_window_rating_and_undo, test_review_modes, test_grid, test_anticipation, test_request_moves_up, test_preview_limit, test_layout_stable_during_analysis, test_toile_loading, test_tri_zoom, test_like, test_export, test_themed_dialog, test_update, test_views_and_filter_panel, test_tri_filters, test_clear_stars, test_swipe_zoom, test_review_state_remembered, test_background_color, test_broken_file_does_not_stop_preload, test_recent_project_cycle]
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
