#!/bin/bash
# VisionDock GUI launcher (PySide6 / Qt 6) — Jetson, Linux, macOS

export DISPLAY=${DISPLAY:-:0}
if [ "$(uname)" = "Linux" ]; then
    export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-xcb}
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}Starting VisionDock (PySide6)...${NC}"

PIP="python3 -m pip"

echo -e "${YELLOW}Checking GUI dependencies...${NC}"

# Full GUI stack: Qt, OpenCV, paramiko (Devices → SSH), numpy bound in requirements
if ! python3 -c "import PySide6, psutil, cv2, numpy, paramiko" &> /dev/null; then
    echo "Installing GUI requirements (PySide6, OpenCV, paramiko, …) via pip..."
    $PIP install --quiet -r "$SCRIPT_DIR/gui/requirements.txt"
fi

echo -e "${GREEN}Dependencies OK${NC}"

cd "$SCRIPT_DIR"
echo -e "${CYAN}Working directory: $SCRIPT_DIR${NC}"

# Optional: load VISIONDOCK_* / JETSON_REMOTE etc. from project root .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$SCRIPT_DIR/.env"
    set +a
fi

python3 gui/main.py
