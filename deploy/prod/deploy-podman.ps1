param(
    [Parameter(Position=0)]
    [ValidateSet("deploy", "migrate", "stop", "restart", "logs")]
    [string]$Command = "deploy"
)

# Production deployment script for Kalanjiyam using Podman on Windows PowerShell
#
# Usage:
#   .\deploy\prod\deploy-podman.ps1          # build + start all services
#   .\deploy\prod\deploy-podman.ps1 migrate  # run DB migrations only
#   .\deploy\prod\deploy-podman.ps1 stop     # stop all services
#   .\deploy\prod\deploy-podman.ps1 logs     # tail logs
#

$ErrorActionPreference = "Stop"

# Resolve directories
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$ComposeFile = Join-Path $ScriptDir "docker-compose.yml"
$PROJECT = "kalanjiyam-prod"

# Change directory to repo root
Set-Location $RepoRoot

# --- Helpers ----------------------------------------------------------------

function Load-Env {
    if (-not $env:HOME) {
        $env:HOME = $env:USERPROFILE
        [System.Environment]::SetEnvironmentVariable("HOME", $env:USERPROFILE, [System.EnvironmentVariableTarget]::Process)
    }
    if (Test-Path .env) {
        Get-Content .env | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and $line -like "*=*") {
                $key, $value = $line -split '=', 2
                $key = $key.Trim()
                $value = $value.Trim()
                # Strip quotes if present
                if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                [System.Environment]::SetEnvironmentVariable($key, $value, [System.EnvironmentVariableTarget]::Process)
            }
        }
    }
}

function Invoke-Compose {
    param(
        [Parameter(ValueFromRemainingArguments=$true)]
        [string[]]$Arguments
    )
    
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    
    # Try podman compose first
    try {
        $null = & podman compose version
        if ($LASTEXITCODE -eq 0) {
            $ErrorActionPreference = $oldEAP
            & podman compose @Arguments
            if ($LASTEXITCODE -ne 0) {
                Write-Error "ERROR: 'podman compose' failed with exit code $LASTEXITCODE"
                Exit $LASTEXITCODE
            }
            return
        }
    } catch {}
    
    # Fallback to podman-compose
    if (Get-Command podman-compose -ErrorAction SilentlyContinue) {
        $ErrorActionPreference = $oldEAP
        & podman-compose @Arguments
        if ($LASTEXITCODE -ne 0) {
            Write-Error "ERROR: 'podman-compose' failed with exit code $LASTEXITCODE"
            Exit $LASTEXITCODE
        }
        return
    }

    # Fallback to docker compose
    try {
        $null = & docker compose version
        if ($LASTEXITCODE -eq 0) {
            $ErrorActionPreference = $oldEAP
            & docker compose @Arguments
            if ($LASTEXITCODE -ne 0) {
                Write-Error "ERROR: 'docker compose' failed with exit code $LASTEXITCODE"
                Exit $LASTEXITCODE
            }
            return
        }
    } catch {}

    # Fallback to docker-compose
    if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
        $ErrorActionPreference = $oldEAP
        & docker-compose @Arguments
        if ($LASTEXITCODE -ne 0) {
            Write-Error "ERROR: 'docker-compose' failed with exit code $LASTEXITCODE"
            Exit $LASTEXITCODE
        }
        return
    }
    
    $ErrorActionPreference = $oldEAP
    Write-Error "Could not find 'podman compose', 'podman-compose', 'docker compose', or 'docker-compose'. Please ensure a compose tool is installed."
    Exit 1
}

function Check-Podman {
    $hasPodman = Get-Command podman -ErrorAction SilentlyContinue
    if (-not $hasPodman) {
        Write-Error "ERROR: Podman is not installed or not in PATH."
        Exit 1
    }
    # Check if Podman machine is responsive
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & podman info > $null 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Podman machine is not responsive. Attempting 'podman machine start'..." -ForegroundColor Yellow
        & podman machine start
        & podman info > $null 2>&1
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $oldEAP
            Write-Error "ERROR: Unable to connect to Podman socket. Please start Podman machine using 'podman machine start'."
            Exit 1
        }
    }
    $ErrorActionPreference = $oldEAP
}

function Check-Env {
    Check-Podman
    
    if (-not (Test-Path .env)) {
        Write-Error "ERROR: .env not found. Copy .env.example to .env and fill in all values."
        Exit 1
    }
    Load-Env

    $requiredVars = @("SECRET_KEY", "SQLALCHEMY_DATABASE_URI", "FLASK_UPLOAD_FOLDER", "POSTGRES_PASSWORD", "KALANJIYAM_BOT_PASSWORD")
    foreach ($var in $requiredVars) {
        $val = [System.Environment]::GetEnvironmentVariable($var)
        if ([string]::IsNullOrEmpty($val)) {
            Write-Error "ERROR: $var is not set in .env"
            Exit 1
        }
    }

    if ($env:FLASK_ENV -ne "production") {
        Write-Error "ERROR: FLASK_ENV must be 'production' in .env"
        Exit 1
    }

    if ($env:APPLICATION_URL_PREFIX -ne "/kalanjiyam") {
        Write-Error "ERROR: APPLICATION_URL_PREFIX must be '/kalanjiyam' in .env"
        Exit 1
    }

    $storageBackend = if ($env:STORAGE_BACKEND) { $env:STORAGE_BACKEND } else { "s3" }
    if ($storageBackend -eq "s3") {
        $requiredS3Vars = @("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
        foreach ($var in $requiredS3Vars) {
            $val = [System.Environment]::GetEnvironmentVariable($var)
            if ([string]::IsNullOrEmpty($val)) {
                Write-Error "ERROR: $var is not set in .env (required when STORAGE_BACKEND=s3)"
                Exit 1
            }
        }
    }

    $dataDir = if ($env:KALANJIYAM_DATA_DIR) { $env:KALANJIYAM_DATA_DIR } else { Join-Path $env:USERPROFILE "kalanjiyam-data" }
    $dataDirNormalized = $dataDir.Replace('\', '/')
    $env:KALANJIYAM_DATA_DIR = $dataDirNormalized
    [System.Environment]::SetEnvironmentVariable("KALANJIYAM_DATA_DIR", $dataDirNormalized, [System.EnvironmentVariableTarget]::Process)

    $uploadsDir = Join-Path $dataDir "uploads"
    if (-not (Test-Path $uploadsDir)) {
        New-Item -ItemType Directory -Force -Path $uploadsDir | Out-Null
    }
    $metadataDir = "${dataDir}-metadata"
    if (-not (Test-Path $metadataDir)) {
        New-Item -ItemType Directory -Force -Path $metadataDir | Out-Null
    }
    Write-Host "[OK] .env OK" -ForegroundColor Green
}

function Build-Image {
    Write-Host "Building Podman image (this takes 2-5 min on first run)..."
    $gitCommit = (git rev-parse --short HEAD).Trim()
    $gitBranch = (git rev-parse --abbrev-ref HEAD).Trim() -replace '[^A-Za-z0-9_.-]', '-'
    $image = "kalanjiyam:v0.1-$gitBranch-$gitCommit"
    $imageLatest = "kalanjiyam-rel:latest"
    podman build --no-cache -t $image -t $imageLatest -f build/containers/Dockerfile.final .
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ERROR: 'podman build' failed with exit code $LASTEXITCODE"
        Exit $LASTEXITCODE
    }
    $env:KALANJIYAM_IMAGE = $image
    Write-Host "[OK] Image: $image" -ForegroundColor Green
}

function Run-Migrations {
    Write-Host "Running database migrations..."
    Load-Env
    
    $allContainers = (& podman ps -a --format "{{.Names}}") -split "`r?\n"
    if ($allContainers -contains "kalanjiyam-db") {
        $runningContainers = (& podman ps --format "{{.Names}}") -split "`r?\n"
        if ($runningContainers -notcontains "kalanjiyam-db" -or $runningContainers -notcontains "kalanjiyam-redis") {
            podman start kalanjiyam-db kalanjiyam-redis | Out-Null
            Start-Sleep -Seconds 3
        }
    } else {
        Invoke-Compose -p $PROJECT -f $ComposeFile up -d kalanjiyam-db kalanjiyam-redis
        Start-Sleep -Seconds 5  # wait for postgres to be ready
    }
    
    $targetImage = if ($env:KALANJIYAM_IMAGE) { $env:KALANJIYAM_IMAGE } else { "kalanjiyam-rel:latest" }
    $postgresPass = if ($env:POSTGRES_PASSWORD) { $env:POSTGRES_PASSWORD } else { "kalanjiyam" }
    
    # Discover network attached to kalanjiyam-db container
    $composeNet = ""
    if ($allContainers -contains "kalanjiyam-db") {
        $composeNet = (& podman inspect kalanjiyam-db --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' 2>$null).Trim()
    }
    if (-not $composeNet) {
        $netList = & podman network ls --format "{{.Name}}"
        $composeNet = ($netList -split "`r?\n" | Where-Object { $_ -like "*${PROJECT}*" -or $_ -like "*kalanjiyam*" -or $_ -like "*prod*" }) | Select-Object -First 1
    }
    if (-not $composeNet) {
        $composeNet = "${PROJECT}_default"
    }

    Write-Host "Using Podman network: $composeNet"
    $runArgs = @(
        "run", "--rm",
        "--network", $composeNet,
        "--env-file", ".env",
        "-e", "FLASK_ENV=production",
        "-e", "REDIS_URL=redis://kalanjiyam-redis:6379/0",
        "-e", "SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:${postgresPass}@kalanjiyam-db/kalanjiyam",
        $targetImage,
        "alembic", "upgrade", "head"
    )
    podman @runArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ERROR: Database migration failed."
        Exit 1
    }
        
    Write-Host "[OK] Migrations applied" -ForegroundColor Green
    
    Write-Host "Seeding default database lookup tables..."
    $seedArgs = @(
        "run", "--rm",
        "--network", $composeNet,
        "--env-file", ".env",
        "-e", "FLASK_ENV=production",
        "-e", "REDIS_URL=redis://kalanjiyam-redis:6379/0",
        "-e", "SQLALCHEMY_DATABASE_URI=postgresql://kalanjiyam:${postgresPass}@kalanjiyam-db/kalanjiyam",
        $targetImage,
        "python", "-m", "kalanjiyam.seed.lookup"
    )
    podman @seedArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ERROR: Seeding lookup tables failed."
        Exit 1
    }
        
    Write-Host "[OK] Lookups seeded" -ForegroundColor Green
}

# --- Commands ---------------------------------------------------------------

switch ($Command) {
    "deploy" {
        Check-Env
        Build-Image
        Run-Migrations
        Write-Host "Starting services..."
        Invoke-Compose -p $PROJECT -f $ComposeFile up -d
        Write-Host ""
        Write-Host "[OK] Kalanjiyam is running at https://siddhasagaram.in/kalanjiyam" -ForegroundColor Green
        Write-Host "   Logs: .\deploy\prod\deploy-podman.ps1 logs"
        Write-Host "   Stop: .\deploy\prod\deploy-podman.ps1 stop"
    }
    "migrate" {
        Check-Env
        Build-Image
        Run-Migrations
    }
    "stop" {
        Check-Env
        $env:KALANJIYAM_IMAGE = "kalanjiyam-rel:latest"
        Invoke-Compose -p $PROJECT -f $ComposeFile stop
        Invoke-Compose -p $PROJECT -f $ComposeFile rm -f
        Write-Host "[OK] Services stopped" -ForegroundColor Green
    }
    "restart" {
        Check-Env
        $env:KALANJIYAM_IMAGE = "kalanjiyam-rel:latest"
        Invoke-Compose -p $PROJECT -f $ComposeFile up -d
        Write-Host "[OK] Services restarted" -ForegroundColor Green
    }
    "logs" {
        Check-Env
        $env:KALANJIYAM_IMAGE = "kalanjiyam-rel:latest"
        Invoke-Compose -p $PROJECT -f $ComposeFile logs -f
    }
}
