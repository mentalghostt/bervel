param([Parameter(Mandatory=$true)][string]$BackupPath)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$backupRoot = (Resolve-Path -LiteralPath $BackupPath).Path
$allowedRoot = Join-Path $projectRoot '.dev/local/docker/backups'
if (!$backupRoot.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Backup must be inside the project backup directory.' }
$manifest = Get-Content -LiteralPath (Join-Path $backupRoot 'manifest.json') -Raw | ConvertFrom-Json
foreach ($file in $manifest.files) {
    if ($file.name -ne [IO.Path]::GetFileName($file.name)) { throw 'Invalid backup filename.' }
    if ((Get-FileHash -LiteralPath (Join-Path $backupRoot $file.name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw 'Backup checksum differs.' }
}
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerExe = if ($dockerCommand) { $dockerCommand.Source } else { 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
$composeArguments = @('compose','-f','compose.yaml','-f','compose.orchestration.yaml','--profile','test')
$databaseName = 'bervel_restore_check_' + [Guid]::NewGuid().ToString('N')
$volumeName = $databaseName + '_objects'
$temporaryDump = "/tmp/$databaseName.dump"
$databaseCreated = $false
$volumeCreated = $false

function Invoke-RestoreCheckDocker {
    param([string[]]$DockerArguments)
    & $dockerExe @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker restore-check exited with code $LASTEXITCODE" }
}

Push-Location $projectRoot
try {
    Invoke-RestoreCheckDocker ($composeArguments + @('exec','-T','postgres','createdb','-U','postgres','--owner=bervel_core_stage31_test',$databaseName))
    $databaseCreated = $true
    Invoke-RestoreCheckDocker ($composeArguments + @('cp',(Join-Path $backupRoot 'bervel_core_stage31.dump'),"postgres:$temporaryDump"))
    Invoke-RestoreCheckDocker ($composeArguments + @('exec','-T','postgres','pg_restore','-U','postgres','--role=bervel_core_stage31_test','--no-owner','--no-acl','--single-transaction','--exit-on-error',"--dbname=$databaseName",$temporaryDump))
    Invoke-RestoreCheckDocker @('volume','create','--label','com.docker.compose.project=bervel',$volumeName)
    $volumeCreated = $true
    $extractCode = @'
import os,pathlib,tarfile
root=pathlib.Path('/data')
assert not any(root.iterdir())
with tarfile.open('/backup/bervel_objects.tar') as archive: archive.extractall(root,filter='data')
for path in [root,*root.rglob('*')]: os.chown(path,10001,10001)
'@
    Invoke-RestoreCheckDocker @('run','--rm','--mount',"type=volume,source=$volumeName,target=/data",'--mount',"type=bind,source=$backupRoot,target=/backup,readonly",
        'python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e','python','-c',$extractCode)
    Invoke-RestoreCheckDocker ($composeArguments + @('run','--rm','--no-deps','-e',"BERVEL_RESTORE_DATABASE=$databaseName",'-v',"${volumeName}:/restore-objects",'-v',"${backupRoot}:/backup:ro",'tests','python','.dev/scripts/docker_check_restored.py'))
} finally {
    try {
        if ($databaseCreated) { Invoke-RestoreCheckDocker ($composeArguments + @('exec','-T','postgres','dropdb','-U','postgres',$databaseName)) }
        if ($volumeCreated) { Invoke-RestoreCheckDocker @('volume','rm',$volumeName) }
        Invoke-RestoreCheckDocker ($composeArguments + @('exec','-T','postgres','rm','-f',$temporaryDump))
    } finally { Pop-Location }
}
