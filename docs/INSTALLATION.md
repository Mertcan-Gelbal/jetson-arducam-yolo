# Installation Guide

This guide provides comprehensive instructions for setting up the YOLOv8 Arducam environment on NVIDIA Jetson devices.

## Table of Contents

- [Prerequisites](#prerequisites)
- [System Preparation](#system-preparation)
- [Camera Driver Installation](#camera-driver-installation)
- [Docker Setup](#docker-setup)
- [Container Deployment](#container-deployment)
- [Verification](#verification)
- [Performance Optimization](#performance-optimization)

## Prerequisites

### Hardware Setup

1. **Jetson Device**: Ensure your Jetson is properly powered and connected
2. **Camera Connection**: Attach Arducam IMX519 cameras to MIPI CSI ports
3. **Storage**: Verify you have sufficient storage (32GB minimum)
4. **Cooling**: Ensure proper cooling for extended operation

### Required Software

- JetPack 5.1.1 or higher installed
- Internet connection for downloading packages
- SSH access (recommended for remote setup)

## System Preparation

### 1. Update System Packages

```bash
# Update package lists
sudo apt update

# Upgrade existing packages
sudo apt upgrade -y

# Install essential tools
sudo apt install -y wget curl git i2c-tools v4l-utils nano
```

### 2. Check JetPack Version

```bash
# Verify JetPack version
sudo apt-cache show nvidia-jetpack | grep Version

# Expected output: Version: 5.1.1-b56 or higher
```

### 3. Configure Swap Space

Jetson devices benefit from swap space, especially during model compilation:

```bash
# Create 8GB swap file
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Verify swap is active
free -h

# Make swap persistent across reboots
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Optional: Reduce swappiness for better performance
sudo sysctl vm.swappiness=10
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
```

### 4. Set Power Mode to Maximum Performance

```bash
# Set to MAXN mode (maximum performance)
sudo nvpmodel -m 0

# Lock clocks to maximum frequency
sudo jetson_clocks

# Verify power mode
sudo nvpmodel -q
```

## Camera Driver Installation

### 1. Download Arducam Installation Script

```bash
# Navigate to home directory
cd ~

# Download the official Arducam installer
wget https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3/install_full.sh

# Make executable
chmod +x install_full.sh
```

### 2. List Available Camera Modules

```bash
# Check which camera modules are supported
./install_full.sh -l
```

You should see a list including:
- imx219
- imx477
- **imx519** ← We'll install this one
- ov9281
- etc.

### 3. Install IMX519 Driver

```bash
# Install the IMX519 camera driver
sudo ./install_full.sh -m imx519

# System will prompt for confirmation
# Type 'y' and press Enter

# Reboot is required after installation
sudo reboot
```

### 4. Verify Camera Detection

After reboot, verify cameras are detected:

```bash
# List video devices
ls -l /dev/video*

# Expected output:
# /dev/video0
# /dev/video1
# (and more if you have additional cameras)

# Check detailed device information
v4l2-ctl --list-devices
```

### 5. Verify nvargus Daemon

The nvargus daemon manages camera access:

```bash
# Check daemon status
sudo systemctl status nvargus-daemon --no-pager

# If not running, start it
sudo systemctl start nvargus-daemon

# Enable on boot
sudo systemctl enable nvargus-daemon
```

### 6. Test Camera with GStreamer

Test each camera individually:

```bash
# Test Camera 0
gst-launch-1.0 nvarguscamerasrc sensor-id=0 ! \
  'video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1' ! \
  nvvidconv ! nvegltransform ! nveglglessink -e

# Press Ctrl+C to stop

# Test Camera 1
gst-launch-1.0 nvarguscamerasrc sensor-id=1 ! \
  'video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1' ! \
  nvvidconv ! nvegltransform ! nveglglessink -e
```

If you see video output, cameras are working correctly!

### 7. Check I2C Communication (Multi-Camera Arrays)

For CamArray boards with I2C multiplexer:

```bash
# Detect I2C buses
ls -l /dev/i2c*

# Scan for devices on typical buses
sudo i2cdetect -y -r 7
sudo i2cdetect -y -r 9

# Look for address 0x70 (CamArray multiplexer)
```

## Docker Setup

### 1. Verify Docker Installation

Docker should be pre-installed with JetPack:

```bash
# Check Docker version
docker --version

# Check NVIDIA runtime
docker info | grep -i runtime
```

### 2. Configure Docker Daemon

Create or edit Docker daemon configuration:

```bash
# Create daemon configuration
sudo mkdir -p /etc/docker

# Edit daemon.json
sudo nano /etc/docker/daemon.json
```

Add the following content:

```json
{
    "runtimes": {
        "nvidia": {
            "path": "nvidia-container-runtime",
            "runtimeArgs": []
        }
    },
    "default-runtime": "nvidia",
    "storage-driver": "overlay2",
    "data-root": "/var/lib/docker"
}
```

Save and exit (Ctrl+X, Y, Enter)

```bash
# Restart Docker
sudo systemctl restart docker

# Verify configuration
docker info | grep -i runtime
# Should show: Default Runtime: nvidia
```

### 3. Add User to Docker Group (Optional)

To run Docker without sudo:

```bash
# Add current user to docker group
sudo usermod -aG docker $USER

# Log out and back in for changes to take effect
# Or run: newgrp docker
```

## Container Deployment

### 1. Clone Repository

```bash
# Navigate to your projects directory
cd ~/Documents

# Clone the repository
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo.git
cd jetson-arducam-yolo
```

### 2. Review Dockerfile

The Dockerfile uses NVIDIA's official L4T PyTorch base image:

```bash
# View Dockerfile contents
cat Dockerfile
```

Key components:
- Base: `nvcr.io/nvidia/l4t-pytorch:r35.4.1-pth2.0-py3`
- OpenCV with CUDA support
- GStreamer with hardware acceleration
- v4l-utils for camera control
- YOLOv8 (Ultralytics)

### 3. Build Docker Image

We provide an automated script that detects your JetPack version and builds the correct image:

```bash
# Build the container (takes 10-20 minutes)
./scripts/build_docker.sh
```

**Troubleshooting Build Issues:**

If build fails due to memory:
```bash
# The script will auto-detect low memory and suggest creating a swap file.
# Follow the on-screen instructions.
```

### 4. Run Container

Use the run script to automatically handle device mounting:

```bash
# Start the container
./scripts/run_docker.sh
```

**What the script does:**
- Mounts all `/dev/video*` devices
- Sets up NVIDIA runtime
- Mounts current directory to workspace
- Auto-restarts on boot

### 5. Verify Container is Running

```bash
# Check container status
sudo docker ps

# View container logs
sudo docker logs jetson-arducam-ctr

# Access container shell
sudo docker exec -it jetson-arducam-ctr bash
```

## Verification

### 1. Test Python Environment

Inside the container:

```bash
# Enter container
sudo docker exec -it jetson-arducam-ctr bash

# Test Python
python3 --version

# Test PyTorch
python3 -c "import torch; print(f'PyTorch: {torch.__version__}'); print(f'CUDA: {torch.cuda.is_available()}')"

# Expected output:
# PyTorch: 2.0.x
# CUDA: True
```

### 2. Test OpenCV with CUDA

```bash
python3 -c "import cv2; print(f'OpenCV: {cv2.__version__}'); print(f'CUDA: {cv2.cuda.getCudaEnabledDeviceCount()}')"

# Expected output should show OpenCV version and CUDA device count > 0
```

### 3. Test YOLOv8 Installation

```bash
# Inside container
python3 << 'EOF'
from ultralytics import YOLO
import torch

print(f"Ultralytics installed: ✓")
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CUDA device: {torch.cuda.get_device_name(0)}")

# Download and load YOLOv8 nano model
model = YOLO('yolov8n.pt')
print("YOLOv8 model loaded successfully: ✓")
EOF
```

### 4. Test Camera Access

```bash
# Inside container
python3 << 'EOF'
import cv2

# Try to open camera
cap0 = cv2.VideoCapture(0)
ret, frame = cap0.read()

if ret:
    print(f"Camera 0: ✓ (Resolution: {frame.shape[1]}x{frame.shape[0]})")
else:
    print("Camera 0: ✗ Failed to capture")

cap0.release()
EOF
```

## Performance Optimization

### 1. Enable Maximum Performance

```bash
# On the host (not in container)
sudo nvpmodel -m 0
sudo jetson_clocks

# Verify settings
sudo tegrastats
```

### 2. Monitor System Resources

```bash
# CPU, GPU, and memory usage
sudo tegrastats

# Detailed GPU info
sudo nvidia-smi

# Disk usage
df -h
```

### 3. Optional: Install jtop (Jetson Stats)

```bash
# On the host
sudo pip3 install jetson-stats
sudo reboot

# After reboot, monitor system
sudo jtop
```

## Next Steps

After successful installation:

1. Review [USAGE.md](USAGE.md) for running inference examples
2. Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if you encounter issues
3. Explore the `examples/` directory for sample scripts

## Common Installation Issues

### Issue: Camera Not Detected

**Solution:**
```bash
# Check kernel messages
dmesg | grep -i imx519

# Restart nvargus daemon
sudo systemctl restart nvargus-daemon

# Check I2C communication
sudo i2cdetect -y -r 7
```

### Issue: Docker Build Out of Memory

**Solution:**
```bash
# Add swap space (see System Preparation)
# Try building with reduced workers
sudo docker build --memory=4g --memory-swap=8g -t jetson-arducam:latest .
```

### Issue: Permission Denied for Docker

**Solution:**
```bash
# Either use sudo
sudo docker ps

# Or add user to docker group
sudo usermod -aG docker $USER
newgrp docker
```

For more issues, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
