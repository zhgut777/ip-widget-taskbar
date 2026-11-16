# Сборка IP-widget.exe (один файл, без установки).
# Запуск:  powershell -ExecutionPolicy Bypass -File build.ps1
# Параметр -SkipTests пропускает прогон тестов перед сборкой.
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($candidate in @("py", "python")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $py = $candidate; break }
}
if (-not $py) { throw "Не найден Python 3. Установите его с python.org" }
Write-Host "Python: $py" -ForegroundColor Cyan

& $py -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }

if (-not $SkipTests) {
    & $py -m unittest discover -s tests -t .
    if ($LASTEXITCODE -ne 0) { throw "Тесты не прошли — сборка остановлена" }
}

& $py packaging\make_version_file.py packaging\version_info.txt
if ($LASTEXITCODE -ne 0) { throw "Не удалось создать файл версии" }

# Лишнее не тащим в exe: меньше размер и быстрее распаковка при запуске.
$exclude = @("tkinter", "unittest", "pydoc", "doctest", "test", "setuptools", "pkg_resources",
             "distutils", "numpy", "PIL.ImageTk", "PIL._imagingtk", "PIL.ImageQt")
$excludeArgs = $exclude | ForEach-Object { "--exclude-module"; $_ }

& $py -m PyInstaller --noconfirm --clean --onefile --windowed --optimize 2 `
    @excludeArgs `
    --name "IP-widget" `
    --icon "icon.ico" `
    --version-file "packaging\version_info.txt" `
    --add-data "ipwidget\data;ipwidget\data" `
    "ip_widget.pyw"
if ($LASTEXITCODE -ne 0) { throw "Сборка не удалась" }

$exe = Join-Path $PSScriptRoot "dist\IP-widget.exe"
if (-not (Test-Path $exe)) { throw "exe не появился: $exe" }

# Контрольная сумма рядом с файлом: её же выкладывает CI в релиз.
$hash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
"$hash *IP-widget.exe" | Set-Content -Path "$exe.sha256" -Encoding ascii
Write-Host ("Готово: {0} ({1:N1} МБ)" -f $exe, ((Get-Item $exe).Length / 1MB)) -ForegroundColor Green
Write-Host "SHA-256: $hash"
