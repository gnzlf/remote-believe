# Cliente de Windows

`mesh_client.py` contiene el panel de sesion y la API de Oficina. Los archivos
se muestran en el Explorador nativo mediante `cloud_files.py` y el puente C++.
`transfer_control.py` gestiona pausa y cancelacion; `app_updates.py` consulta
las releases publicas y verifica el instalador; `desktop_controls.py` muestra
transferencias, historial y el bloqueo de actualizacion.

Usar el README principal y `docs/actualizaciones.md` para instalacion y releases.
Tailscale se configura fuera de Believe y no se instala ni modifica desde la app.
