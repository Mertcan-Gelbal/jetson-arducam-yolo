#!/bin/bash
#
# Universal Camera Setup & Diagnostic Tool
# Supports: CSI (MIPI) Cameras & USB Webcams
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}==================================================${NC}"
echo -e "${CYAN}   Jetson Camera Setup & Diagnostics Tool       ${NC}"
echo -e "${CYAN}==================================================${NC}"

# Check Connectivity
echo "Checking internet connectivity..."
if ! ping -c 1 8.8.8.8 &> /dev/null; then
    echo -e "${YELLOW}Warning: No internet. Some features may fail.${NC}"
fi

# Install Diagnostics Tools
echo "Installing verification tools..."
sudo apt update -qq && sudo apt install -y i2c-tools v4l-utils usbutils > /dev/null

echo ""
echo "How is your camera connected?"
echo "-----------------------------"
echo "1) CSI / MIPI (Ribbon Cable)"
echo "   -> Uses internal ISP, Low Latency, Requires Drivers (Arducam)"
echo "   -> Best for: High FPS, Multi-camera synchronization"
echo ""
echo "2) USB (USB Cable)"
echo "   -> Plug & Play, Uses CPU/RAM more, No specific driver needed"
echo "   -> Best for: Webcams, Depth Cameras (Realsense/Zed)"
echo ""
read -p "Select connection type [1]: " CONN_TYPE

# ==========================================
# PROCESS FOR USB CAMERAS
# ==========================================
if [ "$CONN_TYPE" == "2" ]; then
    echo ""
    echo -e "${GREEN}>>> Configuring for USB Camera...${NC}"
    
    echo "Scanning USB Bus..."
    lsusb
    echo ""
    
    echo "Scanning Video Devices..."
    if ls -l /dev/video* &> /dev/null; then
        echo -e "${GREEN}✓ Video devices found:${NC}"
        v4l2-ctl --list-devices
    else
        echo -e "${RED}✗ No video devices found!${NC}"
        echo "Troubleshooting Tips:"
        echo "1. Try a different USB port."
        echo "2. If using a HUB, ensure it is powered (Jetson USB power is limited)."
        echo "3. Check 'dmesg | grep usb' for errors."
        exit 1
    fi
    
    echo ""
    echo -e "${YELLOW}Important Note for USB Cameras:${NC}"
    echo "- USB cameras consume USB bandwidth. Using multiple high-res USB cams may fail."
    echo "- In Python scripts, use --source-type usb flag."
    
    echo ""
    echo -e "${GREEN}Setup Complete! No drivers needed for standard UVC cameras.${NC}"
    exit 0
fi

# ==========================================
# PROCESS FOR CSI / ARDUCAM
# ==========================================

echo ""
echo -e "${GREEN}>>> Configuring for CSI (Ribbon) Camera...${NC}"

# 1. I2C Check
echo "Checking I2C Bus (Communication Line)..."
# Orin/Xavier usually use bus 7 or 9/10 depending on carrier board
# We scan common buses
FOUND_I2C=0
for bus in 0 1 7 8 9 10 30 31; do
    if i2cdetect -y -r $bus 2>/dev/null | grep -q "[0-9a-f][0-9a-f]"; then
       FOUND_I2C=1
    fi
done

if [ $FOUND_I2C -eq 1 ]; then
    echo -e "${GREEN}✓ I2C Bus is active. Devices detected.${NC}"
else
    echo -e "${YELLOW}⚠ Warning: No I2C devices active. Check ribbon cable orientation!${NC}"
fi

# 2. Arducam Installer
echo ""
echo "Downloading Arducam installer..."
cd ~
rm -f install_full.sh
wget -q -O install_full.sh https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3/install_full.sh
chmod +x install_full.sh

echo ""
echo -e "${YELLOW}Select your CSI camera model:${NC}"
echo "  1) IMX219 (Raspberry Pi V2)"
echo "  2) IMX477 (Raspberry Pi HQ)"
echo "  3) IMX519 (16MP Arducam - Auto Focus)"
echo "  4) IMX708 (Raspberry Pi V3)"
echo "  5) OV9281 (Global Shutter)"
echo "  6) OV7251 (Global Shutter)"
echo ""
read -p "Enter number [3]: " CHOICE

MODEL="imx519"
case $CHOICE in
    1) MODEL="imx219" ;;
    2) MODEL="imx477" ;;
    3) MODEL="imx519" ;;
    4) MODEL="imx708" ;;
    5) MODEL="ov9281" ;;
    6) MODEL="ov7251" ;;
esac

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
fi
