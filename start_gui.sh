#!/bin/bash
# GUI Launcher for Jetson Arducam AI Kit
# Starts the Qt5 Desktop Application

# Set environment
export DISPLAY=${DISPLAY:-:0}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}Starting Desktop GUI...${NC}"

# Resolve pip — works on all Ubuntu/Jetson variants
PIP="python3 -m pip"

# Always ensure GUI dependencies are installed
echo -e "${YELLOW}Checking GUI dependencies...${NC}"

# PyQt5: prefer system package (works on Jetson without pip wheel issues)
if ! python3 -c "import PyQt5" &> /dev/null; then
    echo "Installing PyQt5 via apt..."
    sudo apt-get install -y python3-pyqt5 python3-pyqt5.qtwidgets &> /dev/null
fi

# Python package dependencies (psutil, opencv, numpy, etc.)
if ! python3 -c "import psutil, cv2" &> /dev/null; then
    echo "Installing Python dependencies..."
    $PIP install --quiet -r "$SCRIPT_DIR/gui/requirements.txt"
fi

echo -e "${GREEN}Dependencies OK${NC}"

# Run
cd "$SCRIPT_DIR"
python3 gui/main.py

