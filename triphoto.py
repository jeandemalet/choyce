from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import json
import os
import queue
import re
import shutil
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageCms, ImageOps, ImageTk


# The folder keeps its name: every existing project, export and note refers to it.
APP_NAME = "Choyce"
APP_VERSION = "0.25.0"
SESSION_DIR_NAME = "TriPhoto_Session"
SESSION_FILE_NAME = "session.json"
SESSION_VERSION = 2
EXPORT_DIR_NAME = "Export_5_Etoiles"
SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
    ".bmp",
}
SORT_OPTIONS = {
    "name_asc": "Nom A → Z",
    "name_desc": "Nom Z → A",
    "date_asc": "Date ancienne → récente",
    "date_desc": "Date récente → ancienne",
}
STAR_FILTER_OPTIONS = {
    "all": "Toutes les notes",
    0: "0 étoile",
    1: "1 étoile",
    2: "2 étoiles",
    3: "3 étoiles",
    4: "4 étoiles",
    5: "5 étoiles",
}
FLAG_FILTER_OPTIONS = {
    "all": "Tous les drapeaux",
    "flagged": "Drapeaux uniquement",
    "unflagged": "Sans drapeau",
}
FAST_PREVIEW_SIDE = 800
MAX_PREVIEW_SIDE = 1800
THUMBNAIL_SIZE = (150, 105)
THUMB_ROW_HEIGHT = 138
PRELOAD_RADIUS = 2
CANVAS_PADDING = 40


PICK_NEUTRAL, PICK_SELECT, PICK_REJECT = "neutral", "select", "reject"
PICK_VALUES = (PICK_NEUTRAL, PICK_SELECT, PICK_REJECT)


@dataclass
class Photo:
    path: Path
    relative_path: str
    stars: int = 0
    pick: str = PICK_NEUTRAL
    liked: bool = False
    publish: bool = False

    # The historical flag is the "select" state of the three-state marker.
    @property
    def flagged(self) -> bool:
        return self.pick == PICK_SELECT

    @flagged.setter
    def flagged(self, value: bool) -> None:
        self.pick = PICK_SELECT if value else PICK_NEUTRAL


def norm_path(path: Path) -> Path:
    return Path(os.path.normcase(os.path.abspath(str(path))))


def is_inside(path: Path, parent: Path) -> bool:
    try:
        norm_path(path).relative_to(norm_path(parent))
        return True
    except ValueError:
        return False


def ensure_writable_session_dir(root: Path) -> Path:
    session_dir = root / SESSION_DIR_NAME
    session_dir.mkdir(exist_ok=True)
    return session_dir


def discover_photos(root: Path) -> list[Photo]:
    root = norm_path(root)
    photos: list[Photo] = []
    for path in sorted(root.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        # Also skips the session folders of projects nested in this one.
        rel_path = path.relative_to(root)
        if SESSION_DIR_NAME.lower() in (part.lower() for part in rel_path.parts):
            continue
        rel = rel_path.as_posix()
        photos.append(Photo(path=path, relative_path=rel))
    return photos


def sort_photos(photos: list[Photo], mode: str) -> list[Photo]:
    if mode not in SORT_OPTIONS:
        mode = "name_asc"
    if mode.startswith("date"):
        def date_key(photo: Photo) -> tuple[float, list[tuple[int, object]]]:
            try:
                return (photo.path.stat().st_mtime, natural_sort_key(photo.relative_path))
            except OSError:
                return (0.0, natural_sort_key(photo.relative_path))

        return sorted(photos, key=date_key, reverse=mode == "date_desc")
    return sorted(photos, key=lambda photo: natural_sort_key(photo.relative_path), reverse=mode == "name_desc")


def natural_sort_key(value: str) -> list[tuple[int, object]]:
    """Sort embedded digit groups numerically, not character by character."""
    return [
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", value)
    ]


def original_inventory(root: Path) -> dict[str, dict[str, int]]:
    inventory: dict[str, dict[str, int]] = {}
    for photo in discover_photos(root):
        try:
            stat = photo.path.stat()
        except OSError:
            continue
        inventory[photo.relative_path] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return inventory


def inventory_fingerprint(inventory: dict[str, dict[str, int]]) -> str:
    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_srgb_profile():
    return ImageCms.createProfile("sRGB")


def convert_to_srgb(image: Image.Image, target_profile=None) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    icc_profile = image.info.get("icc_profile")
    if icc_profile:
        try:
            source = ImageCms.ImageCmsProfile(BytesIO(icc_profile))
            target = target_profile or create_srgb_profile()
            converted = ImageCms.profileToProfile(image, source, target, outputMode="RGB")
            return converted
        except Exception:
            pass
    if image.mode in ("RGBA", "LA"):
        background = Image.new("RGB", image.size, "white")
        alpha = image.getchannel("A")
        background.paste(image.convert("RGB"), mask=alpha)
        image = background
    else:
        image = image.convert("RGB")
    if target_profile is not None:
        return ImageCms.profileToProfile(image, create_srgb_profile(), target_profile, outputMode="RGB")
    return image


def load_srgb(path: Path, max_side: int | None = None) -> Image.Image:
    with Image.open(path) as source:
        # JPEG/TIFF decoders can often decode directly near the requested size.
        # This avoids expanding a 45 MP original before making a preview.
        if max_side:
            try:
                source.draft("RGB", (max_side, max_side))
            except (AttributeError, OSError):
                pass
        image = convert_to_srgb(source)
        if max_side and max(image.size) > max_side:
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return image.copy()


def load_screen_preview(path: Path, max_side: int, target_profile=None) -> Image.Image:
    """Load a color-managed screen preview after decoder-level reduction.

    The decoder reduces large originals before the ICC conversion, keeping
    navigation responsive while preserving the intended screen colors.
    """
    with Image.open(path) as source:
        try:
            source.draft("RGB", (max_side, max_side))
        except (AttributeError, OSError):
            pass
        image = convert_to_srgb(source, target_profile)
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
        return image.copy()


def fit_scale(image_size: tuple[int, int], canvas_size: tuple[int, int], padding: int = CANVAS_PADDING) -> float:
    image_width, image_height = image_size
    canvas_width, canvas_height = canvas_size
    available_width = max(1, canvas_width - padding)
    available_height = max(1, canvas_height - padding)
    if image_width <= 0 or image_height <= 0:
        return 1.0
    return min(available_width / image_width, available_height / image_height)


def export_original_copy(source_path: Path, destination_path: Path) -> None:
    """Copy the original bytes without decoding, converting, or re-encoding."""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)


def export_folder_name(text: str) -> str:
    """What Windows accepts as the name of a folder."""
    return re.sub(r'[<>:"/\\|?*]', '-', text).strip(' .')


def write_json_atomic(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


class PhotoSession:
    def __init__(self, root: Path):
        self.root = norm_path(root)
        self.session_dir = ensure_writable_session_dir(self.root)
        self.session_file = self.session_dir / SESSION_FILE_NAME
        self.sort_mode = "name_asc"
        # Saved notes of photos out of reach for now, written back as they were.
        self.kept: list[dict] = []
        self.photos = sort_photos(self.discover(), self.sort_mode)
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.load()

    def discover(self) -> list[Photo]:
        return discover_photos(self.root)

    def out_of_reach(self, relative_path: str) -> bool:
        return False

    def load(self) -> None:
        if not self.session_file.exists():
            return
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
            self.kept = [item for item in data.get("photos", []) if self.out_of_reach(item.get("relative_path"))]
            self.sort_mode = data.get("sort_mode", "name_asc") if data.get("sort_mode", "name_asc") in SORT_OPTIONS else "name_asc"
            if int(data.get("version", 1)) < SESSION_VERSION:
                # First opening by this version: keep the older file once.
                backup = self.session_dir / f"session.v{int(data.get('version', 1))}.bak.json"
                if not backup.exists():
                    shutil.copy2(self.session_file, backup)
            saved = {
                item.get("relative_path"): {
                    "stars": int(item.get("stars", 0)),
                    "pick": item.get("pick") if item.get("pick") in PICK_VALUES
                        else (PICK_SELECT if item.get("flagged", False) else PICK_NEUTRAL),
                    "liked": bool(item.get("liked", False)),
                    "publish": bool(item.get("publish", False)),
                }
                for item in data.get("photos", [])
            }
            for photo in self.photos:
                state = saved.get(photo.relative_path, {})
                photo.stars = max(0, min(5, state.get("stars", 0)))
                photo.pick = state.get("pick", PICK_NEUTRAL)
                photo.liked = state.get("liked", False)
                photo.publish = state.get("publish", False)
            self.photos = sort_photos(self.photos, self.sort_mode)
            self.created_at = data.get("created_at", self.created_at)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        payload = {
            "application": APP_NAME,
            "version": SESSION_VERSION,
            "created_at": self.created_at,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "root": str(self.root),
            "sort_mode": self.sort_mode,
            "color_management": "Original bytes copied unchanged; no export conversion",
            "originals_policy": "Original files are read-only to this application.",
            "inventory": original_inventory(self.root),
            "photos": [
                # "flagged" stays written so that older versions can read the file.
                {"relative_path": photo.relative_path, "stars": photo.stars,
                 "flagged": photo.flagged, "pick": photo.pick, "liked": photo.liked, "publish": photo.publish}
                for photo in self.photos
            ]+self.kept,
        }
        write_json_atomic(self.session_file, payload)

    def refresh(self) -> None:
        saved = {
            photo.relative_path: {"relative_path": photo.relative_path, "stars": photo.stars, "flagged": photo.flagged,
                                  "pick": photo.pick, "liked": photo.liked, "publish": photo.publish}
            for photo in self.photos
        }
        saved.update({item["relative_path"]: item for item in self.kept})
        self.photos = sort_photos(self.discover(), self.sort_mode)
        for photo in self.photos:
            state = saved.get(photo.relative_path, {})
            photo.stars, photo.pick = state.get("stars", 0), state.get("pick", PICK_NEUTRAL)
            photo.liked, photo.publish = state.get("liked", False), state.get("publish", False)
        self.kept = [item for relative, item in saved.items() if self.out_of_reach(relative)]


class ImageCache:
    def __init__(self, capacity: int = 7):
        self.capacity = capacity
        self.items: OrderedDict[tuple[str, int | None], Image.Image] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: tuple[str, int | None]) -> Image.Image | None:
        with self.lock:
            image = self.items.get(key)
            if image is not None:
                self.items.move_to_end(key)
                return image
        return None

    def put(self, key: tuple[str, int | None], image: Image.Image) -> None:
        with self.lock:
            self.items[key] = image
            self.items.move_to_end(key)
            while len(self.items) > self.capacity:
                self.items.popitem(last=False)


class TriPhotoApp:
    def __init__(self, root_window: tk.Tk, session: PhotoSession):
        self.root_window = root_window
        self.session = session
        self.all_photos = session.photos
        self.photos = self.all_photos
        self.star_filter: str | int = "all"
        self.flag_filter = "all"
        self.index = 0
        self.selected: set[int] = set()
        self.anchor_index = 0
        self.zoom = 1.0
        self.pan_x = 0
        self.pan_y = 0
        self.drag_start: tuple[int, int] | None = None
        self.main_cache = ImageCache(capacity=5)
        self.thumbnail_cache = ImageCache(capacity=36)
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="triphoto")
        self.thumbnail_refs: dict[int, ImageTk.PhotoImage] = {}
        self.main_ref: ImageTk.PhotoImage | None = None
        self.load_queue: queue.Queue[tuple[str, tuple[str, int | None], Image.Image | None, str | None]] = queue.Queue()
        self.loading: set[tuple[str, int | None]] = set()
        self.main_futures = {}
        self.preview_cache_dir = self.session.session_dir / "Previews"
        self.preview_cache_dir.mkdir(exist_ok=True)
        self.originals_before = original_inventory(self.session.root)

        self.root_window.title(f"{APP_NAME} | {self.session.root.name}")
        self.root_window.geometry("1400x900")
        self.root_window.minsize(950, 600)
        self.root_window.state("zoomed")
        self.root_window.configure(bg="#17191c")
        self.build_ui()
        self.bind_keys()
        self.root_window.update_idletasks()
        self.refresh_sidebar()
        if self.photos:
            self.selected = {0}
            self.show_index(0)
        self.root_window.after(80, self.process_load_queue)
        self.root_window.protocol("WM_DELETE_WINDOW", self.close)

    def build_ui(self) -> None:
        self.menu = tk.Menu(self.root_window)
        project_menu = tk.Menu(self.menu, tearoff=False)
        project_menu.add_command(label="Ouvrir un dossier…", command=self.open_folder)
        project_menu.add_command(label="Actualiser le dossier", command=self.refresh_session)
        project_menu.add_separator()
        project_menu.add_command(label="Exporter les cinq étoiles", command=self.export_five_stars)
        project_menu.add_command(label="Vérifier l’intégrité des originaux", command=self.verify_originals)
        project_menu.add_separator()
        project_menu.add_command(label="Quitter", command=self.close)
        self.menu.add_cascade(label="Projet", menu=project_menu)
        self.root_window.configure(menu=self.menu)

        toolbar = tk.Frame(self.root_window, bg="#202328", height=48)
        toolbar.pack(fill="x", side="top")
        tk.Label(toolbar, text="TRIPHOTO", fg="#f1f1f1", bg="#202328", font=("Segoe UI", 12, "bold")).pack(side="left", padx=(18, 20))
        self.folder_label = tk.Label(toolbar, text=str(self.session.root), fg="#9ca3af", bg="#202328", anchor="w")
        self.folder_label.pack(side="left", fill="x", expand=True)
        sort_frame = tk.Frame(toolbar, bg="#202328")
        sort_frame.pack(side="right", padx=(0, 12))
        tk.Label(sort_frame, text="Trier", fg="#9ca3af", bg="#202328").pack(side="left", padx=(0, 6))
        self.sort_combo = ttk.Combobox(sort_frame, state="readonly", width=25, values=list(SORT_OPTIONS.values()))
        self.sort_combo.set(SORT_OPTIONS[self.session.sort_mode])
        self.sort_combo.pack(side="left")
        self.sort_combo.bind("<<ComboboxSelected>>", self.change_sort_mode)
        flag_filter_frame = tk.Frame(toolbar, bg="#202328")
        flag_filter_frame.pack(side="right", padx=(0, 12))
        tk.Label(flag_filter_frame, text="Drapeau", fg="#9ca3af", bg="#202328").pack(side="left", padx=(0, 6))
        self.flag_filter_combo = ttk.Combobox(flag_filter_frame, state="readonly", width=19, values=list(FLAG_FILTER_OPTIONS.values()))
        self.flag_filter_combo.set(FLAG_FILTER_OPTIONS[self.flag_filter])
        self.flag_filter_combo.pack(side="left")
        self.flag_filter_combo.bind("<<ComboboxSelected>>", self.change_flag_filter)
        star_filter_frame = tk.Frame(toolbar, bg="#202328")
        star_filter_frame.pack(side="right", padx=(0, 12))
        tk.Label(star_filter_frame, text="Notes", fg="#9ca3af", bg="#202328").pack(side="left", padx=(0, 6))
        self.star_filter_combo = ttk.Combobox(star_filter_frame, state="readonly", width=16, values=list(STAR_FILTER_OPTIONS.values()))
        self.star_filter_combo.set(STAR_FILTER_OPTIONS[self.star_filter])
        self.star_filter_combo.pack(side="left")
        self.star_filter_combo.bind("<<ComboboxSelected>>", self.change_star_filter)
        self.load_progress = ttk.Progressbar(toolbar, mode="indeterminate", length=120)
        self.counter_label = tk.Label(toolbar, text="", fg="#d7dbe0", bg="#202328")
        self.counter_label.pack(side="right", padx=18)

        body = tk.Frame(self.root_window, bg="#17191c")
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="#111315", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.render_main())
        self.canvas.bind("<MouseWheel>", self.on_zoom)
        self.canvas.bind("<ButtonPress-1>", self.start_pan)
        self.canvas.bind("<B1-Motion>", self.move_pan)
        self.canvas.bind("<ButtonRelease-1>", self.end_pan)

        sidebar_frame = tk.Frame(body, bg="#202328", width=230)
        sidebar_frame.pack(side="right", fill="y")
        sidebar_frame.pack_propagate(False)
        sidebar_header = tk.Frame(sidebar_frame, bg="#202328", height=38)
        sidebar_header.pack(fill="x")
        self.selection_label = tk.Label(sidebar_header, text="0 sélectionnée", bg="#202328", fg="#d7dbe0", anchor="w")
        self.selection_label.pack(side="left", padx=12)
        self.thumb_canvas = tk.Canvas(sidebar_frame, bg="#202328", highlightthickness=0)
        self.thumb_canvas.pack(side="left", fill="both", expand=True)
        self.thumb_scrollbar = tk.Canvas(sidebar_frame, width=18, bg="#111315", highlightthickness=0, cursor="hand2")
        self.thumb_scrollbar.pack(side="right", fill="y")
        self.thumb_scrollbar.bind("<Button-1>", self.on_scrollbar_press)
        self.thumb_scrollbar.bind("<B1-Motion>", self.on_scrollbar_drag)
        self.thumb_scrollbar.bind("<Configure>", lambda _event: self.update_custom_scrollbar())
        self.scrollbar_drag_offset: float | None = None
        self.thumb_canvas.bind("<Configure>", self.on_thumbnail_canvas_configure)
        self.thumb_canvas.bind("<MouseWheel>", self.on_thumbnail_scroll)
        self.thumb_canvas.bind("<Button-1>", self.on_thumbnail_click)

        bottom = tk.Frame(self.root_window, bg="#202328", height=34)
        bottom.pack(fill="x", side="bottom")
        self.status_label = tk.Label(bottom, text="C/V ou ←/→ naviguer · Maj C zéro étoile · Maj V cinq étoiles · Ctrl+clic / Maj+clic sélectionner · clic drapeau garder", bg="#202328", fg="#8f98a3", anchor="w")
        self.status_label.pack(side="left", padx=14)
        self.zoom_label = tk.Label(bottom, text="100 %", bg="#202328", fg="#d7dbe0")
        self.zoom_label.pack(side="right", padx=14)

    def bind_keys(self) -> None:
        self.root_window.bind_all("<KeyPress-c>", lambda event: self.handle_key(event, "previous"))
        self.root_window.bind_all("<KeyPress-C>", lambda event: self.handle_key(event, "previous"))
        self.root_window.bind_all("<KeyPress-v>", lambda event: self.handle_key(event, "next"))
        self.root_window.bind_all("<KeyPress-V>", lambda event: self.handle_key(event, "next"))
        self.root_window.bind_all("<Left>", lambda event: self.handle_key(event, "previous"))
        self.root_window.bind_all("<Right>", lambda event: self.handle_key(event, "next"))
        self.root_window.bind_all("<Shift-KeyPress-c>", lambda event: self.handle_key(event, "zero"))
        self.root_window.bind_all("<Shift-KeyPress-C>", lambda event: self.handle_key(event, "zero"))
        self.root_window.bind_all("<Shift-KeyPress-v>", lambda event: self.handle_key(event, "five"))
        self.root_window.bind_all("<Shift-KeyPress-V>", lambda event: self.handle_key(event, "five"))
        self.root_window.bind_all("<KeyPress-plus>", lambda _event: self.adjust_zoom(1.15))
        self.root_window.bind_all("<KeyPress-minus>", lambda _event: self.adjust_zoom(1 / 1.15))
        self.root_window.bind_all("<KeyPress-0>", lambda _event: self.set_zoom(1.0))

    def handle_key(self, event: tk.Event, action: str):
        if action == "previous":
            next_index = max(0, self.index - 1)
            self.selected = {next_index}
            self.anchor_index = next_index
            self.show_index(next_index)
        elif action == "next":
            next_index = min(len(self.photos) - 1, self.index + 1)
            self.selected = {next_index}
            self.anchor_index = next_index
            self.show_index(next_index)
        elif action in {"zero", "five"}:
            self.set_stars(0 if action == "zero" else 5)
        return "break"

    def refresh_sidebar(self) -> None:
        self.thumbnail_refs = {}
        total_height = max(1, len(self.photos) * THUMB_ROW_HEIGHT)
        self.thumb_canvas.configure(scrollregion=(0, 0, max(1, self.thumb_canvas.winfo_width()), total_height))
        self.render_thumbnail_panel()
        self.update_selection_ui()
        self.root_window.after_idle(self.load_visible_thumbnails)

    def on_thumbnail_canvas_configure(self, event: tk.Event) -> None:
        self.thumb_canvas.configure(scrollregion=(0, 0, event.width, max(1, len(self.photos) * THUMB_ROW_HEIGHT)))
        self.render_thumbnail_panel()
        self.root_window.after_idle(self.load_visible_thumbnails)

    def on_thumbnail_scroll(self, event: tk.Event) -> None:
        self.thumb_canvas.yview_scroll(-int(event.delta / 120), "units")
        self.render_thumbnail_panel()
        self.root_window.after_idle(self.load_visible_thumbnails)

    def update_custom_scrollbar(self) -> None:
        self.thumb_scrollbar.delete("all")
        height = max(1, self.thumb_scrollbar.winfo_height())
        top_fraction, bottom_fraction = self.thumb_canvas.yview()
        thumb_top = top_fraction * height
        thumb_height = max(28, (bottom_fraction - top_fraction) * height)
        thumb_bottom = min(height, thumb_top + thumb_height)
        self.thumb_scrollbar.create_rectangle(4, 0, 14, height, fill="#202328", outline="")
        self.thumb_scrollbar.create_rectangle(3, thumb_top, 15, thumb_bottom, fill="#69727e", outline="")
        self.thumb_scrollbar.create_line(6, thumb_top + 6, 12, thumb_top + 6, fill="#b8c0ca", width=1)
        self.thumb_scrollbar.create_line(6, thumb_bottom - 6, 12, thumb_bottom - 6, fill="#b8c0ca", width=1)

    def on_scrollbar_press(self, event: tk.Event) -> None:
        height = max(1, self.thumb_scrollbar.winfo_height())
        top_fraction, bottom_fraction = self.thumb_canvas.yview()
        thumb_top = top_fraction * height
        thumb_height = max(28, (bottom_fraction - top_fraction) * height)
        if thumb_top <= event.y <= thumb_top + thumb_height:
            self.scrollbar_drag_offset = event.y - thumb_top
        else:
            self.scrollbar_drag_offset = thumb_height / 2
            self.scrollbar_move_to(event.y)

    def on_scrollbar_drag(self, event: tk.Event) -> None:
        if self.scrollbar_drag_offset is None:
            return
        self.scrollbar_move_to(event.y - self.scrollbar_drag_offset)

    def scrollbar_move_to(self, y: float) -> None:
        height = max(1, self.thumb_scrollbar.winfo_height())
        top_fraction, bottom_fraction = self.thumb_canvas.yview()
        thumb_fraction = max(0.0, bottom_fraction - top_fraction)
        maximum_top = max(0.0, 1.0 - thumb_fraction)
        target = max(0.0, min(maximum_top, y / height))
        self.thumb_canvas.yview_moveto(target)
        self.render_thumbnail_panel()
        self.root_window.after_idle(self.load_visible_thumbnails)

    def load_visible_thumbnails(self) -> None:
        if not self.photos:
            return
        top_fraction, bottom_fraction = self.thumb_canvas.yview()
        view_height = max(1, self.thumb_canvas.winfo_height())
        top = max(0, int(top_fraction * len(self.photos) * THUMB_ROW_HEIGHT) - 4 * THUMB_ROW_HEIGHT)
        bottom = int(bottom_fraction * len(self.photos) * THUMB_ROW_HEIGHT) + 4 * THUMB_ROW_HEIGHT + view_height
        first = max(0, top // THUMB_ROW_HEIGHT)
        last = min(len(self.photos), bottom // THUMB_ROW_HEIGHT + 1)
        for index in range(first, last):
            self.request_image(index, 256, "thumbnail")

    def on_thumbnail_click(self, event: tk.Event) -> None:
        if not self.photos:
            return
        y = int(self.thumb_canvas.canvasy(event.y))
        index = y // THUMB_ROW_HEIGHT
        if 0 <= index < len(self.photos):
            if event.x >= self.thumb_canvas.winfo_width() - 52:
                self.toggle_flag(index)
                return
            self.select_thumbnail(index, event)

    def render_thumbnail_panel(self) -> None:
        self.thumb_canvas.delete("all")
        self.thumbnail_refs = {}
        if not self.photos:
            self.update_custom_scrollbar()
            return
        top_fraction, bottom_fraction = self.thumb_canvas.yview()
        total_height = len(self.photos) * THUMB_ROW_HEIGHT
        view_height = max(1, self.thumb_canvas.winfo_height())
        first = max(0, int(top_fraction * total_height) // THUMB_ROW_HEIGHT - 4)
        last = min(len(self.photos), int(bottom_fraction * total_height + view_height) // THUMB_ROW_HEIGHT + 5)
        width = max(180, self.thumb_canvas.winfo_width())
        for index in range(first, last):
            photo = self.photos[index]
            y = index * THUMB_ROW_HEIGHT
            selected = index in self.selected
            colour = "#9b8cff" if selected else "#202328"
            self.thumb_canvas.create_rectangle(0, y, width, y + THUMB_ROW_HEIGHT, fill=colour, outline="")
            self.thumb_canvas.create_rectangle(7, y + 6, 157, y + 111, fill="#111315", outline="#30343b", width=2)
            image = self.thumbnail_cache.get((str(photo.path), 256))
            if image is None:
                self.thumb_canvas.create_text(82, y + 58, text="…", fill="#8f98a3", font=("Segoe UI", 18))
            else:
                image = image.copy()
                image.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                background = Image.new("RGB", THUMBNAIL_SIZE, "#111315")
                background.paste(image, ((THUMBNAIL_SIZE[0] - image.width) // 2, (THUMBNAIL_SIZE[1] - image.height) // 2))
                ref = ImageTk.PhotoImage(background)
                self.thumbnail_refs[index] = ref
                self.thumb_canvas.create_image(82, y + 58, image=ref)
            stars = "★" * photo.stars + "☆" * (5 - photo.stars)
            self.thumb_canvas.create_text(166, y + 28, text=f"{index + 1:03d}", anchor="w", fill="#d7dbe0", font=("Segoe UI", 9, "bold"))
            self.thumb_canvas.create_text(166, y + 50, text=Path(photo.relative_path).name, anchor="w", fill="#b4bac3", width=max(80, width - 174), font=("Segoe UI", 9))
            self.thumb_canvas.create_text(166, y + 89, text=stars, anchor="w", fill="#f2c14e", font=("Segoe UI", 10, "bold"))
            self.draw_flag(width - 28, y + 16, photo.flagged)
        self.update_custom_scrollbar()

    def draw_flag(self, x: int, y: int, flagged: bool) -> None:
        colour = "#f2c14e" if flagged else "#69727e"
        self.thumb_canvas.create_line(x, y, x, y + 22, fill=colour, width=2)
        self.thumb_canvas.create_polygon(x, y, x + 13, y + 4, x, y + 9, fill=colour, outline=colour)

    def update_selection_ui(self) -> None:
        count = len(self.selected)
        self.selection_label.configure(text=f"{count} sélectionnée{'' if count == 1 else 's'}")
        if self.photos:
            rated = sum(1 for photo in self.photos if photo.stars == 5)
            self.counter_label.configure(text=f"{self.index + 1} / {len(self.photos)} · {rated} cinq étoiles")
        else:
            self.counter_label.configure(text="Aucune photo")

    def select_thumbnail(self, index: int, event: tk.Event | None = None) -> None:
        if event and (event.state & 0x0001):
            start, end = sorted((self.anchor_index, index))
            self.selected = set(range(start, end + 1))
        elif event and (event.state & 0x0004):
            if index in self.selected and len(self.selected) > 1:
                self.selected.remove(index)
            else:
                self.selected.add(index)
            self.anchor_index = index
        else:
            self.selected = {index}
            self.anchor_index = index
        self.show_index(index)

    def show_index(self, index: int) -> None:
        if not self.photos:
            return
        self.index = max(0, min(index, len(self.photos) - 1))
        if not self.selected:
            self.selected = {self.index}
        self.pan_x = 0
        self.pan_y = 0
        self.cancel_stale_main_loads({self.index})
        self.render_main()
        self.update_selection_ui()
        self.render_thumbnail_panel()
        self.root_window.after_idle(lambda current_index=self.index: self.ensure_thumbnail_visible(current_index))
        self.preload_nearby()

    def ensure_thumbnail_visible(self, index: int) -> None:
        if not self.photos or not 0 <= index < len(self.photos):
            return
        total_height = max(1, len(self.photos) * THUMB_ROW_HEIGHT)
        viewport_height = max(1, self.thumb_canvas.winfo_height())
        top_fraction, bottom_fraction = self.thumb_canvas.yview()
        visible_top = top_fraction * total_height
        visible_bottom = bottom_fraction * total_height
        item_top = index * THUMB_ROW_HEIGHT
        item_bottom = item_top + THUMB_ROW_HEIGHT
        margin = 12
        target_top = None
        if item_top < visible_top + margin:
            target_top = max(0, item_top - margin)
        elif item_bottom > visible_bottom - margin:
            target_top = min(total_height - viewport_height, item_bottom - viewport_height + margin)
        if target_top is not None:
            self.thumb_canvas.yview_moveto(max(0.0, min(1.0, target_top / total_height)))
            self.render_thumbnail_panel()
            self.root_window.after_idle(self.load_visible_thumbnails)

    def cancel_stale_main_loads(self, keep_indices: set[int]) -> None:
        keep_keys = {
            (str(self.photos[index].path), MAX_PREVIEW_SIDE)
            for index in keep_indices
            if 0 <= index < len(self.photos)
        }
        keep_keys.update(
            (str(self.photos[index].path), FAST_PREVIEW_SIDE)
            for index in keep_indices
            if 0 <= index < len(self.photos)
        )
        for key, future in list(self.main_futures.items()):
            if key not in keep_keys and future.cancel():
                self.loading.discard(key)
                self.main_futures.pop(key, None)

    def request_image(self, index: int, max_side: int | None, kind: str) -> None:
        if index < 0 or index >= len(self.photos):
            return
        photo = self.photos[index]
        key = (str(photo.path), max_side)
        cache = self.thumbnail_cache if kind == "thumbnail" else self.main_cache
        if cache.get(key) is not None or key in self.loading:
            return
        self.loading.add(key)

        def worker() -> None:
            try:
                image = self.load_preview_with_disk_cache(photo, max_side, kind)
                self.load_queue.put((kind, key, image, None))
            except Exception as error:
                self.load_queue.put((kind, key, None, str(error)))

        future = self.executor.submit(worker)
        if kind != "thumbnail":
            self.main_futures[key] = future

    def load_preview_with_disk_cache(self, photo: Photo, max_side: int | None, kind: str) -> Image.Image:
        cache_path: Path | None = None
        if kind in {"thumbnail", "fast"} and max_side:
            stat = photo.path.stat()
            token = hashlib.sha1(f"preview-v3-icc|{photo.relative_path}|{stat.st_size}|{stat.st_mtime_ns}|{max_side}".encode("utf-8")).hexdigest()
            cache_path = self.preview_cache_dir / f"{token}.jpg"
            if cache_path.exists():
                try:
                    return load_srgb(cache_path)
                except (OSError, ValueError):
                    try:
                        cache_path.unlink()
                    except OSError:
                        pass
        image = load_screen_preview(photo.path, max_side)
        if cache_path is not None:
            temporary = cache_path.with_suffix(".tmp.jpg")
            image.save(temporary, format="JPEG", quality=95, subsampling=0)
            os.replace(temporary, cache_path)
        return image

    def process_load_queue(self) -> None:
        while True:
            try:
                kind, key, image, error = self.load_queue.get_nowait()
            except queue.Empty:
                break
            self.loading.discard(key)
            self.main_futures.pop(key, None)
            if image is not None:
                cache = self.thumbnail_cache if kind == "thumbnail" else self.main_cache
                cache.put(key, image)
            if kind == "thumbnail":
                self.render_thumbnail_panel()
            elif kind in {"fast", "main"} and key[0] == str(self.photos[self.index].path):
                self.render_main()
            if error and kind == "main":
                self.status_label.configure(text=f"Impossible de lire cette image : {error}")
        self.root_window.after(80, self.process_load_queue)

    def preload_nearby(self) -> None:
        keep_indices = {
            candidate
            for offset in range(-PRELOAD_RADIUS, PRELOAD_RADIUS + 1)
            for candidate in [self.index + offset]
            if 0 <= candidate < len(self.photos)
        }
        self.cancel_stale_main_loads(keep_indices)
        for offset in range(-PRELOAD_RADIUS, PRELOAD_RADIUS + 1):
            candidate = self.index + offset
            if 0 <= candidate < len(self.photos):
                self.request_image(candidate, FAST_PREVIEW_SIDE, "fast")
        self.request_image(self.index, MAX_PREVIEW_SIDE, "main")

    def render_main(self) -> None:
        self.canvas.delete("all")
        if not self.photos:
            self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2, text="Aucune photo compatible dans ce dossier", fill="#aab1ba", font=("Segoe UI", 16))
            return
        photo = self.photos[self.index]
        key = (str(photo.path), MAX_PREVIEW_SIDE)
        fast_key = (str(photo.path), FAST_PREVIEW_SIDE)
        detailed_image = self.main_cache.get(key)
        image = detailed_image
        if image is None:
            image = self.main_cache.get(fast_key)
            self.request_image(self.index, FAST_PREVIEW_SIDE, "fast")
            self.request_image(self.index, MAX_PREVIEW_SIDE, "main")
            if image is None:
                self.load_progress.pack(side="right", padx=(0, 8))
                self.load_progress.start(12)
                self.canvas.create_text(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2, text="Préparation de l’aperçu…", fill="#aab1ba", font=("Segoe UI", 16))
                self.zoom_label.configure(text=f"{round(self.zoom * 100)} %")
                return
        if detailed_image is None:
            self.load_progress.pack(side="right", padx=(0, 8))
            self.load_progress.start(12)
        else:
            self.load_progress.stop()
            self.load_progress.pack_forget()
        scale = fit_scale(image.size, (self.canvas.winfo_width(), self.canvas.winfo_height())) * self.zoom
        width = max(1, int(image.width * scale))
        height = max(1, int(image.height * scale))
        display = image.resize((width, height), Image.Resampling.LANCZOS) if abs(scale - 1.0) > 0.001 else image
        self.main_ref = ImageTk.PhotoImage(display)
        cx = self.canvas.winfo_width() // 2 + self.pan_x
        cy = self.canvas.winfo_height() // 2 + self.pan_y
        self.canvas.create_image(cx, cy, image=self.main_ref)
        self.canvas.create_text(14, 14, text=f"{Path(photo.relative_path).name}  ·  {photo.stars}/5", anchor="nw", fill="#d7dbe0", font=("Segoe UI", 10), tags="overlay")
        self.zoom_label.configure(text=f"{round(self.zoom * 100)} %")

    def on_zoom(self, event: tk.Event) -> None:
        self.adjust_zoom(1.15 if event.delta > 0 else 1 / 1.15)

    def adjust_zoom(self, factor: float) -> None:
        self.set_zoom(max(0.1, min(8.0, self.zoom * factor)))

    def set_zoom(self, value: float) -> None:
        self.zoom = value
        if self.zoom <= 1.0:
            self.pan_x = 0
            self.pan_y = 0
        self.render_main()

    def start_pan(self, event: tk.Event) -> None:
        if self.zoom > 1.0:
            self.drag_start = (event.x, event.y)

    def move_pan(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        old_x, old_y = self.drag_start
        self.pan_x += event.x - old_x
        self.pan_y += event.y - old_y
        self.drag_start = (event.x, event.y)
        self.render_main()

    def end_pan(self, _event: tk.Event) -> None:
        self.drag_start = None

    def set_stars(self, value: int) -> None:
        targets = self.selected or {self.index}
        current_path = self.photos[self.index].relative_path if self.photos else None
        selected_paths = {
            self.photos[index].relative_path
            for index in targets
            if 0 <= index < len(self.photos)
        }
        for index in targets:
            if 0 <= index < len(self.photos):
                self.photos[index].stars = value
        self.session.save()
        self.apply_filters(current_path, selected_paths)
        self.status_label.configure(text=f"{len(targets)} photo(s) passée(s) à {value} étoile")

    def toggle_flag(self, index: int) -> None:
        if not 0 <= index < len(self.photos):
            return
        photo = self.photos[index]
        photo.flagged = not photo.flagged
        current_path = photo.relative_path
        selected_paths = {
            self.photos[i].relative_path
            for i in self.selected
            if 0 <= i < len(self.photos)
        }
        self.session.save()
        self.apply_filters(current_path, selected_paths)

    def refresh_session(self) -> None:
        self.session.refresh()
        self.all_photos = self.session.photos
        current_path = self.photos[self.index].relative_path if self.photos else None
        self.apply_filters(current_path)

    def apply_filters(self, current_path: str | None = None, selected_paths: set[str] | None = None) -> None:
        if selected_paths is None:
            selected_paths = {
                self.photos[index].relative_path
                for index in self.selected
                if 0 <= index < len(self.photos)
            }
        self.photos = [
            photo for photo in self.all_photos
            if (self.star_filter == "all" or photo.stars == self.star_filter)
            and (self.flag_filter == "all" or (self.flag_filter == "flagged" and photo.flagged) or (self.flag_filter == "unflagged" and not photo.flagged))
        ]
        if current_path is not None:
            self.index = next((i for i, photo in enumerate(self.photos) if photo.relative_path == current_path), 0)
        else:
            self.index = min(self.index, max(0, len(self.photos) - 1))
        self.selected = {
            i for i, photo in enumerate(self.photos)
            if photo.relative_path in selected_paths
        }
        if not self.selected and self.photos:
            self.selected = {self.index}
        self.anchor_index = self.index
        self.refresh_sidebar()
        self.render_main()
        self.preload_nearby()
        if self.photos:
            self.ensure_thumbnail_visible(self.index)

    def change_star_filter(self, _event: tk.Event | None = None) -> None:
        label = self.star_filter_combo.get()
        self.star_filter = next((value for value, text in STAR_FILTER_OPTIONS.items() if text == label), "all")
        self.apply_filters()

    def change_flag_filter(self, _event: tk.Event | None = None) -> None:
        label = self.flag_filter_combo.get()
        self.flag_filter = next((value for value, text in FLAG_FILTER_OPTIONS.items() if text == label), "all")
        self.apply_filters()

    def change_sort_mode(self, _event: tk.Event | None = None) -> None:
        selected_label = self.sort_combo.get()
        new_mode = next((mode for mode, label in SORT_OPTIONS.items() if label == selected_label), "name_asc")
        current_path = self.photos[self.index].relative_path if self.photos else None
        selected_paths = {
            self.photos[index].relative_path
            for index in self.selected
            if 0 <= index < len(self.photos)
        }
        self.session.sort_mode = new_mode
        self.all_photos = sort_photos(self.all_photos, new_mode)
        self.session.photos = self.all_photos
        self.session.save()
        self.apply_filters(current_path, selected_paths)

    def open_folder(self) -> None:
        chosen = filedialog.askdirectory(title="Choisir le dossier de photos")
        if not chosen:
            return
        self.session.save()
        self.root_window.destroy()
        next_root = tk.Tk()
        TriPhotoApp(next_root, PhotoSession(Path(chosen)))
        next_root.mainloop()

    def export_five_stars(self) -> None:
        originals_before = original_inventory(self.session.root)
        if originals_before != self.originals_before:
            messagebox.showerror(APP_NAME, "Export bloqué : l’inventaire des originaux a changé depuis l’ouverture de la session. Vérifiez le dossier avant de continuer.")
            return
        five = [photo for photo in self.photos if photo.stars == 5]
        if not five:
            messagebox.showinfo(APP_NAME, "Aucune photo n’a cinq étoiles.")
            return
        export_dir = self.session.session_dir / EXPORT_DIR_NAME
        export_dir.mkdir(exist_ok=True)
        errors: list[str] = []
        for photo in five:
            destination = export_dir / photo.relative_path
            try:
                export_original_copy(photo.path, destination)
            except Exception as error:
                errors.append(f"{photo.relative_path}: {error}")
        self.session.save()
        originals_after = original_inventory(self.session.root)
        if originals_before != originals_after or originals_after != self.originals_before:
            messagebox.showerror(APP_NAME, f"Contrôle interrompu : l’inventaire des originaux a changé. Aucun original n’a été réécrit par {APP_NAME}, mais vérifiez le dossier avant de continuer.")
            return
        if errors:
            messagebox.showwarning(APP_NAME, f"Export terminé avec {len(errors)} erreur(s).\n\n" + "\n".join(errors[:8]))
        else:
            messagebox.showinfo(APP_NAME, f"{len(five)} photo(s) copiée(s) sans conversion dans :\n{export_dir}")

    def verify_originals(self) -> None:
        current = original_inventory(self.session.root)
        before = self.originals_before
        if current == before:
            messagebox.showinfo(APP_NAME, f"Contrôle terminé : {len(current)} original(aux) inchangé(s).\n\n{APP_NAME} n’écrit que dans {SESSION_DIR_NAME}.")
        else:
            removed = sorted(set(before) - set(current))
            added = sorted(set(current) - set(before))
            changed = sorted(name for name in set(before) & set(current) if before[name] != current[name])
            messagebox.showwarning(APP_NAME, "L’inventaire diffère depuis l’ouverture.\n\n" + f"Ajout(s) : {len(added)}\nSuppression(s) : {len(removed)}\nModification(s) : {len(changed)}")

    def close(self) -> None:
        try:
            self.session.save()
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.root_window.destroy()


def run_self_test() -> int:
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="triphoto-test-") as temporary:
        root = Path(temporary)
        # A non-RGB ICC source proves conversion occurs, rather than silently
        # falling back to Image.convert after a profile-loading error.
        lab_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
        lab_image = Image.new("LAB", (32, 24), (150, 170, 100))
        lab_image.info["icc_profile"] = lab_profile.tobytes()
        expected = ImageCms.profileToProfile(
            lab_image, lab_profile, create_srgb_profile(), outputMode="RGB"
        )
        assert convert_to_srgb(lab_image).tobytes() == expected.tobytes()
        lab_path = root / "profile-test.tif"
        lab_image.save(lab_path, icc_profile=lab_profile.tobytes())
        assert load_srgb(lab_path).tobytes() == expected.tobytes()
        assert load_screen_preview(lab_path, 32).tobytes() == expected.tobytes()
        lab_path.unlink()
        image_path = root / "image test.jpg"
        Image.new("RGB", (32, 24), (20, 40, 60)).save(image_path, quality=95)
        before = original_inventory(root)
        session = PhotoSession(root)
        assert len(session.photos) == 1
        session.photos[0].stars = 5
        session.save()
        export_original_copy(image_path, session.session_dir / EXPORT_DIR_NAME / image_path.name)
        exported_path = session.session_dir / EXPORT_DIR_NAME / image_path.name
        assert exported_path.exists()
        assert image_path.read_bytes() == exported_path.read_bytes()
        assert original_inventory(root) == before
        restored = PhotoSession(root)
        assert restored.photos[0].stars == 5
    print("TriPhoto self-test: OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    root_window = tk.Tk()
    root_window.withdraw()
    chosen = filedialog.askdirectory(title="Choisir le dossier de photos à trier")
    if not chosen:
        root_window.destroy()
        return 0
    root_window.deiconify()
    TriPhotoApp(root_window, PhotoSession(Path(chosen)))
    root_window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
