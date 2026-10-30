"""Keep each Believe account's cache and pending work separate in one Windows profile."""
import copy
import hashlib
from pathlib import Path


PREFERENCES = {"auto_upload": True, "asset_auto_update": True, "offline_folders": [], "cloud_populated_folders": [], "local_deleted": [],
               "transfer_paused": {}, "cancelled_uploads": {}}


class AccountProfiles:
    def __init__(self, data_dir, load_json, save_json):
        self.directory = Path(data_dir) / "accounts"
        self.load, self.save = load_json, save_json

    @staticmethod
    def identity(username):
        return hashlib.sha256(username.strip().encode("utf-8")).hexdigest()

    def _paths(self, identity):
        if len(identity) != 64 or any(char not in "0123456789abcdef" for char in identity):
            raise ValueError("Perfil local de Believe invalido")
        directory = self.directory / identity
        return directory / "manifest.json", directory / "outbox.json", directory / "preferences.json"

    def save_preferences(self, store):
        identity = store.settings.get("active_account")
        if identity in store.settings.get("account_profiles", {}):
            self.save(self._paths(identity)[2], {key: store.settings.get(key, copy.deepcopy(value))
                                               for key, value in PREFERENCES.items()})

    def _bind(self, store, identity, load=True):
        record = store.settings["account_profiles"][identity]
        store._manifest_path, store._outbox_path, preferences_path = self._paths(identity)
        store.settings.update(active_account=identity, cache_dir=record["cache_dir"], cache_owner=record["username"])
        if load:
            store.manifest = self.load(store._manifest_path, {})
            store.outbox = self.load(store._outbox_path, {"version": 1, "items": {}})
            if not isinstance(store.manifest, dict):
                store.manifest = {}
            if not isinstance(store.outbox, dict) or not isinstance(store.outbox.get("items"), dict):
                store.outbox = {"version": 1, "items": {}}
            preferences = self.load(preferences_path, {})
            for key, default in PREFERENCES.items():
                store.settings[key] = preferences.get(key, copy.deepcopy(default))

    def restore(self, store):
        identity = store.settings.get("active_account")
        if identity in store.settings.get("account_profiles", {}):
            self._bind(store, identity)

    def _migrate_legacy(self, store, username):
        cache = store.cache_dir.resolve()
        owner = str(store.settings.get("cache_owner") or "").strip()
        previous_login = str(store.settings.get("username") or "").strip()
        if owner and previous_login.casefold() == owner.casefold():
            owner = previous_login
        if not owner and store.settings.get("mode") == "home":
            owner = previous_login
        has_work = bool(store.manifest or store.outbox.get("items"))
        if not owner and not has_work and (not cache.exists() or not any(cache.iterdir())):
            owner = username.strip()
        # Unidentified legacy files remain preserved rather than belonging to a new login.
        identity = self.identity(owner) if owner else hashlib.sha256(b"unassigned-legacy-cache").hexdigest()
        store.settings["accounts_cache_dir"] = str(cache.with_name(cache.name + ".accounts"))
        store.settings["account_profiles"] = {identity: {"username": owner, "cache_dir": str(cache), "legacy": True}}
        self._bind(store, identity, load=False)
        self.save(store._manifest_path, store.manifest)
        self.save(store._outbox_path, store.outbox)
        self.save_preferences(store)

    def activate(self, store, username):
        if getattr(store, "cloud_engine", None):
            raise RuntimeError("Cierra la sesion actual antes de cambiar de usuario.")
        if not store.settings.get("account_profiles"):
            self._migrate_legacy(store, username)
        store.save_manifest()
        store.save_outbox()
        self.save_preferences(store)
        identity = self.identity(username)
        profiles = store.settings["account_profiles"]
        if identity not in profiles:
            profiles[identity] = {"username": username.strip(),
                                  "cache_dir": str(Path(store.settings["accounts_cache_dir"]) / identity)}
        self._bind(store, identity)
        store.settings["username"] = username.strip()
        store.local_drives, store.shared_drives, store.mount_errors = {}, {}, {}
        store.save_settings()
