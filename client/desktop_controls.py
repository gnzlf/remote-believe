"""Transfer dashboard and mandatory update screen, kept separate from file operations."""
import hashlib
import logging
import os
from pathlib import Path
import subprocess
import threading
import tkinter as tk
from tkinter import ttk


class DesktopControls:
    def _install_transfer_controls(self, parent):
        if getattr(self, "_transfer_tick_id", None):
            self.after_cancel(self._transfer_tick_id)
        box = ttk.LabelFrame(parent, text="Control de transferencias", padding=10)
        box.pack(fill="x", padx=20, pady=(0, 12))
        buttons = ttk.Frame(box)
        buttons.pack(fill="x")
        self.upload_pause_button = ttk.Button(buttons, command=lambda: self._toggle_transfers("upload"))
        self.upload_pause_button.pack(side="left", padx=(0, 8))
        self.download_pause_button = ttk.Button(buttons, command=lambda: self._toggle_transfers("download"))
        self.download_pause_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Cancelar seleccionada", command=self._cancel_selected_transfer).pack(side="left")
        ttk.Button(buttons, text="Ver registros", command=self._open_diagnostics).pack(side="right")
        self.transfer_table = ttk.Treeview(box, columns=("direction", "path", "status"), show="headings", height=3)
        for column, label, width in (("direction", "Tipo", 90), ("path", "Archivo", 540), ("status", "Estado", 140)):
            self.transfer_table.heading(column, text=label)
            self.transfer_table.column(column, width=width)
        self.transfer_table.pack(fill="x", pady=(8, 0))
        self._transfer_rows = {}
        self._refresh_transfers()

    def _toggle_transfers(self, direction):
        control = self.transfers
        if control.is_paused(direction):
            control.resume(direction)
        else:
            control.pause(direction)
        self.store.settings["transfer_paused"] = dict(control.paused)
        self.store.save_settings()
        kind = "Subida" if direction == "upload" else "Descarga"
        self._log_activity(kind, "Transferencias pausadas" if control.is_paused(direction) else "Transferencias habilitadas")
        if direction == "upload" and not control.is_paused(direction):
            self._retry_pending()

    def _open_diagnostics(self):
        from diagnostics import log_directory
        folder = log_directory()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    def _refresh_transfers(self):
        if self.screen != "main" or self._closing_app or not self.transfer_table.winfo_exists():
            return
        selection = self.transfer_table.selection()
        self.transfer_table.delete(*self.transfer_table.get_children())
        self._transfer_rows = {}
        for operation in self.transfers.snapshot():
            identifier = "active-" + operation["id"]
            self._transfer_rows[identifier] = operation
            percent = int(100 * operation["done"] / operation["total"]) if operation["total"] else 0
            self.transfer_table.insert("", "end", iid=identifier, values=(
                "Subida" if operation["direction"] == "upload" else "Descarga", operation["path"], str(percent) + "%"))
        active = {(op["root"], op["path"]) for op in self._transfer_rows.values() if op["direction"] == "upload"}
        for index, entry in enumerate(self.store.pending_uploads()):
            if not entry.get("rootId") or not entry.get("remotePath"):
                continue  # Keep malformed/legacy pending records for diagnosis, without crashing the panel.
            if (entry["rootId"], entry["remotePath"]) in active:
                continue
            identifier = "pending-" + hashlib.sha256((entry["rootId"] + "::" + entry["remotePath"]).encode()).hexdigest()
            self._transfer_rows[identifier] = {"direction": "upload", "root": entry["rootId"], "path": entry["remotePath"]}
            self.transfer_table.insert("", "end", iid=identifier, values=("Subida", entry["remotePath"], "Pendiente"))
        for identifier in selection:
            if identifier in self._transfer_rows:
                self.transfer_table.selection_add(identifier)
        self.upload_pause_button.configure(text="Reanudar subidas" if self.transfers.is_paused("upload") else "Pausar subidas")
        self.download_pause_button.configure(text="Reanudar descargas" if self.transfers.is_paused("download") else "Pausar descargas")
        self._transfer_tick_id = self.after(700, self._refresh_transfers)

    def _cancel_selected_transfer(self):
        for identifier in self.transfer_table.selection():
            operation = self._transfer_rows.get(identifier)
            if not operation:
                continue
            if operation["direction"] == "upload":
                root = self._root_by_id(operation["root"])
                if root:
                    path = self.store.local_path(root, operation["path"])
                    try:
                        metadata = path.stat()
                        snapshot = {"localMtimeNs": metadata.st_mtime_ns, "localSize": metadata.st_size}
                    except OSError:
                        snapshot = None
                    if snapshot:
                        key = self.store.key(operation["root"], operation["path"])
                        self.store.settings.setdefault("cancelled_uploads", {})[key] = snapshot
                        self.store.save_settings()
                    self.store.remove_upload(operation["root"], operation["path"])
            self.transfers.cancel_path(operation["direction"], operation["root"], operation["path"])
            self._log_activity("Subida" if operation["direction"] == "upload" else "Descarga",
                               "Transferencia cancelada", operation["path"], "cancelled")

    def _install_history_filter(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=18, pady=(0, 6))
        ttk.Label(row, text="Mostrar historial:").pack(side="left")
        self.history_filter = tk.StringVar(value="Todas")
        options = ttk.Combobox(row, textvariable=self.history_filter, values=("Todas", "Subidas", "Descargas", "Sistema"), state="readonly", width=16)
        options.pack(side="left", padx=8)
        options.bind("<<ComboboxSelected>>", lambda event: self._refresh_activity_panel())

    def _render_history(self):
        if not hasattr(self, "activity_list") or not self.activity_list.winfo_exists():
            return
        for child in self.activity_list.winfo_children():
            child.destroy()
        table = ttk.Treeview(self.activity_list, columns=("time", "type", "status", "message", "path"), show="headings", height=4)
        for name, label, width in (("time", "Fecha", 140), ("type", "Tipo", 90), ("status", "Estado", 90),
                                   ("message", "Actividad", 380), ("path", "Ruta", 250)):
            table.heading(name, text=label)
            table.column(name, width=width)
        scroll = ttk.Scrollbar(self.activity_list, command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        table.pack(fill="both", expand=True)
        selected = self.history_filter.get() if hasattr(self, "history_filter") else "Todas"
        for entry in self.activity:
            direction = entry.get("direction", "system")
            if selected != "Todas" and direction != {"Subidas": "upload", "Descargas": "download", "Sistema": "system"}[selected]:
                continue
            status = {"success": "Completado", "error": "Error", "cancelled": "Cancelado",
                      "upload": "En curso", "download": "En curso", "info": "Informacion",
                      "pending": "Pendiente", "denied": "Sin permiso"}.get(entry["state"], entry["state"])
            table.insert("", "end", values=(entry["time"], entry["kind"], status, entry["message"], entry["detail"]))

    def _update_start(self):
        self.update_blocked = True
        self.update_release = None
        self.update_message = "Comprobando la ultima version de Remote Believe..."
        self._build_update_screen()
        self._check_update_now()

    def _build_update_screen(self):
        self.screen = "update"
        for child in self.winfo_children():
            child.destroy()
        box = ttk.Frame(self, padding=36)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="Actualizacion de Remote Believe", font=("Segoe UI", 20, "bold")).pack(pady=(60, 20))
        self.update_text = tk.StringVar(value=self.update_message)
        ttk.Label(box, textvariable=self.update_text, wraplength=700, justify="center").pack(pady=16)
        self.update_install_button = ttk.Button(box, text="Descargar e instalar actualizacion", command=self._install_update,
                                               state="normal" if self.update_release else "disabled")
        self.update_install_button.pack(pady=8)
        ttk.Button(box, text="Reintentar comprobacion", command=self._check_update_now).pack(pady=8)
        ttk.Button(box, text="Salir", command=self._quit_app).pack(pady=8)
        ttk.Label(box, text="Tus archivos y pendientes se conservan. Se requiere la ultima version para continuar.", wraplength=700).pack(pady=24)

    def _check_update_now(self):
        if getattr(self, "_checking_update", False) or self._closing_app:
            return
        self._checking_update = True
        def run():
            try:
                available, release = self.updater.check()
                result = (available, release, None)
            except Exception as exc:
                result = (False, None, str(exc))
            if not self._closing_app:
                self.after(0, self._update_result, *result)
        threading.Thread(target=run, daemon=True).start()

    def _update_result(self, available, release, problem):
        self._checking_update = False
        timer = getattr(self, "_update_timer_id", None)
        if timer:
            self.after_cancel(timer)
        if problem and self.screen == "main" and not getattr(self, "update_release", None):
            # An already verified session must not lose its cloud provider just
            # because GitHub had a transient failure. Known updates still block.
            logging.getLogger("RemoteBelieve").warning("Update verification will retry: %s", problem)
            self.set_status("No se pudo comprobar GitHub; reintentando sin desconectar tus carpetas.")
            self._update_timer_id = self.after(60000, self._check_update_now)
            return
        self.update_blocked = bool(available or problem)
        self.update_release = release if available else None
        self.update_message = problem or ("Hay una nueva version: " + release["tag"] if available else "Version actualizada")
        if self.update_blocked:
            if self.screen == "main":
                self._logout()  # Stops sockets, provider, watcher and Office API before requiring update.
            elif self.screen != "logout":
                self._build_update_screen()
                self._show_window()
        elif self.screen == "update":
            self._build_login()
        self._update_timer_id = self.after(300000, self._check_update_now)

    def _install_update(self):
        if not self.update_release or getattr(self, "_installing_update", False):
            return
        self._installing_update = True
        self.update_install_button.configure(state="disabled")
        def run():
            try:
                path = self.updater.download(self.update_release, lambda done, total:
                    self.after(0, self.update_text.set, "Descargando instalador: {}%".format(int(100 * done / total))))
                subprocess.Popen([str(path), "--update", "--wait-pid", str(os.getpid())],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.after(0, self._quit_app)
            except Exception as exc:
                self.after(0, self.update_text.set, "No se pudo actualizar: " + str(exc))
                self.after(0, self.update_install_button.configure, {"state": "normal"})
                self._installing_update = False
        threading.Thread(target=run, daemon=True).start()
