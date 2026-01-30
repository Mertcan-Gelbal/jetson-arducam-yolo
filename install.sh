#!/bin/bash
#
# Jetson Arducam AI Kit - Master Installer
# Orchestrates the entire setup process: Drivers -> Verify -> Build -> Run
#

set -e

# Colors & Formatting
BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

clear
echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}   🚀 Jetson Arducam AI Kit - All-in-One Installer   ${NC}"
echo -e "${CYAN}======================================================${NC}"
echo ""
echo "This script will guide you through the entire setup process."
echo ""

# Ensure scripts are executable
chmod +x scripts/*.sh

# ==============================================================================
# STEP 1: Camera Drivers & Hardware Setup
# ==============================================================================
echo -e "${BOLD}Step 1: Hardware & Drivers${NC}"
echo "--------------------------"

if dmesg | grep -q "imx[0-9]*" || lsusb | grep -q "Logitech\|Intel"; then
    echo -e "${GREEN}✓ Camera devices detected (CSI or USB).${NC}"
    read -p "Do you want to run the Camera Setup Wizard anyway? (y/N): " RUN_SETUP
else
    echo -e "${YELLOW}⚠ No specific camera drivers detected active in kernel.${NC}"
    RUN_SETUP="y"
fi

if [[ $RUN_SETUP =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Starting Camera Setup Wizard...${NC}"
    ./scripts/setup_cameras.sh
    
    # If setup script requested reboot (exit code check or marker), we should stop advice
    echo ""
    echo -e "${YELLOW}NOTE: If you just installed CSI drivers, you MUST reboot before continuing.${NC}"
    read -p "Did you just install new drivers that require reboot? (y/N): " REBOOT_NEEDED
    if [[ $REBOOT_NEEDED =~ ^[Yy]$ ]]; then
        echo -e "${RED}Please reboot your Jetson and run this script again: ./install.sh${NC}"
        echo "Rebooting in 5 seconds..."
        sleep 5
        sudo reboot
        exit 0
    fi
fi

echo ""

# ==============================================================================
# STEP 2: System Validation
# ==============================================================================
echo -e "${BOLD}Step 2: System Verification${NC}"
echo "---------------------------"
echo "Running diagnostics..."
if ./scripts/test_installation.sh; then
    echo -e "${GREEN}✓ System looks good!${NC}"
else
    echo -e "${YELLOW}⚠ Some checks failed. Please review above.${NC}"
    read -p "Continue anyway? (y/N): " CONT
    if [[ ! $CONT =~ ^[Yy]$ ]]; then
        echo "Exiting."
        exit 1
    fi
fi

echo ""

# ==============================================================================
# STEP 3: Docker Environment
# ==============================================================================
echo -e "${BOLD}Step 3: AI Environment (Docker)${NC}"
echo "-------------------------------"

# Check if image exists
if sudo docker images | grep -q "jetson-arducam"; then
    echo -e "${GREEN}✓ Docker image 'jetson-arducam' already exists.${NC}"
    read -p "Do you want to rebuild it? (y/N): " REBUILD
else
    echo -e "${YELLOW}⚠ Docker image not found.${NC}"
    REBUILD="y"
fi

if [[ $REBUILD =~ ^[Yy]$ ]]; then
    echo -e "${CYAN}Starting Build Process... (This takes 15-20 mins)${NC}"
    ./scripts/build_docker.sh
fi

echo ""

# ==============================================================================
# STEP 4: Launch
# ==============================================================================
echo -e "${BOLD}Step 4: Launch Application${NC}"
echo "--------------------------"
echo -e "${GREEN}Setup Complete!${NC}"
echo ""
read -p "Do you want to start the container now? (Y/n): " START_NOW

if [[ $START_NOW =~ ^[Yy]$ ]] || [[ -z $START_NOW ]]; then
    ./scripts/run_docker.sh
    
    echo ""
    echo -e "${CYAN}======================================================${NC}"
    echo -e "${GREEN}   System is Running! 🚀${NC}"
    echo -e "${CYAN}======================================================${NC}"
    echo "To run object detection:"
    echo "1. Enter container:  sudo docker exec -it jetson-arducam-ctr bash"
    echo "2. Run example:      python3 examples/basic_detection.py --source-type csi"
    echo ""
else
    echo "You can start later using: ./scripts/run_docker.sh"
    echo "Goodbye!"
fi
