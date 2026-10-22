param(
    [ValidateSet("normal", "fin_de_mois", "panne", "campagne", "vague_de_fraude", "contestation_carte", "vishing_otp", "virement_sans_verification")]
    [string]$Scenario = "normal",
    [ValidateRange(-1.0, 1.0)]
    [double]$Mood = 0,
    [ValidateSet("auto", "fr", "darija", "en")]
    [string]$Language = "auto",
    [ValidateRange(0.0, 1.0)]
    [double]$Difficulty = 0,
    [string]$Seed = "",
    [ValidateRange(1, 100)]
    [int]$MaxTurns = 8,
    [ValidateSet("rules", "auto", "claude", "gemini", "groq", "ollama")]
    [string]$ClientLlm = "auto",
    [ValidateRange(1024, 65535)]
    [int]$AdvisorPort = 8000
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$clientDir = Join-Path $root "Client_Simulator"
$advisorDir = Join-Path $root "Customer_Advisor"
$activationScript = ".\.venv\Scripts\Activate.ps1"
$ports = @($AdvisorPort)

foreach ($requiredPath in @(
    (Join-Path $clientDir ".venv\Scripts\Activate.ps1"),
    (Join-Path $advisorDir ".venv\Scripts\Activate.ps1"),
    (Join-Path $PSScriptRoot "run_simulation.py")
)) {
    if (-not (Test-Path $requiredPath)) {
        throw "Required path not found: $requiredPath"
    }
}

$occupiedPorts = @(
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in $ports }
)
if ($occupiedPorts.Count -gt 0) {
    $details = $occupiedPorts | ForEach-Object {
        "port $($_.LocalPort) (PID $($_.OwningProcess))"
    }
    throw "Required port(s) already in use: $($details -join ', '). Stop those apps or free the ports, then retry."
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

$advisorCommand = "`$env:API_PORT = '$AdvisorPort'; . '$activationScript'; python app.py"

Write-Host "Starting Customer Advisor API and automatic Client Simulator interaction..."
Start-AppTerminal -Title "Customer Advisor API :$AdvisorPort" -WorkingDirectory $advisorDir -Command $advisorCommand

Wait-ForEndpoint -Url "http://127.0.0.1:$AdvisorPort/health" -ServiceName "Customer Advisor API"

$pipelineArguments = @(
    "--scenario", $Scenario,
    "--mood", $Mood.ToString([Globalization.CultureInfo]::InvariantCulture),
    "--language", $Language,
    "--difficulty", $Difficulty.ToString([Globalization.CultureInfo]::InvariantCulture),
    "--max-turns", "$MaxTurns",
    "--client-llm", $ClientLlm,
    "--advisor-url", "http://127.0.0.1:$AdvisorPort"
)
if ($Seed -ne "") {
    $pipelineArguments += @("--seed", $Seed)
}
$quotedPipelineArguments = ($pipelineArguments | ForEach-Object {
    "'" + $_.Replace("'", "''") + "'"
}) -join " "
$pipelineCommand = ". '$activationScript'; python '$($PSScriptRoot)\run_simulation.py' $quotedPipelineArguments"
Start-AppTerminal -Title "Agent Communication Pipeline" -WorkingDirectory $clientDir -Command $pipelineCommand

Write-Host ""
Write-Host "Customer Advisor API: http://127.0.0.1:$AdvisorPort"
Write-Host "The Client Simulator customer is already communicating with Customer Advisor."
Write-Host "Pending financial actions are automatically declined in simulation mode."
Write-Host "Close each service window or press Ctrl+C in it to stop that process."
