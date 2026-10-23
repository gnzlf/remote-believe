import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DiagnosticTests(unittest.TestCase):
    def test_local_logs_capture_worker_and_interface_failures_without_closing_the_app(self):
        client = Path(__file__).resolve().parents[1] / "client"
        with tempfile.TemporaryDirectory() as temp:
            code = """import diagnostics, logging, threading
diagnostics.configure('3.1.1-test')
def fail(): raise RuntimeError('test worker failure')
thread = threading.Thread(target=fail)
thread.start()
thread.join()
from mesh_client import MeshClientApp
from types import SimpleNamespace
app = SimpleNamespace(set_status=lambda text: None)
try: raise ValueError('test interface failure')
except Exception:
    import sys
    MeshClientApp.report_callback_exception(app, *sys.exc_info())
print('still running')
"""
            env = dict(os.environ, APPDATA=temp, PYTHONPATH=str(client))
            result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("still running", result.stdout)
            folder = Path(temp) / "RemoteBelieveMesh/logs"
            log = (folder / "RemoteBelieve.log").read_text(encoding="utf-8")
            self.assertIn("3.1.1-test", log)
            self.assertIn("test worker failure", log)
            self.assertIn("test interface failure", log)
            self.assertTrue((folder / "native-faults.log").exists())
