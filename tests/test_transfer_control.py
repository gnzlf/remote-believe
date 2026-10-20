import contextlib
import hashlib
import http.server
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "client"), str(Path(__file__).parent)]
import mesh_client as mesh
import test_office_api as office
from transfer_control import TransferControl, TransferStopped, background_reader


class ContentPolicyTests(unittest.TestCase):
    def setUp(self):
        office.OfficeApiTests.setUp(self)
        self.stack.enter_context(patch.object(mesh, "DATA_DIR", self.base / "provider-state"))
        self.folder = "Project/shots/s010/animation"
        original = self.file
        self.file = self.base / self.folder / "scene.ma"
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(original.read_bytes())
        original.unlink()
        self.local = self.base / "LocalWork" / "scene.ma"
        self.local.parent.mkdir()

    def test_identical_content_with_different_mtime_sends_no_chunks(self):
        self.local.write_bytes(self.file.read_bytes())
        self.api.upload_chunk = Mock()
        self.assertEqual(self.api.upload_file("test", self.folder, self.local, lambda *args: None)["status"], "already-current")
        self.api.upload_chunk.assert_not_called()

    def test_same_name_with_changed_content_is_uploaded(self):
        self.local.write_bytes(b"new animator work")
        self.assertEqual(self.api.upload_file("test", self.folder, self.local, lambda *args: None)["status"], "uploaded")
        self.assertEqual(self.file.read_bytes(), self.local.read_bytes())

    def test_same_size_and_date_are_not_used_as_proof_of_identical_content(self):
        metadata = self.file.stat()
        self.local.write_bytes(b"x" * metadata.st_size)
        os.utime(self.local, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        self.assertEqual(self.api.upload_file("test", self.folder, self.local, lambda *args: None)["status"], "uploaded")
        self.assertEqual(self.file.read_bytes(), b"x" * metadata.st_size)

    def test_new_name_can_be_uploaded(self):
        self.local.write_bytes(b"new file")
        self.assertEqual(self.api.upload_file("test", self.folder + "/new-shot", self.local, lambda *args: None)["status"], "uploaded")
        self.assertEqual((self.file.parent / "new-shot/scene.ma").read_bytes(), b"new file")

    def test_offline_existence_check_never_means_remote_file_absent(self):
        self.local.write_bytes(b"new file")
        original = self.file.read_bytes()
        self.api.remote_item = Mock(side_effect=mesh.RBError("offline"))
        self.api.upload_chunk = Mock()
        with self.assertRaises(mesh.RBError):
            self.api.upload_file("test", self.folder, self.local, lambda *args: None)
        self.api.upload_chunk.assert_not_called()
        self.assertEqual(self.file.read_bytes(), original)

    def test_server_edit_during_upload_is_not_overwritten(self):
        self.local.write_bytes(b"local change")
        upload = self.api.upload_chunk
        def chunk(*args):
            result = upload(*args)
            self.file.write_bytes(b"another animator changed Office")
            return result
        self.api.upload_chunk = chunk
        with self.assertRaisesRegex(mesh.RBError, "Oficina cambio"):
            self.api.upload_file("test", self.folder, self.local, lambda *args: None)
        self.assertEqual(self.file.read_bytes(), b"another animator changed Office")
        self.assertEqual(self.local.read_bytes(), b"local change")

    def test_server_edit_between_queue_check_and_upload_is_not_overwritten(self):
        previous = self.api.remote_item("test", self.folder + "/scene.ma")["sha256"]
        self.local.write_bytes(b"local change")
        self.file.write_bytes(b"new work in Office")
        self.api.upload_chunk = Mock()
        with self.assertRaisesRegex(mesh.RBError, "Oficina cambio"):
            self.api.upload_file("test", self.folder, self.local, lambda *args: None, expected_hash=previous)
        self.api.upload_chunk.assert_not_called()
        self.assertEqual(self.file.read_bytes(), b"new work in Office")

    def test_hash_lookup_respects_permissions(self):
        with self.assertRaises(mesh.RBError):
            self.api.remote_item("test", "OtherJob/private.ma")

    def test_cancelled_queue_entry_cannot_start_a_later_upload(self):
        self.local.write_bytes(b"local change")
        self.api.remote_item = Mock()
        self.api.upload_chunk = Mock()
        with self.assertRaises(TransferStopped):
            self.api.upload_file("test", self.folder, self.local, lambda *args: None, pending_cb=lambda: False)
        self.api.remote_item.assert_not_called()
        self.api.upload_chunk.assert_not_called()

    def test_upload_permission_revoked_during_transfer_prevents_commit(self):
        self.local.write_bytes(b"local change")
        original = self.file.read_bytes()
        upload = self.api.upload_chunk
        def chunk(*args):
            result = upload(*args)
            self.config["users"]["worker"]["directUpload"] = False
            return result
        self.api.upload_chunk = chunk
        with self.assertRaisesRegex(mesh.RBError, "subida directa"):
            self.api.upload_file("test", self.folder, self.local, lambda *args: None)
        self.assertEqual(self.file.read_bytes(), original)


class TransferControlTests(unittest.TestCase):
    def test_pause_is_directional_and_old_operation_stays_cancelled_after_resume(self):
        control = TransferControl()
        with self.assertRaises(TransferStopped):
            with control.operation("upload", "root", "file.ma") as operation:
                control.pause("upload")
                self.assertFalse(control.is_paused("download"))
                control.resume("upload")
                operation.check()
        with control.operation("upload", "root", "file.ma"):
            pass

    def test_paused_direction_rejects_new_operations(self):
        control = TransferControl({"download": True})
        with self.assertRaises(TransferStopped), control.operation("download", "root", "file.jpg"):
            self.fail("Must not start")
        self.assertEqual(control.snapshot(), [])

    def test_cancel_path_stops_descendants_without_stopping_other_projects(self):
        control = TransferControl()
        with contextlib.ExitStack() as stack:
            first = stack.enter_context(control.operation("download", "root", "Project/file.jpg"))
            second = stack.enter_context(control.operation("download", "root", "Other/file.jpg"))
            control.cancel_path("download", "root", "Project")
            self.assertTrue(first.stopped.is_set())
            self.assertFalse(second.stopped.is_set())
            # Explicitly consume the cancellation on exit.
            first.stopped.clear()

    def test_background_windows_readers_are_blocked_and_work_apps_are_allowed(self):
        self.assertTrue(background_reader(r"C:\Windows\explorer.exe"))
        self.assertTrue(background_reader(r"C:\Windows\System32\SearchProtocolHost.exe"))
        self.assertFalse(background_reader(r"C:\Program Files\Autodesk\maya.exe"))
        self.assertFalse(background_reader(r"C:\Program Files\Photos\Photos.exe"))

    def test_pause_interrupts_a_blocked_download_socket(self):
        ready, release = threading.Event(), threading.Event()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(20 * 1024 * 1024))
                self.end_headers()
                self.wfile.write(b"x" * 1024)
                self.wfile.flush()
                ready.set()
                release.wait(5)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            api = mesh.OfficeFileApi("127.0.0.1")
            api.office_url = "http://127.0.0.1:" + str(server.server_port)
            result = []
            with tempfile.TemporaryDirectory() as temp:
                def download():
                    try: api.download_file("test", "file.ma", Path(temp) / "file.ma", lambda *args: None)
                    except Exception as exc: result.append(exc)
                thread = threading.Thread(target=download)
                thread.start()
                self.assertTrue(ready.wait(3))
                started = time.monotonic()
                api.transfers.pause("download")
                thread.join(3)
                self.assertFalse(thread.is_alive(), "Stop must interrupt the read, not wait for logout or the timeout")
                self.assertLess(time.monotonic() - started, 3)
                self.assertIsInstance(result[0], TransferStopped)
                self.assertFalse((Path(temp) / "file.ma").exists())
        finally:
            release.set()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
