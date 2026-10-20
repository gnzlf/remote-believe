import contextlib
import gc
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import mesh_client as mesh
from windows_session import SavedSession


class ManualSessionTests(unittest.TestCase):
    def tearDown(self):
        gc.collect()

    @contextlib.contextmanager
    def application(self, mode="home", saved=False):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            store = mesh.LocalStore.__new__(mesh.LocalStore)
            store.settings = {"cache_dir": temp, "mode": mode, "username": "worker", "cache_owner": "worker",
                              "auto_upload": False, "remember_session": True}
            store.manifest = {"test::Project/work.ma": {"local": True}}
            store.outbox = {"items": {"pending": {"rootId": "test", "path": "Project/work.ma"}}}
            store.mount_errors, store.cloud_engine = {}, None
            store.save_settings = Mock()
            session = SavedSession(temp)
            if saved:
                session.save("worker", "previous-password", mode)
            stack.enter_context(patch.object(mesh, "DATA_DIR", Path(temp)))
            stack.enter_context(patch.object(mesh, "LocalStore", return_value=store))
            stack.enter_context(patch.object(mesh, "get_or_create_device_id", return_value="test-only"))
            stack.enter_context(patch.object(mesh, "SERVER_TOKENS", {}))
            for name in ("_setup_tray", "_show_window", "_start_command_server", "_connect_explorer",
                         "_dashboard_refresh", "_start_office_server", "_navigate"):
                stack.enter_context(patch.object(mesh.MeshClientApp, name))
            stack.enter_context(patch("cloud_files.NativeCloud"))
            links = stack.enter_context(patch("shell_integration.publish_links"))
            app = mesh.MeshClientApp()
            app.withdraw()
            app.file_api = None
            try:
                yield app, store, session, links
            finally:
                app._closing_app = True
                app.watcher_stop.set()
                if hasattr(app, "progress"):
                    app.progress.stop()
                app.destroy()

    def pump_until(self, app, condition, timeout=3):
        errors = []
        deadline = time.monotonic() + timeout
        def poll():
            try:
                if condition():
                    app.quit()
                    return
                if time.monotonic() >= deadline:
                    raise AssertionError("El cierre de sesion no termino a tiempo")
                app.after(10, poll)
            except Exception as exc:
                errors.append(exc)
                app.quit()
        app.after(10, poll)
        app.mainloop()
        if errors:
            raise errors[0]

    def test_startup_requires_login_even_with_a_saved_session_in_both_modes(self):
        for mode in ("home", "office"):
            with self.subTest(mode=mode), self.application(mode, saved=True) as (app, store, session, links):
                finish = time.monotonic() + 0.4
                self.pump_until(app, lambda: time.monotonic() >= finish)
                self.assertEqual(app.screen, "login")
                self.assertEqual(app.pw_var.get(), "")
                self.assertFalse(store.settings["remember_session"])
                self.assertIsNone(session.load())
                app._start_office_server.assert_not_called()
                app._connect_explorer.assert_not_called()

    def test_home_logout_cancels_old_work_and_preserves_local_work(self):
        with self.application() as (app, store, session, links):
            root = {"id": "test", "name": "X: Proyectos", "driveLetter": "X", "access": ["Project"]}
            engine = SimpleNamespace(stop_event=threading.Event(), close=Mock(), connections={"test": object()})
            app.cloud_engine = store.cloud_engine = engine
            app.file_api = mesh.OfficeFileApi("127.0.0.1")
            api = app.file_api
            api.token, api._login_secret = "test-token", ("worker", "test-password")
            api.opener = Mock()
            app.command_server = SimpleNamespace(stop=Mock())
            commands = app.command_server
            app._build_main([root], {"roots": [root], "role": "user"})
            app.pw_var.set("must be cleared")
            app.pending_changes["work"] = "local edit"
            manifest, outbox = dict(store.manifest), dict(store.outbox)
            ready, release = threading.Event(), threading.Event()
            stale_result = Mock()
            def old_work():
                ready.set()
                release.wait(2)
                app.post(stale_result)
            old_thread = app._start_session_thread(old_work)
            self.assertTrue(ready.wait(1))
            self.assertEqual(app.logout_btn.cget("text"), "Cerrar sesión")
            app.logout_btn.invoke()
            app.after(30, release.set)
            self.pump_until(app, lambda: app.screen == "login")
            self.assertFalse(old_thread.is_alive())
            stale_result.assert_not_called()
            self.assertIsNone(api.token)
            self.assertIsNone(api._login_secret)
            self.assertTrue(engine.stop_event.is_set())
            engine.close.assert_called_once()
            commands.stop.assert_called_once()
            links.assert_called_once()
            self.assertEqual(store.manifest, manifest)
            self.assertEqual(store.outbox, outbox)
            self.assertEqual(app.pw_var.get(), "")
            self.assertIsNone(app.file_api)
            self.assertIsNone(app.cloud_engine)
            self.assertEqual(app.roots, [])
            self.assertIsNone(app.connection_timer_id)
            self.assertFalse(app.watcher_stop.is_set())
            app._worker = lambda callback: callback()
            app._build_main([root], {"roots": [root], "role": "user"})
            self.assertEqual(app.screen, "main")

    def test_office_has_logout_and_stops_its_server(self):
        with self.application("office") as (app, store, session, links):
            root = {"id": "test", "name": "X: Proyectos", "driveLetter": "X", "access": ["Project"]}
            app.office_server = SimpleNamespace(stop=Mock())
            server = app.office_server
            mesh.SERVER_TOKENS["test-session"] = {}
            app._build_main([root], {"roots": [root], "role": "user"})
            self.assertEqual(app.logout_btn.cget("text"), "Cerrar sesión")
            app.logout_btn.invoke()
            self.pump_until(app, lambda: app.screen == "login")
            server.stop.assert_called_once()
            self.assertIsNone(app.office_server)
            self.assertEqual(mesh.SERVER_TOKENS, {})

    def test_account_without_authorized_roots_detaches_previous_mounts(self):
        with self.application() as (app, store, session, links):
            with patch.object(store, "detach_account_mounts") as detach:
                app._build_main([], {"roots": [], "role": "user"})
                detach.assert_called_once()
                app._connect_explorer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
