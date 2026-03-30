#!/usr/bin/env bash
# VisionDock quick health check after install or on the factory floor.
# Exit 0 = basic checks passed, 1 = critical failure.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FAIL=0

echo "[1/5] Python syntax (gui/main.py)…"
if python3 -m py_compile gui/main.py 2>/dev/null; then
  echo "      OK"
else
  echo "      ERROR: py_compile failed"
  FAIL=1
fi

echo "[2/5] PySide6 import…"
if python3 -c "from PySide6.QtWidgets import QApplication" 2>/dev/null; then
  echo "      OK"
else
  echo "      WARN: PySide6 missing (GUI will not run)"
  FAIL=1
fi

echo "[3/5] OpenCV…"
if python3 -c "import cv2; print('      cv2', cv2.__version__)" 2>/dev/null; then
  :
else
  echo "      WARN: cv2 import failed"
  FAIL=1
fi

echo "[4/5] Focus script path…"
if [[ -f scripts/focus_imx519.py ]]; then
  echo "      OK scripts/focus_imx519.py"
else
  echo "      WARN: scripts/focus_imx519.py not found"
fi

echo "[5/5] Docker (optional)…"
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    echo "      OK docker reachable"
  else
    echo "      WARN: docker info failed (daemon off or no permission)"
    echo "            Try: ./scripts/prepare_docker_and_permissions.sh   or   ./install.sh --docker-host"
  fi
else
  echo "      SKIP: docker not installed"
fi

if [[ "$FAIL" -ne 0 ]]; then
  echo "--- Result: FAILED ---"
  exit 1
fi
echo "--- Result: OK ---"
exit 0
