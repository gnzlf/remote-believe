"""Windows files on demand. All remote reads still use the Office/Tailscale API."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from cache_paths import local_exists
from transfer_control import TransferControl, TransferStopped, background_reader
from content_hash import file_hash

PARTIAL = 0x10
PLACEHOLDER = 0x1
IN_SYNC = 0x8
UNAVAILABLE = ctypes.c_long(0xC000CF11).value
DENIED = ctypes.c_long(0xC000CF18).value
FAILED = ctypes.c_long(0xC000CF12).value
FILETIME_EPOCH = 116444736000000000
CHUNK = 4 * 1024 * 1024


class CloudError(RuntimeError):
    pass


class NativeCloud:
    def __init__(self, dll_path=None):
        if sys.platform != "win32":
            raise CloudError("Los archivos a demanda requieren Windows 10/11.")
        path = Path(dll_path) if dll_path else Path(__file__).parent / "native" / "RemoteBelieveCloud.dll"
        self.dll = ctypes.CDLL(str(path))
        self.callback_type = ctypes.WINFUNCTYPE(None, ctypes.c_int, ctypes.c_ulonglong,
                                               ctypes.c_void_p, ctypes.c_ulong, ctypes.c_longlong,
                                               ctypes.c_longlong, ctypes.c_longlong, ctypes.c_wchar_p)
        signatures = {
            "RBConnect": ([ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong, self.callback_type, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_long),
            "RBDisconnect": ([ctypes.c_void_p], None),
            "RBUnregister": ([ctypes.c_wchar_p], ctypes.c_long),
            "RBFinish": ([ctypes.c_ulonglong, ctypes.c_long], ctypes.c_long),
            "RBPending": ([ctypes.c_ulonglong], ctypes.c_int),
            "RBRelease": ([ctypes.c_ulonglong], None),
            "RBTransfer": ([ctypes.c_ulonglong, ctypes.c_void_p, ctypes.c_longlong, ctypes.c_longlong], ctypes.c_long),
            "RBCreate": ([ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong,
                          ctypes.c_longlong, ctypes.c_longlong, ctypes.c_int], ctypes.c_long),
            "RBConvert": ([ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int], ctypes.c_long),
            "RBUpdate": ([ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_longlong,
                          ctypes.c_longlong, ctypes.c_int, ctypes.c_int], ctypes.c_long),
            "RBMarkInSync": ([ctypes.c_wchar_p], ctypes.c_long),
            "RBHydrate": ([ctypes.c_wchar_p], ctypes.c_long),
            "RBState": ([ctypes.c_wchar_p], ctypes.c_ulong),
            "RBSetPin": ([ctypes.c_wchar_p, ctypes.c_int], ctypes.c_long),
            "RBRegisterShell": ([ctypes.c_wchar_p, ctypes.c_wchar_p], ctypes.c_long),
            "RBUnregisterShell": ([ctypes.c_wchar_p], ctypes.c_long),
            "RBShortcut": ([ctypes.c_wchar_p, ctypes.c_wchar_p], ctypes.c_long),
            "RBShellChanged": ([ctypes.c_wchar_p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, result

    @staticmethod
    def check(hr, action):
        if hr < 0:
            raise CloudError("{} (Windows 0x{:08X})".format(action, hr & 0xFFFFFFFF))
        return hr

    def connect(self, path, identity, handler):
        def callback(kind, request_id, blob, size, file_size, offset, length, process):
            try:
                data = json.loads(ctypes.string_at(blob, size).decode("utf-8"))
                handler(kind, request_id, data, file_size, offset, length, process)
            except Exception:
                if request_id:
                    self.dll.RBFinish(request_id, FAILED)
        callback_ref = self.callback_type(callback)
        result = ctypes.c_void_p()
        raw = json.dumps(identity, ensure_ascii=False).encode("utf-8")
        self.check(self.dll.RBConnect(str(path), raw, len(raw), callback_ref, ctypes.byref(result)), "No se pudo conectar el Explorador")
        return result, callback_ref

    def state(self, path):
        return self.dll.RBState(str(path))

    def create(self, parent, name, identity, item):
        raw = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.check(self.dll.RBCreate(str(parent), name, raw, len(raw), int(item.get("size") or 0),
                                    identity["filetime"], item.get("type") == "directory"), "No se pudo publicar " + name)

    def convert(self, path, identity, directory, in_sync):
        raw = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.check(self.dll.RBConvert(str(path), raw, len(raw), directory, in_sync), "No se pudo integrar " + str(path))

    def update(self, path, identity, directory, dehydrate):
        raw = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.check(self.dll.RBUpdate(str(path), raw, len(raw), identity["size"], identity["filetime"], directory, dehydrate),
                   "No se pudo actualizar " + str(path))


class CloudFilesEngine:
    def __init__(self, store, api, allowed, modified_time, downloaded, report=None, native=None):
        self.store, self.api, self.allowed = store, api, allowed
        self.modified_time, self.downloaded = modified_time, downloaded
        self.report = report or (lambda *args: None)
        self.native = native or NativeCloud()
        self.controls = getattr(api, "transfers", None) or TransferControl()
        self.shell_ids = {}
        self.roots, self.connections = {}, {}
        self.callbacks = []  # Keep native callback pointers alive until disconnected.
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="BelieveExplorer")
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.hydrating = set()
        self.populated = {}
        self.remote_items = {}
        self.refresh_thread = None
        self.deleted_paths = {}
        self.deletion_workers = set()
        self.cancelled_paths = set()

    def identity(self, root, rp, item=None):
        item = item or {}
        timestamp = self.modified_time(item.get("modifiedAt")) or time.time()
        return {"root": root["id"], "path": rp, "size": int(item.get("size") or 0),
                "modifiedAt": item.get("modifiedAt", ""), "filetime": int(timestamp * 10000000) + FILETIME_EPOCH}

    def start(self, roots, background=True):
        self.store.reconcile_local_deletions(roots)
        for root in roots:
            root_id = root["id"]
            try:
                self.store.ensure_destination(root, "", directory=True).mkdir(parents=True, exist_ok=True)
                self.roots[root_id] = root
                connection, callback = self.native.connect(self.store.root_dir(root).resolve(), self.identity(root, ""), self._event)
                self.connections[root_id] = connection
                self.callbacks.append(callback)
                shell_id = "RemoteBelieve!" + hashlib.sha256(str(self.store.root_dir(root).resolve()).casefold().encode()).hexdigest()[:32]
                self.shell_ids[root_id] = shell_id
                self.native.check(self.native.dll.RBRegisterShell(str(self.store.root_dir(root).resolve()), shell_id), "No se pudo registrar el estado visual del Explorador")
                self.native.dll.RBSetPin(str(self.store.root_dir(root)), 0)
                self.populate(root_id, "")
            except Exception as exc:
                self.report("error", "No se pudo conectar {} al Explorador".format(root.get("name", root_id)), str(exc))
                connection = self.connections.pop(root_id, None)
                if connection:
                    self.native.dll.RBDisconnect(connection)
                self.roots.pop(root_id, None)
        if not self.connections:
            raise CloudError("No hay carpetas disponibles en el Explorador. Revisa la conexion y el estado de los discos.")
        for record in self.store.settings.get("cloud_populated_folders", []):
            root_id, rp = record.get("root"), record.get("path", "")
            if (root_id in self.roots and self.allowed(root_id, rp)
                    and local_exists(self.store.local_path(self.roots[root_id], rp)) is True):
                self.populated[(root_id, rp.casefold())] = rp
        if background:
            self.executor.submit(self.refresh)
        if background:
            self.refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True, name="BelieveExplorerRefresh")
            self.refresh_thread.start()

    def _event(self, kind, request_id, identity, size, offset, length, process=""):
        if kind == 2:
            if not self.stop_event.is_set():
                root_id, rp = identity.get("root"), identity.get("path", "")
                if root_id in self.roots and local_exists(self.store.local_path(self.roots[root_id], rp)) is False:
                    self.controls.cancel_path("download", root_id, rp)
                    with self.lock:
                        self.store.record_local_deletion(root_id, rp)
                        self._forget_deleted()
                    self.report("info", "Eliminado solo en este PC", rp)
                    with self.lock:
                        pending = self.deleted_paths.setdefault(root_id, {"paths": set(), "time": 0})
                        pending["paths"].add(rp)
                        pending["time"] = time.monotonic()
                        if root_id not in self.deletion_workers:
                            self.deletion_workers.add(root_id)
                            self.executor.submit(self._restore_listing, root_id)
            return
        if self.stop_event.is_set():
            self.native.dll.RBFinish(request_id, UNAVAILABLE)
            return
        if kind == 0 and (self.controls.is_paused("download") or background_reader(process)):
            self.native.dll.RBFinish(request_id, UNAVAILABLE)
            return
        self.executor.submit(self._handle, kind, request_id, identity, size, offset, length)

    def _restore_listing(self, root_id):
        while not self.stop_event.wait(0.5):
            with self.lock:
                pending = self.deleted_paths.get(root_id)
                if not pending:
                    self.deletion_workers.discard(root_id)
                    return
                if time.monotonic() - pending["time"] < 0.4:
                    continue
                parents = {rp.rpartition("/")[0] for rp in pending["paths"]}
                parents = [rp for rp in parents if local_exists(self.store.local_path(self.roots[root_id], rp)) is True]
                self.deleted_paths.pop(root_id, None)
            try:
                for parent in sorted(parents, key=lambda rp: rp.count("/")):
                    if not self.stop_event.is_set():
                        self.populate(root_id, parent)
            except Exception as exc:
                self.report("info", "Catalogo pendiente de actualizar", str(exc))
        self.deletion_workers.discard(root_id)

    def _handle(self, kind, request_id, identity, size, offset, length):
        root_id, rp = identity.get("root"), identity.get("path", "")
        try:
            if root_id not in self.roots or not self.allowed(root_id, rp):
                self.native.dll.RBFinish(request_id, DENIED)
                return
            if kind == 1:
                self.populate(root_id, rp)
                self.native.check(self.native.dll.RBFinish(request_id, 0), "No se pudo mostrar la carpeta")
            else:
                self._fetch(request_id, identity, size, offset, length)
        except TransferStopped:
            self.cancelled_paths.add(self.store.key(root_id, rp))
            self.native.dll.RBFinish(request_id, UNAVAILABLE)
            self.report("cancelled", "Descarga detenida: " + rp, rp)
        except Exception as exc:
            self.native.dll.RBFinish(request_id, UNAVAILABLE)
            self.report("error", "No se pudo abrir " + rp, str(exc))

    def populate(self, root_id, rp):
        if not self.allowed(root_id, rp):
            raise CloudError("Sin permiso para esta carpeta.")
        root = self.roots[root_id]
        data = self.api.list_folder(root_id, rp)
        with self.lock:
            known = not rp or self.store.key(root_id, rp) in self.store.manifest or (root_id, rp.casefold()) in self.populated
            if known and local_exists(self.store.local_path(root, rp)) is False:
                self.store.record_local_deletion(root_id, rp)
                self._forget_deleted()
            parent = self.store.ensure_destination(root, rp, directory=True)
            parent.mkdir(parents=True, exist_ok=True)
            items = data.get("items", [])
            visible_names = {item.get("name", "").casefold() for item in items}
            for key in list(self.remote_items):
                prefix = root_id + "::"
                if key.startswith(prefix):
                    old_rp = key[len(prefix):]
                    if old_rp.rpartition("/")[0].casefold() == rp.casefold() and old_rp.rpartition("/")[2].casefold() not in visible_names:
                        self.remote_items.pop(key, None)
            for item in items:
                name = item.get("name", "")
                if not name or name in (".", "..") or any(char in name for char in '/\\:\x00'):
                    continue
                child = rp + "/" + name if rp else name
                if not self.allowed(root_id, child):
                    continue
                try:
                    self._publish(root, child, item)
                except Exception as exc:
                    self.report("error", "No se pudo publicar " + child, str(exc))
            folder_key = (root_id, rp.casefold())
            is_new = folder_key not in self.populated
            self.populated[folder_key] = rp
            if is_new:
                records = self.store.settings.setdefault("cloud_populated_folders", [])
                if not any(record.get("root") == root_id and record.get("path", "").casefold() == rp.casefold() for record in records):
                    records.append({"root": root_id, "path": rp})
                    self.store.save_settings()
            self.store.save_manifest()

    def _publish(self, root, rp, item):
        directory = item.get("type") == "directory"
        path = self.store.ensure_destination(root, rp, directory=directory)
        key = self.store.key(root["id"], rp)
        identity = self.identity(root, rp, item)
        old = self.store.manifest.get(key, {})
        self.remote_items[key] = dict(item)
        exists = path.exists()
        if old and not exists:
            if directory:
                self.store.record_local_deletion(root["id"], rp)
            elif not self.store.is_local_deleted(root["id"], rp):
                return  # Wait for a deletion notification or the end of an atomic save.
        state = self.native.state(path) if exists else 0
        if exists and not directory and old.get("placeholder") and not old.get("cloudPlaceholder") and not state & PLACEHOLDER and path.stat().st_size == 0:
            path.unlink()  # Upgrade only a recorded, empty proxy from older clients.
            self.store.remove_folder_marker_entry(path)
            exists, state = False, 0
        if not exists:
            self.native.create(path.parent, path.name, identity, item)
            self.native.check(self.native.dll.RBSetPin(str(path), 2), "No se pudo marcar como disponible para descargar")
            self.native.dll.RBShellChanged(str(path))
        elif not state & PLACEHOLDER:
            in_sync = directory or self.store.manifest_has_current_local_snapshot(root["id"], rp, path)
            self.native.convert(path, identity, directory, in_sync)
            self.native.dll.RBShellChanged(str(path))
        elif not directory:
            changed = old.get("modifiedAt") != item.get("modifiedAt") or old.get("size") != item.get("size")
            if changed and state & IN_SYNC and key not in self.hydrating and not self.store.is_upload_pending(root["id"], rp):
                self.native.update(path, identity, directory=False, dehydrate=True)
            elif (not changed and not state & (IN_SYNC | PARTIAL) and old.get("sha256")
                  and not self.store.is_upload_pending(root["id"], rp) and file_hash(path) == old["sha256"]):
                self.native.dll.RBMarkInSync(str(path))
                self.native.update(path, identity, directory=False, dehydrate=False)
        if exists and state & PLACEHOLDER and old.get("pinPolicyVersion") != 1:
            self.native.check(self.native.dll.RBSetPin(str(path), 0), "No se pudo quitar una marca offline anterior")
        entry = dict(old)
        entry["pinPolicyVersion"] = 1
        entry.update({"cloudPlaceholder": True, "cloudDirectory": directory, "localPath": str(path)})
        if directory or not exists or self.native.state(path) & PARTIAL:
            entry.update({"placeholder": not directory, "size": item.get("size"), "modifiedAt": item.get("modifiedAt")})
        self.store.manifest[key] = entry

    def _fetch(self, request_id, identity, size, offset, length):
        with self.controls.operation("download", identity["root"], identity["path"]) as operation:
            operation.total = size
            self._fetch_payload(request_id, identity, size, offset, length, operation)

    def _fetch_payload(self, request_id, identity, size, offset, length, operation):
        root_id, rp = identity["root"], identity["path"]
        # An application opening a visible file requests its contents. Background
        # downloads are gated separately by hydrate(explicit=False) and offline sync.
        root = self.roots[root_id]
        path = self.store.local_path(root, rp)
        key = self.store.key(root_id, rp)
        self.hydrating.add(key)
        self.report("download", "Descargando " + Path(rp).name, rp)
        digest = hashlib.sha256()
        received = 0
        try:
            params = {"root": root_id, "path": rp, "expectedSize": str(size), "expectedModifiedAt": identity.get("modifiedAt", "")}
            with self.api._req("GET", "/api/download", params=params, timeout=45) as response:
                if int(response.headers.get("Content-Length", -1)) != size:
                    raise CloudError("Oficina cambio el archivo. Actualiza la carpeta y vuelve a abrirlo.")
                while received < size:
                    if self.stop_event.is_set() or not self.native.dll.RBPending(request_id):
                        raise CloudError("Descarga cancelada.")
                    operation.check()
                    data = response.read(min(CHUNK, size - received))
                    operation.check()
                    if not data:
                        raise CloudError("La descarga quedo incompleta.")
                    # Transfer offsets/lengths must be 4K aligned except at EOF.
                    while len(data) % 4096 and received + len(data) < size:
                        extra = response.read(min(4096 - len(data) % 4096, size - received - len(data)))
                        if not extra:
                            raise CloudError("La descarga quedo incompleta.")
                        data += extra
                    digest.update(data)
                    self.native.check(self.native.dll.RBTransfer(request_id, data, received, len(data)), "Windows no pudo recibir el archivo")
                    received += len(data)
                    operation.done = received
                    self.report("progress", rp, (received, size))
            if size == 0:
                self.native.check(self.native.dll.RBTransfer(request_id, None, 0, 0), "No se pudo abrir el archivo vacio")
            item = {"name": Path(rp).name, "size": size, "modifiedAt": identity.get("modifiedAt", ""), "type": "file"}
            self.downloaded(root, rp, item, path, digest.hexdigest())
            self.native.dll.RBMarkInSync(str(path))
            self.native.dll.RBShellChanged(str(path))
            self.report("success", "Disponible: " + Path(rp).name, rp)
            self.native.dll.RBRelease(request_id)
        finally:
            self.hydrating.discard(key)

    def is_unhydrated(self, path):
        return bool(self.native.state(path) & PARTIAL)

    def is_hydrating(self, root_id, rp):
        return self.store.key(root_id, rp) in self.hydrating

    def hydrate(self, root, rp, item=None, explicit=True):
        if self.controls.is_paused("download"):
            raise TransferStopped("Las descargas estan pausadas.")
        if explicit:
            self.store.restore_local_downloads(root["id"], rp)
        else:
            raise TransferStopped("Las descargas de contenido requieren una solicitud explicita.")
        path = self.store.ensure_destination(root, rp)
        if not path.exists():
            self.populate(root["id"], rp.rpartition("/")[0])
        if self.is_unhydrated(path):
            result = self.native.dll.RBHydrate(str(path))
            key = self.store.key(root["id"], rp)
            if result < 0 and (self.controls.is_paused("download") or key in self.cancelled_paths):
                self.cancelled_paths.discard(key)
                raise TransferStopped("Descarga detenida; el archivo sigue disponible como proxy.")
            self.native.check(result, "No se pudo descargar " + rp)
        return path

    def iter_files(self, root):
        queue = [(self.store.root_dir(root), "")]
        while queue:
            directory, rp = queue.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                child = rp + "/" + entry.name if rp else entry.name
                path = Path(entry.path)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        # Enumerate cached entries, including work saved while offline.
                        queue.append((path, child))
                    elif entry.is_file(follow_symlinks=False):
                        yield path
                except OSError:
                    continue  # A single unavailable placeholder must not stop all uploads.

    def _forget_deleted(self):
        with self.lock:
            for key, rp in list(self.populated.items()):
                if rp and local_exists(self.store.local_path(self.roots[key[0]], rp)) is False:
                    self.populated.pop(key, None)

    def refresh(self):
        self.store.reconcile_local_deletions(list(self.roots.values()), live=True)
        self._forget_deleted()
        for (root_id, _key), rp in list(self.populated.items()):
            if self.stop_event.is_set():
                return
            if root_id in self.roots and self.allowed(root_id, rp):
                try:
                    self.populate(root_id, rp)
                except Exception as exc:
                    self.report("error", "Sin conexion con Oficina", str(exc))
                    return

    def _refresh_loop(self):
        while not self.stop_event.wait(30):
            self.refresh()

    def close(self, unregister=False):
        self.stop_event.set()
        for root_id in self.roots:
            self.controls.cancel_path("download", root_id, "")
        for connection in self.connections.values():
            self.native.dll.RBDisconnect(connection)
        self.connections.clear()
        self.executor.shutdown(wait=True)
        for identifier in self.shell_ids.values():
            self.native.dll.RBUnregisterShell(identifier)
        self.shell_ids.clear()
        if unregister:
            for root in self.roots.values():
                self.native.dll.RBUnregister(str(self.store.root_dir(root)))
        self.callbacks.clear()
