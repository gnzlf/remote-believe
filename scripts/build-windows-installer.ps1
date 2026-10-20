param(
    [string]$Release = "",
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
if ($Release -and $Release -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    throw "Release debe ser un nombre de version sin separadores de ruta."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$clientDir = Join-Path $repoRoot "client"
$clientScript = Join-Path $clientDir "mesh_client.py"
$requirements = Join-Path $clientDir "requirements.txt"
$installerScript = Join-Path $repoRoot "installer\remote_believe_installer.py"
$distDir = Join-Path $repoRoot "dist"
$buildDir = Join-Path $repoRoot "build"
if ($Release) {
    $distDir = Join-Path $distDir $Release
    $buildDir = Join-Path $buildDir $Release
}
$appDistDir = Join-Path $distDir "app"

if (-not (Test-Path -LiteralPath $clientScript)) {
    throw "No se encontro $clientScript"
}
if (-not (Test-Path -LiteralPath $installerScript)) {
    throw "No se encontro $installerScript"
}

if (-not $SkipDependencyInstall) {
    Write-Host "Instalando dependencias..."
    python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "No se pudo actualizar pip" }
    python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias" }
    python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar PyInstaller" }
}

Write-Host "Compilando RemoteBelieve.exe..."
& (Join-Path $PSScriptRoot 'build-cloud-bridge.ps1')
$sourceFiles=@(Get-ChildItem -LiteralPath $clientDir -Filter '*.py') + @(
    (Get-Item -LiteralPath (Join-Path $repoRoot 'native\cloud_bridge.cpp')),
    (Get-Item -LiteralPath (Join-Path $clientDir 'native\RemoteBelieveCloud.dll')),
    (Get-Item -LiteralPath $installerScript))
$sourceHashes=@{}
foreach($sourceFile in $sourceFiles) {
    $relative=$sourceFile.FullName.Substring($repoRoot.Length+1).Replace('\','/')
    $sourceHashes[$relative]=(Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$buildInfo=Join-Path $clientDir 'build-info.json'
@{version=$Release; files=$sourceHashes} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $buildInfo -Encoding UTF8
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "RemoteBelieve" `
    --add-binary "$(Join-Path $clientDir 'native\RemoteBelieveCloud.dll');native" `
    --add-data "$(Join-Path $clientDir 'connection-defaults.json');." `
    --add-data "$buildInfo;." `
    --distpath $appDistDir `
    --workpath (Join-Path $buildDir "app") `
    --specpath $buildDir `
    $clientScript
if ($LASTEXITCODE -ne 0) { throw "Fallo la compilacion del cliente" }

$appExe = Join-Path $appDistDir "RemoteBelieve.exe"
if (-not (Test-Path -LiteralPath $appExe)) {
    throw "No se genero $appExe"
}

Write-Host "Compilando instalador..."
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "RemoteBelieveSetup" `
    --paths $clientDir `
    --add-data "$appExe;payload" `
    --distpath $distDir `
    --workpath (Join-Path $buildDir "installer") `
    --specpath $buildDir `
    $installerScript
if ($LASTEXITCODE -ne 0) { throw "Fallo la compilacion del instalador" }

$setupExe = Join-Path $distDir "RemoteBelieveSetup.exe"
if (-not (Test-Path -LiteralPath $setupExe)) {
    throw "No se genero $setupExe"
}

Write-Host ""
Write-Host "Listo:"
Write-Host "  App:        $appExe"
Write-Host "  Instalador: $setupExe"
Write-Host ""
Write-Host "Para instalar, ejecuta RemoteBelieveSetup.exe."
Write-Host "El instalador crea arranque con Windows, menu contextual y desinstalador."
