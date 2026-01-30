# Quick Start

Get running in 15 minutes.

## Prerequisites

✅ Jetson device (Orin Nano/NX/AGX)  
✅ JetPack 5.1.1+  
✅ Arducam IMX519 camera(s)  
✅ Internet connection

## Setup (5 Steps)

### 1. Clone & Setup Cameras (5 min)

```bash
cd ~/Documents
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo.git
cd jetson-arducam-yolo

# Automated camera setup
chmod +x scripts/*.sh
./scripts/setup_cameras.sh
```

**System will reboot. After reboot, continue...**

### 2. Verify (2 min)

```bash
cd ~/Documents/jetson-arducam-yolo
./scripts/test_installation.sh
```

✅ All checks should pass

### 3. Build (5-10 min)

```bash
sudo docker build -t yolo_jp512:latest .
```

### 4. Run (30 sec)

```bash
sudo docker run -d --name yolo_ctr \
  --runtime nvidia --net=host --restart unless-stopped \
  --device=/dev/video0 --device=/dev/video1 \
  -v $(pwd):/workspace -w /workspace \
  yolo_jp512:latest
```

### 5. Test Detection

```bash
# No display
sudo docker exec -it yolo_ctr python3 examples/basic_detection.py --camera 0

# With display
sudo docker exec -it yolo_ctr python3 examples/basic_detection.py --camera 0 --display
```

## Performance Boost

**TensorRT (2-3x faster):**

```bash
sudo docker exec -it yolo_ctr python3 examples/tensorrt_export.py --model yolov8n.pt --export --compare

# Use optimized model
sudo docker exec -it yolo_ctr python3 examples/basic_detection.py --model yolov8n.engine --display
```

## Multi-Camera

```bash
# 2 cameras
sudo docker exec -it yolo_ctr python3 examples/multi_camera_detection.py --cameras 0 1 --display

# 4 cameras  
sudo docker exec -it yolo_ctr python3 examples/multi_camera_detection.py --cameras 0 1 2 3 --display
```

## Troubleshooting

**Camera not detected:**
```bash
ls -l /dev/video*
sudo systemctl restart nvargus-daemon
```

**Low FPS:**
```bash
sudo nvpmodel -m 0 && sudo jetson_clocks
```

**Out of memory:**
```bash
sudo fallocate -l 8G /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

## Expected Performance

| Model | FPS (Orin Nano 8GB) | Use Case |
|-------|---------------------|----------|
| YOLOv8n PyTorch | 15-20 | Testing |
| YOLOv8n TensorRT | 40-60 | Production |
| Multi-cam (2x) | 20-30 each | Surveillance |

*At 640x640 resolution*

## Common Commands

```bash
# Container management
sudo docker start yolo_ctr
sudo docker stop yolo_ctr
sudo docker logs yolo_ctr
sudo docker exec -it yolo_ctr bash
```

## Next Steps

- 📖 [Full Documentation](README.md)
- 🔧 [Troubleshooting](docs/TROUBLESHOOTING.md)
- 📚 [Advanced Usage](docs/USAGE.md)

---

**Made with ❤️ for the Jetson Community**
