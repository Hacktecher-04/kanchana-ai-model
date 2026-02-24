$apiKeyLine = Get-Content .env | Where-Object { $_ -like "APP_API_KEY=*" }
$secretLine = Get-Content .env | Where-Object { $_ -like "APP_CLIENT_SECRET=*" }
$apiKey = ($apiKeyLine -split "=", 2)[1]
$clientSecret = ($secretLine -split "=", 2)[1]

$proc = Start-Process `
  -FilePath ".\.venv\Scripts\python.exe" `
  -ArgumentList "-m uvicorn api_service:app --host 127.0.0.1 --port 8000 --env-file .env" `
  -PassThru `
  -RedirectStandardOutput "server_big.out.log" `
  -RedirectStandardError "server_big.err.log"

$ready = $false
for ($i = 0; $i -lt 240; $i++) {
  Start-Sleep -Seconds 2
  try {
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5
    if ($health.status -eq "ok") {
      $ready = $true
      break
    }
  } catch {
  }
}

if (-not $ready) {
  Write-Host "HEALTH_NOT_READY"
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  exit 1
}

$headers = @{
  "x-api-key" = $apiKey
  "x-client-secret" = $clientSecret
  "content-type" = "application/json"
}
$body = '{"message":"Draft two concise taglines for a fictional tea brand called River Ember.","history":[]}'
$resp = $null
$tries = 0
while ($tries -lt 60) {
  $tries += 1
  $resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/v1/chat" -Headers $headers -Body $body -TimeoutSec 900
  if ($resp.model -ne "fast-fallback-runtime") {
    break
  }
  Start-Sleep -Seconds 10
}
Write-Host ("TRIES=" + $tries)
Write-Host ("MODEL=" + $resp.model)
Write-Host ("REPLY=" + $resp.reply)

Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
