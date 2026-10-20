param([string]$OutputDirectory = "")
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repoRoot 'client\native' }
$buildDirectory = Join-Path $repoRoot ('build\native-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $OutputDirectory, $buildDirectory -Force | Out-Null
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$visualStudio = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $visualStudio) { throw 'Se necesitan las herramientas C++ de Visual Studio para compilar el proveedor.' }
$vcvars = Join-Path $visualStudio 'VC\Auxiliary\Build\vcvars64.bat'
$source = Join-Path $repoRoot 'native\cloud_bridge.cpp'
$output = Join-Path $OutputDirectory 'RemoteBelieveCloud.dll'
$commandPath = Join-Path $buildDirectory 'build-bridge.cmd'
$command = @"
@echo off
call "$vcvars" >nul
if errorlevel 1 exit /b 1
cl /nologo /std:c++17 /EHsc /W4 /O2 /LD /MT /DUNICODE /D_UNICODE /DWIN32_LEAN_AND_MEAN /DNOMINMAX /DNTDDI_VERSION=0x0A00000C "$source" /Fe:"$output" /Fo:"$buildDirectory\cloud_bridge.obj" /link cldapi.lib ole32.lib shell32.lib windowsapp.lib runtimeobject.lib /IMPLIB:"$buildDirectory\RemoteBelieveCloud.lib"
"@
[IO.File]::WriteAllText($commandPath, $command, [Text.Encoding]::Default)
& $env:ComSpec /d /c $commandPath
if ($LASTEXITCODE -ne 0) { throw 'Fallo la compilacion de Cloud Files.' }
Write-Host "Proveedor compilado: $output"
