# Rutas existentes y Explorador de Windows

Este documento describe la entrega 2.2.0. La integracion nativa ya implementada en 3.0.0 se describe en [explorador-3.0.md](explorador-3.0.md).

## Disco X: o R: ya ocupado por un disco local

Remote Believe 2.2.0 conserva las rutas absolutas de la oficina, como `X:\ProyectoBelieve\shots\...` y `R:\Rigs\...`. No cambia las letras ni modifica las referencias de las escenas.

Cuando la letra esta libre, el montaje completo de la cache sigue usando `subst`. Cuando esta ocupada por un disco o carpeta local NTFS, la app crea una junction por carpeta de primer nivel autorizada:

```text
X:\OtroTrabajo       -> contenido existente, ajeno a Believe
X:\ProyectoBelieve   -> C:\Users\animador\RemoteBelieveSync\X\ProyectoBelieve
```

Se crean los enlaces al conectar y al acceder/descargar una carpeta. La segunda ruta es la cache habitual, no una segunda copia. Guardar un archivo desde la primera ruta modifica el mismo archivo de la cache. La vigilancia y la bandeja de salida siguen trabajando sobre la cache, nunca sobre el disco X: completo. Las politicas de subida, permisos, estabilidad del archivo, deteccion de cambios y conflictos con versiones nuevas de Oficina se conservan.

Si `X:\ProyectoBelieve` ya existe como carpeta real, archivo o enlace a otra ubicacion, se informa un conflicto y no se modifica. Incluso una carpeta vacia se deja intacta. No se desconecta ninguna unidad existente.

Requisitos y limites:

- El volumen ocupado debe ser local NTFS y debe permitir crear carpetas en su raiz. La app no solicita elevacion automaticamente.
- La cache tambien debe estar en un volumen local NTFS para esta modalidad.
- Una unidad de red del otro trabajo no se puede integrar de esta manera. No se crean enlaces ni se copian archivos en ese servidor.
- Los archivos sueltos directamente en la raiz de una unidad ocupada quedan bloqueados. La integracion opera por carpetas completas.
- Si ambas empresas requieren una carpeta con la misma ruta exacta, se necesita resolver esa colision o usar sesiones de Windows separadas; dos contenidos distintos no pueden ocupar simultaneamente la misma ruta en una misma sesion.
- La desinstalacion conserva la cache y sus enlaces, junto con la configuracion local, para no borrar trabajo del animador.

## Barra de rutas

La barra muestra una ruta Windows utilizable, como `X:\ProyectoBelieve\shots`.

- Copiar, cortar y pegar mediante teclado o menu contextual.
- Boton Copiar para copiar la ruta completa.
- Enter o Ir para abrir una carpeta; Escape restaura la ruta actual.
- Ctrl+L y Alt+D seleccionan la barra completa.
- Acepta X:/R:, rutas UNC configuradas y rutas fisicas de la cache.
- Permite rutas relativas, `..` y cambio de disco; no permite salir de las raices autorizadas.
- Una ruta inexistente, un archivo o una carpeta sin permiso no cambia la carpeta actual.

## Que aparece hoy en el Explorador

`Abrir local` abre la carpeta de trabajo con las rutas X:/R: conservadas. Los archivos descargados se pueden abrir y guardar desde el Explorador o desde Maya. Los archivos remotos aun no descargados se siguen consultando/preparando desde Remote Believe. Los enlaces de carpeta no descargan archivos por si solos.

El servidor no ofrece una operacion de borrado remoto y el cliente no propaga eliminaciones de la cache. Un usuario si puede borrar una copia local en una carpeta Windows escribible; este cambio no introduce un bloqueo de borrado local.

## Integracion completa como un cliente de sincronizacion

Es viable mostrar tambien archivos aun no descargados, con descarga al abrirlos, estados de disponibilidad y progreso dentro del Explorador. Para implementarlo se necesita un proveedor de sincronizacion real de Windows mediante Cloud Files API, conectado a la API de Oficina por Tailscale. El nombre de esta API no implica subir los archivos a un almacenamiento publico.

El proveedor deberia:

1. Publicar placeholders reales con identidad de root y ruta, no archivos normales vacios de 0 bytes.
2. Descargar contenido al abrirlo y registrar la version recibida antes de observar cambios locales.
3. Reutilizar permisos y politicas actuales de subida, la misma ruta destino y la bandeja de salida. Verificar contenido/version antes de transferir para evitar reenvios innecesarios.
4. No enviar borrados al servidor y gestionar los conflictos antes de sobrescribir.
5. Definir por separado el bloqueo de borrado local, probando los guardados de Maya que reemplazan o renombran archivos temporales.

Esta version no implementa ese proveedor ni reactiva los proxies vacios anteriores.

Documentacion oficial:

- [Microsoft: junctions y limites entre volumenes locales/red](https://learn.microsoft.com/en-us/windows/win32/fileio/hard-links-and-junctions)
- [Microsoft: proveedor de sincronizacion con Cloud Files API](https://learn.microsoft.com/en-us/windows/win32/cfapi/build-a-cloud-file-sync-engine)
- [Microsoft: operaciones de hidratacion y confirmacion de borrados](https://learn.microsoft.com/en-us/windows/win32/api/cfapi/ns-cfapi-cf_operation_parameters)

## Verificacion y compilacion de esta entrega

Se incluyen pruebas aisladas en `tests/test_drive_paths.py`. Usan carpetas temporales y junctions reales, sin modificar los X:/R: del equipo ni los datos de usuario. Comprueban conflictos, edicion a traves del enlace, ausencia de subidas por una descarga sin cambios, deteccion de una edicion local y navegacion de la barra Tk.

```powershell
python -m unittest discover -s tests -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1 -Release 2.2.0 -SkipDependencyInstall
```

El instalador de esta entrega esta en `dist\2.2.0\RemoteBelieveSetup.exe`. Se puede instalar en el PC de casa con la misma conexion Tailscale y usuario. No se ha instalado automaticamente en el PC de oficina ni se ha probado contra el PC real del freelance.
