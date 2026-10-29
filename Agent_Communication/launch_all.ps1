param(
    [ValidateRange(1024, 65535)]
    [int]$AdvisorPort = 8000,
    [ValidateRange(1024, 65535)]
    [int]$ClientApiPort = 8001,
    [ValidateRange(1024, 65535)]
    [int]$UiPort = 3000
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$clientDir = Join-Path $root "Client_Simulator"
$advisorDir = Join-Path $root "Customer_Advisor"
$frontendDir = Join-Path $PSScriptRoot "frontend"
$ports = @($AdvisorPort, $ClientApiPort, $UiPort) | Select-Object -Unique

foreach ($requiredPath in @(
    (Join-Path $clientDir ".venv\Scripts\Activate.ps1"),
    (Join-Path $advisorDir ".venv\Scripts\Activate.ps1"),
    (Join-Path $frontendDir "package.json"),
    (Join-Path $frontendDir "node_modules\next\dist\bin\next")
)) {
    if (-not (Test-Path $requiredPath)) {
        throw "Required path not found: $requiredPath. Install the project dependencies and retry."
    }
}

if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw "npm.cmd was not found. Install Node.js and retry."
}

$occupiedPorts = @(
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in $ports }
)

function Test-HealthyService {
    param(
        [string]$Url,
        [string]$ExpectedProperty,
        [object]$ExpectedValue
    )

    try {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec 3
        return $response.$ExpectedProperty -eq $ExpectedValue
    } catch {
        return $false
    }
}

$advisorAlreadyRunning = $false
$clientAlreadyRunning = $false
$uiAlreadyRunning = $false
$conflicts = @()
foreach ($port in ($occupiedPorts | Select-Object -ExpandProperty LocalPort -Unique)) {
    $listeners = @($occupiedPorts | Where-Object { $_.LocalPort -eq $port })
    $healthy = $false

    if ($port -eq $AdvisorPort) {
        $healthy = Test-HealthyService `
            -Url "http://127.0.0.1:$AdvisorPort/health" `
            -ExpectedProperty "status" `
            -ExpectedValue "ok"
        $advisorAlreadyRunning = $healthy
    } elseif ($port -eq $ClientApiPort) {
        $healthy = Test-HealthyService `
            -Url "http://127.0.0.1:$ClientApiPort/api/health" `
            -ExpectedProperty "ok" `
            -ExpectedValue $true
        $clientAlreadyRunning = $healthy
    } elseif ($port -eq $UiPort) {
        try {
            $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$UiPort" -TimeoutSec 3
            $healthy = $page.StatusCode -eq 200 -and $page.Content -match '(/_next/|__next)'
        } catch {
            $healthy = $false
        }
        $uiAlreadyRunning = $healthy
    }

    if (-not $healthy) {
        $conflicts += $listeners | ForEach-Object {
            "port $($_.LocalPort) (PID $($_.OwningProcess))"
        }
    } else {
        Write-Host "Reusing the existing project service on port $port."
    }
}

if ($conflicts.Count -gt 0) {
    throw "Required port(s) already in use by another process: $($conflicts -join ', '). Stop those apps or choose other ports, then retry."
}

$redisComposeFile = Join-Path $advisorDir "docker-compose.redis.yml"
$dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
if ($dockerCommand) {
    $dockerAvailable = $false
    try {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $dockerCommand.Source info --format "{{.ServerVersion}}" 2>&1 | Out-Null
        $dockerAvailable = $LASTEXITCODE -eq 0
    } catch {
        $dockerAvailable = $false
    } finally {
        if ($null -ne $previousErrorActionPreference) {
            $ErrorActionPreference = $previousErrorActionPreference
            Remove-Variable previousErrorActionPreference
        }
    }

    if ($dockerAvailable) {
        & $dockerCommand.Source compose -f $redisComposeFile up -d
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Redis cache is running on 127.0.0.1:6379."
        } else {
            Write-Warning "Could not start Redis. The Advisor will continue without cache; see Customer_Advisor\README.md."
        }
    } else {
        Write-Warning "Docker Desktop is not running. The Advisor will continue without Redis cache. Start Docker Desktop and run 'docker compose -f .\Customer_Advisor\docker-compose.redis.yml up -d' to enable it."
    }
} else {
    Write-Warning "Docker is not installed. The Advisor will continue without Redis cache."
}

function Start-AppTerminal {
    param(
        [string]$Title,
        [string]$WorkingDirectory,
        [string]$Command
    )

    $terminalCommand = "`$host.ui.RawUI.WindowTitle = '$Title'; Set-Location -LiteralPath '$WorkingDirectory'; $Command"
    $encodedCommand = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($terminalCommand)
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", $encodedCommand
    ) | Out-Null
}

function Wait-ForEndpoint {
    param(
        [string]$Url,
        [string]$ServiceName
    )

    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri $Url -TimeoutSec 3 | Out-Null
            Write-Host "$ServiceName is ready at $Url"
            return
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    throw "$ServiceName did not become ready at $Url within 120 seconds. Check its terminal window."
}

$advisorActivation = ".\.venv\Scripts\Activate.ps1"
$clientActivation = ".\.venv\Scripts\Activate.ps1"
$corsOrigins = "http://localhost:$UiPort,http://127.0.0.1:$UiPort"
$advisorCommand = "`$env:API_PORT = '$AdvisorPort'; `$env:CORS_ORIGINS = '$corsOrigins'; . '$advisorActivation'; python app.py"
$clientCommand = "`$env:CORS_ORIGINS = '$corsOrigins'; . '$clientActivation'; python -m uvicorn api:app --host 127.0.0.1 --port $ClientApiPort"
$uiCommand = "`$env:NEXT_PUBLIC_ADVISOR_API_URL = 'http://127.0.0.1:$AdvisorPort'; `$env:NEXT_PUBLIC_CLIENT_API_URL = 'http://127.0.0.1:$ClientApiPort'; npm.cmd run dev -- --hostname 127.0.0.1 --port $UiPort"

Write-Host "Starting the Customer Advisor API, Client Simulator API, and Relay dashboard..."
if (-not $advisorAlreadyRunning) {
    Start-AppTerminal -Title "Customer Advisor API :$AdvisorPort" -WorkingDirectory $advisorDir -Command $advisorCommand
}
if (-not $clientAlreadyRunning) {
    Start-AppTerminal -Title "Client Simulator API :$ClientApiPort" -WorkingDirectory $clientDir -Command $clientCommand
}

Wait-ForEndpoint -Url "http://127.0.0.1:$AdvisorPort/health" -ServiceName "Customer Advisor API"
Wait-ForEndpoint -Url "http://127.0.0.1:$ClientApiPort/api/health" -ServiceName "Client Simulator API"

if (-not $uiAlreadyRunning) {
    Start-AppTerminal -Title "Relay Agent Communication UI :$UiPort" -WorkingDirectory $frontendDir -Command $uiCommand
}
Wait-ForEndpoint -Url "http://127.0.0.1:$UiPort" -ServiceName "Relay dashboard"

Write-Host ""
Write-Host "Relay dashboard: http://127.0.0.1:$UiPort"
Write-Host "Customer Advisor API: http://127.0.0.1:$AdvisorPort"
Write-Host "Client Simulator API: http://127.0.0.1:$ClientApiPort"
Write-Host "Use the dashboard to choose a scenario, mood, language, and response mode."
Write-Host "Close each service window or press Ctrl+C in it to stop that process."
