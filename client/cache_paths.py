"""Inspect cache paths without asking Cloud Files to resolve placeholder names."""
import os
from pathlib import Path
import stat


def _expand_short_component(path):
    if os.name != "nt" or "~" not in path.name:
        return path
    # A junction target can use long names while %TEMP% uses an 8.3 user name.
    # Expand that component's spelling, without querying a final path handle.
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel.GetLongPathNameW
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    function.restype = ctypes.c_ulong
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(str(path), buffer, len(buffer))
    if not length:
        raise ctypes.WinError(ctypes.get_last_error())
    if length >= len(buffer):
        raise OSError("La ruta local supera el limite de Windows")
    return Path(buffer.value)


def local_path_key(path):
    # Tracking keys do not need a file handle, hydration, or a physical file name.
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def local_exists(path):
    """Three states: unavailable metadata is not evidence of a local deletion."""
    try:
        return Path(path).exists()
    except OSError:
        return None


def resolve_name_links(path):
    """Resolve real links, leaving Cloud Files reparse points as ordinary entries.

    GetFinalPathNameByHandle (Path.resolve on Windows) can return
    ERROR_FLT_INVALID_NAME_REQUEST for placeholders. Cloud reparse tags are not
    name surrogates; junctions and symbolic links are, and still require the
    usual containment check. Never use a lexical fallback after a link error.
    """
    absolute = Path(path).absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        if part == "..":
            current = current.parent
            continue
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        current = _expand_short_component(current)
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_reparse_tag", 0) & 0x20000000:
            current = current.resolve()
    return current


def cache_relative_path(root, path):
    return resolve_name_links(path).relative_to(resolve_name_links(root))
