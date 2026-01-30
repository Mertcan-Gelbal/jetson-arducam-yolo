# Usage Guide

This guide provides detailed usage instructions and examples for the YOLOv8 Arducam project.

## Table of Contents

- [Basic Usage](#basic-usage)
- [Advanced Features](#advanced-features)
- [Example Scripts](#example-scripts)
- [Docker Commands](#docker-commands)
- [Performance Optimization](#performance-optimization)
- [Best Practices](#best-practices)

## Basic Usage

### Running Basic Detection

The simplest way to get started:

```bash
# Enter the Docker container
sudo docker exec -it jetson-arducam-ctr bash

# Run basic detection
python3 examples/basic_detection.py --camera 0 --display

# With custom model
python3 examples/basic_detection.py --camera 0 --model yolov8s.pt --conf 0.3 --display
```

### Command-Line Arguments

All example scripts support common arguments:

| Argument | Description | Default |
|----------|-------------|---------|
| `--camera` | Camera device ID | `0` |
| `--model` | Path to YOLOv8 model | `yolov8n.pt` |
| `--conf` | Confidence threshold | `0.25` |
| `--iou` | IOU threshold for NMS | `0.45` |
| `--imgsz` | Input image size | `640` |
| `--display` | Display results | `False` |

## Advanced Features

### Multi-Camera Processing

Process multiple cameras simultaneously:

```bash
# 2 cameras
python3 examples/multi_camera_detection.py --cameras 0 1 --display

# 4 cameras
python3 examples/multi_camera_detection.py --cameras 0 1 2 3 --model yolov8n.pt --display

# Custom confidence threshold
python3 examples/multi_camera_detection.py --cameras 0 1 --conf 0.3 --display
```

**Features:**
- Threaded camera capture for maximum FPS
- Grid layout for multiple cameras
- Independent processing per camera
- Synchronized display

### Hardware-Accelerated Pipeline

Use GStreamer for optimal performance:

```bash
# nvargus camera (recommended)
python3 examples/gstreamer_pipeline.py \
  --sensor-id 0 \
  --capture-width 1920 \
  --capture-height 1080 \
  --display-width 1280 \
  --display-height 720 \
  --framerate 30 \
  --display

# V4L2 fallback
python3 examples/gstreamer_pipeline.py \
  --v4l2 \
  --device /dev/video0 \
  --display

# Different resolutions for performance
python3 examples/gstreamer_pipeline.py \
  --sensor-id 0 \
  --capture-width 3840 \  # 4K capture
  --capture-height 2160 \
  --display-width 640 \   # Downscale for inference
  --display-height 480 \
  --display
```

**GStreamer Parameters:**
- `--sensor-id`: Camera sensor ID (0, 1, 2, 3)
- `--capture-width/height`: Camera capture resolution
- `--display-width/height`: Processing resolution
- `--framerate`: Camera framerate
- `--flip`: Image rotation (0-5)

### TensorRT Optimization

Export and use TensorRT for maximum performance:

```bash
# Export to TensorRT
python3 examples/tensorrt_export.py \
  --model yolov8n.pt \
  --export

# Benchmark comparison
python3 examples/tensorrt_export.py \
  --model yolov8n.pt \
  --engine yolov8n.engine \
  --compare \
  --frames 100

# Test accuracy
python3 examples/tensorrt_export.py \
  --model yolov8n.pt \
  --engine yolov8n.engine \
  --test-accuracy \
  --test-image test.jpg

# Use FP32 instead of FP16
python3 examples/tensorrt_export.py \
  --model yolov8n.pt \
  --export \
  --fp32
```

**Expected Performance:**
- **YOLOv8n PyTorch FP32**: ~15-20 FPS on Jetson Orin Nano
- **YOLOv8n TensorRT FP16**: ~40-60 FPS on Jetson Orin Nano
- **Speedup**: 2-3x faster with TensorRT

### Using TensorRT Models

Once exported, use the engine file directly:

```python
from ultralytics import YOLO

# Load TensorRT engine (much faster than .pt)
model = YOLO('yolov8n.engine')

# Use normally
results = model(frame)
```

Or with examples:

```bash
python3 examples/basic_detection.py \
  --camera 0 \
  --model yolov8n.engine \
  --display
```

## Example Scripts

### 1. basic_detection.py

Simple single-camera detection with real-time annotation.

```bash
# Basic usage
python3 examples/basic_detection.py --display

# All options
python3 examples/basic_detection.py \
  --camera 0 \
  --model yolov8n.pt \
  --conf 0.25 \
  --iou 0.45 \
  --imgsz 640 \
  --display
```

**Use Cases:**
- Testing camera setup
- Quick detection demo
- Model evaluation

### 2. multi_camera_detection.py

Multi-threaded multi-camera processing.

```bash
# 2 cameras side-by-side
python3 examples/multi_camera_detection.py --cameras 0 1 --display

# 4 cameras in 2x2 grid
python3 examples/multi_camera_detection.py --cameras 0 1 2 3 --display
```

**Use Cases:**
- Multi-camera surveillance
- Stereo vision applications
- Wide-area monitoring

### 3. gstreamer_pipeline.py

Hardware-accelerated video pipeline for maximum FPS.

```bash
# High-res capture, downscaled for inference
python3 examples/gstreamer_pipeline.py \
  --sensor-id 0 \
  --capture-width 3840 \
  --capture-height 2160 \
  --display-width 640 \
  --display-height 480 \
  --display
```

**Use Cases:**
- Production deployments
- Maximum FPS applications
- High-resolution capture

### 4. tensorrt_export.py

Model optimization and benchmarking tool.

```bash
# Full workflow: export + benchmark + compare
python3 examples/tensorrt_export.py \
  --model yolov8n.pt \
  --export \
  --compare \
  --frames 100
```

**Use Cases:**
- Performance optimization
- Model evaluation
- Deployment preparation

## Docker Commands

### Container Management

```bash
# Start container
sudo docker start jetson-arducam-ctr

# Stop container
sudo docker stop jetson-arducam-ctr

# Restart container
sudo docker restart jetson-arducam-ctr

# Remove container
sudo docker rm jetson-arducam-ctr

# View logs
sudo docker logs -f jetson-arducam-ctr

# Check status
sudo docker ps -a | grep jetson-arducam-ctr
```

### Accessing Container

```bash
# Interactive shell
sudo docker exec -it jetson-arducam-ctr bash

# Run single command
sudo docker exec jetson-arducam-ctr python3 --version

# Run with display (X11 forwarding)
xhost +local:docker
sudo docker exec -it -e DISPLAY=$DISPLAY jetson-arducam-ctr bash
```

### Resource Monitoring

```bash
# Real-time stats
sudo docker stats jetson-arducam-ctr

# Resource limits
sudo docker update --memory=6g jetson-arducam-ctr
```

## Performance Optimization

### 1. System Configuration

```bash
# Maximum performance mode
sudo nvpmodel -m 0
sudo jetson_clocks

# Verify settings
sudo nvpmodel -q
sudo tegrastats
```

### 2. Model Selection

Choose the right model for your use case:

| Model | Size | Speed | Accuracy | Use Case |
|-------|------|-------|----------|----------|
| YOLOv8n | 6.3 MB | Fastest | Good | Real-time, multi-camera |
| YOLOv8s | 21.5 MB | Fast | Better | Balanced |
| YOLOv8m | 49.7 MB | Medium | Best | Single camera, accuracy-critical |

### 3. Input Resolution

Lower resolution = faster inference:

```python
# 320x320 - Fastest, lower accuracy
results = model(frame, imgsz=320)

# 640x640 - Balanced (default)
results = model(frame, imgsz=640)

# 1280x1280 - Slowest, best accuracy
results = model(frame, imgsz=1280)
```

### 4. Confidence Threshold

Higher threshold = fewer detections = faster post-processing:

```python
# Faster, only high-confidence detections
results = model(frame, conf=0.5)

# Slower, more detections
results = model(frame, conf=0.1)
```

### 5. Skip Frames

Process every Nth frame for higher throughput:

```python
frame_count = 0
skip_frames = 2  # Process every 2nd frame

while True:
    ret, frame = cap.read()
    frame_count += 1
    
    if frame_count % skip_frames == 0:
        results = model(frame)
```

## Best Practices

### Memory Management

```python
import torch

# Clear cache periodically
torch.cuda.empty_cache()

# Use with statement for automatic cleanup
with torch.no_grad():
    results = model(frame)
```

### Error Handling

```python
import cv2

cap = cv2.VideoCapture(0)

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to capture frame, retrying...")
            cap.release()
            cap = cv2.VideoCapture(0)
            continue
        
        results = model(frame)
        
except KeyboardInterrupt:
    print("Stopping...")
finally:
    cap.release()
    cv2.destroyAllWindows()
```

### Batch Processing

For multiple images:

```python
# Process batch (faster than individual)
image_list = [img1, img2, img3, img4]
results = model(image_list)

# Process each result
for r in results:
    boxes = r.boxes
    print(f"Found {len(boxes)} objects")
```

### Save Results

```python
# Save annotated image
results = model(frame)
annotated = results[0].plot()
cv2.imwrite('output.jpg', annotated)

# Save to video
fourcc = cv2.VideoWriter_fourcc(*'mp4v')
out = cv2.VideoWriter('output.mp4', fourcc, 30.0, (1280, 720))

while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    results = model(frame)
    annotated = results[0].plot()
    out.write(annotated)

out.release()
```

### Custom Classes

Filter specific object classes:

```python
# Only detect persons (class 0)
results = model(frame, classes=[0])

# Multiple classes: person (0), car (2), truck (7)
results = model(frame, classes=[0, 2, 7])

# Access class names
names = model.names
print(names)  # {0: 'person', 1: 'bicycle', ...}
```

## Production Deployment

### Systemd Service

Create `/etc/systemd/system/yolo-detection.service`:

```ini
[Unit]
Description=YOLOv8 Detection Service
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/docker exec jetson-arducam-ctr python3 /workspace/examples/basic_detection.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable yolo-detection.service
sudo systemctl start yolo-detection.service
```

### Logging

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('detection.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Use in code
logger.info("Starting detection...")
logger.warning(f"Low FPS: {fps}")
logger.error("Camera failed")
```

## Additional Resources

- [Ultralytics YOLOv8 Docs](https://docs.ultralytics.com/)
- [GStreamer Documentation](https://gstreamer.freedesktop.org/documentation/)
- [NVIDIA Jetson Developer Guide](https://developer.nvidia.com/embedded/learn/get-started-jetson-orin-nano-devkit)

## Getting Help

If you encounter issues:

1. Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
2. Review logs: `sudo docker logs jetson-arducam-ctr`
3. Open an issue on GitHub

---

**Tip:** Always test with the smallest model (yolov8n) first, then scale up if accuracy requirements aren't met.
