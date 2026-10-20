from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import mesh_client as mesh


class LocalDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = mesh.LocalStore.__new__(mesh.LocalStore)
        self.store.settings = {"cache_dir": self.temp.name}
        self.store.manifest, self.store.outbox = {}, {"items": {}}
        self.store.save_settings, self.store.save_manifest, self.store.save_outbox = Mock(), Mock(), Mock()
        self.root = {"id": "test", "driveLetter": "X"}

    def test_missing_cache_cancels_root_pin_even_without_a_manifest(self):
        self.store.settings["offline_folders"] = [{"rootId": "test", "remotePath": ""}]
        self.store.reconcile_local_deletions([self.root])
        self.assertEqual(self.store.settings["offline_folders"], [])
        self.assertTrue(self.store.is_local_deleted("test", "Project/scene.ma"))
        self.assertFalse(self.store.root_dir(self.root).exists())

    def test_new_installation_without_history_can_browse_normally(self):
        self.assertFalse(self.store.reconcile_local_deletions([self.root]))
        self.assertFalse(self.store.is_local_deleted("test", "Project"))

    def test_deleted_child_overrides_parent_pin_and_drops_only_missing_upload(self):
        folder = self.store.local_path(self.root, "Project")
        folder.mkdir(parents=True)
        work = folder / "work.ma"
        work.write_bytes(b"keep this local work")
        gone = folder / "gone.ma"
        self.store.manifest = {"test::Project/gone.ma": {"localPath": str(gone)}}
        self.store.settings["offline_folders"] = [{"rootId": "test", "remotePath": "Project"},
                                                   {"rootId": "other", "remotePath": "Elsewhere"}]
        self.store.outbox["items"] = {
            "gone": {"rootId": "test", "remotePath": "Project/gone.ma", "localPath": str(gone)},
            "work": {"rootId": "test", "remotePath": "Project/work.ma", "localPath": str(work)}}
        self.store.reconcile_local_deletions([self.root])
        self.assertEqual(self.store.settings["offline_folders"], [{"rootId": "other", "remotePath": "Elsewhere"}])
        self.assertEqual(list(self.store.outbox["items"]), ["work"])
        self.assertEqual(work.read_bytes(), b"keep this local work")
        app = SimpleNamespace(store=self.store)
        self.assertEqual(mesh.MeshClientApp._should_background_download(app, self.root, "Project/gone.ma", {"type": "file"}), (False, False))
        self.store.restore_local_downloads("test", "Project/gone.ma")
        self.assertFalse(self.store.is_local_deleted("test", "Project/gone.ma"))
        self.assertNotIn("test::Project/gone.ma", self.store.manifest)
        self.assertEqual(self.store.settings["offline_folders"], [{"rootId": "other", "remotePath": "Elsewhere"}])

    def test_atomic_save_gap_does_not_turn_into_a_local_deletion(self):
        folder = self.store.local_path(self.root, "Project")
        folder.mkdir(parents=True)
        path = folder / "scene.ma"
        self.store.manifest["test::Project/scene.ma"] = {"localPath": str(path)}
        self.store.reconcile_local_deletions([self.root], live=True)
        self.assertFalse(self.store.is_local_deleted("test", "Project/scene.ma"))
        path.write_bytes(b"Maya finished replacing the scene")
        self.store.reconcile_local_deletions([self.root], live=True)
        self.assertFalse(self.store.is_local_deleted("test", "Project/scene.ma"))

    def test_deletion_during_offline_listing_wins_and_manual_download_can_restore(self):
        self.root.update(access="*", projectScoped=False)
        folder = self.store.local_path(self.root, "Project")
        folder.mkdir(parents=True)
        self.store.settings["offline_folders"] = [{"rootId": "test", "remotePath": "Project"}]
        response = {"items": [{"name": "scene.ma", "type": "file", "size": 32}]}
        app = SimpleNamespace(store=self.store, access={"roots": [self.root]},
                              file_api=SimpleNamespace(list_folder=Mock(side_effect=lambda *args: (folder.rmdir(), response)[1])),
                              _ensure_local_folder=Mock(), _materialize_items=Mock(),
                              _download_remote_file=Mock(return_value=[folder / "scene.ma"]),
                              post=Mock(), _log_activity=Mock(), _friendly_transfer_error=str)
        self.assertEqual(mesh.MeshClientApp._sync_offline_folder(app, self.root, "Project", 10), (0, 0))
        app._ensure_local_folder.assert_not_called()
        app._download_remote_file.assert_not_called()
        self.assertTrue(self.store.is_local_deleted("test", "Project"))
        app.file_api.list_folder = Mock(return_value=response)
        app._ensure_local_folder = lambda root, rp: self.store.local_path(root, rp).mkdir(parents=True, exist_ok=True)
        self.assertEqual(mesh.MeshClientApp._download_remote_folder(app, self.root, "Project"), 1)
        self.assertTrue(folder.exists())
        self.assertFalse(self.store.is_local_deleted("test", "Project"))


if __name__ == "__main__":
    unittest.main()
