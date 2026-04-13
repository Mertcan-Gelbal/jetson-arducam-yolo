#!/bin/bash
# VisionDock GUI launcher (PySide6 / Qt 6) — Jetson, Linux, macOS

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}Starting VisionDock Dashboard...${NC}"

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is not installed or not in PATH.${NC}"
    exit 1
fi

# Check for venv module 
if ! python3 -m venv --help &> /dev/null; then
    echo -e "${RED}Error: Required system components are missing. Please seek administrator assistance.${NC}"
    exit 1
fi

VENV_DIR="$SCRIPT_DIR/.venv_gui"

# Virtual Environment Setup
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Preparing local environment...${NC}"
    python3 -m venv "$VENV_DIR"
fi

# Activate venv
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo -e "${YELLOW}Verifying application requirements...${NC}"

# Full GUI stack check
if ! python3 -c "import PySide6, psutil, cv2, numpy, paramiko, yaml" &> /dev/null; then
    echo -e "${YELLOW}Installing required components... Please wait.${NC}"
    python3 -m pip install --quiet --upgrade pip
    python3 -m pip install --quiet -r "$SCRIPT_DIR/gui/requirements.txt"
fi

echo -e "${GREEN}Environment ready.${NC}"

# OS specific environment variables
if [ "$(uname)" = "Linux" ]; then
    export DISPLAY=${DISPLAY:-:0}
    export QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-xcb}
fi

# Optional: load VISIONDOCK_* / JETSON_REMOTE etc. from project root .env
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$SCRIPT_DIR/.env"
    set +a
fi

echo -e "${CYAN}Launching Operator Dashboard...${NC}"
export PYTHONPATH="$SCRIPT_DIR"
python3 gui/main.py
