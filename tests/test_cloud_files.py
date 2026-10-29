"""Native integration tests: isolated NTFS roots, no real X:/R: or user settings."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

CLIENT = Path(__file__).resolve().parents[1] / "client"
sys.path.insert(0, str(CLIENT))
import mesh_client as mesh
from cloud_files import CloudFilesEngine, NativeCloud, PARTIAL


class Response(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class FakeOffice:
    def __init__(self):
        self.payload = (b"Remote Believe native hydration test\n" * 23451) + b"EOF"
        self.modified = "2026-10-16T10:00:00Z"
        self.reads = 0
        self.lists = []

    def list_folder(self, root_id, rp):
        self.lists.append(rp)
        if not rp:
            items = [{"name": "Project", "type": "directory", "modifiedAt": self.modified}]
        elif rp == "Project":
            items = [{"name": "scene.ma", "type": "file", "size": len(self.payload), "modifiedAt": self.modified}]
        else:
            items = []
        return {"items": items}

    def _req(self, method, path, params=None, **kwargs):
        from office_errors import OfficeResponseError
        if params and (str(params.get("expectedSize")) != str(len(self.payload)) or params.get("expectedModifiedAt") != self.modified):
            raise OfficeResponseError("El archivo cambio en Oficina", 409)
        self.reads += 1
        return Response(self.payload)

    def remote_item(self, root_id, rp, with_hash=True):
        return {"name": rp.rsplit("/", 1)[-1], "type": "file", "size": len(self.payload), "modifiedAt": self.modified}


@unittest.skipUnless(sys.platform == "win32", "Windows Cloud Files API")
class CloudFilesTests(unittest.TestCase):
    def setUp(self):
        runtime = Path(__file__).parent / "runtime"
        runtime.mkdir(exist_ok=True)
        # The Windows Cloud Filter does not attach sync roots under the OS Temp tree.
        self.temp = tempfile.TemporaryDirectory(prefix="provider-", dir=runtime)
        self.store = mesh.LocalStore.__new__(mesh.LocalStore)
        self.store.settings = {"cache_dir": self.temp.name}
        self.store.local_drives, self.store.shared_drives, self.store.mount_errors = {}, {}, {}
        self.store.manifest, self.store.outbox = {}, {"items": {}}
        self.store.save_manifest = Mock()
        self.store.save_settings = Mock()
        self.store.ensure_destination = lambda root, rp, directory=False: self.store.local_path(root, rp)
        self.root = {"id": "test", "name": "X:", "driveLetter": "X", "access": "*"}
        self.office = FakeOffice()
        self.downloads = []
        self.events = []
        def downloaded(root, rp, item, path, sha):
            self.store.mark_downloaded(root["id"], rp, item, path)
            self.store.manifest[self.store.key(root["id"], rp)]["sha256"] = sha
            self.downloads.append(rp)
        self.engine = CloudFilesEngine(self.store, self.office, lambda root_id, rp: True,
                                       mesh.parse_remote_modified_at, downloaded,
                                       lambda *args: self.events.append(args))
        self.engine.start([self.root], background=False)

    def tearDown(self):
        self.engine.close(unregister=True)
        self.temp.cleanup()

    def test_explorer_enumerates_and_reads_native_placeholder(self):
        root = self.store.root_dir(self.root)
        self.assertEqual(self.office.reads, 0)
        # The provider process deliberately bypasses its own directory callbacks.
        result = subprocess.run([sys.executable, "-c", "import os,sys,json; print(json.dumps(os.listdir(sys.argv[1])))", str(root / "Project")],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        self.assertEqual(json.loads(result.stdout), ["scene.ma"])
        local = root / "Project" / "scene.ma"
        self.assertEqual(local.stat().st_size, len(self.office.payload))
        self.assertTrue(self.engine.native.state(local) & PARTIAL)
        self.assertEqual(self.office.reads, 0)
        self.assertEqual(local.read_bytes(), self.office.payload)
        self.assertFalse(self.engine.native.state(local) & PARTIAL)
        self.assertEqual(self.office.reads, 1)
        self.assertEqual(local.read_bytes(), self.office.payload)
        self.assertEqual(self.office.reads, 1)

    def test_watcher_does_not_populate_unopened_directories(self):
        self.assertEqual(list(self.engine.iter_files(self.root)), [])
        self.assertNotIn("Project", self.office.lists)
        self.assertEqual(self.office.reads, 0)

    def test_failed_folder_enumeration_can_be_retried_without_reconnecting(self):
        from office_errors import OfficeNetworkError
        original = self.office.list_folder
        failed = []
        def catalog(root, rp):
            if rp == "Project" and not failed:
                failed.append(True)
                raise OfficeNetworkError("temporary Office interruption")
            return original(root, rp)
        self.office.list_folder = catalog
        folder = self.store.local_path(self.root, "Project")
        command = [sys.executable, "-c", "import os,sys; print(os.listdir(sys.argv[1]))", str(folder)]
        first = subprocess.run(command, capture_output=True, text=True, timeout=15)
        # Windows may report an error, return an empty enumeration, or retry
        # internally. The important guarantee is that the next read recovers.
        self.assertTrue(failed)
        second = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("scene.ma", second.stdout)
        self.assertEqual(self.office.reads, 0)

    def test_native_provider_progress_is_accepted_during_real_hydration(self):
        self.engine.populate("test", "Project")
        original = self.office._req
        reports = []
        def download(*args, **kwargs):
            with self.engine.request_lock:
                pending = list(self.engine.pending_requests)
            for request in pending:
                reports.append(self.engine.native.dll.RBProgress(request, 0, len(self.office.payload)))
            return original(*args, **kwargs)
        self.office._req = download
        path = self.store.local_path(self.root, "Project/scene.ma")
        self.assertEqual(path.read_bytes(), self.office.payload)
        self.assertTrue(reports)
        self.assertTrue(all(value >= 0 for value in reports))

    def test_explorer_thumbnail_request_does_not_download_content(self):
        from cloud_files import ABORTED
        with patch.object(self.engine.native.dll, "RBFinish") as finish, patch.object(self.engine.executor, "submit") as submit:
            self.engine._event(0, 42, {"root": "test", "path": "Project/scene.ma"}, 123, 0, 123, r"C:\Windows\explorer.exe")
            finish.assert_called_once_with(42, ABORTED)
            submit.assert_not_called()
        self.assertEqual(self.office.reads, 0)

    def test_explorer_explicit_hydration_is_allowed_without_enabling_thumbnails(self):
        with patch.object(self.engine.native.dll, "RBFinish") as finish, patch.object(self.engine.executor, "submit") as submit:
            self.engine._event(3, 43, {"root": "test", "path": "Project/scene.ma"}, 123, 0, 123, r"C:\Windows\explorer.exe")
            finish.assert_not_called()
            submit.assert_called_once()
            self.assertEqual(submit.call_args.args[1], 0)
        with self.engine.request_lock:
            self.engine.pending_requests.pop(43, None)

    def test_folder_catalog_does_not_wait_behind_file_download_workers(self):
        with patch.object(self.engine.catalog_executor, "submit") as catalog, patch.object(self.engine.executor, "submit") as downloads:
            self.engine._event(1, 44, {"root": "test", "path": "Project"}, 0, 0, 0)
            catalog.assert_called_once()
            downloads.assert_not_called()

    def test_repeated_deletions_restore_zero_byte_allocations_without_fetching(self):
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCompressedFileSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.GetCompressedFileSizeW.restype = ctypes.c_ulong
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        for _ in range(3):
            path.unlink()
            self.store.record_local_deletion("test", "Project/scene.ma")
            self.engine.refresh()
            self.assertTrue(self.engine.is_unhydrated(path))
            self.assertEqual(path.stat().st_size, len(self.office.payload))
            high = ctypes.c_ulong()
            self.assertEqual(kernel.GetCompressedFileSizeW(str(path), ctypes.byref(high)), 0)
            self.assertEqual(high.value, 0)
            self.assertTrue(mesh.get_windows_attributes(path) & 0x100000)  # UNPINNED
        self.assertEqual(self.office.reads, 0)

    def test_scan_of_real_cloud_placeholder_does_not_require_final_file_name(self):
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        self.store.cloud_engine = self.engine
        original = Path.resolve
        def resolve(candidate, *args, **kwargs):
            if candidate == path:
                import ctypes
                raise ctypes.WinError(ctypes.c_long(0x801F0005).value)
            return original(candidate, *args, **kwargs)
        with patch.object(Path, "resolve", resolve):
            self.assertEqual(self.store.remote_path_from_local(self.root, path), "Project/scene.ma")
            self.assertTrue(self.store.is_placeholder_path(self.root, path))
        self.assertTrue(self.engine.is_unhydrated(path))
        self.assertEqual(self.office.reads, 0)

    def test_offline_local_work_is_seen_without_fetching_directory_metadata(self):
        path = self.store.local_path(self.root, "Project/new-local.ma")
        path.write_bytes(b"offline animator work")
        self.assertIn(path, list(self.engine.iter_files(self.root)))
        self.assertNotIn("Project", self.office.lists)
        self.assertEqual(self.office.reads, 0)

    def test_remote_update_is_downloaded_on_next_open(self):
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        self.assertEqual(path.read_bytes(), self.office.payload)
        # Wait for the callback to finish recording its baseline after the read unblocks.
        import time
        for _ in range(100):
            if self.downloads: break
            time.sleep(0.01)
        self.office.payload = b"new remote version with different content"
        self.office.modified = "2026-10-16T11:00:00Z"
        self.engine.populate("test", "Project")
        self.assertTrue(self.engine.native.state(path) & PARTIAL)
        self.assertEqual(path.read_bytes(), self.office.payload)

    def test_delete_recreates_only_the_listing_and_downloads_again_when_opened(self):
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        result = subprocess.run([sys.executable, "-c", "import os,sys; os.unlink(sys.argv[1])", str(path)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(path.exists())
        self.engine.refresh()
        self.engine.populate("test", "Project")
        self.assertTrue(path.exists())
        self.assertTrue(self.engine.is_unhydrated(path))
        self.assertTrue(self.store.is_local_deleted("test", "Project/scene.ma"))
        self.assertEqual(self.office.reads, 0)
        self.assertEqual(path.read_bytes(), self.office.payload)
        self.assertEqual(self.office.reads, 1)

    def test_deleted_cache_keeps_project_visible_without_restoring_offline_downloads(self):
        self.engine.populate("test", "Project")
        self.store.settings["offline_folders"] = [{"rootId": "test", "remotePath": ""}]
        callback, report = self.engine.downloaded, self.engine.report
        self.engine.close()  # Match closing the installed app: the sync-root registration remains.
        import shutil
        cache = self.store.root_dir(self.root)
        self.assertTrue(cache.resolve().is_relative_to(Path(self.temp.name).resolve()))
        shutil.rmtree(cache)
        self.engine = CloudFilesEngine(self.store, self.office, lambda root_id, rp: True,
                                       mesh.parse_remote_modified_at, callback, report)
        self.engine.start([self.root], background=False)
        self.engine.refresh()
        self.assertEqual(self.office.reads, 0)
        self.assertTrue((cache / "Project").exists())
        self.assertIn("test::Project", self.engine.remote_items)
        self.assertEqual(self.store.settings["offline_folders"], [])
        self.assertNotIn({"root": "test", "path": "Project"}, self.store.settings["cloud_populated_folders"])
        self.assertTrue(self.store.is_local_deleted("test", "Project/scene.ma"))
        result = subprocess.run([sys.executable, "-c", "import os,sys,json; print(json.dumps(os.listdir(sys.argv[1])))", str(cache / "Project")],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        self.assertEqual(json.loads(result.stdout), ["scene.ma"])
        self.assertEqual(self.office.reads, 0)
        self.assertTrue(self.engine.is_unhydrated(cache / "Project/scene.ma"))
        self.assertEqual((cache / "Project/scene.ma").read_bytes(), self.office.payload)
        self.assertEqual(self.office.reads, 1)

    def test_move_out_of_cache_recreates_only_an_online_listing(self):
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        removed = Path(self.temp.name) / "removed.ma"
        result = subprocess.run([sys.executable, "-c", "import os,sys; os.rename(sys.argv[1], sys.argv[2])", str(path), str(removed)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        reads = self.office.reads
        # A rename may finish without a delete notification; honor the same
        # short grace period used to distinguish an atomic save from a removal.
        with patch("local_deletions.time.monotonic", return_value=10):
            self.engine.refresh()
        with patch("local_deletions.time.monotonic", return_value=13):
            self.engine.refresh()
        self.assertTrue(path.exists())
        self.assertTrue(self.engine.is_unhydrated(path))
        self.assertEqual(self.office.reads, reads)
        self.assertTrue(removed.exists())

    def test_saved_deletion_does_not_hide_assigned_project_nested_folders_or_explorer_link(self):
        self.store.settings["local_deleted"] = [{"rootId": "test", "remotePath": ""}]
        access = {"role": "user", "roots": [{**self.root, "projectScoped": True, "access": ["Project"]}]}
        self.engine.allowed = lambda root_id, rp: mesh.can_access_remote_path(access, root_id, rp)
        modified = self.office.modified
        def directory(name):
            return {"name": name, "type": "directory", "modifiedAt": modified}
        catalog = {"": [directory("Project"), directory("NotAssigned")],
                   "Project": [directory("shots")], "Project/shots": [directory("s010")],
                   "Project/shots/s010": [{"name": "scene.ma", "type": "file", "size": len(self.office.payload), "modifiedAt": modified}]}
        self.office.list_folder = lambda root_id, rp: {"items": catalog.get(rp, [])}
        self.engine.populate("test", "")
        app = SimpleNamespace(store=self.store, roots=access["roots"], access=access, cloud_engine=self.engine)
        with patch("shell_integration.publish_links", return_value=Path(self.temp.name) / "hub") as links, \
             patch("shell_integration.register_namespace"):
            mesh.MeshClientApp._publish_explorer_links(app)
            self.assertEqual(links.call_args.args[1], [("X - Project", Path("X:/Project"))])
        cache = self.store.root_dir(self.root)
        self.assertFalse((cache / "NotAssigned").exists())
        result = subprocess.run([sys.executable, "-c",
            "import os,sys,json; from pathlib import Path; p=Path(sys.argv[1]); print(json.dumps([os.listdir(p), os.listdir(p/'shots'), os.listdir(p/'shots'/'s010')]))",
            str(cache / "Project")], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        self.assertEqual(json.loads(result.stdout), [["shots"], ["s010"], ["scene.ma"]])
        self.assertEqual(self.office.reads, 0)
        rp = "Project/shots/s010/scene.ma"
        self.assertEqual(mesh.MeshClientApp._should_background_download(app, self.root, rp, catalog["Project/shots/s010"][0]), (False, False))
        self.assertEqual((cache / rp).read_bytes(), self.office.payload)
        self.assertEqual(self.office.reads, 1)

    def test_native_files_work_through_a_junction(self):
        import _winapi
        cache = self.store.root_dir(self.root)
        partition = Path(self.temp.name) / "occupied-drive"
        partition.mkdir()
        alias = partition / "Project"
        _winapi.CreateJunction(str(cache / "Project"), str(alias))
        result = subprocess.run([sys.executable, "-c", "import sys,pathlib,hashlib; p=pathlib.Path(sys.argv[1]); print(hashlib.sha256(p.read_bytes()).hexdigest())", str(alias / "scene.ma")],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        import hashlib
        self.assertEqual(result.stdout.strip(), hashlib.sha256(self.office.payload).hexdigest())
        alias.rmdir()

    def test_revoked_permission_cannot_hydrate_a_file(self):
        self.engine.populate("test", "Project")
        self.engine.allowed = lambda root_id, rp: False
        path = self.store.local_path(self.root, "Project/scene.ma")
        result = subprocess.run([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).read_bytes()", str(path)],
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.office.reads, 0)

    def test_local_edit_is_preserved_when_remote_changes(self):
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        path.read_bytes()
        import time
        for _ in range(100):
            if self.downloads: break
            time.sleep(0.01)
        path.write_bytes(b"animator work in progress")
        self.office.modified = "2026-10-16T12:00:00Z"
        self.office.payload = b"other animator's newer office version"
        self.engine.populate("test", "Project")
        self.assertEqual(path.read_bytes(), b"animator work in progress")
        self.assertEqual(self.office.reads, 1)

    def test_maya_timestamp_change_on_empty_proxy_is_repaired_without_downloading(self):
        from cloud_files import IN_SYNC
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        timestamp = path.stat().st_mtime
        os.utime(path, (timestamp + 10, timestamp + 10))
        self.assertFalse(self.engine.native.state(path) & IN_SYNC)
        self.assertTrue(self.engine.native.clean(self.engine.native.inspect(path)))
        self.engine.populate("test", "Project")
        self.assertTrue(self.engine.native.state(path) & IN_SYNC)
        self.assertTrue(self.engine.is_unhydrated(path))
        self.assertEqual(self.office.reads, 0)
        self.engine.hydrate(self.root, "Project/scene.ma")
        self.assertEqual(path.read_bytes(), self.office.payload)

    def test_explicit_download_refreshes_stale_proxy_before_hydrating(self):
        self.engine.populate("test", "Project")
        self.office.modified = "2026-10-29T10:00:00Z"
        self.office.payload = b"new Office reference, changed size" * 199
        path = self.engine.hydrate(self.root, "Project/scene.ma")
        self.assertEqual(path.read_bytes(), self.office.payload)
        self.assertEqual(self.office.reads, 1)

    def test_opening_stale_reference_restarts_native_hydration_without_reopening_maya(self):
        self.engine.populate("test", "Project")
        self.office.modified = "2026-10-29T10:00:00Z"
        self.office.payload = b"updated Maya reference" * 137
        path = self.store.local_path(self.root, "Project/scene.ma")
        result = subprocess.run([sys.executable, "-c", "import pathlib,hashlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())", str(path)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        import hashlib
        self.assertEqual(result.stdout.strip(), hashlib.sha256(self.office.payload).hexdigest())
        self.assertEqual(self.office.reads, 1)

    def test_save_between_inspection_and_native_update_is_preserved(self):
        from cloud_files import CloudConflict
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        path.read_bytes()
        inspect = self.engine.native.inspect
        local_work = b"Maya save arrived while metadata was being checked"
        def racing_inspection(candidate):
            details = inspect(candidate)
            candidate.write_bytes(local_work)
            return details
        identity = self.engine.identity(self.root, "Project/scene.ma", self.office.remote_item("test", "Project/scene.ma"))
        with patch.object(self.engine.native, "inspect", side_effect=racing_inspection):
            with self.assertRaises(CloudConflict):
                self.engine.native.update(path, identity, directory=False, dehydrate=True)
        self.assertEqual(path.read_bytes(), local_work)

    def test_opening_reference_that_grew_in_office_receives_the_complete_new_file(self):
        self.engine.populate("test", "Project")
        self.office.modified = "2026-10-29T10:00:00Z"
        self.office.payload = self.office.payload * 2 + b"larger reference EOF"
        path = self.store.local_path(self.root, "Project/scene.ma")
        result = subprocess.run([sys.executable, "-c", "import pathlib,hashlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())", str(path)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        import hashlib
        self.assertEqual(result.stdout.strip(), hashlib.sha256(self.office.payload).hexdigest())

    def test_pending_work_does_not_advance_proxy_metadata_before_native_update(self):
        self.engine.populate("test", "Project")
        key = self.store.key("test", "Project/scene.ma")
        previous = dict(self.store.manifest[key])
        self.store.outbox["items"][key] = {"rootId": "test", "remotePath": "Project/scene.ma"}
        self.office.modified = "2026-10-29T10:00:00Z"
        self.office.payload = b"new version in Office"
        self.engine.populate("test", "Project")
        self.assertEqual(self.store.manifest[key]["modifiedAt"], previous["modifiedAt"])
        self.assertEqual(self.store.manifest[key]["size"], previous["size"])
        self.assertEqual(self.office.reads, 0)
        self.store.outbox["items"].clear()
        self.engine.populate("test", "Project")
        self.assertEqual(self.store.manifest[key]["size"], len(self.office.payload))
        self.assertEqual(self.office.reads, 0)

    def test_locally_truncated_file_is_not_treated_as_an_empty_downloadable_proxy(self):
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        path.read_bytes()
        path.write_bytes(b"")
        self.assertFalse(self.engine.native.clean(self.engine.native.inspect(path)))
        self.office.payload = b"new remote content"
        self.office.modified = "2026-10-29T10:00:00Z"
        self.engine.populate("test", "Project")
        self.assertEqual(path.stat().st_size, 0)
        self.assertEqual(self.office.reads, 1)

    def test_existing_content_is_not_uploaded_and_new_name_can_be_uploaded(self):
        self.engine.populate("test", "Project")
        path = self.store.local_path(self.root, "Project/scene.ma")
        path.read_bytes()
        import time
        for _ in range(100):
            if self.downloads: break
            time.sleep(0.01)
        self.store.save_outbox = Mock()
        app = SimpleNamespace(store=self.store, file_snapshots={}, post=Mock(), _clear_transfer_state=Mock())
        self.assertFalse(mesh.MeshClientApp._content_needs_upload(app, self.root, "Project/scene.ma", path))
        copy = path.with_name("copied.ma")
        copy.write_bytes(self.office.payload)
        self.assertTrue(mesh.MeshClientApp._content_needs_upload(app, self.root, "Project/copied.ma", copy))
        path.write_bytes(b"new animator changes")
        self.assertTrue(mesh.MeshClientApp._content_needs_upload(app, self.root, "Project/scene.ma", path))

    def test_legacy_empty_proxy_is_upgraded_to_real_placeholder(self):
        folder = self.store.local_path(self.root, "Project")
        legacy = folder / "scene.ma"
        legacy.touch()
        self.store.manifest[self.store.key("test", "Project/scene.ma")] = {"placeholder": True, "localPath": str(legacy)}
        self.engine.populate("test", "Project")
        self.assertEqual(legacy.stat().st_size, len(self.office.payload))
        self.assertTrue(self.engine.is_unhydrated(legacy))
        self.assertEqual(legacy.read_bytes(), self.office.payload)


if __name__ == "__main__":
    unittest.main()
