"""Use real Cloud Files placeholders through the same path while switching accounts."""
import contextlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import mesh_client as mesh
from cloud_files import CloudFilesEngine
from test_cloud_files import FakeOffice


@unittest.skipUnless(sys.platform == "win32", "Windows Cloud Files")
class AccountCloudFilesTests(unittest.TestCase):
    def test_same_explorer_path_reads_the_correct_accounts_content(self):
        runtime = Path(__file__).parent / "runtime"
        runtime.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="accounts-", dir=runtime) as temp, contextlib.ExitStack() as stack:
            base = Path(temp).resolve()
            data, cache, drive = base / "data", base / "cache", base / "existing-X"
            drive.mkdir()
            (base / "unused-R").mkdir()
            mesh.save_json(data / "settings.json", {"mode": "home", "username": "alice", "cache_owner": "alice",
                                                    "cache_dir": str(cache), "auto_upload": False})
            for name, value in (("DATA_DIR", data), ("SETTINGS_PATH", data / "settings.json"),
                                ("MANIFEST_PATH", data / "manifest.json"), ("OUTBOX_PATH", data / "outbox.json")):
                stack.enter_context(patch.object(mesh, name, value))
            store = mesh.LocalStore()
            stack.enter_context(patch.object(store, "drive_root", side_effect=lambda root: drive if root["driveLetter"] == "X" else base / "unused-R"))
            stack.enter_context(patch.object(mesh, "existing_subst_target", return_value=""))
            stack.enter_context(patch.object(mesh, "ensure_subst_drive", return_value=(False, "existing local disk")))
            root = {"id": "test", "driveLetter": "X", "access": ["Project"], "projectScoped": True}
            addresses = []
            for username, payload in (("alice", b"A" * 4096), ("bob", b"B" * 4096), ("alice", b"A" * 4096)):
                store.switch_account(username)
                store.mount_roots([root])
                office = FakeOffice()
                office.payload = payload
                def downloaded(root, rp, item, path, digest):
                    store.mark_downloaded(root["id"], rp, item, path)
                    store.manifest[store.key(root["id"], rp)]["sha256"] = digest
                    store.save_manifest()
                engine = CloudFilesEngine(store, office, lambda root_id, rp: True,
                                          mesh.parse_remote_modified_at, downloaded)
                store.cloud_engine = engine
                try:
                    engine.start([root], background=False)
                    engine.populate("test", "Project")
                    path = drive / "Project" / "scene.ma"
                    addresses.append(str(path))
                    result = subprocess.run([sys.executable, "-c", "import pathlib,sys; sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())", str(path)],
                                            capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, payload)
                finally:
                    engine.close(unregister=True)
                    store.cloud_engine = None
            self.assertEqual(len(set(addresses)), 1)
