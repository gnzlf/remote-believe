from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import mesh_client as mesh
from cloud_files import CloudConflict
from transfer_control import TransferStopped


class MayaFolderDownloadTests(unittest.TestCase):
    def application(self, failure):
        root = {"id": "test"}
        items = [{"name": "working.ma", "type": "file"}, {"name": "reference.ma", "type": "file"}]
        store = SimpleNamespace(reconcile_local_deletions=Mock(), restore_local_downloads=Mock(),
                                is_local_deleted=Mock(return_value=False))
        app = SimpleNamespace(store=store, access={"role": "user", "roots": [{**root, "access": "*"}]}, file_api=SimpleNamespace(
            list_folder=Mock(return_value={"items": items})), _ensure_local_folder=Mock(),
            _download_remote_file=Mock(side_effect=[failure, [Path("reference.ma")]]), post=Mock(), _log_activity=Mock())
        return root, app

    def test_one_conflicting_maya_file_does_not_block_other_reference_downloads(self):
        root, app = self.application(CloudConflict("Working scene preserved"))
        count = mesh.MeshClientApp._download_folder_payload(app, root, "Project")
        self.assertEqual(count, 1)
        self.assertEqual(app._download_remote_file.call_count, 2)
        self.assertIn("working.ma", app.post.call_args.args[3])

    def test_user_cancellation_still_stops_the_entire_folder(self):
        root, app = self.application(TransferStopped("Stopped by user"))
        with self.assertRaises(TransferStopped):
            mesh.MeshClientApp._download_folder_payload(app, root, "Project")
        self.assertEqual(app._download_remote_file.call_count, 1)
