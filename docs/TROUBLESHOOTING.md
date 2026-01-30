# Troubleshooting Guide

This guide covers common issues and their solutions when working with Arducam cameras and YOLOv8 on Jetson devices.

## Table of Contents

- [Camera Issues](#camera-issues)
- [Docker Issues](#docker-issues)
- [Performance Issues](#performance-issues)
- [Model Issues](#model-issues)
- [System Issues](#system-issues)
- [Debugging Tools](#debugging-tools)

## Camera Issues

### Camera Not Detected

**Symptom:** No `/dev/video*` devices after camera installation

**Solutions:**

1. **Check physical connection:**
   ```bash
   # Power off Jetson
   sudo poweroff
   
   # Reconnect camera cables firmly
   # Power on and check
   ls -l /dev/video*
   ```

2. **Verify camera driver installation:**
   ```bash
   # Check if IMX519 driver is installed
   dmesg | grep -i imx519
   
   # Reinstall if necessary
   sudo ./install_full.sh -m imx519
   sudo reboot
   ```

3. **Check kernel modules:**
   ```bash
   lsmod | grep imx519
   
   # If not loaded, try manual load
   sudo modprobe imx519
   ```

### nvargus-daemon Errors

**Symptom:** `nvargus-daemon` fails or shows errors in logs

**Solutions:**

1. **Check daemon status:**
   ```bash
   sudo systemctl status nvargus-daemon --no-pager
   ```

2. **View detailed logs:**
   ```bash
   sudo journalctl -u nvargus-daemon -n 200 --no-pager
   ```

3. **Restart daemon:**
   ```bash
   sudo systemctl restart nvargus-daemon
   sudo systemctl enable nvargus-daemon
   ```

4. **Common error: "NvPclHwGetModuleList: No module found"**
   ```bash
   # This means camera is not detected
   # Check I2C communication
   sudo i2cdetect -y -r 7
   sudo i2cdetect -y -r 9
   
   # Verify camera power
   v4l2-ctl --list-devices
   ```

### Multi-Camera I2C Issues

**Symptom:** Only one camera works in multi-camera setup

**Solutions:**

1. **Check CamArray I2C multiplexer:**
   ```bash
   # Find the correct I2C bus
   ls -l /dev/i2c*
   
   # Scan for multiplexer (address 0x70)
   sudo i2cdetect -y -r 7
   sudo i2cdetect -y -r 9
   ```

2. **Manually switch camera:**
   ```bash
   # Select camera 0
   sudo i2cset -y 7 0x70 0x00 0x01
   
   # Select camera 1
   sudo i2cset -y 7 0x70 0x00 0x02
   
   # Select camera 2
   sudo i2cset -y 7 0x70 0x00 0x04
   
   # Select camera 3
   sudo i2cset -y 7 0x70 0x00 0x08
   ```

3. **Verify all cameras:**
   ```bash
   for i in {0..3}; do
       echo "Testing camera $i"
       gst-launch-1.0 nvarguscamerasrc sensor-id=$i ! \
         'video/x-raw(memory:NVMM),width=640,height=480,framerate=30/1' ! \
         fakesink -e
   done
   ```

### GStreamer Pipeline Errors

**Symptom:** `gst-launch-1.0` fails with errors

**Solutions:**

1. **"Could not open resource for reading and writing"**
   ```bash
   # Check camera permissions
   ls -l /dev/video*
   
   # Add user to video group
   sudo usermod -aG video $USER
   
   # Reboot
   sudo reboot
   ```

2. **"Failed to allocate required memory"**
   ```bash
   # Reduce resolution or framerate
   gst-launch-1.0 nvarguscamerasrc sensor-id=0 ! \
     'video/x-raw(memory:NVMM),width=640,height=480,framerate=15/1' ! \
     nvvidconv ! nvegltransform ! nveglglessink -e
   ```

3. **Enable debug output:**
   ```bash
   GST_DEBUG=3 gst-launch-1.0 nvarguscamerasrc sensor-id=0 ! \
     'video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1' ! \
     nvvidconv ! nvegltransform ! nveglglessink -e
   ```

## Docker Issues

### Docker Build Out of Memory

**Symptom:** Build fails with "Killed" or "Out of memory"

**Solutions:**

1. **Enable swap:**
   ```bash
   # Create 8GB swap
   sudo fallocate -l 8G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

2. **Build with memory limits:**
   ```bash
   sudo docker build --memory=6g --memory-swap=8g -t yolo_jp512:latest .
   ```

3. **Clean Docker cache:**
   ```bash
   sudo docker system prune -a
   ```

### Docker Container Can't Access Camera

**Symptom:** Camera works on host but not in container

**Solutions:**

1. **Verify device mapping:**
   ```bash
   # List cameras on host
   ls -l /dev/video*
   
   # Run container with explicit device mapping
   sudo docker run -it --rm \
     --runtime nvidia \
     --device=/dev/video0 \
     --device=/dev/video1 \
     yolo_jp512:latest \
     ls -l /dev/video*
   ```

2. **Use privileged mode (not recommended for production):**
   ```bash
   sudo docker run -it --rm \
     --runtime nvidia \
     --privileged \
     -v /dev:/dev \
     yolo_jp512:latest bash
   ```

3. **Check container logs:**
   ```bash
   sudo docker logs yolo_ctr
   ```

### NVIDIA Runtime Not Found

**Symptom:** "unknown or invalid runtime name: nvidia"

**Solutions:**

1. **Install nvidia-container-runtime:**
   ```bash
   sudo apt-get install nvidia-container-runtime
   sudo systemctl restart docker
   ```

2. **Configure Docker daemon:**
   ```bash
   sudo nano /etc/docker/daemon.json
   ```
   
   Add:
   ```json
   {
       "runtimes": {
           "nvidia": {
               "path": "nvidia-container-runtime",
               "runtimeArgs": []
           }
       },
       "default-runtime": "nvidia"
   }
   ```
   
   ```bash
   sudo systemctl restart docker
   ```

## Performance Issues

### Low FPS (Frames Per Second)

**Symptom:** Inference is slower than expected

**Solutions:**

1. **Set maximum performance mode:**
   ```bash
   # Set MAXN power mode
   sudo nvpmodel -m 0
   
   # Lock clocks
   sudo jetson_clocks
   
   # Verify
   sudo nvpmodel -q
   ```

2. **Use TensorRT optimized model:**
   ```python
   from ultralytics import YOLO
   
   # Export to TensorRT with FP16
   model = YOLO('yolov8n.pt')
   model.export(format='engine', device=0, half=True)
   
   # Load optimized model
   model = YOLO('yolov8n.engine')
   ```

3. **Reduce model size:**
   ```python
   # Use nano model instead of larger variants
   model = YOLO('yolov8n.pt')  # Fastest
   # model = YOLO('yolov8s.pt')  # Medium
   # model = YOLO('yolov8m.pt')  # Slower
   ```

4. **Optimize input resolution:**
   ```python
   # Resize frames before inference
   results = model(frame, imgsz=640)  # Try 320, 416, 640
   ```

### High Memory Usage

**Symptom:** System runs out of memory

**Solutions:**

1. **Enable memory monitoring:**
   ```bash
   # Install jtop
   sudo pip3 install jetson-stats
   sudo reboot
   
   # Monitor in real-time
   sudo jtop
   ```

2. **Increase swap:**
   ```bash
   # Remove old swap
   sudo swapoff /swapfile
   sudo rm /swapfile
   
   # Create larger swap (16GB)
   sudo fallocate -l 16G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   ```

3. **Disable desktop environment:**
   ```bash
   # Boot to console mode (frees ~500MB)
   sudo systemctl set-default multi-user.target
   sudo reboot
   
   # To restore desktop
   sudo systemctl set-default graphical.target
   sudo reboot
   ```

### GPU Not Being Used

**Symptom:** CUDA shows as unavailable

**Solutions:**

1. **Verify CUDA installation:**
   ```bash
   python3 -c "import torch; print(torch.cuda.is_available())"
   ```

2. **Check CUDA version:**
   ```bash
   nvcc --version
   ```

3. **Verify GPU access:**
   ```bash
   nvidia-smi  # or sudo tegrastats
   ```

## Model Issues

### Model Download Fails

**Symptom:** Cannot download YOLOv8 weights

**Solutions:**

1. **Check internet connection:**
   ```bash
   ping -c 4 google.com
   ```

2. **Manually download model:**
   ```bash
   # Download from GitHub releases
   wget https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt
   ```

3. **Use local model path:**
   ```python
   model = YOLO('/path/to/yolov8n.pt')
   ```

### TensorRT Export Fails

**Symptom:** Error when exporting to TensorRT engine

**Solutions:**

1. **Check TensorRT installation:**
   ```bash
   dpkg -l | grep TensorRT
   ```

2. **Export with specific settings:**
   ```python
   model = YOLO('yolov8n.pt')
   model.export(
       format='engine',
       device=0,
       half=True,
       workspace=4,  # GB
       verbose=True
   )
   ```

3. **Use ONNX as intermediate:**
   ```python
   # First export to ONNX
   model.export(format='onnx', simplify=True)
   
   # Then convert ONNX to TensorRT using trtexec
   ```

## System Issues

### Disk Space Full

**Symptom:** "No space left on device"

**Solutions:**

1. **Check disk usage:**
   ```bash
   df -h
   du -sh /* | sort -h
   ```

2. **Clean Docker images:**
   ```bash
   sudo docker system prune -a
   sudo docker volume prune
   ```

3. **Remove logs:**
   ```bash
   sudo journalctl --vacuum-time=3d
   ```

4. **Move Docker to external storage:**
   ```bash
   # Stop Docker
   sudo systemctl stop docker
   
   # Move data
   sudo mv /var/lib/docker /mnt/external/docker
   sudo ln -s /mnt/external/docker /var/lib/docker
   
   # Start Docker
   sudo systemctl start docker
   ```

### System Freezes or Crashes

**Symptom:** Jetson becomes unresponsive

**Solutions:**

1. **Check temperature:**
   ```bash
   # Monitor temperature
   watch -n 1 'cat /sys/devices/virtual/thermal/thermal_zone*/temp'
   ```

2. **Ensure adequate cooling:**
   - Add heatsink or fan
   - Improve airflow

3. **Reduce workload:**
   ```bash
   # Lower power mode
   sudo nvpmodel -m 1
   ```

## Debugging Tools

### System Information

```bash
# JetPack version
sudo apt-cache show nvidia-jetpack | grep Version

# L4T version
head -n 1 /etc/nv_tegra_release

# CUDA version
nvcc --version

# Kernel version
uname -a

# Memory info
free -h
```

### Camera Debugging

```bash
# List all V4L2 devices
v4l2-ctl --list-devices

# Get detailed device info
v4l2-ctl -d /dev/video0 --all

# Test pattern
v4l2-ctl -d /dev/video0 --set-ctrl bypass_mode=0

# Capture test image
v4l2-ctl -d /dev/video0 --set-fmt-video=width=1920,height=1080 \
  --stream-mmap --stream-count=1 --stream-to=test.raw
```

### Docker Debugging

```bash
# Container logs
sudo docker logs -f yolo_ctr

# Execute commands in container
sudo docker exec -it yolo_ctr bash

# Check container resource usage
sudo docker stats yolo_ctr

# Inspect container
sudo docker inspect yolo_ctr
```

### GStreamer Debugging

```bash
# Enable full debug
export GST_DEBUG=4
gst-launch-1.0 nvarguscamerasrc sensor-id=0 ! fakesink

# List available plugins
gst-inspect-1.0

# Check specific plugin
gst-inspect-1.0 nvarguscamerasrc
```

## Getting More Help

If you still experience issues:

1. **Check system logs:**
   ```bash
   sudo dmesg | tail -n 100
   sudo journalctl -xe
   ```

2. **Search existing issues:**
   - [Project Issues](https://github.com/Mertcan-Gelbal/jetson-arducam-yolo/issues)
   - [Arducam Forums](https://www.arducam.com/forums/)
   - [NVIDIA Developer Forums](https://forums.developer.nvidia.com/c/agx-autonomous-machines/jetson-embedded-systems/)

3. **Create a detailed issue report including:**
   - Jetson model and JetPack version
   - Complete error messages
   - Steps to reproduce
   - Output from debugging commands above

4. **Useful resources:**
   - [JetsonHacks](https://jetsonhacks.com/)
   - [dusty-nv GitHub](https://github.com/dusty-nv)
   - [Arducam Documentation](https://docs.arducam.com/)
