# Updates de rigs y environments

Desde 3.2.1, Updates muestra exclusivamente rigs/environments descargados en
el PC de la cuenta activa que tienen una actualizacion disponible. Los masters
que no se han descargado, los proxies, las copias eliminadas/liberadas y los
archivos ya actualizados no aparecen ni generan avisos. La lista indica proyecto,
version publicada, version local conocida y estado. La ruta del elemento
seleccionado se puede copiar desde el campo inferior.

Oficina inspecciona `03_Production/Assets` y `Assets` dentro de cada proyecto.
Se reconocen productos Rig/Rigging y los productos de categorias Environment.
Se admiten masters .ma, .mb, .fbx, .abc, .usd, .usda, .usdc y .blend publicados en
`Export/<producto>/master`. La version sale del `versioninfo.json` del master;
si falta, se identifica como master y se detectan cambios de fecha/tamano.
Tambien se avisa cuando se vuelve a publicar una misma version.

Crear una carpeta `v####` o guardar una escena en Scenefiles no actualiza el
master. El pipeline debe publicar el master como lo hace actualmente. El
detector espera al menos 30 segundos desde la escritura de archivos/metadatos
y confirma los cambios entre observaciones. Oficina comparte una cache de un
minuto entre clientes; Casa comprueba cada dos minutos. Un aviso puede tardar
varios minutos despues de publicar. Buscar updates consulta el catalogo sin
descargar los archivos. La bandeja muestra los avisos si Windows permite
notificaciones de Believe; la pestana y el historial conservan los estados.

La opcion automatica actualiza solo masters que ya se descargaron/abrieron en
ese PC y conservan su copia local. Si Believe devolvio una copia a proxy por
un cambio publicado, su actualizacion pendiente se conserva hasta completarla.
Actualizar seleccionados y Actualizar todos los pendientes solicitan actualizar
solo los masters locales de esa lista. Las
actualizaciones conservan el nombre y la ruta del master y usan la conexion
existente a Oficina; no crean copias con otros nombres ni generan subidas.
No se descargan las versiones anteriores, texturas ni archivos relacionados.

Si Maya u otro programa mantiene abierto el master, puede quedar pendiente
hasta cerrar esa escena. Las ediciones locales y pendientes de subida no se
reemplazan. Un error en un master permite continuar con los demas. Pausar
mantiene las actualizaciones pendientes para reanudar; cancelar una transferencia
evita reintentar automaticamente esa revision, hasta solicitarla manualmente
o publicarse otra. Una copia eliminada localmente sigue como proxy y requiere
una accion explicita para descargarla de nuevo.

Los avisos, versiones locales y cancelaciones pertenecen a cada cuenta Believe.
Al cerrar sesion se detienen las tareas. La API verifica los permisos vigentes
antes de mostrar cada proyecto y antes de descargar. No publica los datos de
autores, dependencias ni rutas de trabajo del versioninfo.

Instalar 3.2.1 primero en Oficina e iniciar sesion en modo Oficina. Actualizar
despues Casa/freelancers mediante el actualizador existente. Se usa la misma
base de usuarios/permisos, las mismas rutas y Tailscale ya conectado. No borrar
la cache ni desinstalar la version anterior.
