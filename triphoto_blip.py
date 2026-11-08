"""Photos handed to Blip, which sends them to a phone; and where a phone installs Blip.

Blip, once installed on Windows, answers to `blip.exe --file <photo> --file <photo>…`:
it opens on a new sending of these files, and the phone is chosen in its window.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BLIP_STORES = [('iPhone', 'https://blip.net/download/ios'), ('Android', 'https://blip.net/download/android')]
# Windows refuses a command line longer than 32 767 characters.
COMMAND_LIMIT = 30000


def blip_executable():
    """The alias that Blip's Windows package puts on the path, or None where Blip is absent."""
    if sys.platform != 'win32':
        return None
    alias = Path(os.environ.get('LOCALAPPDATA', ''))/'Microsoft'/'WindowsApps'/'blip.exe'
    if alias.exists():
        return alias
    found = shutil.which('blip.exe')
    return Path(found) if found else None


def short_copies(paths):
    """Too many photos for one command line: links in a short folder, or copies where a link cannot be made."""
    folder = Path(tempfile.mkdtemp(prefix='choyce-blip-'))
    used, result = set(), []
    for path in paths:
        stem, suffix = os.path.splitext(path.name)
        name, number = path.name, 2
        while name.lower() in used:
            name, number = f'{stem} ({number}){suffix}', number+1
        used.add(name.lower())
        target = folder/name
        try:
            os.link(path, target)
        except OSError:
            shutil.copy2(path, target)
        result.append(target)
    return result


def blip_command(executable, paths):
    command = [str(executable)]
    for path in paths:
        command += ['--file', str(path)]
    return command


def send_with_blip(paths, executable=None, launch=subprocess.Popen):
    """Hands the photos to Blip. Returns what went wrong, or an empty text."""
    executable = executable or blip_executable()
    if executable is None:
        return "Blip n'est pas installé sur cet ordinateur."
    paths = [Path(path) for path in paths if Path(path).is_file()]
    if not paths:
        return 'Ces photos sont introuvables pour le moment.'
    command = blip_command(executable, paths)
    if len(subprocess.list2cmdline(command)) > COMMAND_LIMIT:
        command = blip_command(executable, short_copies(paths))
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        launch(command, creationflags=flags, close_fds=True)
    except OSError as error:
        return f"Blip n'a pas pu s'ouvrir : {error}"
    return ''
