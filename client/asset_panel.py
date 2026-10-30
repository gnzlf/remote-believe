"""Freelancer asset updates tab; all filesystem/network work runs off Tk."""
import threading
import time
import tkinter as tk
from tkinter import ttk

from asset_updates import AssetUpdateTracker
from transfer_control import TransferStopped


class AssetUpdatesPanel:
    def _install_asset_updates(self, parent):
        self.asset_tab = ttk.Frame(parent, padding=14)
        parent.add(self.asset_tab, text="Updates")
        self.asset_notebook = parent
        ttk.Label(self.asset_tab, text="Rigs y environments publicados", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        ttk.Label(self.asset_tab, text="Se actualizan solo los masters ya descargados, en su misma ruta X:/R:.\n"
                  "Los demas se descargan cuando los eliges. Los cambios locales se conservan.").pack(anchor="w", pady=(4, 10))
        self.asset_auto_var = tk.BooleanVar(value=self.store.settings.get("asset_auto_update", True))
        ttk.Checkbutton(self.asset_tab, text="Actualizar automaticamente mis masters descargados",
                        variable=self.asset_auto_var, command=self._asset_toggle_auto).pack(anchor="w", pady=(0, 10))
        actions = ttk.Frame(self.asset_tab)
        actions.pack(fill="x")
        ttk.Button(actions, text="Buscar updates", command=lambda: self._asset_check(force=True)).pack(side="left")
        ttk.Button(actions, text="Actualizar seleccionados", command=self._asset_update_selected).pack(side="left", padx=6)
        ttk.Button(actions, text="Actualizar todos los pendientes", command=lambda: self._asset_update_selected(all_pending=True)).pack(side="left")
        ttk.Button(actions, text="Pausar / reanudar descargas", command=lambda: self._toggle_transfers("download")).pack(side="left", padx=6)
        self.asset_filter = tk.StringVar(value="Pendientes")
        select = ttk.Combobox(actions, textvariable=self.asset_filter, values=("Pendientes", "Todos"), state="readonly", width=12)
        select.pack(side="right")
        select.bind("<<ComboboxSelected>>", lambda event: self._render_asset_updates())
        table_frame = ttk.Frame(self.asset_tab)
        table_frame.pack(fill="both", expand=True, pady=12)
        columns = ("project", "kind", "asset", "product", "localVersion", "version", "status")
        self.asset_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")
        for column, label, width in zip(columns, ("Proyecto", "Tipo", "Asset", "Producto", "Local", "Publicada", "Estado"),
                                        (170, 95, 150, 100, 65, 75, 220)):
            self.asset_tree.heading(column, text=label)
            self.asset_tree.column(column, width=width, minwidth=50, stretch=True)
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=self.asset_tree.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.asset_tree.xview)
        self.asset_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        horizontal.pack(side="bottom", fill="x")
        vertical.pack(side="right", fill="y")
        self.asset_tree.pack(fill="both", expand=True)
        self.asset_path_var = tk.StringVar()
        ttk.Entry(self.asset_tab, textvariable=self.asset_path_var, state="readonly").pack(fill="x")
        self.asset_tree.bind("<<TreeviewSelect>>", self._asset_selected)
        self.asset_status_var = tk.StringVar(value="Buscando masters publicados en tus proyectos...")
        ttk.Label(self.asset_tab, textvariable=self.asset_status_var, wraplength=920).pack(anchor="w", pady=(8, 0))
        self._render_asset_updates()

    def _asset_toggle_auto(self):
        self.store.settings["asset_auto_update"] = self.asset_auto_var.get()
        self.store.save_settings()
        if self.asset_auto_var.get():
            self._asset_check(force=True)

    def _asset_selected(self, event=None):
        selected = self.asset_tree.selection()
        record = getattr(self, "asset_tracker", None)
        record = record.records.get(selected[0]) if record and selected else None
        if record:
            root = next((r for r in self.roots if r["id"] == record["root"]), None)
            if root:
                self.asset_path_var.set(str(self.store.explorer_path(root, record["path"])))

    def _render_asset_updates(self):
        if self.screen != "main" or not self.explorer_dashboard or not hasattr(self, "asset_tree"):
            return
        tracker = getattr(self, "asset_tracker", None)
        rows = tracker.rows if tracker else []
        selected = self.asset_tree.selection()
        self.asset_tree.delete(*self.asset_tree.get_children())
        pending = sum(not row["current"] for row in rows)
        for row in rows:
            if self.asset_filter.get() == "Pendientes" and row["current"]:
                continue
            self.asset_tree.insert("", "end", iid=row["key"], values=tuple(row[c] for c in
                ("project", "kind", "asset", "product", "localVersion", "version", "status")))
        self.asset_tree.selection_set([key for key in selected if self.asset_tree.exists(key)])
        self.asset_notebook.tab(self.asset_tab, text="Updates ({})".format(pending) if pending else "Updates")

    def _asset_update_selected(self, all_pending=False):
        tracker = getattr(self, "asset_tracker", None)
        keys = [row["key"] for row in tracker.rows if not row["current"]] if tracker and all_pending else list(self.asset_tree.selection())
        if not keys:
            self.asset_status_var.set("Selecciona un rig o environment pendiente de descargar.")
            return
        self._asset_check(force=True, manual_keys=keys)

    def _asset_check(self, force=False, manual_keys=None):
        engine = self.cloud_engine
        if self.screen != "main" or not self.explorer_dashboard or not engine or engine.stop_event.is_set():
            return
        lock = self.__dict__.setdefault("_asset_check_lock", threading.Lock())
        if not force and time.monotonic() - getattr(self, "_asset_checked_at", 0) < 120:
            return
        if not lock.acquire(blocking=False):
            if force:
                self.asset_status_var.set("Hay una comprobacion o actualizacion en curso. Puedes detenerla en las transferencias.")
            return
        self._asset_checked_at = time.monotonic()
        roots = list(self.roots)
        auto = self.store.settings.get("asset_auto_update", True)
        self.asset_status_var.set("Comprobando publicaciones en Oficina...")

        def work():
            try:
                tracker = getattr(self, "asset_tracker", None)
                if not tracker or tracker.engine is not engine:
                    tracker = AssetUpdateTracker(self.store, engine, getattr(self, "_asset_state", {}),
                                                getattr(self, "_save_asset_state", None))
                    self.asset_tracker = tracker
                records, errors = [], []
                for root in roots:
                    if engine.stop_event.is_set():
                        return
                    try:
                        projects = root.get("access", [])
                        if projects == "*" or not root.get("projectScoped", True):
                            projects = [item["name"] for item in self.file_api.list_folder(root["id"], "")["items"] if item["type"] == "directory"]
                        for project in projects:
                            if engine.stop_event.is_set():
                                return
                            try:
                                records.extend(self.file_api.asset_updates(root["id"], project))
                            except Exception as exc:
                                errors.append(str(exc))
                                records.extend(r for r in tracker.records.values() if r["root"] == root["id"] and r["project"] == project)
                    except Exception as exc:
                        errors.append(str(exc))
                        records.extend(r for r in tracker.records.values() if r["root"] == root["id"])
                notifications = tracker.observe(records)
                self.post(self._render_asset_updates)
                if notifications:
                    names = ["{} {} ({})".format(r["asset"], r["version"], r["kind"]) for r in notifications[:3]]
                    message = "; ".join(names) + (" y {} mas".format(len(notifications) - 3) if len(notifications) > 3 else "")
                    self.post(self._notify, "Updates de rigs y environments", message + ". Revisa la pestana Updates.")
                    self.post(self._log_activity, "Assets", "{} publicaciones disponibles".format(len(notifications)), message, "info")
                keys = list(manual_keys or []) if manual_keys is not None else [r["key"] for r in tracker.rows if auto and r["automatic"]]
                for key in keys:
                    if engine.stop_event.is_set() or engine.controls.is_paused("download"):
                        break
                    if key not in tracker.records:
                        continue
                    record = tracker.records[key]
                    self.post(self.asset_status_var.set, "Actualizando {} · {}...".format(record["asset"], record["version"]))
                    try:
                        tracker.update(key, manual=manual_keys is not None)
                        self.post(self._log_activity, "Descarga", "Master actualizado: " + record["asset"], record["path"], "success")
                    except TransferStopped:
                        if engine.controls.is_paused("download"):
                            break
                    except Exception as exc:
                        self.post(self._log_activity, "Descarga", "Master pendiente: " + record["asset"], str(exc), "error")
                    tracker.observe(records)
                    self.post(self._render_asset_updates)
                tracker.observe(records)
                self.post(self._render_asset_updates)
                pending = sum(not r["current"] for r in tracker.rows)
                message = "{} masters publicados · {} pendientes. Ultima comprobacion: {}.".format(len(records), pending, time.strftime("%H:%M"))
                if engine.controls.is_paused("download"):
                    message += " Descargas pausadas; reanudalas en Transferencias."
                if errors:
                    message += " No se pudieron comprobar algunos proyectos: " + errors[0]
                self.post(self.asset_status_var.set, message)
            except Exception as exc:
                self.post(self.asset_status_var.set, "No se pudieron comprobar los updates: " + str(exc))
            finally:
                lock.release()
        thread = self._start_session_thread(work)
        if thread is None:
            lock.release()
