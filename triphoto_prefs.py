"""Recent projects, customizable shortcuts and the preferences dialog."""
from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QRunnable, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView, QKeySequenceEdit,
    QLabel, QLineEdit, QPushButton, QRadioButton, QTableWidget, QVBoxLayout, QWidget)
from triphoto import SESSION_DIR_NAME, SESSION_FILE_NAME, SUPPORTED_EXTENSIONS

MAX_RECENTS = 30
SCAN_SKIPPED = {'$recycle.bin', 'system volume information', 'windows', 'program files', 'program files (x86)',
                'programdata', 'appdata', 'node_modules', '.git'}
# Say nothing of a project on their own: only the folder above them does.
GENERIC_FOLDERS = {'output', 'selects', 'capture', 'export', 'raw', 'jpeg', 'jpg'}
# From this many photos, a folder holding them all is the lost one whatever its name.
SURE_FOLDER = 5

# (identifier, label, default keys). Two keys at most per action.
ACTIONS = [
    ('prev', 'Photo précédente', ['C', 'Left']),
    ('next', 'Photo suivante', ['V', 'Right']),
    ('rate0', 'Zéro étoile', ['Shift+C']),
    ('rate1', '1 étoile', ['1']),
    ('rate2', '2 étoiles', ['2']),
    ('rate3', '3 étoiles', ['3']),
    ('rate4', '4 étoiles', ['4']),
    ('rate5', '5 étoiles', ['5', 'Shift+V']),
    ('flag', 'Drapeau : ajouter ou retirer', ['F']),
    ('like', "J'aime : ajouter ou retirer", ['L']),
    ('portfolio', 'Portfolio : ajouter ou retirer', ['K']),
    ('publish', 'À publier : ajouter ou retirer', ['I']),
    ('select', 'Retenir', ['P']),
    ('reject', 'Rejeter', ['X']),
    ('neutral', 'Neutre', ['U']),
    ('select_all', 'Sélectionner toutes les photos du filtre', ['Ctrl+A']),
    ('deselect', 'Désélectionner', ['Ctrl+D']),
    ('undo', 'Annuler la dernière action', ['Ctrl+Z']),
    ('redo', 'Rétablir ce qui vient d’être annulé', ['Ctrl+Y']),
    ('fit', 'Ajuster la photo', ['0']),
    ('zoom_in', 'Zoom avant', ['+']),
    ('zoom_out', 'Zoom arrière', ['-']),
    ('background', 'Changer la couleur du fond', ['B']),
    ('review', 'Basculer entre Classique et la dernière vue', ['G']),
    ('shuffle', 'Vues : mélanger', ['R']),
    ('mono', 'Vues : noir et blanc', ['N']),
    ('mode_classic', 'Vue Classique', ['Ctrl+0']),
    ('mode_wall', 'Vue Grille', ['Ctrl+1']),
    ('mode_toile', 'Vue Pêle-mêle', ['Ctrl+2']),
    ('mode_color', 'Vue Grille, triée par couleur', ['Ctrl+3']),
    ('mode_swipe', 'Vue Tindr', ['Ctrl+4']),
    ('mode_instagram', 'Vue Instagram', ['Ctrl+5']),
    ('custom_home', 'Accueil', ['Ctrl+6']),
    ('refresh', 'Actualiser le projet', ['F5']),
    ('open', 'Ajouter des photos', ['Ctrl+O']),
    ('close', 'Fermer le projet', ['Ctrl+W']),
    ('preferences', 'Préférences', ['Ctrl+,']),
]
# Not listed in the dialog: Shift held after rating must still navigate.
# The name of each mark, the same in every tooltip.
MARK_TIPS = {'portfolio': 'Portfolio : ajouter ou retirer', 'publish': 'À publier : ajouter ou retirer', 'like': "J'aime : ajouter ou retirer",
             'flag': 'Drapeau : ajouter ou retirer', 'select': 'Retenir', 'reject': 'Rejeter'}
# Each marking system only shows its own commands.
MARKER_ACTIONS = {'flag': ('flag',), 'pick': ('select', 'reject', 'neutral')}
ALIASES = {'prev': ['Shift+Left'], 'next': ['Shift+Right'], 'redo': ['Ctrl+Shift+Z']}


class ThemedDialog(QDialog):
    """A message in the design of the program, rather than the system's."""

    def __init__(self, parent, title, text, buttons):
        super().__init__(parent)
        self.choice = None
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame(objectName='dialog')
        outer.addWidget(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel(title, objectName='dialogTitle'))
        # A long path may only wrap where it is allowed to.
        message = QLabel(text.replace('\\', '\\\u200b'))
        message.setWordWrap(True)
        message.setFixedWidth(440)
        layout.addWidget(message)
        layout.addSpacing(6)
        row = QHBoxLayout()
        row.addStretch(1)
        for key, label, primary in buttons:
            button = QPushButton(label, objectName='primary' if primary else 'ghost')
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, value=key: self.choose(value))
            row.addWidget(button)
        layout.addLayout(row)

    def choose(self, value):
        self.choice = value
        self.accept()


def ask(parent, title, text, buttons):
    """The key of the button chosen, or None when closed with Escape."""
    dialog = ThemedDialog(parent, title, text, buttons)
    dialog.exec()
    return dialog.choice


def inform(parent, title, text):
    ask(parent, title, text, [('close', 'Fermer', True)])


def shortcut_map(settings):
    """Saved keys over defaults, so that new actions appear after an update."""
    try:
        saved = json.loads(settings.value('shortcuts', '{}'))
    except (TypeError, ValueError):
        saved = {}
    return {name: saved[name] if isinstance(saved.get(name), list) else list(keys) for name, _, keys in ACTIONS}


def save_shortcut_map(settings, mapping):
    settings.setValue('shortcuts', json.dumps(mapping, ensure_ascii=False))


def conflicts(mapping):
    """Keys assigned to more than one action, as {key: [labels]}."""
    labels = {name: label for name, label, _ in ACTIONS}
    used = {}
    for name, keys in mapping.items():
        for key in keys:
            used.setdefault(QKeySequence(key).toString(), []).append(labels[name])
    return {key: names for key, names in used.items() if len(names) > 1}


def path_key(path):
    return os.path.normcase(os.path.abspath(str(path)))


class Recents:
    """Projects opened on this computer. Entries survive an unplugged disk."""

    def __init__(self, settings):
        self.settings = settings

    def entries(self):
        try:
            data = json.loads(self.settings.value('recents', '[]'))
        except (TypeError, ValueError):
            return []
        return [entry for entry in data if isinstance(entry, dict) and entry.get('path')]

    def _store(self, entries):
        entries.sort(key=lambda entry: entry.get('opened', 0), reverse=True)
        self.settings.setValue('recents', json.dumps(entries[:MAX_RECENTS], ensure_ascii=False))

    def add(self, path, photos=None, opened=None):
        """Insert or refresh a project. Photos give the card's figures and cover."""
        entries = self.entries()
        known = next((e for e in entries if path_key(e['path']) == path_key(path)), None)
        entry = known or {'path': str(path), 'opened': 0}
        if known is None:
            entries.append(entry)
        if opened is not None:
            entry['opened'] = opened
        if photos is not None:
            entry.update(summary(photos))
        self._store(entries)

    def merge(self, found):
        """Add discovered projects without touching those already known."""
        entries = self.entries()
        known = {path_key(entry['path']) for entry in entries}
        new = [entry for entry in found if path_key(entry['path']) not in known]
        self._store(entries+new)
        return len(new)

    def remove(self, path):
        self._store([e for e in self.entries() if path_key(e['path']) != path_key(path)])

    def relocate(self, old, new):
        """The same project at its new place, keeping its figures and its rank."""
        entries = [e for e in self.entries() if path_key(e['path']) != path_key(new)]
        for entry in entries:
            if path_key(entry['path']) == path_key(old):
                entry['path'] = str(new)
        self._store(entries)


def moved_path(path, old, new):
    """Where a path below a folder that moved is now, or None if it is not below it."""
    path, old = os.path.abspath(str(path)), os.path.abspath(str(old))
    if os.path.normcase(path) == os.path.normcase(old):
        return str(new)
    if os.path.normcase(path).startswith(os.path.normcase(old)+os.sep):
        return str(Path(new)/path[len(old)+1:])
    return None


def photos_below(folder):
    """The photos of a folder as {lowercase name: [paths]}, without what Vision Board keeps there."""
    found = {}
    for current, directories, files in os.walk(folder):
        directories[:] = [d for d in directories if d != SESSION_DIR_NAME and d.lower() not in SCAN_SKIPPED and not d.startswith('.')]
        for name in files:
            if os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS:
                found.setdefault(name.lower(), []).append(os.path.join(current, name))
    return found


def locate_project(old, chosen):
    """What a folder chosen by hand holds of a lost project, as (folder, how).

    The chosen folder may be the project itself, hold it further down, or only hold photos
    without the notes of a project. Several projects inside need a closer choice.
    """
    chosen = Path(chosen)
    if (chosen/SESSION_DIR_NAME/SESSION_FILE_NAME).is_file():
        return chosen, 'project'
    found = find_sessions(chosen)
    same_name = [entry for entry in found if Path(entry['path']).name.lower() == Path(old).name.lower()]
    candidates = same_name or found
    if len(candidates) == 1:
        return Path(candidates[0]['path']), 'inside'
    if candidates:
        return None, 'several'
    return (chosen, 'photos') if photos_below(chosen) else (None, 'empty')


def closeness(old, candidate):
    """How many folders two paths share, counted from the end."""
    same = 0
    for a, b in zip(reversed(Path(old).parts), reversed(Path(candidate).parts)):
        if a.lower() != b.lower():
            break
        same += 1
    return same


def locate_photos(missing, chosen):
    """Each lost path found by its name below a folder, as {old: new}. Among several, the one in the most similar folders."""
    below = photos_below(chosen)
    return {old: max(below[Path(old).name.lower()], key=lambda candidate: closeness(Path(old).parent, Path(candidate).parent))
            for old in missing if Path(old).name.lower() in below}


def known_links(portfolio, sessions, recents):
    """Every place Vision Board refers to, as (photos, {project folder: name of its cover}). Touches no disk."""
    photos, folders = set(), {}
    for item in portfolio.items.values():
        if item.get('linked') and item.get('source'):
            photos.add(item['source'])
    for session in sessions:
        photos.update(photo['path'] for photo in session.photos)
        photos.update(session.data.get('removed', []))
        for source in session.data.get('sources', []):
            folders.setdefault(source['key'], '')
    for entry in recents.entries():
        folders[entry['path']] = Path(entry.get('cover') or '').name
    if portfolio.folder is not None:
        folders.setdefault(str(portfolio.folder), '')
    return photos, folders


def search_links(photos, folders, root, cancelled=lambda: False):
    """Where lost photos and folders are now below a disk, as {old: new}.

    One pass over the folders, shallowest first, keeping only the names looked for. It ends as soon as
    each lost folder of photos has been found whole and no project remains to find, which spares the rest of the disk.
    """
    wanted = {}
    for path in photos:
        wanted.setdefault(str(Path(path).parent), set()).add(Path(path).name.lower())
    names = set().union(*wanted.values()) if wanted else set()
    names.update(cover.lower() for cover in folders.values() if cover)
    hits, projects, pending = {}, [], set(wanted)
    queue = deque([os.path.normpath(str(root))])
    while queue and not cancelled() and (pending or folders):
        current = queue.popleft()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    name = entry.name.lower()
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name == SESSION_DIR_NAME:
                            projects.append(current)
                        elif name not in SCAN_SKIPPED and not name.startswith('.'):
                            queue.append(entry.path)
                    elif name in names:
                        hits.setdefault(current, {})[name] = entry.name
        except OSError:
            continue
        if current in hits:
            pending = {folder for folder in pending if not (wanted[folder] <= hits[current].keys()
                       and (len(wanted[folder]) >= SURE_FOLDER or Path(folder).name.lower() == Path(current).name.lower()))}
    moves, best = {}, {}
    # The folder holding the most photos of a lost one takes them all, so that a stray copy elsewhere does not scatter them.
    for folder, lost in wanted.items():
        held = [place for place, found in hits.items() if lost & found.keys()]
        if held:
            best[folder] = max(held, key=lambda place: (len(lost & hits[place].keys()), closeness(folder, place)))
    for path in photos:
        folder, name = str(Path(path).parent), Path(path).name.lower()
        place = best.get(folder)
        if place and name not in hits[place]:
            place = max((p for p, found in hits.items() if name in found), key=lambda p: closeness(folder, p), default=None)
        if place:
            moves[path] = os.path.join(place, hits[place][name])
    for folder, cover in folders.items():
        needed = 2 if Path(folder).name.lower() in GENERIC_FOLDERS else 1
        ranked = sorted(((cover.lower() in hits.get(place, {}), closeness(folder, place), place) for place in projects
                         if closeness(folder, place) >= needed or cover.lower() in hits.get(place, {})), reverse=True)
        if ranked and (len(ranked) == 1 or ranked[0][:2] != ranked[1][:2]):
            moves[folder] = ranked[0][2]
        elif folder in best:
            moves[folder] = best[folder]
    return moves


def summary(photos):
    """Figures of a project; accepts Photo objects or session.json items."""
    def field(photo, name, default):
        return photo.get(name, default) if isinstance(photo, dict) else getattr(photo, name, default)
    ordered = sorted(photos, key=lambda p: -field(p, 'stars', 0))
    return {'count': len(photos), 'five': sum(field(p, 'stars', 0) == 5 for p in photos),
            'cover': field(ordered[0], 'relative_path', '') if ordered else ''}


def find_sessions(folder, max_depth=6):
    """Earlier projects below a folder, from their session files."""
    found = []
    base = len(Path(folder).parts)
    for current, directories, _ in os.walk(folder):
        if SESSION_DIR_NAME in directories:
            session = Path(current)/SESSION_DIR_NAME/SESSION_FILE_NAME
            try:
                # The folder on disk wins over the stored root: it may have moved.
                data = json.loads(session.read_text(encoding='utf-8'))
                found.append({'path': current, 'opened': session.stat().st_mtime, **summary(data.get('photos', []))})
            except (OSError, ValueError, AttributeError, TypeError):
                pass
        depth = len(Path(current).parts)-base
        directories[:] = [] if depth >= max_depth else [d for d in directories
            if d != SESSION_DIR_NAME and d.lower() not in SCAN_SKIPPED and not d.startswith('.')]
    return found


class LinkSearchSignals(QObject):
    done = Signal(dict, int, int, list)


class LinkSearchJob(QRunnable):
    def __init__(self, photos, folders, root):
        super().__init__()
        self.photos, self.folders, self.root = photos, folders, root
        self.cancelled = False
        self.signals = LinkSearchSignals()

    def run(self):
        photos = {path for path in self.photos if not os.path.isfile(path)}
        folders = {path: cover for path, cover in self.folders.items() if not os.path.isdir(path)}
        moves = search_links(photos, folders, self.root, lambda: self.cancelled) if photos or folders else {}
        projects = find_sessions(self.root) if not self.cancelled else []
        self.signals.done.emit(moves, len(photos), len(folders), projects)


def age_label(timestamp):
    if not timestamp:
        return ''
    days = int((time.time()-timestamp)//86400)
    if days <= 0:
        return "aujourd'hui"
    if days == 1:
        return 'hier'
    if days < 30:
        return f'il y a {days} jours'
    return time.strftime('%d/%m/%Y', time.localtime(timestamp))


class PreferencesDialog(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle('Préférences')
        self.resize(560, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel('MARQUAGE', objectName='section'))
        self.marker = QButtonGroup(self)
        current = settings.value('marker', 'flag')
        for key, text in [('flag', 'Étoiles et drapeau'),
                          ('pick', 'Étoiles et Retenue, Rejetée, Neutre, comme Lightroom ou Capture One')]:
            radio = QRadioButton(text)
            radio.setProperty('key', key)
            radio.setChecked(current == key)
            self.marker.addButton(radio)
            layout.addWidget(radio)
        note = QLabel('Un drapeau et une photo retenue sont la même information : changer de système ne perd rien.', objectName='faint')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addSpacing(8)
        layout.addWidget(QLabel('PORTFOLIO', objectName='section'))
        place = QHBoxLayout()
        self.portfolio_label = QLabel()
        self.portfolio_label.setWordWrap(True)
        place.addWidget(self.portfolio_label, 1)
        choose = QPushButton("Choisir l'emplacement…", objectName='ghost')
        choose.clicked.connect(self.choose_portfolio)
        place.addWidget(choose)
        layout.addLayout(place)
        self.show_portfolio_place()
        layout.addSpacing(8)
        layout.addWidget(QLabel('RACCOURCIS', objectName='section'))
        mapping = shortcut_map(settings)
        self.table = QTableWidget(len(ACTIONS), 3)
        self.table.setHorizontalHeaderLabels(['Commande', 'Touche', 'Autre touche'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.edits = {}
        for row, (name, label, _) in enumerate(ACTIONS):
            self.table.setCellWidget(row, 0, self._label(label))
            self.edits[name] = []
            for column in (0, 1):
                keys = mapping[name]
                edit = QKeySequenceEdit(QKeySequence(keys[column]) if column < len(keys) else QKeySequence())
                edit.setMaximumSequenceLength(1)
                edit.setClearButtonEnabled(True)
                edit.findChild(QLineEdit).setPlaceholderText('Appuyer…')
                self.table.setCellWidget(row, column+1, edit)
                self.edits[name].append(edit)
            self.table.setRowHeight(row, 36)
        self.marker.buttonToggled.connect(lambda *_: self.show_marker_actions())
        self.show_marker_actions()
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 130)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        reset = QPushButton('Rétablir les raccourcis', objectName='ghost')
        reset.clicked.connect(self.reset)
        buttons.addWidget(reset)
        buttons.addStretch()
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText('Annuler')
        buttons.addWidget(box)
        layout.addLayout(buttons)

    def show_marker_actions(self):
        current = self.marker.checkedButton().property('key')
        for row, (name, _, _) in enumerate(ACTIONS):
            self.table.setRowHidden(row, any(name in names and key != current for key, names in MARKER_ACTIONS.items()))

    def show_portfolio_place(self):
        folder = self.parent().portfolio.folder
        self.portfolio_label.setText(str(folder).replace('\\', '\\\u200b') if folder else 'Aucun dossier choisi pour le moment.')

    def choose_portfolio(self, *args):
        self.parent().choose_portfolio_folder()
        self.show_portfolio_place()

    def _label(self, text):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(6, 0, 6, 0)
        row.addWidget(QLabel(text))
        return holder

    def reset(self):
        for name, _, keys in ACTIONS:
            for column, edit in enumerate(self.edits[name]):
                edit.setKeySequence(QKeySequence(keys[column]) if column < len(keys) else QKeySequence())

    def mapping(self):
        return {name: [edit.keySequence().toString() for edit in edits if not edit.keySequence().isEmpty()]
                for name, edits in self.edits.items()}

    def accept(self):
        clashes = conflicts(self.mapping())
        if clashes:
            text = '\n'.join(f"{key} : {' et '.join(names)}" for key, names in clashes.items())
            inform(self, 'Raccourcis en conflit', 'Une même touche ne peut servir à deux commandes.\n\n'+text)
            return
        save_shortcut_map(self.settings, self.mapping())
        self.settings.setValue('marker', self.marker.checkedButton().property('key'))
        super().accept()
