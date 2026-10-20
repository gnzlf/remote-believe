$ErrorActionPreference = "Stop"

param(
    [string[]]$Roots = @("X:\", "R:\"),
    [string]$Out = ""
)

if (-not $Out) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $Out = Join-Path (Get-Location) "zero-byte-files-$stamp.csv"
}

$results = New-Object System.Collections.Generic.List[object]

foreach ($root in $Roots) {
    if (-not (Test-Path -LiteralPath $root)) {
        Write-Warning "No existe o no esta montado: $root"
        continue
    }

    Write-Host "Revisando $root ..."
    Get-ChildItem -LiteralPath $root -File -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -eq 0 } |
        ForEach-Object {
            $results.Add([pscustomobject]@{
                FullName      = $_.FullName
                Length        = $_.Length
                LastWriteTime = $_.LastWriteTime
                CreationTime  = $_.CreationTime
            })
        }
}

$results |
    Sort-Object FullName |
    Export-Csv -LiteralPath $Out -NoTypeInformation -Encoding UTF8

Write-Host ""
Write-Host "Encontrados: $($results.Count)"
Write-Host "Reporte: $Out"
