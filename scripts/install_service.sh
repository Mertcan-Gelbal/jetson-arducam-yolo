#!/bin/bash
# Install VisionDock Inspection Runtime as a systemd service on Jetson.
# Run as the user who owns the VisionDock install (sudo required for systemd).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="visiondock-runtime"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
RUNTIME_USER="${1:-$USER}"

echo "Installing VisionDock Inspection Runtime service..."
echo "  Project root : $SCRIPT_DIR"
echo "  Service user : $RUNTIME_USER"

cat <<EOF | sudo tee "$SERVICE_PATH" > /dev/null
[Unit]
Description=VisionDock Inspection Runtime
After=network.target nvargus-daemon.service
Wants=nvargus-daemon.service

[Service]
Type=simple
User=${RUNTIME_USER}
WorkingDirectory=${SCRIPT_DIR}
Environment=PYTHONPATH=${SCRIPT_DIR}
Environment=VISIONDOCK_LOG_CONSOLE=0

# Start the inspection HTTP service (default: 127.0.0.1:8787)
# Override host/port as needed: --host 0.0.0.0 --port 8787
ExecStart=/usr/bin/python3 -m runtime.inspection_service --host 127.0.0.1 --port 8787

# Graceful shutdown
ExecStop=/bin/kill -TERM \$MAINPID
KillMode=process
TimeoutStopSec=10

# Auto-restart on failure
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5

[Install]
WantedBy=multi-user.target graphical.target
EOF

echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

echo "Enabling ${SERVICE_NAME} to start on boot..."
sudo systemctl enable "${SERVICE_NAME}"

echo "=========================================="
echo "Service installed: ${SERVICE_NAME}"
echo "  Start now  : sudo systemctl start ${SERVICE_NAME}"
echo "  Status     : sudo systemctl status ${SERVICE_NAME}"
echo "  Logs       : journalctl -u ${SERVICE_NAME} -f"
echo "  Stop       : sudo systemctl stop ${SERVICE_NAME}"
echo "=========================================="
