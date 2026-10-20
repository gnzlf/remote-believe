import contextlib
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import mesh_client as mesh


class OfficeApiTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.base = Path(temp)
        (self.base / "Project").mkdir()
        self.file = self.base / "Project" / "scene.ma"
        self.file.write_bytes(b"mock office scene")
        os.utime(self.file, (1792159200, 1792159200))
        self.config = {"users": {"worker": {"password": "test-only", "role": "user", "directUpload": True,
                                          "projectAccess": {"test": ["Project"]}}},
                       "roots": [{"id": "test", "name": "X:", "driveLetter": "X", "path": str(self.base), "projectScoped": True}]}
        self.stack.enter_context(patch.object(mesh, "load_syncremote", return_value=self.config))
        self.stack.enter_context(patch.object(mesh, "SERVER_TOKENS", {}))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), mesh.OfficeServerHandler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = mesh.OfficeFileApi("127.0.0.1")
        self.api.office_url = "http://127.0.0.1:{}".format(self.server.server_port)
        self.api.login("worker", "test-only")

    def test_reconnect_after_office_token_is_lost(self):
        mesh.SERVER_TOKENS.clear()
        data = self.api.list_folder("test", "Project")
        self.assertEqual(data["items"][0]["name"], "scene.ma")

    def test_logout_revokes_only_its_own_token_and_cannot_reconnect(self):
        other = mesh.OfficeFileApi("127.0.0.1")
        other.office_url = self.api.office_url
        with patch.object(mesh.time, "time", return_value=1792159200):
            self.api.login("worker", "test-only")
            other.login("worker", "test-only")
        token = self.api.token
        self.assertNotEqual(token, other.token)
        self.api.close_session()
        self.assertNotIn(token, mesh.SERVER_TOKENS)
        self.assertIsNone(self.api.token)
        self.assertIsNone(self.api._login_secret)
        with patch.object(self.api.opener, "open") as opened:
            with self.assertRaisesRegex(mesh.RBError, "Sesion cerrada"):
                self.api.get_access()
            with self.assertRaisesRegex(mesh.RBError, "Sesion cerrada"):
                self.api.login("worker", "test-only")
            opened.assert_not_called()
        self.assertEqual(other.list_folder("test", "Project")["items"][0]["name"], "scene.ma")

    def test_logout_during_upload_prevents_commit(self):
        self.api.upload_chunk = Mock(side_effect=lambda *args: self.api.close_session())
        self.api.complete_upload = Mock()
        source = self.base / "local-change.ma"
        source.write_bytes(b"new local work")
        with self.assertRaises((mesh.RBError, mesh.TransferStopped)):
            self.api.upload_file("test", "Project", source, lambda *args: None)
        self.api.complete_upload.assert_not_called()

    def test_changed_download_version_is_rejected(self):
        with self.assertRaisesRegex(mesh.RBError, "cambio en Oficina"):
            self.api._req("GET", "/api/download", {"root": "test", "path": "Project/scene.ma", "expectedSize": 1})
        with self.api._req("GET", "/api/download", {"root": "test", "path": "Project/scene.ma", "expectedSize": self.file.stat().st_size}) as response:
            self.assertEqual(response.read(), self.file.read_bytes())

    def test_forbidden_paths_and_delete_do_not_modify_office(self):
        for path in ("../outside", "Project/../../outside", "Project/../Other", "Project/.. /Other"):
            with self.subTest(path=path), self.assertRaises(mesh.RBError):
                self.api.list_folder("test", path)
        with self.assertRaises(mesh.RBError):
            self.api._req("POST", "/api/delete", data=json.dumps({"root": "test", "path": "Project/scene.ma"}).encode())
        self.assertEqual(self.file.read_bytes(), b"mock office scene")

    def test_user_cannot_read_syncremote_even_with_full_root_access(self):
        (self.base / "syncremote.json").write_text('{"users": "secret"}', encoding="utf-8")
        self.config["users"]["worker"]["projectAccess"]["test"] = "*"
        listed = self.api.list_folder("test", "")
        self.assertNotIn("syncremote.json", [item["name"] for item in listed["items"]])
        with self.assertRaises(mesh.RBError):
            self.api._req("GET", "/api/download", {"root": "test", "path": "syncremote.json"})


if __name__ == "__main__":
    unittest.main()
