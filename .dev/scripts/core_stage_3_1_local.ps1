param(
    [ValidateSet('configure','serve','migrate','check','verify')]
    [string]$Action = 'configure',
    [string]$PgHost = '127.0.1.49',
    [int]$Port = 8011
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$localRoot = Join-Path $projectRoot '.dev/local/core-stage-3.1'
$pythonPath = Join-Path $localRoot 'venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) {
    throw 'Create the project Python environment as described in the local instructions.'
}
New-Item -ItemType Directory -Path (Join-Path $localRoot 'tmp') -Force | Out-Null
if (!$env:BERVEL_DATABASE_URL) {
    $env:BERVEL_DATABASE_URL = "postgresql+psycopg://bervel_core_stage31@${PgHost}:5432/bervel_core_stage31"
}
if (!$env:BERVEL_TEST_DATABASE_URL) {
    $env:BERVEL_TEST_DATABASE_URL = "postgresql+psycopg://bervel_core_stage31@${PgHost}:5432/bervel_core_stage31_test"
}
$env:BERVEL_OBJECT_ROOT = Join-Path $localRoot 'objects'
$env:BERVEL_PORT = [string]$Port
$env:TEMP = Join-Path $localRoot 'tmp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
if (!$env:BERVEL_ACCESS_KEYS) {
    $permissions = @('module:read','artifact:read','artifact:write','dataset:read','dataset:publish')
    $env:BERVEL_ACCESS_KEYS = @(
        @{key=[guid]::NewGuid().ToString('N'); actor_id='local-user-a'; data_scope='test-area-a'; permissions=$permissions},
        @{key=[guid]::NewGuid().ToString('N'); actor_id='local-user-b'; data_scope='test-area-b'; permissions=$permissions}
    ) | ConvertTo-Json -Depth 4 -Compress
}
if ($Action -eq 'configure') {
    Write-Output 'Configured current terminal. Keys stay in its environment; no secret file was written.'
} else {
    & $pythonPath (Join-Path $PSScriptRoot 'core_stage_3_1_local.py') $Action
    if ($LASTEXITCODE -ne 0) { throw "BERVEL command failed: $Action" }
}
