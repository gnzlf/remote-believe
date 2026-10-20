"""Content comparisons use complete bytes, never a filename or an mtime alone."""
import hashlib
from pathlib import Path


def file_hash(path, operation=None):
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            if operation:
                operation.check()
            data = stream.read(1024 * 1024)
            if not data:
                break
            digest.update(data)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError("El archivo cambio mientras se comparaba su contenido; se reintentara.")
    return digest.hexdigest()
