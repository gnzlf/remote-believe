"""Drive collisions and address navigation, isolated from real user state/drives."""
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "client" / "mesh_client.py"
spec = importlib.util.spec_from_file_location("mesh_client", SOURCE)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)

ROOT_X = {"id": "proyectos", "name": "X: Proyectos", "driveLetter": "X", "projectScoped": True, "access": ["Proyecto A"]}
ROOT_R = {"id": "rig", "name": "R:", "driveLetter": "R", "projectScoped": True, "access": "*"}
ROOTS = [ROOT_X, ROOT_R]


class DrivePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = client.LocalStore.__new__(client.LocalStore)
        self.store.settings = {"cache_dir": self.temp.name, "drive_letters": {}}
        self.store.local_drives = {}
        self.store.shared_drives = {}
        self.store.mount_errors = {}
        self.store.mount_lock = threading.RLock()
        self.store.save_settings = Mock()

    def simulate_mounts(self, occupied):
        devices = dict(occupied)
        def mount(letter, target):
            target = str(Path(target).resolve())
            if letter in devices and devices[letter] != target:
                return False, "ocupado"
            devices[letter] = target
            return True, "ok"
        return devices, patch.object(client, "ensure_subst_drive", side_effect=mount)

    def test_foreign_subst_is_never_removed(self):
        with patch.object(client, "existing_subst_target", return_value=str(Path(self.temp.name) / "otro-trabajo")), \
             patch.object(client.subprocess, "run") as run:
            ok, _ = client.ensure_subst_drive("X", self.temp.name)
        self.assertFalse(ok)
        run.assert_not_called()

    def test_disconnected_drive_is_not_replaced(self):
        with patch.object(client, "existing_subst_target", return_value=""), \
             patch.object(client, "windows_drive_in_use", return_value=True), \
             patch.object(client.subprocess, "run") as run:
            ok, _ = client.ensure_subst_drive("X", self.temp.name)
        self.assertFalse(ok)
        run.assert_not_called()

    def test_own_subst_is_reused(self):
        with patch.object(client, "existing_subst_target", return_value=self.temp.name), \
             patch.object(client.subprocess, "run") as run:
            ok, _ = client.ensure_subst_drive("X", self.temp.name)
        self.assertTrue(ok)
        run.assert_not_called()

    def test_occupied_local_drives_keep_letters_and_cache(self):
        devices, mounts = self.simulate_mounts({"X": "foreign-x", "R": "foreign-r"})
        with mounts as mount_mock, patch.object(client, "supports_local_junctions", return_value=True):
            self.store.mount_roots(ROOTS)
        self.assertEqual(self.store.local_drives, {})
        self.assertEqual(self.store.shared_drives, {"proyectos": "X", "rig": "R"})
        self.assertEqual([call.args[0] for call in mount_mock.call_args_list], ["X", "R"])
        self.assertEqual(devices["X"], "foreign-x")
        self.assertEqual(devices["R"], "foreign-r")
        self.assertEqual(self.store.root_dir(ROOT_X), Path(self.temp.name) / "X")
        self.assertEqual(client.root_drive_letter(ROOT_X), "X")
        self.assertTrue(client.upload_policy(ROOT_X, "Proyecto A/shots/s010/animation/scene.ma", Path("scene.ma"))[0])

    def test_free_letters_are_mounted_using_original_letters(self):
        devices, mounts = self.simulate_mounts({})
        with mounts:
            self.store.mount_roots(ROOTS)
        self.assertEqual(self.store.local_drives, {"proyectos": "X", "rig": "R"})
        with patch.object(client, "ensure_subst_drive", side_effect=lambda letter, target: (devices.get(letter) == str(Path(target).resolve()), "")):
            self.store.mount_roots(ROOTS)
        self.assertEqual(self.store.local_drives["proyectos"], "X")

    def test_occupied_network_drive_blocks_mount_without_changing_paths(self):
        with patch.object(client, "ensure_subst_drive", return_value=(False, "ocupado")), \
             patch.object(client, "supports_local_junctions", return_value=False):
            messages = self.store.mount_roots(ROOTS)
        self.assertEqual(self.store.local_drives, {})
        self.assertEqual(self.store.shared_drives, {})
        self.assertEqual(str(self.store.explorer_path(ROOT_X, "Proyecto A")), "X:\\Proyecto A")
        with self.assertRaises(client.RBError):
            self.store.ensure_destination(ROOT_X, "Proyecto A/scene.ma")
        self.assertIn("no admite enlaces", messages[0])

    def test_address_accepts_original_unc_and_cache_paths(self):
        for address in [r'"X:\Proyecto A\shots"', r"x:\Proyecto A\shots", "X:/Proyecto A/shots/",
                        r"\\BLVSERVER\Proyectos_3D\Proyecto A\shots", str(self.store.root_dir(ROOT_X) / "Proyecto A" / "shots")]:
            with self.subTest(address=address):
                root, rp = client.resolve_browser_address(self.store, ROOTS, address, ROOT_R)
                self.assertEqual((root["id"], rp), ("proyectos", "Proyecto A/shots"))
        root, rp = client.resolve_browser_address(self.store, ROOTS, r"R:\personajes", ROOT_X)
        self.assertEqual((root["id"], rp), ("rig", "personajes"))

    def test_relative_parent_and_invalid_paths(self):
        root, rp = client.resolve_browser_address(self.store, ROOTS, "../animation", ROOT_X, "Proyecto A/shots")
        self.assertEqual(rp, "Proyecto A/animation")
        for value in [r"X:\..\otro", r"X:\.. \otro", r"X:\NUL.txt", r"X:\Proyecto A\file:stream", r"W:\unrelated", r"\\otro\servidor\file", ""]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.resolve_browser_address(self.store, ROOTS, value, ROOT_X)
        root, rp = client.resolve_browser_address(self.store, ROOTS, r"X:\Proyecto ajeno", ROOT_X)
        self.assertFalse(client.can_access_remote_path({"roots": ROOTS}, root["id"], rp))

    def test_explorer_rejects_foreign_x_but_accepts_own_mount_and_cache(self):
        with patch.object(client, "existing_subst_target", return_value=""), \
             patch.object(client.Path, "resolve", lambda path: path.absolute()):
            self.assertIsNone(client.target_to_root_path(self.store, ROOTS, r"X:\Proyecto A\scene.ma")[0])
        self.store.local_drives = {"proyectos": "X"}
        with patch.object(client, "existing_subst_target", return_value=str(self.store.root_dir(ROOT_X))), \
             patch.object(client.Path, "resolve", lambda path: path.absolute()):
            root, rp, local = client.target_to_root_path(self.store, ROOTS, r"X:\Proyecto A\scene.ma")
            self.assertEqual((root["id"], rp), ("proyectos", "Proyecto A/scene.ma"))
            root, rp, _ = client.target_to_root_path(self.store, ROOTS, str(local))
            self.assertEqual((root["id"], rp), ("proyectos", "Proyecto A/scene.ma"))
        with patch.object(client, "existing_subst_target", return_value=str(Path(self.temp.name) / "foreign")):
            with patch.object(client.Path, "resolve", lambda path: path.absolute()):
                self.assertIsNone(client.target_to_root_path(self.store, ROOTS, r"X:\Proyecto A\scene.ma")[0])

    def test_real_junction_preserves_path_without_copying_foreign_files(self):
        drive = Path(self.temp.name) / "existing-drive"
        drive.mkdir()
        foreign = drive / "Otro trabajo"
        foreign.mkdir()
        (foreign / "private.ma").write_text("untouched", encoding="utf-8")
        self.store.settings["cache_dir"] = str(Path(self.temp.name) / "cache")
        self.store.shared_drives = {"proyectos": "X"}
        with patch.object(self.store, "drive_root", return_value=drive):
            local = self.store.ensure_destination(ROOT_X, "Proyecto A/shots/scene.ma")
            local.parent.mkdir(parents=True)
            local.write_text("downloaded", encoding="utf-8")
            original_path = self.store.explorer_path(ROOT_X, "Proyecto A/shots/scene.ma")
            self.assertEqual(original_path, drive / "Proyecto A" / "shots" / "scene.ma")
            self.assertEqual(original_path.read_text(encoding="utf-8"), "downloaded")
            original_path.write_text("saved by animator", encoding="utf-8")
            self.assertEqual(local.read_text(encoding="utf-8"), "saved by animator")
            self.assertEqual(client.target_to_root_path(self.store, ROOTS, str(original_path))[1], "Proyecto A/shots/scene.ma")
            self.store.ensure_destination(ROOT_X, "Proyecto A/shots/scene.ma")
            self.assertIsNone(client.target_to_root_path(self.store, ROOTS, str(foreign / "private.ma"))[0])
        self.assertFalse((self.store.root_dir(ROOT_X) / "Otro trabajo").exists())
        self.assertEqual((foreign / "private.ma").read_text(encoding="utf-8"), "untouched")

    def test_existing_folder_or_foreign_junction_is_never_adopted(self):
        target = Path(self.temp.name) / "cache" / "Project"
        occupied = Path(self.temp.name) / "Project"
        occupied.mkdir()
        (occupied / "original.ma").write_text("foreign", encoding="utf-8")
        with self.assertRaisesRegex(client.RBError, "Conflicto"):
            client.ensure_folder_junction(occupied, target)
        self.assertEqual((occupied / "original.ma").read_text(encoding="utf-8"), "foreign")
        import _winapi
        link = Path(self.temp.name) / "foreign-link"
        _winapi.CreateJunction(str(occupied), str(link))
        with self.assertRaisesRegex(client.RBError, "Conflicto"):
            client.ensure_folder_junction(link, target)
        self.assertEqual(link.resolve(), occupied.resolve())

    def test_root_file_in_occupied_drive_is_blocked(self):
        self.store.shared_drives = {"rig": "R"}
        with self.assertRaisesRegex(client.RBError, "archivo en la raiz"):
            self.store.ensure_destination(ROOT_R, "syncremote.json")

    def test_only_local_edits_are_detected_after_download_via_junction(self):
        drive = Path(self.temp.name) / "existing-drive"
        drive.mkdir()
        self.store.settings["cache_dir"] = str(Path(self.temp.name) / "cache")
        self.store.shared_drives = {"proyectos": "X"}
        self.store.manifest = {}
        self.store.outbox = {"items": {}}
        self.store.save_manifest = Mock()
        self.store.save_outbox = Mock()
        rp = "Proyecto A/shots/shot010/animation/scene.ma"
        with patch.object(self.store, "drive_root", return_value=drive):
            local = self.store.ensure_destination(ROOT_X, rp)
            local.parent.mkdir(parents=True)
            local.write_text("downloaded scene", encoding="utf-8")
            self.store.mark_downloaded("proyectos", rp, {"size": local.stat().st_size}, local)
            queue = Mock()
            app = SimpleNamespace(roots=[ROOT_X], store=self.store, download_paths=set(), file_snapshots={},
                                  _queue_pending_upload=queue, post=Mock(), set_status=Mock(), _set_transfer_state=Mock())
            self.assertEqual(client.MeshClientApp._scan_for_offline_changes(app), 0)
            queue.assert_not_called()
            self.store.explorer_path(ROOT_X, rp).write_text("animator saved this", encoding="utf-8")
            self.assertEqual(client.MeshClientApp._scan_for_offline_changes(app), 1)
            self.assertEqual(queue.call_args.args[1], rp)
            self.assertEqual(queue.call_args.args[2], local)


class AddressBarUITests(unittest.TestCase):
    def test_edit_copy_paste_navigation_and_history(self):
        with tempfile.TemporaryDirectory() as temp:
            store = client.LocalStore.__new__(client.LocalStore)
            store.settings = {"cache_dir": temp, "mode": "home", "username": "test", "auto_upload": False,
                              "office_ip": "100.64.0.1", "office_name": "Test Office"}
            store.local_drives, store.shared_drives, store.mount_errors = {}, {}, {}
            store.mount_roots = Mock(return_value=[])
            store.manifest, store.outbox = {}, {"items": {}}
            disabled = ["_setup_tray", "_discard_saved_login", "_start_command_server", "_start_heartbeat", "_start_watcher",
                        "_clean_placeholder_tree", "_start_shared_folder_mounts", "_start_offline_folder_sync",
                        "_prune_unsafe_pending_uploads", "_restore_pending_upload_states", "_refresh"]
            from contextlib import ExitStack
            with ExitStack() as stack:
                stack.enter_context(patch.object(client, "LocalStore", return_value=store))
                stack.enter_context(patch.object(client, "get_or_create_device_id", return_value="test-only"))
                for method in disabled:
                    stack.enter_context(patch.object(client.MeshClientApp, method))
                app = client.MeshClientApp()
                app.withdraw()
                try:
                    app.post = lambda cb, *args: cb(*args)
                    errors = []
                    def run_work(callback):
                        try:
                            callback()
                        except Exception as exc:
                            errors.append(str(exc))
                    app._worker = run_work
                    folders = {"": [{"name": "Proyecto A", "type": "directory"}],
                               "Proyecto A": [{"name": "shots", "type": "directory"}],
                               "Proyecto A/shots": []}
                    app.file_api = SimpleNamespace(office_url="http://100.64.0.1:8090",
                                                   list_folder=lambda root_id, rp: {"items": folders.get(rp, [])})
                    app._build_legacy_browser(ROOTS, {"roots": ROOTS, "username": "test", "role": "user"})
                    app.update_idletasks()
                    self.assertEqual(app.path_entry.cget("state"), "normal")
                    self.assertEqual(app.path_var.get(), "X:\\")
                    app.path_entry.delete(0, client.END)
                    app.path_entry.insert(0, "X:/Proyecto A/shots")
                    app._submit_address()
                    self.assertEqual(app.current_path, "Proyecto A/shots")
                    self.assertEqual(app.path_var.get(), "X:\\Proyecto A\\shots")
                    app._go_back()
                    self.assertEqual(app.current_path, "")
                    app._go_forward()
                    self.assertEqual(app.current_path, "Proyecto A/shots")
                    app.path_var.set("X:/Proyecto A/no-existe")
                    app._submit_address()
                    self.assertIn("no existe", errors[-1])
                    self.assertEqual(app.current_path, "Proyecto A/shots")
                    app.path_var.set("X:/Proyecto ajeno")
                    app._submit_address()
                    self.assertIn("Sin permiso", app.status_var.get())
                    app._reset_address()
                    app._select_address()
                    self.assertTrue(app.path_entry.selection_present())
                    with patch.object(app, "clipboard_clear"), patch.object(app, "clipboard_append") as copied:
                        app._copy_address()
                        copied.assert_called_once_with("X:\\Proyecto A\\shots")
                    self.assertTrue(app.path_entry.bind("<Return>"))
                    self.assertTrue(app.bind("<Control-l>"))
                    self.assertTrue(app.bind("<Alt-d>"))
                finally:
                    app.destroy()

    def test_server_returns_404_for_missing_folder_and_file(self):
        handler = client.OfficeServerHandler.__new__(client.OfficeServerHandler)
        handler._json = Mock()
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            file_path = Path(temp) / "file.ma"
            file_path.write_text("scene", encoding="utf-8")
            for path in [missing, file_path]:
                with self.subTest(path=path), patch.object(handler, "_resolve_local_path", return_value=path):
                    handler._handle_list("proyectos", "missing")
                    self.assertEqual(handler._json.call_args.args[0], 404)


if __name__ == "__main__":
    unittest.main()
