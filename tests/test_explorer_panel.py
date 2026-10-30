import contextlib
import gc
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import mesh_client as mesh
from windows_session import SavedSession


class PanelTests(unittest.TestCase):
    def tearDown(self):
        # Finalize destroyed Tk objects on the UI thread before HTTP tests start.
        gc.collect()

    def test_home_panel_has_no_file_browser_and_keeps_admin_and_upload_controls(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            store = mesh.LocalStore.__new__(mesh.LocalStore)
            store.settings = {"cache_dir": temp, "mode": "home", "username": "test", "auto_upload": True}
            store.manifest, store.outbox, store.mount_errors = {}, {"items": {}}, {}
            store.save_settings = Mock()
            stack.enter_context(patch.object(mesh, "LocalStore", return_value=store))
            stack.enter_context(patch.object(mesh, "get_or_create_device_id", return_value="test"))
            for name in ("_setup_tray", "_discard_saved_login", "_start_command_server", "_connect_explorer", "_schedule_connection_check", "_start_watcher"):
                stack.enter_context(patch.object(mesh.MeshClientApp, name))
            app = mesh.MeshClientApp()
            app.withdraw()
            app.file_api = SimpleNamespace(office_url="http://127.0.0.1:8090")
            app._worker = lambda callback: callback()
            try:
                root = {"id": "test", "name": "X: Proyectos", "driveLetter": "X", "projectScoped": True, "access": ["Project"]}
                app._build_main([root], {"roots": [root], "role": "superuser"})
                app.update_idletasks()
                widgets = []
                queue = list(app.winfo_children())
                while queue:
                    widget = queue.pop()
                    widgets.append(widget)
                    queue.extend(widget.winfo_children())
                self.assertFalse(hasattr(app, "file_tree"))  # Tables are transfer/history controls, not a file browser.
                labels = [str(widget.cget("text")) for widget in widgets if "text" in widget.keys()]
                self.assertIn("Abrir mis carpetas", labels)
                self.assertIn("Administracion", labels)
                self.assertIn("Subir pendientes", labels)
                self.assertIn("Subir archivos nuevos o modificados", labels)
                self.assertIn("Pausar subidas", labels)
                self.assertIn("Pausar descargas", labels)
                self.assertIn("Cancelar seleccionada", labels)
                self.assertIn("Cerrar sesión", labels)
                self.assertIn("Actualizar conexion y permisos", labels)
                self.assertNotIn("Volver a mostrar eliminados", labels)
                self.assertTrue(app.explorer_dashboard)
                self.assertEqual([app.asset_notebook.tab(tab, "text") for tab in app.asset_notebook.tabs()], ["Transferencias", "Updates"])
                app.asset_notebook.select(app.asset_tab)
                app.asset_tracker = SimpleNamespace(rows=[{"key": "test::master", "project": "Project", "kind": "Rig", "asset": "Hero",
                    "product": "Rigging", "version": "v0010", "localVersion": "v0009", "status": "Pendiente de actualizar", "current": False}])
                app._render_asset_updates()
                self.assertEqual(app.asset_notebook.tab(app.asset_tab, "text"), "Updates (1)")
                self.assertEqual(len(app.asset_tree.get_children()), 1)
                self.assertEqual(app.asset_path_var.get(), "")
                self.assertIn("Actualizar seleccionados", labels)
                self.assertIn("Buscar updates", labels)
                app.asset_auto_var.set(False)
                app._asset_toggle_auto()
                self.assertFalse(store.settings["asset_auto_update"])
                app._log_activity("Subida", "upload only", "up.ma", "success")
                app._log_activity("Descarga", "download only", "down.ma", "success")
                app.history_filter.set("Subidas")
                app._refresh_activity_panel()
                table = next(widget for widget in app.activity_list.winfo_children() if isinstance(widget, mesh.ttk.Treeview))
                self.assertEqual(len(table.get_children()), 1)
                self.assertIn("upload only", table.item(table.get_children()[0], "values"))
                app.history_filter.set("Descargas")
                app._refresh_activity_panel()
                table = next(widget for widget in app.activity_list.winfo_children() if isinstance(widget, mesh.ttk.Treeview))
                self.assertEqual(len(table.get_children()), 1)
                self.assertIn("download only", table.item(table.get_children()[0], "values"))
                app.au_var.set(False)
                app._toggle_au()
                self.assertFalse(store.settings["auto_upload"])
            finally:
                app.destroy()

    def test_saved_credentials_are_encrypted_and_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as temp:
            saved = SavedSession(temp)
            saved.save("test@example.invalid", "test-secret-not-a-real-password", "home")
            self.assertNotIn(b"test-secret-not-a-real-password", saved.path.read_bytes())
            self.assertEqual(saved.load()["password"], "test-secret-not-a-real-password")
            saved.clear()
            self.assertIsNone(saved.load())

    def test_upload_does_not_commit_if_file_changes_during_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scene.ma"
            path.write_bytes(b"initial file")
            api = mesh.OfficeFileApi("127.0.0.1")
            api.remote_item = Mock(return_value=None)
            api.upload_chunk = Mock(side_effect=lambda *args: path.write_bytes(b"changed while uploading"))
            api.complete_upload = Mock()
            with self.assertRaisesRegex(mesh.RBError, "cambio mientras"):
                api.upload_file("test", "Project/shots/shot010/animation", path, lambda *args: None)
            api.complete_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
