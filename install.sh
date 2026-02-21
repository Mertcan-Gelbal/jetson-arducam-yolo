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
    echo "Running without options performs full installation."
    echo ""
    echo "Options:"
    echo "  (none)       Full installation (Drivers -> Verify -> Build)"
    echo "  --drivers    Run only Camera Setup Wizard"
    echo "  --verify     Run only system diagnostics"
    echo "  --build      Build/Rebuild Docker environment"
    echo "  --run        Launch the container (docker run)"
    echo "  --compose    Launch via Docker Compose (recommended for production)"
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
        log_info "Launching Container (docker run)..."
        ./scripts/run_docker.sh
        ;;
    --compose)
        log_info "Launching via Docker Compose..."
        docker compose up -d
        echo ""
        log_success "Container started. Enter with:"
        echo "  docker compose exec jetson-ai bash"
        ;;
    --help)
        show_help
        ;;
    "")
        # Default: Full Installation
        log_info "Starting Full Installation..."
        
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
        
        # 4. Success Output & Final Checklist
        echo ""
        echo -e "${GREEN}${BOLD}======================================================${NC}"
        echo -e "${GREEN}${BOLD}   Installation Successfully Completed!             ${NC}"
        echo -e "${GREEN}${BOLD}======================================================${NC}"
        echo ""
        echo -e "${BOLD}Final Technical Checklist:${NC}"
        echo -e " 1. ${CYAN}Maximizing Performance:${NC} Run 'sudo nvpmodel -m 0 && sudo jetson_clocks'"
        echo -e " 2. ${CYAN}Camera Permissions:${NC} User is in 'video' group? (verify with 'groups')"
        echo -e " 3. ${CYAN}Model Preparation:${NC} Popular models pre-downloaded? (run './scripts/download_models.sh')"
        echo -e " 4. ${CYAN}Diagnostic Tool:${NC} If anything fails, run './scripts/test_installation.sh'"
        echo ""
        echo -e "${BOLD}Quick Run Commands:${NC}"
        echo -e "  - Start (Compose):    ${YELLOW}docker compose up -d${NC}  [Recommended]"
        echo -e "  - Start (Script):     ${YELLOW}./scripts/run_docker.sh${NC}"
        echo -e "  - Enter Shell:        ${YELLOW}docker compose exec jetson-ai bash${NC}"
        echo -e "  - CSI Camera Demo:    ${YELLOW}python3 examples/basic_detection.py --source-type csi --display${NC}"
        echo -e "  - TensorRT Export:    ${YELLOW}python3 examples/tensorrt_export.py --model yolov8n.pt --export${NC}"
        echo ""
        echo "Enjoy your Jetson Arducam AI Kit!"
        ;;
    *)
        log_error "Unknown option: $1\nUse --help for usage instructions."
        ;;
esac
