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

# 3. Detect JetPack Version
echo ""
echo "Detecting L4T Version..."
if [ -f /etc/nv_tegra_release ]; then
    L4T_STRING=$(head -n 1 /etc/nv_tegra_release)
    L4T_RELEASE=$(echo $L4T_STRING | cut -f 2 -d ' ' | grep -oE '[0-9.]+')
    L4T_REVISION=$(echo $L4T_STRING | cut -f 2 -d ',' | grep -oE '[0-9.]+')
    
    L4T_VERSION="r$L4T_RELEASE.$L4T_REVISION"
    echo -e "${GREEN}✓ Detected: $L4T_VERSION${NC}"
else
    echo -e "${YELLOW}Warning: L4T version not found. Defaulting to r35.4.1${NC}"
    L4T_VERSION="r35.4.1"
fi

# 4. Build
echo ""
echo -e "${GREEN}Building Image: jetson-yolo:latest (Base: $L4T_VERSION)${NC}"
echo "This may take 15-20 minutes. Please wait..."

if sudo docker build --build-arg L4T_VERSION=$L4T_VERSION -t jetson-yolo:latest .; then
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
