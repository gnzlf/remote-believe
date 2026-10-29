#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remote Believe per-user Windows installer/uninstaller."""

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import time
import winreg
from pathlib import Path
from tkinter import messagebox
from shell_integration import register_namespace, unregister_namespace, hub_path


APP_NAME = "Remote Believe"
APP_ID = "RemoteBelieve"
APP_EXE_NAME = "RemoteBelieve.exe"
UNINSTALL_EXE_NAME = "Uninstall Remote Believe.exe"
APP_VERSION = "3.1.2-explorer"


def appdata_path(var_name, fallback):
    value = os.environ.get(var_name)
    return Path(value) if value else Path.home() / fallback


LOCAL_APPDATA = appdata_path("LOCALAPPDATA", "AppData/Local")
ROAMING_APPDATA = appdata_path("APPDATA", "AppData/Roaming")
INSTALL_DIR = LOCAL_APPDATA / "Programs" / "Remote Believe"
START_MENU_DIR = ROAMING_APPDATA / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Remote Believe"


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def bundle_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def payload_app_exe():
    candidates = [
        bundle_root() / "payload" / APP_EXE_NAME,
        Path(__file__).resolve().parents[1] / "dist" / "app" / APP_EXE_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No se encontro {} dentro del instalador.".format(APP_EXE_NAME))


def quote_cmd(path, args=""):
    command = '"{}"'.format(Path(path))
    if args:
        command += " " + args
    return command


def delete_registry_tree(root, subkey):
    try:
        winreg.DeleteKey(root, subkey)
        return
    except FileNotFoundError:
        return
    except OSError:
        pass

    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                delete_registry_tree(root, subkey + "\\" + child)
        winreg.DeleteKey(root, subkey)
    except FileNotFoundError:
        pass


def set_registry_string(root, subkey, name, value):
    with winreg.CreateKey(root, subkey) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))


def delete_registry_value(root, subkey, name):
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def create_shortcut(link_path, target, args="", workdir=None, icon=None):
    link_path = Path(link_path)
    link_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = str(workdir or Path(target).parent)
    icon = str(icon or target)

    def ps_quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    script = "\n".join([
        "$w = New-Object -ComObject WScript.Shell",
        "$s = $w.CreateShortcut({})".format(ps_quote(link_path)),
        "$s.TargetPath = {}".format(ps_quote(target)),
        "$s.Arguments = {}".format(ps_quote(args)),
        "$s.WorkingDirectory = {}".format(ps_quote(workdir)),
        "$s.IconLocation = {}".format(ps_quote(icon)),
        "$s.Save()",
    ])
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )


def install_explorer_menu(app_exe):
    verbs = [
        (
            r"Software\Classes\*\shell\RemoteBelieveDownload",
            "Remote Believe: descargar ahora",
            quote_cmd(app_exe, '--explorer-download "%1"'),
        ),
        (
            r"Software\Classes\Directory\shell\RemoteBelieveDownloadFolder",
            "Remote Believe: descargar carpeta ahora",
            quote_cmd(app_exe, '--explorer-download "%1"'),
        ),
        (
            r"Software\Classes\*\shell\RemoteBelieveUpload",
            "Remote Believe: subir mis cambios ahora",
            quote_cmd(app_exe, '--explorer-upload "%1"'),
        ),
        (
            r"Software\Classes\Directory\shell\RemoteBelieveUnpin",
            "Remote Believe: detener descargas de esta carpeta",
            quote_cmd(app_exe, '--explorer-unpin "%1"'),
        ),
        (
            r"Software\Classes\SystemFileAssociations\.ma\shell\RemoteBelievePrepareShot",
            "Remote Believe: preparar shot completo",
            quote_cmd(app_exe, '--explorer-prepare "%1"'),
        ),
    ]

    for key_path, label, command in verbs:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, label)
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, label)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(app_exe))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)


def uninstall_explorer_menu():
    keys = [
        r"Software\Classes\*\shell\RemoteBelieveDownload",
        r"Software\Classes\Directory\shell\RemoteBelieveDownloadFolder",
        r"Software\Classes\SystemFileAssociations\.ma\shell\RemoteBelievePrepareShot",
        r"Software\Classes\*\shell\RemoteBelieveUpload",
        r"Software\Classes\Directory\shell\RemoteBelieveUnpin",
    ]
    for key_path in keys:
        delete_registry_tree(winreg.HKEY_CURRENT_USER, key_path)


def register_startup(app_exe):
    set_registry_string(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        APP_NAME,
        quote_cmd(app_exe, "--start-minimized"),
    )


def unregister_startup():
    delete_registry_value(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        APP_NAME,
    )


def register_uninstall(uninstaller_exe, app_exe):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{}".format(APP_ID)
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": APP_VERSION,
        "Publisher": "Believe",
        "InstallLocation": str(INSTALL_DIR),
        "DisplayIcon": str(app_exe),
        "UninstallString": quote_cmd(uninstaller_exe, "--uninstall"),
        "QuietUninstallString": quote_cmd(uninstaller_exe, "--uninstall --quiet"),
    }
    for name, value in values.items():
        set_registry_string(winreg.HKEY_CURRENT_USER, key_path, name, value)


def unregister_uninstall():
    delete_registry_tree(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{}".format(APP_ID))
    delete_registry_tree(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\RemoteBelieveMesh")


def stop_running_app():
    # The app already owns this per-user/per-session mutex while running.
    # Never terminate every RemoteBelieve.exe on the computer.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    data = ROAMING_APPDATA / "RemoteBelieveMesh"
    name = "Local\\RemoteBelieve-" + hashlib.sha256(str(data).lower().encode()).hexdigest()[:20]
    handle = kernel.OpenMutexW(0x100000, False, name)
    if handle:
        kernel.CloseHandle(handle)
        raise RuntimeError("Cierra Remote Believe desde el icono junto al reloj > Salir y vuelve a intentar. No se forzara el cierre ni se borraran tus pendientes.")
    if ctypes.get_last_error() not in (0, 2):
        raise ctypes.WinError(ctypes.get_last_error())


def wait_for_parent(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, int(pid))
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if kernel.WaitForSingleObject(handle, 45000) != 0:
            raise RuntimeError("Believe aun se esta cerrando. Guarda tu trabajo, sal desde la bandeja y vuelve a abrir el instalador.")
    finally:
        kernel.CloseHandle(handle)


def schedule_delete_on_reboot(path):
    try:
        ctypes.windll.kernel32.MoveFileExW(str(path), None, 0x4)
    except Exception:
        pass


def remove_install_files():
    current_exe = Path(sys.executable).resolve() if is_frozen() else Path(__file__).resolve()
    if not INSTALL_DIR.exists():
        return

    for child in list(INSTALL_DIR.iterdir()):
        try:
            if child.resolve() == current_exe:
                schedule_delete_on_reboot(child)
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
        except Exception:
            schedule_delete_on_reboot(child)

    try:
        INSTALL_DIR.rmdir()
    except Exception:
        pass


def install(update=False):
    payload = payload_app_exe()
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    START_MENU_DIR.mkdir(parents=True, exist_ok=True)

    app_exe = INSTALL_DIR / APP_EXE_NAME
    uninstaller_exe = INSTALL_DIR / UNINSTALL_EXE_NAME

    stop_running_app()
    candidate = INSTALL_DIR / ".RemoteBelieve.installing.exe"
    previous = INSTALL_DIR / "RemoteBelieve.previous.exe"
    if app_exe.exists():
        shutil.copy2(app_exe, previous)
    shutil.copy2(payload, candidate)
    try:
        for attempt in range(40):
            try:
                os.replace(candidate, app_exe)
                break
            except PermissionError:
                if attempt == 39:
                    raise
                time.sleep(0.25)
    finally:
        candidate.unlink(missing_ok=True)

    if is_frozen():
        shutil.copy2(Path(sys.executable), uninstaller_exe)
    else:
        shutil.copy2(Path(__file__), INSTALL_DIR / "remote_believe_installer.py")

    register_startup(app_exe)
    install_explorer_menu(app_exe)
    register_namespace(app_exe)

    create_shortcut(
        START_MENU_DIR / "Remote Believe.lnk",
        app_exe,
        "",
        INSTALL_DIR,
        app_exe,
    )
    create_shortcut(START_MENU_DIR / "Mis carpetas Believe.lnk", hub_path(), workdir=hub_path(), icon=app_exe)
    create_shortcut(
        START_MENU_DIR / "Desinstalar Remote Believe.lnk",
        uninstaller_exe if is_frozen() else INSTALL_DIR / "remote_believe_installer.py",
        "--uninstall",
        INSTALL_DIR,
        app_exe,
    )
    register_uninstall(uninstaller_exe if is_frozen() else INSTALL_DIR / "remote_believe_installer.py", app_exe)

    try:
        subprocess.Popen([str(app_exe)], cwd=str(INSTALL_DIR))
    except Exception:
        pass

    if update:
        return
    messagebox.showinfo(
        "Remote Believe",
        "Remote Believe quedo instalado.\n\n"
        "Inicia sesion para ver tus carpetas en Remote Believe, dentro del Explorador de Windows.\n\n"
        "El programa se abrira con Windows y seguira funcionando en el area de notificaciones.",
    )


def uninstall(quiet=False):
    if not quiet:
        ok = messagebox.askyesno(
            "Remote Believe",
            "Esto desinstalara Remote Believe de Windows.\n\n"
            "No borra tu cache ni configuracion en %APPDATA%.\n\n"
            "Continuar?",
        )
        if not ok:
            return

    stop_running_app()
    unregister_startup()
    uninstall_explorer_menu()
    unregister_namespace()
    # Credentials are removed; local work and its paths are preserved.
    from windows_session import SavedSession
    SavedSession(ROAMING_APPDATA / "RemoteBelieveMesh").clear()
    unregister_uninstall()

    try:
        if START_MENU_DIR.exists():
            shutil.rmtree(START_MENU_DIR)
    except Exception:
        pass

    remove_install_files()

    if not quiet:
        messagebox.showinfo(
            "Remote Believe",
            "Remote Believe fue desinstalado.\n\n"
            "Si Windows no pudo borrar el desinstalador mientras estaba abierto, lo eliminara despues de reiniciar.",
        )


def main():
    args = {arg.lower() for arg in sys.argv[1:]}
    try:
        if "--uninstall" in args:
            uninstall(quiet="--quiet" in args)
        else:
            if "--wait-pid" in args:
                index = sys.argv.index("--wait-pid")
                wait_for_parent(int(sys.argv[index + 1]))
            install(update="--update" in args)
    except Exception as exc:
        messagebox.showerror("Remote Believe", str(exc))
        raise


if __name__ == "__main__":
    main()
