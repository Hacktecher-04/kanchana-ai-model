param(
  [int]$Count = 50,
  [string]$ApiUrl = "http://127.0.0.1:8000",
  [int]$MaxHistory = 16,
  [int]$Seed = 42,
  [switch]$UseHttpApi,
  [switch]$UseExistingApi
)

$ErrorActionPreference = "Stop"

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  Write-Error ".venv Python not found. Create venv first."
  exit 1
}

$apiUri = [System.Uri]$ApiUrl
$apiHost = if ([string]::IsNullOrWhiteSpace($apiUri.Host)) { "127.0.0.1" } else { $apiUri.Host }
$apiPort = if ($apiUri.IsDefaultPort) { 8000 } else { $apiUri.Port }
$apiProc = $null

function Get-AppApiKey {
  if ($env:APP_API_KEY) {
    return $env:APP_API_KEY.Trim()
  }
  if (-not (Test-Path ".env")) {
    return ""
  }
  $line = Get-Content ".env" | Where-Object { $_ -match '^\s*APP_API_KEY=' } | Select-Object -First 1
  if (-not $line) {
    return ""
  }
  return (($line -split '=', 2)[1]).Trim()
}

function Get-ClientSecret {
  if ($env:APP_CLIENT_SECRET) {
    return $env:APP_CLIENT_SECRET.Trim()
  }
  if (-not (Test-Path ".env")) {
    return ""
  }
  $line = Get-Content ".env" | Where-Object { $_ -match '^\s*APP_CLIENT_SECRET=' } | Select-Object -First 1
  if (-not $line) {
    return ""
  }
  return (($line -split '=', 2)[1]).Trim()
}

function Test-ApiHealth {
  param([string]$BaseUrl)
  try {
    $h = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health" -TimeoutSec 4
    return ($h.status -eq "ok")
  } catch {
    return $false
  }
}

try {
  if (-not $UseHttpApi) {
    & $python "scripts\run_random_demo.py" --count $Count --max-history $MaxHistory --seed $Seed --direct
    exit $LASTEXITCODE
  }

  $apiKey = Get-AppApiKey
  $clientSecret = Get-ClientSecret
  if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw "APP_API_KEY not found in environment or .env"
  }
  if ([string]::IsNullOrWhiteSpace($clientSecret)) {
    throw "APP_CLIENT_SECRET not found in environment or .env"
  }

  $healthy = Test-ApiHealth -BaseUrl $ApiUrl
  if (-not $healthy -and -not $UseExistingApi) {
    Write-Host "Starting API on $apiHost`:$apiPort ..."
    Remove-Item "server_test.out.log" -ErrorAction SilentlyContinue
    Remove-Item "server_test.err.log" -ErrorAction SilentlyContinue
    $apiProc = Start-Process -FilePath $python -ArgumentList @(
      "-m", "uvicorn", "api_service:app",
      "--host", $apiHost,
      "--port", "$apiPort",
      "--env-file", ".env"
    ) -PassThru -RedirectStandardOutput "server_test.out.log" -RedirectStandardError "server_test.err.log"

    $ready = $false
    for ($i = 0; $i -lt 240; $i++) {
      Start-Sleep -Milliseconds 500
      if ($apiProc.HasExited) {
        $errTail = if (Test-Path "server_test.err.log") {
          (Get-Content "server_test.err.log" -Tail 60) -join "`n"
        } else {
          "<no server_test.err.log>"
        }
        throw "API process exited early. Last stderr:`n$errTail"
      }
      if (Test-ApiHealth -BaseUrl $ApiUrl) {
        $ready = $true
        break
      }
    }
    if (-not $ready) {
      throw "API did not become ready at $ApiUrl"
    }
  } elseif (-not $healthy -and $UseExistingApi) {
    throw "UseExistingApi set but API is not reachable at $ApiUrl"
  }

  & $python "scripts\run_random_demo.py" --api-url $ApiUrl --api-key $apiKey --client-secret $clientSecret --count $Count --max-history $MaxHistory --seed $Seed
}
finally {
  if ($apiProc -and -not $apiProc.HasExited) {
    Stop-Process -Id $apiProc.Id -Force
  }
}
