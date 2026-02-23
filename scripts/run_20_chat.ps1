param(
  [int]$Turns = 20,
  [string]$ApiUrl = "http://127.0.0.1:8000",
  [int]$MaxHistory = 80,
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

function Test-ApiChat {
  param(
    [string]$BaseUrl,
    [string]$ApiKey,
    [string]$ClientSecret
  )
  if ([string]::IsNullOrWhiteSpace($ApiKey) -or [string]::IsNullOrWhiteSpace($ClientSecret)) {
    return $false
  }
  try {
    $headers = @{
      "x-api-key" = $ApiKey
      "x-client-secret" = $ClientSecret
      "content-type" = "application/json"
    }
    $body = @{
      message = "ping"
      history = @()
      max_tokens = 24
    } | ConvertTo-Json -Depth 6
    $resp = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/chat-text" -Headers $headers -Body $body -TimeoutSec 30
    return -not [string]::IsNullOrWhiteSpace([string]$resp)
  } catch {
    return $false
  }
}

try {
  $apiKey = Get-AppApiKey
  $clientSecret = Get-ClientSecret
  $healthy = Test-ApiHealth -BaseUrl $ApiUrl
  $chatReady = Test-ApiChat -BaseUrl $ApiUrl -ApiKey $apiKey -ClientSecret $clientSecret

  if ((-not $healthy -or -not $chatReady) -and -not $UseExistingApi) {
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

      $healthy = Test-ApiHealth -BaseUrl $ApiUrl
      $chatReady = Test-ApiChat -BaseUrl $ApiUrl -ApiKey $apiKey -ClientSecret $clientSecret
      if ($healthy -and $chatReady) {
        $ready = $true
        break
      }
    }

    if (-not $ready) {
      throw "API did not become ready at $ApiUrl"
    }
  } elseif ((-not $healthy -or -not $chatReady) -and $UseExistingApi) {
    throw "UseExistingApi set, but API is not fully reachable at $ApiUrl (health/chat preflight failed)."
  }

  & $python "scripts\chat_20_terminal.py" --api-url $ApiUrl --turns $Turns --max-history $MaxHistory --api-key $apiKey --client-secret $clientSecret
}
finally {
  if ($apiProc -and -not $apiProc.HasExited) {
    Stop-Process -Id $apiProc.Id -Force
  }
}
