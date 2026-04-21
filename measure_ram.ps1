$process = Start-Process -FilePath ".\venv\Scripts\python.exe" -ArgumentList "run.py", "--once" -PassThru -NoNewWindow
$maxMemory = 0
while (!$process.HasExited) {
    $mem = (Get-Process python, chrome, chromedriver -ErrorAction SilentlyContinue | Measure-Object -Property WorkingSet -Sum).Sum / 1MB
    if ($mem -gt $maxMemory) { $maxMemory = $mem }
    Start-Sleep -Seconds 1
}
Write-Host "Peak Memory Consumed: $maxMemory MB"
