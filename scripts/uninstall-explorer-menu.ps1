$ErrorActionPreference = "Stop"

$keys = @(
    "Software\Classes\*\shell\RemoteBelieveDownload",
    "Software\Classes\Directory\shell\RemoteBelieveDownloadFolder",
    "Software\Classes\SystemFileAssociations\.ma\shell\RemoteBelievePrepareShot"
)

foreach ($key in $keys) {
    [Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree($key, $false)
}

Write-Host "Menu contextual de Remote Believe eliminado."
