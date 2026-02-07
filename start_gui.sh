#!/bin/bash
#
# GUI Launcher Script
# Starts the Jetson Arducam AI Kit Web Interface
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUI_DIR="$SCRIPT_DIR/gui"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=================================================${NC}"
echo -e "${CYAN}   Jetson Arducam AI Kit - Web GUI Launcher      ${NC}"
echo -e "${CYAN}=================================================${NC}"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}Error: Python3 is not installed.${NC}"
    exit 1
fi

# Check/Install dependencies
echo -e "${CYAN}Checking dependencies...${NC}"
if ! python3 -c "import flask" 2>/dev/null; then
    echo "Installing required packages..."
    pip3 install -q -r "$GUI_DIR/requirements.txt"
fi

# Get IP address for remote access
IP_ADDR=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}Starting Web Interface...${NC}"
echo ""
echo -e "  Local:   ${CYAN}http://localhost:5000${NC}"
echo -e "  Network: ${CYAN}http://${IP_ADDR}:5000${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the server${NC}"
echo ""

# Start the Flask app
cd "$GUI_DIR"
python3 app.py
