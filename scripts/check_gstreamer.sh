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

# 2. Test GStreamer Version & Plugin
echo -n "Checking GStreamer version... "
if gst-inspect-1.0 --version &> /dev/null; then
    GST_VER=$(gst-inspect-1.0 --version | head -n 1 | awk '{print $NF}')
    echo -e "${GREEN}$GST_VER${NC}"
    
    # Version logic
    if [[ $(echo -e "$GST_VER\n1.20" | sort -V | head -n1) == "1.20" ]]; then
        echo -e "${GREEN}✓ Modern GStreamer detected (1.20+). Good for new sensors.${NC}"
    else
        echo -e "${YELLOW}! Legacy GStreamer detected ($GST_VER). Newer camera sensors might be limited.${NC}"
    fi
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

# 5. OpenCV + GStreamer Support Check
echo -e "\n${CYAN}Checking OpenCV + GStreamer Integration...${NC}"
if python3 -c "import cv2; print(cv2.getBuildInformation())" | grep -q "GStreamer:.*YES"; then
    echo -e "${GREEN}✓ OpenCV is compiled with GStreamer support.${NC}"
else
    echo -e "${RED}✗ OpenCV GStreamer support MISSING.${NC}"
    echo "Tip: Avoid installing 'python3-opencv' via apt inside Docker."
fi

# 6. Capture Test (The Ultimate Proof)
echo -e "\n${CYAN}Testing Frame Capture...${NC}"
rm -f test_frame.jpg
if timeout 5 gst-launch-1.0 nvarguscamerasrc sensor-id=0 num-buffers=1 ! \
    'video/x-raw(memory:NVMM),width=640,height=480' ! \
    nvvidconv ! jpegenc ! filesink location=test_frame.jpg &> /dev/null; then
    
    if [ -f test_frame.jpg ]; then
        echo -e "${GREEN}✓ Success! Single frame captured to test_frame.jpg${NC}"
        rm test_frame.jpg
    else
        echo -e "${RED}✗ Pipeline finished but no file produced.${NC}"
    fi
else
    echo -e "${RED}✗ Critical: GStreamer cannot capture from sensor 0.${NC}"
    echo "Check 'dmesg' for more details."
fi

echo -e "\n${CYAN}Diagnostic complete.${NC}"
