param(
    [ValidateSet('init','start','stop','status','key','dev','worker','tests','e2e')]
    [string]$Action = 'status',
    [switch]$Mox
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) { $dockerCommand.Source } else { 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
$baseArguments = @('compose', '-f', 'compose.yaml')
$backgroundWorkers = @('worker-knowledge','worker-chat','worker-inspection','worker-ai-tender-radar')
if ($Mox) {
    $moxDirectory = Join-Path $env:USERPROFILE '.codex'
    if (!$env:BERVEL_MOX_AUTH_PATH) { $env:BERVEL_MOX_AUTH_PATH = Join-Path $moxDirectory 'auth.json' }
    if (!$env:BERVEL_MOX_CONFIG_PATH) { $env:BERVEL_MOX_CONFIG_PATH = Join-Path $moxDirectory 'config.toml' }
    foreach ($moxPath in @($env:BERVEL_MOX_AUTH_PATH, $env:BERVEL_MOX_CONFIG_PATH)) {
        if (!(Test-Path -LiteralPath $moxPath -PathType Leaf)) { throw 'Existing external MOX auth/config file is required; no login or file creation is performed.' }
    }
    if (!$env:BERVEL_MOX_CA_PATH) {
        $trustedMox = @(Get-ChildItem Cert:\CurrentUser\Root,Cert:\LocalMachine\Root |
            Where-Object { $_.Subject -eq 'O=MOX ACCESS, CN=localhost' -and $_.NotAfter -gt (Get-Date) } |
            Sort-Object Thumbprint -Unique)
        if ($trustedMox.Count -ne 1) { throw 'Set BERVEL_MOX_CA_PATH to an existing trusted public PEM certificate; automatic selection is ambiguous or unavailable.' }
        $publicCertificateDirectory = Join-Path $projectRoot 'docker/mox'
        New-Item -ItemType Directory -Path $publicCertificateDirectory -Force | Out-Null
        $publicCertificatePath = Join-Path $publicCertificateDirectory 'ca.pem'
        $pem = "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($trustedMox[0].RawData, [Base64FormattingOptions]::InsertLineBreaks) + "`n-----END CERTIFICATE-----`n"
        [IO.File]::WriteAllText($publicCertificatePath, $pem, [Text.Encoding]::ASCII)
    } elseif (!(Test-Path -LiteralPath $env:BERVEL_MOX_CA_PATH -PathType Leaf)) {
        throw 'The configured public CA certificate file does not exist.'
    }
    $baseArguments += @('-f', 'compose.mox.yaml')
}

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
                '-e',"BERVEL_ENGINEERING_HOST_PROJECT_ROOT=$($projectRoot.Replace('\','/'))",
                'python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e',
                'python','.dev/scripts/docker_env.py')
        }
        start {
            Invoke-ProjectDocker ($baseArguments + @('up','--build','-d','--wait','api','frontend','codex') + $backgroundWorkers)
            Invoke-ProjectDocker ($baseArguments + @('--profile','inspection-tools','build','inspection-backend-tests','inspection-frontend-tests'))
        }
        stop { Invoke-ProjectDocker ($baseArguments + @('--profile','test','stop')) }
        status { Invoke-ProjectDocker ($baseArguments + @('--profile','test','ps','-a')) }
        key {
            $keyLine = Get-Content -LiteralPath '.env' | Where-Object { $_.StartsWith('BERVEL_ACCESS_KEYS=') }
            if (!$keyLine) { throw 'Initialize .env first.' }
            $bindings = $keyLine.Substring('BERVEL_ACCESS_KEYS='.Length).Trim("'") | ConvertFrom-Json
            $bindings[0].key | Set-Clipboard
            Write-Output 'Application key copied to clipboard; it was not printed.'
        }
        dev { Invoke-ProjectDocker ($baseArguments + @('-f','.dev/docker/compose.dev.yaml','up','--build','--watch','api','frontend','codex') + $backgroundWorkers) }
        worker { Invoke-ProjectDocker ($baseArguments + @('up','-d','--wait') + $backgroundWorkers) }
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
