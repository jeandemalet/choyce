"""Non-regression tests, run with: py -3 tests/test_triphoto.py

They work on synthetic images in a temporary folder, with separate settings, and
never touch real galleries or the user's preferences.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ['TRIPHOTO_SETTINGS'] = 'TriPhotoTests'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image
from PySide6.QtCore import QEventLoop, QPointF, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import triphoto
import triphoto_prefs
import triphoto_qt
import triphoto_review

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
    # Swipe keeps its deck through decisions, and undo returns to the card.
    review.set_mode('swipe')
    swipe = review.view()
    review.swipe_rule.setCurrentIndex(review.swipe_rule.findData('pick'))
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
    # Dragging far enough decides, a short drag springs back.
    swipe = review.view()
    swipe.resize(1200, 800)
    start = swipe.index
    swipe.press = QPointF(600, 400)
    swipe.offset = QPointF(40, 0)
    swipe.mouseReleaseEvent(_Event())
    wait(260)
    assert swipe.index == start and swipe.offset.x() == 0
    swipe.press = QPointF(600, 400)
    swipe.offset = QPointF(500, 0)
    swipe.mouseReleaseEvent(_Event())
    settle(window, lambda: swipe.index == start+1)
    # Duel: the kept photo meets the next one, to the end.
    review.set_mode('duel')
    duel = review.view()
    deck = list(duel.deck)
    window.navigate(1)
    assert duel.deck[duel.champion] is deck[1] and duel.challenger == 2
    window.navigate(-1)
    assert duel.deck[duel.champion] is deck[1] and duel.challenger == 3
    review.skip(-1)
    assert duel.challenger == 2
    while not duel.finished():
        window.navigate(-1)
    assert duel.current() is deck[1]
    # Toile: positions survive a note, shuffle moves them.
    review.set_mode('toile')
    toile = review.view()
    assert len(toile.positions) == 24
    key = toile.stack[0].relative_path
    position = list(toile.positions[key])
    window.select_photo(toile.stack[0])
    window.rate(2)
    assert toile.positions[key] == position
    review.shuffle()
    assert toile.positions[key] != position
    # Background, mirror and black and white.
    window.set_background('#ffffff')
    assert window.viewer.background == '#ffffff' and any(s.isChecked() for s in review.picker.swatches)
    review.toggle_mono()
    review.toggle_mirror()
    wait(80)
    toile.grab()
    window.set_review(False)
    assert window.pages.currentIndex() == 1
    window.close()
    clear_settings()


class _Event:
    def position(self):
        return QPointF(0, 0)


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
             test_window_rating_and_undo, test_review_modes, test_background_color, test_broken_file_does_not_stop_preload, test_recent_project_cycle]
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
