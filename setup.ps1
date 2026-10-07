# Smart Attendance setup for Windows (PowerShell). Safe to re-run.
#   Set-ExecutionPolicy -Scope Process Bypass; .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
try { & $py -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" } catch { throw "Python 3.10+ is required (https://www.python.org/downloads/)." }
if ($LASTEXITCODE -ne 0) { throw "Python 3.10 or newer is required." }

if (-not (Test-Path ".venv")) { Write-Host "==> creating virtual environment (.venv)"; & $py -m venv .venv }
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Write-Host "==> installing dependencies"
& $venvPy -m pip install --upgrade pip | Out-Null
& $venvPy -m pip install -r requirements.txt
& $venvPy -m pip install tzdata | Out-Null   # IANA time zones are not built into Windows

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  $key = & $venvPy -c "import secrets;print(secrets.token_hex(32))"
  (Get-Content ".env") -replace "^APP_SECRET_KEY=.*$", "APP_SECRET_KEY=$key" | Set-Content ".env"
  Write-Host "==> created .env (secret key generated)."
  Write-Host "    EDIT .env NOW: set MYSQL_PASSWORD, ADMIN_PASSWORD and APP_TIMEZONE."
} else { Write-Host "==> .env already exists (left unchanged)" }
New-Item -ItemType Directory -Force -Path logs | Out-Null
Write-Host "==> running doctor"
& $venvPy doctor.py --migrate
if ($LASTEXITCODE -ne 0) { Write-Host "`nFix the problems above, then run: .\.venv\Scripts\python.exe doctor.py --migrate"; exit 1 }
Write-Host "`nSetup complete. Start the application with:"
Write-Host "    .\.venv\Scripts\python.exe server.py"
