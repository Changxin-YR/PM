param([ValidateSet('setup','test','api','check','web','browser','bootstrap','migrate','outbox','dead-letters','rules','analysis')] [string]$Action='api')
$ErrorActionPreference='Stop'
Set-Location (Join-Path $PSScriptRoot '..')
function Invoke-Checked([scriptblock]$Command) {
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "Command exited $LASTEXITCODE" }
}
$file = '.local/dev.env'
if ($Action -eq 'setup') {
  New-Item -ItemType Directory -Force .local | Out-Null
  if (-not (Test-Path -LiteralPath $file)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $value = [BitConverter]::ToString($bytes).Replace('-','')
    [IO.File]::WriteAllText((Join-Path $PWD $file),"PMO_DB_PASSWORD=$value")
  }
  Invoke-Checked { uv sync --locked }
}
$line=(Get-Content -Raw -LiteralPath $file).Trim()
if ($line -notmatch '^PMO_DB_PASSWORD=([0-9a-fA-F]{64})$') { throw 'Invalid local settings' }
$env:DATABASE_URL="postgresql+psycopg://pmo:$($matches[1])@127.0.0.1:55439/pmo"
$env:PMO_TEST_DATABASE_URL=$env:DATABASE_URL
$env:PYTHONUTF8='1'
$env:PMO_LOCAL_HTTP='1'
$python='.venv/Scripts/python.exe'
switch ($Action) {
  setup {
    Invoke-Checked { docker compose --env-file $file -p pmo104dev up -d --wait db }
    Invoke-Checked { & $python -m alembic upgrade head }
  }
  test { Invoke-Checked { & $python -m pytest -q } }
  migrate { Invoke-Checked { & $python -m alembic upgrade head } }
  outbox { Invoke-Checked { & $python -m pmo.outbox } }
  rules { Invoke-Checked { & $python -m pmo.rule_scan } }
  analysis { Invoke-Checked { & $python -m pmo.report_analysis_worker } }
  dead-letters { Invoke-Checked { & $python -m pmo.outbox --dead-letters } }
  browser { Invoke-Checked { & $python -m pytest -q tests/browser_case.py } }
  web { Invoke-Checked { npm.cmd --prefix web run dev } }
  bootstrap { $org=Read-Host 'Organization'; $user=Read-Host 'Username'; $name=Read-Host 'Display name'; Invoke-Checked { & $python -m pmo.bootstrap --organization $org --username $user --name $name } }
  check {
    foreach ($prefix in '08','15','19') {
      $script=(Get-Item "docs/spec/$prefix*.py").FullName
      Invoke-Checked { & $python $script }
    }
    Invoke-Checked { & $python -m mypy pmo --ignore-missing-imports }
  }
  api { Invoke-Checked { & $python -m uvicorn pmo.api:app --host 127.0.0.1 --port 8000 --reload --reload-dir pmo --no-access-log } }
}
