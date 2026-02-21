#!/bin/bash
# Install Systemd Auto-Restart Service for Jetson Arducam AI
# This enables the AI stack or GUI to start automatically on boot and auto-heal on crash.

echo "Installing Jetson Arducam Systemd Service..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="jetson-arducam"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

# Create Service Definition
cat <<EOF | sudo tee $SERVICE_PATH > /dev/null
[Unit]
Description=Jetson Arducam AI Industrial Edge Service
After=network.target docker.service nvargus-daemon.service
Requires=docker.service nvargus-daemon.service

[Service]
Type=simple
User=$USER
Environment=DISPLAY=:0
Environment=QT_QPA_PLATFORM=xcb
WorkingDirectory=$SCRIPT_DIR

# You can change this to start_gui.sh or your headless analytics script:
# ExecStart=$SCRIPT_DIR/start_gui.sh
ExecStart=/usr/bin/python3 $SCRIPT_DIR/examples/basic_detection.py --source-type csi --model yolo11n.pt

# Auto-Restart Logic (Industrial resilience)
Restart=always
RestartSec=5
StartLimitIntervalSec=0

[Install]
WantedBy=graphical.target multi-user.target
EOF

# Reload and Enable
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling $SERVICE_NAME to start on boot..."
sudo systemctl enable $SERVICE_NAME

echo "========================================="
echo "Service Installed Successfully!"
echo "To start it now:   sudo systemctl start $SERVICE_NAME"
echo "To check status:   sudo systemctl status $SERVICE_NAME"
echo "To view logs:      journalctl -u $SERVICE_NAME -f"
echo "========================================="
