#!/bin/bash
#
# Build Script
# Detects L4T version and builds appropriate Docker image
#

set -e

# Detect L4T Version
if [ -f /etc/nv_tegra_release ]; then
    L4T_STRING=$(head -n 1 /etc/nv_tegra_release)
    # Extract Release and Revision (e.g. R35 (release), REVISION: 4.1)
    L4T_RELEASE=$(echo $L4T_STRING | cut -f 2 -d ' ' | grep -oE '[0-9.]+')
    L4T_REVISION=$(echo $L4T_STRING | cut -f 2 -d ',' | grep -oE '[0-9.]+')
    
    L4T_VERSION="r$L4T_RELEASE.$L4T_REVISION"
    echo "Detected Jetson L4T Version: $L4T_VERSION"
else
    echo "Warning: Not running on Jetson or L4T version not found."
    echo "Defaulting to r35.4.1 (JetPack 5.1.2)"
    L4T_VERSION="r35.4.1"
fi

# Build Docker Image
echo "Building Docker image for $L4T_VERSION..."
echo "Command: sudo docker build --build-arg L4T_VERSION=$L4T_VERSION -t jetson-yolo:latest ."

sudo docker build \
    --build-arg L4T_VERSION=$L4T_VERSION \
    -t jetson-yolo:latest .

echo ""
echo "Build success! Run the container using:"
echo "./scripts/run_docker.sh"
