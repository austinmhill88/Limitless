Set-Location "C:\Users\austi\OneDrive\Desktop\Limitless"

# Open local UI on the PC after a short delay
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Start-Process "http://127.0.0.1:8000/ui"
} | Out-Null

# Start the server (no Tailscale/Cloudflare)
& "C:\Users\austi\miniconda3\envs\limitless\python.exe" -m uvicorn --app-dir src bot.api.server:app --host 0.0.0.0 --port 8000 --log-level info