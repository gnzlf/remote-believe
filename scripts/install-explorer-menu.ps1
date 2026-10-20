$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$clientScript = Join-Path $repoRoot "client\mesh_client.py"

if (-not (Test-Path -LiteralPath $clientScript)) {
    throw "No se encontro mesh_client.py en $clientScript"
}

function Get-PythonLaunchPrefix {
    $pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonw) {
        return "`"$($pythonw.Source)`" `"$clientScript`""
    }

    $pyw = Get-Command pyw.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyw) {
        return "`"$($pyw.Source)`" -3 `"$clientScript`""
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($python) {
        return "`"$($python.Source)`" `"$clientScript`""
    }

    throw "No se encontro pythonw.exe, pyw.exe ni python.exe en PATH."
}

function Set-ExplorerVerb($keyPath, $label, $command) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($keyPath)
    $key.SetValue("", $label, [Microsoft.Win32.RegistryValueKind]::String)
    $key.SetValue("MUIVerb", $label, [Microsoft.Win32.RegistryValueKind]::String)
    $key.SetValue("Icon", "shell32.dll,16739", [Microsoft.Win32.RegistryValueKind]::String)
    $key.Close()

    $commandKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("$keyPath\command")
    $commandKey.SetValue("", $command, [Microsoft.Win32.RegistryValueKind]::String)
    $commandKey.Close()
}

$prefix = Get-PythonLaunchPrefix
$downloadCommand = "$prefix --explorer-download `"%1`""
$prepareCommand = "$prefix --explorer-prepare `"%1`""

Set-ExplorerVerb "Software\Classes\*\shell\RemoteBelieveDownload" "Remote Believe: descargar ahora" $downloadCommand
Set-ExplorerVerb "Software\Classes\Directory\shell\RemoteBelieveDownloadFolder" "Remote Believe: descargar carpeta" $downloadCommand
Set-ExplorerVerb "Software\Classes\SystemFileAssociations\.ma\shell\RemoteBelievePrepareShot" "Remote Believe: preparar shot completo" $prepareCommand

Write-Host "Menu contextual instalado."
Write-Host "En Windows 11 puede aparecer dentro de 'Mostrar mas opciones'."
Write-Host "Remote Believe debe estar abierto o en el area de notificaciones."
