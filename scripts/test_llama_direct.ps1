$apiKey = "local-big-test"
$proc = Start-Process `
  -FilePath "bin\llama-cpp\llama-server.exe" `
  -ArgumentList "-m models/qwen2.5-7b-instruct-q3_k_m.gguf --host 127.0.0.1 --port 8085 --ctx-size 1024 --threads 8 --n-gpu-layers 0 --api-key $apiKey" `
  -PassThru `
  -RedirectStandardOutput "llama_direct.out.log" `
  -RedirectStandardError "llama_direct.err.log"

$ready = $false
for ($i = 0; $i -lt 360; $i++) {
  Start-Sleep -Seconds 2
  try {
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8085/health" -TimeoutSec 5
    if ($health.status -eq "ok") {
      $ready = $true
      break
    }
  } catch {
  }
}

if (-not $ready) {
  Write-Host "LLAMA_HEALTH_NOT_READY"
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  exit 1
}

$headers = @{
  "Authorization" = "Bearer $apiKey"
  "content-type" = "application/json"
}
$body = '{"model":"local","messages":[{"role":"system","content":"You are concise."},{"role":"user","content":"Write two short taglines for River Ember tea."}],"temperature":0.5,"top_p":0.9,"max_tokens":48}'
$resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8085/v1/chat/completions" -Headers $headers -Body $body -TimeoutSec 900
$text = $resp.choices[0].message.content
Write-Host ("GEN=" + $text)

Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
