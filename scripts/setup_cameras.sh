#!/bin/bash
#
# Arducam Camera Setup Script for NVIDIA Jetson
# Automates camera driver installation and verification
#

set -e  # Exit on error

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=================================================="
echo "   Arducam IMX519 Camera Setup for Jetson"
echo "=================================================="
echo ""

# Check if running on Jetson
if [ ! -f /etc/nv_tegra_release ]; then
    echo -e "${RED}Error: This script must be run on a Jetson device${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Running on Jetson device${NC}"

# Display Jetson info
echo ""
echo "System Information:"
cat /etc/nv_tegra_release
echo ""

# Step 1: Update system packages
echo "Step 1: Updating system packages..."
sudo apt update
sudo apt install -y wget curl i2c-tools v4l-utils

echo -e "${GREEN}✓ System packages updated${NC}"
echo ""

# Step 2: Download Arducam installer
echo "Step 2: Downloading Arducam installer..."
cd ~

if [ ! -f install_full.sh ]; then
    wget https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3/install_full.sh
    chmod +x install_full.sh
    echo -e "${GREEN}✓ Installer downloaded${NC}"
else
    echo -e "${YELLOW}Installer already exists${NC}"
fi
echo ""

# Step 3: List available cameras
echo "Step 3: Available camera modules:"
./install_full.sh -l
echo ""

# Step 4: Check if IMX519 is already installed
echo "Step 4: Checking for existing IMX519 installation..."
if dmesg | grep -q imx519; then
    echo -e "${YELLOW}IMX519 driver appears to be already installed${NC}"
    read -p "Reinstall? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Skipping installation..."
    else
        echo "Installing IMX519 driver..."
        sudo ./install_full.sh -m imx519
    fi
else
    echo "Installing IMX519 driver..."
    sudo ./install_full.sh -m imx519
fi

echo ""
echo -e "${GREEN}✓ Camera driver installation complete${NC}"
echo ""

# Ask about reboot
echo -e "${YELLOW}System reboot is required for camera driver to load${NC}"
read -p "Reboot now? (y/N): " -n 1 -r
echo

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Rebooting in 3 seconds..."
    sleep 3
    sudo reboot
else
    echo ""
    echo -e "${YELLOW}Please reboot manually with: sudo reboot${NC}"
    echo ""
    echo "After reboot, run: ./scripts/test_installation.sh"
fi

echo ""
echo "=================================================="
echo "   Camera Setup Complete"
echo "=================================================="
