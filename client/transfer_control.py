"""Per-session controls. Cancellation interrupts sockets, never deletes local work."""
import contextlib
import http.client
import socket
import threading
import time
import uuid
from urllib import request


class TransferStopped(RuntimeError):
    pass


class Operation:
    def __init__(self, controller, direction, root, path):
        self.controller, self.direction, self.root, self.path = controller, direction, root, path
        self.id = uuid.uuid4().hex
        self.stopped = threading.Event()
        self.sockets = set()
        self.lock = threading.Lock()
        self.done, self.total, self.started = 0, 0, time.time()

    def check(self):
        if self.stopped.is_set() or self.controller.is_paused(self.direction):
            raise TransferStopped("Transferencia detenida; tus archivos se conservan.")

    def track_socket(self, connection):
        with self.lock:
            self.sockets.add(connection)
        self.check()

    def cancel(self):
        self.stopped.set()
        with self.lock:
            connections = list(self.sockets)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass


class TransferControl:
    def __init__(self, preferences=None):
        self.lock = threading.RLock()
        self.paused = {key: bool((preferences or {}).get(key, False)) for key in ("upload", "download")}
        self.active = {}
        self.context = threading.local()

    def is_paused(self, direction):
        with self.lock:
            return self.paused[direction]

    def pause(self, direction):
        with self.lock:
            self.paused[direction] = True
            operations = [op for op in self.active.values() if op.direction == direction]
        for operation in operations:
            operation.cancel()

    def resume(self, direction):
        with self.lock:
            self.paused[direction] = False

    def cancel(self, identifier):
        with self.lock:
            operation = self.active.get(identifier)
        if operation:
            operation.cancel()

    def cancel_path(self, direction, root, path):
        with self.lock:
            operations = [op for op in self.active.values() if op.direction == direction and
                          op.root == root and (not path or op.path.casefold() == path.casefold() or
                          op.path.casefold().startswith(path.casefold().rstrip("/") + "/"))]
        for operation in operations:
            operation.cancel()

    def cancel_all(self):
        for direction in ("upload", "download"):
            self.pause(direction)

    @contextlib.contextmanager
    def operation(self, direction, root, path):
        operation = Operation(self, direction, root, path)
        with self.lock:
            operation.check()
            self.active[operation.id] = operation
        previous = getattr(self.context, "operation", None)
        self.context.operation = operation
        try:
            yield operation
            operation.check()
        except Exception:
            operation.check()  # Report cancellation rather than a socket error after Stop.
            raise
        finally:
            self.context.operation = previous
            with self.lock:
                self.active.pop(operation.id, None)

    def snapshot(self):
        with self.lock:
            return [{"id": op.id, "direction": op.direction, "root": op.root, "path": op.path,
                     "done": op.done, "total": op.total} for op in self.active.values()]


class ControlledHTTPHandler(request.HTTPHandler):
    def __init__(self, controls):
        super().__init__()
        self.controls = controls

    def http_open(self, req):
        operation = getattr(self.controls.context, "operation", None)
        class Connection(http.client.HTTPConnection):
            def connect(self):
                if operation:
                    operation.check()
                super().connect()
                if operation:
                    operation.track_socket(self.sock)
        return self.do_open(Connection, req)


def background_reader(process):
    name = str(process or "").replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name in {"explorer.exe", "dllhost.exe", "searchindexer.exe", "searchprotocolhost.exe",
                    "searchfilterhost.exe", "mspmpeng.exe", "msmpeng.exe", "everything.exe"}
