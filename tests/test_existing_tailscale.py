import contextlib
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "client"))
import mesh_client as mesh

spec = importlib.util.spec_from_file_location("believe_installer", REPO / "installer" / "remote_believe_installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ExistingTailscaleTests(unittest.TestCase):
    def test_installer_installs_believe_without_a_tailscale_payload_or_setup(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            folder = Path(temp)
            payload = folder / "RemoteBelieve.exe"
            payload.write_bytes(b"test application")
            destination = folder / "installed"
            stack.enter_context(patch.object(installer, "INSTALL_DIR", destination))
            stack.enter_context(patch.object(installer, "START_MENU_DIR", folder / "menu"))
            stack.enter_context(patch.object(installer, "payload_app_exe", return_value=payload))
            for name in ("stop_running_app", "register_startup", "install_explorer_menu", "register_namespace",
                         "create_shortcut", "register_uninstall"):
                stack.enter_context(patch.object(installer, name))
            stack.enter_context(patch.object(installer.messagebox, "showinfo"))
            run = stack.enter_context(patch.object(installer.subprocess, "run"))
            launch = stack.enter_context(patch.object(installer.subprocess, "Popen"))
            stack.enter_context(patch.object(installer.shutil, "which", return_value=None))
            installer.install()
            self.assertEqual((destination / "RemoteBelieve.exe").read_bytes(), payload.read_bytes())
            run.assert_not_called()
            launch.assert_called_once_with([str(destination / "RemoteBelieve.exe")], cwd=str(destination))

    def test_home_login_uses_the_existing_connection_without_tailscale_authentication(self):
        def variable(value):
            return SimpleNamespace(get=lambda: value, set=Mock())

        store = SimpleNamespace(settings={"office_ip": "100.64.0.1"}, save_settings=Mock())
        app = SimpleNamespace(
            username_var=variable("freelance"), pw_var=variable("test-password"), mode_var=variable("home"),
            office_ip_var=variable("100.64.0.1"), manual_ip_var=variable(False), screen="login", busy=False,
            store=store, post=lambda fn, *args: fn(*args), set_status=Mock(),
            _worker=lambda callback: callback(), _prepare_session=Mock(), _login_complete=Mock(),
        )
        access = {"roots": [{"id": "X", "access": ["Project"]}]}
        with patch.object(mesh, "discover_office_ip", return_value="100.64.0.1"), \
             patch.object(mesh, "tailscale_peer_name_for_ip", return_value="Oficina"), \
             patch.object(mesh, "OfficeFileApi") as api, \
             patch.object(mesh.subprocess, "run") as run:
            api.return_value.login.return_value = {"access": access}
            mesh.MeshClientApp._login(app)
            api.return_value.login.assert_called_once_with("freelance", "test-password")
            app._login_complete.assert_called_once_with(access["roots"], access)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
