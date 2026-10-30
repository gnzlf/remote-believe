# Remote Believe

Cliente Windows de archivos bajo demanda para trabajar con las rutas `X:\` y `R:\`
a traves de la conexion Tailscale ya instalada. Los archivos se usan desde el
Explorador de Windows; Believe gestiona sesion, transferencias y permisos.

## Descargar

[Instalador de la ultima version](https://github.com/gnzlf/remote-believe/releases/latest).

## Version 3.2.0

- Pestana **Updates** con rigs y environments publicados de los proyectos asignados,
  versiones, estado local y acciones para descargar uno, varios o todos los pendientes.
- Avisos de publicaciones nuevas en la bandeja de Windows y en el historial. La
  comprobacion es cada dos minutos mientras Believe esta conectado; los avisos
  ya vistos se recuerdan por cuenta, incluso al volver a iniciar sesion.
- Actualizacion automatica de los masters ya descargados, conservando su ruta
  X:/R:. Los masters que no se utilizan permanecen a demanda. Se puede desactivar
  la actualizacion automatica, pausar/reanudar descargas y cancelar transferencias.
- Se detecta la version de `Assets/.../Export/<producto>/master/versioninfo.json`.
  Las escenas de trabajo y carpetas `v####` no son publicaciones hasta que se
  actualiza su master. Se espera a que la publicacion se estabilice. No se
  descargan carpetas de versiones, texturas ni dependencias automaticamente.
- Los masters abiertos en Maya quedan pendientes; se reintentan cuando se cierran.
  Los cambios locales, archivos pendientes de subida y eliminaciones locales se
  respetan. Cancelar una descarga evita reintentar esa misma revision automaticamente.

Actualizar primero Oficina e iniciar sesion alli, y despues los PCs remotos.
Usuarios y permisos siguen en la base existente. [Uso y alcance de Updates](docs/updates-assets.md).

## Version 3.1.2

- Reparacion del error Windows `0x80070179` al abrir referencias de Maya o
  descargar proxies cuya version cambio en Oficina. Se renueva su identidad
  y se reinicia la solicitud nativa con la version actual, con reintentos limitados.
- Los cambios de fecha sin cambios de contenido ya no bloquean un proxy vacio.
  La inspeccion nativa comprueba los bytes modificados y el tamano original antes
  de actualizar. Los guardados concurrentes, truncados y archivos locales se conservan.
- Actualizar metadatos no descarga contenido. El manifiesto solo avanza cuando
  el cambio se aplico realmente en Windows.
- Un archivo en conflicto no detiene la descarga de los demas archivos de una
  carpeta; el historial identifica los pendientes. Cancelar sigue deteniendo el lote.

## Version 3.1.1

- Descargas del proveedor en bloques pequenos, con reanudacion por rangos ante
  cortes y notificacion de progreso a Windows mientras espera o descarga.
- Catalogos de carpetas independientes de los trabajadores de descarga.
- Los errores de pausa, permisos o archivo cambiado ya no se notifican como
  red no disponible. La hidratacion explicita del Explorador puede descargarse
  sin permitir descargas automaticas de miniaturas.
- Conexion a Oficina directa por Tailscale, evitando proxies de Internet.
- Un fallo temporal de GitHub no desconecta una sesion ya verificada. Las
  actualizaciones confirmadas siguen siendo obligatorias.
- Boton **Ver registros** con diagnostico local y registros rotativos.

## Version 3.1.0

- Abrir una carpeta solo carga su catalogo. Las miniaturas e indexadores de
  Windows no descargan el contenido. Se eliminaron las antiguas instrucciones
  de descargar carpetas completas automaticamente. La descarga de una carpeta
  desde el menu contextual es una accion explicita y se ejecuta una sola vez.
- Las subidas comparan SHA256 del archivo con su destino: un contenido identico
  no se envia aunque tenga otra fecha; uno nuevo o modificado puede subirse,
  segun permisos y politicas. Se conservan los cambios del servidor si otra
  persona modifica el archivo durante la subida.
- Pausar/reanudar subidas y descargas por separado y cancelar una transferencia
  seleccionada sin cerrar sesion. Pausar conserva los pendientes. Cancelar una
  subida evita reencolar esa misma copia sin cambios; para solicitarla de nuevo,
  usar la opcion de subida manual del Explorador.
- Historial separado en Todas, Subidas, Descargas y Sistema, conservado por cuenta.
- Tras eliminar copias locales se reconstruyen proxies nativos sin descargar
  contenido. Se registran sus estados con el Explorador y se notifican los
  cambios de icono. Una carpeta proxy contiene metadatos; no los bytes remotos.
- Actualizaciones obligatorias desde releases estables de este repositorio,
  sin credenciales de GitHub en los PCs. El instalador se verifica por SHA256.

### Instalar la primera version con actualizador

Cerrar Believe desde el icono junto al reloj > Salir. Instalar 3.1.0 sobre la
version actual, primero en Oficina y despues en cada PC remoto. No borrar la
cache ni desinstalar: archivos, usuarios, permisos y pendientes se conservan.
Las versiones anteriores no tienen este mecanismo y requieren esa instalacion
inicial. En las siguientes versiones, la app muestra Actualizar y realiza el
reemplazo por usuario.

Si es una Oficina completamente nueva y su base no tiene usuarios, las
credenciales ingresadas localmente en modo Oficina crean la primera cuenta.
No se incluyen contrasenas iniciales compartidas en el codigo.

[Como publicar y aplicar actualizaciones](docs/actualizaciones.md).

### Compilar y comprobar

Requiere Windows x64, Python 3.9+ y las herramientas C++/SDK de Visual Studio.

```powershell
python -m pip install -r client/requirements.txt pyinstaller==6.19.0
./scripts/build-cloud-bridge.ps1
python -m unittest discover -s tests -v
./scripts/build-windows-installer.ps1 -Release 3.2.0 -SkipDependencyInstall
python scripts/check-release.py 3.2.0
```

El codigo de este repositorio no contiene bases de usuarios, caches, pendientes,
credenciales ni archivos de proyectos. Los binarios se distribuyen en Releases.

## Historial de versiones

## Version 3.0.6: corregir consultas de nombres en archivos bajo demanda

Se corrige la consulta de la ruta fisica de los placeholders, que puede producir
`ERROR_FLT_INVALID_NAME_REQUEST` (Windows `0x801F0005`) al encontrar un JPG u otro
archivo en la cache de una cuenta. El seguimiento usa claves de ruta sin abrir
el archivo. La identificacion de rutas resuelve junctions/symlinks reales y
mantiene la comprobacion de pertenencia a la cache, incluyendo rutas cortas 8.3.

Un error temporal de metadatos no se interpreta como una eliminacion local:
conserva los pendientes y las instrucciones offline. El escaneo continua con
los otros archivos y reintenta los inaccesibles posteriormente. Se mantienen
X:/R:, inicio de sesion manual, permisos y las reglas de subida existentes.

Instalador: `dist/3.0.6/RemoteBelieveSetup.exe`. Para este error basta actualizar
el PC de casa afectado, cerrando Believe desde la bandeja antes de instalar.
No es necesario actualizar Oficina ni los otros freelancers.

Verificacion: 69 pruebas, incluidas consultas con el error de Windows simulado,
archivos Cloud Files reales, limites de cache, cambio de cuentas, eliminaciones
locales, conservacion de ediciones y ausencia de subidas de copias sin cambios.

Referencia del codigo de Windows:
https://learn.microsoft.com/en-us/windows/win32/com/com-error-codes-5

## Version 3.0.5: archivos en el Explorador de Windows

Para un PC freelance que queda en "Conectando tus carpetas al Explorador", hay un
paquete de diagnostico y reparacion local en `dist/soporte-remoto/Reparar-RemoteBelieve-PC.zip`.
Se extrae y se abre `Reparar-RemoteBelieve.cmd` con doble clic normal en ese PC.
No requiere reinstalar ni actualizar los otros clientes. Consulta
`scripts/soporte-remoto/LEEME.txt` para su alcance y el informe que genera.

En Casa, Remote Believe ahora es un panel de conexion, actividad, transferencias y pendientes. Las carpetas autorizadas aparecen en la entrada **Remote Believe** del Explorador. Los archivos remotos tienen placeholders nativos de Windows y se descargan cuando una aplicacion los abre, incluyendo las referencias que Maya solicita por X:/R:.

El instalador incluye el proveedor nativo y solo instala Believe. Se utiliza Tailscale tal como ya este instalado y conectado en Windows, sin reinstalarlo, configurarlo ni pedir otro inicio de sesion de Tailscale. Believe pide iniciar sesion al abrirse y elimina las credenciales recordadas de versiones anteriores. El boton **Cerrar sesion**, disponible en Casa, Oficina y en el menu de la bandeja, desconecta la sesion y vuelve al acceso. La IP inicial de Oficina se incluye en `client/connection-defaults.json`, sin credenciales.

Se mantienen las rutas X:/R:, permisos, administracion, politicas de subida, bandeja offline, temas y menu contextual de preparacion de shots. Oficina conserva su interfaz y sus funciones de servidor. Las descargas y las copias sin cambios se distinguen de los archivos nuevos o modificados para evitar subidas innecesarias.

Se puede cerrar sesion e ingresar con otra cuenta de Believe en el mismo perfil de Windows. Cada cuenta conserva su propia cache, manifest, carpetas offline y pendientes. Los archivos existentes se conservan en su ubicacion y quedan asociados a la cuenta anterior. X:/R: se conectan a la cuenta activa; las unidades y carpetas del otro trabajo no se reemplazan.

Los proyectos asignados, sus carpetas y la lista de archivos siempre estan disponibles en el Explorador. Mostrar esos nombres no descarga el contenido. Borrar una copia local o la cache cancela las instrucciones offline que afectan a lo eliminado y evita descargar de nuevo su contenido en segundo plano. El archivo sigue visible y se descarga cuando el usuario lo abre o lo solicita. Se corrige automaticamente la ocultacion causada por las marcas guardadas en la version 3.0.4, conservando la proteccion contra descargas automaticas. Borrar o mover fuera de la cache solo afecta al PC del usuario, nunca a Oficina.

Instalador: `dist\3.0.5\RemoteBelieveSetup.exe`. Instalar esta version en Oficina y en los clientes permite aplicar tambien la comprobacion de version de descarga y la revocacion de sesion del servidor.

[Guia de instalacion y alcance de la version 3.0](docs/explorador-3.0.md).

## Version 2.2.0: conservar X: y R: cuando ya existen

Las rutas de Believe se mantienen exactamente como en la oficina. No se usan letras alternativas ni se reescriben las escenas de Maya.

- Si X: o R: esta libre, se monta la cache con esa misma letra.
- Si la letra pertenece a un disco/carpeta local NTFS, se crean enlaces de directorio (junctions) para las carpetas autorizadas. Por ejemplo, `X:\ProyectoBelieve` apunta a `%USERPROFILE%\RemoteBelieveSync\X\ProyectoBelieve`.
- El enlace y la cache muestran los mismos archivos; no son dos copias.
- Las otras carpetas del disco existente quedan fuera de la cache y del escaneo de subidas.
- Una carpeta existente con el mismo nombre se considera un conflicto: no se adopta, mezcla, borra ni sobrescribe.
- No se reemplazan unidades ajenas, incluyendo unidades creadas con `subst`.

El montaje de carpetas requiere NTFS local y permiso para crear una carpeta en la raiz del disco. Una unidad de red ocupada, un volumen sin NTFS o un archivo suelto en la raiz no admiten esta solucion; la app informa el bloqueo y conserva las rutas.

La barra superior permite editar, copiar y pegar rutas de carpeta. Enter/Ir navega; Escape restaura la ruta actual; Ctrl+L o Alt+D seleccionan la barra. Tambien acepta rutas UNC conocidas y rutas de la cache, respetando los permisos del usuario.

Detalles y alcance del modo Explorador: [rutas-y-explorador.md](docs/rutas-y-explorador.md).

## Cerebro

El cerebro actual es:

```text
R:\syncremote.json
```

Ese archivo guarda:

- usuarios;
- roles (`user`, `admin`, `superuser`);
- permiso de subida directa;
- acceso por disco y carpeta/proyecto;
- roots disponibles, por defecto `X:` y `R:`.

## Flujo

1. En el PC de oficina se abre la app en modo `Oficina`.
2. Ese PC lee los discos reales `X:\` y `R:\`.
3. En casa se abre la app en modo `Casa` usando la IP Tailscale del PC de oficina.
4. Los archivos pesados viajan directo por Tailscale.
5. El superuser administra usuarios desde el panel `Administracion`.

## Subidas offline

Si un usuario crea o modifica archivos sin conexion, Remote Believe guarda esos cambios en una bandeja de salida local:

```text
%APPDATA%\RemoteBelieveMesh\accounts\<identificador>\outbox.json
```

Al volver a abrir la app con conexion, esos archivos quedan como `Pendiente` y se reintentan automaticamente hasta subir a la oficina.

## Proteccion contra archivos vacios

Remote Believe bloquea subidas de `0 bytes`, rechaza chunks vacios y arma cada subida en un archivo temporal antes de reemplazar el archivo real del servidor. Esto evita que una subida incompleta pueda pisar un archivo existente y dejarlo en `0 KB`.

Para auditar archivos vacios existentes en `X:\` y `R:\`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\find-zero-byte-server-files.ps1"
```

## Seguridad

- Los usuarios normales solo ven carpetas autorizadas en `syncremote.json`.
- `X:` y `R:` son project-scoped, o sea se pueden limitar por carpeta de primer nivel.
- Solo `admin` y `superuser` pueden editar usuarios.
- Los usuarios normales solo pueden subir si tienen `directUpload` activado.

## Instalador Windows

Para crear el instalador `.exe`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\build-windows-installer.ps1"
```

El instalador queda en:

```text
dist\RemoteBelieveSetup.exe
```

Al instalar, Remote Believe queda en `%LOCALAPPDATA%\Programs\Remote Believe`, se inicia automaticamente con Windows, instala el menu contextual del Explorador y agrega un desinstalador en el menu Inicio y en "Aplicaciones instaladas" de Windows.
