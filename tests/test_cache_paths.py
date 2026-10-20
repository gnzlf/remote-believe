"""Cloud name failures must not download files, forget work, or escape the cache."""
import ctypes
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import mesh_client as mesh
from cache_paths import cache_relative_path, local_path_key


def name_error():
    return ctypes.WinError(ctypes.c_long(0x801F0005).value)


class CachePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = mesh.LocalStore.__new__(mesh.LocalStore)
        self.store.settings = {"cache_dir": self.temp.name}
        self.store.manifest, self.store.outbox = {}, {"items": {}}
        self.store.save_settings, self.store.save_manifest, self.store.save_outbox = Mock(), Mock(), Mock()
        self.root = {"id": "test", "driveLetter": "X", "access": "*"}
        self.path = self.store.local_path(self.root, "Project/nofilebig.jpg")
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"unchanged picture")

    def fail_final_name(self):
        original = Path.resolve
        path = self.path
        def resolve(candidate, *args, **kwargs):
            if candidate == path:
                raise name_error()
            return original(candidate, *args, **kwargs)
        return patch.object(Path, "resolve", resolve)

    def fail_metadata(self):
        original = Path.exists
        path = self.path
        def exists(candidate):
            if candidate == path:
                raise name_error()
            return original(candidate)
        return patch.object(Path, "exists", exists)

    def test_cloud_reparse_tag_does_not_request_its_final_name(self):
        original = Path.lstat
        path = self.path
        def metadata(candidate):
            if candidate == path:
                return SimpleNamespace(st_mode=stat.S_IFREG, st_reparse_tag=0x9000001A)
            return original(candidate)
        with self.fail_final_name(), patch.object(Path, "lstat", metadata):
            self.assertEqual(self.store.remote_path_from_local(self.root, path), "Project/nofilebig.jpg")

    def test_tracking_keys_never_open_or_resolve_a_file(self):
        with patch.object(Path, "resolve", side_effect=name_error()), patch.object(Path, "stat", side_effect=name_error()):
            self.assertEqual(local_path_key(self.path), os.path.normcase(str(self.path)))

    def test_cache_selection_from_explorer_accepts_the_placeholder(self):
        self.store.local_drive = Mock(return_value="")
        with self.fail_final_name():
            root, rp, path = mesh.target_to_root_path(self.store, [self.root], str(self.path))
        self.assertEqual((root, rp, path), (self.root, "Project/nofilebig.jpg", self.path))

    def test_real_junction_inside_cache_still_cannot_escape(self):
        import _winapi
        outside = Path(self.temp.name) / "other-job"
        outside.mkdir()
        link = self.path.parent / "escape"
        _winapi.CreateJunction(str(outside), str(link))
        try:
            with self.assertRaises(ValueError):
                cache_relative_path(self.store.root_dir(self.root), link / "work.ma")
            with patch.object(Path, "resolve", side_effect=PermissionError("blocked junction")):
                with self.assertRaises(PermissionError):
                    cache_relative_path(self.store.root_dir(self.root), link / "work.ma")
        finally:
            self.assertEqual(link.lstat().st_reparse_tag, 0xA0000003)
            link.rmdir()  # Only the junction just created, never its target.

    def test_same_prefix_is_not_containment(self):
        with self.assertRaises(ValueError):
            cache_relative_path(self.store.root_dir(self.root), Path(self.temp.name) / "X-other" / "file.ma")

    def test_download_completion_keeps_baseline_without_final_name_lookup(self):
        key = local_path_key(self.path)
        app = SimpleNamespace(store=self.store, file_snapshots={}, pending_changes={key: "pending"})
        with self.fail_final_name():
            mesh.MeshClientApp._cloud_downloaded(app, self.root, "Project/nofilebig.jpg",
                {"size": self.path.stat().st_size}, self.path, "known-digest")
        self.assertIn(key, app.file_snapshots)
        self.assertNotIn(key, app.pending_changes)
        self.assertEqual(self.store.manifest["test::Project/nofilebig.jpg"]["sha256"], "known-digest")

    def test_unknown_metadata_does_not_cancel_pin_or_delete_pending_work(self):
        self.store.settings["offline_folders"] = [{"rootId": "test", "remotePath": "Project"}]
        self.store.manifest["test::Project/nofilebig.jpg"] = {"localPath": str(self.path)}
        self.store.outbox["items"]["work"] = {"rootId": "test", "remotePath": "Project/nofilebig.jpg", "localPath": str(self.path)}
        with self.fail_metadata():
            self.assertFalse(self.store.reconcile_local_deletions([self.root]))
            # Even another deletion under the same project must keep the unavailable work.
            self.store.record_local_deletion("test", "Project")
        self.assertIn("work", self.store.outbox["items"])

    def test_unknown_metadata_leaves_existing_deletion_policy_unchanged(self):
        self.store.settings["offline_folders"] = [{"rootId": "test", "remotePath": "Project"}]
        self.store.manifest["test::Project/nofilebig.jpg"] = {"localPath": str(self.path)}
        with self.fail_metadata():
            self.assertFalse(self.store.reconcile_local_deletions([self.root]))
        self.assertFalse(self.store.is_local_deleted("test", "Project/nofilebig.jpg"))
        self.assertEqual(self.store.settings["offline_folders"], [{"rootId": "test", "remotePath": "Project"}])

    def test_pruning_preserves_unavailable_pending_file(self):
        self.store.outbox["items"]["work"] = {"rootId": "test", "remotePath": "Project/nofilebig.jpg", "localPath": str(self.path)}
        app = SimpleNamespace(store=self.store, _root_by_id=Mock(return_value=self.root), _clear_transfer_state=Mock())
        with self.fail_metadata():
            mesh.MeshClientApp._prune_unsafe_pending_uploads(app)
        self.assertIn("work", self.store.outbox["items"])
        app._clear_transfer_state.assert_not_called()

    def test_offline_scan_continues_to_healthy_work_after_metadata_error(self):
        work = self.path.parent / "work.ma"
        work.write_bytes(b"new animator work")
        self.store.iter_local_files = Mock(return_value=iter([self.path, work]))
        app = SimpleNamespace(store=self.store, roots=[self.root], download_paths=set(), file_snapshots={},
                              _queue_pending_upload=Mock(), post=Mock(), set_status=Mock())
        original = Path.is_file
        def is_file(candidate):
            if candidate == self.path:
                raise name_error()
            return original(candidate)
        with patch.object(Path, "is_file", is_file), patch.object(mesh, "upload_policy", return_value=(True, "")):
            self.assertEqual(mesh.MeshClientApp._scan_for_offline_changes(app), 1)
        self.assertEqual(app._queue_pending_upload.call_args.args[2], work)


if __name__ == "__main__":
    unittest.main()
