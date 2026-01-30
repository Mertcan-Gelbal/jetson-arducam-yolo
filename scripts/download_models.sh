#!/bin/bash
# 
# Helper script to download common YOLO models pre-emptively
#

GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

MODELS=(
    "yolov8n.pt"  # YOLOv8 Nano
    "yolov8s.pt"  # YOLOv8 Small
    "yolo11n.pt"  # YOLO11 Nano (Latest)
    "yolo11s.pt"  # YOLO11 Small
)

echo -e "${CYAN}Jetson AI Model Downloader${NC}"
echo "--------------------------"

mkdir -p models
cd models

for model in "${MODELS[@]}"; do
    if [ -f "$model" ]; then
        echo -e "${GREEN}✓ $model already exists.${NC}"
    else
        echo "Downloading $model..."
        wget -q --show-progress "https://github.com/ultralytics/assets/releases/download/v8.2.0/$model"
    fi
done

echo ""
echo -e "${GREEN}All models ready in /models directory.${NC}"
