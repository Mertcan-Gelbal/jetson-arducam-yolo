#!/bin/bash
# GUI Launcher for Jetson Arducam AI Kit
# Starts the Qt5 Desktop Application

# Set environment
export DISPLAY=${DISPLAY:-:0}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}Starting Desktop GUI...${NC}"

# Check dependencies
if ! python3 -c "import PyQt5" &> /dev/null; then
    echo "Installing PyQt5..."
    sudo apt-get install -y python3-pyqt5
    pip3 install -r "$SCRIPT_DIR/gui/requirements.txt"
fi

# Run
cd "$SCRIPT_DIR"
python3 gui/main.py
