"""New versions, published as GitHub releases: detection, download and installation."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

REPOSITORY = 'jeandemalet/choyce'
LATEST_URL = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
# Each release carries two archives: the program installed before the name Choyce
# keeps receiving its own, with its own executable, so that its shortcuts still work.
PACKAGES = {'Choyce.exe': ('Choyce-Windows.zip', 'Choyce'), 'VisionBoard.exe': ('VisionBoard-Windows.zip', 'VisionBoard')}
EXECUTABLE = 'Choyce.exe'


def running_executable():
    """The name of the packaged program that runs, Choyce.exe or the former VisionBoard.exe."""
    name = Path(sys.executable).name
    return name if name in PACKAGES else EXECUTABLE


def parse_version(text):
    """(1, 2, 0) for "v1.2.0"; an empty tuple for anything else, which is never newer."""
    parts = str(text).strip().lstrip('v').split('.')
    return tuple(int(part) for part in parts) if all(part.isdigit() for part in parts) else ()


def is_newer(latest, current):
    return parse_version(latest) > parse_version(current)


def can_install():
    """Only the packaged program replaces itself: a source folder belongs to git."""
    return bool(getattr(sys, 'frozen', False)) and sys.platform == 'win32' and Path(sys.executable).name in PACKAGES


class UpdateSignals(QObject):
    found = Signal(dict)
    progress = Signal(int, int)
    ready = Signal(str, str)


class UpdateCheck(QRunnable):
    """Asks GitHub for the latest release. Offline or refused, it simply says nothing."""

    def __init__(self, current, executable=None):
        super().__init__()
        self.current = current
        self.executable = executable or running_executable()
        self.signals = UpdateSignals()

    def run(self):
        try:
            request = urllib.request.Request(LATEST_URL, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Choyce'})
            with urllib.request.urlopen(request, timeout=10) as response:
                release = json.load(response)
            tag = release['tag_name']
            if is_newer(tag, self.current):
                archive = PACKAGES[self.executable][0]
                asset = next((a for a in release.get('assets', []) if a.get('name') == archive), None)
                self.signals.found.emit({'version': tag.lstrip('v'), 'page': release.get('html_url', ''),
                                         'url': asset['browser_download_url'] if asset else '',
                                         'digest': (asset or {}).get('digest') or ''})
        except Exception:
            pass


class UpdateDownload(QRunnable):
    """Downloads and unpacks a release beside nothing the program uses until it restarts."""

    def __init__(self, url, digest='', executable=None):
        super().__init__()
        self.url, self.digest = url, digest
        self.executable = executable or running_executable()
        self.signals = UpdateSignals()
        self.cancelled = threading.Event()

    def unpack(self, folder):
        name, folder_name = PACKAGES[self.executable]
        archive = folder/name
        total = done = 0
        hasher = hashlib.sha256()
        request = urllib.request.Request(self.url, headers={'User-Agent': 'Choyce'})
        with urllib.request.urlopen(request, timeout=30) as response, open(archive, 'wb') as output:
            total = int(response.headers.get('Content-Length') or 0)
            while chunk := response.read(1024*1024):
                if self.cancelled.is_set():
                    return None
                output.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                self.signals.progress.emit(done, total)
        if self.digest.startswith('sha256:') and self.digest[7:] != hasher.hexdigest():
            raise ValueError('Le fichier téléchargé ne correspond pas à la version publiée.')
        with zipfile.ZipFile(archive) as content:
            content.extractall(folder/'contenu')
        source = folder/'contenu'/folder_name
        if not (source/self.executable).is_file() or not (source/'_internal').is_dir():
            raise ValueError('La version téléchargée est incomplète.')
        archive.unlink()
        return source

    def run(self):
        try:
            source = self.unpack(Path(tempfile.mkdtemp(prefix='choyce-maj-')))
            if source is not None:
                self.signals.ready.emit(str(source), '')
        except Exception as error:
            self.signals.ready.emit('', str(error))


def write_installer(source, target, pid, executable=None):
    """A script that outlives the program: it waits for its end, swaps the files and restarts it.

    Only _internal is mirrored, and the executable replaced: the program may have been
    unpacked beside files that are none of its business.
    """
    source, target = Path(source), Path(target)
    executable = executable or running_executable()
    work = source.parent.parent
    script = work/'installer.cmd'
    lines = ['@echo off', 'chcp 65001 >nul', ':attente',
             f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto attente)',
             f'robocopy "{source/"_internal"}" "{target/"_internal"}" /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS >nul',
             f'copy /Y "{source/executable}" "{target/executable}" >nul',
             f'start "" "{target/executable}"',
             'cd /d "%TEMP%"',
             f'rmdir /s /q "{work}"']
    script.write_text('\r\n'.join(lines)+'\r\n', encoding='utf-8')
    return script


def launch_installer(script):
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == 'nt' else 0
    subprocess.Popen(['cmd', '/c', str(script)], creationflags=flags, close_fds=True)
