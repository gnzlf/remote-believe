import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
from asset_updates import AssetCatalog, AssetUpdateTracker, master_kind, scan_project
from cloud_files import IN_SYNC, PARTIAL, PLACEHOLDER
from transfer_control import TransferControl, TransferStopped
import mesh_client as mesh


RP = "Project/03_Production/Assets/01_Characters/Hero/Export/Rigging/master/Hero_Rigging_master.mb"


def publish(project, category="01_Characters", product="Rigging", version="v0009", age=120):
    folder = project / "03_Production" / "Assets" / category / "Hero" / "Export" / product / "master"
    folder.mkdir(parents=True, exist_ok=True)
    scene = folder / ("Hero_" + product + "_master.mb")
    scene.write_bytes(b"scene content never read by scanner")
    info = folder / "versioninfo.json"
    info.write_text(json.dumps({"version": version, "sourceScene": "private/working/path", "user": "private-author"}), encoding="utf-8")
    import time
    for file in (scene, info):
        os.utime(file, (time.time() - age, time.time() - age))
    return scene


class AssetScannerTests(unittest.TestCase):
    def test_detects_published_rigs_and_environment_products_without_following_scenes(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "Project"
            rig = publish(project)
            publish(project, "04_Environment", "Animacion", "v0002")
            publish(project, "04_Environment", "Modeling", "v0003")
            publish(project, "02_Props", "Modeling")
            working = rig.parents[1] / "v0010"
            working.mkdir()
            (working / "versioninfo.json").write_text('{"version":"v0010"}')
            original = Path.read_text
            reads = []
            def read(path, *args, **kwargs):
                reads.append(path.name)
                return original(path, *args, **kwargs)
            with patch.object(Path, "read_text", read), patch.object(Path, "read_bytes", side_effect=AssertionError("Scene bytes read")):
                records = scan_project(project, "test", mesh.format_remote_modified_at)
            self.assertEqual(len(records), 3)
            self.assertEqual({r["kind"] for r in records}, {"Rig", "Environment"})
            self.assertEqual(next(r for r in records if r["kind"] == "Rig")["version"], "v0009")
            self.assertTrue(all(name == "versioninfo.json" for name in reads))
            self.assertNotIn("private-author", json.dumps(records))
            self.assertNotIn("private/working/path", json.dumps(records))

    def test_incomplete_fresh_and_malformed_publications_are_not_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "Project"
            scene = publish(project, age=0)
            self.assertEqual(scan_project(project, "test", mesh.format_remote_modified_at), [])
            scene = publish(project)
            (scene.parent / "versioninfo.json").write_text("{half written", encoding="utf-8")
            os.utime(scene.parent / "versioninfo.json", (1, 1))
            self.assertEqual(scan_project(project, "test", mesh.format_remote_modified_at), [])

    def test_republish_same_version_changes_revision_and_waits_for_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "Project"
            scene = publish(project)
            clock = Mock(return_value=0)
            catalog = AssetCatalog(ttl=1, clock=clock)
            first = catalog.get(project, "test", mesh.format_remote_modified_at)
            scene.write_bytes(b"different published scene")
            os.utime(scene, (1, 1))
            clock.return_value = 10
            self.assertEqual(catalog.get(project, "test", mesh.format_remote_modified_at), first)
            clock.return_value = 41
            second = catalog.get(project, "test", mesh.format_remote_modified_at)
            self.assertNotEqual(first[0]["revision"], second[0]["revision"])
            self.assertEqual(second[0]["version"], "v0009")

    def test_master_path_classifier_excludes_textures_shots_and_working_versions(self):
        self.assertEqual(master_kind(RP), "Rig")
        self.assertEqual(master_kind(RP.replace("01_Characters", "04_Environment").replace("Rigging", "Animacion")), "Environment")
        for path in (RP.replace("master/", "v0010/"), RP.replace("Assets", "Shots"), RP.replace(".mb", ".jpg"), RP.replace("Rigging", "Modeling")):
            self.assertIsNone(master_kind(path), path)

    def test_scanner_does_not_follow_a_master_junction_outside_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "Project"
            scene = publish(project)
            outside = Path(temp) / "Outside"
            outside.mkdir()
            from subprocess import run
            product = scene.parent.parent
            link = product.parent / "Rig"
            if sys.platform == "win32":
                result = run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
                self.assertEqual(result.returncode, 0)
                self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
                try:
                    self.assertEqual(len(scan_project(project, "test", mesh.format_remote_modified_at)), 1)
                finally:
                    os.rmdir(link)


class AssetTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "master.mb"
        self.path.write_bytes(b"existing master")
        self.root = {"id": "test"}
        self.store = SimpleNamespace(manifest={}, key=lambda root, rp: root + "::" + rp,
            local_path=lambda root, rp: self.path, is_upload_pending=Mock(return_value=False), is_local_deleted=Mock(return_value=False), save_manifest=Mock())
        self.native = SimpleNamespace(state=Mock(return_value=PLACEHOLDER | IN_SYNC),
            inspect=Mock(return_value={"identity": {"size": 15, "modifiedAt": "old"}, "modified": 0}),
            clean=Mock(return_value=True))
        self.engine = SimpleNamespace(roots={"test": self.root}, allowed=Mock(return_value=True), native=self.native,
            hydrate_asset=Mock(), controls=TransferControl(), stop_event=threading.Event(), lock=threading.RLock())
        self.record = {"root": "test", "path": RP, "project": "Project", "kind": "Rig", "asset": "Hero",
            "product": "Rigging", "version": "v0010", "revision": "revision10",
            "item": {"size": 20, "modifiedAt": "new"}}
        self.tracker = AssetUpdateTracker(self.store, self.engine, {}, Mock())
        self.key = "test::" + RP

    def test_only_consumed_masters_are_automatic_and_notifications_survive_restart(self):
        self.assertEqual(len(self.tracker.observe([self.record])), 1)
        self.assertTrue(self.tracker.rows[0]["automatic"])
        restarted = AssetUpdateTracker(self.store, self.engine, copy.deepcopy(self.tracker.state))
        self.assertEqual(restarted.observe([self.record]), [])
        self.native.state.return_value |= PARTIAL
        self.tracker.observe([self.record])
        self.assertEqual(self.tracker.rows, [])

    def test_metadata_refresh_remembers_consumption_but_freeing_space_is_not_download_intent(self):
        self.native.state.return_value |= PARTIAL
        self.store.manifest[self.key] = {"assetDownloaded": True}
        self.tracker.observe([self.record])
        self.assertEqual(self.tracker.rows, [])
        self.store.manifest[self.key]["assetUpdatePending"] = self.record["item"]
        self.tracker.observe([self.record])
        self.assertTrue(self.tracker.rows[0]["automatic"])
        self.store.is_local_deleted.return_value = True
        self.tracker.observe([self.record])
        self.assertEqual(self.tracker.rows, [])

    def test_local_edits_pending_uploads_and_revoked_projects_are_protected(self):
        self.native.clean.return_value = False
        self.tracker.observe([self.record])
        self.assertFalse(self.tracker.rows[0]["automatic"])
        self.assertIn("Cambios locales", self.tracker.rows[0]["status"])
        self.native.clean.return_value = True
        self.store.is_upload_pending.return_value = True
        self.tracker.observe([self.record])
        self.assertFalse(self.tracker.rows[0]["automatic"])
        self.engine.allowed.return_value = False
        self.assertEqual(self.tracker.observe([self.record]), [])
        self.assertEqual(self.tracker.rows, [])

    def test_cancelling_stays_cancelled_for_revision_but_manual_retry_is_allowed(self):
        self.tracker.observe([self.record])
        self.engine.hydrate_asset.side_effect = TransferStopped("cancelled")
        with self.assertRaises(TransferStopped):
            self.tracker.update(self.key)
        self.tracker.observe([self.record])
        self.assertFalse(self.tracker.rows[0]["automatic"])
        self.engine.hydrate_asset.side_effect = None
        self.tracker.update(self.key, manual=True)
        self.engine.hydrate_asset.assert_called_with(self.record, automatic=False)
        self.assertNotIn("cancelled", self.tracker.state[self.key])

    def test_pausing_keeps_revision_queued_for_resume(self):
        self.tracker.observe([self.record])
        self.engine.controls.pause("download")
        self.engine.hydrate_asset.side_effect = TransferStopped("paused")
        with self.assertRaises(TransferStopped):
            self.tracker.update(self.key)
        self.assertNotIn("cancelled", self.tracker.state[self.key])
        self.engine.controls.resume("download")
        self.tracker.observe([self.record])
        self.assertTrue(self.tracker.rows[0]["automatic"])

    def test_current_master_is_not_downloaded_again(self):
        self.native.inspect.return_value["identity"] = dict(self.record["item"])
        self.assertEqual(self.tracker.observe([self.record]), [])
        self.assertEqual(self.tracker.rows, [])
        self.assertEqual(self.tracker.state[self.key]["localVersion"], "v0010")

    def test_unopened_proxy_and_missing_or_deleted_master_have_no_rows_or_notifications(self):
        for situation in ("proxy", "missing", "deleted"):
            with self.subTest(situation=situation):
                if situation == "proxy":
                    self.native.state.return_value |= PARTIAL
                elif situation == "missing":
                    self.path.unlink()
                else:
                    self.path.write_bytes(b"local copy")
                    self.native.state.return_value = PLACEHOLDER | IN_SYNC
                    self.store.is_local_deleted.return_value = True
                self.assertEqual(self.tracker.observe([self.record]), [])
                self.assertEqual(self.tracker.rows, [])
                self.assertEqual(self.tracker.records, {})
                self.assertNotIn("notified", self.tracker.state[self.key])

    def test_unchanged_local_edits_do_not_create_a_remote_update(self):
        self.native.inspect.return_value["identity"] = dict(self.record["item"])
        self.native.clean.return_value = False
        self.assertEqual(self.tracker.observe([self.record]), [])
        self.assertEqual(self.tracker.rows, [])

    def test_new_published_version_is_pending_even_when_file_keeps_size_and_date(self):
        self.native.inspect.return_value["identity"] = dict(self.record["item"])
        self.tracker.observe([self.record])
        self.record.update(version="v0011", revision="revision11")
        self.assertEqual(len(self.tracker.observe([self.record])), 1)
        self.assertFalse(self.tracker.rows[0]["current"])
        self.assertTrue(self.tracker.rows[0]["automatic"])

    def test_polling_notifies_once_and_downloads_only_consumed_masters(self):
        from asset_panel import AssetUpdatesPanel
        unused = {**self.record, "path": RP.replace("Hero", "Unused"), "asset": "Unused"}
        missing = Path(self.temp.name) / "unused.mb"
        self.store.local_path = lambda root, rp: missing if "Unused" in rp else self.path
        self.store.settings = {"asset_auto_update": True}
        def hydrate(record, automatic=False):
            self.native.inspect.return_value["identity"] = dict(record["item"])
        self.engine.hydrate_asset.side_effect = hydrate
        app = SimpleNamespace(cloud_engine=self.engine, screen="main", explorer_dashboard=True,
            roots=[{"id": "test", "access": ["Project"], "projectScoped": True}], store=self.store,
            file_api=SimpleNamespace(asset_updates=Mock(return_value=[self.record, unused])),
            asset_status_var=Mock(), _render_asset_updates=Mock(), _notify=Mock(), _log_activity=Mock(),
            _asset_state={}, _save_asset_state=Mock(), post=lambda cb, *args: cb(*args))
        def start(work):
            work()
            return object()
        app._start_session_thread = start
        AssetUpdatesPanel._asset_check(app, force=True)
        self.engine.hydrate_asset.assert_called_once_with(self.record, automatic=True)
        app._notify.assert_called_once()
        self.assertNotIn("Unused", app._notify.call_args.args[1])
        self.assertNotIn("2 masters", app.asset_status_var.set.call_args.args[0])
        AssetUpdatesPanel._asset_check(app, force=True)
        app._notify.assert_called_once()
        self.engine.hydrate_asset.assert_called_once()
        app.roots[0]["access"] = []
        AssetUpdatesPanel._asset_check(app, force=True)
        self.assertEqual(app.asset_tracker.rows, [])

    def test_unused_publication_gets_no_notification_even_after_legacy_broad_catalog(self):
        self.native.state.return_value |= PARTIAL
        self.tracker.state[self.key] = {"notified": "older-3.2.0-catalog-entry"}
        self.assertEqual(self.tracker.observe([self.record]), [])
        self.assertEqual(self.tracker.rows, [])
        self.assertEqual(self.tracker.state[self.key]["notified"], "older-3.2.0-catalog-entry")


if __name__ == "__main__":
    unittest.main()
