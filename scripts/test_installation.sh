#!/bin/bash
#
# Installation Test Script
# Verifies camera, Docker, and Python environment setup
#

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ERRORS=0
WARNINGS=0

echo "=================================================="
echo "   Installation Verification Script"
echo "=================================================="
echo ""

# Function to check command
check_command() {
    if command -v "$1" &> /dev/null; then
        echo -e "${GREEN}✓ $2${NC}"
        return 0
    else
        echo -e "${RED}✗ $2${NC}"
        ERRORS=$((ERRORS + 1))
        return 1
    fi
}

# Function to check file
check_file() {
    if [ -e "$1" ]; then
        echo -e "${GREEN}✓ $2${NC}"
        return 0
    else
        echo -e "${RED}✗ $2${NC}"
        ERRORS=$((ERRORS + 1))
        return 1
    fi
}

# System Information
echo "System Information:"
echo "-------------------"
if [ -f /etc/nv_tegra_release ]; then
    echo -e "${GREEN}✓ Running on Jetson${NC}"
    cat /etc/nv_tegra_release | head -n 1
else
    echo -e "${RED}✗ Not running on Jetson${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check OS Version
if [ -f /etc/os-release ]; then
    UBUNTU_VER=$(grep "PRETTY_NAME" /etc/os-release | cut -d'"' -f2)
    echo -e "  OS: ${GREEN}$UBUNTU_VER${NC}"
fi

# Check GStreamer Version
if command -v gst-inspect-1.0 &> /dev/null; then
    GST_VER=$(gst-inspect-1.0 --version | head -n 1 | awk '{print $NF}')
    echo -e "  GStreamer: ${GREEN}$GST_VER${NC}"
else
    echo -e "${YELLOW}⚠ GStreamer tools not found, cannot determine version${NC}"
    WARNINGS=$((WARNINGS + 1))
fi

# JetPack version
JP_VERSION=$(apt-cache show nvidia-jetpack 2>/dev/null | grep Version | head -n 1)
if [ -n "$JP_VERSION" ]; then
    echo -e "${GREEN}✓ JetPack: $JP_VERSION${NC}"
else
    echo -e "${YELLOW}⚠ Could not detect JetPack version${NC}"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""

# Camera Tests
echo "Camera Detection:"
echo "-----------------"

# Check for video devices
VIDEO_DEVICES=$(ls /dev/video* 2>/dev/null | wc -l)
if [ "$VIDEO_DEVICES" -gt 0 ]; then
    echo -e "${GREEN}✓ Found $VIDEO_DEVICES video device(s)${NC}"
    ls -l /dev/video* 2>/dev/null | grep video | awk '{print "  " $NF}'
else
    echo -e "${RED}✗ No video devices found${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check nvargus daemon
if systemctl is-active --quiet nvargus-daemon; then
    echo -e "${GREEN}✓ nvargus-daemon is running${NC}"
else
    echo -e "${RED}✗ nvargus-daemon is not running${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check for IMX519 in kernel messages
if dmesg | grep -q imx519; then
    echo -e "${GREEN}✓ IMX519 driver loaded${NC}"
else
    echo -e "${RED}✗ IMX519 driver not detected in kernel messages${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Check I2C devices
I2C_DEVICES=$(ls /dev/i2c-* 2>/dev/null | wc -l)
if [ "$I2C_DEVICES" -gt 0 ]; then
    echo -e "${GREEN}✓ Found $I2C_DEVICES I2C bus(es)${NC}"
else
    echo -e "${YELLOW}⚠ No I2C devices found${NC}"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""

# Docker Tests
echo "Docker Environment:"
echo "-------------------"

check_command "docker" "Docker installed"

if command -v docker &> /dev/null; then
    # Check Docker service
    if systemctl is-active --quiet docker; then
        echo -e "${GREEN}✓ Docker service is running${NC}"
    else
        echo -e "${RED}✗ Docker service is not running${NC}"
        ERRORS=$((ERRORS + 1))
    fi
    
    # Check NVIDIA runtime
    if docker info 2>/dev/null | grep -q nvidia; then
        echo -e "${GREEN}✓ NVIDIA Docker runtime available${NC}"
    else
        echo -e "${YELLOW}⚠ NVIDIA Docker runtime not detected${NC}"
        WARNINGS=$((WARNINGS + 1))
    fi
    
    # Check for our image
    if docker images | grep -q jetson-arducam; then
        echo -e "${GREEN}✓ Jetson Arducam Docker image found${NC}"
    else
        echo -e "${YELLOW}⚠ Docker image not built yet${NC}"
        echo "  Run: ./scripts/build_docker.sh"
        WARNINGS=$((WARNINGS + 1))
    fi
fi

echo ""

# System Resources
echo "System Resources:"
echo "-----------------"

# Check memory
TOTAL_MEM=$(free -g | awk '/^Mem:/{print $2}')
echo "  Total Memory: ${TOTAL_MEM}GB"

if [ "$TOTAL_MEM" -lt 8 ]; then
    echo -e "${YELLOW}⚠ System has less than 8GB RAM${NC}"
    WARNINGS=$((WARNINGS + 1))
else
    echo -e "${GREEN}✓ Sufficient memory${NC}"
fi

# Check swap
TOTAL_SWAP=$(free -g | awk '/^Swap:/{print $2}')
echo "  Total Swap: ${TOTAL_SWAP}GB"

if [ "$TOTAL_SWAP" -lt 4 ]; then
    echo -e "${YELLOW}⚠ Swap space less than 4GB (recommended: 8GB)${NC}"
    echo "  Create swap: sudo fallocate -l 8G /swapfile"
    WARNINGS=$((WARNINGS + 1))
else
    echo -e "${GREEN}✓ Sufficient swap space${NC}"
fi

# Check disk space
DISK_AVAIL=$(df -BG / | awk 'NR==2{print $4}' | sed 's/G//')
echo "  Available Disk: ${DISK_AVAIL}GB"

if [ "$DISK_AVAIL" -lt 20 ]; then
    echo -e "${YELLOW}⚠ Less than 20GB disk space available${NC}"
    WARNINGS=$((WARNINGS + 1))
else
    echo -e "${GREEN}✓ Sufficient disk space${NC}"
fi

echo ""

# GStreamer Tests
echo "GStreamer:"
echo "----------"

check_command "gst-launch-1.0" "GStreamer installed"
check_command "gst-inspect-1.0" "GStreamer tools available"

# Check for NVIDIA GStreamer plugins
if gst-inspect-1.0 nvarguscamerasrc &> /dev/null; then
    echo -e "${GREEN}✓ nvarguscamerasrc plugin available${NC}"
else
    echo -e "${RED}✗ nvarguscamerasrc plugin not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

if gst-inspect-1.0 nvvidconv &> /dev/null; then
    echo -e "${GREEN}✓ nvvidconv plugin available${NC}"
else
    echo -e "${RED}✗ nvvidconv plugin not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

echo ""

# Python Environment (if not using Docker)
echo "Python Environment (Host):"
echo "--------------------------"

check_command "python3" "Python 3 installed"

if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    echo "  Version: $PYTHON_VERSION"
    
    # Check for pip
    check_command "pip3" "pip3 installed"
fi

echo ""

# Camera Test (Optional)
echo "Quick Camera Test:"
echo "------------------"
echo "Testing camera 0 with GStreamer (5 second test)..."

timeout 5 gst-launch-1.0 nvarguscamerasrc sensor-id=0 ! \
    'video/x-raw(memory:NVMM),width=640,height=480,framerate=30/1' ! \
    fakesink -e 2>/dev/null

if [ $? -eq 0 ] || [ $? -eq 124 ]; then
    echo -e "${GREEN}✓ Camera test successful${NC}"
else
    echo -e "${RED}✗ Camera test failed${NC}"
    echo "  Check logs: sudo journalctl -u nvargus-daemon -n 50"
    ERRORS=$((ERRORS + 1))
fi

echo ""

# Summary
echo "=================================================="
echo "   Summary"
echo "=================================================="
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Build Docker image: ./scripts/build_docker.sh"
    echo "  2. Run container: ./scripts/run_docker.sh"
    echo "  3. Test examples: sudo docker exec -it jetson-arducam-ctr python3 examples/basic_detection.py --display"
elif [ $ERRORS -eq 0 ]; then
    echo -e "${YELLOW}Checks passed with $WARNINGS warning(s)${NC}"
    echo "Review warnings above and address if needed."
else
    echo -e "${RED}Found $ERRORS error(s) and $WARNINGS warning(s)${NC}"
    echo "Please address the errors before proceeding."
    echo ""
    echo "For help, see: docs/TROUBLESHOOTING.md"
fi

echo ""
echo "=================================================="

exit $ERRORS
