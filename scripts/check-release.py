"""Validate the exact installer payload before publishing a mandatory update."""
import ast
import hashlib
import json
import marshal
from pathlib import Path
import re
import sys
from PyInstaller.archive.readers import CArchiveReader

root = Path(__file__).resolve().parents[1]
tag = sys.argv[1].removeprefix("v") if sys.version_info >= (3, 9) else sys.argv[1].lstrip("v")
if not re.fullmatch(r"\d+\.\d+\.\d+", tag):
    raise SystemExit("Version invalida")
directory = root / "dist" / tag
application = directory / "app" / "RemoteBelieve.exe"
setup = directory / "RemoteBelieveSetup.exe"
archive = CArchiveReader(str(application))
code = marshal.loads(archive.extract("mesh_client"))
assert tag + "-explorer" in code.co_consts, "El ejecutable no coincide con la release"
build = json.loads(archive.extract("build-info.json").decode("utf-8-sig"))
assert build["version"] == tag
for name, expected in build["files"].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, "El codigo cambio despues de compilar: " + name
modules = archive.open_embedded_archive("PYZ.pyz").toc
for name in ("app_updates", "transfer_control", "desktop_controls", "content_hash", "cache_paths", "office_errors", "diagnostics"):
    assert name in modules, "Falta " + name
installer = CArchiveReader(str(setup))
assert installer.extract("payload\\RemoteBelieve.exe") == application.read_bytes(), "Payload incorrecto"
for file in (application, setup):
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    file.with_suffix(file.suffix + ".sha256").write_text(digest + "  " + file.name + "\n", encoding="ascii")
    print(file.name, digest)
manifest = {"tag": "v" + tag, "sha256": hashlib.sha256(setup.read_bytes()).hexdigest(), "size": setup.stat().st_size,
            "url": "https://github.com/gnzlf/remote-believe/releases/download/v" + tag + "/RemoteBelieveSetup.exe"}
(directory / "update.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print("Instalador y version verificados.")
