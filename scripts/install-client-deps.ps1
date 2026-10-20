$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$requirements = Join-Path $repoRoot "client\requirements.txt"

if (-not (Test-Path -LiteralPath $requirements)) {
    throw "No se encontro requirements.txt en $requirements"
}

Write-Host "Instalando dependencias de Remote Believe..."
python -m pip install --upgrade pip
python -m pip install -r $requirements
Write-Host "Dependencias instaladas."
