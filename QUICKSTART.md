# Quick Start

## Prerequisites

*   NVIDIA Jetson device (Orin Nano/NX/AGX)
*   JetPack 5.x or 6.x
*   Arducam IMX519 camera(s)
*   Internet connection

## Installation Steps

### 1. Setup

Clone the repository and install camera drivers.

```bash
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo.git
cd jetson-arducam-yolo

# Install camera drivers (Requires Reboot)
chmod +x scripts/*.sh
./scripts/setup_cameras.sh
```

### 2. Verify System

After rebooting, verify that cameras are detected and requirements are met.

```bash
cd jetson-arducam-yolo
./scripts/test_installation.sh
```

### 3. Build & Run

Build the Docker environment. The script automatically detects your JetPack version.

```bash
# Build Docker image
./scripts/build_docker.sh

# Start Container
./scripts/run_docker.sh
```

### 4. Test Detection

Inside the container:

```bash
# Enter container
sudo docker exec -it jetson-arducam-ctr bash

# Run basic detection
python3 examples/basic_detection.py --camera 0 --display
```

## Performance

To enable maximum performance on the host:

```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```

For 2-3x faster inference, use the TensorRT example usage in `README.md`.
