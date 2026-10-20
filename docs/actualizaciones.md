# Actualizaciones obligatorias

Los instaladores se publican en las releases estables de
https://github.com/gnzlf/remote-believe.

## Primera instalacion de este mecanismo

Instalar 3.1.0 una vez en Oficina y en cada PC remoto, usando el mismo perfil de
Windows donde ya esta Believe. Cerrar antes desde la bandeja > Salir y ejecutar
el instalador sobre la instalacion existente. No borrar caches ni desinstalar.
Se conservan cuentas, permisos, archivos y pendientes. Tailscale se utiliza tal
como ya esta conectado.

Las versiones 3.0.x no incluyen el actualizador y necesitan esta instalacion
inicial. Oficina 3.1.0 rechaza los clientes anteriores indicando la pagina de
descarga. Instalar 3.1.0 en Casa sin actualizar Oficina no habilita el nuevo
protocolo de comparacion por contenido.

## Uso posterior en cada PC

Believe comprueba GitHub antes de permitir iniciar sesion y cada cinco minutos.
Una version nueva detiene transferencias, cierra la sesion con seguridad y
muestra Actualizar. Si no puede verificar GitHub, muestra Reintentar y mantiene
el acceso bloqueado. La comprobacion requiere Internet, aunque Tailscale siga
funcionando. No existe un boton para saltarse la verificacion.

Si GitHub limita las consultas de su API por compartir una misma conexion de
Internet, se consulta el manifiesto publico `update.json` de la misma release.
Este tambien exige origen, tamano y SHA256 validos; no necesita una cuenta.

Descargar e instalar verifica el tamano y SHA256 publicado por GitHub. El
instalador espera a que termine el proceso anterior, conserva una copia del
ejecutable anterior y reemplaza el programa por usuario. No mata procesos de
otros usuarios, no instala Tailscale y no cambia las carpetas de trabajo.

## Publicar una version

1. Cambiar la version en cliente, instalador y proveedor nativo y guardar los
   cambios en GitHub.
2. En Actions, ejecutar **Preparar instalador de Windows** con esa version.
   Crea una release en borrador con instalador, SHA256 y `update.json`. Tambien puede usarse
   `scripts/publish-release.ps1` en un PC con las herramientas de compilacion.
3. Revisar el instalador y publicar la release estable en GitHub. Los borradores
   y prereleases no se ofrecen a los usuarios. Una release publicada debe
   contener exactamente un `RemoteBelieveSetup.exe` completo y el `update.json`
   generado por `scripts/check-release.py` para ese mismo instalador.

No reemplazar el instalador de una version publicada: crear una version nueva.
Las releases publicadas sin instalador valido bloquean la comprobacion y deben
completarse o retirarse. No incluir tokens de GitHub en los ejecutables: los PCs
descargan el instalador publico sin una cuenta de GitHub.

Referencias:
https://docs.github.com/en/rest/releases/releases
https://docs.github.com/en/rest/releases/assets
