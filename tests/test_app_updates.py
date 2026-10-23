import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from app_updates import GitHubUpdates, UpdateError, REPOSITORY, validate_release, version
from desktop_controls import DesktopControls


class Response(io.BytesIO):
    headers = {"ETag": '"v1"'}
    def geturl(self): return "https://release-assets.githubusercontent.com/test"


def release(tag="v3.1.1", payload=b"test installer"):
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "assets": [{"name": "RemoteBelieveSetup.exe", "state": "uploaded", "size": len(payload),
                        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
                        "browser_download_url": "https://github.com/" + REPOSITORY + "/releases/download/" + tag + "/RemoteBelieveSetup.exe"}]}


class UpdateTests(unittest.TestCase):
    def test_versions_are_numeric_not_lexical(self):
        self.assertGreater(version("v3.10.0"), version("3.9.9-explorer"))
        for invalid in ("latest", "../v3.1.0", "3.1", "v3.1.0-beta"):
            with self.assertRaises(UpdateError): version(invalid)

    def test_unpublished_incomplete_or_unsigned_assets_are_not_accepted(self):
        for transform in (lambda r: r.update(draft=True), lambda r: r.update(prerelease=True),
                          lambda r: r.update(assets=[]), lambda r: r["assets"][0].update(digest=""),
                          lambda r: r["assets"][0].update(browser_download_url="https://other.example/payload.exe")):
            data = release()
            transform(data)
            with self.assertRaises(UpdateError): validate_release(data)

    def test_latest_release_is_checked_before_allowing_use(self):
        opener = Mock()
        opener.open.return_value = Response(json.dumps(release()).encode())
        available, data = GitHubUpdates("3.1.0-explorer", "unused", opener).check()
        self.assertTrue(available)
        self.assertEqual(data["tag"], "v3.1.1")

    def test_304_uses_only_a_previously_verified_response(self):
        opener = Mock()
        opener.open.side_effect = [Response(json.dumps(release()).encode()), HTTPError("url", 304, "same", {}, None)]
        updater = GitHubUpdates("3.1.0-explorer", "unused", opener)
        self.assertEqual(updater.check(), updater.check())

    def test_offline_check_has_no_silent_latest_version_bypass(self):
        opener = Mock()
        opener.open.side_effect = OSError("offline")
        with self.assertRaises(UpdateError): GitHubUpdates("3.1.0-explorer", "unused", opener).check()

    def test_api_rate_limit_uses_public_verified_manifest(self):
        asset = release()["assets"][0]
        manifest = {"tag": "v3.1.1", "url": asset["browser_download_url"], "size": asset["size"], "sha256": asset["digest"][7:]}
        opener = Mock()
        opener.open.side_effect = [HTTPError("url", 403, "quota", {}, None), Response(json.dumps(manifest).encode())]
        available, data = GitHubUpdates("3.1.0-explorer", "unused", opener).check()
        self.assertTrue(available)
        self.assertEqual(data["tag"], "v3.1.1")

    def test_only_complete_hash_verified_installer_is_promoted(self):
        opener = Mock()
        opener.open.return_value = Response(b"test installer")
        with tempfile.TemporaryDirectory() as temp:
            target = GitHubUpdates("3.1.0-explorer", temp, opener).download(validate_release(release()))
            self.assertEqual(target.read_bytes(), b"test installer")
            self.assertFalse(target.with_suffix(".downloading").exists())

    def test_corrupt_installer_is_removed_and_cannot_run(self):
        opener = Mock()
        opener.open.return_value = Response(b"bad installer!")
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(UpdateError):
                GitHubUpdates("3.1.0-explorer", temp, opener).download(validate_release(release()))
            self.assertFalse(list(Path(temp).rglob("*.exe")))
            self.assertFalse(list(Path(temp).rglob("*.downloading")))

    def test_new_release_stops_a_live_session_before_showing_the_gate(self):
        app = SimpleNamespace(screen="main", _logout=Mock(), after=Mock(), _check_update_now=Mock())
        DesktopControls._update_result(app, True, {"tag": "v3.1.1"}, None)
        self.assertTrue(app.update_blocked)
        app._logout.assert_called_once()

    def test_failed_verification_does_not_enable_login(self):
        app = SimpleNamespace(screen="update", _build_update_screen=Mock(), _show_window=Mock(),
                              after=Mock(), after_cancel=Mock(), _check_update_now=Mock(), _build_login=Mock())
        DesktopControls._update_result(app, False, None, "offline")
        self.assertTrue(app.update_blocked)
        app._build_login.assert_not_called()
        DesktopControls._update_result(app, False, {"tag": "v3.1.0"}, None)
        self.assertFalse(app.update_blocked)
        app._build_login.assert_called_once()

    def test_transient_github_failure_does_not_disconnect_a_verified_live_session(self):
        app = SimpleNamespace(screen="main", update_release=None, update_blocked=False, _logout=Mock(),
                              after=Mock(), _check_update_now=Mock(), set_status=Mock())
        DesktopControls._update_result(app, False, None, "temporary network failure")
        app._logout.assert_not_called()
        self.assertFalse(app.update_blocked)
        self.assertEqual(app.after.call_args.args[0], 60000)


if __name__ == "__main__": unittest.main()
