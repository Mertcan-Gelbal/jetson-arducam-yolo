# VisionDock Production Deployment Guide

This document outlines how to take the VisionDock Jetson setup from a development environment to a hardened, headless production line configuration (Phase 8).

## Overview

In production, the Jetson device runs the `visiondock-runtime` service automatically on boot. It operates as an HTTP API backend taking hardware triggers (e.g., via a foot pedal connected to GPIO) and driving output signals (e.g., PLCs, tower lights). The desktop GUI acts purely as a monitoring and configuration dashboard over the network.

## 1. System Requirements Check

Ensure your Jetson meets the requirements for a headless inspection station:
1. The correct CSI driver for your module (for example IMX219, IMX477, or IMX519) or a supported USB camera is installed.
2. `~/.visiondock` directory initialized.
3. Relevant models (e.g., YOLO ONNX or TensorRT engine) exported and activated via the GUI.

Run the self-check diagnostic to verify hardware and storage:
```bash
cd visiondock
python3 runtime/selfcheck.py
```
*Expected Output:* `VISIONDOCK RUNTIME SELFCHECK: PASS`

## 2. Installing the Systemd Runtime Service

To ensure the system boots directly into the inspection logic without requiring a user to log in or start docker containers, install the generic Systemd service:

```bash
cd visiondock
chmod +x scripts/install_service.sh
./scripts/install_service.sh
```

This script generates `/etc/systemd/system/visiondock-runtime.service` with the following key attributes:
- Starts automatically on boot (`WantedBy=multi-user.target graphical.target`).
- Recovers cleanly from crashes (`Restart=on-failure`).
- Executes `python3 -m runtime.inspection_service` as the primary process.

## 3. Storage and Disk Rotation

The runtime service continuously saves inspection results (images and JSON metadata) to `~/.visiondock/results/`. 

To prevent disk exhaustion on the Jetson SD card or eMMC, the runtime utilizes a built-in **Disk Rotation Strategy**:
- By default, it preserves the last **1500 limit** inspection cycles.
- When this limit is exceeded, older results and images are automatically pruned during the next inspection cycle.

*Note: If you need to retain more data, consider mounting an external NVMe drive to the `~/.visiondock/results/` path.*

## 4. Hardware Triggering and GPIO Integration

### Setting Up a Pedal/Button (Input Pin)
For operator-initiated inspections, wire a pedal or button to a Jetson GPIO pin. Default behavior expects:
- Pull-up resistor circuit (Jetson internal pull-up is utilized).
- Trigger edge: `falling` (Connecting the pin to ground).

Enable the input logic using the GUI's `Settings -> Inspection camera` card. Sensor family and focus actuator are commissioning metadata and should be left locked for daily operators. The runtime profile is stored in `~/.visiondock/inspection_profile.json`:
```json
"gpio": {
  "enabled": true,
  "trigger_pin": 12,
  "trigger_edge": "falling"
}
```

### Tower Lights and PLCs (Output Pins)
Map specific states to Jetson output pins to signal operators:
- `PASS`, `FAIL`, `FAULT` (runtime error), `BUSY` (inference running).

To maintain safe power constraints, always route these signals through an **Optocoupler** or **Relay Board** rather than directly driving LEDs.

## 5. Connecting the GUI Monitor

Once the Jetson is in production, operators monitor the flow using a standard x86 PC or Mac running VisionDock GUI on the same factory network (or via ZeroTier).

1. In the GUI, navigate to `Devices`.
2. Add the Jetson's local IP (e.g., `192.168.1.50`).
3. Switch to the `Inspection` page to monitor runtime state and manual triggers.
4. Set the app to Production Mode by starting it with `VISIONDOCK_PRODUCTION=1 ./start_gui.sh` to prevent operators from accidentally deleting models.

## 6. Logs & Diagnostics

If the runtime fails repeatedly, inspect the systemd logs:
```bash
sudo journalctl -u visiondock-runtime -f -n 100
```
