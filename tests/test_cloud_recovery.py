"""Reproduce interrupted/slow cloud reads without any production files."""
import hashlib
import io
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from cloud_files import (CloudFilesEngine, NativeCloud, failure_status, UNAVAILABLE, FAILED,
                         ABORTED, DENIED, NOT_IN_SYNC, TIMED_OUT)
from office_errors import OfficeNetworkError, OfficeResponseError
from transfer_control import TransferControl, TransferStopped


class Response:
    def __init__(self, payload, start=0, fail_after=None):
        self.stream = io.BytesIO(payload[start:])
        self.fail_after = fail_after
        self.status = 206 if start else 200
        self.headers = {"Content-Length": str(len(payload) - start)}
        if start:
            self.headers["Content-Range"] = "bytes {}-{}/{}".format(start, len(payload) - 1, len(payload))

    def read(self, size):
        raise AssertionError("Cloud hydration must stream available bytes using read1, not wait for a large read")

    def read1(self, size):
        if self.fail_after is not None and self.stream.tell() >= self.fail_after:
            raise ConnectionResetError("temporary interruption")
        return self.stream.read(min(size, 4096))

    def __enter__(self): return self
    def __exit__(self, *args): pass


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.payload = b"content from Office\n" * 10001
        self.store = SimpleNamespace(key=lambda r, p: r + "::" + p,
            local_path=lambda r, p: Path(self.temp.name) / p, root_dir=lambda r: Path(self.temp.name))
        self.native = Mock()
        self.native.check = NativeCloud.check
        self.native.dll.RBPending.return_value = True
        self.native.dll.RBTransfer.return_value = 0
        self.api = SimpleNamespace(transfers=TransferControl(), _req=Mock())
        self.downloaded = Mock()
        self.engine = CloudFilesEngine(self.store, self.api, lambda *args: True, lambda x: 0,
                                       self.downloaded, native=self.native)
        self.engine.roots["test"] = {"id": "test"}
        self.addCleanup(self.engine.close)
        self.identity = {"root": "test", "path": "Project/scene.ma", "modifiedAt": "2026-10-23T10:00:00Z"}

    def fetch(self):
        with patch("cloud_files.logger", logging.getLogger("RecoveryTests")):
            self.engine._fetch(42, self.identity, len(self.payload), 0, len(self.payload))

    def assert_transferred_once(self):
        chunks = self.native.dll.RBTransfer.call_args_list
        received = 0
        for call in chunks:
            request_id, data, offset, length = call.args
            self.assertEqual(offset, received)
            self.assertEqual(len(data), length)
            self.assertTrue(length % 4096 == 0 or offset + length == len(self.payload))
            received += length
        self.assertEqual(b"".join(call.args[1] for call in chunks), self.payload)
        self.assertEqual(self.downloaded.call_args.args[-1], hashlib.sha256(self.payload).hexdigest())

    def test_small_stream_reads_do_not_wait_for_a_multi_megabyte_block(self):
        self.api._req.return_value = Response(self.payload)
        self.fetch()
        self.assert_transferred_once()

    def test_midstream_interruption_resumes_from_confirmed_bytes_without_duplicates(self):
        def response(*args, **kwargs):
            start = int(kwargs["headers"].get("Range", "bytes=0-")[6:-1])
            return Response(self.payload, start, fail_after=65536 if not start else None)
        self.api._req.side_effect = response
        self.fetch()
        self.assertEqual(self.api._req.call_count, 2)
        self.assertEqual(self.api._req.call_args.kwargs["headers"], {"Range": "bytes=65536-"})
        self.assert_transferred_once()

    def test_stop_during_interruption_does_not_retry_or_mark_download_complete(self):
        response = Response(self.payload)
        def read(size):
            self.api.transfers.pause("download")
            raise ConnectionResetError("stopped")
        response.read1 = read
        self.api._req.return_value = response
        with self.assertRaises(TransferStopped):
            self.fetch()
        self.assertEqual(self.api._req.call_count, 1)
        self.downloaded.assert_not_called()

    def test_unavailable_office_is_retried_but_permission_errors_are_not(self):
        self.api._req.side_effect = [OfficeNetworkError("temporary"), Response(self.payload)]
        self.fetch()
        self.assertEqual(self.api._req.call_count, 2)
        self.assert_transferred_once()
        self.api._req.reset_mock(side_effect=True)
        self.api._req.side_effect = OfficeResponseError("Sin permiso", 403)
        with self.assertRaises(OfficeResponseError): self.fetch()
        self.assertEqual(self.api._req.call_count, 1)

    def test_windows_receives_the_real_failure_class_not_always_network_unavailable(self):
        cases = [(OfficeNetworkError("offline"), UNAVAILABLE), (ValueError("metadata"), FAILED),
                 (TransferStopped("paused"), ABORTED), (PermissionError("denied"), DENIED),
                 (OfficeResponseError("changed", 409), NOT_IN_SYNC), (TimeoutError("slow"), TIMED_OUT)]
        for exc, expected in cases:
            with self.subTest(exception=exc): self.assertEqual(failure_status(exc), expected)

    def test_waiting_requests_report_progress_before_download_workers_are_available(self):
        self.engine.pending_requests[42] = {"size": 12345, "done": 4096, "root": "test", "path": "Project/scene.ma"}
        with patch.object(self.engine.stop_event, "wait", side_effect=[False, True]):
            self.engine._keep_requests_alive()
        self.native.dll.RBProgress.assert_called_once_with(42, 4096, 12345)

    def test_stale_request_is_renewed_at_most_twice_while_office_keeps_changing(self):
        self.store.is_upload_pending = Mock(return_value=False)
        self.native.clean = NativeCloud.clean
        self.native.inspect.return_value = {"modified": 0, "size": len(self.payload), "identity": {"size": len(self.payload)}}
        self.api.remote_item = Mock(return_value={"type": "file", "size": len(self.payload) + 100, "modifiedAt": "new"})
        self.engine._restart_changed(42, self.identity, len(self.payload))
        self.engine._restart_changed(43, self.identity, len(self.payload))
        from cloud_files import CloudError
        with self.assertRaisesRegex(CloudError, "sigue cambiando"):
            self.engine._restart_changed(44, self.identity, len(self.payload))
        self.assertEqual(self.native.restart.call_count, 2)

    def test_stale_request_never_restarts_over_pending_local_work(self):
        from cloud_files import CloudConflict
        self.store.is_upload_pending = Mock(return_value=True)
        with self.assertRaises(CloudConflict):
            self.engine._restart_changed(42, self.identity, len(self.payload))
        self.native.restart.assert_not_called()


if __name__ == "__main__": unittest.main()
