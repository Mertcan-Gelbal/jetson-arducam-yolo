#!/bin/bash
#
# Arducam Driver Installer (Smart Error Handling)
# Downloads official Arducam installer and sets up the selected camera model
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Error Handler
handle_error() {
    echo -e "${RED}Error occurred in line $1${NC}"
    if [ "$2" == "wget" ]; then
        echo "--> Could not download installer. Check your internet connection."
        echo "    Try: ping -c 1 google.com"
    elif [ "$2" == "apt" ]; then
        echo "--> Package installation failed."
        echo "    Try: sudo apt update --fix-missing"
    fi
    exit 1
}

trap 'handle_error $LINENO $BASH_COMMAND' ERR

# Check Connectivity
echo "Checking internet connectivity..."
if ! ping -c 1 google.com &> /dev/null; then
    echo -e "${RED}Error: No internet connection detected.${NC}"
    echo "Please connect to WiFi or Ethernet and try again."
    exit 1
fi

# Essential Checks
if [ ! -f /etc/nv_tegra_release ]; then
    echo -e "${RED}Error: Not running on NVIDIA Jetson.${NC}"
    exit 1
fi

echo -e "${GREEN}System check passed. Starting installation...${NC}"

# Install Dependencies
echo "Updating system dependencies..."
sudo apt update
sudo apt install -y wget curl i2c-tools v4l-utils

# Download Official Installer
echo "Downloading Arducam installer..."
cd ~
rm -f install_full.sh
wget -O install_full.sh https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3/install_full.sh
chmod +x install_full.sh

# Camera Selection
MODEL=""
if [ ! -z "$1" ]; then
    MODEL=$1
else
    echo ""
    echo -e "${YELLOW}Select your camera model:${NC}"
    echo "  1) IMX219 (Raspberry Pi V2)"
    echo "  2) IMX477 (Raspberry Pi HQ)"
    echo "  3) IMX519 (16MP Arducam)"
    echo "  4) IMX708 (Raspberry Pi V3)"
    echo "  5) OV9281 (Global Shutter)"
    echo "  6) OV7251 (Global Shutter)"
    echo ""
    read -p "Enter number [3]: " CHOICE
    case $CHOICE in
        1) MODEL="imx219" ;;
        2) MODEL="imx477" ;;
        3) MODEL="imx519" ;;
        4) MODEL="imx708" ;;
        5) MODEL="ov9281" ;;
        6) MODEL="ov7251" ;;
        *) MODEL="imx519" ;;
    esac
fi

# Install
echo ""
echo -e "${GREEN}Installing driver for: $MODEL${NC}"
sudo ./install_full.sh -m $MODEL

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✓ Installation Successful!${NC}"
    echo -e "${YELLOW}A system reboot is required.${NC}"
    read -p "Reboot now? (y/N): " REBOOT
    if [[ $REBOOT =~ ^[Yy]$ ]]; then
        sudo reboot
    fi
else
    echo -e "${RED}Driver installation failed.${NC}"
    echo "Please check the error messages above."
fi
