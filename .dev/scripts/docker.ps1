param(
    [ValidateSet('init','start','stop','status','key','dev','orchestration','tests','e2e')]
    [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) { $dockerCommand.Source } else { 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
$baseArguments = @('compose', '-f', 'compose.yaml', '-f', 'compose.orchestration.yaml')

function Invoke-ProjectDocker {
    param([string[]]$DockerArguments)
    & $dockerExe @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker exited with code $LASTEXITCODE" }
}

Push-Location $projectRoot
try {
    switch ($Action) {
        init {
            Invoke-ProjectDocker @('run','--rm','--mount',"type=bind,source=$projectRoot,target=/workspace",'--workdir','/workspace',
                'python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e',
                'python','.dev/scripts/docker_env.py')
        }
        start { Invoke-ProjectDocker ($baseArguments + @('up','--build','-d','--wait','api','frontend')) }
        stop { Invoke-ProjectDocker ($baseArguments + @('--profile','orchestration','--profile','test','stop')) }
        status { Invoke-ProjectDocker ($baseArguments + @('--profile','orchestration','--profile','test','ps','-a')) }
        key {
            $keyLine = Get-Content -LiteralPath '.env' | Where-Object { $_.StartsWith('BERVEL_ACCESS_KEYS=') }
            if (!$keyLine) { throw 'Initialize .env first.' }
            $bindings = $keyLine.Substring('BERVEL_ACCESS_KEYS='.Length).Trim("'") | ConvertFrom-Json
            $bindings[0].key | Set-Clipboard
            Write-Output 'Application key copied to clipboard; it was not printed.'
        }
        dev { Invoke-ProjectDocker ($baseArguments + @('-f','.dev/docker/compose.dev.yaml','up','--build','--watch','api','frontend')) }
        orchestration { Invoke-ProjectDocker ($baseArguments + @('--profile','orchestration','up','-d','--wait','redis','n8n-main','n8n-worker')) }
        tests {
            Invoke-ProjectDocker ($baseArguments + @('build','api'))
            Invoke-ProjectDocker ($baseArguments + @('--profile','test','build','tests'))
            Invoke-ProjectDocker ($baseArguments + @('--profile','test','run','--rm','tests'))
        }
        e2e {
            $testArguments = $baseArguments + @('-f','.dev/docker/compose.e2e.yaml','--profile','test')
            Invoke-ProjectDocker ($testArguments + @('build','api','frontend','e2e'))
            Invoke-ProjectDocker ($testArguments + @('build','tests'))
            Invoke-ProjectDocker ($testArguments + @('run','--rm','e2e','npm','run','test:frontend'))
            Invoke-ProjectDocker ($testArguments + @('run','--rm','e2e','npm','run','test:frontend:live'))
            Invoke-ProjectDocker ($baseArguments + @('--profile','test','run','--rm','--no-deps','-v',"${projectRoot}/.dev/local/docker/frontend:/browser-results:ro",'tests','python','.dev/scripts/docker_e2e_sql.py'))
        }
    }
} finally { Pop-Location }
