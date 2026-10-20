param([Parameter(Mandatory=$true)][string]$RunId)
$ErrorActionPreference = 'Stop'
if ($RunId -notmatch '^[a-f0-9]{32}$') { exit 2 }
$result = [ordered]@{ BeforeStart=$null; AfterStart=$null; Status=''; Error='' }
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Se necesita elevacion para activar el controlador.'
    }
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\CldFlt'
    $result.BeforeStart = [int](Get-ItemProperty -LiteralPath $key -Name Start).Start
    # Only a disabled driver needs a configuration change. Other start modes are preserved.
    if ($result.BeforeStart -eq 4) {
        & "$env:SystemRoot\System32\sc.exe" config CldFlt start= auto | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "sc config fallo: $LASTEXITCODE" }
    }
    $service = Get-Service -Name CldFlt
    if ($service.Status -ne 'Running') {
        & "$env:SystemRoot\System32\sc.exe" start CldFlt | Out-Null
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1056) { throw "sc start fallo: $LASTEXITCODE" }
        $service.WaitForStatus([ServiceProcess.ServiceControllerStatus]::Running, [TimeSpan]::FromSeconds(15))
    }
    $result.AfterStart = [int](Get-ItemProperty -LiteralPath $key -Name Start).Start
    $result.Status = (Get-Service -Name CldFlt).Status.ToString()
} catch { $result.Error = $_.Exception.Message }
$directory = Join-Path $env:ProgramData 'RemoteBelieveSupport'
[IO.Directory]::CreateDirectory($directory) | Out-Null
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $directory ($RunId + '.json')) -Encoding UTF8
if ($result.Error) { exit 1 }
exit 0
