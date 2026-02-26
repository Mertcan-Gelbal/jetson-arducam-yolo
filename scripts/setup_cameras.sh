#!/bin/bash
#
# Universal Camera Setup & Diagnostic Tool v2.0
# Supports: CSI (MIPI) Cameras & USB Webcams
# Features: Automatic L4T version fallback for Arducam drivers
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_VERSION="2.0.0"

echo -e "${CYAN}==================================================${NC}"
echo -e "${CYAN}   Jetson Camera Setup & Diagnostics Tool v${SCRIPT_VERSION}   ${NC}"
echo -e "${CYAN}==================================================${NC}"

# ==========================================
# HELPER FUNCTIONS
# ==========================================

log_info() { echo -e "${CYAN}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Get Jetson information
get_jetson_info() {
    JETSON_TYPE=$(cat /sys/firmware/devicetree/base/model 2>/dev/null || echo "Unknown")
    L4T_VERSION=$(dpkg-query --showformat='${Version}' --show nvidia-l4t-kernel 2>/dev/null || echo "Unknown")
    
    # Extract major.minor from L4T version (e.g., 5.10.216-tegra-35.6.4-... -> 35.6)
    L4T_MAJOR_MINOR=$(echo "$L4T_VERSION" | grep -oP '\d+\.\d+(?=\.\d+-)' | tail -1)
    
    log_info "Jetson Type: ${BOLD}$JETSON_TYPE${NC}"
    log_info "L4T Version: ${BOLD}$L4T_VERSION${NC}"
    log_info "L4T Major.Minor: ${BOLD}$L4T_MAJOR_MINOR${NC}"
}

# Find fallback L4T version
find_fallback_version() {
    local target_version="$1"
    local links_file="$2"
    local platform="$3"
    
    # Extract target major.minor (e.g., 35.6.4 -> 35.6)
    local target_major=$(echo "$target_version" | grep -oP 'tegra-\K\d+\.\d+' | head -1)
    
    if [ -z "$target_major" ]; then
        echo ""
        return
    fi
    
    log_info "Searching for fallback version for L4T ${target_major}.x..."
    
    # Get all available versions from links file for the platform
    local available_versions=""
    case "$platform" in
        "Orin NX")
            available_versions=$(grep -oP 'orin_nx_names\["\K[^"]+' "$links_file" 2>/dev/null | sort -V)
            ;;
        "Orin Nano")
            available_versions=$(grep -oP 'orin_nano_names\["\K[^"]+' "$links_file" 2>/dev/null | sort -V)
            ;;
        "Xavier NX")
            available_versions=$(grep -oP 'nx_names\["\K[^"]+' "$links_file" 2>/dev/null | sort -V)
            ;;
        "Nano")
            available_versions=$(grep -oP 'nano_names\["\K[^"]+' "$links_file" 2>/dev/null | sort -V)
            ;;
        "AGX Orin")
            available_versions=$(grep -oP 'agx_orin_names\["\K[^"]+' "$links_file" 2>/dev/null | sort -V)
            ;;
    esac
    
    if [ -z "$available_versions" ]; then
        echo ""
        return
    fi
    
    # Find matching versions with the same major.minor
    local matching_versions=""
    for v in $available_versions; do
        if echo "$v" | grep -q "tegra-${target_major}"; then
            matching_versions="$matching_versions $v"
        fi
    done
    
    if [ -z "$matching_versions" ]; then
        # Try to find the closest lower major version
        log_warn "No ${target_major}.x version found, searching for closest available..."
        # Get the latest available version as fallback
        echo "$available_versions" | tr ' ' '\n' | tail -1
        return
    fi
    
    # Return the latest matching version
    echo "$matching_versions" | tr ' ' '\n' | sort -V | tail -1
}

# Check Connectivity
check_connectivity() {
    log_info "Checking internet connectivity..."
    if ! ping -c 1 8.8.8.8 &> /dev/null; then
        log_warn "No internet connection. Some features may fail."
        return 1
    fi
    log_success "Internet connection OK"
    return 0
}

# Install diagnostic tools
install_tools() {
    log_info "Installing verification tools..."
    sudo apt update -qq && sudo apt install -y i2c-tools v4l-utils usbutils > /dev/null 2>&1
    log_success "Tools installed"
}

# ==========================================
# USB CAMERA SETUP
# ==========================================
setup_usb_camera() {
    echo ""
    log_info ">>> Configuring for USB Camera..."
    
    echo "Scanning USB Bus..."
    lsusb
    echo ""
    
    echo "Scanning Video Devices..."
    if ls -l /dev/video* &> /dev/null; then
        log_success "Video devices found:"
        v4l2-ctl --list-devices
    else
        log_error "No video devices found!"
        echo "Troubleshooting Tips:"
        echo "1. Try a different USB port."
        echo "2. If using a HUB, ensure it is powered (Jetson USB power is limited)."
        echo "3. Check 'dmesg | grep usb' for errors."
        exit 1
    fi
    
    echo ""
    log_warn "Important Note for USB Cameras:"
    echo "- USB cameras consume USB bandwidth. Using multiple high-res USB cams may fail."
    echo "- In Python scripts, use --source-type usb flag."
    
    echo ""
    log_success "Setup Complete! No drivers needed for standard UVC cameras."
}

# ==========================================
# CSI CAMERA SETUP (ARDUCAM)
# ==========================================
setup_csi_camera() {
    echo ""
    log_info ">>> Configuring for CSI (Ribbon) Camera..."
    
    # Get Jetson info
    get_jetson_info
    
    # I2C Check
    echo ""
    log_info "Checking I2C Bus (Communication Line)..."
    FOUND_I2C=0
    for bus in 0 1 7 8 9 10 30 31; do
        if i2cdetect -y -r $bus 2>/dev/null | grep -q "[0-9a-f][0-9a-f]"; then
           FOUND_I2C=1
        fi
    done
    
    if [ $FOUND_I2C -eq 1 ]; then
        log_success "I2C Bus is active. Devices detected."
    else
        log_warn "No I2C devices active. Check ribbon cable orientation!"
    fi
    
    # Download Arducam installer
    echo ""
    log_info "Downloading Arducam installer..."
    cd ~
    rm -f install_full.sh modules.txt *.txt 2>/dev/null || true
    
    ARDUCAM_RELEASE_URL="https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3"
    wget -q -O install_full.sh "${ARDUCAM_RELEASE_URL}/install_full.sh"
    wget -q -O modules.txt "${ARDUCAM_RELEASE_URL}/modules.txt"
    chmod +x install_full.sh
    
    log_success "Arducam installer downloaded"
    
    # Camera model selection
    echo ""
    echo -e "${YELLOW}Select your CSI camera model:${NC}"
    echo "  1) IMX219 (Raspberry Pi V2)"
    echo "  2) IMX477 (Raspberry Pi HQ - 12MP)"
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
    log_info "Selected model: ${BOLD}$MODEL${NC}"
    
    # Download the model-specific links file
    LINKS_FILE="${MODEL}_links.txt"
    wget -q -O "$LINKS_FILE" "${ARDUCAM_RELEASE_URL}/${LINKS_FILE}" 2>/dev/null || {
        log_error "Failed to download ${LINKS_FILE}"
        exit 1
    }
    
    # Determine platform
    PLATFORM=""
    if echo "$JETSON_TYPE" | grep -q "Orin NX"; then
        PLATFORM="Orin NX"
    elif echo "$JETSON_TYPE" | grep -q "Orin Nano"; then
        PLATFORM="Orin Nano"
    elif echo "$JETSON_TYPE" | grep -q "AGX Orin"; then
        PLATFORM="AGX Orin"
    elif echo "$JETSON_TYPE" | grep -q "Xavier NX"; then
        PLATFORM="Xavier NX"
    elif echo "$JETSON_TYPE" | grep -q "Nano"; then
        PLATFORM="Nano"
    fi
    
    log_info "Detected Platform: ${BOLD}$PLATFORM${NC}"
    
    # Try direct installation first
    echo ""
    log_info "Attempting driver installation for L4T: $L4T_VERSION"
    
    # Run install_full.sh and capture output
    INSTALL_OUTPUT=$(sudo ./install_full.sh -m $MODEL 2>&1) || true
    
    # Check if installation failed due to version mismatch
    if echo "$INSTALL_OUTPUT" | grep -q "Cannot find the corresponding deb package"; then
        echo ""
        log_warn "Exact L4T version not found in Arducam repository."
        log_info "Attempting fallback to closest compatible version..."
        
        # Find fallback version
        FALLBACK_VERSION=$(find_fallback_version "$L4T_VERSION" "$LINKS_FILE" "$PLATFORM")
        
        if [ -n "$FALLBACK_VERSION" ]; then
            echo ""
            log_info "Found fallback version: ${BOLD}$FALLBACK_VERSION${NC}"
            echo ""
            echo -e "${YELLOW}Your L4T version ($L4T_VERSION) is not directly supported.${NC}"
            echo -e "${YELLOW}However, a compatible driver for '$FALLBACK_VERSION' may work.${NC}"
            echo ""
            read -p "Try installing with fallback version? (y/N): " TRY_FALLBACK
            
            if [[ $TRY_FALLBACK =~ ^[Yy]$ ]]; then
                # Extract the download link for the fallback version
                source "$LINKS_FILE"
                
                # Get the correct download link based on platform
                FALLBACK_LINK=""
                FALLBACK_PKG=""
                case "$PLATFORM" in
                    "Orin NX")
                        FALLBACK_LINK="${orin_nx_download_links[$FALLBACK_VERSION]}"
                        FALLBACK_PKG="${orin_nx_names[$FALLBACK_VERSION]}"
                        ;;
                    "Orin Nano")
                        FALLBACK_LINK="${orin_nano_download_links[$FALLBACK_VERSION]}"
                        FALLBACK_PKG="${orin_nano_names[$FALLBACK_VERSION]}"
                        ;;
                    "AGX Orin")
                        FALLBACK_LINK="${agx_orin_download_links[$FALLBACK_VERSION]}"
                        FALLBACK_PKG="${agx_orin_names[$FALLBACK_VERSION]}"
                        ;;
                    "Xavier NX")
                        FALLBACK_LINK="${nx_download_links[$FALLBACK_VERSION]}"
                        FALLBACK_PKG="${nx_names[$FALLBACK_VERSION]}"
                        ;;
                    "Nano")
                        FALLBACK_LINK="${nano_download_links[$FALLBACK_VERSION]}"
                        FALLBACK_PKG="${nano_names[$FALLBACK_VERSION]}"
                        ;;
                esac
                
                if [ -n "$FALLBACK_LINK" ] && [ -n "$FALLBACK_PKG" ]; then
                    log_info "Downloading: $FALLBACK_PKG"
                    wget -q "$FALLBACK_LINK" -O "$FALLBACK_PKG"
                    
                    if [ -f "$FALLBACK_PKG" ]; then
                        log_info "Installing driver package..."
                        sudo dpkg -i "$FALLBACK_PKG"
                        
                        if [ $? -eq 0 ]; then
                            log_success "Driver installed successfully with fallback version!"
                            echo ""
                            log_warn "Note: This driver is from L4T $FALLBACK_VERSION"
                            log_warn "Minor compatibility issues may occur. Test thoroughly."
                            
                            echo ""
                            echo -e "${YELLOW}A system reboot is required.${NC}"
                            read -p "Reboot now? (y/N): " REBOOT
                            if [[ $REBOOT =~ ^[Yy]$ ]]; then
                                sudo reboot
                            fi
                            exit 0
                        else
                            log_error "Driver installation failed!"
                        fi
                    else
                        log_error "Failed to download fallback package"
                    fi
                else
                    log_error "Could not find download link for fallback version"
                fi
            fi
        fi
        
        # If we get here, fallback failed - show available versions and detailed guide
        echo ""
        log_error "Automatic installation failed."
        echo ""
        echo -e "${CYAN}Available L4T versions for $MODEL on $PLATFORM:${NC}"
        echo "------------------------------------------------"
        
        # List available versions
        case "$PLATFORM" in
            "Orin NX")
                grep -oP 'orin_nx_names\["\K[^"]+' "$LINKS_FILE" | sort -V
                ;;
            "Orin Nano")
                grep -oP 'orin_nano_names\["\K[^"]+' "$LINKS_FILE" | sort -V
                ;;
            "AGX Orin")
                grep -oP 'agx_orin_names\["\K[^"]+' "$LINKS_FILE" | sort -V
                ;;
            "Xavier NX")
                grep -oP 'nx_names\["\K[^"]+' "$LINKS_FILE" | sort -V
                ;;
            "Nano")
                grep -oP 'nano_names\["\K[^"]+' "$LINKS_FILE" | sort -V
                ;;
        esac
        
        echo ""
        echo -e "${YELLOW}Your version: $L4T_VERSION${NC}"
        echo ""
        echo -e "${BOLD}=====================================================================${NC}"
        echo -e "${BOLD}  TROUBLESHOOTING GUIDE                                              ${NC}"
        echo -e "${BOLD}=====================================================================${NC}"
        echo ""
        echo -e "${CYAN}Option 1: Manual Driver Installation (Recommended)${NC}"
        echo "  Your L4T version does not have an exact driver match."
        echo "  You can manually install the closest compatible driver:"
        echo ""
        echo "  1. Visit the Arducam releases page:"
        echo "     https://github.com/ArduCAM/MIPI_Camera/releases"
        echo ""
        echo "  2. Download the .deb package closest to your L4T version."
        echo "     Look for packages matching 'tegra-35.6' in the filename."
        echo ""
        echo "  3. Install manually:"
        echo "     sudo dpkg -i <downloaded_package>.deb"
        echo "     sudo reboot"
        echo ""
        echo -e "${CYAN}Option 2: Reflash with a Supported JetPack Version${NC}"
        echo "  If no compatible driver exists, consider reflashing your"
        echo "  Jetson with a JetPack version that matches an available driver."
        echo "  Use NVIDIA SDK Manager: https://developer.nvidia.com/sdk-manager"
        echo ""
        echo -e "${CYAN}Option 3: Contact Arducam Support${NC}"
        echo "  Email: support@arducam.com"
        echo "  GitHub: https://github.com/ArduCAM/MIPI_Camera/issues"
        echo "  Include your L4T version and Jetson model in your request."
        echo ""
        echo -e "${BOLD}=====================================================================${NC}"
        exit 1
    else
        # Installation succeeded or different error
        if echo "$INSTALL_OUTPUT" | grep -q "reboot"; then
            echo ""
            log_success "Driver installation completed!"
            echo -e "${YELLOW}Restarting nvargus-daemon...${NC}"
            sudo systemctl restart nvargus-daemon || true
            echo -e "${YELLOW}A system reboot is required for external modules.${NC}"
            read -p "Reboot now? (y/N): " REBOOT
            if [[ $REBOOT =~ ^[Yy]$ ]]; then
                sudo reboot
            fi
        else
            echo "$INSTALL_OUTPUT"
            if [ $? -eq 0 ]; then
                log_success "Driver installation completed!"
                sudo systemctl restart nvargus-daemon || true
            else
                log_error "Installation encountered an issue. Check output above."
            fi
        fi
    fi
}

# ==========================================
# MAIN EXECUTION
# ==========================================

check_connectivity
install_tools

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

case "$CONN_TYPE" in
    2)
        setup_usb_camera
        ;;
    *)
        setup_csi_camera
        ;;
esac
