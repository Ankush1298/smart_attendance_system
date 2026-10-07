#!/usr/bin/env bash
# Smart Attendance setup for macOS / Linux. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "Python 3.10+ is required (install it, or set PYTHON=/path/to/python)"; exit 1; }
"$PY" - <<'PYEOF' || { echo "Python 3.10 or newer is required."; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PYEOF

if [ ! -d .venv ]; then echo "==> creating virtual environment (.venv)"; "$PY" -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "==> installing dependencies"
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  python - <<'PYEOF'
import re, secrets, pathlib
p = pathlib.Path(".env"); s = p.read_text()
s = re.sub(r"^APP_SECRET_KEY=.*$", "APP_SECRET_KEY=" + secrets.token_hex(32), s, flags=re.M)
p.write_text(s)
PYEOF
  chmod 600 .env
  echo "==> created .env (secret key generated)."
  echo "    EDIT .env NOW: set MYSQL_PASSWORD, ADMIN_PASSWORD and APP_TIMEZONE."
else
  echo "==> .env already exists (left unchanged)"
fi
mkdir -p logs
echo "==> running doctor"
python doctor.py --migrate || { echo; echo "Fix the problems above, then run: source .venv/bin/activate && python doctor.py --migrate"; exit 1; }
echo
echo "Setup complete. Start the application with:"
echo "    source .venv/bin/activate && python server.py"
