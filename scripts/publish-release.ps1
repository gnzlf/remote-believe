param([Parameter(Mandatory=$true)][string]$Version, [string]$NotesFile, [switch]$SkipBuild)
$ErrorActionPreference='Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Usa una version como 3.1.0.' }
$root=Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $client=Get-Content -LiteralPath (Join-Path $root 'client\mesh_client.py') -Raw
    if ($client -notmatch ('APP_VERSION\s*=\s*"'+[regex]::Escape($Version)+'-explorer"')) { throw 'Primero cambia APP_VERSION en cliente, instalador y proveedor nativo.' }
    python -m unittest discover -s tests -q
    if ($LASTEXITCODE) { throw 'Fallaron las pruebas; no se publicara una actualizacion obligatoria.' }
    if (-not $SkipBuild) { & (Join-Path $PSScriptRoot 'build-windows-installer.ps1') -Release $Version -SkipDependencyInstall }
    python (Join-Path $PSScriptRoot 'check-release.py') $Version
    if ($LASTEXITCODE) { throw 'No se pudo verificar el instalador.' }
    $assets=@((Join-Path $root "dist\$Version\RemoteBelieveSetup.exe"), (Join-Path $root "dist\$Version\RemoteBelieveSetup.exe.sha256"), (Join-Path $root "dist\$Version\update.json"))
    $arguments=@('release','create',"v$Version",'--repo','gnzlf/remote-believe','--draft','--title',"Remote Believe $Version")+$assets
    if ($NotesFile) { $arguments+=@('--notes-file',(Resolve-Path -LiteralPath $NotesFile).Path) }
    else { $arguments+=@('--generate-notes') }
    & gh @arguments
    if ($LASTEXITCODE) { throw 'No se pudo preparar el borrador de la release.' }
    gh release view "v$Version" --repo gnzlf/remote-believe --json assets
    Write-Host 'El borrador esta listo. Publicalo en GitHub cuando el instalador haya sido revisado.'
    Write-Host 'Solo una release estable publicada activa el bloqueo y la actualizacion en los PCs.'
} finally { Pop-Location }
