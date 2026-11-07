[CmdletBinding()]
param(
    [ValidateSet('start', 'stop')][string]$Action = 'start',
    [switch]$Tools,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)

function New-Secret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

function Invoke-Compose {
    param([string[]]$DockerArguments)
    & docker compose --env-file .env.docker @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed (exit $LASTEXITCODE)." }
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker was not found. Install Docker Desktop, start it, then run this launcher again. https://www.docker.com/products/docker-desktop/'
    }
    $composeVersion = (& docker compose version --short 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $composeVersion -notmatch '^v?(\d+)\.(\d+)') {
        throw 'Docker Compose is unavailable. Update Docker Desktop (Compose 2.24 or newer is required).'
    }
    if ([int]$Matches[1] -lt 2 -or ([int]$Matches[1] -eq 2 -and [int]$Matches[2] -lt 24)) {
        throw 'Update Docker Desktop: Docker Compose 2.24 or newer is required.'
    }
    $dockerType = (& docker info --format '{{.OSType}}' 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker is not running. Start Docker Desktop, wait until its engine is ready, then run this launcher again.'
    }
    if ($dockerType -ne 'linux') { throw 'Switch Docker Desktop to Linux containers, then run this launcher again.' }

    if ($Action -eq 'start' -and -not (Test-Path -LiteralPath '.env.docker')) {
        $settings = @(
            '# Local credentials: keep this file private and keep it with your data.',
            'COMPOSE_PROJECT_NAME=localinterfaceweb',
            'APP_PORT=8080', 'POSTGRES_DB=photo_service', 'POSTGRES_USER=photo_service',
            "POSTGRES_PASSWORD=$(New-Secret)", "DJANGO_SECRET_KEY=$(New-Secret)",
            'ADMIN_USERNAME=admin', "ADMIN_PASSWORD=$(New-Secret)",
            'PGADMIN_PORT=5050', 'PGADMIN_DEFAULT_EMAIL=admin@example.com',
            "PGADMIN_DEFAULT_PASSWORD=$(New-Secret)"
        )
        # CreateNew prevents another launcher from overwriting credentials.
        $stream = [IO.File]::Open((Join-Path (Get-Location) '.env.docker'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        $writer = New-Object IO.StreamWriter($stream, (New-Object Text.UTF8Encoding($false)))
        try { $writer.Write(($settings -join "`n") + "`n") } finally { $writer.Dispose() }
        Write-Host 'Created .env.docker with random local credentials. Your .env was left unchanged.'
    }
    if (-not (Test-Path -LiteralPath '.env.docker' -PathType Leaf)) {
        throw 'Missing .env.docker. Restore the original file before stopping an existing installation, or run start.cmd for a new installation.'
    }
    if ($Action -eq 'stop') {
        Invoke-Compose -DockerArguments @('--profile', 'tools', 'down')
        Write-Host 'Stopped. Database, photos, and saved credentials are preserved.'
        exit 0
    }

    Invoke-Compose -DockerArguments @('config', '--quiet')
    $upArguments = @()
    if ($Tools) { $upArguments += @('--profile', 'tools') }
    $upArguments += @('up', '-d', '--build', '--wait', '--wait-timeout', '180')
    try { Invoke-Compose -DockerArguments $upArguments }
    catch {
        & docker compose --env-file .env.docker logs --tail=80
        throw 'Startup failed. See container logs above. Check whether port 8080 is in use and that Docker Desktop has enough memory. Your saved data and credentials were preserved.'
    }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath '.env.docker') {
        if ($line -match '^([A-Z_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
    }
    $port = '8080'
    if ($values.ContainsKey('APP_PORT')) { $port = $values['APP_PORT'] }
    if ($env:APP_PORT) { $port = $env:APP_PORT }
    $url = "http://localhost:$port"
    Write-Host "Ready: $url"
    if (-not $env:CI) {
        Write-Host "Initial administrator: $($values['ADMIN_USERNAME'])"
        Write-Host "Initial password: $($values['ADMIN_PASSWORD'])"
    }
    Write-Host 'Credentials are stored in .env.docker. An existing account password is never reset on startup.'
    if ($Tools) {
        Write-Host "pgAdmin: http://localhost:$($values['PGADMIN_PORT'])"
        if (-not $env:CI) { Write-Host "pgAdmin login: $($values['PGADMIN_DEFAULT_EMAIL']) / $($values['PGADMIN_DEFAULT_PASSWORD'])" }
    }
    if (-not $NoBrowser -and -not $env:NO_BROWSER -and -not $env:CI) {
        try { Start-Process $url | Out-Null } catch { Write-Host "Open $url in your browser." }
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
