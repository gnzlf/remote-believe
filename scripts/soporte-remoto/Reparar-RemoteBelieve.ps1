#requires -Version 5.1
[CmdletBinding()]
param([switch]$SoloDiagnostico, [switch]$SinPausa, [switch]$NoAbrir,
      [string]$ProbeDirectory = '')

$ErrorActionPreference = 'Stop'

function Get-LocalCachePath([string]$Path) {
    # No network paths, drive roots, relative paths, ADS or traversal in a repair target.
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path.Substring(2) -match '[:*?"<>|]' -or
        @($Path.Split('\') | Where-Object { $_ -eq '..' -or $_ -eq '.' }).Count) { return $null }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full.Length -le 3) { return $null }
    return $full
}

function Get-ActiveCache($Settings) {
    $id = [string]$Settings.active_account
    if ($id) {
        if ($id -notmatch '^[a-f0-9]{64}$' -or -not $Settings.account_profiles) { return $null }
        $record = $Settings.account_profiles.PSObject.Properties[$id]
        if (-not $record) { return $null }
        return Get-LocalCachePath ([string]$record.Value.cache_dir)
    }
    # Old installations have no account_profiles. Never guess another account's cache.
    if ($Settings.account_profiles) { return $null }
    if ($Settings.cache_dir) { return Get-LocalCachePath ([string]$Settings.cache_dir) }
    return Join-Path $env:USERPROFILE 'RemoteBelieveSync'
}

function Get-OwnedDriveTargets($Settings, [string]$Letter) {
    $caches = @()
    if ($Settings.account_profiles) {
        foreach ($record in $Settings.account_profiles.PSObject.Properties) {
            $cache = Get-LocalCachePath ([string]$record.Value.cache_dir)
            if ($cache) { $caches += $cache }
        }
    } elseif ($Settings.cache_owner -and $Settings.cache_owner -ceq $Settings.username) {
        $cache = Get-ActiveCache $Settings
        if ($cache) { $caches += $cache }
    }
    foreach ($cache in $caches) { Join-Path $cache $Letter }
}

function Get-DriveAction([string]$Device, [string[]]$OwnedTargets) {
    if (-not $Device) { return 'Libre' }
    if ($Device.StartsWith('\??\', [StringComparison]::Ordinal)) {
        $target = Get-LocalCachePath $Device.Substring(4)
        if ($target) {
            foreach ($owned in $OwnedTargets) {
                if ([string]::Equals($target, $owned, [StringComparison]::OrdinalIgnoreCase)) { return 'Believe' }
            }
        }
    }
    return 'Ajena'
}

function Write-Report([string]$Message) {
    $line = '[' + (Get-Date -Format 'HH:mm:ss') + '] ' + $Message
    Write-Host $line
    [IO.File]::AppendAllText($script:ReportPath, $line + [Environment]::NewLine, [Text.Encoding]::UTF8)
}

function ConvertTo-NativeArguments([string[]]$Arguments) {
    $quoted = foreach ($argument in $Arguments) {
        if ($argument.Contains('"')) { throw 'Argumento no admitido.' }
        '"' + [regex]::Replace($argument, '(\\+)$', '$1$1') + '"'
    }
    return $quoted -join ' '
}

function Invoke-BoundedProgram([string]$File, [string[]]$Arguments, [int]$TimeoutSeconds = 8) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = ConvertTo-NativeArguments $Arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill() # Only the child process created above, never a Believe process.
            $process.WaitForExit()
            throw "Tiempo de espera agotado ($TimeoutSeconds segundos): $([IO.Path]::GetFileName($File))"
        }
        [pscustomobject]@{ ExitCode=$process.ExitCode; Output=$stdout.Result.Trim(); Error=$stderr.Result.Trim() }
    } finally { $process.Dispose() }
}

function Get-BelieveProcesses {
    @(Get-Process -Name RemoteBelieve -ErrorAction SilentlyContinue | Where-Object {
        $_.SessionId -eq [Diagnostics.Process]::GetCurrentProcess().SessionId
    })
}

function Set-ExplorerEntry([string]$AppPath, [string]$DataDirectory, [string]$BackupDirectory,
                          [Microsoft.Win32.RegistryKey]$RegistryRoot = [Microsoft.Win32.Registry]::CurrentUser) {
    $id = '{8DABAE71-6BF7-4EA9-89B0-7931164542EB}'
    $clsid = 'Software\Classes\CLSID\' + $id
    $namespace = 'Software\Microsoft\Windows\CurrentVersion\Explorer\Desktop\NameSpace\' + $id
    foreach ($key in @($clsid, $namespace)) {
        $existing = $RegistryRoot.OpenSubKey($key)
        if ($existing) {
            $existing.Dispose()
            $name = if ($key -eq $clsid) { 'explorador-clsid.reg' } else { 'explorador-namespace.reg' }
            $saved = Invoke-BoundedProgram "$env:SystemRoot\System32\reg.exe" @('export', ($RegistryRoot.Name+'\'+$key), (Join-Path $BackupDirectory $name), '/y')
            if ($saved.ExitCode) { throw 'No se pudo respaldar el acceso del Explorador.' }
        }
    }
    $hub = Join-Path $DataDirectory 'Explorer'
    [IO.Directory]::CreateDirectory($hub) | Out-Null
    $values = @(
        @($clsid, '', 'Remote Believe'), @($clsid, 'System.IsPinnedToNameSpaceTree', 1),
        @($clsid, 'SortOrderIndex', 66), @(($clsid+'\DefaultIcon'), '', ($AppPath+',0')),
        @(($clsid+'\InProcServer32'), '', (Join-Path $env:SystemRoot 'System32\shell32.dll')),
        @(($clsid+'\Instance'), 'CLSID', '{0E5AAE11-A475-4c5b-AB00-C66DE400274E}'),
        @(($clsid+'\Instance\InitPropertyBag'), 'Attributes', 17),
        @(($clsid+'\Instance\InitPropertyBag'), 'TargetFolderPath', $hub),
        @(($clsid+'\ShellFolder'), 'FolderValueFlags', 40),
        @(($clsid+'\ShellFolder'), 'Attributes', -260046771), @($namespace, '', 'Remote Believe'))
    foreach ($value in $values) {
        $key = $RegistryRoot.CreateSubKey($value[0])
        try {
            $kind = if ($value[2] -is [int]) { [Microsoft.Win32.RegistryValueKind]::DWord } else { [Microsoft.Win32.RegistryValueKind]::String }
            $key.SetValue($value[1], $value[2], $kind)
        } finally { $key.Dispose() }
    }
    if ($RegistryRoot.Name -eq 'HKEY_CURRENT_USER') {
        [BelieveSupport]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
    }
}

function Repair-Compatibility([string]$AppPath, [string]$BackupDirectory,
                              [Microsoft.Win32.RegistryKey]$RegistryRoot = [Microsoft.Win32.Registry]::CurrentUser) {
    $keyPath = 'Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers'
    $key = $RegistryRoot.OpenSubKey($keyPath, $true)
    if (-not $key) { return }
    try {
        $value = [string]$key.GetValue($AppPath)
        if ($value -match '(?i)(^|\s)RUNASADMIN(?=\s|$)') {
            @{ Key=$keyPath; Name=$AppPath; Value=$value } | ConvertTo-Json |
                Set-Content -LiteralPath (Join-Path $BackupDirectory 'compatibilidad-anterior.json') -Encoding UTF8
            # Other compatibility flags remain intact.
            $updated = [regex]::Replace($value, '(?i)(?<=\s|^)RUNASADMIN(?=\s|$)', '').Trim()
            if (-not $updated -or $updated -eq '~') { $key.DeleteValue($AppPath) }
            else { $key.SetValue($AppPath, $updated, [Microsoft.Win32.RegistryValueKind]::String) }
            Write-Report 'Reparado: desactivado Ejecutar como administrador SOLO para RemoteBelieve.exe en este perfil.'
        }
    } finally { $key.Dispose() }
}

function Repair-OwnedShortcut([string]$ShortcutPath, [string]$AppPath, [string]$BackupDirectory) {
    if (-not [IO.File]::Exists($ShortcutPath)) { return $false }
    $shell = New-Object -ComObject WScript.Shell
    try { $target = $shell.CreateShortcut($ShortcutPath).TargetPath }
    finally { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell) }
    if (-not [string]::Equals($target, $AppPath, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    $bytes = [IO.File]::ReadAllBytes($ShortcutPath)
    if ($bytes.Length -lt 76 -or [BitConverter]::ToUInt32($bytes, 0) -ne 76) { return $false }
    $flags = [BitConverter]::ToUInt32($bytes, 20)
    if (($flags -band 8192) -eq 0) { return $false }
    $backup = Join-Path $BackupDirectory ('acceso-anterior-'+[guid]::NewGuid().ToString('N')+'.lnk')
    Copy-Item -LiteralPath $ShortcutPath -Destination $backup
    [Array]::Copy([BitConverter]::GetBytes([uint32]($flags -bxor 8192)), 0, $bytes, 20, 4)
    [IO.File]::WriteAllBytes($ShortcutPath, $bytes)
    return $true
}

function Invoke-BelieveSupport {
    $runId = [guid]::NewGuid().ToString('N')
    $runDirectory = Join-Path $env:LOCALAPPDATA ('RemoteBelieveSupport\' + $runId)
    [IO.Directory]::CreateDirectory($runDirectory) | Out-Null
    $script:ReportPath = Join-Path $runDirectory 'Diagnostico-RemoteBelieve.txt'
    Write-Report ('Remote Believe - soporte 1.0 / ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
    Write-Report ('Modo: ' + $(if ($SoloDiagnostico) { 'solo diagnostico' } else { 'reparacion local' }))
    Write-Report 'No se recopilan claves ni tokens. Los archivos, pendientes, permisos y Tailscale se conservan.'
    try {
        Add-Type -Path (Join-Path $PSScriptRoot 'RemoteBelieve.Support.cs')
        $session = [Diagnostics.Process]::GetCurrentProcess().SessionId
        $elevated = [BelieveSupport]::Elevated($PID)
        Write-Report ('Windows: ' + [Environment]::OSVersion.Version + '; x64=' + [Environment]::Is64BitOperatingSystem + '; PowerShell=' + $PSVersionTable.PSVersion)
        Write-Report ('Sesion Windows=' + $session + '; script elevado=' + $elevated)
        if (-not $SoloDiagnostico -and $elevated -ne 0) {
            Write-Report 'DETENIDO: cierra esta ventana y abre Reparar-RemoteBelieve.cmd con doble clic normal, sin Ejecutar como administrador. La elevacion se pedira aparte solo si el controlador la necesita.'
            return
        }
        $explorers = @(Get-Process -Name explorer -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $session })
        $sameExplorer = @($explorers | Where-Object {
            [BelieveSupport]::UserSid($_.Id) -eq [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -and
            [BelieveSupport]::Elevated($_.Id) -eq $elevated
        }).Count -gt 0
        Write-Report ('Script en el mismo usuario y nivel del Explorador: ' + $sameExplorer)
        if (-not $SoloDiagnostico -and -not $sameExplorer) {
            throw 'No se pudo comprobar la sesion del Explorador. Abre el script desde el Explorador del freelance con doble clic normal.'
        }
        $dataDirectory = Join-Path $env:APPDATA 'RemoteBelieveMesh'
        $appPath = Join-Path $env:LOCALAPPDATA 'Programs\Remote Believe\RemoteBelieve.exe'
        $settingsPath = Join-Path $dataDirectory 'settings.json'
        if (-not [IO.File]::Exists($settingsPath)) { throw 'No hay configuracion de Believe en este perfil de Windows. Ejecuta el script desde la cuenta de Windows del freelance.' }
        $settings = [IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
        Write-Report ('Configuracion: modo=' + $settings.mode + '; perfil Believe separado=' + [bool]$settings.active_account)
        $cache = Get-ActiveCache $settings
        if (-not $cache) { throw 'La ruta de cache o el perfil activo no son validos. Se conservan para revisar el informe; no se asignaran archivos a otra cuenta.' }
        Write-Report ('Cache activa: ' + $cache)
        $volume = [BelieveSupport]::Volume([IO.Path]::GetPathRoot($cache))
        Write-Report ('Volumen de cache: ' + $volume)
        Write-Report ('Proveedor en cache: ' + [BelieveSupport]::Provider($cache))
        if ([IO.File]::Exists($appPath)) { Write-Report ('Cliente instalado SHA256: ' + (Get-FileHash -LiteralPath $appPath -Algorithm SHA256).Hash) }
        else { Write-Report ('AVISO: no se encontro el cliente en ' + $appPath) }
        $compatPath = 'Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers'
        $globalRunAsAdmin = $false
        foreach ($hive in @([Microsoft.Win32.Registry]::CurrentUser, [Microsoft.Win32.Registry]::LocalMachine)) {
            $key = $hive.OpenSubKey($compatPath)
            if ($key) {
                try {
                    $flags = [string]$key.GetValue($appPath)
                    if ($flags) { Write-Report ('Compatibilidad '+$hive.Name+': '+$flags) }
                    if ($hive.Name -eq 'HKEY_LOCAL_MACHINE' -and $flags -match '(?i)(^|\s)RUNASADMIN(?=\s|$)') { $globalRunAsAdmin=$true }
                } finally { $key.Dispose() }
            }
        }
        foreach ($process in (Get-BelieveProcesses)) {
            Write-Report ('Believe activo: PID=' + $process.Id + '; elevado=' + [BelieveSupport]::Elevated($process.Id))
        }
        if (-not $SoloDiagnostico) {
            Write-Host ''
            Write-Host 'Guarda tu trabajo y espera a que terminen las transferencias.'
            Write-Host 'Cierra Remote Believe desde su icono junto al reloj > Salir (la X solo lo oculta).'
            if (-not $SinPausa) { $null = Read-Host 'Cuando lo hayas cerrado, presiona Enter' }
            if (@(Get-BelieveProcesses).Count) {
                Write-Report 'DETENIDO: Believe sigue abierto. No se forzara su cierre. Cierralo desde la bandeja y vuelve a ejecutar la reparacion.'
                return
            }
            if ($settings.mode -ne 'home') {
                Write-Report 'DETENIDO: este perfil esta en modo Oficina. Esta reparacion es para el PC freelance en modo Casa.'
                return
            }
            # The application could have saved state when it closed. Reload, never overwrite it.
            $settings = [IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
            $cache = Get-ActiveCache $settings
            $volume = if ($cache) { [BelieveSupport]::Volume([IO.Path]::GetPathRoot($cache)) } else { '' }
            if (-not $cache -or $volume -ne 'NTFS') { throw 'Se necesita una cache valida en NTFS local. No se moveran los archivos automaticamente.' }
            if ([IO.Directory]::Exists($cache)) {
                $probeFile = Join-Path $cache ('.comprobar-permisos-believe-'+$runId+'.tmp')
                $stream = New-Object IO.FileStream($probeFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
                    [IO.FileShare]::None, 4096, [IO.FileOptions]::DeleteOnClose)
                try { $stream.WriteByte(0) } finally { $stream.Dispose() }
                Write-Report 'Permisos de escritura en la cache: OK (prueba temporal eliminada al cerrar).'
            } else { Write-Report 'La cache no existe actualmente. Believe recreara el catalogo despues de iniciar sesion; el script no recupera archivos eliminados.' }
        }
        $metadataDirectory = $dataDirectory
        if ($settings.active_account) { $metadataDirectory = Join-Path $dataDirectory ('accounts\' + $settings.active_account) }
        foreach ($name in @('manifest.json', 'outbox.json', 'preferences.json')) {
            $path = Join-Path $metadataDirectory $name
            if ([IO.File]::Exists($path)) { Write-Report ($name + ': ' + (Get-Item -LiteralPath $path).Length + ' bytes (contenido privado conservado)') }
        }
        foreach ($letter in @('X', 'R')) {
            $device = [BelieveSupport]::Device($letter)
            $action = Get-DriveAction $device @(Get-OwnedDriveTargets $settings $letter)
            Write-Report ($letter + ': ' + $action + '; destino Windows=' + $device)
            if ($device) {
                try { Write-Report ($letter + ': sistema de archivos=' + [BelieveSupport]::Volume($letter+':\')) }
                catch { Write-Report ($letter + ': no se puede consultar el volumen: ' + $_.Exception.Message) }
            }
            if (-not $SoloDiagnostico -and $action -eq 'Believe') {
                # Recheck immediately; only remove an exact known Believe SUBST, never a real disk/junction.
                if ([BelieveSupport]::Device($letter) -cne $device -or @(Get-BelieveProcesses).Count) {
                    throw 'Las unidades o el programa cambiaron durante la reparacion; vuelve a intentarlo con Believe cerrado.'
                }
                $removed = Invoke-BoundedProgram "$env:SystemRoot\System32\subst.exe" @(($letter+':'), '/d')
                if ($removed.ExitCode -ne 0) { throw ('No se pudo desconectar el montaje de Believe en '+$letter+': '+$removed.Error) }
                Write-Report ($letter + ': montaje anterior de Believe desconectado; la app lo recreara al iniciar sesion. Los archivos siguen en la cache.')
            } elseif ($action -eq 'Ajena') {
                Write-Report ($letter + ': se conserva la unidad existente. Believe debe enlazar solamente sus carpetas autorizadas si el disco es NTFS local.')
            }
        }
        foreach ($scope in @('HKCU:', 'HKLM:')) {
            $policy = Get-ItemProperty -LiteralPath ($scope+'\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer') -ErrorAction SilentlyContinue
            foreach ($name in @('NoDrives', 'NoViewOnDrive')) {
                if ($policy -and $policy.PSObject.Properties[$name]) { Write-Report ('Politica Windows '+$scope+' '+$name+'='+$policy.$name+' (se conserva)') }
            }
        }
        $service = Get-Service -Name CldFlt -ErrorAction SilentlyContinue
        if ($service) {
            $start = (Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\CldFlt' -Name Start).Start
            Write-Report ('Controlador CldFlt: '+$service.Status+'; Start='+$start)
            if (-not $SoloDiagnostico -and ($service.Status -ne 'Running' -or $start -eq 4)) {
                Write-Report 'Se solicitara permiso de Windows para activar SOLO el controlador de archivos bajo demanda.'
                $arguments = ConvertTo-NativeArguments @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'Activar-ArchivosNube.ps1'), '-RunId', $runId)
                $helper = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru
                try {
                    if (-not $helper.WaitForExit(45000)) { throw 'La activacion sigue pendiente. Espera a que termine y vuelve a ejecutar el diagnostico.' }
                    $resultPath = Join-Path $env:ProgramData ('RemoteBelieveSupport\'+$runId+'.json')
                    $result = [IO.File]::ReadAllText($resultPath) | ConvertFrom-Json
                    Write-Report ('Controlador: Start anterior='+$result.BeforeStart+'; Start final='+$result.AfterStart+'; estado='+$result.Status+'; error='+$result.Error)
                    if ($helper.ExitCode -ne 0 -or $result.Error) { throw 'No se pudo activar CldFlt. Revisa el resultado anterior; puede requerir soporte de Windows.' }
                } finally { $helper.Dispose() }
            }
        } else { Write-Report 'AVISO: Windows no tiene el controlador CldFlt disponible. Requiere revisar Windows; reinstalar Believe no agrega ese componente.' }
        $platform = New-Object BelieveSupport+Platform
        $hr = [BelieveSupport]::CfGetPlatformInfo([ref]$platform)
        Write-Report ('Cloud Files: HRESULT=0x'+$hr.ToString('X8')+'; build='+$platform.Build+'; revision='+$platform.Revision+'; integracion='+$platform.Integration)
        $officeIP = [string]$settings.office_ip
        $parsedIP = $null
        if ([Net.IPAddress]::TryParse($officeIP, [ref]$parsedIP) -and $parsedIP.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork) {
            try {
                $request = [Net.HttpWebRequest]::Create('http://'+$officeIP+':8090/ping')
                $request.Proxy=$null; $request.Timeout=4000; $request.ReadWriteTimeout=4000; $request.AllowAutoRedirect=$false
                $response = $request.GetResponse()
                try {
                    $reader = New-Object IO.StreamReader($response.GetResponseStream())
                    try { $ping = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
                    Write-Report ('Servidor Oficina '+$officeIP+':8090 responde; ok='+[bool]$ping.ok+'; version='+[string]$ping.version)
                } finally { $response.Dispose() }
            } catch { Write-Report ('AVISO: Oficina '+$officeIP+':8090 no responde al ping: '+$_.Exception.Message) }
        } else { Write-Report 'IP de Oficina sin configurar o no es IPv4; comprobar la direccion en la pantalla de acceso.' }
        if (-not $SoloDiagnostico) {
            if (@(Get-BelieveProcesses).Count) { throw 'Believe se abrio durante la reparacion. Cierralo antes de repetirla.' }
            Repair-Compatibility $appPath $runDirectory
            foreach ($shortcut in @(
                (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Remote Believe.lnk'),
                (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Remote Believe\Remote Believe.lnk'),
                (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Remote Believe.lnk'))) {
                if (Repair-OwnedShortcut $shortcut $appPath $runDirectory) { Write-Report ('Reparado acceso sin elevacion: '+$shortcut) }
            }
            Set-ExplorerEntry $appPath $dataDirectory $runDirectory
            Write-Report 'Reparado: acceso de Remote Believe al Explorador de este perfil de Windows.'
            # Run the driver registration test separately, with a hard timeout and no real cache changes.
            $probe = Invoke-BoundedProgram "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-ProbeDirectory', $runDirectory) 20
            Write-Report ('Prueba aislada de archivos bajo demanda: '+$probe.Output+' '+$probe.Error)
            if ($probe.ExitCode -ne 0) { Write-Report 'AVISO: la prueba de Windows no pudo terminar correctamente. Revisa el informe si la conexion sigue fallando.' }
            Write-Report 'Reparacion local terminada. Falta comprobar el montaje real iniciando sesion en Believe.'
            if ($globalRunAsAdmin) {
                Write-Report 'PENDIENTE: Windows obliga a abrir Believe como administrador para todos los usuarios. En Propiedades de RemoteBelieve.exe > Compatibilidad > Cambiar configuracion para todos los usuarios, desactiva Ejecutar como administrador y luego abre Believe normalmente.'
            } elseif (-not $NoAbrir -and [IO.File]::Exists($appPath)) {
                Start-Process -FilePath $appPath | Out-Null
                Write-Report 'Believe abierto desde la sesion normal. Inicia sesion manualmente en modo Casa; despues revisa X:/R:.'
            }
        } else { Write-Report 'Diagnostico terminado. No se cambiaron montajes, configuracion, archivos de proyectos ni servicios.' }
    } catch { Write-Report ('PENDIENTE: '+$_.Exception.Message) }
    finally {
        Write-Report ('Informe para soporte: '+$script:ReportPath)
        Write-Host 'Si sigue fallando, envia ese archivo Diagnostico-RemoteBelieve.txt a Gonzalo.'
        $desktop = [Environment]::GetFolderPath('Desktop')
        if ($desktop -and [IO.Directory]::Exists($desktop)) {
            try {
                $copy = Join-Path $desktop ('Diagnostico-RemoteBelieve-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+$runId.Substring(0,6)+'.txt')
                Copy-Item -LiteralPath $script:ReportPath -Destination $copy -ErrorAction Stop
                Write-Host ('Tambien guardado en el Escritorio: '+$copy)
            } catch { Write-Host 'No se pudo copiar al Escritorio; el informe sigue disponible en la ruta anterior.' }
        }
    }
}

# Dot-sourcing imports the pure helpers for tests without performing any repair.
if ($MyInvocation.InvocationName -ne '.') {
    if ($ProbeDirectory) {
        try {
            $candidate = Get-LocalCachePath $ProbeDirectory
            $parent = Join-Path $env:LOCALAPPDATA 'RemoteBelieveSupport'
            if (-not $candidate -or [IO.Path]::GetDirectoryName($candidate) -ine $parent -or
                [IO.Path]::GetFileName($candidate) -notmatch '^[a-f0-9]{32}$') { throw 'Directorio de prueba no valido.' }
            Add-Type -Path (Join-Path $PSScriptRoot 'RemoteBelieve.Support.cs')
            $result = [BelieveSupport]::Probe($candidate)
            Write-Output $result
            if ($result -notlike 'CfRegisterSyncRoot=0x00000000; CfUnregisterSyncRoot=0x00000000') { exit 1 }
            exit 0
        } catch { Write-Output $_.Exception.Message; exit 1 }
    }
    Invoke-BelieveSupport
}
