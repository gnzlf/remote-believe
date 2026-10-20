"""Remember a login using Windows DPAPI, scoped to the current Windows user."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class SavedSession:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "session.dpapi"

    @staticmethod
    def _crypt(payload, decrypt=False):
        buffer = ctypes.create_string_buffer(payload)
        source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
        if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(result.data, result.size)
        finally:
            kernel = ctypes.WinDLL("kernel32")
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree(result.data)

    def save(self, username, password, mode):
        encrypted = self._crypt(json.dumps({"username": username, "password": password, "mode": mode}).encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(encrypted)
        os.replace(temporary, self.path)

    def load(self):
        try:
            value = json.loads(self._crypt(self.path.read_bytes(), decrypt=True).decode("utf-8"))
            return value if value.get("username") and value.get("password") else None
        except (OSError, ValueError):
            return None

    def clear(self):
        self.path.unlink(missing_ok=True)
