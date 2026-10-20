# Remote Believe 3.0: instalacion y uso

## Preparacion en Oficina

1. Actualizar Remote Believe con `dist\3.0.5\RemoteBelieveSetup.exe` cuando no haya transferencias activas. El instalador cierra la version anterior para actualizarla.
2. Mantener el modo Oficina, Tailscale conectado y acceso a los discos/compartidos originales X:/R:.
3. Iniciar sesion al abrir Believe. No hay inicio de sesion automatico, incluso si una version anterior recordaba las credenciales.
4. Administrar usuarios y carpetas desde el mismo panel Administracion. La configuracion sigue en `R:\syncremote.json`; este archivo no se publica a usuarios normales, aunque tengan acceso a toda la raiz R:.

La configuracion y los archivos de trabajo existentes no se reemplazan por archivos de ejemplo. Las cuentas nuevas se crean desde Administracion.

## Instalacion del freelance

1. Con Tailscale ya instalado y conectado a la red de Believe, ejecutar el mismo instalador. Incluye el componente nativo; no necesita Python, Visual Studio, comandos ni montar discos manualmente.
2. El instalador solo instala Believe. No reinstala Tailscale, no modifica su configuracion y no abre un inicio de sesion de Tailscale. Si Tailscale no esta conectado, se puede abrir desde Windows como de costumbre.
3. En Remote Believe, elegir Casa e iniciar sesion con el usuario asignado. La IP inicial de Oficina viene configurada. Believe pide iniciar sesion cada vez que se abre.
4. Abrir **Remote Believe** en el panel izquierdo del Explorador de Windows, o pulsar **Abrir mis carpetas**. Solo se generan accesos a las carpetas autorizadas.

Los accesos del Explorador dirigen a las rutas X:/R: originales. Los archivos se descargan al abrirse. Una vez descargados, quedan en la cache local y pueden abrirse sin conexion. El programa debe permanecer abierto o en el area de notificaciones para descargar archivos que aun no estan disponibles localmente.

## Trabajo diario

- Explorar, abrir y guardar desde el Explorador o desde Maya.
- Las referencias X:/R: se conservan; no se reescriben escenas para cambiar las letras.
- Los cambios locales estables se suben segun las politicas de carpeta/extension y los permisos que ya tenia el usuario.
- Descargar o copiar un archivo sin cambiar su contenido no provoca una subida de ese contenido conocido.
- Los pendientes sobreviven a reinicios. Mientras el usuario mantenga su sesion abierta, la app recupera la conexion cuando Oficina vuelve a estar disponible.
- Desde el menu contextual se conservan descargar/preparar shot y se agregan subir cambios y mantener/dejar de mantener una carpeta sin conexion. En Windows 11 pueden estar dentro de Mostrar mas opciones.
- El panel de Casa muestra estado, actividad, progreso, reintento de pendientes y pausa de subidas; el acceso administrativo se mantiene para los roles autorizados.
- **Cerrar sesion** esta disponible en Casa, Oficina y en el menu del icono junto al reloj. Detiene las transferencias y tareas de esa sesion, desconecta el proveedor del Explorador, retira sus accesos publicados y vuelve al acceso. Conserva cache, archivos locales, pendientes, configuracion, usuarios y permisos. Las tareas anteriores no pueden volver a abrir una sesion. En Oficina tambien se detiene el servidor hasta volver a iniciar sesion.

## Rutas ocupadas y borrados

Cuando X: o R: esta libre, se monta la cache con esa misma letra. Si es un disco o carpeta local NTFS ocupado, se enlazan exclusivamente las carpetas autorizadas dentro de el. El enlace y la cache son el mismo contenido, no dos copias. Las otras carpetas del disco existente no se escanean ni se suben.

Si una carpeta con el mismo nombre ya existe y no apunta a la cache de Believe, se informa el conflicto y se conserva intacta. Se necesita permiso para crear las carpetas en la raiz. Esta modalidad no puede integrar una unidad de red ocupada ni archivos sueltos en su raiz. Dos contenidos distintos no pueden compartir la misma ruta exacta en una sesion de Windows.

El proveedor permite borrar copias locales y moverlas fuera de la cache, incluida la Papelera. Estas operaciones no se propagan a Oficina: el servidor no incorpora ninguna operacion de borrado. Los renombrados dentro de la cache mantienen las restricciones anteriores, con las excepciones necesarias para el guardado/reemplazo de Maya. Los reemplazos de Maya no se interpretan como una orden de dejar de descargar el archivo.

Desde la version 3.0.5, los proyectos autorizados y sus carpetas siempre son visibles, aunque se haya borrado la cache o existan marcas de borrado de la version 3.0.4. La lista del Explorador se publica sin descargar el contenido. Antes de montar la cache se revisa lo que falta y se cancelan las marcas offline relacionadas con las copias eliminadas. La decision de no descargar automaticamente queda separada de la visibilidad y de los permisos; cada cuenta mantiene sus propias preferencias.

Para recuperar el contenido, basta con abrir el archivo desde el Explorador o Maya, usar la orden manual de descarga o marcar una carpeta de nuevo para mantenerla sin conexion. No hace falta un boton para volver a mostrar el proyecto. **Actualizar conexion y permisos** actualiza la lista habitual. Los nombres, carpetas y accesos X:/R: pueden reaparecer como elementos virtuales; eso no significa que sus archivos se hayan vuelto a descargar. Las marcas offline antiguas no se reactivan por navegar ni por iniciar sesion.

Los permisos se vuelven a consultar y se aplican antes de publicar o descargar. Quitar permisos bloquea nuevas descargas y retira los accesos publicados; no permite recuperar remotamente copias completas que el usuario ya habia descargado.

Desde la version 3.0.3 se pueden usar distintas cuentas de Believe en un mismo perfil de Windows. Al cerrar sesion se desconectan exclusivamente los montajes y enlaces de Believe; al iniciar otra cuenta, las mismas rutas X:/R: apuntan a su propia cache. Cada cuenta mantiene por separado sus archivos, manifest, pendientes, carpetas offline y preferencias de subida. No se necesitan otros perfiles de Windows.

La cache anterior se registra bajo el propietario guardado y sus archivos no se mueven ni se borran. Los pendientes se recuperan cuando vuelve a entrar esa cuenta; otra cuenta no los sube. Si una cache antigua no tiene propietario identificable, se conserva aparte y el nuevo usuario recibe una cache vacia. Las unidades de otros programas, las carpetas normales y los enlaces que no apuntan a una cache registrada de Believe se conservan intactos.

## Datos y dependencias

- Cache original: `%USERPROFILE%\RemoteBelieveSync`, conservada para la cuenta anterior. Las otras cuentas reciben carpetas propias en `RemoteBelieveSync.accounts`, junto a la cache original. Las rutas de trabajo visibles siguen siendo X:/R:.
- Configuracion: `%APPDATA%\RemoteBelieveMesh`. El manifest, la bandeja y las preferencias de cada cuenta se guardan en `accounts\<identificador>`. Los JSON originales se conservan durante la migracion.
- Credenciales: no se guardan para iniciar sesion automaticamente. Al abrir la version 3.0.2 se elimina el antiguo `session.dpapi` y se desactiva la opcion anterior de recordar sesion. La reconexion usa credenciales en memoria solo mientras la sesion actual permanezca abierta.
- Archivos de proyecto: viajan directamente por la API de Oficina sobre Tailscale. No se agrego almacenamiento de proyectos en una nube publica.
- Windows 10/11 de 64 bits con Cloud Files y NTFS. El proveedor nativo se distribuye dentro del ejecutable y usa el componente Cloud Filter incluido en Windows.
- Tailscale: se utiliza la instalacion y conexion existentes del PC. Believe consulta la IP y los pares para encontrar Oficina; no instala, configura ni inicia sesion en Tailscale.

La desinstalacion quita el inicio automatico, el nodo y menu del Explorador y la credencial recordada. Conserva la cache, pendientes, configuracion y enlaces de carpeta para no borrar trabajo. Los archivos que nunca se descargaron requieren el proveedor para obtener su contenido.

## Compilacion y verificacion

```powershell
python -m unittest discover -s tests -v
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-windows-installer.ps1 -Release 3.0.5 -SkipDependencyInstall
```

La compilacion necesita Python/PyInstaller y las herramientas C++ de Visual Studio con el SDK de Windows. El usuario final no necesita esas herramientas. El codigo nativo esta en `native/cloud_bridge.cpp` y la logica de archivos a demanda en `client/cloud_files.py`.

Las pruebas registran raices temporales NTFS aisladas y usan procesos externos para enumerar, abrir, borrar y mover archivos. Cubren hidratacion real, apertura por junction, actualizaciones remotas, preservacion de ediciones locales, permisos, migracion de proxies antiguos, deduplicacion de contenido conocido, DPAPI, panel sin explorador, reconexion de la API, acceso manual con una sesion antigua guardada, cierre desde Casa y Oficina, cancelacion de tareas y subidas, y revocacion del token sin afectar otras sesiones. No modifican X:/R: reales, permisos de produccion ni el registro del Explorador del usuario.

Tambien cubren eliminacion local desde otro proceso, borrado de toda la cache entre sesiones, cancelacion de marcas offline antiguas, visibilidad de proyectos y subcarpetas con marcas de borrado guardadas, publicacion del acceso del Explorador, navegacion sin descargar datos, apertura posterior, borrado durante una consulta en segundo plano y persistencia de la decision por cuenta. La prueba de visibilidad recorre carpetas anidadas desde un proceso externo, comprueba cero lecturas de contenido hasta abrir un archivo y confirma que un proyecto sin permiso no aparece. El rechazo de borrados en Oficina se prueba por separado.

Las pruebas de cuentas tambien cambian entre dos usuarios y vuelven al primero: comprueban conservacion de pendientes y archivos, restauracion tras reiniciar, cambio de subst propio, proteccion de unidades ajenas y junctions NTFS. Una prueba nativa abre la misma ruta desde otro proceso en las tres sesiones y verifica que cada cuenta obtiene su propio contenido, incluso con el mismo nombre y metadatos remotos.

Se compilo y verifico localmente el paquete. Queda pendiente validar el flujo de instalacion y guardado de escenas reales con Maya en el PC del freelance con su conexion Tailscale habitual. No se actualizo automaticamente la instancia activa de Oficina.
