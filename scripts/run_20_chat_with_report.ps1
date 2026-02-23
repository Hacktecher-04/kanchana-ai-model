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

& .\scripts\run_20_chat.ps1 -Turns $Turns -ApiUrl $ApiUrl -MaxHistory $MaxHistory -UseExistingApi:$UseExistingApi

$latest = Get-ChildItem transcripts\chat_20_*.txt -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if (-not $latest) {
  Write-Error "No chat transcript found in transcripts\chat_20_*.txt"
  exit 1
}

Write-Host "Analyzing transcript: $($latest.FullName)"
& $python scripts\analyze_chat_transcript.py --input-file $latest.FullName

