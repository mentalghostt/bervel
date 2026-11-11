param(
    [ValidateSet('install', 'start', 'status', 'stop')]
    [string]$Action = 'status',
    [ValidateRange(1, 65535)]
    [int]$Port = 5173
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$runtimeDirectory = Join-Path $projectRoot '.dev/local/cloudflare'
$executable = Join-Path $runtimeDirectory 'cloudflared.exe'
$statePath = Join-Path $runtimeDirectory 'state.json'
$stderrPath = Join-Path $runtimeDirectory 'tunnel.stderr.log'
$stdoutPath = Join-Path $runtimeDirectory 'tunnel.stdout.log'
$configPath = Join-Path $runtimeDirectory 'empty.yml'
$version = '2026.10.7'
$expectedHash = '2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343'

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
# Serialize start/stop, including calls from separate PowerShell windows.
$lock = [IO.File]::Open((Join-Path $runtimeDirectory 'control.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $state = if (Test-Path -LiteralPath $statePath) { Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } else { $null }
    $tunnelProcess = $null
    if ($state) {
        $candidate = Get-Process -Id $state.process_id -ErrorAction SilentlyContinue
        # A recycled PID must never let stop kill an unrelated process.
        if ($candidate -and $candidate.Path -eq $executable -and
            $candidate.StartTime.ToUniversalTime().Ticks.ToString() -eq $state.start_ticks) {
            $tunnelProcess = $candidate
        }
    }

    if ($Action -eq 'status') {
        if (!$tunnelProcess) {
            Write-Output 'Cloudflare tunnel is stopped. Run: .\.dev\scripts\cloudflare.ps1 -Action start'
        } else {
            Write-Output "Cloudflare process is running (PID $($state.process_id))."
            Write-Output "Public URL: $($state.url)"
            Write-Output "Local origin: $($state.origin)"
            Write-Output "Logs: $stderrPath"
        }
        return
    }

    if ($Action -eq 'stop') {
        if ($tunnelProcess) {
            Stop-Process -InputObject $tunnelProcess
            $tunnelProcess.WaitForExit()
        }
        if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath }
        Write-Output 'Cloudflare tunnel is stopped; the temporary URL no longer serves this application.'
        return
    }

    if ($tunnelProcess) {
        if ($Action -eq 'install') { throw 'Stop this project tunnel before installing cloudflared.' }
        if ($state.origin -ne "http://127.0.0.1:$Port") { throw 'A tunnel for another port is already running. Stop it first.' }
        Write-Output "Already running: $($state.url)"
        return
    }

    if ($Action -eq 'start') {
        $origin = "http://127.0.0.1:$Port"
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$origin/" -TimeoutSec 5
        } catch {
            throw "BERVEL frontend is unavailable at $origin. Start Docker Desktop and the existing application first. No tunnel was started."
        }
        # Publish the built site, never Vite's development/source server or the raw API.
        if ($response.StatusCode -ne 200 -or $response.Headers['Server'] -notmatch '^nginx/') {
            throw 'Use the production BERVEL Nginx frontend port; development servers and the API are not published by this helper.'
        }
    }

    if (!(Test-Path -LiteralPath $executable)) {
        $downloadPath = Join-Path $runtimeDirectory 'cloudflared.download.exe'
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/cloudflare/cloudflared/releases/download/$version/cloudflared-windows-amd64.exe" -OutFile $downloadPath
        if ((Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
            Remove-Item -LiteralPath $downloadPath
            throw 'The cloudflared download failed SHA-256 verification.'
        }
        Move-Item -LiteralPath $downloadPath -Destination $executable
    }
    if ((Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw 'The project cloudflared binary does not match the pinned SHA-256. It was not executed.'
    }
    if ($Action -eq 'install') {
        & $executable --version
        if ($LASTEXITCODE -ne 0) { throw 'cloudflared could not start.' }
        return
    }

    $edgeArguments = @()
    $srvRecords = @(Resolve-DnsName -Name '_v2-origintunneld._tcp.argotunnel.com' -Type SRV -DnsOnly -QuickTimeout -ErrorAction SilentlyContinue |
        Where-Object { $_.Type -eq 'SRV' })
    if (!$srvRecords.Count) {
        # Some resolvers return no SRV records. Resolve through Cloudflare's HTTPS
        # API for this process only; do not change Windows DNS or VPN settings.
        $dns = Invoke-RestMethod -Uri 'https://cloudflare-dns.com/dns-query?name=_v2-origintunneld._tcp.argotunnel.com&type=SRV' `
            -Headers @{ Accept = 'application/dns-json' } -TimeoutSec 15
        if ($dns.Status -ne 0) { throw 'Cloudflare HTTPS DNS could not resolve tunnel servers.' }
        foreach ($answer in $dns.Answer) {
            if ($answer.type -ne 33) { continue }
            $parts = $answer.data -split '\s+'
            if ($parts.Count -ne 4 -or $parts[2] -ne '7844' -or
                $parts[3] -notmatch '^[a-zA-Z0-9.-]+\.argotunnel\.com\.?$') {
                throw 'Cloudflare HTTPS DNS returned an unexpected tunnel server record.'
            }
            $edgeArguments += @('--edge', ($parts[3].TrimEnd('.') + ':7844'))
        }
        if (!$edgeArguments.Count) { throw 'Cloudflare HTTPS DNS returned no tunnel servers.' }
        Write-Output 'Using Cloudflare HTTPS DNS for tunnel discovery; system network settings are unchanged.'
    }

    # An explicit empty config leaves the user's global Cloudflare settings untouched.
    [IO.File]::WriteAllText($configPath, "{}`n", [Text.Encoding]::ASCII)
    $arguments = @('--config', ('"{0}"' -f $configPath), 'tunnel', '--no-autoupdate',
        '--protocol', 'http2', '--edge-ip-version', '4', '--url', $origin,
        '--http-host-header', 'localhost') + $edgeArguments
    $launched = Start-Process -FilePath $executable -ArgumentList $arguments -WorkingDirectory $projectRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    $state = [ordered]@{
        process_id = $launched.Id
        start_ticks = $launched.StartTime.ToUniversalTime().Ticks.ToString()
        origin = $origin
        url = $null
    }
    try {
        $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
        $deadline = (Get-Date).AddSeconds(60)
        do {
            $launched.Refresh()
            if ($launched.HasExited) { throw "cloudflared exited with code $($launched.ExitCode). See $stderrPath" }
            $log = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw } else { '' }
            if ($log -match 'https://[a-z0-9-]+\.trycloudflare\.com') { $state.url = $Matches[0] }
            if ($state.url -and $log -match 'Registered tunnel connection') { break }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if (!$state.url -or $log -notmatch 'Registered tunnel connection') {
            throw "Cloudflare did not connect within 60 seconds. See $stderrPath. VPN/network settings were not changed."
        }
        # A registered control connection alone does not prove the site can load.
        $publicSite = Invoke-WebRequest -UseBasicParsing -Uri ($state.url + '/') -TimeoutSec 15
        if ($publicSite.Content -ne $response.Content) { throw 'The public frontend differs from the local site.' }
        $bundle = [regex]::Match($response.Content, 'src="(/assets/[^" ]+\.js)"')
        if (!$bundle.Success) { throw 'The built BERVEL JavaScript bundle was not found.' }
        $bundlePath = $bundle.Groups[1].Value
        $localBundle = Join-Path $runtimeDirectory 'check-local.js'
        $publicBundle = Join-Path $runtimeDirectory 'check-public.js'
        Invoke-WebRequest -UseBasicParsing -Uri ($origin + $bundlePath) -OutFile $localBundle -TimeoutSec 10
        Invoke-WebRequest -UseBasicParsing -Uri ($state.url + $bundlePath) -OutFile $publicBundle -TimeoutSec 25
        if ((Get-FileHash -LiteralPath $localBundle).Hash -ne (Get-FileHash -LiteralPath $publicBundle).Hash) {
            throw 'The JavaScript bundle could not be transferred intact through Cloudflare.'
        }
        $apiCheck = Invoke-WebRequest -UseBasicParsing -Uri ($state.url + '/api/v0.1/artifacts?limit=1') `
            -Headers @{ 'X-Bervel-Web' = '1'; 'Sec-Fetch-Site' = 'same-origin' } -TimeoutSec 15
        if ($apiCheck.StatusCode -ne 200 -or !($apiCheck.Content | ConvertFrom-Json).PSObject.Properties['artifacts']) {
            throw 'The application API did not pass the public read-only check.'
        }
        $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    } catch {
        $launched.Refresh()
        if (!$launched.HasExited) { Stop-Process -InputObject $launched; $launched.WaitForExit() }
        if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath }
        throw "The temporary link did not pass startup verification and was closed. $($_.Exception.Message) Logs: $stderrPath"
    }
    Write-Output "Public URL: $($state.url)"
    Write-Output 'Anyone with this URL can use the shared BERVEL workspace.'
    Write-Output 'Stop sharing: .\.dev\scripts\cloudflare.ps1 -Action stop'
} finally {
    $lock.Dispose()
}
