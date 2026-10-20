"""Per-user Explorer entry and links that keep the canonical X:/R: paths."""
import json
import os
from pathlib import Path
import re
import winreg

CLSID = "{8DABAE71-6BF7-4EA9-89B0-7931164542EB}"
CLSID_KEY = "Software\\Classes\\CLSID\\" + CLSID
NAMESPACE_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Desktop\\NameSpace\\" + CLSID
HIDE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel"


def hub_path():
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "RemoteBelieveMesh" / "Explorer"


def _set(path, name, value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ, value)


def register_namespace(app_exe, directory=None):
    directory = Path(directory or hub_path())
    directory.mkdir(parents=True, exist_ok=True)
    _set(CLSID_KEY, "", "Remote Believe")
    _set(CLSID_KEY, "System.IsPinnedToNameSpaceTree", 1)
    _set(CLSID_KEY, "SortOrderIndex", 0x42)
    _set(CLSID_KEY + r"\DefaultIcon", "", str(app_exe) + ",0")
    _set(CLSID_KEY + r"\InProcServer32", "", os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "shell32.dll"))
    _set(CLSID_KEY + r"\Instance", "CLSID", "{0E5AAE11-A475-4c5b-AB00-C66DE400274E}")
    _set(CLSID_KEY + r"\Instance\InitPropertyBag", "Attributes", 0x11)
    _set(CLSID_KEY + r"\Instance\InitPropertyBag", "TargetFolderPath", str(directory))
    _set(CLSID_KEY + r"\ShellFolder", "FolderValueFlags", 0x28)
    _set(CLSID_KEY + r"\ShellFolder", "Attributes", 0xF080004D)
    _set(NAMESPACE_KEY, "", "Remote Believe")
    _set(HIDE_KEY, CLSID, 1)


def _remove_tree(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _remove_tree(path + "\\" + child)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def unregister_namespace():
    _remove_tree(NAMESPACE_KEY)
    _remove_tree(CLSID_KEY)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, HIDE_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, CLSID)
    except FileNotFoundError:
        pass


def publish_links(native, entries, directory=None):
    directory = Path(directory or hub_path())
    directory.mkdir(parents=True, exist_ok=True)
    record_path = directory.parent / "explorer-links.json"
    try:
        previous = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = []
    current = []
    for label, target in entries:
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).rstrip(" .") + ".lnk"
        if name in current:
            continue
        native.check(native.dll.RBShortcut(str(directory / name), str(target)), "No se pudo crear el acceso a " + label)
        current.append(name)
    for name in previous:
        path = directory / name
        if name not in current and path.parent.resolve() == directory.resolve() and path.suffix.lower() == ".lnk":
            path.unlink(missing_ok=True)
    temporary = record_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, record_path)
    native.dll.RBShellChanged(str(directory))
    return directory
