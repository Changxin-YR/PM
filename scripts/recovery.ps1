param(
  [Parameter(Mandatory)] [ValidateSet('backup','restore-drill')] [string]$Action,
  [Parameter(Mandatory)] [string]$OutputDirectory,
  [string]$Point,
  [switch]$TrustedBackup
)
$ErrorActionPreference='Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory)
$argsForRecovery = @('-m', 'pmo.recovery', '--dev-compose', $Action)
if ($Action -eq 'restore-drill') {
  if (-not ($Point -and $TrustedBackup)) { throw 'Choose an explicit local trusted snapshot and pass -TrustedBackup' }
  $argsForRecovery += @((Resolve-Path -LiteralPath $Point).Path, $outputPath, '--trusted-backup')
} else {
  $argsForRecovery += $outputPath
}
$line=(Get-Content -Raw -LiteralPath (Join-Path $repo '.local/dev.env')).Trim()
if ($line -notmatch '^PMO_DB_PASSWORD=([0-9a-fA-F]{64})$') { throw 'Invalid local settings' }
$url="postgresql+psycopg://pmo:$($matches[1])@127.0.0.1:55439/pmo"
$oldSource=$env:DATABASE_URL
$oldTarget=$env:PMO_RESTORE_ADMIN_URL
Push-Location -LiteralPath $repo
try {
  if ($Action -eq 'backup') { $env:DATABASE_URL=$url } else { $env:PMO_RESTORE_ADMIN_URL=$url }
  & .venv/Scripts/python.exe @argsForRecovery
  $result=$LASTEXITCODE
} finally {
  $env:DATABASE_URL=$oldSource
  $env:PMO_RESTORE_ADMIN_URL=$oldTarget
  Pop-Location
}
exit $result
