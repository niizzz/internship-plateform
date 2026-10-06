#!/bin/bash
# macOS launcher (the Mac twin of launch.bat + setup.bat).
# First run: installs everything WITHOUT an admin password or Homebrew —
#   Python 3.14 via uv (into ~/.local), Node.js into ./.tools/node,
#   the backend venv, the scrapers' Chromium, and the frontend packages.
# Every run: starts backend (:8000) + frontend (:5173) and opens the browser.
# Closing this window stops the platform.

cd "$(dirname "$0")" || exit 1
ROOT="$(pwd)"
export PATH="$HOME/.local/bin:$ROOT/.tools/node/bin:$PATH"

fail() {
  echo
  echo "!!! $1"
  echo "Take a screenshot of this window and send it over."
  read -r -p "Press Enter to close..."
  exit 1
}

node_ok() {
  command -v npm >/dev/null 2>&1 && \
    [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge 18 ]
}

if [ ! -f backend/.venv/.setup-ok ]; then
  echo
  echo " === Internship Plateform - first-time setup ==="
  echo " This takes 3-10 minutes. Leave this window open."
  echo

  if ! command -v uv >/dev/null 2>&1; then
    echo "[1/5] Installing uv (Python installer, no admin needed)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || fail "Could not install uv - check the internet connection."
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || fail "uv installed but not found."
  fi

  echo "[2/5] Installing Python 3.14 + packages..."
  uv python install 3.14 || fail "Could not install Python 3.14."
  if [ ! -x backend/.venv/bin/python ]; then
    uv venv --python 3.14 backend/.venv || fail "Could not create the Python environment."
  fi
  uv pip install --python backend/.venv/bin/python -r backend/requirements.txt \
    || fail "Could not install the Python packages."

  echo "[3/5] Installing the scraper browser (Chromium)..."
  backend/.venv/bin/python -m playwright install chromium || fail "Could not install Chromium."

  if ! node_ok; then
    echo "[4/5] Installing Node.js (into this folder)..."
    case "$(uname -m)" in arm64) NARCH=arm64 ;; *) NARCH=x64 ;; esac
    BASE="https://nodejs.org/dist/latest-v22.x"
    TARBALL=$(curl -fsSL "$BASE/SHASUMS256.txt" | awk -v a="darwin-$NARCH.tar.gz" '$2 ~ a"$" {print $2}')
    [ -n "$TARBALL" ] || fail "Could not find a Node.js download."
    rm -rf .tools/node && mkdir -p .tools
    curl -fsSL "$BASE/$TARBALL" | tar -xz -C .tools || fail "Could not download Node.js."
    mv ".tools/${TARBALL%.tar.gz}" .tools/node || fail "Could not unpack Node.js."
    node_ok || fail "Node.js installed but not working."
  else
    echo "[4/5] Node.js OK"
  fi

  echo "[5/5] Installing the website packages..."
  (cd frontend && npm ci --no-audit --no-fund) || fail "Could not install the website packages."

  touch backend/.venv/.setup-ok
  echo
  echo " === Setup complete ==="
  echo
fi

PIDS=()
cleanup() { [ ${#PIDS[@]} -gt 0 ] && kill "${PIDS[@]}" 2>/dev/null; }
trap cleanup EXIT INT TERM HUP

if curl -s -o /dev/null http://127.0.0.1:8000/api/refresh/status; then
  echo "Backend already running."
else
  (cd backend && exec .venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000) &
  PIDS+=($!)
fi

if curl -s -o /dev/null http://localhost:5173; then
  echo "Frontend already running."
else
  (cd frontend && exec npm run dev) &
  PIDS+=($!)
fi

echo "Starting Internship Plateform..."
for _ in $(seq 1 30); do
  curl -s -o /dev/null http://localhost:5173 && break
  sleep 1
done
open http://localhost:5173

echo
echo " Platform running at http://localhost:5173"
echo " Keep this window open while you use it. Close it to stop the platform."
echo
wait
