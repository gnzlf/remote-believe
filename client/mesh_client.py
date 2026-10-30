#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Remote Believe Client v2.0 (Tailscale P2P Edition)

Cliente P2P directo (Oficina <-> Casa) utilizando Tailscale.
- Modo Oficina (Servidor): Lee discos fisicos/de red locales y sirve API local en puerto 7878.
- Modo Casa (Cliente): Obtiene IP de Tailscale de la oficina y descarga/sube directo.
"""

import hashlib
import http.server
import json
import logging
import math
import ntpath
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import calendar
from http.cookiejar import CookieJar
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, filedialog, messagebox
import tkinter as tk
from tkinter import ttk
from urllib import error, parse, request as url_request
from local_deletions import LocalDeletionPolicy
from cache_paths import cache_relative_path, local_path_key
from content_hash import file_hash
from desktop_controls import DesktopControls
from asset_panel import AssetUpdatesPanel
from asset_updates import OFFICE_ASSETS, master_kind
from transfer_control import TransferControl, TransferStopped, ControlledHTTPHandler
from app_updates import GitHubUpdates, UpdateError, DOWNLOAD_PAGE, version as release_version
from office_errors import OfficeError as RBError, OfficeNetworkError, OfficeResponseError

# ─── Constantes ──────────────────────────────────────────────────────────────

APP_NAME        = "Remote Believe"
APP_VERSION     = "3.2.1-explorer"
UPLOAD_CHUNK    = 4 * 1024 * 1024    # Chunks mas chicos para que descargas puedan convivir con subidas
PEER_PORT       = 8090               # Puerto del servidor local mesh (Oficina)
COMMAND_PORT    = 7879               # Puerto local para ordenes del Explorer
HEARTBEAT_S     = 60                 # Heartbeat
WATCH_S         = 4                  # Watcher de cambios
STABLE_S        = 5                  # Segundos antes de subir
MAX_PREP_FILES  = 5000               # Limite preparacion
PEER_TIMEOUT_S  = 300                # Timeout general de red
DOWNLOAD_TIMEOUT_S = 900             # Archivos pesados/secuencias pueden tardar bastante
DOWNLOAD_RETRIES = 3
SHA256_CHUNK    = 1024 * 1024        # Chunks para hash
DOWNLOAD_CHUNK  = 2 * 1024 * 1024
MAX_MIRROR_ITEMS = 200000            # Limite de placeholders por sesion
REMOTE_MTIME_TOLERANCE_S = 2.0       # SMB/Windows puede redondear timestamps
MAX_UPLOADS_PER_OUTBOX_CYCLE = 3     # Evita que una secuencia bloquee la cola completa
UPLOAD_YIELD_WHEN_DOWNLOADING_S = 0.15
OFFLINE_FOLDER_SYNC_S = 30
MAX_OFFLINE_DOWNLOADS_PER_CYCLE = 250
JSON_SAVE_RETRIES = 40
JSON_SAVE_RETRY_S = 0.05

# Paleta visual estilo iOS
BG       = "#f5f5f7"
PANEL    = "#ffffff"
SIDEBAR  = "#f2f2f7"
LINE     = "#d8dee8"
TEXT     = "#1c1c1e"
MUTED    = "#6e6e73"
BLUE     = "#0a84ff"
BLUE_SOFT = "#e7f1ff"
GREEN    = "#34c759"
GREEN_SOFT = "#e8f8ee"
CLOUD    = "#7b8794"
RED      = "#ff3b30"
RED_SOFT = "#fff0ef"
YELLOW   = "#ff9f0a"
YELLOW_SOFT = "#fff6e6"
OFFICE_COL = RED
CONTROL_BG = "#eef2f7"
ROW_ALT = "#fbfcff"

THEMES = {
    "light": {
        "BG": "#f5f5f7",
        "PANEL": "#ffffff",
        "SIDEBAR": "#f2f2f7",
        "LINE": "#d8dee8",
        "TEXT": "#1c1c1e",
        "MUTED": "#6e6e73",
        "BLUE": "#0a84ff",
        "BLUE_SOFT": "#e7f1ff",
        "GREEN": "#34c759",
        "GREEN_SOFT": "#e8f8ee",
        "CLOUD": "#7b8794",
        "RED": "#ff3b30",
        "RED_SOFT": "#fff0ef",
        "YELLOW": "#ff9f0a",
        "YELLOW_SOFT": "#fff6e6",
        "CONTROL_BG": "#eef2f7",
        "ROW_ALT": "#fbfcff",
        "CONTROL_ACTIVE": "#e3e9f2",
        "TREE_HEAD_BG": "#fbfcff",
        "SELECT_BG": "#dcecff",
        "PROGRESS_TROUGH": "#e9eef5",
        "CARD_BORDER": "#e6e9ef",
        "SECTION_BG": "#f7f9fc",
        "TILE_BG": "#ffffff",
    },
    "dark": {
        "BG": "#1c1c1e",
        "PANEL": "#2c2c2e",
        "SIDEBAR": "#242426",
        "LINE": "#3a3a3c",
        "TEXT": "#f5f5f7",
        "MUTED": "#a1a1a6",
        "BLUE": "#0a84ff",
        "BLUE_SOFT": "#10375f",
        "GREEN": "#30d158",
        "GREEN_SOFT": "#153d24",
        "CLOUD": "#9aa0a6",
        "RED": "#ff453a",
        "RED_SOFT": "#4a1f1c",
        "YELLOW": "#ffd60a",
        "YELLOW_SOFT": "#3d2f12",
        "CONTROL_BG": "#3a3a3c",
        "ROW_ALT": "#252527",
        "CONTROL_ACTIVE": "#48484a",
        "TREE_HEAD_BG": "#323236",
        "SELECT_BG": "#164d7c",
        "PROGRESS_TROUGH": "#3a3a3c",
        "CARD_BORDER": "#3a3a3c",
        "SECTION_BG": "#242426",
        "TILE_BG": "#2c2c2e",
    },
}

CONTROL_ACTIVE = THEMES["light"]["CONTROL_ACTIVE"]
TREE_HEAD_BG = THEMES["light"]["TREE_HEAD_BG"]
SELECT_BG = THEMES["light"]["SELECT_BG"]
PROGRESS_TROUGH = THEMES["light"]["PROGRESS_TROUGH"]
CARD_BORDER = THEMES["light"]["CARD_BORDER"]
SECTION_BG = THEMES["light"]["SECTION_BG"]
TILE_BG = THEMES["light"]["TILE_BG"]

def apply_visual_theme(theme_name):
    palette = THEMES.get(str(theme_name or "light").lower(), THEMES["light"])
    globals().update(palette)
    globals()["OFFICE_COL"] = globals()["RED"]

FONT_UI = ("Segoe UI Variable", 10)
FONT_UI_FALLBACK = ("Segoe UI", 10)
ICON_BACK = "\u2190"
ICON_FORWARD = "\u2192"
ICON_UP = "\u2191"
ICON_REFRESH = "\u21bb"
ICON_FOLDER = "\U0001f4c1"
ICON_FILE = "\U0001f4c4"
ICON_CLOUD = "\u2601"
ICON_CHECK = "\u2713"
ICON_OFFICE = "\u25cf"
ICON_UPLOAD = "\u2191"
ICON_ERROR = "!"
ICON_DENIED = "\u26a0"

MAYA_EXTS = {".ma"}
DEP_EXTS  = {
    ".abc", ".ass", ".bmp", ".dds", ".exr", ".fbx", ".gif", ".hdr",
    ".iff", ".jpeg", ".jpg", ".ma", ".mb", ".mel", ".mov", ".mp4",
    ".obj", ".png", ".psd", ".rat", ".rs", ".sgi", ".tga", ".tif",
    ".tiff", ".tx", ".usd", ".usda", ".usdc", ".vdb", ".webp", ".xml",
}
IMAGE_SEQUENCE_EXTS = {
    ".bmp", ".dds", ".exr", ".gif", ".hdr", ".iff", ".jpeg", ".jpg",
    ".png", ".psd", ".rat", ".sgi", ".tga", ".tif", ".tiff", ".tx",
    ".webp",
}
AUTO_UPLOAD_ALLOWED_EXTS = {
    ".abc", ".aif", ".aiff", ".avi", ".exr", ".fbx", ".iff", ".jpg",
    ".jpeg", ".ma", ".mb", ".mov", ".mp4", ".png", ".tga", ".tif",
    ".tiff", ".wav",
}
AUTO_UPLOAD_ALLOWED_SEGMENTS = {
    "shots", "shot", "scenefiles", "animation", "lighting", "render",
    "renders", "rendering", "2drender", "3drender", "playblast",
    "playblasts", "exports", "output", "outputs", "preview",
    "previews", "media",
}
RENDER_UPLOAD_SEGMENTS = {
    "render", "renders", "rendering", "2drender", "3drender",
    "playblast", "playblasts", "output", "outputs", "preview",
    "previews", "media",
}
RENDER_UPLOAD_EXTS = IMAGE_SEQUENCE_EXTS | {".mov", ".mp4", ".avi"}
UPLOAD_BLOCKED_SEGMENTS = {
    ".prism", "_prism", "prism", "pipeline", "00_pipeline",
    "_tools_library", "tools", "_tools", "rig", "rigs", "characters",
    "props", "environment", "environments", "textures", "sourceimages",
    "asset", "assets", "reference", "references", "cache", "caches",
    "sim", "sims", "fx",
}
R_UPLOAD_BLOCKED_SEGMENTS = {
    ".prism", "_prism", "prism", "pipeline", "00_pipeline",
    "cache", "caches", "sim", "sims", "fx",
}
UPLOAD_BLOCKED_EXTS = {
    ".tmp", ".part", ".downloading", ".uploading", ".lock", ".swp",
}
DRIVE_ALIASES = {
    "X": ["X:/", "X:\\", "//BLVSERVER/Proyectos_3D/", "\\\\BLVSERVER\\Proyectos_3D\\"],
    "R": ["R:/", "R:\\", "//BLVSERVER/Rig/",           "\\\\BLVSERVER\\Rig\\"],
}

# ─── Data y Utils ────────────────────────────────────────────────────────────

def _app_data():
    base = os.environ.get("APPDATA")
    return Path(base) / "RemoteBelieveMesh" if base else Path.home() / ".remote-believe-mesh"

DATA_DIR      = _app_data()
SETTINGS_PATH = DATA_DIR / "settings.json"
MANIFEST_PATH = DATA_DIR / "manifest.json"
OUTBOX_PATH   = DATA_DIR / "outbox.json"
DEVICE_ID_FILE = DATA_DIR / "device_id.txt"
DEFAULT_CACHE = Path.home() / "RemoteBelieveSync"
PLACEHOLDER_MARKER = ".remote-believe-placeholder.json"

JSON_SAVE_LOCKS = {}
JSON_SAVE_LOCKS_GUARD = threading.Lock()

WIN_ATTR_HIDDEN = 0x2
WIN_ATTR_ARCHIVE = 0x20
WIN_ATTR_OFFLINE = 0x1000
WIN_ATTR_NOT_CONTENT_INDEXED = 0x2000

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default

def json_save_lock(path):
    key = local_path_key(Path(path)).casefold()
    with JSON_SAVE_LOCKS_GUARD:
        lock = JSON_SAVE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            JSON_SAVE_LOCKS[key] = lock
        return lock

def save_json(path, data):
    path = Path(path)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    lock = json_save_lock(path)
    last_error = None
    tmp = None
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock:
        for attempt in range(JSON_SAVE_RETRIES):
            tmp = path.with_name(".{}.{}.{}.{}.tmp".format(path.name, os.getpid(), threading.get_ident(), uuid.uuid4().hex))
            try:
                tmp.write_text(text, encoding="utf-8")
                os.replace(str(tmp), str(path))
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                winerror = getattr(exc, "winerror", None)
                if not isinstance(exc, PermissionError) and winerror not in {5, 32, 33}:
                    raise
                time.sleep(min(1.0, JSON_SAVE_RETRY_S * (attempt + 1)))
        if last_error:
            raise last_error

def get_windows_attributes(path):
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(Path(path)))
        if attrs == -1:
            return None
        return attrs
    except Exception:
        return None

def set_windows_attributes(path, attrs):
    if not sys.platform.startswith("win") or attrs is None:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.SetFileAttributesW(str(Path(path)), attrs))
    except Exception:
        return False

def mark_windows_placeholder(path):
    attrs = get_windows_attributes(path)
    if attrs is None:
        return
    set_windows_attributes(path, attrs | WIN_ATTR_ARCHIVE | WIN_ATTR_OFFLINE | WIN_ATTR_NOT_CONTENT_INDEXED)

def clear_windows_placeholder(path):
    attrs = get_windows_attributes(path)
    if attrs is None:
        return
    set_windows_attributes(path, attrs & ~WIN_ATTR_OFFLINE & ~WIN_ATTR_NOT_CONTENT_INDEXED)

def hide_windows_file(path):
    attrs = get_windows_attributes(path)
    if attrs is None:
        return
    set_windows_attributes(path, attrs | WIN_ATTR_HIDDEN)

def get_or_create_device_id():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DEVICE_ID_FILE.exists():
        v = DEVICE_ID_FILE.read_text(encoding="utf-8").strip()
        if v: return v
    new_id = "mesh-" + uuid.uuid4().hex[:16]
    DEVICE_ID_FILE.write_text(new_id, encoding="utf-8")
    return new_id

def tailscale_executable():
    return shutil.which("tailscale") or str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale" / "tailscale.exe")

def auto_detect_tailscale_ip():
    """Detecta la IP de Tailscale (100.x.x.x) en el sistema local."""
    try:
        res = subprocess.run([tailscale_executable(), "ip", "-4"], capture_output=True, text=True, timeout=5, **subprocess_hidden_kwargs())
        if res.returncode == 0 and res.stdout.strip().startswith("100."):
            return res.stdout.strip()
    except Exception:
        pass
    try:
        # Fallback manual buscando interfaces que empiecen con 100.x (puede dar falsos positivos con CGNAT)
        import socket
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip.startswith("100."):
                return ip
    except Exception:
        pass
    return ""

def is_tailscale_ip(value):
    ip = str(value or "").strip()
    if not re.match(r"^100\.(?:\d{1,3}\.){2}\d{1,3}$", ip):
        return False
    return all(0 <= int(part) <= 255 for part in ip.split("."))

def subprocess_hidden_kwargs():
    if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW"):
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}

def office_server_responds(ip, timeout=1.5):
    ip = str(ip or "").strip()
    if not ip:
        return False
    try:
        with url_request.urlopen("http://{}:{}/ping".format(ip, PEER_PORT), timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        return bool(data.get("ok"))
    except Exception:
        return False

def tailscale_status_data():
    try:
        res = subprocess.run(
            [tailscale_executable(), "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            **subprocess_hidden_kwargs(),
        )
        if res.returncode != 0:
            return {}
        data = json.loads(res.stdout or "{}")
    except Exception:
        return {}
    return data

def tailscale_self_user_id(data=None):
    data = data or tailscale_status_data()
    self_info = data.get("Self") or {}
    value = self_info.get("UserID")
    return str(value) if value is not None else ""

def tailscale_peer_ips(same_user_only=True):
    data = tailscale_status_data()
    self_user_id = tailscale_self_user_id(data)

    peers = []
    for peer in (data.get("Peer") or {}).values():
        peer_user_id = peer.get("UserID")
        peer_user_id = str(peer_user_id) if peer_user_id is not None else ""
        if same_user_only and self_user_id and peer_user_id != self_user_id:
            continue
        ips = peer.get("TailscaleIPs") or []
        if not ips:
            continue
        name = peer.get("HostName") or peer.get("DNSName") or ""
        online = bool(peer.get("Online"))
        if not online:
            continue
        peers.append((not online, name.lower(), ips[0]))
    return [ip for _offline, _name, ip in sorted(peers)]

def tailscale_peer_name_for_ip(ip):
    ip = str(ip or "").strip()
    if not ip:
        return ""
    data = tailscale_status_data()
    for peer in (data.get("Peer") or {}).values():
        ips = peer.get("TailscaleIPs") or []
        if ip in ips:
            name = peer.get("HostName") or peer.get("DNSName") or ""
            return name.strip().rstrip(".")
    return ""

def discover_office_ip(preferred="", same_user_only=True):
    preferred = str(preferred or "").strip()
    candidates = tailscale_peer_ips(same_user_only=same_user_only)
    if preferred and preferred in candidates and office_server_responds(preferred, timeout=1.2):
        return preferred
    for ip in candidates:
        if ip == preferred:
            continue
        if office_server_responds(ip, timeout=1.2):
            return ip
    return ""

def windows_device_target(letter):
    if not sys.platform.startswith("win"):
        return "", 2
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.QueryDosDeviceW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    kernel.QueryDosDeviceW.restype = ctypes.c_ulong
    if kernel.QueryDosDeviceW("{}:".format(letter.upper()), buffer, len(buffer)):
        return buffer.value, 0
    return "", ctypes.get_last_error()

def existing_subst_target(letter):
    target, _error = windows_device_target(letter)
    if target.startswith("\\??\\") and re.match(r"^[A-Za-z]:\\", target[4:]):
        return target[4:]
    return ""

def ensure_subst_drive(letter, target):
    if not sys.platform.startswith("win"):
        return False, "subst solo esta disponible en Windows"
    letter = letter.upper()
    if not re.fullmatch(r"[A-Z]", letter):
        return False, "Letra de disco invalida"
    target = str(Path(target).resolve())
    current_subst = existing_subst_target(letter)
    if current_subst:
        if Path(current_subst).resolve() == Path(target).resolve():
            return True, "{}: ya apunta al cache".format(letter)
        return False, "{}: esta ocupado por otra carpeta.".format(letter)
    if windows_drive_in_use(letter):
        return False, "{}: esta ocupado por otro disco o conexion.".format(letter)
    res = subprocess.run(["subst", "{}:".format(letter), target], capture_output=True, text=True, **subprocess_hidden_kwargs())
    if res.returncode != 0:
        return False, (res.stderr or res.stdout or "No se pudo montar {}:".format(letter)).strip()
    return True, "{}: montado en cache local".format(letter)

def windows_drive_in_use(letter):
    """Include disconnected network drives and devices without a readable root."""
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    target, last_error = windows_device_target(letter)
    if target:
        return True
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    # Fail closed if Windows could not determine whether this name exists.
    if last_error not in (2, 3):
        return True
    mask = kernel.GetLogicalDrives()
    return bool(mask & (1 << (ord(letter) - ord("A")))) or Path("{}:\\".format(letter)).exists()

def supports_local_junctions(path):
    if not sys.platform.startswith("win"):
        return False
    import ctypes
    from ctypes import wintypes
    root = str(Path(path).absolute().anchor)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    if kernel.GetDriveTypeW(root) not in (2, 3):  # Local removable/fixed, never SMB.
        return False
    fs_name = ctypes.create_unicode_buffer(32)
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    ok = kernel.GetVolumeInformationW(root, None, 0, None, None, None, fs_name, len(fs_name))
    return bool(ok and fs_name.value.upper() == "NTFS")

def ensure_folder_junction(link, target):
    """Create a new link only; never adopt, merge or delete an existing directory."""
    link, target = Path(link), Path(target).resolve()
    if os.path.lexists(link):
        if (get_windows_attributes(link) or 0) & 0x400 and link.resolve() == target:
            return
        raise RBError("Conflicto: {} ya existe y no pertenece a la cache de Believe. No se modifico.".format(link))
    if not supports_local_junctions(link.parent) or not supports_local_junctions(target):
        raise RBError("Para integrar {} se necesitan carpetas locales NTFS; no se puede hacer sobre una unidad de red.".format(link))
    target.mkdir(parents=True, exist_ok=True)
    import _winapi
    # CPython creates an NTFS junction directly, without interpreting shell text.
    _winapi.CreateJunction(str(target), str(link))
    if link.resolve() != target:
        raise RBError("No se pudo verificar el enlace {}.".format(link))

def clean_browser_relative(value):
    parts = []
    for part in str(value).replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if not parts:
                raise ValueError("La ruta sale del disco autorizado.")
            parts.pop()
        elif any(char in part for char in ':<>"|?*\x00'):
            raise ValueError("La ruta contiene caracteres no validos.")
        elif part != part.rstrip(" .") or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I):
            raise ValueError("La ruta contiene un nombre reservado de Windows.")
        else:
            parts.append(part)
    return "/".join(parts)

def path_under_prefix(value, prefix):
    value = str(value).replace("\\", "/").rstrip("/")
    prefix = str(prefix).replace("\\", "/").rstrip("/")
    if not prefix:
        return None
    if value.lower() == prefix.lower():
        return ""
    if value.lower().startswith(prefix.lower() + "/"):
        return value[len(prefix) + 1:]
    return None

def resolve_browser_address(store, roots, value, current_root, current_path=""):
    """Remote aliases are accepted only inside the app, never by Explorer IPC."""
    value = str(value).strip().strip('"')
    if not value:
        raise ValueError("Escribe una ruta de carpeta.")
    # Test full cache and UNC prefixes before drive letters (cache often is C:).
    prefixes = []
    for root in roots:
        prefixes.append((str(store.root_dir(root)), root))
        configured = str(root.get("path") or "")
        if configured and ntpath.isabs(configured):
            prefixes.append((configured, root))
        for alias in DRIVE_ALIASES.get(root_drive_letter(root), []):
            if alias.startswith(("\\\\", "//")):
                prefixes.append((alias, root))
    for prefix, root in sorted(prefixes, key=lambda pair: len(pair[0]), reverse=True):
        relative = path_under_prefix(value, prefix)
        if relative is not None:
            return root, clean_browser_relative(relative)
    drive, tail = ntpath.splitdrive(value)
    if drive:
        letter = drive.rstrip(":").upper()
        # A real local assignment has priority over a logical server alias.
        for root in roots:
            if store.local_drives.get(root["id"]) == letter:
                return root, clean_browser_relative(tail)
        for root in roots:
            if root_drive_letter(root) == letter:
                return root, clean_browser_relative(tail)
        raise ValueError("Esa ruta no corresponde a un disco de Remote Believe.")
    if not current_root:
        raise ValueError("Selecciona un disco primero.")
    relative = value if value.startswith(("/", "\\")) else join_remote(current_path, value)
    return current_root, clean_browser_relative(relative)

def format_size(value):
    if value is None: return ""
    v = float(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    u = 0
    while v >= 1024 and u < len(units) - 1:
        v /= 1024.0
        u += 1
    dec = 0 if v >= 10 or u == 0 else 1
    return "{:.{}f} {}".format(v, dec, units[u])

def safe_component(name):
    original = str(name or "")
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", original).rstrip(" .")
    reserved = {"CON","PRN","AUX","NUL","COM1","LPT1"}
    if not cleaned or cleaned.upper() in reserved: cleaned = "_"
    return cleaned

def join_remote(base, name): return "/".join(p for p in [base, name] if p)
def parent_remote(path):
    parts = [p for p in path.split("/") if p]
    if parts: parts.pop()
    return "/".join(parts)
def normalize_remote(value): return str(value or "").replace("\\", "/").strip().lstrip("/")

def remote_segments(value):
    return [part.lower() for part in normalize_remote(value).split("/") if part]

def first_remote_segment(value):
    parts = remote_segments(value)
    return parts[0] if parts else ""

def has_shot_upload_context(segments):
    return any(
        part == "shots"
        or part.endswith("_shots")
        or part.endswith("-shots")
        or part.startswith("shot_")
        for part in segments
    )

def is_render_output_upload(root, remote_path, local_path):
    if root_drive_letter(root) != "X":
        return False
    local_path = Path(local_path)
    if local_path.suffix.lower() not in RENDER_UPLOAD_EXTS:
        return False
    segment_list = remote_segments(remote_path)
    segments = set(segment_list)
    if segments & UPLOAD_BLOCKED_SEGMENTS:
        return False
    return has_shot_upload_context(segment_list) and bool(segments & RENDER_UPLOAD_SEGMENTS)

def is_rig_root(root):
    return root_drive_letter(root) == "R" or "rig" in str(root.get("id", "")).lower()

def upload_policy(root, remote_path, local_path, reason="changed"):
    rp = normalize_remote_path(remote_path)
    local_path = Path(local_path)
    letter = root_drive_letter(root)
    ext = local_path.suffix.lower()
    segment_list = remote_segments(rp)
    segments = set(segment_list)
    manual = str(reason or "").lower() == "manual"

    if ext in UPLOAD_BLOCKED_EXTS:
        return False, "Bloqueado: archivo temporal o de bloqueo."
    if is_rig_root(root):
        if not rp:
            return False, "Bloqueado: ruta R invalida."
        if segments & R_UPLOAD_BLOCKED_SEGMENTS:
            return False, "Bloqueado: carpeta sensible o cache/pipeline."
        if manual:
            return True, ""
        if ext and ext not in AUTO_UPLOAD_ALLOWED_EXTS:
            return False, "Bloqueado: extension no permitida para auto-subida."
        return True, ""
    if segments & UPLOAD_BLOCKED_SEGMENTS:
        return False, "Bloqueado: carpeta sensible o cache/pipeline."
    if manual:
        return True, ""
    if ext and ext not in AUTO_UPLOAD_ALLOWED_EXTS:
        return False, "Bloqueado: extension no permitida para auto-subida."
    if not has_shot_upload_context(segment_list):
        return False, "Bloqueado: auto-subida solo en carpetas de shots."
    if not (segments & AUTO_UPLOAD_ALLOWED_SEGMENTS):
        return False, "Bloqueado: fuera de carpetas de trabajo/shot."
    return True, ""

def root_access_record(access_data, root_id):
    requested = str(root_id or "").strip().rstrip(":").lower()
    for root in (access_data or {}).get("roots", []):
        if root.get("id") == root_id:
            return root
    for root in (access_data or {}).get("roots", []):
        if requested and requested in root_access_aliases(root):
            return root
    return None

def sync_root_record(sync_data, root_id):
    requested = str(root_id or "").strip().rstrip(":").lower()
    for root in (sync_data or {}).get("roots", []):
        if root.get("id") == root_id:
            return root
    for root in (sync_data or {}).get("roots", []):
        if requested and requested in root_access_aliases(root):
            return root
    return None

def root_access_aliases(root):
    aliases = set()
    for value in (root.get("id"), root.get("name"), root.get("driveLetter"), root_drive_letter(root)):
        text = str(value or "").strip().rstrip(":")
        if text:
            aliases.add(text.lower())
    letter = root_drive_letter(root)
    if letter == "R":
        aliases.update({"r", "rig", "rigs"})
    elif letter == "X":
        aliases.update({"x", "proyectos", "proyectos-3d", "projects"})
    return aliases

def project_access_for_root(project_access, root):
    if not isinstance(project_access, dict):
        return None
    for key in root_access_aliases(root):
        if key in project_access:
            return project_access.get(key)
    lower_map = {str(key).lower(): value for key, value in project_access.items()}
    for key in root_access_aliases(root):
        if key in lower_map:
            return lower_map.get(key)
    return None

def normalized_access_list(value):
    if value == "*":
        return "*"
    if not isinstance(value, list):
        return []
    clean = []
    seen = set()
    for item in value:
        text = normalize_remote_path(item).strip("/")
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        clean.append(text)
    return clean

def remote_path_allowed_by_access(access, remote_path):
    if access == "*":
        return True
    if not isinstance(access, list):
        return False
    rp = normalize_remote_path(remote_path).strip("/")
    if not rp:
        return True
    rp_low = rp.casefold()
    first = first_remote_segment(rp).casefold()
    for value in access:
        allowed = normalize_remote_path(value).strip("/")
        if not allowed:
            continue
        allowed_low = allowed.casefold()
        allowed_first = first_remote_segment(allowed).casefold()
        if first == allowed_first or rp_low == allowed_low or rp_low.startswith(allowed_low + "/"):
            return True
    return False

def can_access_remote_path(access_data, root_id, remote_path):
    if "syncremote.json" in remote_segments(remote_path) and not is_admin_role((access_data or {}).get("role")):
        return False
    root = root_access_record(access_data, root_id)
    if not root:
        return False
    access = root.get("access")
    if access == "*" or not root.get("projectScoped"):
        return True
    return remote_path_allowed_by_access(access, remote_path)

def root_drive_letter(root):
    explicit = str(root.get("driveLetter") or "").strip().rstrip(":").upper()
    if re.match(r"^[A-Z]$", explicit):
        return explicit
    alias = str(root.get("archiveAlias") or "").strip()
    m = re.match(r"^([A-Za-z]):", alias)
    if m: return m.group(1).upper()
    name = root.get("name") or root.get("id") or ""
    m = re.match(r"^([A-Za-z]):", name)
    if m: return m.group(1).upper()
    path = str(root.get("path") or "").lower()
    if "proyectos_3d" in path or "proyectos" in path:
        return "X"
    if re.search(r"(?:^|[\\/])rig(?:[\\/]|$)", path) or path.endswith("\\rig") or path.endswith("/rig"):
        return "R"
    rid = (root.get("id") or "").lower()
    if "rig" in rid: return "R"
    if "proyecto" in rid or "project" in rid or "3d" in rid: return "X"
    return ""

def root_display_label(root):
    letter = (root.get("driveLetter") or root_drive_letter(root) or "").strip().rstrip(":").upper()
    name = str(root.get("name") or root.get("id") or "").strip()
    compact_name = name.rstrip(":").upper()
    if letter and compact_name == letter:
        return "{}:".format(letter)
    if letter:
        name = re.sub(r"^{}:\s*".format(re.escape(letter)), "", name, flags=re.I).strip()
    if letter and name:
        return "{}: {}".format(letter, name)
    if letter:
        return "{}:".format(letter)
    return name or "Disco"

def is_ignored(path):
    name = path.name.lower()
    if name.endswith((".downloading", ".uploading", ".tmp", ".part", ".swp")): return True
    if name.startswith("~$") or name.startswith("."): return True
    if name in {"thumbs.db", "desktop.ini"}: return True
    return False

def local_snapshot(path):
    s = path.stat()
    return (s.st_mtime_ns, s.st_size)

def local_snapshot_record(path):
    mtime_ns, size = local_snapshot(Path(path))
    return {"localMtimeNs": mtime_ns, "localSize": size}

def format_remote_modified_at(timestamp):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(float(timestamp)))
    except Exception:
        return ""

def parse_remote_modified_at(value):
    text = str(value or "").strip()
    if not text:
        return None
    text = text.rstrip("Z")
    if "." in text:
        text = text.split(".", 1)[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return float(calendar.timegm(time.strptime(text[:19], fmt)))
        except Exception:
            pass
    return None

def apply_remote_mtime(local_path, item):
    timestamp = parse_remote_modified_at((item or {}).get("modifiedAt"))
    if timestamp is None:
        return False
    try:
        os.utime(local_path, (timestamp, timestamp))
        return True
    except OSError:
        return False

def local_matches_remote_item(local_path, item):
    local_path = Path(local_path)
    if not local_path.exists() or not local_path.is_file():
        return False
    try:
        stat = local_path.stat()
    except OSError:
        return False
    size = (item or {}).get("size")
    if size is not None:
        try:
            if stat.st_size != int(size):
                return False
        except (TypeError, ValueError):
            return False
    remote_mtime = parse_remote_modified_at((item or {}).get("modifiedAt"))
    if remote_mtime is not None and abs(stat.st_mtime - remote_mtime) > REMOTE_MTIME_TOLERANCE_S:
        return False
    return True

def has_dependency_extension(value):
    clean = str(value or "").split("?", 1)[0]
    clean = clean.replace("<UDIM>", "1001").replace("<udim>", "1001")
    clean = clean.replace("<f>", "1001").replace("<F>", "1001")
    clean = clean.replace("<frame>", "1001").replace("<FRAME>", "1001")
    clean = re.sub(r"#+", "1001", clean)
    clean = re.sub(r"%0?\d*d", "1001", clean)
    clean = re.sub(r"\$F\d*", "1001", clean, flags=re.I)
    return Path(clean).suffix.lower() in DEP_EXTS

def is_maya_ascii_name(name):
    return Path(str(name or "")).suffix.lower() in MAYA_EXTS

def normalize_remote_path(value):
    return normalize_remote(value)

def pattern_to_regex(name):
    result = []
    i = 0
    while i < len(name):
        chunk = name[i:]
        low = chunk.lower()
        if low.startswith("<udim>"):
            result.append(r"\d{4}")
            i += 6
            continue
        if low.startswith("<frame>"):
            result.append(r"\d+")
            i += 7
            continue
        if low.startswith("<f>"):
            result.append(r"\d+")
            i += 3
            continue
        m = re.match(r"%0?(\d*)d", chunk, flags=re.I)
        if m:
            width = m.group(1)
            result.append(r"\d{{{}}}".format(width) if width else r"\d+")
            i += len(m.group(0))
            continue
        m = re.match(r"\$F(\d*)", chunk, flags=re.I)
        if m:
            width = m.group(1)
            result.append(r"\d{{{}}}".format(width) if width else r"\d+")
            i += len(m.group(0))
            continue
        if name[i] == "#":
            j = i
            while j < len(name) and name[j] == "#":
                j += 1
            result.append(r"\d{{{}}}".format(j - i))
            i = j
            continue
        result.append(re.escape(name[i]))
        i += 1
    return re.compile("^{}$".format("".join(result)), re.IGNORECASE)

def is_pattern_path(remote_path):
    low = str(remote_path or "").lower()
    return (
        "#" in low
        or "<udim>" in low
        or "<f>" in low
        or "<frame>" in low
        or re.search(r"%0?\d*d", low)
        or re.search(r"\$f\d*", low)
    )

def is_image_dependency(value):
    clean = str(value or "").split("?", 1)[0]
    clean = clean.replace("<UDIM>", "1001").replace("<udim>", "1001")
    clean = clean.replace("<f>", "1001").replace("<F>", "1001")
    clean = clean.replace("<frame>", "1001").replace("<FRAME>", "1001")
    clean = re.sub(r"#+", "1001", clean)
    clean = re.sub(r"%0?\d*d", "1001", clean)
    clean = re.sub(r"\$F\d*", "1001", clean, flags=re.I)
    return Path(clean).suffix.lower() in IMAGE_SEQUENCE_EXTS

def numbered_sequence_pattern(value, explicit_sequence=False):
    normalized = str(value or "").strip().replace("\\\\", "/").replace("\\", "/")
    if not normalized or is_pattern_path(normalized) or not is_image_dependency(normalized):
        return None
    clean = normalized.split("?", 1)[0]
    match = re.search(r"^(.*?)([._-]?)(\d{3,8})(\.[^./\\]+)$", clean)
    if not match:
        return None
    prefix, sep, digits, ext = match.groups()
    if not explicit_sequence and len(digits) < 4:
        return None
    return "{}{}{}{}".format(prefix, sep, "#" * len(digits), ext)

def target_to_root_path(store, roots, target_path):
    value = str(target_path or "").strip().strip('"')
    if not value:
        return None, "", None
    target = Path(value)
    drive = ntpath.splitdrive(value)[0].rstrip(":").upper()
    for root in roots:
        letter = store.local_drive(root)
        if letter and drive == letter:
            rp = clean_browser_relative(ntpath.splitdrive(value)[1])
            return root, rp, store.local_path(root, rp)

    for root in roots:
        try:
            rel = cache_relative_path(store.root_dir(root), target)
            rp = normalize_remote_path("/".join(rel.parts))
            return root, rp, store.local_path(root, rp)
        except Exception:
            continue
    return None, "", None

def show_desktop_message(title, message, kind="info"):
    try:
        root = tk.Tk()
        root.withdraw()
        if kind == "error":
            messagebox.showerror(title, message)
        elif kind == "warning":
            messagebox.showwarning(title, message)
        else:
            messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        print(message)

def send_explorer_command(action, target_path):
    params = parse.urlencode({"action": action, "path": str(target_path or "")})
    url = "http://127.0.0.1:{}/command?{}".format(COMMAND_PORT, params)
    try:
        with url_request.urlopen(url, timeout=3) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw or "{}")
        if not data.get("ok"):
            show_desktop_message(APP_NAME, data.get("error") or "No se pudo enviar la orden.", "warning")
            return 1
        return 0
    except Exception:
        show_desktop_message(
            APP_NAME,
            "Remote Believe debe estar abierto o en el area de notificaciones para usar esta opcion.",
            "warning",
        )
        return 1

# ─── API del Coordinador Web ──────────────────────────────────────────────────


import hmac
import secrets

def get_syncremote_path():
    return Path("R:/syncremote.json") if sys.platform.startswith("win") else Path("/tmp/syncremote.json")

def default_syncremote_data():
    return {'users': {}, 'roots': [{'id': 'rig', 'name': 'R:', 'driveLetter': 'R', 'projectScoped': True, 'groups': []}, {'id': 'proyectos', 'name': 'X:', 'driveLetter': 'X', 'projectScoped': True, 'groups': []}]}

def normalize_syncremote(data):
    data = data if isinstance(data, dict) else {}
    defaults = default_syncremote_data()
    users = data.setdefault("users", {})
    if not isinstance(users, dict):
        data["users"] = defaults["users"]
        users = data["users"]
    if not users:
        users.update(defaults["users"])
    for username, info in list(users.items()):
        if not isinstance(info, dict):
            users[username] = info = {}
        info.setdefault("password", "")
        info.setdefault("role", "user")
        info.setdefault("projectAccess", {})
        info.setdefault("directUpload", False)
        info.setdefault("groups", [])

    roots = data.setdefault("roots", [])
    if not isinstance(roots, list) or not roots:
        data["roots"] = defaults["roots"]
        roots = data["roots"]

    normalized_roots = []
    existing_ids = set()
    existing_letters = set()
    for root in roots:
        if not isinstance(root, dict):
            continue
        root.setdefault("id", safe_component(root.get("name") or "root").lower())
        root.setdefault("name", root.get("driveLetter", root["id"]))
        letter = root.get("driveLetter") or root_drive_letter(root)
        if letter:
            root["driveLetter"] = letter.upper()
        if root.get("id") in {"rig", "proyectos"} or root.get("driveLetter") in {"R", "X"}:
            root["projectScoped"] = True
        else:
            root["projectScoped"] = bool(root.get("projectScoped", True))
        root.setdefault("groups", [])
        normalized_roots.append(root)
        existing_ids.add(str(root.get("id", "")))
        if root.get("driveLetter"):
            existing_letters.add(root["driveLetter"])

    for default_root in defaults["roots"]:
        default_letter = root_drive_letter(default_root)
        if default_root["id"] not in existing_ids and default_letter not in existing_letters:
            root = dict(default_root)
            normalized_roots.append(root)
            existing_ids.add(root["id"])
            if default_letter:
                existing_letters.add(default_letter)

    deduped = []
    seen_keys = set()
    for root in normalized_roots:
        letter = root.get("driveLetter") or root_drive_letter(root)
        key = ("letter", letter) if letter else ("id", root.get("id"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(root)
    data["roots"] = deduped
    return data

def load_syncremote():
    path = get_syncremote_path()
    if not path.exists():
        default_data = default_syncremote_data()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(default_data, indent=2), encoding="utf-8")
        return default_data
    return normalize_syncremote(json.loads(path.read_text(encoding="utf-8")))

def save_syncremote(data):
    path = get_syncremote_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_syncremote(data)
    if not any(is_admin_role(info.get("role")) for info in normalized.get("users", {}).values()):
        raise ValueError("Debe existir al menos un superuser o admin")
    path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")

def is_admin_role(role):
    return str(role or "").lower() in {"admin", "superuser"}

def build_access(sync_data, username):
    sync_data = normalize_syncremote(sync_data)
    users = sync_data.get("users", {})
    user_data = users.get(username)
    if not user_data:
        return None
    role = user_data.get("role", "user")
    project_access = user_data.get("projectAccess", {})
    roots = []
    for root in sync_data.get("roots", []):
        root_copy = dict(root)
        if is_admin_role(role):
            root_copy["access"] = "*"
        elif isinstance(project_access, dict):
            root_copy["access"] = normalized_access_list(project_access_for_root(project_access, root_copy))
        elif isinstance(project_access, list):
            # Formato antiguo: solo indicaba roots visibles. En la version por
            # carpetas no debe convertirse en acceso total automaticamente.
            root_copy["access"] = []
        else:
            root_copy["access"] = []
        roots.append(root_copy)
    return {
        "username": username,
        "role": role,
        "projectAccess": project_access,
        "directUpload": bool(user_data.get("directUpload", False)),
        "roots": roots,
    }

def access_is_admin(access):
    return is_admin_role((access or {}).get("role"))

def root_base_path(root):
    letter = str(root.get("driveLetter") or root_drive_letter(root) or "").strip().rstrip(":").upper()
    configured = root.get("path")
    if configured:
        return Path(configured)
    if letter:
        return Path("{}:\\".format(letter))
    return None

def list_access_folders_local(root):
    base = root_base_path(root)
    if not base or not base.exists():
        return []
    ignored = {"System Volume Information", "$RECYCLE.BIN"}
    try:
        folders = [p.name for p in base.iterdir() if p.is_dir() and p.name not in ignored and not p.name.startswith("~$")]
        return sorted(folders, key=str.lower)
    except Exception:
        return []

def user_access_summary(info, roots):
    if is_admin_role(info.get("role")):
        return "Todos los discos"
    access = info.get("projectAccess", {})
    if not isinstance(access, dict):
        return "Sin accesos"
    parts = []
    for root in roots:
        rid = root.get("id")
        letter = root.get("driveLetter") or root_drive_letter(root) or rid
        value = project_access_for_root(access, root)
        if value == "*":
            parts.append("{}: todo".format(letter))
        elif isinstance(value, list) and value:
            parts.append("{}: {}".format(letter, len(normalized_access_list(value))))
    return ", ".join(parts) if parts else "Sin accesos"

UPLOAD_COMMIT_LOCK = threading.RLock()
SERVER_TOKENS = {}
SERVER_SECRET = secrets.token_hex(32)

# ─── Cliente Archivos (Conecta a Oficina) ───

class OfficeFileApi:
    def __init__(self, office_ip, token=None):
        self.office_url = "http://{}:{}".format(office_ip, PEER_PORT)
        self.token = token
        self.transfers = TransferControl()
        # Office is reached directly over the tailnet, never through an Internet proxy.
        self.opener = url_request.build_opener(url_request.ProxyHandler({}), ControlledHTTPHandler(self.transfers))
        self._login_secret = None
        self._login_lock = threading.RLock()
        self._session_closed = threading.Event()

    def _ensure_session_open(self):
        if self._session_closed.is_set():
            raise RBError("Sesion cerrada. Inicia sesion en Remote Believe para continuar.")

    def close_session(self):
        self._session_closed.set()
        self.transfers.cancel_all()
        with self._login_lock:
            token, self.token, self._login_secret = self.token, None, None
        if token:
            req = url_request.Request(self.office_url + "/api/logout", data=b"{}", method="POST",
                                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "RemoteBelieveMesh/" + APP_VERSION})
            try:
                with url_request.build_opener(url_request.ProxyHandler({})).open(req, timeout=5):
                    pass
            except Exception:
                pass  # Local logout also works offline or with an older Office server.

    def login(self, username, password):
        self._ensure_session_open()
        payload = {"username": username, "password": password}
        data = json.dumps(payload).encode("utf-8")
        h = {"Content-Type": "application/json", "User-Agent": "RemoteBelieveMesh/" + APP_VERSION}
        req = url_request.Request(self.office_url + "/api/login", data=data, method="POST", headers=h)
        try:
            with self.opener.open(req, timeout=10) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                with self._login_lock:
                    self._ensure_session_open()
                    self.token = res.get("token")
                    self._login_secret = (username, password)
                return res
        except error.HTTPError as exc:
            raise OfficeResponseError(f"Login fallido: {exc.read().decode()}", exc.code) from exc
        except error.URLError as exc:
            raise OfficeNetworkError("Error conectando a la oficina: {}".format(exc.reason)) from exc
        except Exception as exc:
            raise RBError(f"Error conectando a la oficina: {exc}")

    def get_access(self):
        with self._req("GET", "/api/access") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _req(self, method, path, params=None, data=None, headers=None, timeout=30, _retry=True):
        self._ensure_session_open()
        operation = getattr(self.transfers.context, "operation", None)
        if operation:
            operation.check()
        url = "{}{}".format(self.office_url, path)
        if params: url = "{}?{}".format(url, parse.urlencode(params))
        h = {"Authorization": "Bearer {}".format(self.token), "User-Agent": "RemoteBelieveMesh/{}".format(APP_VERSION), **(headers or {})}
        req = url_request.Request(url, data=data, method=method, headers=h)
        try:
            return self.opener.open(req, timeout=timeout)
        except error.HTTPError as exc:
            if exc.code in (401, 403) and _retry and self._login_secret:
                with self._login_lock:
                    self._ensure_session_open()
                    self.login(*self._login_secret)
                return self._req(method, path, params, data, headers, timeout, _retry=False)
            msg = "Error Oficina: {}".format(exc.code)
            try:
                raw = exc.read().decode("utf-8")
                msg = json.loads(raw).get("error") or msg
            except Exception:
                pass
            raise OfficeResponseError(msg, exc.code) from exc
        except error.URLError as exc:
            raise OfficeNetworkError("No se puede conectar a la Oficina ({}): {}".format(self.office_url, exc.reason)) from exc

    def list_folder(self, root_id, rp):
        with self._req("GET", "/api/list", {"root": root_id, "path": rp}) as resp:
            raw = resp.read().decode("utf-8")
        try:
            return json.loads(raw)
        except Exception as e:
            raise RBError(f"Oficina devolvio algo que no es JSON: '{raw[:100]}'")

    def remote_item(self, root_id, rp, with_hash=True):
        with self._req("GET", "/api/exists", {"root": root_id, "path": rp, "hash": "1" if with_hash else "0"}) as response:
            return json.loads(response.read().decode("utf-8")).get("item")

    def download_file(self, root_id, rp, local_path, progress_cb):
        with self.transfers.operation("download", root_id, rp):
            return self._download_payload(root_id, rp, local_path, progress_cb)

    def _download_payload(self, root_id, rp, local_path, progress_cb):
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = local_path.with_suffix(local_path.suffix + ".downloading")
        tmp.unlink(missing_ok=True)
        last_error = None

        def content_range_total(value):
            match = re.search(r"/(\d+)$", str(value or ""))
            return int(match.group(1)) if match else 0

        for attempt in range(1, DOWNLOAD_RETRIES + 1):
            resume_at = tmp.stat().st_size if tmp.exists() else 0
            headers = {"Range": "bytes={}-".format(resume_at)} if resume_at > 0 else {}
            try:
                with self._req("GET", "/api/download", {"root": root_id, "path": rp}, headers=headers, timeout=DOWNLOAD_TIMEOUT_S) as resp:
                    status = getattr(resp, "status", resp.getcode())
                    content_length = int(resp.headers.get("Content-Length") or 0)
                    total = content_length
                    received = 0
                    mode = "wb"
                    if status == 206 and resume_at > 0:
                        total = content_range_total(resp.headers.get("Content-Range")) or (resume_at + content_length)
                        received = resume_at
                        mode = "ab"
                    elif resume_at > 0:
                        tmp.unlink(missing_ok=True)

                    with tmp.open(mode) as out:
                        while True:
                            self._ensure_session_open()
                            self.transfers.context.operation.check()
                            chunk = resp.read(DOWNLOAD_CHUNK)
                            self.transfers.context.operation.check()
                            if not chunk:
                                break
                            out.write(chunk)
                            received += len(chunk)
                            progress_cb(received, total)

                final_size = tmp.stat().st_size if tmp.exists() else 0
                if total and final_size != total:
                    raise RBError("Descarga incompleta: {} de {} bytes.".format(final_size, total))
                tmp.replace(local_path)
                return local_path
            except TransferStopped:
                raise
            except Exception as exc:
                operation = getattr(self.transfers.context, "operation", None)
                if operation:
                    operation.check()
                self._ensure_session_open()
                last_error = exc
                if attempt >= DOWNLOAD_RETRIES:
                    break
                time.sleep(min(10, 1.5 * attempt))

        tmp.unlink(missing_ok=True)
        raise last_error or RBError("No se pudo descargar el archivo.")

    def upload_chunk(self, root_id, folder, filename, uid, idx, total, data):
        params = {"root": root_id, "path": folder, "name": filename, "uploadId": uid, "index": str(idx), "total": str(total)}
        h = {"Content-Type": "application/octet-stream"}
        with self._req("POST", "/api/upload-chunk", params=params, data=data, headers=h, timeout=PEER_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def asset_updates(self, root_id, project):
        try:
            with self._req("GET", "/api/asset-updates", {"root": root_id, "path": project}, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))["items"]
        except OfficeResponseError as exc:
            if exc.status == 404:
                raise RBError("Actualiza Remote Believe en Oficina para consultar los assets.") from exc
            raise

    def complete_upload(self, root_id, folder, filename, uid, total, size, mtime, overwrite=False, expected_hash=None, digest=None):
        payload = {"root": root_id, "path": folder, "name": filename, "uploadId": uid, "total": total, "size": size, "mtime": mtime, "overwrite": overwrite, "expectedHash": expected_hash, "sha256": digest}
        data = json.dumps(payload).encode("utf-8")
        h = {"Content-Type": "application/json", "User-Agent": "RemoteBelieveMesh/" + APP_VERSION}
        with self._req("POST", "/api/upload-complete", data=data, headers=h, timeout=PEER_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def upload_file(self, root_id, folder, local_path, progress_cb, overwrite=False, yield_cb=None, expected_hash=None, pending_cb=None):
        rp = join_remote(folder, Path(local_path).name)
        with self.transfers.operation("upload", root_id, rp) as operation:
            if pending_cb and not pending_cb():
                raise TransferStopped("La subida fue cancelada mientras se comprobaba el archivo.")
            operation.total = Path(local_path).stat().st_size
            remote = self.remote_item(root_id, rp)
            digest = file_hash(local_path, operation)
            if remote and remote.get("sha256") == digest:
                return {"ok": True, "status": "already-current"}
            if expected_hash is not None and (remote or {}).get("sha256", "") != expected_hash:
                raise RBError("Oficina cambio este archivo; se conserva la copia local y no se reemplaza el servidor.")
            if remote and (remote.get("type") == "directory" or not remote.get("sha256")):
                raise RBError("No se pudo comparar el archivo de Oficina; no se reemplaza.")
            def progress(sent, total):
                operation.done, operation.total = sent, total
                operation.check()
                progress_cb(sent, total)
            return self._upload_payload(root_id, folder, local_path, progress, overwrite=bool(remote),
                yield_cb=yield_cb, expected_hash=remote.get("sha256") if remote else None, digest=digest)

    def _upload_payload(self, root_id, folder, local_path, progress_cb, overwrite=False, yield_cb=None, expected_hash=None, digest=None):
        local_path = Path(local_path)
        stat = local_path.stat()
        size = stat.st_size
        if size <= 0:
            raise RBError("Subida bloqueada: Remote Believe no sube archivos de 0 bytes.")
        total_chunks = max(1, math.ceil(size / UPLOAD_CHUNK))
        uid = uuid.uuid4().hex
        sent = 0
        with local_path.open("rb") as fh:
            for idx in range(total_chunks):
                self._ensure_session_open()
                if yield_cb:
                    yield_cb()
                self.transfers.context.operation.check()
                data = fh.read(UPLOAD_CHUNK)
                self.upload_chunk(root_id, folder, local_path.name, uid, idx, total_chunks, data)
                sent += len(data)
                progress_cb(sent, size)
                if yield_cb:
                    yield_cb()
        self._ensure_session_open()
        self.transfers.context.operation.check()
        current = local_path.stat()
        if current.st_size != stat.st_size or current.st_mtime_ns != stat.st_mtime_ns:
            raise RBError("El archivo cambio mientras se subia. Se reintentara cuando termine de guardarse.")
        return self.complete_upload(root_id, folder, local_path.name, uid, total_chunks, size, stat.st_mtime, overwrite, expected_hash, digest)

    def get_admin_syncremote(self):
        with self._req("GET", "/api/admin/syncremote") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def save_admin_syncremote(self, data):
        payload = json.dumps(data).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        with self._req("POST", "/api/admin/syncremote", data=payload, headers=headers) as resp:
            return json.loads(resp.read().decode("utf-8"))


# ─── Servidor Local (Modo Oficina) ───────────────────────────────────────────

class OfficeServerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        try:
            parsed = parse.urlparse(self.path)
            if parsed.path == "/ping":
                return self._json(200, {"ok": True, "version": APP_VERSION})

            if not self._check_auth(): return

            params = dict(parse.parse_qsl(parsed.query))
            root_id = params.get("root", "")
            rp = params.get("path", "")

            if parsed.path == "/api/admin/syncremote":
                return self._handle_admin_get()

            if parsed.path == "/api/access":
                return self._json(200, getattr(self, "auth_access", {}))
            if parsed.path == "/api/asset-updates":
                project = clean_browser_relative(rp)
                if not project or "/" in project:
                    return self._json(400, {"error": "Selecciona un proyecto autorizado."})
                local = self._resolve_local_path(root_id, project)
                if not local.is_dir():
                    return self._json(404, {"error": "Proyecto no disponible en Oficina."})
                items = OFFICE_ASSETS.get(local, root_id, format_remote_modified_at)
                return self._json(200, {"items": [item for item in items if can_access_remote_path(self.auth_access, root_id, item["path"])]})
            if parsed.path == "/api/exists":
                local = self._resolve_local_path(root_id, rp)
                item = None
                if local.exists():
                    metadata = local.stat()
                    item = {"name": local.name, "type": "directory" if local.is_dir() else "file",
                            "size": metadata.st_size, "modifiedAt": format_remote_modified_at(metadata.st_mtime)}
                    if local.is_file() and params.get("hash") == "1":
                        item["sha256"] = file_hash(local)
                return self._json(200, {"ok": True, "item": item})
            if parsed.path == "/api/list":
                return self._handle_list(root_id, rp)
            if parsed.path == "/api/download":
                return self._handle_download(root_id, rp, params)

            self._json(404, {"error": "Not found"})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_POST(self):
        try:
            parsed = parse.urlparse(self.path)

            if parsed.path == "/api/login":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                return self._handle_login(body)

            if not self._check_auth(): return

            if parsed.path == "/api/logout":
                SERVER_TOKENS.pop(self.headers.get("Authorization", "")[7:], None)
                return self._json(200, {"ok": True})

            if parsed.path == "/api/upload-chunk":
                params = dict(parse.parse_qsl(parsed.query))
                length = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(length)
                return self._handle_upload_chunk(params, data)

            if parsed.path == "/api/upload-complete":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                return self._handle_upload_complete(body)

            if parsed.path == "/api/admin/syncremote":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                return self._handle_admin_save(body)

            self._json(404, {"error": "Not found"})
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def _client_current(self):
        match = re.fullmatch(r"RemoteBelieveMesh/(.+)", self.headers.get("User-Agent", ""))
        try:
            current = match and release_version(match.group(1)) >= release_version(APP_VERSION)
        except UpdateError:
            current = False
        if not current:
            self._json(426, {"error": "Actualiza Remote Believe antes de continuar: " + DOWNLOAD_PAGE,
                             "version": APP_VERSION, "updateRequired": True})
        return bool(current)

    def _handle_login(self, body):
        if not self._client_current():
            return
        un = body.get("username")
        pw = body.get("password")
        sync_data = load_syncremote()
        users = sync_data.get("users", {})
        if un not in users or users[un].get("password") != pw:
            return self._json(401, {"error": "Credenciales invalidas"})

        token_data = "{}:{}:{}".format(un, int(time.time()), secrets.token_hex(16))
        signature = hmac.new(SERVER_SECRET.encode(), token_data.encode(), hashlib.sha256).hexdigest()
        token = "{}.{}".format(token_data, signature)
        access = build_access(sync_data, un)
        SERVER_TOKENS[token] = access
        return self._json(200, {"token": token, "access": access})

    def _check_auth(self):
        if not self._client_current():
            return False
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._json(401, {"error": "Token requerido"})
            return False
        token = auth[7:]
        if token not in SERVER_TOKENS:
            self._json(403, {"error": "Token invalido"})
            return False
        cached_access = SERVER_TOKENS[token]
        username = cached_access.get("username") or token.split(":", 1)[0]
        fresh_access = build_access(load_syncremote(), username)
        if not fresh_access:
            self._json(403, {"error": "Usuario sin permisos vigentes"})
            return False
        SERVER_TOKENS[token] = fresh_access
        self.auth_access = fresh_access
        return True

    def _resolve_local_path(self, root_id, rp):
        clean_rp = clean_browser_relative(rp)
        if not can_access_remote_path(getattr(self, "auth_access", None), root_id, clean_rp):
            raise PermissionError("Sin permiso para esta ruta")
        sync_data = load_syncremote()
        root = sync_root_record(sync_data, root_id)
        if not root:
            raise FileNotFoundError("Root no encontrado")
        base = root_base_path(root)
        if not base:
            raise FileNotFoundError("Root sin ruta local")
        parts = []
        for part in clean_rp.split("/"):
            if not part or part == ".":
                continue
            if part == "..":
                raise PermissionError("Ruta invalida")
            parts.append(part)
        cur = base
        for p in parts: cur = cur / p
        try:
            cur.resolve().relative_to(base.resolve())
        except ValueError:
            raise PermissionError("La ruta sale de la carpeta autorizada")
        return cur

    def _handle_admin_get(self):
        if not access_is_admin(getattr(self, "auth_access", None)):
            return self._json(403, {"error": "Solo superuser puede administrar"})
        self._json(200, load_syncremote())

    def _handle_admin_save(self, body):
        if not access_is_admin(getattr(self, "auth_access", None)):
            return self._json(403, {"error": "Solo superuser puede administrar"})
        save_syncremote(body)
        self._json(200, {"ok": True})

    def _handle_list(self, root_id, rp):
        try:
            local = self._resolve_local_path(root_id, rp)
            if not local.is_dir():
                return self._json(404, {"error": "La ruta no existe o no es una carpeta"})
            items = []
            if local.exists() and local.is_dir():
                for p in local.iterdir():
                    if p.name.startswith("~$") or p.name in {"thumbs.db", "desktop.ini"}: continue
                    item_rp = join_remote(rp, p.name)
                    if not can_access_remote_path(getattr(self, "auth_access", None), root_id, item_rp): continue
                    stat = p.stat()
                    items.append({
                        "name": p.name,
                        "type": "directory" if p.is_dir() else "file",
                        "size": stat.st_size if p.is_file() else None,
                        "modifiedAt": format_remote_modified_at(stat.st_mtime)
                    })
            self._json(200, {"rootId": root_id, "path": rp, "items": items})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def _handle_download(self, root_id, rp, expected=None):
        try:
            local = self._resolve_local_path(root_id, rp)
            if not local.exists() or not local.is_file():
                return self._json(404, {"error": "Archivo no encontrado en la Oficina"})
            remote_stat = local.stat()
            size = remote_stat.st_size
            expected = expected or {}
            expected_time = parse_remote_modified_at(expected.get("expectedModifiedAt"))
            if (("expectedSize" in expected and int(expected["expectedSize"]) != size) or
                    (expected_time and abs(remote_stat.st_mtime - expected_time) > REMOTE_MTIME_TOLERANCE_S)):
                return self._json(409, {"error": "El archivo cambio en Oficina. Actualiza la carpeta y vuelve a abrirlo."})
            start = 0
            status = 200
            range_header = self.headers.get("Range", "")
            match = re.match(r"^bytes=(\d+)-$", range_header)
            if match:
                start = int(match.group(1))
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */{}".format(size))
                    self.end_headers()
                    return
                status = 206

            self.send_response(status)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size - start))
            self.send_header("Content-Type", "application/octet-stream")
            if status == 206:
                self.send_header("Content-Range", "bytes {}-{}/{}".format(start, size - 1, size))
            self.end_headers()
            with local.open("rb") as fh:
                if start:
                    fh.seek(start)
                while True:
                    chunk = fh.read(DOWNLOAD_CHUNK)
                    if not chunk: break
                    self.wfile.write(chunk)
        except PermissionError as e:
            self._json(403, {"error": str(e)})
        except Exception as e:
            try:
                self._json(400, {"error": str(e)})
            except Exception:
                pass # Si falla el envio a la mitad no podemos mandar json

    def _handle_upload_chunk(self, params, data):
        try:
            uid = params.get("uploadId")
            idx = params.get("index")
            if not re.fullmatch(r"[a-f0-9]{32}", str(uid or "")) or not str(idx or "").isdigit():
                raise ValueError("Identificador de subida invalido")
            root_id = params.get("root")
            final_rp = join_remote(params.get("path", ""), params.get("name", ""))
            sync_data = load_syncremote()
            root = sync_root_record(sync_data, root_id)
            if not root:
                raise FileNotFoundError("Root no encontrado")
            allowed, policy_reason = upload_policy(root, final_rp, Path(final_rp), reason="remote-client")
            if not allowed:
                raise PermissionError(policy_reason)
            auth_access = getattr(self, "auth_access", None)
            if not access_is_admin(auth_access) and not (auth_access or {}).get("directUpload"):
                raise PermissionError("Este usuario no tiene subida directa habilitada")
            if not can_access_remote_path(getattr(self, "auth_access", None), root_id, final_rp):
                raise PermissionError("Sin permiso para subir a esta ruta")
            if not data:
                raise ValueError("Subida bloqueada: se recibio un chunk vacio.")
            tmp_dir = DATA_DIR / "uploads" / uid
            tmp_dir.mkdir(parents=True, exist_ok=True)
            (tmp_dir / idx).write_bytes(data)
            self._json(200, {"ok": True})
        except Exception as e:
            self._json(400, {"error": str(e)})

    def _handle_upload_complete(self, body):
        temp_path = None
        tmp_dir = None
        chunks = []
        try:
            uid = body.get("uploadId")
            total = int(body.get("total", 0))
            auth_access = getattr(self, "auth_access", None)
            if not access_is_admin(auth_access) and not (auth_access or {}).get("directUpload"):
                raise PermissionError("Este usuario no tiene subida directa habilitada")
            expected_size = int(body.get("size", -1))
            source_mtime = body.get("mtime")
            if not re.fullmatch(r"[a-f0-9]{32}", str(uid or "")) or total <= 0:
                raise ValueError("Subida incompleta: faltan datos de control.")
            if expected_size <= 0:
                raise ValueError("Subida bloqueada: no se aceptan archivos de 0 bytes.")
            tmp_dir = DATA_DIR / "uploads" / uid
            if not tmp_dir.exists() or not tmp_dir.is_dir():
                raise FileNotFoundError("Subida incompleta: no existe la carpeta temporal.")

            final_remote_path = join_remote(body.get("path"), body.get("name"))
            sync_data = load_syncremote()
            root = sync_root_record(sync_data, body.get("root"))
            if not root:
                raise FileNotFoundError("Root no encontrado")
            allowed, policy_reason = upload_policy(root, final_remote_path, Path(final_remote_path), reason="remote-client")
            if not allowed:
                raise PermissionError(policy_reason)
            final_path = self._resolve_local_path(body.get("root"), final_remote_path)
            final_path.parent.mkdir(parents=True, exist_ok=True)

            received_size = 0
            for i in range(total):
                chunk_file = tmp_dir / str(i)
                if not chunk_file.exists() or not chunk_file.is_file():
                    raise ValueError("Subida incompleta: falta el chunk {}.".format(i))
                chunk_size = chunk_file.stat().st_size
                if chunk_size <= 0:
                    raise ValueError("Subida bloqueada: el chunk {} esta vacio.".format(i))
                received_size += chunk_size
                chunks.append(chunk_file)
            if received_size != expected_size:
                raise ValueError("Subida incompleta: tamano recibido {} distinto de {}.".format(received_size, expected_size))

            temp_name = ".{}.remote-believe-uploading-{}".format(final_path.name, safe_component(uid))
            temp_path = final_path.with_name(temp_name)
            temp_path.unlink(missing_ok=True)
            with temp_path.open("wb") as out:
                for chunk_file in chunks:
                    with chunk_file.open("rb") as chunk_in:
                        shutil.copyfileobj(chunk_in, out, length=1024 * 1024)
            written_size = temp_path.stat().st_size
            if written_size <= 0 or written_size != expected_size:
                raise ValueError("Subida bloqueada: archivo temporal invalido.")

            received_hash = file_hash(temp_path)
            if body.get("sha256") and received_hash != body["sha256"]:
                raise ValueError("Los bytes recibidos no coinciden con el archivo enviado.")
            with UPLOAD_COMMIT_LOCK:
                current_hash = file_hash(final_path) if final_path.is_file() else None
                if current_hash and current_hash == received_hash:
                    status = "already-current"
                    temp_path.unlink(missing_ok=True)
                else:
                    if final_path.exists() and (not body.get("overwrite") or not current_hash
                            or body.get("expectedHash") != current_hash):
                        raise PermissionError("Oficina cambio este archivo; se conserva la copia local y no se reemplaza el servidor.")
                    if current_hash:
                        os.replace(temp_path, final_path)
                    elif os.name == "nt":
                        os.rename(temp_path, final_path)  # Create-only, even when another writer races.
                    else:
                        os.link(temp_path, final_path)
                        temp_path.unlink()
                    status = "uploaded"

            try:
                source_mtime = float(source_mtime)
                if source_mtime > 0 and status == "uploaded":
                    os.utime(final_path, (source_mtime, source_mtime))
            except Exception:
                pass
            for chunk_file in chunks:
                try:
                    chunk_file.unlink()
                except FileNotFoundError:
                    pass
            tmp_dir.rmdir()
            self._json(200, {"ok": True, "status": status})
        except Exception as e:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
            for chunk_file in chunks:
                try:
                    chunk_file.unlink()
                except Exception:
                    pass
            if tmp_dir:
                try:
                    tmp_dir.rmdir()
                except Exception:
                    pass
            self._json(400, {"error": str(e)})

    def _json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class OfficeServer:
    def __init__(self, port, app):
        self.port = port
        self._server = None
        self._app = app

    def start(self):
        try:
            srv = http.server.ThreadingHTTPServer(("0.0.0.0", self.port), OfficeServerHandler)
            srv._app = self._app
            self._server = srv
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            return True, f"Servidor Oficina en puerto {self.port}"
        except Exception as exc:
            return False, str(exc)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class CommandServerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        try:
            parsed = parse.urlparse(self.path)
            if parsed.path != "/command":
                return self._json(404, {"ok": False, "error": "Ruta no encontrada"})
            params = dict(parse.parse_qsl(parsed.query))
            action = params.get("action", "")
            target = params.get("path", "")
            ok, msg = self.server._app.enqueue_explorer_command(action, target)
            self._json(200 if ok else 400, {"ok": ok, "message": msg, "error": None if ok else msg})
        except Exception as exc:
            self._json(500, {"ok": False, "error": str(exc)})

    def _json(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class CommandServer:
    def __init__(self, port, app):
        self.port = port
        self._server = None
        self._app = app

    def start(self):
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), CommandServerHandler)
            srv._app = self._app
            self._server = srv
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            return True, "Menu Explorer activo en 127.0.0.1:{}".format(self.port)
        except Exception as exc:
            return False, str(exc)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


# ─── Storage Local (Cache para Casa) ─────────────────────────────────────────

class LocalStore(LocalDeletionPolicy):
    def __init__(self):
        self.local_drives = {}
        self.shared_drives = {}
        self.mount_errors = {}
        self.mount_lock = threading.RLock()
        self.cloud_engine = None
        self.settings = load_json(SETTINGS_PATH, {

            "cache_dir":   str(DEFAULT_CACHE),
            "username":    "",
            "mode":        "home", # "home" o "office"
            "office_ip": "",
            "manual_office_ip": False,
            "auto_upload": True,
            "theme": "light",
            "offline_folders": [],
        })
        self.manifest = load_json(MANIFEST_PATH, {})
        if self.manifest and self.settings.get("mode") == "home" and self.settings.get("username") and not self.settings.get("cache_owner"):
            self.settings["cache_owner"] = self.settings["username"]
        if not self.settings.get("office_ip"):
            defaults = load_json(Path(__file__).parent / "connection-defaults.json", {})
            self.settings["office_ip"] = str(defaults.get("office_ip") or "")
        self.outbox = load_json(OUTBOX_PATH, {"version": 1, "items": {}})
        if not isinstance(self.outbox, dict):
            self.outbox = {"version": 1, "items": {}}
        if not isinstance(self.outbox.get("items"), dict):
            self.outbox["items"] = {}
        from account_profiles import AccountProfiles
        self.account_profiles = AccountProfiles(DATA_DIR, load_json, save_json)
        self.account_profiles.restore(self)

    @property
    def cache_dir(self): return Path(self.settings.get("cache_dir") or str(DEFAULT_CACHE))

    def save_settings(self):
        manager = getattr(self, "account_profiles", None)
        if manager:
            manager.save_preferences(self)
        save_json(SETTINGS_PATH, self.settings)

    def save_manifest(self): save_json(getattr(self, "_manifest_path", MANIFEST_PATH), self.manifest)
    def save_outbox(self): save_json(getattr(self, "_outbox_path", OUTBOX_PATH), self.outbox)

    def switch_account(self, username):
        self.account_profiles.activate(self, username)

    def root_dir(self, root):
        letter = root_drive_letter(root) or safe_component(root.get("id", "root"))
        return self.cache_dir / letter

    def drive_root(self, root):
        return Path("{}:\\".format(root_drive_letter(root)))

    def local_drive(self, root):
        letter = self.local_drives.get(root["id"], "")
        target = existing_subst_target(letter) if letter else ""
        if target:
            if Path(target).resolve() == self.root_dir(root).resolve():
                return letter
        return ""

    def _account_roots(self, letter):
        return {Path(record["cache_dir"]).resolve() / letter
                for record in self.settings.get("account_profiles", {}).values() if record.get("cache_dir")}

    def _ensure_account_drive(self, letter, cache):
        current = existing_subst_target(letter)
        if current and Path(current).resolve() != cache.resolve() and Path(current).resolve() in self._account_roots(letter):
            # Only remap a verified Believe cache, never another job's subst or disk.
            if existing_subst_target(letter) != current:
                return False, "{}: cambio mientras se preparaba el acceso.".format(letter)
            result = subprocess.run(["subst", letter + ":", "/d"], capture_output=True, text=True, **subprocess_hidden_kwargs())
            if result.returncode:
                return False, "No se pudo desconectar la cache anterior de {}:.".format(letter)
            ok, message = ensure_subst_drive(letter, cache)
            if not ok:
                ensure_subst_drive(letter, current)
            return ok, message
        return ensure_subst_drive(letter, cache)

    def _detach_other_account_junctions(self, root, include_current=False):
        letter = root_drive_letter(root)
        known = self._account_roots(letter)
        if not known:
            return
        current = self.root_dir(root).resolve()
        for link in self.drive_root(root).iterdir():
            # rmdir removes only the junction entry; it never removes its target files.
            try:
                if getattr(link.lstat(), "st_reparse_tag", 0) != 0xA0000003:
                    continue
                target = link.resolve()
            except OSError:
                # Unrelated protected folders must not block the account's own links.
                continue
            if (include_current or target != current / link.name) and target in {base / link.name for base in known}:
                if link.resolve() == target and getattr(link.lstat(), "st_reparse_tag", 0) == 0xA0000003:
                    os.rmdir(link)

    def detach_account_mounts(self, keep_letters=(), include_current=True):
        if not self.settings.get("account_profiles"):
            return
        letters = set(self.settings.get("account_mount_letters", [])) | {"X", "R"}
        for letter in letters - set(keep_letters):
            if not re.fullmatch(r"[A-Z]", letter):
                continue
            current = existing_subst_target(letter)
            known = self._account_roots(letter)
            if current and Path(current).resolve() in known:
                if existing_subst_target(letter) != current:
                    raise RBError("{}: cambio mientras se desconectaba la cuenta anterior.".format(letter))
                result = subprocess.run(["subst", letter + ":", "/d"], capture_output=True, text=True, **subprocess_hidden_kwargs())
                if result.returncode:
                    raise RBError("No se pudo desconectar la cache anterior de {}:.".format(letter))
            else:
                root = {"id": "account-detach-" + letter, "driveLetter": letter}
                if supports_local_junctions(self.drive_root(root)):
                    self._detach_other_account_junctions(root, include_current=include_current)

    def mount_roots(self, roots):
        self.local_drives = {}
        self.shared_drives = {}
        self.mount_errors = {}
        messages = []
        letters = {root_drive_letter(root) for root in roots if root_drive_letter(root)}
        self.detach_account_mounts(keep_letters=letters)
        self.settings["account_mount_letters"] = sorted(set(self.settings.get("account_mount_letters", [])) | letters)
        if self.settings.get("account_profiles"):
            self.save_settings()
        for root in roots:
            logical = root_drive_letter(root)
            cache = self.root_dir(root)
            cache.mkdir(parents=True, exist_ok=True)
            if not logical:
                continue
            ok, message = self._ensure_account_drive(logical, cache)
            if ok:
                self.local_drives[root["id"]] = logical
                messages.append(message)
            elif existing_subst_target(logical) and Path(existing_subst_target(logical)).resolve() in self._account_roots(logical):
                self.mount_errors[root["id"]] = message
                messages.append(message)
            elif supports_local_junctions(self.drive_root(root)) and supports_local_junctions(cache):
                try:
                    self._detach_other_account_junctions(root)
                except OSError as exc:
                    self.mount_errors[root["id"]] = "No se pudieron desconectar las carpetas de la cuenta anterior: {}".format(exc)
                    messages.append(self.mount_errors[root["id"]])
                    continue
                self.shared_drives[root["id"]] = logical
                messages.append("{}: existente: se enlazaran las carpetas de Believe conservando sus rutas.".format(logical))
            else:
                self.mount_errors[root["id"]] = "{}: esta ocupado y no admite enlaces de carpeta locales NTFS. Se mantienen las rutas; no se reemplaza el disco.".format(logical)
                messages.append(self.mount_errors[root["id"]])
        return messages

    def ensure_destination(self, root, rp, directory=False):
        rp = clean_browser_relative(rp)
        if not sys.platform.startswith("win"):
            return self.local_path(root, rp)
        if self.local_drive(root):
            return self.local_path(root, rp)
        logical = self.shared_drives.get(root["id"])
        if not logical:
            raise RBError(self.mount_errors.get(root["id"], "El disco de Believe no esta montado. Actualiza la conexion antes de continuar."))
        if not rp:
            return self.root_dir(root)
        parts = rp.split("/")
        if len(parts) == 1 and not directory:
            raise RBError("{}:\\{} es un archivo en la raiz de un disco ocupado. Solo se pueden integrar carpetas completas sin cambiar rutas.".format(logical, rp))
        # Reject a substituted target if it changed since login; never write a link on SMB.
        link = self.drive_root(root) / parts[0]
        with self.mount_lock:
            if not supports_local_junctions(link.parent):
                raise RBError("{}: ya no es un disco local NTFS disponible.".format(logical))
            ensure_folder_junction(link, self.root_dir(root) / parts[0])
        return self.local_path(root, rp)

    def explorer_path(self, root, rp=""):
        letter = root_drive_letter(root)
        if sys.platform.startswith("win") and letter:
            return self.drive_root(root).joinpath(*rp.split("/"))
        return self.local_path(root, rp)

    def display_label(self, root):
        label = root_display_label(root)
        if root["id"] in self.shared_drives:
            return "{} (disco existente)".format(label)
        if root["id"] in self.mount_errors:
            return "{} (conflicto)".format(label)
        return label

    def local_path(self, root, rp):
        cur = self.root_dir(root)
        for part in [p for p in rp.split("/") if p]: cur = cur / safe_component(part)
        return cur

    def key(self, root_id, rp): return f"{root_id}::{rp}"

    def offline_folder_key(self, root_id, rp):
        return self.key(root_id, normalize_remote_path(rp).lower().rstrip("/"))

    def offline_folders(self):
        folders = self.settings.setdefault("offline_folders", [])
        if not isinstance(folders, list):
            folders = []
            self.settings["offline_folders"] = folders
        clean = []
        seen = set()
        changed = False
        for entry in folders:
            if not isinstance(entry, dict):
                changed = True
                continue
            root_id = str(entry.get("rootId") or "").strip()
            rp = normalize_remote_path(entry.get("remotePath", "")).rstrip("/")
            if not root_id:
                changed = True
                continue
            key = self.offline_folder_key(root_id, rp)
            if key in seen:
                changed = True
                continue
            seen.add(key)
            clean.append({
                "rootId": root_id,
                "rootName": entry.get("rootName", root_id),
                "remotePath": rp,
                "addedAt": entry.get("addedAt") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        if changed or clean != folders:
            self.settings["offline_folders"] = clean
            self.save_settings()
        return clean

    def add_offline_folder(self, root, rp):
        root_id = root.get("id")
        rp = normalize_remote_path(rp).rstrip("/")
        if not root_id:
            return False
        new_key = self.offline_folder_key(root_id, rp)
        kept = []
        exists = False
        for entry in self.offline_folders():
            old_rp = normalize_remote_path(entry.get("remotePath", "")).rstrip("/")
            old_key = self.offline_folder_key(entry.get("rootId", ""), old_rp)
            if old_key == new_key:
                exists = True
                kept.append(entry)
                continue
            if entry.get("rootId") == root_id:
                if old_rp == "" or (rp and rp.startswith(old_rp + "/")):
                    exists = True
                    kept.append(entry)
                    continue
                if old_rp and (rp == "" or old_rp.startswith(rp + "/")):
                    continue
            kept.append(entry)
        if not exists:
            kept.append({
                "rootId": root_id,
                "rootName": root_display_label(root),
                "remotePath": rp,
                "addedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        self.settings["offline_folders"] = kept
        self.save_settings()
        return not exists

    def remove_offline_folder(self, root_id, rp):
        rp = normalize_remote_path(rp).rstrip("/")
        key = self.offline_folder_key(root_id, rp)
        folders = [entry for entry in self.offline_folders() if self.offline_folder_key(entry.get("rootId", ""), entry.get("remotePath", "")) != key]
        changed = len(folders) != len(self.settings.get("offline_folders", []))
        if changed:
            self.settings["offline_folders"] = folders
            self.save_settings()
        return changed

    def is_offline_folder(self, root_id, rp):
        rp = normalize_remote_path(rp).rstrip("/")
        key = self.offline_folder_key(root_id, rp)
        return any(self.offline_folder_key(entry.get("rootId", ""), entry.get("remotePath", "")) == key for entry in self.offline_folders())

    def is_inside_offline_folder(self, root_id, rp):
        rp = normalize_remote_path(rp).rstrip("/")
        for entry in self.offline_folders():
            if entry.get("rootId") != root_id:
                continue
            base = normalize_remote_path(entry.get("remotePath", "")).rstrip("/")
            if base == "" or rp == base or rp.startswith(base + "/"):
                return True
        return False

    def mark_downloaded(self, root_id, rp, item, local_path):
        local_path = Path(local_path)
        apply_remote_mtime(local_path, item)
        clear_windows_placeholder(local_path)
        self.remove_folder_marker_entry(local_path)
        entry = {
            "size": item.get("size"),
            "modifiedAt": item.get("modifiedAt"),
            "localPath": str(local_path),
            "downloadedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "placeholder": False,
        }
        if Path(local_path).exists():
            entry.update(local_snapshot_record(local_path))
        self.manifest[self.key(root_id, rp)] = entry
        self.save_manifest()

    def mark_placeholder(self, root_id, rp, item, local_path):
        key = self.key(root_id, rp)
        current = self.manifest.get(key, {})
        if current.get("placeholder") is False and Path(current.get("localPath", "")).exists():
            return
        mark_windows_placeholder(local_path)
        self.manifest[key] = {
            "size": item.get("size"),
            "modifiedAt": item.get("modifiedAt"),
            "localPath": str(local_path),
            "placeholder": True,
            "seenAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def mark_synced(self, root_id, rp, local_path):
        local_path = Path(local_path)
        clear_windows_placeholder(local_path)
        self.remove_folder_marker_entry(local_path)
        modified_at = format_remote_modified_at(local_path.stat().st_mtime) if local_path.exists() else time.strftime("%Y-%m-%dT%H:%M:%S")
        entry = {
            "size": local_path.stat().st_size if local_path.exists() else None,
            "modifiedAt": modified_at,
            "localPath": str(local_path),
            "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "placeholder": False,
        }
        if local_path.exists():
            entry.update(local_snapshot_record(local_path))
        self.manifest[self.key(root_id, rp)] = entry
        self.save_manifest()

    def manifest_has_current_local_snapshot(self, root_id, rp, local_path):
        local_path = Path(local_path)
        entry = self.manifest.get(self.key(root_id, rp), {})
        if not entry or entry.get("placeholder"):
            return False
        if not local_path.exists() or not local_path.is_file():
            return False
        snap = local_snapshot_record(local_path)
        if "localMtimeNs" in entry and "localSize" in entry:
            return entry.get("localMtimeNs") == snap["localMtimeNs"] and entry.get("localSize") == snap["localSize"]
        recorded_size = entry.get("size")
        if recorded_size is None:
            recorded_size = entry.get("localSize")
        return recorded_size is not None and int(recorded_size) == snap["localSize"]

    def outbox_key(self, root_id, rp):
        return self.key(root_id, normalize_remote_path(rp))

    def enqueue_upload(self, root, rp, local_path, reason="changed"):
        local_path = Path(local_path)
        if not local_path.exists() or not local_path.is_file():
            return None
        root_id = root.get("id")
        rp = normalize_remote_path(rp)
        key = self.outbox_key(root_id, rp)
        snap = local_snapshot_record(local_path)
        existing = self.outbox.setdefault("items", {}).get(key, {})
        entry = {
            **existing,
            "rootId": root_id,
            "rootName": root_display_label(root),
            "remotePath": rp,
            "localPath": str(local_path),
            "reason": reason,
            "queuedAt": existing.get("queuedAt") or time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "nextAttemptAt": min(float(existing.get("nextAttemptAt", 0) or 0), time.time()) if existing else 0,
            "attempts": int(existing.get("attempts", 0) or 0),
            "lastError": existing.get("lastError", ""),
            **snap,
        }
        self.outbox["items"][key] = entry
        self.save_outbox()
        return entry

    def remove_upload(self, root_id, rp):
        key = self.outbox_key(root_id, rp)
        if key in self.outbox.setdefault("items", {}):
            self.outbox["items"].pop(key, None)
            self.save_outbox()

    def update_upload_error(self, root_id, rp, message, delay_s=None):
        key = self.outbox_key(root_id, rp)
        entry = self.outbox.setdefault("items", {}).get(key)
        if not entry:
            return
        attempts = int(entry.get("attempts", 0) or 0) + 1
        delay = delay_s if delay_s is not None else min(300, 15 * attempts)
        entry["attempts"] = attempts
        entry["lastError"] = str(message)
        entry["lastAttemptAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry["nextAttemptAt"] = time.time() + delay
        self.save_outbox()

    def pending_uploads(self, ready_only=False):
        now = time.time()
        entries = list(self.outbox.setdefault("items", {}).values())
        if ready_only:
            entries = [entry for entry in entries if float(entry.get("nextAttemptAt", 0) or 0) <= now]
        return sorted(entries, key=lambda entry: entry.get("updatedAt", ""))

    def is_upload_pending(self, root_id, rp):
        return self.outbox_key(root_id, rp) in self.outbox.setdefault("items", {})

    def local_items_for_folder(self, root, remote_path):
        local_dir = self.local_path(root, remote_path)
        if not local_dir.exists() or not local_dir.is_dir():
            return []
        items = []
        for p in local_dir.iterdir():
            if is_ignored(p):
                continue
            child_rp = join_remote(remote_path, p.name)
            if p.is_file() and self.is_placeholder_path(root, p):
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            items.append({
                "name": p.name,
                "type": "directory" if p.is_dir() else "file",
                "size": stat.st_size if p.is_file() else None,
                "modifiedAt": format_remote_modified_at(stat.st_mtime),
                "localOnly": True,
                "remotePath": child_rp,
            })
        return items

    def folder_marker_path(self, local_dir):
        return Path(local_dir) / PLACEHOLDER_MARKER

    def load_folder_marker(self, local_dir):
        return load_json(self.folder_marker_path(local_dir), {"version": 1, "items": {}})

    def save_folder_marker(self, local_dir, marker):
        path = self.folder_marker_path(local_dir)
        save_json(path, marker)
        hide_windows_file(path)

    def update_folder_marker(self, local_dir, entries):
        if not entries:
            return
        marker = self.load_folder_marker(local_dir)
        marker["version"] = 1
        marker["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        marker.setdefault("items", {}).update(entries)
        self.save_folder_marker(local_dir, marker)

    def remove_folder_marker_entry(self, local_path):
        local_path = Path(local_path)
        marker_path = self.folder_marker_path(local_path.parent)
        if not marker_path.exists():
            return
        marker = self.load_folder_marker(local_path.parent)
        items = marker.get("items", {})
        if local_path.name in items:
            items.pop(local_path.name, None)
            self.save_folder_marker(local_path.parent, marker)

    def is_placeholder(self, root_id, rp):
        entry = self.manifest.get(self.key(root_id, rp), {})
        engine = getattr(self, "cloud_engine", None)
        if engine and entry.get("cloudPlaceholder") and entry.get("localPath"):
            return engine.is_hydrating(root_id, rp) or engine.is_unhydrated(entry["localPath"])
        return bool(entry.get("placeholder"))

    def iter_local_files(self, root):
        engine = getattr(self, "cloud_engine", None)
        if engine:
            yield from engine.iter_files(root)
        else:
            yield from self.root_dir(root).rglob("*")

    def is_placeholder_path(self, root, local_path):
        engine = getattr(self, "cloud_engine", None)
        if engine and engine.is_unhydrated(local_path):
            return True
        rp = self.remote_path_from_local(root, local_path)
        return bool(rp and self.is_placeholder(root.get("id"), rp))

    def is_dirty_cloud_file(self, path):
        engine = getattr(self, "cloud_engine", None)
        if not engine:
            return False
        from cloud_files import PLACEHOLDER, PARTIAL, IN_SYNC
        state = engine.native.state(path)
        return bool(state & PLACEHOLDER and not state & (PARTIAL | IN_SYNC))

    def remote_path_from_local(self, root, local_path):
        try:
            rel = cache_relative_path(self.root_dir(root), local_path)
            return "/".join(rel.parts)
        except ValueError:
            return ""

    def is_local_current(self, root, root_id, rp, item):
        local = self.local_path(root, rp)
        if item.get("type") == "directory":
            return local.exists() and local.is_dir() and any(local.iterdir())
        if self.is_upload_pending(root_id, rp):
            return local.exists() and local.is_file()
        if not local_matches_remote_item(local, item):
            return False
        if self.is_placeholder(root_id, rp):
            size = item.get("size")
            if local.stat().st_size > 0 or size in (0, "0"):
                self.mark_downloaded(root_id, rp, item, local)
                return True
            return False
        return True


# ─── Aplicación Principal ────────────────────────────────────────────────────

class MeshClientApp(AssetUpdatesPanel, DesktopControls, tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1120x720")
        self.minsize(900, 560)

        self.store = LocalStore()
        apply_visual_theme(self.store.settings.get("theme", "light"))
        self.device_id = get_or_create_device_id()
        self.web_api = None   # Para hablar con el servidor web (auth)
        self.file_api = None  # Para descargar/subir (OfficeApi o null en modo office)
        self.access = {"roots": []}
        self.roots = []
        self.current_root = None
        self.current_path = ""
        self.items = {}
        self.all_items = []
        self.busy = False

        self.watcher_stop = threading.Event()
        self.watcher_thread = None
        self.file_snapshots = {}
        self.pending_changes = {}
        self.download_paths = set()
        self.upload_paths = set()
        self.offline_sync_thread = None
        self.offline_sync_lock = threading.Lock()
        self.transfer_states = {}
        self.activity = []
        self.transfers = TransferControl()
        self.update_blocked = False
        self.updater = GitHubUpdates(APP_VERSION, DATA_DIR / "updates")
        self.activity_visible = True
        self.progress_active = False

        self.nav_back = []
        self.nav_forward = []
        self.root_buttons = {}
        self.icon_images = {}

        self.office_server = None
        self.command_server = None
        self.tray_icon = None
        self.tray_available = False
        self._tray_message_shown = False
        self._closing_app = False
        self._token_cache = {}
        self.cloud_engine = None
        self.explorer_dashboard = False
        self.session_epoch = 0
        self.session_context = threading.local()
        self.session_threads = set()
        self.session_threads_lock = threading.Lock()
        self.connection_timer_id = None
        self.asset_tracker = None
        self._asset_state = {}
        self._asset_checked_at = 0
        self.screen = "login"

        s = ttk.Style(self)
        s.theme_use("clam")
        self._setup_style(s)
        self._create_icon_images()
        self._setup_tray()
        self._discard_saved_login()
        if getattr(sys, "frozen", False):
            self._update_start()
        else:
            self._build_login()
        self.bind("<Unmap>", self._on_unmap)

    def post(self, cb, *args):
        epoch = getattr(self.session_context, "epoch", self.session_epoch)
        if epoch != self.session_epoch or self._closing_app:
            return
        def dispatch():
            if epoch == self.session_epoch and not self._closing_app:
                cb(*args)
        try:
            self.after(0, dispatch)
        except (RuntimeError, tk.TclError):
            if epoch == self.session_epoch and not self._closing_app:
                raise

    def report_callback_exception(self, exception, value, traceback):
        logging.getLogger("RemoteBelieve").error("Interface callback failed", exc_info=(exception, value, traceback))
        self.set_status("Ocurrio un error. Puedes ver el detalle en Ver registros.")

    def _start_session_thread(self, target):
        epoch = getattr(self.session_context, "epoch", self.session_epoch)
        if epoch != self.session_epoch or self._closing_app:
            return None
        def wrapped():
            self.session_context.epoch = epoch
            try:
                if epoch == self.session_epoch:
                    target()
            except Exception as exc:
                self.post(self._err, exc)
            finally:
                with self.session_threads_lock:
                    self.session_threads.discard(threading.current_thread())
        thread = threading.Thread(target=wrapped, daemon=True)
        with self.session_threads_lock:
            self.session_threads.add(thread)
            thread.start()
        return thread

    def _setup_style(self, s):
        self.configure(bg=BG)
        self.option_add("*Font", FONT_UI)
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=TEXT)
        s.configure("Icon.TButton", padding=(12, 8), relief="flat", borderwidth=0, background=CONTROL_BG, foreground=BLUE, font=("Segoe UI", 13, "bold"))
        s.map("Icon.TButton", background=[("active", BLUE_SOFT), ("disabled", CONTROL_BG)], foreground=[("disabled", MUTED)])
        s.configure("Accent.TButton", padding=(14, 8), relief="flat", borderwidth=0, foreground="#fff", background=BLUE)
        s.map("Accent.TButton", background=[("active", "#0a84ff"), ("disabled", "#9bc8ff")])
        s.configure("Ghost.TButton", padding=(10, 7), relief="flat", borderwidth=0, background=CONTROL_BG)
        s.map("Ghost.TButton", background=[("active", CONTROL_ACTIVE)])
        s.configure("Treeview", rowheight=38, font=FONT_UI, fieldbackground=PANEL, background=PANEL, borderwidth=0)
        s.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), foreground=MUTED, background=TREE_HEAD_BG, relief="flat")
        s.map("Treeview", background=[("selected", SELECT_BG)], foreground=[("selected", TEXT)])
        s.configure("Horizontal.TProgressbar", troughcolor=PROGRESS_TROUGH, background=BLUE)

    def _create_icon_images(self):
        self.icon_images = {
            "cloud": self._draw_cloud_icon(),
            "check": self._draw_check_icon(),
            "office": self._draw_office_icon(),
            "download": self._draw_download_icon(),
            "upload": self._draw_upload_icon(),
            "error": self._draw_error_icon(),
            "denied": self._draw_denied_icon(),
        }

    def _new_icon(self, size=24, bg=None):
        bg = PANEL if bg is None else bg
        image = tk.PhotoImage(width=size, height=size)
        image.put(bg, to=(0, 0, size, size))
        return image

    def _put_circle(self, image, cx, cy, radius, color):
        for x in range(cx - radius, cx + radius + 1):
            for y in range(cy - radius, cy + radius + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                    if 0 <= x < image.width() and 0 <= y < image.height():
                        image.put(color, to=(x, y))

    def _put_rect(self, image, x1, y1, x2, y2, color):
        image.put(color, to=(max(0, x1), max(0, y1), min(image.width(), x2), min(image.height(), y2)))

    def _draw_cloud_icon(self):
        image = self._new_icon()
        self._put_circle(image, 9, 13, 5, CLOUD)
        self._put_circle(image, 14, 11, 6, CLOUD)
        self._put_circle(image, 18, 14, 4, CLOUD)
        self._put_rect(image, 6, 13, 21, 18, CLOUD)
        return image

    def _draw_check_icon(self):
        image = self._new_icon()
        self._put_circle(image, 12, 12, 10, GREEN)
        for offset in range(3):
            self._put_rect(image, 7 + offset, 12 + offset, 10 + offset, 15 + offset, "#ffffff")
            self._put_rect(image, 10 + offset, 15 - offset, 18 + offset, 18 - offset, "#ffffff")
        return image

    def _draw_office_icon(self):
        image = self._new_icon()
        self._put_circle(image, 12, 12, 9, OFFICE_COL)
        self._put_circle(image, 12, 12, 4, "#ffffff")
        return image

    def _draw_download_icon(self):
        image = self._new_icon()
        self._put_circle(image, 12, 12, 10, BLUE)
        self._put_rect(image, 11, 6, 13, 15, "#ffffff")
        self._put_rect(image, 8, 13, 16, 15, "#ffffff")
        self._put_rect(image, 9, 15, 15, 17, "#ffffff")
        return image

    def _draw_upload_icon(self):
        image = self._new_icon()
        self._put_circle(image, 12, 12, 10, YELLOW)
        self._put_rect(image, 11, 9, 13, 18, "#ffffff")
        self._put_rect(image, 8, 8, 16, 10, "#ffffff")
        self._put_rect(image, 9, 6, 15, 8, "#ffffff")
        return image

    def _draw_error_icon(self):
        image = self._new_icon()
        self._put_circle(image, 12, 12, 10, RED)
        self._put_rect(image, 11, 6, 13, 14, "#ffffff")
        self._put_rect(image, 11, 17, 13, 19, "#ffffff")
        return image

    def _draw_denied_icon(self):
        image = self._new_icon()
        self._put_circle(image, 12, 12, 10, YELLOW)
        for offset in range(-1, 2):
            self._put_rect(image, 6, 16 + offset, 18, 18 + offset, "#ffffff")
        return image

    def _setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            self.tray_available = False
            return

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=14, fill=(10, 132, 255, 255))
        draw.ellipse((20, 22, 44, 46), fill=(255, 255, 255, 255))
        draw.rectangle((18, 32, 46, 44), fill=(255, 255, 255, 255))

        def open_app(icon=None, item=None):
            self.post(self._show_window)

        def hide_app(icon=None, item=None):
            self.post(self._hide_to_tray)

        def quit_app(icon=None, item=None):
            self.post(self._quit_app)

        def logout(icon=None, item=None):
            self.post(self._logout)

        menu = pystray.Menu(
            pystray.MenuItem("Abrir Remote Believe", open_app),
            pystray.MenuItem("Ocultar", hide_app),
            pystray.MenuItem("Cerrar sesion", logout),
            pystray.MenuItem("Salir", quit_app),
        )
        self.tray_icon = pystray.Icon("RemoteBelieve", image, APP_NAME, menu)
        self.tray_available = True
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_window(self):
        self.deiconify()
        self.state("normal")
        self._tray_message_shown = False
        self.lift()
        self.after(100, self.focus_force)

    def _hide_to_tray(self):
        if self.tray_available:
            self.withdraw()
            if not self._tray_message_shown:
                self._notify("Remote Believe", "Sigue funcionando en segundo plano.")
                self._tray_message_shown = True
        else:
            self.iconify()

    def _on_unmap(self, event):
        if event.widget is not self or self._closing_app:
            return
        try:
            minimized = self.state() == "iconic"
        except Exception:
            minimized = False
        if minimized and self.tray_available:
            self.after(120, self._hide_to_tray)

    def _theme_key(self):
        value = str(self.store.settings.get("theme", "light")).lower()
        return value if value in THEMES else "light"

    def _theme_button_text(self):
        return "Tema claro" if self._theme_key() == "dark" else "Tema oscuro"

    def _toggle_theme(self):
        new_theme = "light" if self._theme_key() == "dark" else "dark"
        self.store.settings["theme"] = new_theme
        self.store.save_settings()
        apply_visual_theme(new_theme)
        style = ttk.Style(self)
        style.theme_use("clam")
        self._setup_style(style)
        self._create_icon_images()
        if self.screen == "main":
            root_id = self.current_root.get("id") if self.current_root else None
            current_path = self.current_path
            self._build_main(self.roots, self.access, initial_root_id=root_id, initial_path=current_path)
        else:
            self._build_login()

    def _notify(self, title, message):
        if self.tray_icon and hasattr(self.tray_icon, "notify"):
            try:
                self.tray_icon.notify(message, title)
            except Exception:
                pass

    def _quit_app(self):
        self._closing_app = True
        if getattr(self, "transfers", None):
            self.transfers.cancel_all()
        self.watcher_stop.set()
        if self.office_server: self.office_server.stop()
        if self.command_server: self.command_server.stop()
        if self.cloud_engine:
            self.cloud_engine.close()
            self.cloud_engine = None
        if self.tray_icon:
            try: self.tray_icon.stop()
            except Exception: pass
        self.destroy()

    def _worker(self, target):
        def wrapped():
            try: target()
            except TransferStopped:
                self.post(self.set_status, "Transferencia detenida; tus archivos se conservan.")
                self.post(self._reset_progress)
            except Exception as exc: self.post(self._err, exc)
            finally: self.post(self._busy, False)
        self._busy(True)
        self._start_session_thread(wrapped)

    def _busy(self, busy):
        self.busy = busy
        if hasattr(self, "progress"):
            if busy and not self.progress_active:
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
            elif not busy and not self.progress_active:
                self.progress.stop()
                self.progress.configure(mode="determinate")
                if hasattr(self, "progress_var"):
                    self.progress_var.set(0)

    def _err(self, exc):
        friendly = self._friendly_transfer_error(exc)
        self._reset_progress()
        self.set_status("Error: {}".format(friendly))
        self._log_activity("Error", friendly, state="error")
        self._notify("Remote Believe", "Error: {}".format(friendly))
        messagebox.showerror(APP_NAME, friendly)

    def set_status(self, text):
        if hasattr(self, "status_var"): self.status_var.set(text)
        if hasattr(self, "login_status_var"): self.login_status_var.set(text)

    def _friendly_transfer_error(self, exc):
        msg = str(exc)
        low = msg.lower()
        if "subida directa" in low or "sin permiso" in low:
            return "Sin permiso: {}".format(msg)
        if "used by another process" in low or "being used by another process" in low or "archivo bloqueado" in low:
            return "Archivo bloqueado. Cierra Maya u otro programa que lo este usando y vuelve a intentar."
        if "no se puede conectar" in low or "timed out" in low or "refused" in low or "unreachable" in low:
            return "Oficina apagada o Tailscale desconectado. Revisa que el PC oficina este encendido y el cliente este abierto."
        return msg

    def _transfer_key(self, root_id, rp):
        return "{}::{}".format(root_id, normalize_remote_path(rp).lower())

    def _set_transfer_state(self, root_id, rp, state, message=""):
        self.transfer_states[self._transfer_key(root_id, rp)] = {"state": state, "message": message}
        if self.explorer_dashboard:
            self._update_dashboard_status()
        if hasattr(self, "tree"):
            self._apply_filter()

    def _clear_transfer_state(self, root_id, rp):
        self.transfer_states.pop(self._transfer_key(root_id, rp), None)
        if hasattr(self, "tree"):
            self._apply_filter()

    def _set_progress(self, done, total, label=""):
        if not hasattr(self, "progress"):
            if label:
                self.set_status(label)
            return
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100)
        self.progress_active = True
        percent = 0
        if total:
            percent = max(0, min(100, (float(done) / float(total)) * 100))
        self.progress_var.set(percent)
        if hasattr(self, "progress_text_var"):
            if total:
                self.progress_text_var.set("{}  {:.0f}%".format(label, percent).strip())
            else:
                self.progress_text_var.set(label)
        if label:
            self.set_status(label)

    def _reset_progress(self):
        self.progress_active = False
        if hasattr(self, "progress"):
            self.progress.stop()
            self.progress.configure(mode="determinate")
        if hasattr(self, "progress_var"):
            self.progress_var.set(0)
        if hasattr(self, "progress_text_var"):
            self.progress_text_var.set("")

    def _download_active(self):
        engine = getattr(self, "cloud_engine", None)
        return bool(getattr(self, "download_paths", set()) or (engine and engine.hydrating))

    def _upload_yield_for_downloads(self):
        if self._download_active():
            time.sleep(UPLOAD_YIELD_WHEN_DOWNLOADING_S)

    def _log_activity(self, kind, message, detail="", state="info"):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "kind": kind,
            "message": str(message),
            "detail": str(detail or ""),
            "state": state,
        }
        entry["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
        entry["direction"] = ("upload" if kind.casefold().startswith("subida") or state == "upload" or "subir" in str(message).casefold() else
                              "download" if kind.casefold().startswith("descarga") or state == "download" or "descarg" in str(message).casefold() else "system")
        self.activity.insert(0, entry)
        self.activity = self.activity[:500]
        if getattr(self, "_history_path", None):
            save_json(self._history_path, self.activity)
        self._refresh_activity_panel()

    def _refresh_activity_panel(self):
        return self._render_history()
        if not hasattr(self, "activity_list"):
            return
        for child in self.activity_list.winfo_children():
            child.destroy()
        recent = self.activity[:6]
        if not recent:
            tk.Label(
                self.activity_list,
                text="Sin actividad reciente.",
                bg=PANEL,
                fg=MUTED,
                anchor="w",
                font=("Segoe UI", 9),
            ).pack(fill=X, padx=12, pady=10)
            return
        colors = {
            "success": (GREEN_SOFT, GREEN),
            "error": (RED_SOFT, RED),
            "upload": (YELLOW_SOFT, "#b36b00"),
            "download": (BLUE_SOFT, BLUE),
            "info": (CONTROL_BG, MUTED),
        }
        for entry in recent:
            bg, fg = colors.get(entry.get("state"), colors["info"])
            row = tk.Frame(self.activity_list, bg=PANEL)
            row.pack(fill=X, padx=8, pady=2)
            tk.Label(row, text=entry["kind"], bg=bg, fg=fg, font=("Segoe UI", 8, "bold"), padx=8, pady=3).pack(side=LEFT, padx=(0, 8))
            text = "{}  {}".format(entry["time"], entry["message"])
            tk.Label(row, text=text, bg=PANEL, fg=TEXT, anchor="w", font=("Segoe UI", 9)).pack(side=LEFT, fill=X, expand=True)
            if entry.get("detail"):
                tk.Label(row, text=entry["detail"], bg=PANEL, fg=MUTED, anchor="e", font=("Segoe UI", 8)).pack(side=RIGHT)

    def _toggle_activity_panel(self):
        if not hasattr(self, "activity_container"):
            return
        if self.activity_visible:
            self.activity_container.pack_forget()
            self.activity_visible = False
        else:
            self.activity_container.pack(fill=X, padx=16, pady=(0, 10), before=self.bottom_bar)
            self.activity_visible = True
        self._refresh_activity_panel()

    def _office_ip(self):
        if self.file_api:
            return self.file_api.office_url.split("//", 1)[-1].rsplit(":", 1)[0]
        return self.store.settings.get("office_ip", "")

    def _office_display_name(self):
        ip = self._office_ip()
        return self.store.settings.get("office_name") or tailscale_peer_name_for_ip(ip) or ip or "PC oficina"

    def _connection_text(self):
        mode = self.store.settings.get("mode")
        if mode == "office":
            return "Servidor activo en este PC"
        return "Conectado a {}".format(self._office_display_name())

    def _test_connection(self):
        def work():
            if self.store.settings.get("mode") == "office":
                self.post(self.set_status, "Servidor oficina activo en este PC.")
                self.post(self._log_activity, "Conexion", "Servidor oficina activo.", "", "success")
                return
            ip = self._office_ip()
            self.post(self.set_status, "Buscando PC oficina...")
            if hasattr(self, "connection_var"):
                self.post(self.connection_var.set, "Buscando PC oficina...")
            if office_server_responds(ip, timeout=2.5):
                name = tailscale_peer_name_for_ip(ip) or ip
                self.store.settings["office_name"] = name
                self.store.save_settings()
                text = "Conectado a {}".format(name)
                self.post(self.set_status, text)
                if hasattr(self, "connection_var"):
                    self.post(self.connection_var.set, text)
                self.post(self._log_activity, "Conexion", text, "", "success")
                return
            ts_data = tailscale_status_data()
            text = "Tailscale no disponible" if not ts_data else "Oficina apagada o sin responder"
            if hasattr(self, "connection_var"):
                self.post(self.connection_var.set, text)
            self.post(self.set_status, text)
            self.post(self._log_activity, "Conexion", text, "", "error")
        self._worker(work)

    def _open_current_local_folder(self):
        if not self.current_root:
            return
        if self.store.settings.get("mode") == "office":
            base = root_base_path(self.current_root)
            target = base / self.current_path.replace("/", "\\")
        else:
            try:
                self.store.ensure_destination(self.current_root, self.current_path, directory=True).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                self._err(exc)
                return
            target = self.store.explorer_path(self.current_root, self.current_path)
        self._open_path(target)

    def _on_close(self):
        if self._closing_app:
            self._quit_app()
            return
        self._hide_to_tray()

    # ── Login ──
    def _build_login(self):
        if getattr(self, "update_blocked", False):
            return self._build_update_screen()
        self.screen = "login"
        for c in self.winfo_children(): c.destroy()

        frame = tk.Frame(self, bg=BG)
        frame.pack(fill=BOTH, expand=True)
        tk.Button(frame, text=self._theme_button_text(), command=self._toggle_theme, bg=CONTROL_BG, fg=TEXT, activebackground=CONTROL_ACTIVE, activeforeground=TEXT, relief="flat", bd=0, padx=14, pady=8, font=("Segoe UI", 9, "bold")).place(relx=1.0, x=-24, y=22, anchor="ne")
        panel = tk.Frame(frame, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1)
        panel.place(relx=0.5, rely=0.5, anchor="center")
        panel.grid_columnconfigure(0, weight=1)

        tk.Label(panel, text="Remote Believe", bg=PANEL, fg=TEXT, font=("Segoe UI", 24, "bold")).grid(row=0, column=0, sticky="w", padx=30, pady=(28, 0))
        tk.Label(panel, text="P2P con Tailscale", bg=PANEL, fg=MUTED, font=("Segoe UI", 11)).grid(row=1, column=0, sticky="w", padx=30, pady=(4, 18))


        self.username_var = tk.StringVar(value=self.store.settings.get("username", ""))
        self.pw_var = tk.StringVar()
        self.mode_var = tk.StringVar(value=self.store.settings.get("mode", "home"))
        self.office_ip_var = tk.StringVar(value=self.store.settings.get("office_ip", ""))
        self.manual_ip_var = tk.BooleanVar(value=bool(self.store.settings.get("manual_office_ip", False)))

        def field(row, label, var, show=""):
            tk.Label(panel, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", padx=30, pady=(10, 0))
            wrap = tk.Frame(panel, bg=CONTROL_BG)
            wrap.grid(row=row + 1, column=0, sticky="ew", padx=30, pady=(5, 2))
            entry = tk.Entry(wrap, textvariable=var, width=46, relief="flat", bg=CONTROL_BG, fg=TEXT, insertbackground=BLUE, show=show)
            entry.pack(fill=X, padx=12, ipady=8)
            return entry

        row = 2
        field(row, "Usuario", self.username_var); row += 2
        field(row, "Contraseña", self.pw_var, "*"); row += 2

        tk.Label(panel, text="Modo", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", padx=30, pady=(14, 0))
        mode_frame = tk.Frame(panel, bg=CONTROL_BG)
        mode_frame.grid(row=row + 1, column=0, sticky="ew", padx=30, pady=(6, 2))
        mode_frame.grid_columnconfigure(0, weight=1)
        mode_frame.grid_columnconfigure(1, weight=1)
        self.home_mode_btn = tk.Button(mode_frame, text="Casa (Cliente)", command=lambda: self._set_mode("home"), relief="flat", bd=0, pady=9, font=("Segoe UI", 10, "bold"))
        self.office_mode_btn = tk.Button(mode_frame, text="Oficina (Servidor)", command=lambda: self._set_mode("office"), relief="flat", bd=0, pady=9, font=("Segoe UI", 10, "bold"))
        self.home_mode_btn.grid(row=0, column=0, sticky="ew", padx=(3, 2), pady=3)
        self.office_mode_btn.grid(row=0, column=1, sticky="ew", padx=(2, 3), pady=3)
        row += 2

        self.manual_ip_check = tk.Checkbutton(
            panel,
            text="Usar otra IP manualmente",
            variable=self.manual_ip_var,
            command=self._update_login_ui,
            bg=PANEL,
            fg=TEXT,
            selectcolor=PANEL,
            relief="flat",
            anchor="w",
        )
        self.manual_ip_check.grid(row=row, column=0, sticky="w", padx=30, pady=(12, 0))
        self.auto_ip_hint = tk.Label(panel, text="Si esta desactivado, busca un servidor oficina de tu misma cuenta Tailscale.", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w", wraplength=420)
        self.auto_ip_hint.grid(row=row + 1, column=0, sticky="w", padx=30, pady=(2, 0))
        row += 1

        self.ts_lbl = tk.Label(panel, text="IP de la Oficina", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"))
        self.ts_lbl.grid(row=row, column=0, sticky="w", padx=30, pady=(12, 0))
        self.ts_wrap = tk.Frame(panel, bg=CONTROL_BG)
        self.ts_wrap.grid(row=row + 1, column=0, sticky="ew", padx=30, pady=(5, 0))
        self.ts_entry = tk.Entry(self.ts_wrap, textvariable=self.office_ip_var, width=46, relief="flat", bg=CONTROL_BG, fg=TEXT, insertbackground=BLUE)
        self.ts_entry.pack(fill=X, padx=12, ipady=8)
        self.ts_hint = tk.Label(panel, text="Requerida en modo Casa para conectar a la oficina.", bg=PANEL, fg=MUTED, font=("Segoe UI", 8))
        self.ts_hint.grid(row=row + 2, column=0, sticky="w", padx=30, pady=(3, 10))
        row += 3

        self.login_status_var = tk.StringVar(value="Listo para conectar.")
        tk.Label(panel, textvariable=self.login_status_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w", wraplength=420).grid(row=row, column=0, sticky="ew", padx=30, pady=(6, 0))
        row += 1

        tk.Button(panel, text="Conectar", command=self._login, bg=BLUE, fg="#fff", activebackground=BLUE, activeforeground="#fff", relief="flat", bd=0, pady=11, font=("Segoe UI", 11, "bold")).grid(row=row, column=0, sticky="ew", padx=30, pady=(8, 28))

        self._update_login_ui()

    def _set_mode(self, mode):
        self.mode_var.set(mode)
        self._update_login_ui()

    def _update_login_ui(self):
        mode = self.mode_var.get()
        if hasattr(self, "home_mode_btn"):
            self.home_mode_btn.configure(bg=PANEL if mode == "home" else CONTROL_BG, fg=BLUE if mode == "home" else MUTED)
            self.office_mode_btn.configure(bg=PANEL if mode == "office" else CONTROL_BG, fg=OFFICE_COL if mode == "office" else MUTED)
        if mode == "office":
            self.manual_ip_check.grid_remove()
            self.auto_ip_hint.grid_remove()
            self.ts_lbl.grid_remove()
            self.ts_wrap.grid_remove()
            self.ts_hint.grid_remove()
        else:
            self.manual_ip_check.grid()
            if self.manual_ip_var.get():
                self.auto_ip_hint.grid_remove()
                self.ts_lbl.configure(text="IP manual de la Oficina")
                self.ts_hint.configure(text="Solo si quieres conectarte a otro equipo especifico.")
                self.ts_lbl.grid()
                self.ts_wrap.grid()
                self.ts_hint.grid()
            else:
                self.auto_ip_hint.grid()
                self.ts_lbl.grid_remove()
                self.ts_wrap.grid_remove()
                self.ts_hint.grid_remove()

    def _login(self):
        if getattr(self, "update_blocked", False):
            return self._build_update_screen()
        if self.screen != "login" or self.busy:
            return
        un = self.username_var.get().strip()
        pw = self.pw_var.get()
        mode = self.mode_var.get()
        office_ip = self.office_ip_var.get().strip()
        manual_ip = bool(self.manual_ip_var.get())

        if not un or not pw:
            messagebox.showwarning(APP_NAME, "Faltan credenciales")
            return
        if mode == "home" and manual_ip and not office_ip:
            messagebox.showwarning(APP_NAME, "Falta la IP de la Oficina")
            return

        def work():
            self.post(self.set_status, "Conectando...")

            if mode == "office":
                sync_data = load_syncremote()
                users = sync_data.get("users", {})
                if not users:
                    # Only local Office setup can create the first account; no published default password.
                    users[un] = {"password": pw, "role": "superuser", "directUpload": True,
                                 "projectAccess": {root["id"]: "*" for root in sync_data.get("roots", [])}}
                    sync_data["users"] = users
                    save_syncremote(sync_data)
                if un not in users or users[un].get("password") != pw:
                    raise RBError("Credenciales invalidas o usuario no encontrado en syncremote.json")
                access = build_access(sync_data, un)
                roots = access.get("roots", [])
                self.file_api = None # En modo oficina no se usa
                self._prepare_session(un, mode)
                self.store.settings.update({"username": un, "mode": mode, "manual_office_ip": manual_ip})
                self.store.save_settings()
                self.post(self._login_complete, roots, access)
            else:
                resolved_ip = office_ip
                if not manual_ip:
                    self.post(self.set_status, "Buscando PC de oficina en Tailscale...")
                    resolved_ip = discover_office_ip(self.store.settings.get("office_ip", ""), same_user_only=True)
                    if not resolved_ip:
                        if not tailscale_status_data():
                            raise RBError("Tailscale no disponible. Abre Tailscale, inicia sesion y vuelve a intentar.")
                        raise RBError("No encontre ningun PC de oficina activo en tu misma cuenta Tailscale. Activa 'Usar otra IP manualmente' si quieres escribir una IP.")
                    self.post(self.office_ip_var.set, resolved_ip)
                office_name = tailscale_peer_name_for_ip(resolved_ip) or resolved_ip
                api = OfficeFileApi(resolved_ip)
                self.post(self.set_status, "Conectado a {}. Validando usuario...".format(office_name))
                res = api.login(un, pw)
                access = res.get("access", {})
                try:
                    self._prepare_session(un, mode)
                except Exception:
                    api.close_session()
                    raise
                self.transfers = getattr(self, "transfers", None) or TransferControl()
                api.transfers = self.transfers
                api.opener = url_request.build_opener(ControlledHTTPHandler(self.transfers))
                self.file_api = api
                self.store.settings.update({"username": un, "mode": mode, "office_ip": resolved_ip, "office_name": office_name, "manual_office_ip": manual_ip})
                self.store.save_settings()
                self.post(self._login_complete, access.get("roots", []), access)

        self._worker(work)

    def _discard_saved_login(self):
        from windows_session import SavedSession
        SavedSession(DATA_DIR).clear()
        if self.store.settings.get("remember_session") is not False:
            self.store.settings["remember_session"] = False
            self.store.save_settings()

    def _prepare_session(self, username, mode):
        if mode == "home":
            self.store.switch_account(username)
        self.store.settings["remember_session"] = False
        self.store.settings["offline_folders"] = []  # Forget old persistent auto-download instructions.
        self.transfers = TransferControl(self.store.settings.get("transfer_paused"))
        identity = hashlib.sha256(username.strip().encode()).hexdigest()
        self._history_path = DATA_DIR / "accounts" / identity / "history.json"
        asset_path = self._history_path.with_name("asset-updates.json")
        self._asset_state = load_json(asset_path, {})
        self._save_asset_state = lambda state: save_json(asset_path, state)
        self.asset_tracker = None
        self._asset_checked_at = 0
        history = load_json(self._history_path, [])
        self.activity = history if isinstance(history, list) else []
        self.store.save_settings()

    def _login_complete(self, roots, access):
        self.pw_var.set("")
        self._build_main(roots, access)

    def _logout(self):
        if getattr(self, "transfers", None):
            self.transfers.cancel_all()
        if self.screen != "main":
            return
        self.session_epoch += 1
        self.screen = "logout"
        self.explorer_dashboard = False
        self.watcher_stop.set()
        if self.connection_timer_id:
            self.after_cancel(self.connection_timer_id)
            self.connection_timer_id = None
        self.connection_timer_started = False
        self._discard_saved_login()
        self.pw_var.set("")
        api = self.file_api
        if api:
            api._session_closed.set()
        if self.cloud_engine:
            self.cloud_engine.stop_event.set()
        self.access = {"roots": []}
        self._reset_progress()
        for child in self.winfo_children():
            child.destroy()
        tk.Label(self, text="Cerrando sesion...", bg=BG, fg=TEXT,
                 font=("Segoe UI", 16)).pack(expand=True)
        self._show_window()

        def cleanup():
            errors = []
            def attempt(action):
                try:
                    action()
                except Exception as exc:
                    errors.append(str(exc))
            if api:
                attempt(api.close_session)
            with self.session_threads_lock:
                threads = list(self.session_threads)
            for thread in threads:
                attempt(thread.join)
            for service in (self.office_server, self.command_server):
                if service:
                    attempt(service.stop)
            if self.office_server:
                SERVER_TOKENS.clear()
            if self.cloud_engine:
                attempt(self.cloud_engine.close)
            attempt(self.store.detach_account_mounts)
            def clear_links():
                from cloud_files import NativeCloud
                from shell_integration import publish_links
                publish_links(NativeCloud(), [])
            attempt(clear_links)
            self.post(self._logout_complete, errors)
        threading.Thread(target=cleanup, daemon=True).start()

    def _logout_complete(self, errors):
        self.file_api = self.web_api = None
        self.office_server = self.command_server = None
        self.cloud_engine = self.store.cloud_engine = None
        self.watcher_thread = self.offline_sync_thread = None
        self.roots, self.items, self.all_items = [], {}, []
        self.current_root, self.current_path = None, ""
        self.nav_back, self.nav_forward = [], []
        self.pending_changes.clear()
        self.file_snapshots.clear()
        self.download_paths.clear()
        self.upload_paths.clear()
        self.transfer_states.clear()
        self.activity.clear()
        self.asset_tracker = None
        self._asset_state = {}
        self._asset_checked_at = 0
        self.busy = self.progress_active = False
        self.watcher_stop = threading.Event()
        for name in ("progress", "tree", "activity_list", "activity_container", "path_entry", "back_btn", "forward_btn", "up_btn"):
            if hasattr(self, name):
                delattr(self, name)
        self._build_login()
        self.set_status("Sesion cerrada. Inicia sesion para continuar.")
        if errors:
            messagebox.showerror(APP_NAME, "La sesion se cerro, pero hubo un problema al desconectar: " + "; ".join(errors))

    def _build_main(self, roots, access=None, initial_root_id=None, initial_path=None):
        if self.store.settings.get("mode") == "office":
            self.explorer_dashboard = False
            return self._build_legacy_browser(roots, access, initial_root_id, initial_path)
        self.explorer_dashboard = True
        self.screen = "main"
        self.access = access or {"roots": []}
        self.roots = [root for root in roots if root.get("access") == "*" or not root.get("projectScoped", True) or bool(root.get("access"))]
        self.current_root = self.roots[0] if self.roots else None
        self.current_path = ""
        self.root_buttons = {}
        for child in self.winfo_children():
            child.destroy()
        self.geometry("1040x740")
        self.minsize(900, 650)
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill=BOTH, expand=True)
        header = tk.Frame(shell, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1)
        header.pack(fill=X, padx=20, pady=(20, 12))
        tk.Label(header, text="Remote Believe", bg=PANEL, fg=TEXT, font=("Segoe UI", 23, "bold"), anchor="w").pack(fill=X, padx=22, pady=(18, 0))
        tk.Label(header, text="Tus archivos estan en el Explorador de Windows", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 12), anchor="w").pack(fill=X, padx=22, pady=(3, 12))
        self.connection_var = tk.StringVar(value=self._connection_text())
        tk.Label(header, textvariable=self.connection_var, bg=PANEL, fg=BLUE, anchor="w", font=("Segoe UI", 10, "bold")).pack(fill=X, padx=22)
        session_row = tk.Frame(header, bg=PANEL)
        session_row.pack(fill=X, padx=22, pady=(2, 10))
        tk.Label(session_row, text=self.store.settings.get("username", ""), bg=PANEL, fg=MUTED, anchor="w").pack(side=LEFT)
        self.logout_btn = ttk.Button(session_row, text="Cerrar sesión", command=self._logout)
        self.logout_btn.pack(side=RIGHT)
        actions = tk.Frame(header, bg=PANEL)
        actions.pack(fill=X, padx=22, pady=(0, 18))
        self.open_explorer_btn = tk.Button(actions, text="Abrir mis carpetas", command=self._open_explorer_hub,
                                           bg=BLUE, fg="white", relief="flat", bd=0, padx=18, pady=10, font=("Segoe UI", 11, "bold"))
        self.open_explorer_btn.pack(side=LEFT, padx=(0, 8))
        ttk.Button(actions, text="Actualizar conexion y permisos", command=self._dashboard_refresh).pack(side=LEFT, padx=4)
        ttk.Button(actions, text="Subir pendientes", command=self._retry_pending).pack(side=LEFT, padx=4)
        ttk.Button(actions, text=self._theme_button_text(), command=self._toggle_theme).pack(side=RIGHT)
        if access_is_admin(self.access):
            ttk.Button(actions, text="Administracion", command=self._show_admin_panel).pack(side=RIGHT, padx=8)
        notebook = ttk.Notebook(shell)
        notebook.pack(fill=BOTH, expand=True, padx=20, pady=(0, 12))
        transfer_tab = tk.Frame(notebook, bg=BG)
        notebook.add(transfer_tab, text="Transferencias")
        self._install_asset_updates(notebook)
        folders = tk.Frame(transfer_tab, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1)
        folders.pack(fill=X, padx=20, pady=(0, 12))
        tk.Label(folders, text="Carpetas autorizadas", bg=PANEL, fg=TEXT, font=("Segoe UI", 12, "bold"), anchor="w").pack(fill=X, padx=18, pady=(12, 8))
        self.root_state_vars = {}
        for root in self.roots:
            row = tk.Frame(folders, bg=PANEL)
            row.pack(fill=X, padx=18, pady=3)
            tk.Label(row, text=root_display_label(root), bg=PANEL, fg=TEXT, anchor="w", font=("Segoe UI", 10, "bold")).pack(side=LEFT)
            value = tk.StringVar(value="Conectando al Explorador...")
            self.root_state_vars[root["id"]] = value
            tk.Label(row, textvariable=value, bg=PANEL, fg=MUTED, anchor="e").pack(side=RIGHT)
        if not self.roots:
            tk.Label(folders, text="No tienes carpetas asignadas. Solicita acceso al administrador.", bg=PANEL, fg=MUTED).pack(fill=X, padx=18)
        self.au_var = tk.BooleanVar(value=bool(self.store.settings.get("auto_upload", True)))
        tk.Checkbutton(folders, text="Subir archivos nuevos o modificados", variable=self.au_var, command=self._toggle_au,
                       bg=PANEL, fg=TEXT, selectcolor=PANEL, anchor="w").pack(fill=X, padx=14, pady=(8, 0))
        self.pending_var = tk.StringVar(value="Sin archivos pendientes.")
        tk.Label(folders, textvariable=self.pending_var, bg=PANEL, fg=MUTED, anchor="w").pack(fill=X, padx=18, pady=(3, 12))
        self._install_transfer_controls(transfer_tab)
        self.activity_container = tk.Frame(transfer_tab, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1)
        self.activity_container.pack(fill=BOTH, expand=True, padx=20, pady=(0, 12))
        tk.Label(self.activity_container, text="Actividad y transferencias", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(fill=X, padx=18, pady=(12, 8))
        self._install_history_filter(self.activity_container)
        self.activity_list = tk.Frame(self.activity_container, bg=PANEL)
        self.activity_list.pack(fill=BOTH, expand=True, padx=6, pady=(0, 12))
        self._refresh_activity_panel()
        self.bottom_bar = tk.Frame(shell, bg=BG)
        self.bottom_bar.pack(fill=X, padx=20, pady=(0, 16))
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(self.bottom_bar, variable=self.progress_var, maximum=100, length=160)
        self.progress.pack(side=LEFT)
        self.progress_text_var = tk.StringVar()
        tk.Label(self.bottom_bar, textvariable=self.progress_text_var, bg=BG, fg=BLUE).pack(side=LEFT, padx=10)
        self.status_var = tk.StringVar(value="Conectando tus carpetas al Explorador...")
        tk.Label(self.bottom_bar, textvariable=self.status_var, bg=BG, fg=MUTED, anchor="w").pack(side=LEFT, fill=X, expand=True)
        self.bind("<Control-l>", lambda event: "break")
        self.bind("<Alt-d>", lambda event: "break")
        self._start_command_server()
        if self.cloud_engine:
            self._update_dashboard_status()
            self.after(0, self._asset_check)
        elif self.roots:
            self._worker(self._connect_explorer)
        else:
            self.set_status("Sin carpetas asignadas.")
            self.store.detach_account_mounts()
            from cloud_files import NativeCloud
            from shell_integration import publish_links
            publish_links(NativeCloud(), [])
        if not getattr(self, "connection_timer_started", False):
            self.connection_timer_started = True
            self._schedule_connection_check()

    def _connect_explorer(self):
        from cloud_files import CloudFilesEngine
        self.store.reconcile_local_deletions(self.roots)
        self.mount_messages = self.store.mount_roots(self.roots)
        for message in self.mount_messages:
            self.post(self._log_activity, "Discos locales", message)
        engine = CloudFilesEngine(self.store, self.file_api,
                                  lambda root_id, rp: can_access_remote_path(self.access, root_id, rp),
                                  parse_remote_modified_at, self._cloud_downloaded, self._cloud_report)
        self.cloud_engine = self.store.cloud_engine = engine
        try:
            from shell_integration import publish_links
            publish_links(engine.native, [])
            engine.start(self.roots, background=False)
            engine.catalog_executor.submit(engine.refresh)
            self._publish_explorer_links()
            self.post(self.set_status, "Listo. Abre tus carpetas desde el Explorador.")
            self.post(self._log_activity, "Explorador", "Tus carpetas ya estan disponibles.", "", "success")
            self.post(self._prune_unsafe_pending_uploads)
            self.post(self._restore_pending_upload_states)
            self.post(self._start_watcher)
            self.post(self._start_offline_folder_sync)
            self.post(self._update_dashboard_status)
            self.post(self._asset_check)
            if not getattr(self, "connection_timer_started", False):
                self.connection_timer_started = True
                self.post(self._schedule_connection_check)
        except Exception:
            engine.close()
            self.cloud_engine = self.store.cloud_engine = None
            raise

    def _cloud_downloaded(self, root, rp, item, path, digest):
        self.store.mark_downloaded(root["id"], rp, item, path)
        self.store.manifest[self.store.key(root["id"], rp)].update({"cloudPlaceholder": True, "sha256": digest})
        if master_kind(rp):
            self.store.manifest[self.store.key(root["id"], rp)]["assetDownloaded"] = True
        self.store.save_manifest()
        self.file_snapshots[local_path_key(path)] = local_snapshot(path)
        self.pending_changes.pop(local_path_key(path), None)

    def _cloud_report(self, state, message, detail):
        if self._closing_app or self.screen != "main":
            return
        if state == "progress":
            done, total = detail
            self.post(self._set_progress, done, total, "Descargando {} / {}".format(format_size(done), format_size(total)))
        else:
            kind = "Descarga" if state in ("download", "success", "cancelled") or message.startswith("No se pudo abrir ") else "Explorador"
            self.post(self._log_activity, kind, message, detail, state)
            if state in ("success", "error", "cancelled"):
                self.post(self._reset_progress)
            if state == "cancelled":
                self.post(self.set_status, "Descarga detenida; el archivo sigue disponible como proxy.")

    def _publish_explorer_links(self):
        from shell_integration import publish_links, register_namespace
        entries = []
        if not self.cloud_engine:
            return
        with self.cloud_engine.lock:
            remote_items = list(self.cloud_engine.remote_items.items())
        for root in self.roots:
            if root["id"] not in self.cloud_engine.connections:
                continue
            for key, item in remote_items:
                prefix = root["id"] + "::"
                if not key.startswith(prefix):
                    continue
                rp = key[len(prefix):]
                if "/" in rp or not can_access_remote_path(self.access, root["id"], rp):
                    continue
                entries.append(("{} - {}".format(root_drive_letter(root), item["name"]), self.store.explorer_path(root, rp)))
        self.explorer_hub = publish_links(self.cloud_engine.native, entries)
        app_exe = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)
        register_namespace(app_exe, self.explorer_hub)

    def _open_explorer_hub(self):
        if not self.cloud_engine or not self.cloud_engine.connections:
            self.set_status("Las carpetas todavia no estan listas. Revisa la conexion y pulsa Actualizar.")
            return
        from shell_integration import hub_path
        self._open_path(getattr(self, "explorer_hub", hub_path()))

    def _update_dashboard_status(self):
        if not self.explorer_dashboard or not hasattr(self, "pending_var"):
            return
        pending = self.store.pending_uploads()
        self.pending_var.set("{} archivo(s) pendientes de subir.".format(len(pending)) if pending else "Sin archivos pendientes.")
        engine = self.cloud_engine
        for root in self.roots:
            value = self.root_state_vars.get(root["id"])
            if value:
                ready = engine and root["id"] in engine.connections
                value.set("Disponible en el Explorador · descarga al abrir" if ready else self.store.mount_errors.get(root["id"], "Pendiente de conexion"))

    def _retry_pending(self):
        def work():
            self._scan_for_offline_changes()
            self._process_outbox()
            self.post(self._update_dashboard_status)
        self._worker(work)

    def _schedule_connection_check(self):
        if self._closing_app or self.screen != "main":
            self.connection_timer_started = False
            return
        if self.explorer_dashboard and not self.busy:
            self._dashboard_refresh(quiet=True)
            self._asset_check()
        self.connection_timer_id = self.after(30000, self._schedule_connection_check)

    def _dashboard_refresh(self, quiet=False):
        if self.screen != "main":
            return
        def work():
            try:
                access = self.file_api.get_access()
                changed = access != self.access
                if changed and self.cloud_engine:
                    self.cloud_engine.close()
                    self.cloud_engine = self.store.cloud_engine = None
                    self.asset_tracker = None
                    self._asset_checked_at = 0
                if changed:
                    self.post(self._build_main, access.get("roots", []), access)
                    return
                if not self.cloud_engine:
                    self._connect_explorer()
                else:
                    self.cloud_engine.refresh()
                    self._publish_explorer_links()
                self.post(self.connection_var.set, self._connection_text())
                self.post(self._update_dashboard_status)
            except Exception as exc:
                self.post(self.connection_var.set, "Sin conexion · reintentando automaticamente")
                if not quiet:
                    raise
        if quiet:
            self._start_session_thread(work)
        else:
            self._worker(work)

    def _build_legacy_browser(self, roots, access=None, initial_root_id=None, initial_path=None):
        self.screen = "main"
        for c in self.winfo_children(): c.destroy()
        self.roots = roots
        self.access = access or {"roots": []}
        self.current_root = next((root for root in roots if root.get("id") == initial_root_id), None) if initial_root_id else None
        self.current_root = self.current_root or (roots[0] if roots else None)
        self.current_path = initial_path or ""

        mode = self.store.settings["mode"]

        if mode == "home" and sys.platform.startswith("win"):
            self.mount_messages = self.store.mount_roots(self.roots)

        shell = tk.Frame(self, bg=BG)
        shell.pack(fill=BOTH, expand=True)

        # Sidebar
        sb = tk.Frame(shell, bg=SIDEBAR, width=270)
        sb.pack(side=LEFT, fill=tk.Y)
        sb.pack_propagate(False)

        tk.Label(sb, text="Remote Believe", bg=SIDEBAR, fg=TEXT, font=("Segoe UI", 17, "bold"), anchor="w").pack(fill=X, padx=20, pady=(22, 0))
        tk.Label(sb, text=self.store.settings["username"], bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(fill=X, padx=20)

        self.connection_var = tk.StringVar(value=self._connection_text())
        conn_card = tk.Frame(sb, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1)
        conn_card.pack(fill=X, padx=14, pady=(12, 16))
        tk.Label(conn_card, text="CONEXION", bg=PANEL, fg=MUTED, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill=X, padx=12, pady=(10, 0))
        tk.Label(conn_card, textvariable=self.connection_var, bg=PANEL, fg=OFFICE_COL if mode == "office" else BLUE, font=("Segoe UI", 10, "bold"), anchor="w", wraplength=220).pack(fill=X, padx=12, pady=(2, 2))
        conn_detail = "Puerto {}".format(PEER_PORT) if mode == "office" else self._office_ip()
        tk.Label(conn_card, text=conn_detail, bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w", wraplength=220).pack(fill=X, padx=12, pady=(0, 10))
        conn_actions = tk.Frame(conn_card, bg=PANEL)
        conn_actions.pack(fill=X, padx=10, pady=(0, 10))
        tk.Button(conn_actions, text="Probar conexion", command=self._test_connection, bg=CONTROL_BG, fg=TEXT, activebackground=CONTROL_ACTIVE, activeforeground=TEXT, relief="flat", bd=0, padx=8, pady=6, font=("Segoe UI", 9, "bold")).pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        tk.Button(conn_actions, text="Abrir local", command=self._open_current_local_folder, bg=CONTROL_BG, fg=TEXT, activebackground=CONTROL_ACTIVE, activeforeground=TEXT, relief="flat", bd=0, padx=8, pady=6, font=("Segoe UI", 9, "bold")).pack(side=LEFT, fill=X, expand=True, padx=(4, 0))
        tk.Button(conn_card, text="Ver actividad", command=self._toggle_activity_panel, bg=BLUE_SOFT, fg=BLUE, activebackground=CONTROL_ACTIVE, activeforeground=BLUE, relief="flat", bd=0, padx=8, pady=7, font=("Segoe UI", 9, "bold")).pack(fill=X, padx=10, pady=(0, 6))
        tk.Button(conn_card, text=self._theme_button_text(), command=self._toggle_theme, bg=CONTROL_BG, fg=TEXT, activebackground=CONTROL_ACTIVE, activeforeground=TEXT, relief="flat", bd=0, padx=8, pady=7, font=("Segoe UI", 9, "bold")).pack(fill=X, padx=10, pady=(0, 10))
        self.logout_btn = tk.Button(conn_card, text="Cerrar sesión", command=self._logout, bg=CONTROL_BG, fg=TEXT,
                                    relief="flat", bd=0, padx=8, pady=7, font=("Segoe UI", 9, "bold"))
        self.logout_btn.pack(fill=X, padx=10, pady=(0, 10))

        tk.Label(sb, text="DISCOS", bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 8, "bold"), anchor="w").pack(fill=X, padx=20, pady=(0, 6))
        self.roots_frame = tk.Frame(sb, bg=SIDEBAR)
        self.roots_frame.pack(fill=X, padx=12)
        for r in self.roots:
            label = self.store.display_label(r) if mode == "home" else root_display_label(r)
            btn = tk.Button(self.roots_frame, text="{}  {}".format(ICON_FOLDER, label),
                            command=lambda root=r: self._navigate(root, "", True), bg=SIDEBAR, fg=TEXT, relief="flat", bd=0, padx=12, pady=11, anchor="w", font=("Segoe UI", 10, "bold"))
            btn.pack(fill=X, pady=3)
            self.root_buttons[r["id"]] = btn

        if mode == "home":
            tk.Label(sb, text="Se conservan las rutas X: y R:.\nEn discos locales ocupados se enlazan las carpetas de Believe.",
                     bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 8), justify=LEFT,
                     anchor="w", wraplength=230).pack(fill=X, padx=20, pady=(6, 0))

        if mode == "home":
            tk.Frame(sb, bg=LINE, height=1).pack(fill=X, padx=16, pady=14)
            self.au_var = tk.BooleanVar(value=bool(self.store.settings.get("auto_upload", True)))
            tk.Checkbutton(sb, text="Auto subir cambios", variable=self.au_var, command=self._toggle_au, bg=SIDEBAR, fg=TEXT, selectcolor=SIDEBAR, relief="flat", anchor="w").pack(fill=X, padx=16)


        if self.access.get("role") in ["admin", "superuser"]:
            tk.Frame(sb, bg=LINE, height=1).pack(fill=tk.X, padx=16, pady=14)
            tk.Button(sb, text="⚙ Administración", command=self._show_admin_panel, bg=SIDEBAR, fg=TEXT, relief="flat", bd=0, padx=12, pady=11, anchor="w", font=("Segoe UI", 10, "bold")).pack(fill=tk.X, padx=12)

        # Main Area
        main = tk.Frame(shell, bg=PANEL)
        main.pack(side=LEFT, fill=BOTH, expand=True)

        toolbar = tk.Frame(main, bg=PANEL)
        toolbar.pack(fill=X, padx=16, pady=(14, 8))
        self.path_var = tk.StringVar()
        nav = tk.Frame(toolbar, bg=PANEL)
        nav.pack(side=LEFT, padx=(0, 10))
        self.back_btn = ttk.Button(nav, text=ICON_BACK, style="Icon.TButton", command=self._go_back)
        self.forward_btn = ttk.Button(nav, text=ICON_FORWARD, style="Icon.TButton", command=self._go_forward)
        self.up_btn = ttk.Button(nav, text=ICON_UP, style="Icon.TButton", command=self._go_up)
        self.back_btn.pack(side=LEFT, padx=(0, 4))
        self.forward_btn.pack(side=LEFT, padx=(0, 4))
        self.up_btn.pack(side=LEFT)

        search_wrap = tk.Frame(toolbar, bg=CONTROL_BG)
        search_wrap.pack(side=RIGHT, padx=(10, 0))
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        tk.Entry(search_wrap, textvariable=self.filter_var, width=24, relief="flat", bg=CONTROL_BG, fg=TEXT, insertbackground=BLUE).pack(side=LEFT, padx=10, ipady=6)
        self.refresh_btn = ttk.Button(toolbar, text=ICON_REFRESH, style="Icon.TButton", command=self._full_refresh)
        self.refresh_btn.pack(side=RIGHT)

        address = tk.Frame(main, bg=CONTROL_BG, highlightbackground=CARD_BORDER, highlightthickness=1)
        address.pack(fill=X, padx=16, pady=(0, 8))
        tk.Label(address, text="Ruta", bg=CONTROL_BG, fg=MUTED).pack(side=LEFT, padx=(10, 6))
        ttk.Button(address, text="Copiar", command=self._copy_address).pack(side=RIGHT, padx=(4, 6), pady=4)
        ttk.Button(address, text="Ir", command=self._submit_address).pack(side=RIGHT, pady=4)
        self.path_entry = tk.Entry(address, textvariable=self.path_var, width=12,
                                   relief="flat", bg=CONTROL_BG, fg=TEXT, insertbackground=BLUE)
        self.path_entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 8), ipady=8)
        self.path_entry.bind("<Return>", self._submit_address)
        self.path_entry.bind("<Escape>", self._reset_address)
        self.path_entry.bind("<Control-a>", self._select_address)
        self.path_entry.bind("<Button-3>", self._address_context_menu)
        self.bind("<Control-l>", self._select_address)
        self.bind("<Alt-d>", self._select_address)

        tframe = tk.Frame(main, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1)
        tframe.pack(fill=BOTH, expand=True, padx=16, pady=(0, 10))

        self.tree = ttk.Treeview(tframe, columns=("name", "size", "modified", "status"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="")
        self.tree.heading("name", text="Nombre")
        self.tree.heading("size", text="Tamano")
        self.tree.heading("modified", text="Modificado")
        self.tree.heading("status", text="Estado")
        self.tree.column("#0", width=44, anchor="center", stretch=False)
        self.tree.column("name", width=460)
        self.tree.column("size", width=100)
        self.tree.column("modified", width=150)
        self.tree.column("status", width=130)
        self.tree.tag_configure("odd", background=ROW_ALT)
        self.tree.tag_configure("local", foreground="#1f7a3c")
        self.tree.tag_configure("cloud", foreground=CLOUD)
        self.tree.tag_configure("office", foreground=OFFICE_COL)
        self.tree.tag_configure("downloading", foreground=BLUE)
        self.tree.tag_configure("uploading", foreground="#b36b00")
        self.tree.tag_configure("error", foreground=RED)
        self.tree.tag_configure("denied", foreground="#b36b00")

        sb_scroll = ttk.Scrollbar(tframe, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb_scroll.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb_scroll.pack(side=RIGHT, fill=tk.Y)

        self.tree.bind("<Double-1>", lambda _: self._open_selected())
        self.tree.bind("<Button-3>", self._show_context_menu)

        self.activity_container = tk.Frame(main, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1)
        if self.activity_visible:
            self.activity_container.pack(fill=X, padx=16, pady=(0, 10))
        activity_head = tk.Frame(self.activity_container, bg=PANEL)
        activity_head.pack(fill=X, padx=12, pady=(10, 2))
        tk.Label(activity_head, text="Actividad reciente", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").pack(side=LEFT, fill=X, expand=True)
        tk.Label(activity_head, text="descargas, subidas y errores", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="e").pack(side=RIGHT)
        self._install_history_filter(self.activity_container)
        self.activity_list = tk.Frame(self.activity_container, bg=PANEL)
        self.activity_list.pack(fill=X, padx=6, pady=(0, 8))
        self._refresh_activity_panel()

        bottom = tk.Frame(main, bg=PANEL)
        self.bottom_bar = bottom
        bottom.pack(fill=X, padx=16, pady=(0, 12))
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(bottom, mode="determinate", variable=self.progress_var, maximum=100, length=180)
        self.progress.pack(side=LEFT)
        self.progress_text_var = tk.StringVar(value="")
        tk.Label(bottom, textvariable=self.progress_text_var, bg=PANEL, fg=BLUE, font=("Segoe UI", 9, "bold"), anchor="w").pack(side=LEFT, padx=(10, 8))
        self.status_var = tk.StringVar(value="Listo.")
        tk.Label(bottom, textvariable=self.status_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(side=LEFT, padx=(10, 0), fill=X, expand=True)

        if mode == "office":
            self._start_office_server()
        elif mode == "home":
            self._start_command_server()
            self._prune_unsafe_pending_uploads()
            self._restore_pending_upload_states()
            self._start_watcher()
            self._clean_placeholder_tree()
            self._start_shared_folder_mounts()
            self._start_offline_folder_sync()

        self._start_heartbeat()
        self._navigate(self.current_root, self.current_path, push=False)
        for message in getattr(self, "mount_messages", []):
            self._log_activity("Discos locales", message)

    def _address_text(self):
        if not self.current_root:
            return ""
        if self.store.settings.get("mode") == "office":
            return str(root_base_path(self.current_root).joinpath(*self.current_path.split("/")))
        return str(self.store.explorer_path(self.current_root, self.current_path))

    def _reset_address(self, _event=None):
        self.path_var.set(self._address_text())
        return "break"

    def _select_address(self, _event=None):
        if self.screen == "main" and hasattr(self, "path_entry") and self.path_entry.winfo_exists():
            self.path_entry.focus_set()
            self.path_entry.select_range(0, END)
            self.path_entry.icursor(END)
        return "break"

    def _copy_address(self):
        self.clipboard_clear()
        self.clipboard_append(self.path_var.get())
        self.set_status("Ruta copiada.")

    def _address_context_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Cortar", command=lambda: self.path_entry.event_generate("<<Cut>>"))
        menu.add_command(label="Copiar", command=lambda: self.path_entry.event_generate("<<Copy>>"))
        menu.add_command(label="Pegar", command=lambda: self.path_entry.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Seleccionar todo", command=self._select_address)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()
        return "break"

    def _submit_address(self, _event=None):
        try:
            root, rp = resolve_browser_address(self.store, self.roots, self.path_var.get(), self.current_root, self.current_path)
            if not can_access_remote_path(self.access, root["id"], rp):
                raise ValueError("Sin permiso para esta ruta.")
        except ValueError as exc:
            self.set_status(str(exc))
            return "break"

        def work():
            if self.store.settings.get("mode") == "office":
                if not root_base_path(root).joinpath(*rp.split("/")).is_dir():
                    raise RBError("La ruta no existe o no es una carpeta.")
            else:
                # Validate remotely before changing the current folder/history.
                # Older office clients returned [] for a nonexistent directory.
                if rp:
                    parent = self.file_api.list_folder(root["id"], parent_remote(rp))
                    name = rp.rsplit("/", 1)[-1].casefold()
                    if not any(item.get("type") == "directory" and item.get("name", "").casefold() == name
                               for item in parent.get("items", [])):
                        raise RBError("La ruta no existe o no es una carpeta.")
                self.file_api.list_folder(root["id"], rp)
            self.post(self._navigate, root, rp)
        self._worker(work)
        return "break"

    def _toggle_au(self):
        self.store.settings["auto_upload"] = self.au_var.get()
        self.store.save_settings()
        if self.au_var.get():
            self._start_watcher()

    def _start_command_server(self):
        if self.command_server:
            return
        self.command_server = CommandServer(COMMAND_PORT, self)
        ok, msg = self.command_server.start()
        if ok:
            self.set_status(msg)
        else:
            self.set_status("Menu Explorer no disponible: {}".format(msg))

    def enqueue_explorer_command(self, action, target_path):
        action = str(action or "").strip().lower()
        if action not in {"download", "prepare", "upload", "unpin"}:
            return False, "Accion no reconocida"
        if self.store.settings.get("mode") != "home":
            return False, "Esta opcion se usa desde el cliente en modo Casa"
        if not self.file_api:
            return False, "Todavia no hay conexion con el PC de oficina"
        self.post(self._handle_explorer_action, action, target_path)
        return True, "Orden recibida"

    def _handle_explorer_action(self, action, target_path):
        def work():
            root, rp, _local = target_to_root_path(self.store, self.roots, target_path)
            if not root:
                raise RBError("Ese archivo no pertenece a una unidad o carpeta local de Remote Believe.")
            if not can_access_remote_path(self.access, root["id"], rp):
                raise RBError("Sin permiso para esta ruta.")

            if action == "upload":
                local = self.store.local_path(root, rp)
                entry = self._queue_pending_upload(root, rp, local, reason="manual")
                if entry:
                    self._upload_outbox_entry(entry)
                return
            if action == "unpin":
                self.store.remove_offline_folder(root["id"], rp)
                self.transfers.cancel_path("download", root["id"], rp)
                self.post(self.set_status, "Descargas detenidas para esta carpeta; se conservan los archivos.")
                return

            if action == "prepare":
                if not is_maya_ascii_name(rp):
                    raise RBError("Preparar shot solo funciona con archivos .ma.")
                self.post(self.set_status, "Preparando shot desde Explorer: {}".format(rp))
                self._notify("Remote Believe", "Preparando shot completo...")
                self._prepare_shot_for_remote(root, rp)
                self._notify("Remote Believe", "Shot preparado.")
                return

            item = self._find_remote_item(root, rp) if rp else {"name": root.get("name", root["id"]), "type": "directory"}
            if item.get("type") == "directory":
                self.post(self.set_status, "Descargando carpeta desde Explorer: {}".format(rp or root.get("name", root["id"])))
                self._notify("Remote Believe", "Descargando carpeta...")
                self._pin_offline_folder(root, rp, announce=True)
                count = self._download_remote_folder(root, rp)
                self.post(self.set_status, "Carpeta descargada. {} archivo(s).".format(count))
                self._notify("Remote Believe", "Carpeta descargada: {} archivo(s).".format(count))
            else:
                self.post(self.set_status, "Descargando desde Explorer: {}".format(rp))
                self._notify("Remote Believe", "Descargando {}...".format(item.get("name", "archivo")))
                downloaded = self._download_remote_file(root, rp)
                if not downloaded:
                    raise RBError("No se pudo descargar el archivo.")
                self.post(self.set_status, "Archivo descargado: {}".format(rp))
                self._notify("Remote Believe", "Archivo descargado.")
            self.post(self._reset_progress)
            self.post(self._refresh)

        self._worker(work)

    def _materialize_items(self, root, remote_path, items, save=True):
        if self.cloud_engine:
            self.cloud_engine.populate(root["id"], remote_path)
            return
        local_dir = self.store.local_path(root, remote_path)
        local_dir.mkdir(parents=True, exist_ok=True)
        marker_entries = {}
        for item in items:
            name = item.get("name", "")
            if not name:
                continue
            child_remote = join_remote(remote_path, name)
            child_local = self.store.local_path(root, child_remote)
            if item.get("type") == "directory":
                child_local.mkdir(parents=True, exist_ok=True)
                continue
            current = self.store.manifest.get(self.store.key(root["id"], child_remote), {})
            if current.get("placeholder") is False and child_local.exists():
                continue
            if child_local.exists() and not current.get("placeholder") and child_local.stat().st_size > 0:
                continue
            if not child_local.exists():
                child_local.parent.mkdir(parents=True, exist_ok=True)
                child_local.touch()
            self.store.mark_placeholder(root["id"], child_remote, item, child_local)
            marker_entries[child_local.name] = {
                "rootId": root["id"],
                "remotePath": child_remote,
                "size": item.get("size"),
                "modifiedAt": item.get("modifiedAt"),
                "placeholder": True,
            }
        self.store.update_folder_marker(local_dir, marker_entries)
        if save:
            self.store.save_manifest()

    def _ensure_local_folder(self, root, remote_path):
        self.store.ensure_destination(root, remote_path, directory=True).mkdir(parents=True, exist_ok=True)

    def _start_shared_folder_mounts(self):
        if not self.store.shared_drives:
            return
        def work():
            for root in list(self.roots):
                if root["id"] not in self.store.shared_drives:
                    continue
                try:
                    data = self.file_api.list_folder(root["id"], "")
                    for item in data.get("items", []):
                        if item.get("type") != "directory":
                            continue
                        rp = item.get("name", "")
                        if not rp or not can_access_remote_path(self.access, root["id"], rp):
                            continue
                        try:
                            self.store.ensure_destination(root, rp, directory=True)
                        except Exception as exc:
                            self.post(self._log_activity, "Conflicto de carpeta", str(exc), rp, "error")
                except Exception as exc:
                    self.post(self._log_activity, "Discos locales", "No se pudieron enlazar las carpetas de {}".format(root_display_label(root)), str(exc), "error")
        self._start_session_thread(work)

    def _mirror_root_starts(self, root):
        access = root_access_record(self.access, root["id"]) or {}
        allowed = access.get("access")
        if allowed == "*" or not access.get("projectScoped"):
            return [""]
        if isinstance(allowed, list):
            return [str(item) for item in allowed if item]
        return []

    def _start_tree_mirror(self):
        def work():
            try:
                total = 0
                for root in self.roots:
                    queue = list(self._mirror_root_starts(root))
                    for start in queue:
                        self.store.local_path(root, start).mkdir(parents=True, exist_ok=True)
                    while queue and total < MAX_MIRROR_ITEMS:
                        remote_path = queue.pop(0)
                        if not can_access_remote_path(self.access, root["id"], remote_path):
                            continue
                        data = self.file_api.list_folder(root["id"], remote_path)
                        items = data.get("items", [])
                        self._materialize_items(root, remote_path, items, save=False)
                        total += len(items)
                        if total % 250 == 0:
                            self.store.save_manifest()
                            self.post(self.set_status, "Creando proxies en Explorer: {} items...".format(total))
                        for item in items:
                            if item.get("type") == "directory":
                                queue.append(join_remote(remote_path, item.get("name", "")))
                self.store.save_manifest()
                self.post(self.set_status, "Explorer actualizado con proxies remotos.")
                self.post(self._refresh)
            except Exception as exc:
                self.post(self.set_status, "No se pudo actualizar Explorer: {}".format(exc))

        self._start_session_thread(work)

    def _clean_placeholder_tree(self):
        removed = 0
        for root in self.roots:
            root_dir = self.store.root_dir(root)
            if not root_dir.exists():
                continue
            for marker in list(root_dir.rglob(PLACEHOLDER_MARKER)):
                marker_data = load_json(marker, {"items": {}})
                for name in list((marker_data.get("items") or {}).keys()):
                    candidate = marker.parent / name
                    if candidate.exists() and candidate.is_file() and candidate.stat().st_size == 0:
                        candidate.unlink(missing_ok=True)
                        removed += 1
                marker.unlink(missing_ok=True)

            for path in sorted(root_dir.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                if path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass

        for key, entry in list(self.store.manifest.items()):
            if entry.get("placeholder"):
                self.store.manifest.pop(key, None)
        self.store.save_manifest()
        if removed:
            self.set_status("Proxies no descargados limpiados: {}.".format(removed))

    def _navigate(self, root, rp, push=True):
        if not root: return
        rp = clean_browser_relative(rp)
        if not can_access_remote_path(self.access, root["id"], rp):
            self.set_status("Sin permiso para esta ruta.")
            return
        previous = (self.current_root["id"], self.current_path) if self.current_root else None
        target = (root["id"], rp)
        if push and previous and previous != target:
            self.nav_back.append(previous)
            self.nav_forward.clear()
        self.current_root = root
        self.current_path = rp
        self._reset_address()

        for rid, btn in self.root_buttons.items():
            btn.configure(bg=BLUE_SOFT if rid == root["id"] else SIDEBAR, fg=BLUE if rid == root["id"] else TEXT)

        self._refresh()
        self._update_nav_buttons()

    def _root_by_id(self, root_id):
        return next((root for root in self.roots if root["id"] == root_id), None)

    def _update_nav_buttons(self):
        if hasattr(self, "back_btn"):
            self.back_btn.configure(state=tk.NORMAL if self.nav_back else tk.DISABLED)
            self.forward_btn.configure(state=tk.NORMAL if self.nav_forward else tk.DISABLED)
            self.up_btn.configure(state=tk.NORMAL if self.current_path else tk.DISABLED)

    def _go_back(self):
        if not self.nav_back: return
        current = (self.current_root["id"], self.current_path)
        self.nav_forward.append(current)
        root_id, rp = self.nav_back.pop()
        self._navigate(self._root_by_id(root_id), rp, push=False)

    def _go_forward(self):
        if not self.nav_forward: return
        current = (self.current_root["id"], self.current_path)
        self.nav_back.append(current)
        root_id, rp = self.nav_forward.pop()
        self._navigate(self._root_by_id(root_id), rp, push=False)

    def _full_refresh(self):
        if self.explorer_dashboard:
            self._dashboard_refresh()
            return
        def work():
            self.post(self.set_status, "Actualizando discos y permisos...")
            try:
                mode = self.store.settings["mode"]
                if mode == "office":
                    un = self.store.settings.get("username")
                    sync_data = load_syncremote()
                    users = sync_data.get("users", {})
                    if un not in users:
                        raise Exception("Usuario no encontrado en syncremote.json")
                    access = build_access(sync_data, un)
                else:
                    access = self.file_api.get_access()

                if not access or not access.get("username"):
                    raise Exception("Sesion invalida")

                self.access = access
                self.roots = access.get("roots", [])
                self.transfer_states = {key: value for key, value in self.transfer_states.items() if value.get("state") in ("downloading", "uploading", "pending", "denied", "error")}
                if self.current_root:
                    self.current_root = next((r for r in self.roots if r["id"] == self.current_root["id"]), self.current_root)

                self.post(self._rebuild_sidebar)
                if hasattr(self, "connection_var"):
                    self.post(self.connection_var.set, self._connection_text())

                if self.current_root:
                    root_still_exists = any(r["id"] == self.current_root["id"] for r in self.roots)
                    if not root_still_exists:
                        if self.roots:
                            self.post(self._navigate, self.roots[0], "")
                        else:
                            self.current_root = None
                            self.current_path = ""
                            self.post(self._render_items, [])
                    else:
                        self.post(self._refresh)
                elif self.roots:
                    self.post(self._navigate, self.roots[0], "")

                self.post(self.set_status, "Actualizado.")
            except Exception as e:
                self.post(self.set_status, f"Error actualizando permisos: {e}")

        self._worker(work)

    def _rebuild_sidebar(self):
        if not hasattr(self, "roots_frame"):
            return
        for widget in self.roots_frame.winfo_children():
            widget.destroy()

        self.root_buttons = {}
        if self.store.settings.get("mode") == "home" and sys.platform.startswith("win"):
            self.mount_messages = self.store.mount_roots(self.roots)
        for root in self.roots:
            label = self.store.display_label(root) if self.store.settings.get("mode") == "home" else root_display_label(root)
            btn = tk.Button(
                self.roots_frame,
                text="{}  {}".format(ICON_FOLDER, label),
                command=lambda r=root: self._navigate(r, "", True),
                bg=SIDEBAR,
                fg=TEXT,
                relief="flat",
                bd=0,
                padx=12,
                pady=11,
                anchor="w",
                font=("Segoe UI", 10, "bold"),
            )
            btn.pack(fill=X, pady=3)
            self.root_buttons[root["id"]] = btn

        if self.current_root and self.current_root["id"] in self.root_buttons:
            self.root_buttons[self.current_root["id"]].configure(bg=BLUE_SOFT, fg=BLUE)
        self._reset_address()
        if self.store.settings.get("mode") == "home":
            self._start_shared_folder_mounts()

    def _go_up(self):
        if self.current_path:
            self._navigate(self.current_root, parent_remote(self.current_path))

    def _refresh(self):
        if self.explorer_dashboard:
            self._update_dashboard_status()
            return
        if not self.current_root: return
        mode = self.store.settings["mode"]

        def work():
            self.post(self.set_status, "Leyendo carpeta...")
            if mode == "office":
                # Leer disco local directo
                if not can_access_remote_path(self.access, self.current_root["id"], self.current_path):
                    raise RBError("Sin permiso para esta ruta")
                local = root_base_path(self.current_root).joinpath(*self.current_path.split("/"))
                items = []
                if local.exists() and local.is_dir():
                    for p in local.iterdir():
                        if p.name.startswith("~$") or p.name in {"thumbs.db", "desktop.ini"}: continue
                        item_rp = join_remote(self.current_path, p.name)
                        if not can_access_remote_path(self.access, self.current_root["id"], item_rp): continue
                        s = p.stat()
                        items.append({"name": p.name, "type": "directory" if p.is_dir() else "file", "size": s.st_size if p.is_file() else None, "modifiedAt": format_remote_modified_at(s.st_mtime)})
                self.post(self._render_items, items)
            else:
                # Leer desde OfficeApi
                try:
                    data = self.file_api.list_folder(self.current_root["id"], self.current_path)
                except Exception as exc:
                    local_items = self.store.local_items_for_folder(self.current_root, self.current_path)
                    if local_items:
                        self.post(self._log_activity, "Conexion", "Sin conexion con oficina. Mostrando cache local.", self._friendly_transfer_error(exc), "error")
                        self.post(self._render_items, local_items)
                        return
                    raise
                self._ensure_local_folder(self.current_root, self.current_path)
                items = data.get("items", [])
                remote_names = {item.get("name", "").lower() for item in items}
                for local_item in self.store.local_items_for_folder(self.current_root, self.current_path):
                    if local_item.get("name", "").lower() not in remote_names:
                        items.append(local_item)
                self.post(self._render_items, items)
                if self.store.is_inside_offline_folder(self.current_root["id"], self.current_path):
                    for item in data.get("items", []):
                        if item.get("type") == "file":
                            rp = join_remote(self.current_path, item.get("name", ""))
                            if not self.store.is_local_current(self.current_root, self.current_root["id"], rp, item):
                                self._request_offline_folder_sync("vista")
                                break

        self._worker(work)

    def _render_items(self, items):
        self.all_items = sorted(items, key=lambda item: (item.get("type") != "directory", item.get("name", "").lower()))
        self._apply_filter()
        self.set_status("{} elemento(s).".format(len(self.all_items)))

    def _apply_filter(self):
        if self.explorer_dashboard:
            self._update_dashboard_status()
            return
        if not hasattr(self, "tree"): return
        self.tree.delete(*self.tree.get_children())
        self.items = {}
        mode = self.store.settings["mode"]
        query = self.filter_var.get().strip().lower() if hasattr(self, "filter_var") else ""
        shown = [item for item in self.all_items if not query or query in item.get("name", "").lower()]

        for index, item in enumerate(shown):
            rp = join_remote(self.current_path, item["name"])
            icon = ICON_FOLDER if item.get("type") == "directory" else ICON_FILE
            if mode == "office":
                status = "{} Oficina".format(ICON_OFFICE)
                tags = ("office",)
                image_key = "office"
            else:
                transfer = self.transfer_states.get(self._transfer_key(self.current_root["id"], rp))
                if transfer:
                    state = transfer.get("state")
                    if state == "downloading":
                        status = "{} Descargando".format(ICON_CLOUD)
                        tags = ("downloading",)
                        image_key = "download"
                    elif state == "uploading":
                        status = "{} Subiendo".format(ICON_UPLOAD)
                        tags = ("uploading",)
                        image_key = "upload"
                    elif state == "pending":
                        status = "{} Pendiente".format(ICON_UPLOAD)
                        tags = ("uploading",)
                        image_key = "upload"
                    elif state == "denied":
                        status = "{} Sin permiso".format(ICON_DENIED)
                        tags = ("denied",)
                        image_key = "denied"
                    else:
                        status = "{} Error".format(ICON_ERROR)
                        tags = ("error",)
                        image_key = "error"
                elif item.get("localOnly"):
                    is_pending = self.store.is_upload_pending(self.current_root["id"], rp)
                    status = "{} Pendiente".format(ICON_UPLOAD) if is_pending else "{} Local".format(ICON_CHECK)
                    tags = ("uploading",) if is_pending else ("local",)
                    image_key = "upload" if is_pending else "check"
                else:
                    is_local = item["type"] != "directory" and self.store.is_local_current(self.current_root, self.current_root["id"], rp, item)
                    status = "{} Check".format(ICON_CHECK) if is_local else "{} Nube".format(ICON_CLOUD)
                    tags = ("local",) if is_local else ("cloud",)
                    image_key = "check" if is_local else "cloud"
            if index % 2:
                tags = (*tags, "odd")

            iid = self.tree.insert("", END, values=(
                "{}  {}".format(icon, item["name"]), format_size(item.get("size")), item.get("modifiedAt", ""), status
            ), image=self.icon_images.get(image_key), tags=tags)
            self.items[iid] = item
        if query:
            self.set_status("{} de {} elemento(s).".format(len(shown), len(self.all_items)))

    def _selected_item(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.items.get(sel[0])

    def _show_context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
        item = self._selected_item()
        if not item:
            return
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=TEXT, activebackground=BLUE_SOFT, activeforeground=TEXT)
        menu.add_command(label="Abrir", command=self._open_selected)
        if self.store.settings["mode"] == "home":
            if item.get("localOnly"):
                menu.add_command(label="Subir pendiente ahora", command=self._upload_selected_pending)
            elif item.get("type") == "directory":
                folder_rp = join_remote(self.current_path, item["name"])
                if self.store.is_offline_folder(self.current_root["id"], folder_rp):
                    menu.add_command(label="Sincronizar carpeta local ahora", command=self._download_selected_folder)
                    menu.add_command(label="Dejar de mantener local", command=self._unpin_selected_folder)
                else:
                    menu.add_command(label="Descargar carpeta (mantener local)", command=self._download_selected_folder)
            else:
                menu.add_command(label="Descargar sin abrir", command=self._download_selected)
                if is_maya_ascii_name(item.get("name")):
                    menu.add_command(label="Preparar shot completo (.ma)", command=lambda: self._prepare_selected_shot(False))
                    menu.add_command(label="Preparar shot completo y abrir", command=lambda: self._prepare_selected_shot(True))
        menu.add_separator()
        menu.add_command(label="Actualizar", command=self._full_refresh)
        menu.tk_popup(event.x_root, event.y_root)

    def _upload_selected_pending(self):
        item = self._selected_item()
        if not item or self.store.settings["mode"] != "home":
            return
        root = self.current_root
        rp = join_remote(self.current_path, item["name"])
        local_path = self.store.local_path(root, rp)

        def work():
            if item.get("type") == "directory":
                self._scan_for_offline_changes()
                prefix = normalize_remote_path(rp).rstrip("/") + "/"
                for entry in self.store.pending_uploads():
                    if entry.get("rootId") == root["id"] and normalize_remote_path(entry.get("remotePath", "")).startswith(prefix):
                        self._upload_outbox_entry(entry)
                self.post(self._refresh)
                return
            entry = self._queue_pending_upload(root, rp, local_path, reason="manual", announce=False)
            if entry:
                self._upload_outbox_entry(entry)
            self.post(self._refresh)

        self._worker(work)

    def _download_selected(self):
        item = self._selected_item()
        if not item:
            return
        if item.get("type") == "directory":
            self._navigate(self.current_root, join_remote(self.current_path, item["name"]))
            return
        if self.store.settings["mode"] == "office":
            return

        def work():
            rp = join_remote(self.current_path, item["name"])
            local = self.store.ensure_destination(self.current_root, rp)
            root_id = self.current_root["id"]
            self.post(self.set_status, "Descargando {}...".format(item["name"]))
            self.post(self._set_transfer_state, root_id, rp, "downloading")
            self.post(self._log_activity, "Descarga", "Descargando {}".format(item["name"]), format_size(item.get("size")), "download")

            def prog(received, total):
                if total:
                    label = "Descargando {} / {}".format(format_size(received), format_size(total))
                    self.post(self._set_progress, received, total, label)

            self.download_paths.add(local_path_key(local))
            success = False
            try:
                self.file_api.download_file(root_id, rp, local, prog)
                self.store.mark_downloaded(root_id, rp, item, local)
                if self.watcher_thread:
                    self.file_snapshots[local_path_key(local)] = local_snapshot(local)
                success = True
                self.post(self._log_activity, "Descarga", "Descarga terminada: {}".format(item["name"]), format_size(item.get("size")), "success")
                self.post(self._notify, "Remote Believe", "Descarga terminada: {}".format(item["name"]))
                self.post(self._refresh)
            except Exception:
                self.post(self._set_transfer_state, root_id, rp, "error")
                raise
            finally:
                self.download_paths.discard(local_path_key(local))
                if success:
                    self.post(self._clear_transfer_state, root_id, rp)
                self.post(self._reset_progress)

        self._worker(work)

    def _download_selected_folder(self):
        item = self._selected_item()
        if not item or item.get("type") != "directory":
            return
        if self.store.settings["mode"] == "office":
            return

        def work():
            start_rp = join_remote(self.current_path, item["name"])
            root = self.current_root
            root_id = root["id"]
            self.post(self.set_status, "Buscando archivos en {}...".format(item["name"]))
            self.post(self._set_transfer_state, root_id, start_rp, "downloading")
            self.post(self._log_activity, "Descarga", "Preparando carpeta {}".format(item["name"]), "", "download")
            self._pin_offline_folder(root, start_rp, announce=True)
            queue = [start_rp]
            files_to_download = []

            while queue:
                rp = queue.pop(0)
                data = self.file_api.list_folder(root_id, rp)
                for child in data.get("items", []):
                    child_rp = join_remote(rp, child["name"])
                    if child.get("type") == "directory":
                        queue.append(child_rp)
                    else:
                        files_to_download.append((child_rp, child))

            if not files_to_download:
                self.post(self.set_status, "Carpeta vacia.")
                self.post(self._clear_transfer_state, root_id, start_rp)
                self.post(self._reset_progress)
                self.post(self._log_activity, "Descarga", "Carpeta vacia: {}".format(item["name"]), "", "info")
                return

            total_files = len(files_to_download)
            total_bytes = sum(int(file_item.get("size") or 0) for _rp, file_item in files_to_download)
            done_bytes = 0
            downloaded_files = 0
            failed_files = 0
            for i, (rp, file_item) in enumerate(files_to_download):
                local = self.store.ensure_destination(root, rp)
                if self.store.is_local_current(root, root_id, rp, file_item):
                    self.store.mark_downloaded(root_id, rp, file_item, local)
                    done_bytes += int(file_item.get("size") or 0)
                    downloaded_files += 1
                    if self.watcher_thread:
                        self.file_snapshots[local_path_key(local)] = local_snapshot(local)
                    continue

                self.post(self._set_transfer_state, root_id, rp, "downloading")
                def prog(received, total):
                    if total_bytes:
                        label = "Descargando {}/{}: {} / {}".format(i + 1, total_files, format_size(done_bytes + received), format_size(total_bytes))
                        self.post(self._set_progress, done_bytes + received, total_bytes, label)
                    elif total:
                        label = "Descargando {}/{}: {} / {}".format(i + 1, total_files, format_size(received), format_size(total))
                        self.post(self._set_progress, received, total, label)
                    else:
                        self.post(self._set_progress, i + 1, total_files, "Descargando {}/{}".format(i + 1, total_files))

                try:
                    self.download_paths.add(local_path_key(local))
                    self.file_api.download_file(root_id, rp, local, prog)
                    self.store.mark_downloaded(root_id, rp, file_item, local)
                    done_bytes += int(file_item.get("size") or 0)
                    downloaded_files += 1
                    if self.watcher_thread:
                        self.file_snapshots[local_path_key(local)] = local_snapshot(local)
                    self.post(self._clear_transfer_state, root_id, rp)
                except Exception as e:
                    failed_files += 1
                    self.post(self._set_transfer_state, root_id, rp, "error")
                    self.post(self._log_activity, "Error", "No se pudo descargar {}".format(file_item.get("name", rp)), self._friendly_transfer_error(e), "error")
                finally:
                    self.download_paths.discard(local_path_key(local))

            if failed_files:
                self.post(self._set_transfer_state, root_id, start_rp, "error")
                self.post(self.set_status, "Carpeta descargada con {} error(es).".format(failed_files))
                self.post(self._log_activity, "Descarga", "Carpeta terminada con errores: {}".format(item["name"]), "{} ok / {} error".format(downloaded_files, failed_files), "error")
            else:
                self.post(self._clear_transfer_state, root_id, start_rp)
                self.post(self.set_status, "Descarga de carpeta completada.")
                self.post(self._log_activity, "Descarga", "Carpeta descargada: {}".format(item["name"]), "{} archivo(s)".format(total_files), "success")
                self.post(self._notify, "Remote Believe", "Carpeta descargada: {}".format(item["name"]))
            self.post(self._reset_progress)
            self.post(self._refresh)

        self._worker(work)

    def _open_selected(self):
        sel = self.tree.selection()
        if not sel: return
        item = self.items[sel[0]]

        if item["type"] == "directory":
            self._navigate(self.current_root, join_remote(self.current_path, item["name"]))
            return

        mode = self.store.settings["mode"]
        if mode == "office":
            local = root_base_path(self.current_root).joinpath(*self.current_path.split("/")) / item["name"]
            self._open_path(local)
        else:
            def work():
                rp = join_remote(self.current_path, item["name"])
                local = self.store.ensure_destination(self.current_root, rp)
                root_id = self.current_root["id"]
                if item.get("localOnly"):
                    self.post(self._open_path, self.store.explorer_path(self.current_root, rp))
                    self.post(self._refresh)
                    return
                if not self.store.is_local_current(self.current_root, self.current_root["id"], rp, item):
                    self.post(self.set_status, f"Descargando {item['name']} desde la Oficina...")
                    self.post(self._set_transfer_state, root_id, rp, "downloading")
                    self.post(self._log_activity, "Descarga", "Descargando para abrir {}".format(item["name"]), format_size(item.get("size")), "download")
                    def prog(r, t):
                        if t:
                            self.post(self._set_progress, r, t, "Descargando {} / {}".format(format_size(r), format_size(t)))
                    self.download_paths.add(local_path_key(local))
                    success = False
                    try:
                        self.file_api.download_file(root_id, rp, local, prog)
                        self.store.mark_downloaded(root_id, rp, item, local)
                        if self.watcher_thread:
                            self.file_snapshots[local_path_key(local)] = local_snapshot(local)
                        success = True
                        self.post(self._log_activity, "Descarga", "Listo para abrir: {}".format(item["name"]), format_size(item.get("size")), "success")
                    except Exception:
                        self.post(self._set_transfer_state, root_id, rp, "error")
                        raise
                    finally:
                        self.download_paths.discard(local_path_key(local))
                        if success:
                            self.post(self._clear_transfer_state, root_id, rp)
                        self.post(self._reset_progress)
                self.post(self._open_path, self.store.explorer_path(self.current_root, rp))
                self.post(self._refresh)
            self._worker(work)

    def _open_path(self, path):
        path = Path(path)
        if sys.platform.startswith("win"): os.startfile(str(path))
        elif sys.platform == "darwin": subprocess.Popen(["open", str(path)])
        else: subprocess.Popen(["xdg-open", str(path)])

    def _item_remote_path(self, item):
        return join_remote(self.current_path, item["name"])

    def _find_remote_item(self, root, remote_path):
        parent = parent_remote(remote_path)
        name = remote_path.split("/")[-1]
        data = self.file_api.list_folder(root["id"], parent)
        self._ensure_local_folder(root, parent)
        for item in data.get("items", []):
            if item.get("name", "").lower() == name.lower():
                return item
        return {"name": name, "type": "file", "size": None, "modifiedAt": ""}

    def _download_remote_file(self, root, remote_path, item=None, explicit=True):
        remote_path = normalize_remote_path(remote_path)
        if not remote_path or not can_access_remote_path(self.access, root["id"], remote_path):
            return []
        if explicit:
            self.store.restore_local_downloads(root["id"], remote_path)
        elif self.store.is_local_deleted(root["id"], remote_path):
            return []
        if is_pattern_path(remote_path):
            return self._download_pattern_files(root, remote_path)

        if self.cloud_engine:
            return [self.cloud_engine.hydrate(root, remote_path, item, explicit=explicit)]

        item = item or self._find_remote_item(root, remote_path)
        if item.get("type") != "file":
            return []

        local_path = self.store.ensure_destination(root, remote_path)
        if self.store.is_local_current(root, root["id"], remote_path, item):
            self.file_snapshots[local_path_key(local_path)] = local_snapshot(local_path)
            return [local_path]

        self.download_paths.add(local_path_key(local_path))
        self.post(self._set_transfer_state, root["id"], remote_path, "downloading")
        success = False
        try:
            self.post(self.set_status, "Descargando dependencia {}".format(remote_path))

            def progress(received, total):
                if total:
                    label = "Descargando {}: {} de {}".format(item["name"], format_size(received), format_size(total))
                    self.post(self._set_progress, received, total, label)

            self.file_api.download_file(root["id"], remote_path, local_path, progress)
            self.store.mark_downloaded(root["id"], remote_path, item, local_path)
            self.file_snapshots[local_path_key(local_path)] = local_snapshot(local_path)
            success = True
            return [local_path]
        except Exception:
            self.post(self._set_transfer_state, root["id"], remote_path, "error")
            raise
        finally:
            self.download_paths.discard(local_path_key(local_path))
            if success:
                self.post(self._clear_transfer_state, root["id"], remote_path)

    def _download_pattern_files(self, root, remote_path):
        parent = parent_remote(remote_path)
        pattern_name = remote_path.split("/")[-1]
        regex = pattern_to_regex(pattern_name)
        downloaded = []
        data = self.file_api.list_folder(root["id"], parent)
        self._ensure_local_folder(root, parent)
        for item in data.get("items", []):
            if item.get("type") == "file" and regex.match(item.get("name", "")):
                downloaded.extend(self._download_remote_file(root, join_remote(parent, item["name"]), item=item))
        return downloaded

    def _download_remote_folder(self, root, remote_path):
        controls = getattr(self, "transfers", None)
        if controls:
            with controls.operation("download", root["id"], normalize_remote_path(remote_path)):
                return MeshClientApp._download_folder_payload(self, root, remote_path)
        return MeshClientApp._download_folder_payload(self, root, remote_path)

    def _download_folder_payload(self, root, remote_path):
        from cloud_files import CloudError
        self.store.reconcile_local_deletions([root])
        self.store.restore_local_downloads(root["id"], remote_path)
        queue = [normalize_remote_path(remote_path)]
        downloaded = 0
        seen = set()
        while queue:
            folder = queue.pop(0)
            key = folder.lower()
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > MAX_PREP_FILES:
                raise RBError("Limite de descarga alcanzado ({})".format(MAX_PREP_FILES))
            if not can_access_remote_path(self.access, root["id"], folder):
                continue
            data = self.file_api.list_folder(root["id"], folder)
            items = data.get("items", [])
            if self.store.is_local_deleted(root["id"], folder):
                continue
            self._ensure_local_folder(root, folder)
            for item in items:
                child = join_remote(folder, item.get("name", ""))
                if item.get("type") == "directory":
                    queue.append(child)
                else:
                    try:
                        downloaded += len(self._download_remote_file(root, child, item=item, explicit=True))
                    except CloudError as exc:
                        self.post(self._log_activity, "Descarga", "Archivo pendiente; se continua con los demas", child + ": " + str(exc), "error")
        return downloaded

    def _pin_offline_folder(self, root, remote_path, announce=True):
        remote_path = normalize_remote_path(remote_path).rstrip("/")
        self.store.restore_local_downloads(root["id"], remote_path)
        added = False  # Explicit downloads do not create a persistent background pin.
        self._ensure_local_folder(root, remote_path)
        if announce and added:
            label = remote_path or root_display_label(root)
            self.post(self._log_activity, "Local", "Carpeta marcada para mantenerse local", label, "download")
            self.post(self.set_status, "Carpeta marcada para mantenerse local: {}".format(label))
        return added

    def _unpin_selected_folder(self):
        item = self._selected_item()
        if not item or item.get("type") != "directory" or self.store.settings.get("mode") != "home":
            return
        rp = join_remote(self.current_path, item["name"])
        if self.store.remove_offline_folder(self.current_root["id"], rp):
            self.set_status("Carpeta ya no se mantendra local: {}".format(rp))
            self._log_activity("Local", "Carpeta removida de mantener local", rp, "info")
        self._refresh()

    def _start_offline_folder_sync(self):
        return  # Content downloads are explicit; old persistent folder pins are ignored.
        if self.screen != "main":
            return
        if self.offline_sync_thread and self.offline_sync_thread.is_alive():
            return

        def loop():
            self._request_offline_folder_sync("inicio")
            while not self.watcher_stop.wait(OFFLINE_FOLDER_SYNC_S):
                self._request_offline_folder_sync("periodico")

        self.offline_sync_thread = self._start_session_thread(loop)

    def _request_offline_folder_sync(self, reason="manual"):
        if self.screen != "main":
            return
        if self.store.settings.get("mode") != "home" or not self.file_api:
            return
        if not self.store.offline_folders():
            return

        def run():
            if not self.offline_sync_lock.acquire(False):
                return
            try:
                downloaded, skipped = self._sync_offline_folders()
                if downloaded:
                    detail = "{} archivo(s) localizados".format(downloaded)
                    if skipped:
                        detail += ", {} omitido(s) por cambios locales".format(skipped)
                    self.post(self._log_activity, "Local", "Carpetas offline actualizadas", detail, "success")
                    self.post(self.set_status, "Carpetas offline actualizadas: {}".format(detail))
                    self.post(self._reset_progress)
                    if self.screen == "main":
                        self.post(self._apply_filter)
            finally:
                self.offline_sync_lock.release()

        self._start_session_thread(run)

    def _sync_offline_folders(self):
        self.store.reconcile_local_deletions(self.roots, live=bool(self.cloud_engine))
        downloaded = 0
        skipped = 0
        for entry in self.store.offline_folders():
            remaining = MAX_OFFLINE_DOWNLOADS_PER_CYCLE - downloaded
            if remaining <= 0:
                break
            root = self._root_by_id(entry.get("rootId", ""))
            if not root:
                continue
            count, skipped_count = self._sync_offline_folder(root, entry.get("remotePath", ""), remaining)
            downloaded += count
            skipped += skipped_count
        return downloaded, skipped

    def _should_background_download(self, root, rp, item):
        if item.get("type") != "file":
            return False, False
        root_id = root["id"]
        if self.store.is_local_deleted(root_id, rp):
            return False, False
        local = self.store.local_path(root, rp)
        if self.store.is_upload_pending(root_id, rp):
            return False, True
        if not local.exists():
            if self.store.manifest.get(self.store.key(root_id, rp)):
                return False, False
            return True, False
        if self.store.is_placeholder(root_id, rp):
            return True, False
        if not local.is_file():
            return False, True
        if local_matches_remote_item(local, item):
            self.store.mark_downloaded(root_id, rp, item, local)
            self.file_snapshots[local_path_key(local)] = local_snapshot(local)
            return False, False
        if self.store.manifest_has_current_local_snapshot(root_id, rp, local):
            return True, False
        return False, True

    def _sync_offline_folder(self, root, remote_path, max_files):
        remote_path = normalize_remote_path(remote_path).rstrip("/")
        downloaded = 0
        skipped = 0
        queue = [remote_path]
        seen = set()
        while queue and downloaded < max_files:
            folder = queue.pop(0)
            key = folder.lower()
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > MAX_PREP_FILES:
                break
            if not can_access_remote_path(self.access, root["id"], folder):
                continue
            if self.store.is_local_deleted(root["id"], folder):
                continue
            try:
                data = self.file_api.list_folder(root["id"], folder)
            except Exception as exc:
                self.post(self._log_activity, "Conexion", "No se pudo revisar carpeta offline", self._friendly_transfer_error(exc), "error")
                break
            items = data.get("items", [])
            if self.store.is_local_deleted(root["id"], folder):
                continue
            if not self.store.local_path(root, folder).exists():
                self.store.record_local_deletion(root["id"], folder)
                continue
            self._ensure_local_folder(root, folder)
            self._materialize_items(root, folder, items, save=False)
            for item in items:
                child = join_remote(folder, item.get("name", ""))
                if self.store.is_local_deleted(root["id"], child):
                    continue
                if item.get("type") == "directory":
                    queue.append(child)
                    continue
                should_download, should_skip = self._should_background_download(root, child, item)
                if should_skip:
                    skipped += 1
                    continue
                if not should_download:
                    continue
                try:
                    downloaded += len(self._download_remote_file(root, child, item=item, explicit=True))
                except Exception as exc:
                    skipped += 1
                    self.post(self._set_transfer_state, root["id"], child, "error", self._friendly_transfer_error(exc))
                if downloaded >= max_files:
                    break
        self.store.save_manifest()
        return downloaded, skipped

    def _prepare_selected_shot(self, open_after=False):
        item = self._selected_item()
        if not item or item.get("type") != "file" or not is_maya_ascii_name(item.get("name")):
            messagebox.showwarning(APP_NAME, "Selecciona un archivo .ma para preparar el shot.")
            return
        if self.store.settings["mode"] != "home":
            messagebox.showinfo(APP_NAME, "En modo Oficina el shot ya esta en el disco fisico.")
            return

        def work():
            local_path = self._prepare_shot(item)
            if open_after:
                self.post(self._open_path, local_path)

        self._worker(work)

    def _prepare_shot(self, item):
        return self._prepare_shot_for_remote(self.current_root, self._item_remote_path(item))

    def _prepare_shot_for_remote(self, root, shot_remote_path):
        item = self._find_remote_item(root, shot_remote_path)
        if item.get("type") != "file":
            raise RBError("El shot seleccionado no es un archivo.")
        self.post(self._log_activity, "Shot", "Preparando shot completo", Path(shot_remote_path).name, "download")
        downloaded_paths = self._download_remote_file(root, shot_remote_path)
        if not downloaded_paths:
            raise RBError("No se pudo descargar el .ma seleccionado")
        shot_local_path = downloaded_paths[0]
        queue = [(root, shot_remote_path, shot_local_path)]
        seen = set()
        downloaded = 1
        missing = []

        while queue:
            current_root, current_remote_path, current_local_path = queue.pop(0)
            key = (current_root["id"], current_remote_path.lower())
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > MAX_PREP_FILES:
                missing.append("Limite de preparacion alcanzado ({})".format(MAX_PREP_FILES))
                break
            if not is_maya_ascii_name(current_local_path.name):
                continue

            dependencies = self._parse_maya_dependencies(current_local_path)
            base_remote_folder = parent_remote(current_remote_path)
            for dep in dependencies:
                resolved = self._resolve_dependency_path(dep, current_root, base_remote_folder)
                if not resolved:
                    continue
                dep_root, dep_remote_path = resolved
                dep_key = (dep_root["id"], dep_remote_path.lower())
                if dep_key in seen:
                    continue
                try:
                    paths = self._download_remote_file(dep_root, dep_remote_path)
                    downloaded += len(paths)
                    for local_dep in paths:
                        if is_maya_ascii_name(local_dep.name):
                            actual_remote = self.store.remote_path_from_local(dep_root, local_dep)
                            queue.append((dep_root, actual_remote, local_dep))
                except Exception as exc:
                    missing.append("{} ({})".format(dep, exc))

        message = "Shot preparado. {} archivo(s) disponibles.".format(downloaded)
        if missing:
            message += " {} dependencia(s) no encontradas.".format(len(missing))
        self.post(self.set_status, message)
        self.post(self._reset_progress)
        if missing:
            self.post(self._log_activity, "Shot", message, "Revisa dependencias faltantes", "error")
            self.post(messagebox.showwarning, APP_NAME, "{}\n\n{}".format(message, "\n".join(missing[:12])))
        else:
            self.post(self._log_activity, "Shot", message, Path(shot_remote_path).name, "success")
            self.post(self._notify, "Remote Believe", message)
            self.post(messagebox.showinfo, APP_NAME, message)
        return self.store.explorer_path(root, shot_remote_path)

    def _parse_maya_dependencies(self, ma_path):
        try:
            text = Path(ma_path).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = Path(ma_path).read_text(encoding="latin-1", errors="ignore")

        values = []

        def add_dependency(value, explicit_sequence=False):
            value = str(value or "").strip()
            if not value or not has_dependency_extension(value):
                return
            values.append(value)
            pattern = numbered_sequence_pattern(value, explicit_sequence=explicit_sequence)
            if pattern and pattern != value:
                values.append(pattern)

        def block_has_sequence_enabled(block):
            if re.search(r'setAttr\s+"?\.(?:ufe|useFrameExtension)"?\s+(?:yes|true|on|1)\b', block, re.I):
                return True
            if re.search(r'setAttr\s+"?\.(?:ufe|useFrameExtension)"?\s+-type\s+"bool"\s+(?:yes|true|on|1)\b', block, re.I):
                return True
            return False

        node_blocks = re.split(r"(?m)(?=^createNode\s+)", text)
        for block in node_blocks:
            node_match = re.match(r"createNode\s+([^\s;]+)", block.strip())
            node_type = node_match.group(1).strip('"').lower() if node_match else ""
            explicit_sequence = node_type in {"imageplane"} or block_has_sequence_enabled(block)
            for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', block):
                value = match.group(1) if match.group(1) is not None else match.group(2)
                add_dependency(value, explicit_sequence=explicit_sequence)

        for match in re.finditer(r'"([^"]+)"|\'([^\']+)\'', text):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            add_dependency(value)
        for match in re.finditer(r"((?:[A-Za-z]:|//|\\\\)[^\s\"';]+)", text):
            value = match.group(1)
            add_dependency(value)

        clean = []
        seen = set()
        for value in values:
            normalized = value.strip().replace("\\\\", "/").replace("\\", "/")
            if normalized and normalized not in seen:
                seen.add(normalized)
                clean.append(normalized)
        return clean

    def _resolve_dependency_path(self, dep, base_root, base_remote_folder):
        dep = str(dep or "").strip().strip('"').strip("'")
        if not dep or dep.startswith("$"):
            return None

        drive_roots = {root_drive_letter(root): root for root in self.roots if root_drive_letter(root)}
        normalized = dep.replace("\\\\", "/").replace("\\", "/")

        for letter, root in drive_roots.items():
            prefixes = DRIVE_ALIASES.get(letter, []) + ["{}:/".format(letter)]
            for prefix in prefixes:
                prefix_norm = prefix.replace("\\\\", "/").replace("\\", "/")
                if normalized.lower().startswith(prefix_norm.lower()):
                    remote_path = normalize_remote_path(normalized[len(prefix_norm) :])
                    if can_access_remote_path(self.access, root["id"], remote_path):
                        return root, remote_path
                    return None

        if re.match(r"^[A-Za-z]:", normalized):
            return None
        if normalized.startswith("//"):
            return None
        if has_dependency_extension(normalized):
            remote_path = normalize_remote_path(join_remote(base_remote_folder, normalized))
            if can_access_remote_path(self.access, base_root["id"], remote_path):
                return base_root, remote_path
        return None

    # ── Background Services ──

    def _start_office_server(self):
        if self.office_server:
            return
        self.office_server = OfficeServer(PEER_PORT, self)
        ok, msg = self.office_server.start()
        self.set_status(msg)

    def _start_heartbeat(self):
        # La version syncremote no depende de un portal central: el cliente casa
        # se conecta directo al servidor oficina y los permisos viven en R:\syncremote.json.
        return

    def _restore_pending_upload_states(self):
        count = 0
        for entry in self.store.pending_uploads():
            root_id = entry.get("rootId", "")
            rp = entry.get("remotePath", "")
            if not root_id or not rp:
                continue
            self.transfer_states[self._transfer_key(root_id, rp)] = {"state": "pending", "message": entry.get("lastError", "")}
            count += 1
        if count:
            self.set_status("{} archivo(s) pendiente(s) de subir.".format(count))
            self._log_activity("Subida", "{} archivo(s) pendiente(s) de subir.".format(count), "Se reintentara automaticamente", "upload")

    def _prune_unsafe_pending_uploads(self):
        removed = 0
        for entry in list(self.store.pending_uploads()):
            try:
                root_id = entry.get("rootId", "")
                rp = normalize_remote_path(entry.get("remotePath", ""))
                root = self._root_by_id(root_id)
                local_path = Path(entry.get("localPath", ""))
                if not root or not rp or not local_path.exists() or not local_path.is_file():
                    self.store.remove_upload(root_id, rp)
                    self._clear_transfer_state(root_id, rp)
                    removed += 1
                    continue
                if is_ignored(local_path) or self.store.is_placeholder_path(root, local_path):
                    self.store.remove_upload(root_id, rp)
                    self._clear_transfer_state(root_id, rp)
                    removed += 1
                    continue
                allowed, _policy_reason = upload_policy(root, rp, local_path, reason=entry.get("reason", "changed"))
                if not allowed:
                    self.store.remove_upload(root_id, rp)
                    self._clear_transfer_state(root_id, rp)
                    removed += 1
            except OSError:
                continue  # Preserve pending work while Windows metadata is unavailable.
        if removed:
            self.set_status("{} subida(s) antiguas bloqueadas fueron limpiadas.".format(removed))
            self._log_activity("Subida segura", "{} pendiente(s) removido(s) por seguridad.".format(removed), "Rigs/assets/cache/Prism no se suben", "info")

    def _queue_pending_upload(self, root, rp, local_path, reason="changed", announce=True):
        rp = normalize_remote_path(rp)
        local_path = Path(local_path)
        if not rp or not local_path.exists() or not local_path.is_file():
            return None
        if self.store.is_placeholder_path(root, local_path):
            return None
        pending = self.store.outbox.get("items", {}).get(self.store.key(root["id"], rp))
        if (pending and reason != "manual" and all(pending.get(key) == value
                for key, value in local_snapshot_record(local_path).items())):
            return pending
        if getattr(self, "cloud_engine", None) and not self._content_needs_upload(root, rp, local_path):
            return None
        allowed, policy_reason = upload_policy(root, rp, local_path, reason=reason)
        if not allowed:
            self.post(self._set_transfer_state, root["id"], rp, "denied", policy_reason)
            if announce:
                self.post(self._log_activity, "Subida bloqueada", "{} no se subira automaticamente".format(local_path.name), policy_reason, "denied")
            return None
        cancelled = self.store.settings.get("cancelled_uploads", {})
        key = self.store.key(root["id"], rp)
        if cancelled.get(key) == local_snapshot_record(local_path) and reason != "manual":
            return None
        if reason == "manual":
            cancelled.pop(key, None)
        was_pending = self.store.is_upload_pending(root["id"], rp)
        entry = self.store.enqueue_upload(root, rp, local_path, reason=reason)
        if not entry:
            return None
        self.post(self._set_transfer_state, root["id"], rp, "pending")
        if announce and not was_pending:
            self.post(self._log_activity, "Subida", "Pendiente de subir: {}".format(local_path.name), rp, "upload")
        return entry

    def _content_needs_upload(self, root, rp, path):
        key = self.store.key(root["id"], rp)
        digest = hashlib.sha256()
        before = local_snapshot(path)
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(SHA256_CHUNK), b""):
                digest.update(chunk)
        if local_snapshot(path) != before:
            return False
        value = digest.hexdigest()
        current = self.store.manifest.get(key, {})
        known = current.get("sha256") == value
        if not known:
            return True
        current.update({"placeholder": False, "localPath": str(path), "sha256": value, **local_snapshot_record(path)})
        self.store.manifest[key] = current
        self.store.save_manifest()
        self.file_snapshots[local_path_key(Path(path))] = before
        engine = getattr(self.store, "cloud_engine", None)
        if engine:
            engine.native.dll.RBMarkInSync(str(path))
        self.store.remove_upload(root["id"], rp)
        self.post(self._clear_transfer_state, root["id"], rp)
        return False

    def _scan_for_offline_changes(self):
        queued = 0
        blocked = 0
        for root in self.roots:
            root_dir = self.store.root_dir(root)
            if not root_dir.exists():
                continue
            for path in self.store.iter_local_files(root):
                try:
                    if not path.is_file() or is_ignored(path):
                        continue
                    if self.store.is_placeholder_path(root, path):
                        continue
                    pk = local_path_key(path)
                    if pk in self.download_paths:
                        continue
                    rp = self.store.remote_path_from_local(root, path)
                    if not rp:
                        continue
                    self.file_snapshots[pk] = local_snapshot(path)
                    if self.store.is_upload_pending(root["id"], rp):
                        self.post(self._set_transfer_state, root["id"], rp, "pending")
                        continue
                    allowed, policy_reason = upload_policy(root, rp, path, reason="offline")
                    if not allowed:
                        blocked += 1
                        continue
                    if self.store.is_dirty_cloud_file(path) or not self.store.manifest_has_current_local_snapshot(root["id"], rp, path):
                        if self._queue_pending_upload(root, rp, path, reason="offline", announce=False):
                            queued += 1
                except OSError:
                    continue  # Retry this file next scan, without stopping other projects.
        if queued:
            self.post(self.set_status, "{} archivo(s) detectado(s) para subir.".format(queued))
        elif blocked:
            self.post(self.set_status, "{} archivo(s) locales ignorados por seguridad de auto-subida.".format(blocked))
        return queued

    def _remote_item_for_path(self, root_id, rp):
        if not self.file_api:
            return None
        return self.file_api.remote_item(root_id, normalize_remote_path(rp))

    def _discard_pending_upload(self, root, rp, local_path, title, message, state="denied"):
        self.store.remove_upload(root["id"], rp)
        self.post(self._set_transfer_state, root["id"], rp, state, message)
        self.post(self._log_activity, title, "{} no se subio".format(Path(local_path).name), message, state)
        return False

    def _upload_outbox_entry(self, entry):
        if getattr(self, "transfers", None) and self.transfers.is_paused("upload"):
            return False
        root = self._root_by_id(entry.get("rootId", ""))
        if not root:
            return False
        rp = normalize_remote_path(entry.get("remotePath", ""))
        local_path = Path(entry.get("localPath", ""))
        if not rp or not local_path.exists() or not local_path.is_file():
            self.store.remove_upload(entry.get("rootId", ""), rp)
            self.post(self._clear_transfer_state, entry.get("rootId", ""), rp)
            self.post(self._log_activity, "Subida", "Pendiente eliminado porque el archivo ya no existe.", rp, "info")
            return False
        if is_ignored(local_path) or self.store.is_placeholder_path(root, local_path):
            self.store.remove_upload(root["id"], rp)
            self.post(self._clear_transfer_state, root["id"], rp)
            return False
        allowed, policy_reason = upload_policy(root, rp, local_path, reason=entry.get("reason", "changed"))
        if not allowed:
            self.store.remove_upload(root["id"], rp)
            self.post(self._set_transfer_state, root["id"], rp, "denied", policy_reason)
            self.post(self._log_activity, "Subida bloqueada", "{} no se subio".format(local_path.name), policy_reason, "denied")
            return False
        if local_path.stat().st_size <= 0:
            reason = "Subida bloqueada: el archivo local pesa 0 bytes."
            old_error = entry.get("lastError", "")
            self.store.update_upload_error(root["id"], rp, reason, delay_s=300)
            self.post(self._set_transfer_state, root["id"], rp, "error", reason)
            if old_error != reason:
                self.post(self._log_activity, "Error", "No se subio {}".format(local_path.name), reason, "error")
            return False
        if time.time() - local_path.stat().st_mtime < STABLE_S:
            return False

        try:
            remote_item = self._remote_item_for_path(root["id"], rp)
        except Exception as exc:
            self.store.update_upload_error(root["id"], rp, "No se pudo comprobar si el nombre existe: " + str(exc))
            return False
        if remote_item is not None:
            if remote_item.get("type") == "directory" or not remote_item.get("sha256"):
                self.store.update_upload_error(root["id"], rp, "No se pudo comparar el contenido de Oficina.")
                return False
            digest = file_hash(local_path)
            if digest == remote_item["sha256"]:
                baseline = self.store.manifest.setdefault(self.store.key(root["id"], rp), {})
                baseline.update(local_snapshot_record(local_path))
                baseline["sha256"] = digest
                self.store.save_manifest()
                self.file_snapshots[local_path_key(local_path)] = local_snapshot(local_path)
                return self._discard_pending_upload(root, rp, local_path, "Subida",
                    "El contenido es identico al del servidor; no se sube de nuevo.", "success")
            previous_hash = self.store.manifest.get(self.store.key(root["id"], rp), {}).get("sha256")
            if previous_hash and previous_hash != remote_item["sha256"]:
                return self._discard_pending_upload(root, rp, local_path, "Conflicto",
                    "El servidor cambio desde tu ultima descarga; se conserva tu archivo local.", "error")

        current = local_snapshot_record(local_path)
        if entry.get("localMtimeNs") != current["localMtimeNs"] or entry.get("localSize") != current["localSize"]:
            entry = self.store.enqueue_upload(root, rp, local_path, reason=entry.get("reason", "changed"))

        if not access_is_admin(self.access) and not self.access.get("directUpload"):
            reason = "Sin permiso: este usuario no tiene subida directa habilitada."
            old_error = entry.get("lastError", "")
            self.store.update_upload_error(root["id"], rp, reason, delay_s=300)
            self.post(self._set_transfer_state, root["id"], rp, "denied", reason)
            if old_error != reason:
                self.post(self._log_activity, "Subida", "Subida pendiente bloqueada: {}".format(local_path.name), reason, "error")
            return False
        if not can_access_remote_path(self.access, root["id"], rp):
            reason = "Sin permiso para subir a esta ruta."
            old_error = entry.get("lastError", "")
            self.store.update_upload_error(root["id"], rp, reason, delay_s=300)
            self.post(self._set_transfer_state, root["id"], rp, "denied", reason)
            if old_error != reason:
                self.post(self._log_activity, "Subida", "Subida pendiente bloqueada: {}".format(local_path.name), reason, "error")
            return False

        self.post(self.set_status, "Subiendo pendiente: {}".format(local_path.name))
        self.post(self._set_transfer_state, root["id"], rp, "uploading")
        upload_key = local_path_key(local_path)
        self.upload_paths.add(upload_key)
        try:
            def upload_progress(sent, total):
                label = "Subiendo {} / {}".format(format_size(sent), format_size(total))
                self.post(self._set_progress, sent, total, label)

            result = self.file_api.upload_file(root["id"], parent_remote(rp), local_path, upload_progress,
                overwrite=bool(remote_item), yield_cb=self._upload_yield_for_downloads,
                expected_hash=remote_item["sha256"] if remote_item else "",
                pending_cb=lambda: self.store.is_upload_pending(root["id"], rp))
            if result.get("status") in ("already-current",):
                self.file_snapshots[local_path_key(local_path)] = local_snapshot(local_path)
                return self._discard_pending_upload(root, rp, local_path, "Subida", "El contenido ya era identico al del servidor; no se duplico la subida.", "info")
            self.store.mark_synced(root["id"], rp, local_path)
            if self.cloud_engine:
                synced = self.store.manifest[self.store.key(root["id"], rp)]
                digest = hashlib.sha256()
                with local_path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(SHA256_CHUNK), b""):
                        digest.update(chunk)
                synced["sha256"] = digest.hexdigest()
                item = {"name": local_path.name, "type": "file", "size": synced["size"], "modifiedAt": synced["modifiedAt"]}
                try:
                    self.cloud_engine._publish(root, rp, item)
                    self.cloud_engine.native.dll.RBMarkInSync(str(local_path))
                    self.cloud_engine.native.update(local_path, self.cloud_engine.identity(root, rp, item), directory=False, dehydrate=False)
                except Exception:
                    pass  # Data is committed; refresh metadata after Maya releases its handle.
                self.store.save_manifest()
            self.store.remove_upload(root["id"], rp)
            self.file_snapshots[local_path_key(local_path)] = local_snapshot(local_path)
            self.post(self._clear_transfer_state, root["id"], rp)
            self.post(self._reset_progress)
            self.post(self.set_status, "Pendiente subido a Oficina: {}".format(rp))
            self.post(self._log_activity, "Subida", "Subida terminada: {}".format(local_path.name), rp, "success")
            self.post(self._notify, "Remote Believe", "Subida terminada: {}".format(local_path.name))
            return True
        except TransferStopped:
            self.post(self._set_transfer_state, root["id"], rp, "pending", "Subida detenida")
            self.post(self._log_activity, "Subida", "Subida detenida: " + rp, rp, "cancelled")
            return False
        except Exception as exc:
            friendly = self._friendly_transfer_error(exc)
            old_error = entry.get("lastError", "")
            self.store.update_upload_error(root["id"], rp, friendly)
            self.post(self._set_transfer_state, root["id"], rp, "error", friendly)
            self.post(self._reset_progress)
            self.post(self.set_status, "Pendiente no subido: {}".format(friendly))
            if old_error != friendly:
                self.post(self._log_activity, "Error", "No se pudo subir {}".format(local_path.name), friendly, "error")
                self.post(self._notify, "Remote Believe", "Pendiente no subido: {}".format(local_path.name))
            return False
        finally:
            self.upload_paths.discard(upload_key)

    def _process_outbox(self):
        uploaded = 0
        attempted = 0
        for entry in self.store.pending_uploads(ready_only=True):
            if self.watcher_stop.is_set():
                break
            if attempted >= MAX_UPLOADS_PER_OUTBOX_CYCLE:
                break
            attempted += 1
            try:
                if self._upload_outbox_entry(entry):
                    uploaded += 1
            except OSError as exc:
                self.store.update_upload_error(entry.get("rootId", ""), entry.get("remotePath", ""),
                    "Windows no pudo consultar el archivo local; se reintentara: {}".format(exc))
            if self._download_active():
                break
        if uploaded:
            self.post(self._refresh)
        return uploaded

    def _start_watcher(self):
        # Watcher simplificado: si un archivo cambia en la cache local, se sube a la oficina
        if self.screen != "main": return
        if not self.store.settings.get("auto_upload"): return
        if self.watcher_thread and self.watcher_thread.is_alive():
            return
        baselined = False
        scanned_offline = False
        def loop():
            nonlocal baselined, scanned_offline
            while not self.watcher_stop.wait(WATCH_S):
                if not self.store.settings.get("auto_upload"): continue
                if not scanned_offline:
                    self._scan_for_offline_changes()
                    scanned_offline = True
                now = time.time()
                for r in self.roots:
                    rd = self.store.root_dir(r)
                    if not rd.exists(): continue
                    for p in self.store.iter_local_files(r):
                        try:
                            if self.watcher_stop.is_set(): return
                            if not p.is_file() or is_ignored(p): continue
                            if self.store.is_placeholder_path(r, p): continue
                            pk = local_path_key(p)
                            if pk in self.download_paths:
                                continue
                            snap = local_snapshot(p)
                            old = self.file_snapshots.get(pk)
                            if old is None:
                                self.file_snapshots[pk] = snap
                                if baselined:
                                    self.pending_changes[pk] = (r, snap, now)
                                continue
                            if snap != old or self.store.is_dirty_cloud_file(p):
                                pend = self.pending_changes.get(pk)
                                if not pend or pend[1] != snap:
                                    self.pending_changes[pk] = (r, snap, now)
                        except OSError:
                            continue

                baselined = True

                for pk, (r, snap, first) in list(self.pending_changes.items()):
                    try:
                        if now - first < STABLE_S: continue
                        p = Path(pk)
                        if not p.exists():
                            self.pending_changes.pop(pk, None)
                            continue
                        if self.store.is_placeholder_path(r, p):
                            self.pending_changes.pop(pk, None)
                            continue
                        if local_snapshot(p) != snap:
                            self.pending_changes[pk] = (r, local_snapshot(p), now)
                            continue

                        rp = self.store.remote_path_from_local(r, p)
                        if rp:
                            self._queue_pending_upload(r, rp, p, reason="changed")
                        self.pending_changes.pop(pk, None)
                    except OSError:
                        continue  # Keep the change pending until its metadata is readable.
                self._process_outbox()
        self.watcher_thread = self._start_session_thread(loop)


    # Panel de administracion nuevo. Queda definido al final de la clase para
    # reemplazar el panel anterior sin cambiar el resto del flujo de la app.
    def _admin_button(self, parent, text, command, accent=False, danger=False):
        bg = BLUE if accent else (RED if danger else CONTROL_BG)
        fg = "#ffffff" if (accent or danger) else TEXT
        active = BLUE if accent else (RED if danger else CONTROL_ACTIVE)
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=active,
            activeforeground=fg,
            relief="flat",
            bd=0,
            padx=14,
            pady=9,
            font=("Segoe UI", 10, "bold"),
        )
        return btn

    def _admin_load_syncremote(self):
        if self.store.settings.get("mode") == "office":
            return load_syncremote()
        if not self.file_api:
            raise RBError("No hay conexion con la oficina")
        return normalize_syncremote(self.file_api.get_admin_syncremote())

    def _admin_save_syncremote(self, data):
        data = normalize_syncremote(data)
        if self.store.settings.get("mode") == "office":
            save_syncremote(data)
            return {"ok": True}
        if not self.file_api:
            raise RBError("No hay conexion con la oficina")
        return self.file_api.save_admin_syncremote(data)

    def _admin_root_folders(self, root):
        cache_key = root.get("id")
        if not hasattr(self, "admin_folder_cache"):
            self.admin_folder_cache = {}
        if cache_key in self.admin_folder_cache:
            return self.admin_folder_cache[cache_key]

        if self.store.settings.get("mode") == "office":
            folders = list_access_folders_local(root)
        else:
            data = self.file_api.list_folder(root["id"], "")
            folders = sorted(
                [item["name"] for item in data.get("items", []) if item.get("type") == "directory"],
                key=str.lower,
            )
        self.admin_folder_cache[cache_key] = folders
        return folders

    def _show_admin_panel(self):
        if not access_is_admin(self.access):
            messagebox.showwarning(APP_NAME, "Solo superuser puede abrir Administracion.")
            return
        if hasattr(self, "admin_win") and self.admin_win.winfo_exists():
            self.admin_win.lift()
            return

        self.admin_folder_cache = {}
        self.admin_win = tk.Toplevel(self)
        self.admin_win.title("Remote Believe - Administracion")
        self.admin_win.geometry("1080x720")
        self.admin_win.minsize(900, 620)
        self.admin_win.configure(bg=BG)

        header = tk.Frame(self.admin_win, bg=BG)
        header.pack(fill=X, padx=24, pady=(22, 12))
        tk.Label(header, text="Administracion", bg=BG, fg=TEXT, font=("Segoe UI", 22, "bold")).pack(side=LEFT)
        self._admin_button(header, "Actualizar", self._refresh_admin_data, accent=False).pack(side=RIGHT, padx=(8, 0))
        self._admin_button(header, "Cerrar", self.admin_win.destroy).pack(side=RIGHT)

        body = tk.Frame(self.admin_win, bg=BG)
        body.pack(fill=BOTH, expand=True, padx=24, pady=(0, 24))

        left = tk.Frame(body, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1, width=390)
        left.pack(side=LEFT, fill=BOTH, padx=(0, 14))
        left.pack_propagate(False)

        right = tk.Frame(body, bg=PANEL, highlightbackground=CARD_BORDER, highlightthickness=1)
        right.pack(side=LEFT, fill=BOTH, expand=True)

        tk.Label(left, text="Usuarios", bg=PANEL, fg=TEXT, font=("Segoe UI", 14, "bold"), anchor="w").pack(fill=X, padx=16, pady=(16, 8))
        self.admin_user_search_var = tk.StringVar(value="")
        search_wrap = tk.Frame(left, bg=CONTROL_BG)
        search_wrap.pack(fill=X, padx=14, pady=(0, 10))
        tk.Entry(search_wrap, textvariable=self.admin_user_search_var, relief="flat", bg=CONTROL_BG, fg=TEXT, insertbackground=BLUE).pack(fill=X, padx=10, ipady=7)
        self.admin_user_search_var.trace_add("write", lambda *_: self._populate_admin_tree(select_user=self._selected_admin_username()))

        tree_frame = tk.Frame(left, bg=PANEL)
        tree_frame.pack(fill=BOTH, expand=True, padx=14, pady=(0, 12))
        self.admin_tree = ttk.Treeview(tree_frame, columns=("user", "role", "upload", "access"), show="headings", selectmode="browse")
        self.admin_tree.heading("user", text="Usuario")
        self.admin_tree.heading("role", text="Rol")
        self.admin_tree.heading("upload", text="Subida")
        self.admin_tree.heading("access", text="Acceso")
        self.admin_tree.column("user", width=150)
        self.admin_tree.column("role", width=82, stretch=False)
        self.admin_tree.column("upload", width=70, stretch=False)
        self.admin_tree.column("access", width=120)
        self.admin_tree.pack(side=LEFT, fill=BOTH, expand=True)
        admin_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.admin_tree.yview)
        self.admin_tree.configure(yscrollcommand=admin_scroll.set)
        admin_scroll.pack(side=RIGHT, fill=tk.Y)
        self.admin_tree.bind("<<TreeviewSelect>>", lambda _event: self._render_selected_admin_user())

        left_buttons = tk.Frame(left, bg=PANEL)
        left_buttons.pack(fill=X, padx=14, pady=(0, 16))
        self._admin_button(left_buttons, "Nuevo", self._new_user, accent=True).pack(side=LEFT)
        self._admin_button(left_buttons, "Eliminar", self._del_user, danger=True).pack(side=LEFT, padx=(8, 0))

        self.admin_form = tk.Frame(right, bg=PANEL)
        self.admin_form.pack(fill=BOTH, expand=True, padx=18, pady=18)
        self._load_admin_data(select_user=self.access.get("username"))

    def _selected_admin_username(self):
        if not hasattr(self, "admin_tree"):
            return None
        sel = self.admin_tree.selection()
        if not sel:
            return None
        values = self.admin_tree.item(sel[0], "values")
        return values[0] if values else None

    def _refresh_admin_data(self):
        selected = self._selected_admin_username()
        self.admin_folder_cache = {}
        self._load_admin_data(select_user=selected)

    def _load_admin_data(self, select_user=None):
        try:
            self.admin_data = self._admin_load_syncremote()
            self._populate_admin_tree(select_user=select_user)
        except Exception as exc:
            messagebox.showerror(APP_NAME, "No se pudo cargar syncremote.json:\n{}".format(exc), parent=self.admin_win)

    def _populate_admin_tree(self, select_user=None):
        if not hasattr(self, "admin_tree") or not hasattr(self, "admin_data"):
            return
        users = self.admin_data.get("users", {})
        roots = self.admin_data.get("roots", [])
        query = self.admin_user_search_var.get().strip().lower() if hasattr(self, "admin_user_search_var") else ""
        self.admin_tree.delete(*self.admin_tree.get_children())
        self.admin_tree_user_iids = {}
        for username in sorted(users.keys(), key=str.lower):
            if query and query not in username.lower():
                continue
            info = users[username]
            upload = "Si" if info.get("directUpload") or is_admin_role(info.get("role")) else "No"
            access = user_access_summary(info, roots)
            iid = self.admin_tree.insert("", END, values=(username, info.get("role", "user"), upload, access))
            self.admin_tree_user_iids[username] = iid
        target = select_user if select_user in self.admin_tree_user_iids else (next(iter(self.admin_tree_user_iids), None))
        if target:
            self.admin_tree.selection_set(self.admin_tree_user_iids[target])
            self.admin_tree.see(self.admin_tree_user_iids[target])
            self._render_admin_form(target)
        else:
            self._render_admin_form("")

    def _render_selected_admin_user(self):
        sel = self.admin_tree.selection()
        if not sel:
            return
        username = self.admin_tree.item(sel[0], "values")[0]
        self._render_admin_form(username)

    def _new_user(self):
        self.admin_tree.selection_remove(self.admin_tree.selection())
        self._render_admin_form("")

    def _edit_user(self):
        self._render_selected_admin_user()

    def _del_user(self):
        sel = self.admin_tree.selection()
        if not sel:
            return
        username = self.admin_tree.item(sel[0], "values")[0]
        if username == self.access.get("username"):
            messagebox.showwarning(APP_NAME, "No puedes borrar el usuario con el que estas conectado.", parent=self.admin_win)
            return
        data = self.admin_data
        users = data.get("users", {})
        if username not in users:
            return
        superusers = [u for u, info in users.items() if is_admin_role(info.get("role"))]
        if username in superusers and len(superusers) <= 1:
            messagebox.showwarning(APP_NAME, "Debe quedar al menos un superuser.", parent=self.admin_win)
            return
        if not messagebox.askyesno(APP_NAME, "Eliminar a {}?".format(username), parent=self.admin_win):
            return
        users.pop(username, None)
        try:
            self._admin_save_syncremote(data)
            self._load_admin_data()
            self._full_refresh()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.admin_win)

    def _render_admin_form(self, username):
        for widget in self.admin_form.winfo_children():
            widget.destroy()

        is_new = not username
        users = self.admin_data.get("users", {}) if hasattr(self, "admin_data") else {}
        info = dict(users.get(username, {}))
        if is_new:
            info = {"password": "", "role": "user", "directUpload": False, "projectAccess": {}, "groups": []}

        title = "Nuevo usuario" if is_new else username
        tk.Label(self.admin_form, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 18, "bold"), anchor="w").pack(fill=X)
        tk.Label(self.admin_form, text="Permisos guardados en R:\\syncremote.json", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(fill=X, pady=(2, 16))

        fields = tk.Frame(self.admin_form, bg=PANEL)
        fields.pack(fill=X)
        fields.grid_columnconfigure(1, weight=1)

        def labeled_entry(row, label, var, show=""):
            tk.Label(fields, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"), anchor="w").grid(row=row, column=0, sticky="w", pady=(0, 10), padx=(0, 12))
            wrap = tk.Frame(fields, bg=CONTROL_BG)
            wrap.grid(row=row, column=1, sticky="ew", pady=(0, 10))
            entry = tk.Entry(wrap, textvariable=var, relief="flat", bg=CONTROL_BG, fg=TEXT, insertbackground=BLUE, show=show)
            entry.pack(fill=X, padx=10, ipady=7)
            return entry

        username_var = tk.StringVar(value=username)
        password_var = tk.StringVar(value=info.get("password", ""))
        role_var = tk.StringVar(value=info.get("role", "user"))
        direct_upload_var = tk.BooleanVar(value=bool(info.get("directUpload", False)))

        user_entry = labeled_entry(0, "Usuario", username_var)
        if not is_new:
            user_entry.configure(state=tk.DISABLED)
        labeled_entry(1, "Contrasena", password_var, "*")

        tk.Label(fields, text="Rol", bg=PANEL, fg=MUTED, font=("Segoe UI", 9, "bold"), anchor="w").grid(row=2, column=0, sticky="w", pady=(0, 10), padx=(0, 12))
        role_box = ttk.Combobox(fields, textvariable=role_var, values=["user", "admin", "superuser"], state="readonly")
        role_box.grid(row=2, column=1, sticky="ew", pady=(0, 10))

        tk.Checkbutton(
            fields,
            text="Puede subir directo sin aprobacion",
            variable=direct_upload_var,
            bg=PANEL,
            fg=TEXT,
            selectcolor=PANEL,
            relief="flat",
            anchor="w",
        ).grid(row=3, column=1, sticky="w", pady=(0, 14))

        tk.Label(self.admin_form, text="Acceso a carpetas", bg=PANEL, fg=TEXT, font=("Segoe UI", 13, "bold"), anchor="w").pack(fill=X, pady=(8, 6))
        folder_search_var = tk.StringVar(value="")
        folder_search_wrap = tk.Frame(self.admin_form, bg=CONTROL_BG)
        folder_search_wrap.pack(fill=X, pady=(0, 12))
        tk.Entry(folder_search_wrap, textvariable=folder_search_var, relief="flat", bg=CONTROL_BG, fg=TEXT, insertbackground=BLUE).pack(fill=X, padx=10, ipady=7)
        folder_tile_groups = []

        def apply_folder_filter(*_):
            query = folder_search_var.get().strip().lower()
            for group in folder_tile_groups:
                visible = 0
                root_label = group["label"].lower()
                for tile, folder in group["tiles"]:
                    if not query or query in folder.lower() or query in root_label:
                        col = visible % 2
                        tile.grid(row=visible // 2, column=col, sticky="ew", padx=(0, 8) if col == 0 else (8, 0), pady=4)
                        visible += 1
                    else:
                        tile.grid_remove()

        footer = tk.Frame(self.admin_form, bg=PANEL)
        footer.pack(side=tk.BOTTOM, fill=X, pady=(14, 0))
        tk.Label(
            footer,
            text="Los cambios se aplican al guardar.",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(side=LEFT, fill=X, expand=True)
        self._admin_button(
            footer,
            "Guardar cambios",
            lambda: self._save_admin_user(username, username_var, password_var, role_var, direct_upload_var, access_vars),
            accent=True,
        ).pack(side=RIGHT)

        access_wrap = tk.Frame(self.admin_form, bg=PANEL)
        access_wrap.pack(fill=BOTH, expand=True)
        access_canvas = tk.Canvas(access_wrap, bg=PANEL, highlightthickness=0)
        access_scroll = ttk.Scrollbar(access_wrap, orient="vertical", command=access_canvas.yview)
        access_body = tk.Frame(access_canvas, bg=PANEL)
        access_body.bind("<Configure>", lambda _e: access_canvas.configure(scrollregion=access_canvas.bbox("all")))
        access_window = access_canvas.create_window((0, 0), window=access_body, anchor="nw")
        access_canvas.bind("<Configure>", lambda e: access_canvas.itemconfigure(access_window, width=e.width))
        access_canvas.configure(yscrollcommand=access_scroll.set)
        access_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        access_scroll.pack(side=RIGHT, fill=tk.Y)

        def wheel_scroll(event):
            delta = event.delta
            if delta:
                steps = -1 * max(1, abs(delta) // 120) * (1 if delta > 0 else -1)
            else:
                steps = -1 if getattr(event, "num", None) == 4 else 1
            access_canvas.yview_scroll(steps, "units")
            return "break"

        def bind_wheel(_event):
            access_canvas.bind_all("<MouseWheel>", wheel_scroll)
            access_canvas.bind_all("<Button-4>", wheel_scroll)
            access_canvas.bind_all("<Button-5>", wheel_scroll)

        def unbind_wheel(_event):
            access_canvas.unbind_all("<MouseWheel>")
            access_canvas.unbind_all("<Button-4>")
            access_canvas.unbind_all("<Button-5>")

        access_canvas.bind("<Enter>", bind_wheel)
        access_canvas.bind("<Leave>", unbind_wheel)
        access_body.bind("<Enter>", bind_wheel)
        access_body.bind("<Leave>", unbind_wheel)

        access_vars = {}
        existing_access = info.get("projectAccess", {})
        if isinstance(existing_access, list):
            # Formato antiguo: se muestra sin seleccionar para evitar entregar
            # todo el disco por accidente.
            existing_access = {}
        elif not isinstance(existing_access, dict):
            existing_access = {}

        for root in self.admin_data.get("roots", []):
            root_id = root.get("id")
            existing = project_access_for_root(existing_access, root) or []
            all_var = tk.BooleanVar(value=existing == "*")
            folder_vars = {}
            access_vars[root_id] = {"all": all_var, "folders": folder_vars}
            tile_group = {"label": root_display_label(root), "tiles": []}
            folder_tile_groups.append(tile_group)

            section = tk.Frame(access_body, bg=SECTION_BG, highlightbackground=CARD_BORDER, highlightthickness=1)
            section.pack(fill=X, pady=(0, 12), padx=(0, 2))
            section_header = tk.Frame(section, bg=SECTION_BG)
            section_header.pack(fill=X, padx=14, pady=(12, 6))
            tk.Label(section_header, text=root_display_label(root), bg=SECTION_BG, fg=TEXT, font=("Segoe UI", 12, "bold"), anchor="w").pack(side=LEFT, fill=X, expand=True)
            all_check = tk.Checkbutton(section_header, text="Todo el disco", variable=all_var, bg=SECTION_BG, fg=BLUE, selectcolor=SECTION_BG, relief="flat", anchor="e", font=("Segoe UI", 9, "bold"))
            all_check.pack(side=RIGHT)

            try:
                folders = self._admin_root_folders(root)
            except Exception as exc:
                folders = []
                tk.Label(section, text="No se pudo leer este disco: {}".format(exc), bg=SECTION_BG, fg=RED, anchor="w", wraplength=560).pack(fill=X, padx=14, pady=(0, 8))

            if not folders:
                tk.Label(section, text="Sin carpetas detectadas.", bg=SECTION_BG, fg=MUTED, anchor="w").pack(fill=X, padx=14, pady=(0, 12))

            folders_grid = tk.Frame(section, bg=SECTION_BG)
            folders_grid.pack(fill=X, padx=14, pady=(0, 12))
            folders_grid.grid_columnconfigure(0, weight=1)
            folders_grid.grid_columnconfigure(1, weight=1)

            def sync_folder_state(root_state=access_vars[root_id]):
                folder_values = list(root_state["folders"].values())
                root_state["all"].set(bool(folder_values) and all(var.get() for var in folder_values))

            existing_names = {str(name).casefold() for name in existing} if isinstance(existing, list) else set()
            for index, folder in enumerate(folders):
                checked = existing == "*" or folder.casefold() in existing_names
                var = tk.BooleanVar(value=checked)
                folder_vars[folder] = var
                tile = tk.Frame(folders_grid, bg=TILE_BG, highlightbackground=CARD_BORDER, highlightthickness=1)
                tile.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 8) if index % 2 == 0 else (8, 0), pady=4)
                cb = tk.Checkbutton(
                    tile,
                    text=folder,
                    variable=var,
                    command=sync_folder_state,
                    bg=TILE_BG,
                    fg=TEXT,
                    selectcolor=TILE_BG,
                    activebackground=TILE_BG,
                    activeforeground=TEXT,
                    relief="flat",
                    anchor="w",
                    font=("Segoe UI", 9),
                )
                cb.pack(fill=X, padx=10, pady=6)
                tile_group["tiles"].append((tile, folder))

            def sync_all(root_state=access_vars[root_id]):
                checked = root_state["all"].get()
                for folder_var in root_state["folders"].values():
                    folder_var.set(checked)

            all_check.configure(command=sync_all)

        folder_search_var.trace_add("write", apply_folder_filter)
        apply_folder_filter()

    def _save_admin_user(self, original_username, username_var, password_var, role_var, direct_upload_var, access_vars):
        username = username_var.get().strip()
        if not username:
            messagebox.showwarning(APP_NAME, "El usuario no puede estar vacio.", parent=self.admin_win)
            return
        if not password_var.get():
            messagebox.showwarning(APP_NAME, "La contrasena no puede estar vacia.", parent=self.admin_win)
            return

        data = normalize_syncremote(self.admin_data)
        users = data.setdefault("users", {})
        if not original_username and username in users:
            messagebox.showwarning(APP_NAME, "Ese usuario ya existe.", parent=self.admin_win)
            return

        existing = users.get(original_username or username, {})
        project_access = {}
        for root_id, state in access_vars.items():
            if state["all"].get():
                project_access[root_id] = "*"
            else:
                selected = [name for name, var in state["folders"].items() if var.get()]
                if selected:
                    project_access[root_id] = selected

        if original_username and original_username != username:
            users.pop(original_username, None)

        users[username] = {
            "password": password_var.get(),
            "role": role_var.get(),
            "directUpload": bool(direct_upload_var.get()),
            "projectAccess": project_access,
            "groups": existing.get("groups", []),
        }

        try:
            self._admin_save_syncremote(data)
            self.admin_data = data
            self._load_admin_data(select_user=username)
            self._full_refresh()
            self.set_status("Usuario guardado: {}".format(username))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.admin_win)

    def _user_form(self, username="", info=None):
        self._render_admin_form(username)

if __name__ == "__main__":

    if "--check-installation" not in sys.argv:
        from diagnostics import configure
        configure(APP_VERSION)

    if "--check-installation" in sys.argv:
        try:
            from cloud_files import NativeCloud
            from windows_session import SavedSession
            from shell_integration import hub_path
            NativeCloud()
        except Exception:
            sys.exit(2)
        sys.exit(0)

    for action in ("download", "prepare", "upload", "unpin"):
        flag = "--explorer-" + action
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            target = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else ""
            sys.exit(send_explorer_command(action, target))

    if sys.platform.startswith("win"):
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        instance_mutex = kernel.CreateMutexW(None, False, "Local\\RemoteBelieve-" + hashlib.sha256(str(DATA_DIR).lower().encode()).hexdigest()[:20])
        if ctypes.get_last_error() == 183:
            user32 = ctypes.WinDLL("user32")
            user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
            user32.FindWindowW.restype = ctypes.c_void_p
            window = user32.FindWindowW(None, APP_NAME)
            if window:
                user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
                user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
                user32.ShowWindow(window, 9)
                user32.SetForegroundWindow(window)
            sys.exit(0)

    app = MeshClientApp()
    app.protocol("WM_DELETE_WINDOW", app._on_close)
    if "--start-minimized" in sys.argv:
        app.after(600, app._hide_to_tray)
    app.mainloop()
