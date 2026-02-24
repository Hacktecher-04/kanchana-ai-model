param(
  [string]$ModelPath = "models/qwen2.5-7b-instruct-q3_k_m.gguf",
  [int]$Port = 8085,
  [int]$Ctx = 1024,
  [int]$Threads = 8,
  [string]$SystemInstruction = "You are concise, natural, and conversational.",
  [int]$MaxHistoryMessages = 80,
  [switch]$ShowMeta
)

$apiKey = "local-big-chat"
$baseUrl = "http://127.0.0.1:$Port"

$proc = Start-Process `
  -FilePath "bin\llama-cpp\llama-server.exe" `
  -ArgumentList "-m $ModelPath --host 127.0.0.1 --port $Port --ctx-size $Ctx --threads $Threads --n-gpu-layers 0 --api-key $apiKey" `
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
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  exit 1
}

$headers = @{
  "Authorization" = "Bearer $apiKey"
  "content-type" = "application/json"
}

$history = @()

Write-Host "Big LLM chat ready. Commands: /exit, /reset, /system <new instruction>"
try {
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
        Write-Host ("ai> system instruction updated: " + $SystemInstruction)
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
finally {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
}
