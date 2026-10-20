"""Local removals cancel remembered downloads; they never delete Office files."""
from pathlib import Path
import threading
import time
from cache_paths import local_exists


def under(path, parent):
    path, parent = path.replace("\\", "/").strip("/").casefold(), parent.replace("\\", "/").strip("/").casefold()
    return not parent or path == parent or path.startswith(parent + "/")


class LocalDeletionPolicy:
    def _deletion_lock(self):
        return self.__dict__.setdefault("_local_deletion_lock", threading.RLock())

    def is_local_deleted(self, root_id, remote_path):
        """Suppress automatic downloads; this does not hide the remote catalog."""
        return any(record.get("rootId") == root_id and under(remote_path, record.get("remotePath", ""))
                   for record in tuple(self.settings.get("local_deleted", [])))

    def record_local_deletion(self, root_id, remote_path):
        remote_path = remote_path.replace("\\", "/").strip("/")
        if ".." in remote_path.split("/"):
            raise ValueError("Ruta local eliminada invalida")
        with self._deletion_lock():
            if self.is_local_deleted(root_id, remote_path):
                return False
            kept = [record for record in self.settings.get("local_deleted", [])
                    if record.get("rootId") != root_id or not under(record.get("remotePath", ""), remote_path)]
            kept.append({"rootId": root_id, "remotePath": remote_path})
            self.settings["local_deleted"] = kept
            # Removing a child overrides its parent's old "keep offline" instruction.
            self.settings["offline_folders"] = [record for record in self.settings.get("offline_folders", [])
                if record.get("rootId") != root_id or not (under(remote_path, record.get("remotePath", ""))
                    or under(record.get("remotePath", ""), remote_path))]
            self.settings["cloud_populated_folders"] = [record for record in self.settings.get("cloud_populated_folders", [])
                if record.get("root") != root_id or not under(record.get("path", ""), remote_path)]
            outbox_changed = False
            for key, entry in list(self.outbox.get("items", {}).items()):
                if entry.get("rootId") == root_id and under(entry.get("remotePath", ""), remote_path):
                    if not entry.get("localPath") or local_exists(entry["localPath"]) is False:
                        self.outbox["items"].pop(key, None)
                        outbox_changed = True
            self.save_settings()
            if outbox_changed:
                self.save_outbox()
            return True

    def restore_local_downloads(self, root_id, remote_path=""):
        """Only a user action clears a removal. Offline pins are not restored."""
        with self._deletion_lock():
            records = self.settings.get("local_deleted", [])
            removed = [record for record in records if record.get("rootId") == root_id
                       and (under(remote_path, record.get("remotePath", "")) or under(record.get("remotePath", ""), remote_path))]
            if not removed:
                return False
            self.settings["local_deleted"] = [record for record in records if record not in removed]
            # Discard missing-file history, so a refresh cannot treat this recovery as a new deletion.
            prefix = root_id + "::"
            for key, entry in list(self.manifest.items()):
                if key.startswith(prefix) and any(under(key[len(prefix):], record.get("remotePath", "")) for record in removed):
                    if not entry.get("localPath") or local_exists(entry["localPath"]) is False:
                        self.manifest.pop(key, None)
            self.save_settings()
            self.save_manifest()
            return True

    def reconcile_local_deletions(self, roots, live=False):
        """Run before any mount/population can recreate a removed directory."""
        changed = False
        for root in roots:
            root_id, prefix = root["id"], root["id"] + "::"
            known = [key[len(prefix):] for key in tuple(self.manifest) if key.startswith(prefix)]
            known += [record.get("path", "") for record in tuple(self.settings.get("cloud_populated_folders", [])) if record.get("root") == root_id]
            known += [record.get("remotePath", "") for record in tuple(self.settings.get("offline_folders", [])) if record.get("rootId") == root_id]
            if known and local_exists(self.root_dir(root)) is False:
                changed = self.record_local_deletion(root_id, "") or changed
                continue
            for rp in sorted(set(known), key=lambda value: value.count("/")):
                if self.is_local_deleted(root_id, rp):
                    continue
                path = self.local_path(root, rp)
                candidates = self.__dict__.setdefault("_local_missing_candidates", {})
                candidate = (str(self.cache_dir), root_id, rp)
                exists = local_exists(path)
                if exists is None:
                    continue  # Keep pins/pending work until Windows can confirm the file's state.
                if exists:
                    candidates.pop(candidate, None)
                    continue
                entry = self.manifest.get(self.key(root_id, rp), {})
                # Give atomic save/replace operations time to put a file back.
                if live and entry and not entry.get("cloudDirectory"):
                    first_missing = candidates.setdefault(candidate, time.monotonic())
                    if time.monotonic() - first_missing < 2:
                        continue
                changed = self.record_local_deletion(root_id, rp) or changed
        return changed
