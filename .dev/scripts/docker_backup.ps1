$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) { $dockerCommand.Source } else { 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
$composeArguments = @('compose','-f','compose.yaml')
$backupRoot = Join-Path $projectRoot ('.dev/local/docker/backups/docker-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
if (Test-Path -LiteralPath $backupRoot) { throw 'Backup destination already exists.' }

function Invoke-BackupDocker {
    param([string[]]$DockerArguments)
    & $dockerExe @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker backup command exited with code $LASTEXITCODE" }
}

Push-Location $projectRoot
$stoppedServices = @()
try {
    $runningServices = @(& $dockerExe @composeArguments ps --services --status running)
    if ($LASTEXITCODE -ne 0 -or 'postgres' -notin $runningServices) { throw 'Project PostgreSQL must be running.' }
    $stoppedServices = @('frontend','api','worker') | Where-Object { $_ -in $runningServices }
    if ($stoppedServices.Count -gt 0) { Invoke-BackupDocker ($composeArguments + @('stop') + $stoppedServices) }
    New-Item -ItemType Directory -Path $backupRoot | Out-Null
    $counts = @{}
    foreach ($databaseName in @('bervel_core_stage31')) {
        $temporaryDump = "/tmp/bervel-backup-$databaseName.dump"
        Invoke-BackupDocker ($composeArguments + @('exec','-T','postgres','pg_dump','-U','postgres','-d',$databaseName,'-Fc','--no-owner','--no-acl','-f',$temporaryDump))
        Invoke-BackupDocker ($composeArguments + @('cp',"postgres:$temporaryDump",(Join-Path $backupRoot "$databaseName.dump")))
        Invoke-BackupDocker ($composeArguments + @('exec','-T','postgres','rm',$temporaryDump))
        if ($databaseName -eq 'bervel_core_stage31') {
            foreach ($tableName in @('artifacts','artifact_revisions','datasets','dataset_revisions','data_records')) {
                $count = & $dockerExe @composeArguments exec -T postgres psql -U postgres -d $databaseName -At -c "SELECT count(*) FROM $tableName"
                if ($LASTEXITCODE -ne 0) { throw 'Cannot verify database snapshot counts.' }
                $counts[$tableName] = [int]$count
            }
        }
    }
    $tarCode = @'
import sys,tarfile
with tarfile.open(sys.argv[1], 'w') as archive:
    archive.add('/data', arcname='.')
'@
    foreach ($volumeName in @('bervel_objects')) {
        & $dockerExe volume inspect $volumeName --format '{{.Name}}' *> $null
        if ($LASTEXITCODE -ne 0) { continue }
        Invoke-BackupDocker @('run','--rm','--mount',"type=volume,source=$volumeName,target=/data,readonly",'--mount',"type=bind,source=$backupRoot,target=/backup",
            'python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e','python','-c',$tarCode,"/backup/$volumeName.tar")
    }
    Copy-Item -LiteralPath '.env' -Destination (Join-Path $backupRoot 'docker.env')
    $files = @(Get-ChildItem -LiteralPath $backupRoot -File | ForEach-Object { @{name=$_.Name;sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()} })
    @{created_at=[DateTime]::UtcNow.ToString('o');database_counts=$counts;files=$files} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $backupRoot 'manifest.json') -Encoding utf8
    Write-Output "Backup saved inside the project: $backupRoot"
} finally {
    try {
        # up recreates an outdated migration container after an application build.
        if ($stoppedServices.Count -gt 0) { Invoke-BackupDocker ($composeArguments + @('up','-d','--wait') + $stoppedServices) }
    } finally { Pop-Location }
}
