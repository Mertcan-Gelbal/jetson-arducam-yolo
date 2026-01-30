#!/bin/bash
#
# Arducam Driver Installer
# Downloads official Arducam installer and sets up the selected camera model
#

set -e

# Supported Camera Models
CAMERAS=("imx219" "imx477" "imx519" "imx708" "ov9281" "ov7251" "arducam")

echo "=================================================="
echo "   Arducam Driver Installer for NVIDIA Jetson"
echo "=================================================="

# Check if running on Jetson
if [ ! -f /etc/nv_tegra_release ]; then
    echo "Error: This script must be run on a Jetson device."
    exit 1
fi

# Step 1: Install Dependencies
echo "Updating system dependencies..."
sudo apt update
sudo apt install -y wget curl i2c-tools v4l-utils

# Step 2: Download Official Installer
echo "Downloading Arducam installer..."
cd ~
if [ -f install_full.sh ]; then
    rm install_full.sh
fi
wget -q https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3/install_full.sh
chmod +x install_full.sh

# Step 3: Select Camera Model
MODEL=""

# Check if model passed as argument (e.g. ./setup_cameras.sh imx519)
if [ ! -z "$1" ]; then
    MODEL=$1
else
    # Interactive Menu
    echo ""
    echo "Select your camera model:"
    echo "  1) IMX219 (Raspberry Pi V2)"
    echo "  2) IMX477 (Raspberry Pi HQ)"
    echo "  3) IMX519 (16MP Arducam)"
    echo "  4) IMX708 (Raspberry Pi V3)"
    echo "  5) OV9281 (Global Shutter)"
    echo "  6) OV7251 (Global Shutter)"
    echo ""
    read -p "Enter number [3 for IMX519]: " CHOICE
    
    case $CHOICE in
        1) MODEL="imx219" ;;
        2) MODEL="imx477" ;;
        3) MODEL="imx519" ;;
        4) MODEL="imx708" ;;
        5) MODEL="ov9281" ;;
        6) MODEL="ov7251" ;;
        *) MODEL="imx519" ;; # Default
    esac
fi

# Step 4: Install Driver
echo ""
echo "Installing driver for: $MODEL"
echo "Running: sudo ./install_full.sh -m $MODEL"
echo ""

sudo ./install_full.sh -m $MODEL

# Step 5: Post-Install Actions
echo ""
echo "=================================================="
echo "   Installation Complete"
echo "=================================================="
echo "A system reboot is required to activate the camera driver."
read -p "Reboot now? (y/N): " REBOOT
if [[ $REBOOT =~ ^[Yy]$ ]]; then
    sudo reboot
else
    echo "Please reboot manually using 'sudo reboot' before testing."
fi
