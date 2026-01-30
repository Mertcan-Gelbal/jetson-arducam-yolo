#!/bin/bash
#
# Smart Docker Build Script
# usage: ./build_docker.sh
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "========================================"
echo "   Smart Docker Builder & Analyzer"
echo "========================================"

# 1. Check Docker Service
if ! systemctl is-active --quiet docker; then
    echo -e "${YELLOW}Warning: Docker service is not running.${NC}"
    echo "Attempting to start Docker..."
    sudo systemctl start docker
    if ! systemctl is-active --quiet docker; then
        echo -e "${RED}Error: Failed to start Docker.${NC}"
        echo "Try: sudo systemctl status docker"
        exit 1
    fi
    echo -e "${GREEN}✓ Docker started.${NC}"
fi

# 1.5 Quick GStreamer & Camera Health Check
echo ""
echo "Running Hardware Diagnostics..."
if ! ./scripts/check_gstreamer.sh; then
    echo -e "${YELLOW}Warning: Hardware diagnostics found issues.${NC}"
    read -p "Your camera might not work inside Docker. Continue with build? (y/N): " HW_CONT
    if [[ ! $HW_CONT =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 2. Check Memory Resources (Prevent OOM)
TOTAL_MEM=$(free -m | awk '/^Mem:/{print $2}')
TOTAL_SWAP=$(free -m | awk '/^Swap:/{print $2}')
TOTAL_AVAIL=$((TOTAL_MEM + TOTAL_SWAP))

echo "Memory Check:"
echo "  RAM: ${TOTAL_MEM}MB"
echo "  Swap: ${TOTAL_SWAP}MB"
echo "  Total: ${TOTAL_AVAIL}MB"

if [ $TOTAL_AVAIL -lt 8000 ]; then
    echo -e "${RED}CRITICAL WARNING: Total memory (RAM+Swap) is less than 8GB.${NC}"
    echo "Building YOLOv8 requires significant memory. Build will likely fail."
    echo ""
    echo "Solution: Create a Swap file."
    echo "  Run: sudo fallocate -l 8G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile"
    echo ""
    read -p "Continue anyway? (y/N): " CONT
    if [[ ! $CONT =~ ^[Yy]$ ]]; then
        echo "Build cancelled."
        exit 1
    fi
fi

# 3. Detect JetPack Version & Select Base Image
echo ""
echo "Detecting System Version..."
BASE_IMAGE="nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3" # Default fallback

if [ -f /etc/nv_tegra_release ]; then
    L4T_STRING=$(head -n 1 /etc/nv_tegra_release)
    L4T_RELEASE=$(echo $L4T_STRING | cut -f 2 -d ' ' | grep -oE '[0-9.]+')
    
    echo -e "${GREEN}✓ Detected L4T Release: $L4T_RELEASE${NC}"
    
    # Smart Mapping Logic
    # Maps L4T Major versions to best available Docker Base Images
    if [[ $L4T_RELEASE == 36* ]]; then
        # JetPack 6.0 (Orin Series - L4T r36.x)
        BASE_IMAGE="nvcr.io/nvidia/l4t-pytorch:r36.2.0-pth2.3-py3"
        echo -e "${GREEN}✓ Auto-selected JetPack 6 Base Image${NC}"
        
    elif [[ $L4T_RELEASE == 35* ]]; then
        # JetPack 5.1.x (Orin/Xavier - L4T r35.x)
        # We use r35.2.1 as it is the most stable pytorch container for JP 5.1+
        BASE_IMAGE="nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3"
        echo -e "${GREEN}✓ Auto-selected JetPack 5 Base Image${NC}"
        
    elif [[ $L4T_RELEASE == 32* ]]; then
        # JetPack 4.6.x (Nano/TX2 - L4T r32.x)
        # Warning: This repo is optimized for JP5+, but we try our best.
        BASE_IMAGE="nvcr.io/nvidia/l4t-pytorch:r32.7.1-pth1.10-py3"
        echo -e "${YELLOW}⚠ Detected legacy JetPack 4.x. Some features may not work.${NC}"
    else
        echo -e "${YELLOW}⚠ Unknown L4T version ($L4T_RELEASE). Using default JetPack 5 image.${NC}"
    fi
else
    echo -e "${YELLOW}⚠ L4T version not found (Not a Jetson?). using default image.${NC}"
fi

echo "Selected Base Image: $BASE_IMAGE"

# Update Dockerfile to match system (Transparency for User)
if [ -f Dockerfile ]; then
    echo "Updating Dockerfile default to match system..."
    # Use | delimiter for sed because image ID contains slashes /
    sed -i "s|ARG BASE_IMAGE=.*|ARG BASE_IMAGE=$BASE_IMAGE|g" Dockerfile
fi

# 4. Build
echo ""
echo -e "${GREEN}Building Image: jetson-arducam:latest (Base: $BASE_IMAGE)${NC}"
echo "This may take 15-20 minutes. Please wait..."

if sudo docker build --build-arg BASE_IMAGE=$BASE_IMAGE -t jetson-arducam:latest .; then
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}   Build Complete Successfully!   ${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo "Run using: ./scripts/run_docker.sh"
else
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}   Build Failed   ${NC}"
    echo -e "${RED}========================================${NC}"
    echo "Common Solutions:"
    echo "1. Out of memory? -> Add Swap (see above)"
    echo "2. Network issue? -> Check internet"
    echo "3. Disk full? -> Run 'df -h'"
    exit 1
fi
