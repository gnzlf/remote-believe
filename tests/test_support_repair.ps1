# Standalone Windows PowerShell 5.1 tests; no Pester and no production writes.
$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
. (Join-Path $repository 'scripts\soporte-remoto\Reparar-RemoteBelieve.ps1')
$script:Checks = 0
function Assert-Equal($Expected, $Actual, [string]$Description) {
    if ($Expected -cne $Actual) { throw ($Description + ': esperado=['+$Expected+']; recibido=['+$Actual+']') }
    $script:Checks++
}

Assert-Equal $null (Get-LocalCachePath '\\server\share\cache') 'Rechaza UNC'
Assert-Equal $null (Get-LocalCachePath 'X:') 'Rechaza relativa a unidad'
Assert-Equal $null (Get-LocalCachePath 'C:\') 'Rechaza raiz de disco'
Assert-Equal $null (Get-LocalCachePath 'C:\cache\..\Windows') 'Rechaza traversal'
Assert-Equal $null (Get-LocalCachePath 'C:\cache\file:stream') 'Rechaza ADS'
Assert-Equal 'C:\carpeta [test]\cache' (Get-LocalCachePath 'C:\carpeta [test]\cache\') 'Mantiene rutas literales'
$id = 'a' * 64
$otherId = 'b' * 64
$settings = ('{"active_account":"'+$id+'","cache_dir":"C:\\cache-obsoleta","account_profiles":{"'+$id+'":{"cache_dir":"C:\\activa"},"'+$otherId+'":{"cache_dir":"C:\\anterior"}}}') | ConvertFrom-Json
Assert-Equal 'C:\activa' (Get-ActiveCache $settings) 'Selecciona cache de cuenta activa'
Assert-Equal 'Believe' (Get-DriveAction '\??\C:\anterior\X' @(Get-OwnedDriveTargets $settings 'X')) 'Reconoce solo destino propio exacto'
Assert-Equal 'Believe' (Get-DriveAction '\??\c:\ACTIVA\X\' @(Get-OwnedDriveTargets $settings 'X')) 'Normaliza mayusculas y barra final'
Assert-Equal 'Ajena' (Get-DriveAction '\??\C:\anterior\X\otro-trabajo' @(Get-OwnedDriveTargets $settings 'X')) 'No adopta subcarpetas'
Assert-Equal 'Ajena' (Get-DriveAction '\??\C:\anterior-falso\X' @(Get-OwnedDriveTargets $settings 'X')) 'No confunde prefijos'
Assert-Equal 'Ajena' (Get-DriveAction '\Device\HarddiskVolume2' @(Get-OwnedDriveTargets $settings 'X')) 'No toca particiones'
Assert-Equal 'Ajena' (Get-DriveAction '\Device\LanmanRedirector\X' @(Get-OwnedDriveTargets $settings 'X')) 'No toca red'
Assert-Equal 'Ajena' (Get-DriveAction '\??\C:\Otro trabajo\X' @(Get-OwnedDriveTargets $settings 'X')) 'No toca subst ajeno'
Assert-Equal 'Libre' (Get-DriveAction '' @()) 'Reconoce letra libre'
$settings.active_account = $otherId + 'invalido'
Assert-Equal $null (Get-ActiveCache $settings) 'No cae en cache obsoleta con perfil invalido'
$legacy = '{"username":"uno","cache_owner":"dos","cache_dir":"C:\\legacy"}' | ConvertFrom-Json
Assert-Equal 'Ajena' (Get-DriveAction '\??\C:\legacy\X' @(Get-OwnedDriveTargets $legacy 'X')) 'Cache sin propietario no se adopta'
$legacy.cache_owner = 'uno'
Assert-Equal 'Believe' (Get-DriveAction '\??\C:\legacy\X' @(Get-OwnedDriveTargets $legacy 'X')) 'Reconoce legacy con propietario'
Assert-Equal '"C:\carpeta con espacios\archivo.ps1" "-RunId" "abc"' (ConvertTo-NativeArguments @('C:\carpeta con espacios\archivo.ps1','-RunId','abc')) 'Cita argumentos sin shell'
$process = Invoke-BoundedProgram "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @('-NoProfile','-Command','Write-Output 123')
Assert-Equal 0 $process.ExitCode 'Hijo termina correctamente'
Assert-Equal '123' $process.Output 'Recoge salida hijo'
$timedOut = $false
try { $null = Invoke-BoundedProgram "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @('-NoProfile','-Command','Start-Sleep 10') 1 }
catch { $timedOut = $_.Exception.Message -like 'Tiempo de espera agotado*' }
Assert-Equal $true $timedOut 'El diagnostico no queda bloqueado por un hijo'
Add-Type -Path (Join-Path $repository 'scripts\soporte-remoto\RemoteBelieve.Support.cs')
Assert-Equal 'NTFS' ([BelieveSupport]::Volume('C:\')) 'Consulta real de volumen NTFS'
Assert-Equal ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value) ([BelieveSupport]::UserSid($PID)) 'Consulta del usuario de proceso'
$elevation = [BelieveSupport]::Elevated($PID)
Assert-Equal $true ($elevation -eq 0 -or $elevation -eq 1) 'Consulta elevacion de proceso'
$testId = [guid]::NewGuid().ToString('N')
$temporary = Join-Path $env:TEMP ('BelieveSupportTest-'+$testId)
$testRegistryPath = 'Software\BelieveSupportTests\'+$testId
$sandbox = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($testRegistryPath)
[IO.Directory]::CreateDirectory($temporary) | Out-Null
$script:ReportPath = Join-Path $temporary 'informe.txt'
try {
    $fakeApp = 'C:\prueba [literal]\RemoteBelieve.exe'
    Set-ExplorerEntry $fakeApp $temporary $temporary $sandbox
    $bag = $sandbox.OpenSubKey('Software\Classes\CLSID\{8DABAE71-6BF7-4EA9-89B0-7931164542EB}\Instance\InitPropertyBag')
    try { Assert-Equal (Join-Path $temporary 'Explorer') $bag.GetValue('TargetFolderPath') 'Registro apunta a carpeta correcta' }
    finally { $bag.Dispose() }
    $folder = $sandbox.OpenSubKey('Software\Classes\CLSID\{8DABAE71-6BF7-4EA9-89B0-7931164542EB}\ShellFolder')
    try { Assert-Equal -260046771 $folder.GetValue('Attributes') 'Atributos DWORD validos' }
    finally { $folder.Dispose() }
    Set-ExplorerEntry $fakeApp $temporary $temporary $sandbox
    Assert-Equal $true ([IO.File]::Exists((Join-Path $temporary 'explorador-clsid.reg'))) 'Respalda registro existente antes de modificar'
    $layers = $sandbox.CreateSubKey('Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers')
    $layers.SetValue($fakeApp, '~ RUNASADMIN HIGHDPIAWARE')
    $layers.SetValue('C:\Otro.exe', '~ RUNASADMIN')
    Repair-Compatibility $fakeApp $temporary $sandbox
    Assert-Equal '~  HIGHDPIAWARE' $layers.GetValue($fakeApp) 'Quita solo RUNASADMIN'
    Assert-Equal '~ RUNASADMIN' $layers.GetValue('C:\Otro.exe') 'Conserva compatibilidad ajena'
    Assert-Equal $true ([IO.File]::Exists((Join-Path $temporary 'compatibilidad-anterior.json'))) 'Respalda compatibilidad'
    $layers.Dispose()
    $shortcutPath = Join-Path $temporary 'acceso.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $fakeApp
    $shortcut.Save()
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    $bytes = [IO.File]::ReadAllBytes($shortcutPath)
    $bytes[21] = $bytes[21] -bor 32
    [IO.File]::WriteAllBytes($shortcutPath, $bytes)
    Assert-Equal $false (Repair-OwnedShortcut $shortcutPath 'C:\otro.exe' $temporary) 'No modifica acceso ajeno'
    Assert-Equal $true (Repair-OwnedShortcut $shortcutPath $fakeApp $temporary) 'Quita elevacion del acceso propio'
    $fixed = [IO.File]::ReadAllBytes($shortcutPath)
    Assert-Equal 0 ($fixed[21] -band 32) 'Bit de elevacion quitado'
    $fixed[21] = $fixed[21] -bor 32
    Assert-Equal ([Convert]::ToBase64String($bytes)) ([Convert]::ToBase64String($fixed)) 'Conserva todos los otros bytes del acceso'
} finally {
    $sandbox.Dispose()
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($testRegistryPath, $false)
    $fullTemporary = [IO.Path]::GetFullPath($temporary)
    if ([IO.Path]::GetDirectoryName($fullTemporary) -ine [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') -or
        [IO.Path]::GetFileName($fullTemporary) -ne ('BelieveSupportTest-'+$testId)) { throw 'Ruta temporal inesperada; no se borrara.' }
    Remove-Item -LiteralPath $fullTemporary -Recurse -Force
}
Write-Output ("OK: $script:Checks comprobaciones. No se modificaron montajes ni proyectos.")
