"""Recent projects, customizable shortcuts and the preferences dialog."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QObject, QRunnable, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView, QKeySequenceEdit,
    QLabel, QLineEdit, QPushButton, QRadioButton, QTableWidget, QVBoxLayout, QWidget)
from triphoto import SESSION_DIR_NAME, SESSION_FILE_NAME

MAX_RECENTS = 30
SCAN_SKIPPED = {'$recycle.bin', 'system volume information', 'windows', 'program files', 'program files (x86)',
                'programdata', 'appdata', 'node_modules', '.git'}

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
    ('flag', 'Drapeau', ['F']),
    ('like', "J'aime", ['L']),
    ('portfolio', 'Ajouter au portfolio ou retirer', ['K']),
    ('publish', 'À publier', ['I']),
    ('select', 'Retenir', ['P']),
    ('reject', 'Rejeter', ['X']),
    ('neutral', 'Neutre', ['U']),
    ('select_all', 'Sélectionner toutes les photos du filtre', ['Ctrl+A']),
    ('deselect', 'Désélectionner', ['Ctrl+D']),
    ('undo', 'Annuler la dernière note', ['Ctrl+Z']),
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
    ('mode_color', 'Vue Couleur', ['Ctrl+3']),
    ('mode_swipe', 'Vue Tindr', ['Ctrl+4']),
    ('mode_instagram', 'Vue Instagram', ['Ctrl+5']),
    ('custom_home', 'Accueil et sélections custom', ['Ctrl+6']),
    ('refresh', 'Actualiser le dossier', ['F5']),
    ('open', 'Ouvrir un projet', ['Ctrl+O']),
    ('close', 'Fermer le projet', ['Ctrl+W']),
    ('preferences', 'Préférences', ['Ctrl+,']),
]
# Not listed in the dialog: Shift held after rating must still navigate.
ALIASES = {'prev': ['Shift+Left'], 'next': ['Shift+Right']}


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


class ScanSignals(QObject):
    done = Signal(list)


class ScanJob(QRunnable):
    def __init__(self, folder):
        super().__init__()
        self.folder = folder
        self.signals = ScanSignals()

    def run(self):
        self.signals.done.emit(find_sessions(self.folder))


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
