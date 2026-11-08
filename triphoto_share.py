"""Photos sent to a phone on the same Wi-Fi, with nothing to install on it.

Choyce serves a small page on the local network while it is open. A phone joins once by
scanning a QR code, then keeps its own address: saved to its home screen, it opens the
latest photos sent. What a phone needs to come back is kept in the data folder of the
program, beside the selections, which an update of the program never touches.
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import socket
import threading
import time
import zipfile
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image

from triphoto import convert_to_srgb, write_json_atomic

PORTAL_FILE = 'partage.json'
DEFAULT_PORT = 47615
SHARES_KEPT = 20
THUMB_SIDE = 640
THUMBS_KEPT = 400
PASS_THROUGH = {'.jpg', '.jpeg', '.png', '.webp'}


def local_address():
    """The address of this computer on the local network: the one a packet to the outside would leave from."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('192.0.2.1', 80))
        return probe.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        probe.close()


def device_name(agent):
    """What a phone calls itself in its browser's signature, enough to tell two phones apart."""
    agent = agent or ''
    if 'iPhone' in agent:
        return 'iPhone'
    if 'iPad' in agent:
        return 'iPad'
    if 'Android' in agent:
        model = re.search(r'Android [\d.]+; (?:[a-z]{2}[-_][A-Za-z]{2}; )?([^;)]+?)(?: Build|\))', agent)
        return model.group(1).strip() if model and model.group(1).strip() not in ('K', 'wv') else 'Android'
    return 'Navigateur'


def unique_name(name, used):
    stem, suffix = os.path.splitext(name)
    candidate, number = name, 2
    while candidate.lower() in used:
        candidate, number = f'{stem} ({number}){suffix}', number+1
    used.add(candidate.lower())
    return candidate


class Portal:
    """The phones that joined, the photos sent, and the server that shows them."""

    def __init__(self, folder):
        self.path = Path(folder)/PORTAL_FILE
        self.lock = threading.RLock()
        self.server = None
        self.thread = None
        self.thumbs = OrderedDict()
        self.data = self.load()

    def load(self):
        data = {}
        for candidate in (self.path, Path(str(self.path)+'.bak')):
            try:
                data = json.loads(candidate.read_text(encoding='utf-8'))
                break
            except (OSError, ValueError):
                pass
        data = data if isinstance(data, dict) else {}
        data.setdefault('port', DEFAULT_PORT)
        data.setdefault('pair', secrets.token_urlsafe(9))
        data['devices'] = data.get('devices') if isinstance(data.get('devices'), dict) else {}
        data['shares'] = [share for share in data.get('shares', []) if isinstance(share, dict) and share.get('id')]
        return data

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                try:
                    self.path.with_name(self.path.name+'.bak').write_bytes(self.path.read_bytes())
                except OSError:
                    pass
            write_json_atomic(self.path, self.data)

    # What the window asks for.
    @property
    def devices(self):
        return self.data['devices']

    @property
    def running(self):
        return self.server is not None

    def address(self):
        return f"http://{local_address()}:{self.data['port']}"

    def pair_url(self):
        return f"{self.address()}/a/{self.data['pair']}"

    def add_share(self, title, paths):
        with self.lock:
            share = {'id': secrets.token_hex(4), 'title': title, 'created': time.time(), 'photos': [str(path) for path in paths]}
            self.data['shares'] = [share]+self.data['shares'][:SHARES_KEPT-1]
            self.save()
            return share

    def forget(self, key):
        with self.lock:
            self.devices.pop(key, None)
            self.save()

    def start(self):
        """On the port it had, so that the address saved on a phone still leads here; on another only if that one is taken."""
        if self.server:
            return True
        ports = [self.data['port']]+[0]
        for port in ports:
            try:
                server = Server(('0.0.0.0', port), Handler)
            except OSError:
                continue
            server.portal = self
            self.server = server
            if server.server_address[1] != self.data['port']:
                self.data['port'] = server.server_address[1]
                self.save()
            self.thread = threading.Thread(target=server.serve_forever, name='Choyce partage', daemon=True)
            self.thread.start()
            return True
        return False

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # What the server asks for.
    def join(self, agent):
        with self.lock:
            key = secrets.token_urlsafe(12)
            self.devices[key] = {'name': device_name(agent), 'added': time.time(), 'seen': time.time()}
            self.save()
            return key

    def seen(self, key):
        with self.lock:
            device = self.devices.get(key)
            if device is None:
                return False
            # Written at most every minute: a page that refreshes does not rewrite the file each time.
            if time.time()-device.get('seen', 0) > 60:
                device['seen'] = time.time()
                self.save()
            return True

    def share(self, share_id):
        with self.lock:
            return next((share for share in self.data['shares'] if share['id'] == share_id), None)

    def photo(self, share_id, index):
        share = self.share(share_id)
        if share is None or not 0 <= index < len(share['photos']):
            return None
        path = Path(share['photos'][index])
        return path if path.is_file() else None

    def thumb(self, path):
        key = (str(path), path.stat().st_mtime)
        with self.lock:
            if key in self.thumbs:
                self.thumbs.move_to_end(key)
                return self.thumbs[key]
        with Image.open(path) as image:
            image.draft('RGB', (THUMB_SIDE*2, THUMB_SIDE*2))
            image = convert_to_srgb(image)
            image.thumbnail((THUMB_SIDE, THUMB_SIDE))
            output = BytesIO()
            image.save(output, 'JPEG', quality=82)
        data = output.getvalue()
        with self.lock:
            self.thumbs[key] = data
            while len(self.thumbs) > THUMBS_KEPT:
                self.thumbs.popitem(last=False)
        return data

    def full(self, path):
        """The original when a phone can open it; a JPEG in sRGB for the others, such as TIFF."""
        if path.suffix.lower() in PASS_THROUGH:
            return path.read_bytes(), path.name
        with Image.open(path) as image:
            image = convert_to_srgb(image)
            output = BytesIO()
            image.save(output, 'JPEG', quality=92)
        return output.getvalue(), path.stem+'.jpg'


PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Choyce">
<meta name="theme-color" content="#161617">
<link rel="manifest" href="manifest.json">
<title>Choyce</title>
<style>
:root {{ color-scheme: dark; }}
body {{ margin: 0; background: #161617; color: #f2f2f2; font: 15px/1.45 -apple-system, system-ui, sans-serif; padding: 0 14px 40px; }}
header {{ padding: calc(18px + env(safe-area-inset-top)) 2px 6px; }}
h1 {{ font-size: 22px; margin: 0; letter-spacing: -.01em; }}
.hint {{ color: #9a9aa0; font-size: 13px; margin: 4px 0 0; }}
section {{ margin-top: 26px; }}
h2 {{ font-size: 16px; margin: 0; }}
.meta {{ color: #9a9aa0; font-size: 13px; margin: 2px 0 10px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
.meta a {{ color: #f2f2f2; background: #2c2c2e; border-radius: 8px; padding: 6px 11px; text-decoration: none; font-size: 13px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 4px; }}
.grid a {{ display: block; aspect-ratio: 1; background: #232325; border-radius: 4px; overflow: hidden; }}
.grid img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
.empty {{ color: #9a9aa0; margin-top: 40px; }}
</style></head><body>
<header><h1>Choyce</h1>
<p class="hint">{hint}</p></header>
{sections}
<script>
const latest = {latest};
setInterval(() => fetch('etat').then(r => r.json()).then(s => {{ if (s.latest !== latest) location.reload(); }}).catch(() => {{}}), 8000);
</script>
</body></html>"""


class Server(ThreadingHTTPServer):
    """A port of its own: under Windows, the usual reuse of an address would share it with another program."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    server_version = 'Choyce'

    def log_message(self, *args):
        pass

    @property
    def portal(self):
        return self.server.portal

    def send(self, status, body=b'', kind='text/plain; charset=utf-8', headers=()):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store' if kind.startswith(('text/html', 'application/json')) else 'private, max-age=86400')
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        parts = [part for part in url.path.split('/') if part]
        try:
            if len(parts) == 2 and parts[0] == 'a':
                return self.join(parts[1])
            if len(parts) >= 2 and parts[0] == 'p' and self.portal.seen(parts[1]):
                return self.device(parts[1], parts[2:], parse_qs(url.query))
            if len(parts) >= 2 and parts[0] == 'p':
                return self.send(403, 'Ce téléphone a été retiré de Choyce. Scannez de nouveau le QR code affiché sur l’ordinateur.'.encode())
            self.send(404, b'')
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            self.send(500, f'Erreur : {error}'.encode())

    def join(self, code):
        if code != self.portal.data['pair']:
            return self.send(403, 'Ce QR code n’est plus valable. Affichez de nouveau le partage dans Choyce.'.encode())
        key = self.portal.join(self.headers.get('User-Agent'))
        self.send(302, b'', headers=[('Location', f'/p/{key}/')])

    def device(self, key, rest, query):
        if not rest:
            return self.page()
        if rest == ['etat']:
            shares = self.portal.data['shares']
            return self.send(200, json.dumps({'latest': shares[0]['id'] if shares else ''}).encode(), 'application/json')
        if rest == ['manifest.json']:
            manifest = {'name': 'Choyce', 'short_name': 'Choyce', 'start_url': f'/p/{key}/', 'display': 'standalone',
                        'background_color': '#161617', 'theme_color': '#161617'}
            return self.send(200, json.dumps(manifest).encode(), 'application/manifest+json')
        if len(rest) == 2 and rest[0] == 'zip':
            return self.archive(rest[1])
        if len(rest) == 3 and rest[0] in ('t', 'f') and rest[2].isdigit():
            path = self.portal.photo(rest[1], int(rest[2]))
            if path is None:
                return self.send(404, 'Photo introuvable sur l’ordinateur.'.encode())
            if rest[0] == 't':
                return self.send(200, self.portal.thumb(path), 'image/jpeg')
            data, name = self.portal.full(path)
            kind = {'.png': 'image/png', '.webp': 'image/webp'}.get(Path(name).suffix.lower(), 'image/jpeg')
            disposition = 'attachment' if 'dl' in query else 'inline'
            quoted = name.encode('ascii', 'replace').decode().replace('"', '')
            return self.send(200, data, kind, [('Content-Disposition', f'{disposition}; filename="{quoted}"')])
        self.send(404, b'')

    def page(self):
        shares = self.portal.data['shares']
        sections = []
        for share in shares:
            count = len(share['photos'])
            day = time.strftime('%d/%m à %Hh%M', time.localtime(share['created']))
            tiles = ''.join(f'<a href="f/{share["id"]}/{index}"><img loading="lazy" src="t/{share["id"]}/{index}" alt=""></a>' for index in range(count))
            sections.append(f'<section><h2>{html.escape(share["title"])}</h2>'
                            f'<div class="meta"><span>{count} photo{"s" if count > 1 else ""} · le {day}</span>'
                            f'<a href="zip/{share["id"]}">Tout télécharger</a></div><div class="grid">{tiles}</div></section>')
        agent = self.headers.get('User-Agent') or ''
        apple = 'iPhone' in agent or 'iPad' in agent
        hint = ('Touchez une photo, puis appui long et « Ajouter à Photos ». Partager, puis « Sur l’écran d’accueil », garde cette page à portée de main.'
                if apple else 'Touchez une photo pour l’ouvrir en grand et l’enregistrer. Ajoutez cette page à l’écran d’accueil pour la retrouver.')
        body = PAGE.format(hint=hint, sections=''.join(sections) or '<p class="empty">Rien n’a encore été envoyé.</p>',
                           latest=json.dumps(shares[0]['id'] if shares else ''))
        self.send(200, body.encode(), 'text/html; charset=utf-8')

    def archive(self, share_id):
        """Streamed as it is written: no copy of the photos on the disk, and a phone sees it start at once."""
        share = self.portal.share(share_id)
        if share is None:
            return self.send(404, b'')
        title = re.sub(r'[\\/:*?"<>|]+', ' ', share['title']).strip() or 'Choyce'
        self.send_response(200)
        self.send_header('Content-Type', 'application/zip')
        self.send_header('Content-Disposition', f'attachment; filename="{title.encode("ascii", "replace").decode().replace(chr(34), "")}.zip"')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        used = set()
        with zipfile.ZipFile(self.wfile, 'w', zipfile.ZIP_STORED) as archive:
            for index in range(len(share['photos'])):
                path = self.portal.photo(share_id, index)
                if path is not None:
                    data, name = self.portal.full(path)
                    archive.writestr(unique_name(name, used), data)
