#!/bin/bash
#
# Jetson Arducam AI Kit - Modular Master Installer
# Supports standalone execution and automated flags
#

set -e

# Configuration
VERSION="2.0.0"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Helpers
show_help() {
    echo "Usage: ./install.sh [OPTION]"
    echo ""
    echo "Options:"
    echo "  --all        Full installation (Drivers -> Verify -> Build -> Run)"
    echo "  --drivers    Run only Camera Setup Wizard"
    echo "  --verify     Run only system diagnostics"
    echo "  --build      Build/Rebuild Docker environment"
    echo "  --run        Launch the container"
    echo "  --help       Show this help message"
    echo ""
}

log_info() { echo -e "${CYAN}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Header
clear
echo -e "${CYAN}${BOLD}======================================================${NC}"
echo -e "${CYAN}${BOLD}   Jetson Arducam AI Kit v$VERSION              ${NC}"
echo -e "${CYAN}${BOLD}======================================================${NC}"
echo ""

# Permission Check
chmod +x scripts/*.sh

# Main Logic
case "$1" in
    --drivers)
        log_info "Starting Camera Driver Setup..."
        ./scripts/setup_cameras.sh
        ;;
    --verify)
        log_info "Running System Verification..."
        ./scripts/test_installation.sh
        ;;
    --build)
        log_info "Building AI Environment..."
        ./scripts/build_docker.sh
        ;;
    --run)
        log_info "Launching Container..."
        ./scripts/run_docker.sh
        ;;
    --all|"")
        if [ -z "$1" ]; then
            log_info "Starting Interactive Full Installation..."
        else
            log_info "Starting Automated Full Installation..."
        fi
        
        # 1. Hardware
        ./scripts/setup_cameras.sh
        
        # 2. Verify
        echo ""
        log_info "Verifying system state..."
        ./scripts/test_installation.sh || log_warn "Verification had warnings. Proceeding..."
        
        # 3. Build
        echo ""
        log_info "Building Docker environment..."
        ./scripts/build_docker.sh
        
        # 4. Success Output
        echo ""
        echo -e "${GREEN}${BOLD}Installation Complete!${NC}"
        echo "Start your system with: ./scripts/run_docker.sh"
        ;;
    --help)
        show_help
        ;;
    *)
        log_error "Unknown option: $1\nUse --help for usage instructions."
        ;;
esac
