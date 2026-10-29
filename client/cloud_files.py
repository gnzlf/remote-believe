"""Windows files on demand. All remote reads still use the Office/Tailscale API."""
import ctypes
import hashlib
import json
import logging
import os
import http.client
from pathlib import Path
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from cache_paths import local_exists
from transfer_control import TransferControl, TransferStopped, background_reader
from office_errors import OfficeNetworkError

PARTIAL = 0x10
PLACEHOLDER = 0x1
IN_SYNC = 0x8
UNAVAILABLE = ctypes.c_long(0xC000CF11).value
ABORTED = ctypes.c_long(0xC000CF16).value
PROVIDER_STOPPED = ctypes.c_long(0xC000CF01).value
NOT_IN_SYNC = ctypes.c_long(0xC000CF08).value
TIMED_OUT = ctypes.c_long(0xC000CF1F).value
DENIED = ctypes.c_long(0xC000CF18).value
FAILED = ctypes.c_long(0xC000CF12).value
FILETIME_EPOCH = 116444736000000000
CHUNK = 64 * 1024
READ_TIMEOUT = 20
FETCH_ATTEMPTS = 3
logger = logging.getLogger("RemoteBelieve")


def failure_status(exc):
    if isinstance(exc, CloudConflict):
        return NOT_IN_SYNC
    if isinstance(exc, TransferStopped):
        return ABORTED
    if isinstance(exc, PermissionError) or getattr(exc, "status", None) in (401, 403):
        return DENIED
    if getattr(exc, "status", None) == 409:
        return NOT_IN_SYNC
    if isinstance(exc, TimeoutError):
        return TIMED_OUT
    if isinstance(exc, (OfficeNetworkError, ConnectionError)):
        return UNAVAILABLE
    return FAILED


class CloudError(RuntimeError):
    def __init__(self, message, hr=None):
        super().__init__(message)
        self.winerror = (hr & 0xFFFFFFFF) if hr is not None else None


class CloudConflict(CloudError):
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
            "RBProgress": ([ctypes.c_ulonglong, ctypes.c_longlong, ctypes.c_longlong], ctypes.c_long),
            "RBTransfer": ([ctypes.c_ulonglong, ctypes.c_void_p, ctypes.c_longlong, ctypes.c_longlong], ctypes.c_long),
            "RBCreate": ([ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong,
                          ctypes.c_longlong, ctypes.c_longlong, ctypes.c_int], ctypes.c_long),
            "RBConvert": ([ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int], ctypes.c_long),
            "RBUpdate": ([ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_longlong,
                          ctypes.c_longlong, ctypes.c_int, ctypes.c_int, ctypes.c_longlong], ctypes.c_long),
            "RBInspect": ([ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                           ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_longlong)], ctypes.c_long),
            "RBRestart": ([ctypes.c_ulonglong, ctypes.c_wchar_p, ctypes.c_longlong, ctypes.c_void_p,
                           ctypes.c_ulong, ctypes.c_longlong, ctypes.c_longlong], ctypes.c_long),
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
            raise CloudError("{} (Windows 0x{:08X})".format(action, hr & 0xFFFFFFFF), hr)
        return hr

    def connect(self, path, identity, handler):
        def callback(kind, request_id, blob, size, file_size, offset, length, process):
            try:
                data = json.loads(ctypes.string_at(blob, size).decode("utf-8"))
                handler(kind, request_id, data, file_size, offset, length, process)
            except Exception:
                logger.exception("Native cloud callback failed")
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
        details = self.inspect(path) if not directory else None
        if not directory and not self.clean(details):
            raise CloudConflict("Se conserva el archivo local con cambios: " + str(path))
        raw = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        hr = self.dll.RBUpdate(str(path), raw, len(raw), identity["size"], identity["filetime"], directory, dehydrate,
                               details["identity"]["size"] if details else 0)
        if hr & 0xFFFFFFFF in (0x80070178, 0x80070179):
            raise CloudConflict("El archivo cambio localmente; se conserva sin reemplazar: " + str(path), hr)
        self.check(hr, "No se pudo actualizar " + str(path))

    def inspect(self, path):
        raw, length = ctypes.create_string_buffer(4096), ctypes.c_ulong()
        size, modified = ctypes.c_longlong(), ctypes.c_longlong()
        hr = self.dll.RBInspect(str(path), raw, len(raw), ctypes.byref(length), ctypes.byref(size), ctypes.byref(modified))
        if hr < 0:
            return None  # Unknown state must never authorize replacing local work.
        try:
            identity = json.loads(raw.raw[:length.value].decode("utf-8"))
            return {"identity": identity, "size": size.value, "modified": modified.value}
        except (ValueError, UnicodeError):
            return None

    @staticmethod
    def clean(details):
        return bool(details and details["modified"] == 0 and
                    details["size"] == details["identity"].get("size"))

    def restart(self, request_id, path, previous_size, identity):
        raw = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.check(self.dll.RBRestart(request_id, str(path), previous_size, raw, len(raw), identity["size"], identity["filetime"]),
                   "No se pudo renovar la descarga de " + str(path))


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
        self.catalog_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="BelieveCatalog")
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.hydrating = set()
        self.populated = {}
        self.remote_items = {}
        self.refresh_thread = None
        self.deleted_paths = {}
        self.deletion_workers = set()
        self.cancelled_paths = set()
        self.request_lock = threading.Lock()
        self.pending_requests = {}
        self.keepalive_thread = None
        self.last_errors = {}
        self.restart_attempts = {}

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
        self.keepalive_thread = threading.Thread(target=self._keep_requests_alive, daemon=True, name="BelieveCloudProgress")
        self.keepalive_thread.start()
        for record in self.store.settings.get("cloud_populated_folders", []):
            root_id, rp = record.get("root"), record.get("path", "")
            if (root_id in self.roots and self.allowed(root_id, rp)
                    and local_exists(self.store.local_path(self.roots[root_id], rp)) is True):
                self.populated[(root_id, rp.casefold())] = rp
        if background:
            self.catalog_executor.submit(self.refresh)
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
                            self.catalog_executor.submit(self._restore_listing, root_id)
            return
        if self.stop_event.is_set():
            self.native.dll.RBFinish(request_id, PROVIDER_STOPPED)
            return
        explicit = kind == 3
        if explicit:
            kind = 0
        if kind == 0 and (self.controls.is_paused("download") or (background_reader(process) and not explicit)):
            self.native.dll.RBFinish(request_id, ABORTED)
            return
        if kind == 0:
            with self.request_lock:
                self.pending_requests[request_id] = {"size": size, "done": 0, "root": identity.get("root"), "path": identity.get("path", "")}
        executor = self.catalog_executor if kind == 1 else self.executor
        try:
            executor.submit(self._handle, kind, request_id, identity, size, offset, length)
        except RuntimeError:
            with self.request_lock:
                self.pending_requests.pop(request_id, None)
            self.native.dll.RBFinish(request_id, PROVIDER_STOPPED)

    def _keep_requests_alive(self):
        # Windows cancels fetch callbacks after 60s without provider progress,
        # including time spent queued or waiting for the first network bytes.
        while not self.stop_event.wait(5):
            with self.request_lock:
                pending = [(key, dict(value)) for key, value in self.pending_requests.items()]
            for request_id, progress in pending:
                if self.stop_event.is_set():
                    return
                if self.native.dll.RBPending(request_id):
                    self.native.dll.RBProgress(request_id, progress["done"], progress["size"])
                else:
                    self.controls.cancel_path("download", progress["root"], progress["path"])
                    with self.request_lock:
                        self.pending_requests.pop(request_id, None)

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
            if not self.native.dll.RBPending(request_id) or self.stop_event.is_set():
                return
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
            self.native.dll.RBFinish(request_id, ABORTED)
            self.report("cancelled", "Descarga detenida: " + rp, rp)
        except Exception as exc:
            if kind == 0 and getattr(exc, "status", None) == 409:
                try:
                    self._restart_changed(request_id, identity, size)
                    return
                except Exception as renewal_error:
                    exc = renewal_error
            # Keep actionable messages, not tracebacks retaining network buffers.
            self.last_errors[self.store.key(root_id, rp)] = CloudConflict(str(exc)) if isinstance(exc, CloudConflict) else str(exc)
            self.native.dll.RBFinish(request_id, failure_status(exc))
            logger.error("Cloud request failed: kind=%s root=%s path=%s", kind, root_id, rp,
                         exc_info=(type(exc), exc, exc.__traceback__))
            self.report("error", "No se pudo abrir " + rp, str(exc))
        finally:
            with self.request_lock:
                self.pending_requests.pop(request_id, None)

    def _restart_changed(self, request_id, previous, previous_size):
        root_id, rp = previous["root"], previous["path"]
        root, key = self.roots[root_id], self.store.key(root_id, rp)
        path = self.store.local_path(root, rp)
        if self.store.is_upload_pending(root_id, rp) or not self.native.clean(self.native.inspect(path)):
            raise CloudConflict("Se conserva el archivo con cambios locales. Los otros archivos pueden descargarse: " + rp)
        count, since = self.restart_attempts.get(key, (0, time.monotonic()))
        if time.monotonic() - since > 30:
            count, since = 0, time.monotonic()
        if count >= 2:
            raise CloudError("El archivo sigue cambiando en Oficina. Espera a que termine de guardarse: " + rp)
        item = self.api.remote_item(root_id, rp, with_hash=False)
        if not item or item.get("type") != "file":
            raise CloudError("El archivo ya no esta disponible en Oficina: " + rp)
        self.restart_attempts[key] = (count + 1, since)
        self.native.restart(request_id, path, previous_size, self.identity(root, rp, item))
        self.remote_items[key] = dict(item)
        self.report("info", "Informacion del proxy actualizada; continuando descarga", rp)

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
        applied = dict(old)
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
            applied = item
        elif not state & PLACEHOLDER:
            in_sync = directory or self.store.manifest_has_current_local_snapshot(root["id"], rp, path)
            self.native.convert(path, identity, directory, in_sync)
            self.native.dll.RBShellChanged(str(path))
        elif not directory:
            details = self.native.inspect(path)
            if details:
                applied = details["identity"]
            changed = applied.get("modifiedAt") != item.get("modifiedAt") or applied.get("size") != item.get("size")
            if (self.native.clean(details) and (changed or not state & IN_SYNC) and key not in self.hydrating
                    and not self.store.is_upload_pending(root["id"], rp)):
                try:
                    self.native.update(path, identity, directory=False, dehydrate=changed)
                    applied = item
                except CloudConflict:
                    pass  # A concurrent Maya save wins; never advance its proxy metadata.
        if exists and state & PLACEHOLDER and old.get("pinPolicyVersion") != 1:
            self.native.check(self.native.dll.RBSetPin(str(path), 0), "No se pudo quitar una marca offline anterior")
        entry = dict(old)
        entry["pinPolicyVersion"] = 1
        entry.update({"cloudPlaceholder": True, "cloudDirectory": directory, "localPath": str(path)})
        if directory or not exists or self.native.state(path) & PARTIAL:
            metadata = item if directory or not exists else applied
            entry.update({"placeholder": not directory, "size": metadata.get("size"), "modifiedAt": metadata.get("modifiedAt")})
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
        last_progress = 0
        try:
            params = {"root": root_id, "path": rp, "expectedSize": str(size), "expectedModifiedAt": identity.get("modifiedAt", "")}
            for attempt in range(FETCH_ATTEMPTS):
                operation.check()
                if self.stop_event.is_set() or not self.native.dll.RBPending(request_id):
                    raise TransferStopped("Descarga cancelada.")
                headers = {"Range": "bytes={}-".format(received)} if received else {}
                try:
                    with self.api._req("GET", "/api/download", params=params, headers=headers, timeout=READ_TIMEOUT) as response:
                        if int(response.headers.get("Content-Length", -1)) != size - received:
                            raise CloudError("Oficina cambio el archivo. Actualiza la carpeta y vuelve a abrirlo.")
                        if received and (getattr(response, "status", 200) != 206 or
                                response.headers.get("Content-Range") != "bytes {}-{}/{}".format(received, size - 1, size)):
                            raise CloudError("Oficina no confirmo el rango de reanudacion; no se mezclaron los datos.")
                        buffer = b""
                        read = getattr(response, "read1", response.read)
                        while received < size:
                            if self.stop_event.is_set() or not self.native.dll.RBPending(request_id):
                                raise TransferStopped("Descarga cancelada.")
                            operation.check()
                            data = read(min(CHUNK, size - received - len(buffer)))
                            operation.check()
                            if not data:
                                raise http.client.IncompleteRead(buffer, size - received)
                            buffer += data
                            # Keep 4K alignment without waiting for a multi-MB read.
                            count = len(buffer) if received + len(buffer) == size else len(buffer) // 4096 * 4096
                            if not count:
                                continue
                            part, buffer = buffer[:count], buffer[count:]
                            self.native.check(self.native.dll.RBTransfer(request_id, part, received, len(part)), "Windows no pudo recibir el archivo")
                            digest.update(part)
                            received += len(part)
                            operation.done = received
                            with self.request_lock:
                                if request_id in self.pending_requests:
                                    self.pending_requests[request_id]["done"] = received
                            now = time.monotonic()
                            if received == size or now - last_progress >= 0.2:
                                self.report("progress", rp, (received, size))
                                last_progress = now
                    break
                except (OSError, http.client.HTTPException) as exc:
                    operation.check()
                    if self.stop_event.is_set() or not self.native.dll.RBPending(request_id):
                        raise TransferStopped("Descarga cancelada.") from exc
                    if attempt + 1 == FETCH_ATTEMPTS:
                        raise
                    logger.warning("Retrying download at byte %s: %s (%s)", received, rp, exc)
                    self.report("info", "Reintentando descarga desde {} bytes".format(received), rp)
                    if self.stop_event.wait(0.5 * (attempt + 1)):
                        raise TransferStopped("Descarga cancelada.")
            if size == 0:
                self.native.check(self.native.dll.RBTransfer(request_id, None, 0, 0), "No se pudo abrir el archivo vacio")
            item = {"name": Path(rp).name, "size": size, "modifiedAt": identity.get("modifiedAt", ""), "type": "file"}
            self.downloaded(root, rp, item, path, digest.hexdigest())
            self.restart_attempts.pop(key, None)
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
        # Refresh only this requested file, using metadata, before hydrating.
        if self.native.state(path) & PLACEHOLDER:
            current = item or self.api.remote_item(root["id"], rp, with_hash=False)
            if current and current.get("type") == "file":
                with self.lock:
                    try:
                        self._publish(root, rp, current)
                    except CloudError as exc:
                        if exc.winerror not in (0x80070020, 0x80070021):
                            raise
                        # An active reference read can renew through the native
                        # hydration callback; do not disturb the existing handle.
                        logger.info("Reference in use; metadata renewal deferred: %s", rp)
        if self.is_unhydrated(path):
            if self.store.is_upload_pending(root["id"], rp) or not self.native.clean(self.native.inspect(path)):
                raise CloudConflict("El archivo tiene cambios locales que se conservan. No se reemplaza: " + rp)
            key = self.store.key(root["id"], rp)
            self.cancelled_paths.discard(key)
            self.last_errors.pop(key, None)
            result = self.native.dll.RBHydrate(str(path))
            if result < 0 and (self.controls.is_paused("download") or key in self.cancelled_paths):
                self.cancelled_paths.discard(key)
                raise TransferStopped("Descarga detenida; el archivo sigue disponible como proxy.")
            if result < 0 and key in self.last_errors:
                if isinstance(self.last_errors[key], CloudConflict):
                    raise self.last_errors[key]
                raise CloudError("No se pudo descargar {}: {}".format(rp, self.last_errors[key]))
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
        self.catalog_executor.shutdown(wait=True)
        self.executor.shutdown(wait=True)
        if self.keepalive_thread and self.keepalive_thread is not threading.current_thread():
            self.keepalive_thread.join(timeout=2)
        for identifier in self.shell_ids.values():
            self.native.dll.RBUnregisterShell(identifier)
        self.shell_ids.clear()
        if unregister:
            for root in self.roots.values():
                self.native.dll.RBUnregister(str(self.store.root_dir(root)))
        self.callbacks.clear()
