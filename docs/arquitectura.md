# Arquitectura actual

## Modo Oficina

La app se ejecuta en el PC que tiene acceso real a `X:\` y `R:\`.

- Lee usuarios/permisos desde `R:\syncremote.json`.
- Sirve archivos por HTTP local en el puerto `7878`.
- Valida login, permisos, descargas y subidas.

## Modo Casa

La app se ejecuta en el PC remoto.

- Se conecta a la IP Tailscale del PC de oficina.
- Monta/cachea `X:` y `R:` localmente cuando corresponde.
- Si una letra ya pertenece a un disco local NTFS, enlaza solo las carpetas autorizadas dentro de ese disco y conserva las rutas X:/R:.
- No cambia letras ni escanea las carpetas de otros trabajos para subir archivos.
- Descarga archivos bajo demanda.
- Sube cambios solo si el usuario tiene permiso.

## Administracion

El panel `Administracion` esta disponible solo para `admin` y `superuser`.

Desde ahi se puede editar:

- usuarios;
- contrasenas;
- roles;
- permiso de subida directa;
- acceso por carpeta de primer nivel en `X:` y `R:`.

## API local de oficina

- `POST /api/login`
- `GET /api/access`
- `GET /api/list`
- `GET /api/download`
- `POST /api/upload-chunk`
- `POST /api/upload-complete`
- `GET /api/admin/syncremote`
- `POST /api/admin/syncremote`
