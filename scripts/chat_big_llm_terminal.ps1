param(
  [ValidateSet("api", "direct")]
  [string]$Engine = "api",
  [string]$ApiUrl = "http://127.0.0.1:8000",
  [switch]$UseExistingApi,
  [string]$AppApiKey = "",
  [string]$ClientSecret = "",
  [string]$ModelPath = "models/qwen2.5-7b-instruct-q3_k_m.gguf",
  [int]$Port = 8085,
  [int]$Ctx = 4096,
  [int]$Threads = 8,
  [string]$SystemInstruction = "",
  [int]$MaxHistoryMessages = 80,
  [switch]$ShowMeta
)

$ErrorActionPreference = "Stop"

function Get-EnvValue {
  param([string]$Name)
  $v = [Environment]::GetEnvironmentVariable($Name, "Process")
  if (-not [string]::IsNullOrWhiteSpace($v)) {
    return $v.Trim()
  }
  if (-not (Test-Path ".env")) {
    return ""
  }
  $line = Get-Content ".env" | Where-Object { $_ -match ('^\s*' + [regex]::Escape($Name) + '=') } | Select-Object -First 1
  if (-not $line) {
    return ""
  }
  return (($line -split '=', 2)[1]).Trim()
}

if ([string]::IsNullOrWhiteSpace($SystemInstruction)) {
  if (Test-Path "prompts/system_prompt.txt") {
    $SystemInstruction = (Get-Content "prompts/system_prompt.txt" -Raw).Trim()
  }
  if ([string]::IsNullOrWhiteSpace($SystemInstruction)) {
    $SystemInstruction = "You are concise, natural, and conversational."
  }
}

$apiProc = $null
$llamaProc = $null

function Test-ApiHealth {
  param([string]$BaseUrl)
  try {
    $h = Invoke-RestMethod -Method Get -Uri "$BaseUrl/health" -TimeoutSec 5
    return ($h.status -eq "ok")
  } catch {
    return $false
  }
}

function Test-ApiChat {
  param(
    [string]$BaseUrl,
    [string]$ApiKey,
    [string]$Secret
  )
  if ([string]::IsNullOrWhiteSpace($ApiKey) -or [string]::IsNullOrWhiteSpace($Secret)) {
    return $false
  }
  try {
    $headers = @{
      "x-api-key" = $ApiKey
      "x-client-secret" = $Secret
      "content-type" = "application/json"
    }
    $body = @{
      message = "ping"
      history = @()
      max_tokens = 24
    } | ConvertTo-Json -Depth 6 -Compress
    $r = Invoke-RestMethod -Method Post -Uri "$BaseUrl/v1/chat-text" -Headers $headers -Body $body -TimeoutSec 45
    return -not [string]::IsNullOrWhiteSpace([string]$r)
  } catch {
    return $false
  }
}

try {
  if ($Engine -eq "api") {
    if ([string]::IsNullOrWhiteSpace($AppApiKey)) {
      $AppApiKey = Get-EnvValue -Name "APP_API_KEY"
    }
    if ([string]::IsNullOrWhiteSpace($ClientSecret)) {
      $ClientSecret = Get-EnvValue -Name "APP_CLIENT_SECRET"
    }
    if ([string]::IsNullOrWhiteSpace($AppApiKey) -or [string]::IsNullOrWhiteSpace($ClientSecret)) {
      throw "APP_API_KEY / APP_CLIENT_SECRET required for API mode."
    }

    $apiUri = [System.Uri]$ApiUrl
    $isLocalApi = $apiUri.Host -in @("127.0.0.1", "localhost")
    $healthy = Test-ApiHealth -BaseUrl $ApiUrl
    $chatReady = Test-ApiChat -BaseUrl $ApiUrl -ApiKey $AppApiKey -Secret $ClientSecret

    if ((-not $healthy -or -not $chatReady) -and $isLocalApi -and -not $UseExistingApi) {
      $python = ".\.venv\Scripts\python.exe"
      if (-not (Test-Path $python)) {
        throw ".venv python not found. Create venv first."
      }
      Remove-Item "server_big.out.log" -ErrorAction SilentlyContinue
      Remove-Item "server_big.err.log" -ErrorAction SilentlyContinue

      $host = if ([string]::IsNullOrWhiteSpace($apiUri.Host)) { "127.0.0.1" } else { $apiUri.Host }
      $apiPort = if ($apiUri.IsDefaultPort) { 8000 } else { $apiUri.Port }
      $apiProc = Start-Process -FilePath $python -ArgumentList @(
        "-m", "uvicorn", "api_service:app",
        "--host", $host,
        "--port", "$apiPort",
        "--env-file", ".env"
      ) -PassThru -RedirectStandardOutput "server_big.out.log" -RedirectStandardError "server_big.err.log"

      $ready = $false
      for ($i = 0; $i -lt 300; $i++) {
        Start-Sleep -Milliseconds 500
        if ($apiProc.HasExited) {
          $errTail = if (Test-Path "server_big.err.log") {
            (Get-Content "server_big.err.log" -Tail 80) -join "`n"
          } else {
            "<no server_big.err.log>"
          }
          throw "API process exited early. Last stderr:`n$errTail"
        }
        $healthy = Test-ApiHealth -BaseUrl $ApiUrl
        $chatReady = Test-ApiChat -BaseUrl $ApiUrl -ApiKey $AppApiKey -Secret $ClientSecret
        if ($healthy -and $chatReady) {
          $ready = $true
          break
        }
      }
      if (-not $ready) {
        throw "API did not become ready at $ApiUrl"
      }
    } elseif ((-not $healthy -or -not $chatReady) -and $UseExistingApi) {
      throw "UseExistingApi set, but API not ready at $ApiUrl"
    }

    $headers = @{
      "x-api-key" = $AppApiKey
      "x-client-secret" = $ClientSecret
      "content-type" = "application/json"
    }

    $history = @()
    Write-Host "API chat ready. Engine=api. Commands: /exit, /reset, /system <new instruction>"
    while ($true) {
      $userText = Read-Host "you"
      if ([string]::IsNullOrWhiteSpace($userText)) {
        continue
      }
      $trimmed = $userText.Trim()
      if ($trimmed.ToLower() -eq "/exit") {
        break
      }
      if ($trimmed.ToLower() -eq "/reset") {
        $history = @()
        Write-Host "ai> history reset ho gayi."
        continue
      }
      if ($trimmed.ToLower().StartsWith("/system ")) {
        $newInstruction = $trimmed.Substring(8).Trim()
        if ([string]::IsNullOrWhiteSpace($newInstruction)) {
          Write-Host "ai> system instruction empty nahi ho sakti."
        } else {
          $SystemInstruction = $newInstruction
          Write-Host ("ai> system instruction updated")
        }
        continue
      }

      $payload = @{
        message = $userText
        system_prompt = $SystemInstruction
        history = $history
        max_tokens = 220
        temperature = 0.6
        top_p = 0.9
      } | ConvertTo-Json -Depth 10 -Compress

      $resp = Invoke-RestMethod -Method Post -Uri "$ApiUrl/v1/chat" -Headers $headers -Body $payload -TimeoutSec 900
      $reply = [string]$resp.reply
      if ([string]::IsNullOrWhiteSpace($reply)) {
        $reply = "(empty reply)"
      }
      if ($ShowMeta) {
        Write-Host ("meta> model=" + [string]$resp.model + " history_msgs=" + $history.Count)
      }
      Write-Host ("ai> " + $reply)

      $history += @{ role = "user"; content = $userText }
      $history += @{ role = "assistant"; content = $reply }
      if ($MaxHistoryMessages -gt 0 -and $history.Count -gt $MaxHistoryMessages) {
        $history = $history[-$MaxHistoryMessages..-1]
      }
    }
  } else {
    $llamaApiKey = "local-big-chat"
    $baseUrl = "http://127.0.0.1:$Port"

    $llamaProc = Start-Process `
      -FilePath "bin\llama-cpp\llama-server.exe" `
      -ArgumentList "-m $ModelPath --host 127.0.0.1 --port $Port --ctx-size $Ctx --threads $Threads --n-gpu-layers 0 --api-key $llamaApiKey" `
      -PassThru `
      -RedirectStandardOutput "llama_direct.out.log" `
      -RedirectStandardError "llama_direct.err.log"

    $ready = $false
    for ($i = 0; $i -lt 360; $i++) {
      Start-Sleep -Seconds 2
      try {
        $health = Invoke-RestMethod -Method Get -Uri "$baseUrl/health" -TimeoutSec 5
        if ($health.status -eq "ok") {
          $ready = $true
          break
        }
      } catch {
      }
    }

    if (-not $ready) {
      Write-Host "Model server ready nahi hua. logs: llama_direct.err.log"
      exit 1
    }

    $headers = @{
      "Authorization" = "Bearer $llamaApiKey"
      "x-api-key" = $llamaApiKey
      "content-type" = "application/json"
    }

    $history = @()
    Write-Host "Direct LLM chat ready. Engine=direct. Commands: /exit, /reset, /system <new instruction>"
    while ($true) {
      $userText = Read-Host "you"
      if ([string]::IsNullOrWhiteSpace($userText)) {
        continue
      }
      $trimmed = $userText.Trim()
      if ($trimmed.ToLower() -eq "/exit") {
        break
      }
      if ($trimmed.ToLower() -eq "/reset") {
        $history = @()
        Write-Host "ai> history reset ho gayi."
        continue
      }
      if ($trimmed.ToLower().StartsWith("/system ")) {
        $newInstruction = $trimmed.Substring(8).Trim()
        if ([string]::IsNullOrWhiteSpace($newInstruction)) {
          Write-Host "ai> system instruction empty nahi ho sakti."
        } else {
          $SystemInstruction = $newInstruction
          Write-Host ("ai> system instruction updated")
        }
        continue
      }

      $messages = @(@{ role = "system"; content = $SystemInstruction }) + $history + @(@{ role = "user"; content = $userText })
      if ($ShowMeta) {
        Write-Host ("meta> system=1 history_msgs=" + $history.Count + " total_msgs=" + $messages.Count)
      }
      $payload = @{
        model = "local"
        messages = $messages
        temperature = 0.6
        top_p = 0.9
        max_tokens = 220
      } | ConvertTo-Json -Depth 8 -Compress

      $resp = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/chat/completions" -Headers $headers -Body $payload -TimeoutSec 900
      $reply = [string]$resp.choices[0].message.content
      if ([string]::IsNullOrWhiteSpace($reply)) {
        $reply = "(empty reply)"
      }
      Write-Host ("ai> " + $reply)

      $history += @{ role = "user"; content = $userText }
      $history += @{ role = "assistant"; content = $reply }
      if ($MaxHistoryMessages -gt 0 -and $history.Count -gt $MaxHistoryMessages) {
        $history = $history[-$MaxHistoryMessages..-1]
      }
    }
  }
}
finally {
  if ($llamaProc -and -not $llamaProc.HasExited) {
    Stop-Process -Id $llamaProc.Id -Force -ErrorAction SilentlyContinue
  }
  if ($apiProc -and -not $apiProc.HasExited) {
    Stop-Process -Id $apiProc.Id -Force -ErrorAction SilentlyContinue
  }
}
