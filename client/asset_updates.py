"""Published Prism masters: metadata catalog and per-account update decisions.

Only Export/*/master is published. A newer working scene/version directory is
not a release until its master has been published. No scene/texture bytes are
read by this scanner, and paths from versioninfo are never followed or exposed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time


SCENE_EXTENSIONS = {".ma", ".mb", ".fbx", ".abc", ".usd", ".usda", ".usdc", ".blend"}
IGNORED = {"scenefiles", "textures", "renders", "playblasts", "shots", "__templates"}
PUBLISH_SETTLE_S = 30


def master_kind(path):
    parts = str(path).replace("\\", "/").strip("/").split("/")
    lower = [part.casefold() for part in parts]
    if (len(parts) < 6 or "assets" not in lower or lower[-2] != "master" or
            lower[-4] != "export" or Path(parts[-1]).suffix.casefold() not in SCENE_EXTENSIONS):
        return None
    asset_index = lower.index("assets")
    if asset_index >= len(parts) - 4:
        return None
    product = lower[-3]
    if any(re.search(r"(^|[_\W])(env|environment|environments|entorno)($|[_\W])", p)
           for p in lower[asset_index + 1:-4]):
        return "Environment"
    if re.search(r"(^|[_\W])rig(ging)?($|[_\W])", product):
        return "Rig"
    return None


def same_file(left, right):
    return left.get("size") == right.get("size") and left.get("modifiedAt") == right.get("modifiedAt")


def _safe_children(path, base, directories=True):
    # Never traverse junctions/symlinks, recycle bins, templates or other projects.
    result = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name.startswith(("@", ".", "~", "__")):
                continue
            stat = entry.stat(follow_symlinks=False)
            if entry.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                continue
            if entry.is_dir(follow_symlinks=False) != directories:
                continue
            result.append(Path(entry.path))
    return sorted(result, key=lambda p: p.name.casefold())


def scan_project(project_path, root_id, format_time, now=None):
    """Bounded metadata scan of one authorized project, not its working scenes."""
    project = Path(project_path)
    base = project.resolve()
    now = time.time() if now is None else now
    containers = [project / "03_Production" / "Assets", project / "Assets"]
    results, visited = [], 0
    for container in containers:
        if not container.is_dir() or container.is_symlink():
            continue
        container.resolve().relative_to(base)
        queue = [(container, 0)]
        while queue:
            folder, depth = queue.pop()
            visited += 1
            if visited > 10000:
                raise ValueError("El catalogo de assets excede el limite por proyecto.")
            for child in _safe_children(folder, base):
                if not child.is_dir():
                    continue
                name = child.name.casefold()
                if name in IGNORED:
                    continue
                if name != "export":
                    if depth < 4:
                        queue.append((child, depth + 1))
                    continue
                for product in _safe_children(child, base):
                    if not product.is_dir():
                        continue
                    master = product / "master"
                    if not master.is_dir() or master.is_symlink() or getattr(master.lstat(), "st_file_attributes", 0) & 0x400:
                        continue
                    info_path = master / "versioninfo.json"
                    info = {}
                    if info_path.exists():
                        info_stat = info_path.lstat()
                        if (info_path.is_symlink() or getattr(info_stat, "st_file_attributes", 0) & 0x400 or info_stat.st_size > 512 * 1024 or
                                now - info_stat.st_mtime < PUBLISH_SETTLE_S):
                            continue
                        try:
                            info = json.loads(info_path.read_text(encoding="utf-8-sig"))
                        except (ValueError, UnicodeError):
                            continue  # A publisher may still be writing the metadata.
                        if not isinstance(info, dict):
                            continue
                    version = str(info.get("version") or "master")[:80]
                    for file in _safe_children(master, base, directories=False):
                        rp = project.name + "/" + file.relative_to(project).as_posix()
                        kind = master_kind(rp)
                        if not kind or not file.is_file():
                            continue
                        stat = file.stat()
                        if stat.st_size <= 0 or now - stat.st_mtime < PUBLISH_SETTLE_S:
                            continue
                        revision = hashlib.sha256((version + ":" + str(stat.st_size) + ":" + str(stat.st_mtime_ns)).encode()).hexdigest()
                        results.append({"root": root_id, "path": rp, "project": project.name,
                                        "asset": child.parent.name, "product": product.name, "kind": kind,
                                        "version": version, "revision": revision,
                                        "item": {"name": file.name, "type": "file", "size": stat.st_size,
                                                 "modifiedAt": format_time(stat.st_mtime)}})
                        if len(results) > 5000:
                            raise ValueError("Demasiados masters en el proyecto.")
    return sorted(results, key=lambda item: item["path"].casefold())


class AssetCatalog:
    """Office cache shared by freelancers, with a second observation on changes."""
    def __init__(self, ttl=60, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self.lock = threading.Lock()
        self.projects = {}

    def get(self, path, root_id, format_time):
        key = (str(Path(path).resolve()).casefold(), root_id)
        with self.lock:
            now = self.clock()
            cached = self.projects.get(key)
            if cached and now - cached["time"] < self.ttl:
                return list(cached["items"])
            items = scan_project(path, root_id, format_time)
            previous = {item["path"]: item for item in (cached or {}).get("items", [])}
            pending = (cached or {}).get("pending", {})
            stable, next_pending = [], {}
            for item in items:
                old = previous.get(item["path"])
                if cached and (not old or old["revision"] != item["revision"]):
                    candidate = pending.get(item["path"], {})
                    if candidate.get("revision") != item["revision"] or now - candidate.get("since", now) < PUBLISH_SETTLE_S:
                        next_pending[item["path"]] = {"revision": item["revision"],
                            "since": candidate.get("since", now) if candidate.get("revision") == item["revision"] else now}
                        if old:
                            stable.append(old)
                        continue
                stable.append(item)
            # Masters temporarily absent/incomplete remain pending, never a deletion.
            found = {item["path"] for item in stable} | {item["path"] for item in items}
            for rp, old in previous.items():
                if rp in found:
                    continue
                candidate = pending.get(rp, {})
                since = candidate.get("since", now) if candidate.get("missing") else now
                if now - since < PUBLISH_SETTLE_S:
                    stable.append(old)
                    next_pending[rp] = {"missing": True, "since": since}
            self.projects[key] = {"time": now, "items": stable, "pending": next_pending}
            return list(stable)


class AssetUpdateTracker:
    def __init__(self, store, engine, state=None, save=None):
        self.store, self.engine = store, engine
        self.state = state if isinstance(state, dict) else {}
        self.save = save or (lambda state: None)
        self.records, self.rows = {}, []

    def observe(self, records):
        from cloud_files import PARTIAL, PLACEHOLDER
        notifications, rows, manifest_changed = [], [], False
        self.records = {}
        for record in records:
            root_id, rp = record["root"], record["path"]
            if not master_kind(rp) or root_id not in self.engine.roots or not self.engine.allowed(root_id, rp):
                continue
            key = self.store.key(root_id, rp)
            history = self.state.setdefault(key, {})
            root = self.engine.roots[root_id]
            path = self.store.local_path(root, rp)
            entry = self.store.manifest.get(key, {})
            try:
                exists = path.exists()
                state = self.engine.native.state(path) if exists else 0
                details = self.engine.native.inspect(path) if state & PLACEHOLDER else None
            except OSError:
                continue  # An unavailable path is not evidence of a local download.
            full = exists and bool(state & PLACEHOLDER) and not state & PARTIAL
            dirty = exists and (not state & PLACEHOLDER or not self.engine.native.clean(details) or self.store.is_upload_pending(root_id, rp))
            pending = entry.get("assetUpdatePending", {})
            downloaded = (exists and not self.store.is_local_deleted(root_id, rp) and
                          (full or (entry.get("assetDownloaded") and (pending or dirty))))
            if not downloaded:
                continue
            self.records[key] = record
            applied_revision = entry.get("assetRevision") or history.get("localRevision")
            baseline = details["identity"] if details else entry
            outdated = ((applied_revision and applied_revision != record["revision"]) or
                        not same_file(baseline, record["item"]) or bool(pending))
            current = (full and not dirty and same_file(details["identity"], record["item"])
                       and not outdated)
            if current:
                history["localVersion"] = record["version"]
                history["localRevision"] = record["revision"]
                if entry.get("assetRevision") != record["revision"]:
                    with self.engine.lock:
                        self.store.manifest.setdefault(key, {})["assetRevision"] = record["revision"]
                    manifest_changed = True
                history.pop("error", None)
                history.pop("cancelled", None)
            if not outdated:
                continue
            if history.get("notified") != record["revision"]:
                notifications.append(record)
                history["notified"] = record["revision"]
            automatic = ((full or same_file(pending, record["item"]))
                         and not current and not dirty and history.get("cancelled") != record["revision"])
            status = ("Cambios locales: se conserva" if dirty else
                      "Descarga detenida" if history.get("cancelled") == record["revision"] else
                      history.get("error") or "Actualizacion disponible")
            rows.append({**record, "key": key, "localVersion": history.get("localVersion", "—"),
                         "status": status, "current": current, "automatic": automatic})
        self.rows = rows
        if manifest_changed:
            self.store.save_manifest()
        self.save(self.state)
        return notifications

    def update(self, key, manual=False):
        from transfer_control import TransferStopped
        record = self.records[key]
        history = self.state.setdefault(key, {})
        try:
            self.engine.hydrate_asset(record, automatic=not manual)
            history.update(localVersion=record["version"], localRevision=record["revision"])
            history.pop("error", None)
            history.pop("cancelled", None)
        except TransferStopped:
            if not self.engine.controls.is_paused("download") and not self.engine.stop_event.is_set():
                history["cancelled"] = record["revision"]
            history["error"] = "Descargas pausadas" if self.engine.controls.is_paused("download") else "Descarga detenida"
            raise
        except Exception as exc:
            history["error"] = str(exc)
            raise
        finally:
            self.save(self.state)


OFFICE_ASSETS = AssetCatalog()
