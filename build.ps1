# Сборка IP-widget.exe
# Запуск:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($candidate in @("py", "python")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $py = $candidate; break }
}
if (-not $py) { throw "Не найден Python. Установите Python 3 с python.org" }

Write-Host "Python: $py" -ForegroundColor Cyan
& $py -m pip install --upgrade pillow pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }

& $py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "IP-widget" `
    --icon "icon.ico" `
    --add-data "countries_ru.json;." `
    "ip_widget.pyw"
if ($LASTEXITCODE -ne 0) { throw "Сборка не удалась" }

$exe = Join-Path $PSScriptRoot "dist\IP-widget.exe"
if (-not (Test-Path $exe)) { throw "exe не появился: $exe" }
Write-Host ("Готово: {0} ({1:N1} МБ)" -f $exe, ((Get-Item $exe).Length / 1MB)) -ForegroundColor Green
