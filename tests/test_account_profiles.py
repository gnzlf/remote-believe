import contextlib
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import mesh_client as mesh

ROOT_X = {"id": "projects", "driveLetter": "X", "access": ["Project"], "projectScoped": True}
ROOT_R = {"id": "rigs", "driveLetter": "R", "access": ["Characters"], "projectScoped": True}


class AccountProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.data = self.base / "data"
        self.cache = self.base / "legacy-cache"
        self.scene = self.cache / "X" / "Project" / "scene.ma"
        self.scene.parent.mkdir(parents=True)
        self.scene.write_bytes(b"Alice's pending animation")
        self.settings = {"mode": "home", "username": "bob", "cache_owner": "alice", "cache_dir": str(self.cache),
                         "auto_upload": False, "offline_folders": [{"rootId": "projects", "remotePath": "Project"}],
                         "cloud_populated_folders": [{"root": "projects", "path": "Project"}]}
        self.manifest = {"projects::Project/scene.ma": {"localPath": str(self.scene), "localSize": self.scene.stat().st_size}}
        self.outbox = {"version": 1, "items": {"projects::Project/scene.ma": {
            "rootId": "projects", "remotePath": "Project/scene.ma", "localPath": str(self.scene)}}}
        mesh.save_json(self.data / "settings.json", self.settings)
        mesh.save_json(self.data / "manifest.json", self.manifest)
        mesh.save_json(self.data / "outbox.json", self.outbox)
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("DATA_DIR", self.data), ("SETTINGS_PATH", self.data / "settings.json"),
                            ("MANIFEST_PATH", self.data / "manifest.json"), ("OUTBOX_PATH", self.data / "outbox.json"),
                            ("DEFAULT_CACHE", self.cache)):
            stack.enter_context(patch.object(mesh, name, value))
        self.store = mesh.LocalStore()

    def test_new_user_is_not_blocked_and_old_pending_work_is_restored(self):
        self.store.switch_account("bob")
        bob_cache = self.store.cache_dir
        self.assertNotEqual(bob_cache, self.cache)
        self.assertEqual(self.store.manifest, {})
        self.assertEqual(self.store.pending_uploads(), [])
        self.assertEqual(self.store.settings["offline_folders"], [])
        self.assertEqual(self.store.settings["cloud_populated_folders"], [])
        self.assertTrue(self.store.settings["auto_upload"])
        self.assertEqual(list(self.store.iter_local_files(ROOT_X)), [])
        work = self.store.local_path(ROOT_X, "Project/bob.ma")
        work.parent.mkdir(parents=True)
        work.write_bytes(b"Bob's new scene")
        self.store.enqueue_upload(ROOT_X, "Project/bob.ma", work)
        self.store.settings["offline_folders"] = [{"rootId": "projects", "remotePath": "Project/Bob"}]
        self.store.save_settings()
        self.store.switch_account("alice")
        self.assertEqual(self.store.cache_dir, self.cache)
        self.assertEqual(self.store.manifest, self.manifest)
        self.assertEqual(self.store.outbox, self.outbox)
        self.assertFalse(self.store.settings["auto_upload"])
        self.assertEqual(self.store.settings["offline_folders"], self.settings["offline_folders"])
        self.assertEqual(self.scene.read_bytes(), b"Alice's pending animation")
        self.store.switch_account("bob")
        restarted = mesh.LocalStore()
        self.assertEqual(restarted.cache_dir, bob_cache)
        self.assertEqual(restarted.pending_uploads()[0]["localPath"], str(work))
        self.assertEqual(restarted.settings["offline_folders"][0]["remotePath"], "Project/Bob")
        self.assertEqual(work.read_bytes(), b"Bob's new scene")
        self.assertEqual(mesh.load_json(self.data / "outbox.json", {}), self.outbox)

    def test_accounts_that_differ_only_in_case_still_have_separate_work(self):
        self.store.switch_account("Bob")
        first = self.store.cache_dir
        self.store.switch_account("bob")
        self.assertNotEqual(self.store.cache_dir, first)

    def test_local_deletions_persist_and_stay_with_their_own_account(self):
        self.store.switch_account("alice")
        self.scene.unlink()
        self.store.reconcile_local_deletions([ROOT_X])
        self.assertTrue(self.store.is_local_deleted("projects", "Project/scene.ma"))
        self.store.switch_account("bob")
        self.assertFalse(self.store.is_local_deleted("projects", "Project/scene.ma"))
        self.store.switch_account("alice")
        restarted = mesh.LocalStore()
        self.assertTrue(restarted.is_local_deleted("projects", "Project/scene.ma"))
        self.assertEqual(restarted.settings["offline_folders"], [])
        self.assertEqual(restarted.pending_uploads(), [])

    def test_unknown_legacy_files_are_preserved_without_becoming_new_users_work(self):
        for name in ("cache_owner", "username"):
            self.store.settings.pop(name, None)
        self.store.manifest, self.store.outbox = {}, {"items": {}}
        self.store.switch_account("bob")
        self.assertNotEqual(self.store.cache_dir, self.cache)
        self.assertEqual(list(self.store.iter_local_files(ROOT_X)), [])
        self.assertEqual(self.scene.read_bytes(), b"Alice's pending animation")

    def test_own_subst_drives_switch_accounts_and_foreign_drive_is_never_removed(self):
        self.store.switch_account("alice")
        devices = {letter: str(self.cache / letter) for letter in ("X", "R")}
        self.store.switch_account("bob")
        def mount(letter, target):
            if letter in devices and Path(devices[letter]) != Path(target):
                return False, "occupied"
            devices[letter] = str(target)
            return True, "mounted"
        def run(args, **kwargs):
            self.assertEqual(args[0], "subst")
            self.assertEqual(args[2], "/d")
            devices.pop(args[1].rstrip(":"), None)
            return SimpleNamespace(returncode=0)
        with patch.object(mesh, "existing_subst_target", side_effect=lambda letter: devices.get(letter, "")), \
             patch.object(mesh, "ensure_subst_drive", side_effect=mount), \
             patch.object(mesh.subprocess, "run", side_effect=run) as process:
            self.store.mount_roots([ROOT_X, ROOT_R])
            self.assertEqual(devices, {letter: str(self.store.cache_dir / letter) for letter in ("X", "R")})
            self.assertEqual(process.call_count, 2)
            devices["X"] = str(self.base / "another-job")
            process.reset_mock()
            self.store._ensure_account_drive("X", self.store.cache_dir / "X")
            process.assert_not_called()
            self.assertEqual(devices["X"], str(self.base / "another-job"))

    def test_account_without_r_does_not_keep_previous_accounts_r_subst(self):
        self.store.switch_account("alice")
        devices = {"R": str(self.cache / "R")}
        self.store.switch_account("bob")
        with patch.object(mesh, "existing_subst_target", side_effect=lambda letter: devices.get(letter, "")), \
             patch.object(mesh, "ensure_subst_drive", return_value=(True, "mounted")), \
             patch.object(mesh.subprocess, "run", side_effect=lambda args, **kw: (devices.pop(args[1][0]), SimpleNamespace(returncode=0))[1]):
            self.store.mount_roots([ROOT_X])
        self.assertNotIn("R", devices)

    @unittest.skipUnless(sys.platform == "win32", "NTFS junctions")
    def test_existing_local_drive_uses_current_account_and_preserves_foreign_folders(self):
        self.store.switch_account("alice")
        drive_x, drive_r = self.base / "existing-X", self.base / "existing-R"
        drive_x.mkdir()
        drive_r.mkdir()
        foreign = drive_x / "OtherJob"
        foreign.mkdir()
        (foreign / "scene.ma").write_bytes(b"foreign work")
        import _winapi
        foreign_target = self.base / "foreign-cache"
        foreign_target.mkdir()
        _winapi.CreateJunction(str(foreign_target), str(drive_x / "ForeignLink"))
        rig = self.cache / "R" / "Characters" / "rig.ma"
        rig.parent.mkdir(parents=True)
        rig.write_bytes(b"Alice rig")
        with patch.object(self.store, "drive_root", side_effect=lambda root: drive_x if root["driveLetter"] == "X" else drive_r), \
             patch.object(mesh, "existing_subst_target", return_value=""), \
             patch.object(mesh, "ensure_subst_drive", return_value=(False, "existing local disk")):
            self.store.mount_roots([ROOT_X, ROOT_R])
            self.store.ensure_destination(ROOT_X, "Project", directory=True)
            self.store.ensure_destination(ROOT_R, "Characters", directory=True)
            self.assertEqual((drive_x / "Project/scene.ma").read_bytes(), self.scene.read_bytes())
            self.store.switch_account("bob")
            self.store.mount_roots([ROOT_X])
            self.store.ensure_destination(ROOT_X, "Project", directory=True)
            self.assertEqual((drive_x / "Project").resolve(), self.store.cache_dir / "X/Project")
            self.assertFalse((drive_r / "Characters").exists())
            self.assertEqual(rig.read_bytes(), b"Alice rig")
            (drive_x / "Project/bob.ma").write_bytes(b"Bob work")
            self.store.switch_account("alice")
            self.store.mount_roots([ROOT_X, ROOT_R])
            self.store.ensure_destination(ROOT_X, "Project", directory=True)
            self.assertEqual((drive_x / "Project/scene.ma").read_bytes(), b"Alice's pending animation")
            self.assertFalse((drive_x / "Project/bob.ma").exists())
            self.assertEqual((foreign / "scene.ma").read_bytes(), b"foreign work")
            self.assertEqual((drive_x / "ForeignLink").resolve(), foreign_target)

    def test_failed_login_does_not_change_active_local_account(self):
        app = SimpleNamespace(screen="login", busy=False, store=self.store, file_api=None,
                              username_var=SimpleNamespace(get=lambda: "invalid-user"),
                              pw_var=SimpleNamespace(get=lambda: "invalid-password"),
                              mode_var=SimpleNamespace(get=lambda: "home"),
                              office_ip_var=SimpleNamespace(get=lambda: "100.64.0.1"),
                              manual_ip_var=SimpleNamespace(get=lambda: True),
                              post=lambda fn, *args: fn(*args), set_status=Mock(),
                              _worker=lambda work: work(), _prepare_session=Mock())
        with patch.object(mesh, "tailscale_peer_name_for_ip", return_value="Office"), \
             patch.object(mesh, "OfficeFileApi") as api:
            api.return_value.login.side_effect = mesh.RBError("Credenciales invalidas")
            with self.assertRaises(mesh.RBError):
                mesh.MeshClientApp._login(app)
        app._prepare_session.assert_not_called()
        self.assertEqual(self.store.settings["cache_owner"], "alice")
        self.assertEqual(self.store.settings["username"], "bob")

    def test_valid_login_can_change_to_another_account_and_back(self):
        app = SimpleNamespace(screen="login", busy=False, store=self.store, file_api=None,
                              username_var=SimpleNamespace(get=lambda: "bob"),
                              pw_var=SimpleNamespace(get=lambda: "valid-test-password"),
                              mode_var=SimpleNamespace(get=lambda: "home"),
                              office_ip_var=SimpleNamespace(get=lambda: "100.64.0.1"),
                              manual_ip_var=SimpleNamespace(get=lambda: True),
                              post=lambda fn, *args: fn(*args), set_status=Mock(),
                              _worker=lambda work: work(), _login_complete=Mock())
        app._prepare_session = lambda name, mode: mesh.MeshClientApp._prepare_session(app, name, mode)
        with patch.object(mesh, "tailscale_peer_name_for_ip", return_value="Office"), \
             patch.object(mesh, "OfficeFileApi") as api:
            api.return_value.login.return_value = {"access": {"roots": [ROOT_X]}}
            mesh.MeshClientApp._login(app)
            self.assertEqual(self.store.settings["cache_owner"], "bob")
            self.assertEqual(self.store.pending_uploads(), [])
            self.assertIs(app.file_api, api.return_value)
            app.username_var.get = lambda: "alice"
            mesh.MeshClientApp._login(app)
            self.assertEqual(self.store.cache_dir, self.cache)
            self.assertEqual(self.store.outbox, self.outbox)


if __name__ == "__main__":
    unittest.main()
