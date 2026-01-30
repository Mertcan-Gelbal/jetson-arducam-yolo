#!/bin/bash
#
# Jetson GStreamer Compatibility Diagnostic Tool
# Detects version-specific pipeline issues and suggests fixes.
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}==================================================${NC}"
echo -e "${CYAN}   GStreamer Compatibility Diagnostic Tool       ${NC}"
echo -e "${CYAN}==================================================${NC}"

# 1. Detect JetPack/L4T Version
if [ -f /etc/nv_tegra_release ]; then
    L4T_RELEASE=$(head -n 1 /etc/nv_tegra_release | cut -f 2 -d ' ' | grep -oE '[0-9.]+' | cut -d. -f1)
    echo -e "Detected L4T Major Version: ${GREEN}$L4T_RELEASE${NC}"
else
    echo -e "${RED}Error: Not a Jetson system.${NC}"
    exit 1
fi

# 2. Test nvarguscamerasrc plugin
echo -n "Checking nvarguscamerasrc... "
if gst-inspect-1.0 nvarguscamerasrc &> /dev/null; then
    echo -e "${GREEN}FOUND${NC}"
else
    echo -e "${RED}MISSING${NC}"
    echo "Tip: Reinstall nvidia-l4t-gstreamer or check JetPack installation."
fi

# 3. Connectivity & Sensor Test
echo -e "\nTesting Camera Sensor ID 0..."
if timeout 3 gst-launch-1.0 nvarguscamerasrc sensor-id=0 ! fakesink &> /dev/null; then
    echo -e "${GREEN}✓ Sensor 0 is responsive.${NC}"
else
    echo -e "${RED}✗ Sensor 0 failed.${NC}"
    echo "Possible Issues:"
    echo "1. Permission: Run 'sudo usermod -aG video \$USER'"
    echo "2. Daemon: Run 'sudo systemctl restart nvargus-daemon'"
    echo "3. Driver: Check dmesg for sensor errors."
fi

# 4. Version Specific Recommendations
echo -e "\n${CYAN}Version-Specific Recommendations:${NC}"
echo "-------------------------------------"

if [ "$L4T_RELEASE" -eq 36 ]; then
    echo -e "${YELLOW}[JetPack 6.x Detected]${NC}"
    echo "- Ensure you use 'format=(string)NV12' in the pipeline."
    echo "- Some plugins like 'omxh264enc' are deprecated. Use 'v4l2h264enc' instead."
    echo "- Recommended Pipeline Segment:"
    echo "  'nvarguscamerasrc ! video/x-raw(memory:NVMM),format=NV12 ! nvvidconv ! video/x-raw,format=BGRx'"

elif [ "$L4T_RELEASE" -eq 35 ]; then
    echo -e "${YELLOW}[JetPack 5.x Detected]${NC}"
    echo "- Most stable for 'nvarguscamerasrc'."
    echo "- If you see 'Buffer probe' errors, increase 'max-buffers' in appsink."
    
elif [ "$L4T_RELEASE" -eq 32 ]; then
    echo -e "${YELLOW}[JetPack 4.x Detected]${NC}"
    echo "- Legacy environment. Ensure 'omx' plugins are installed."
    echo "- Use 'nveglglessink' for display instead of 'autovideosink'."
fi

echo -e "\n${CYAN}Diagnostic complete.${NC}"
