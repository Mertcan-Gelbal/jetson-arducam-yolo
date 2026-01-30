# YOLOv8 Arducam Multi-Camera Setup for NVIDIA Jetson

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![JetPack](https://img.shields.io/badge/JetPack-5.1.1-green.svg)](https://developer.nvidia.com/embedded/jetpack)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0-red.svg)](https://pytorch.org/)
[![Ultralytics](https://img.shields.io/badge/Ultralytics-8.2-blue.svg)](https://github.com/ultralytics/ultralytics)

A production-ready Docker environment for running YOLOv8 object detection with Arducam multi-camera arrays on NVIDIA Jetson devices. This project leverages hardware acceleration, GStreamer pipelines, and NVIDIA's L4T (Linux for Tegra) ecosystem for optimal performance.

## 🎯 Features

- **🐳 Containerized Deployment**: Docker-based setup following dusty-nv best practices
- **📷 Multi-Camera Support**: Arducam IMX519 stereo/multi-camera array integration
- **⚡ Hardware Accelerated**: Utilizes NVIDIA CUDA, TensorRT optimization for YOLOv8
- **🔧 GStreamer Integration**: Hardware-accelerated video pipeline with nvargus
- **🚀 Production Ready**: Auto-restart, device mapping, and volume mounting configured
- **📊 YOLOv8**: Latest Ultralytics implementation with TensorRT backend

## 📋 Table of Contents

- [Hardware Requirements](#hardware-requirements)
- [Software Requirements](#software-requirements)
- [Quick Start](#quick-start)
- [Detailed Installation](#detailed-installation)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)
- [Project Structure](#project-structure)
- [Performance Tips](#performance-tips)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## 🖥️ Hardware Requirements

- **Jetson Device**: Orin Nano, Orin NX, AGX Orin, or Xavier NX
- **Camera**: Arducam IMX519 Multi-Camera Kit
  - Support for 2-4 camera configurations
  - MIPI CSI-2 interface
- **Storage**: Minimum 32GB (64GB+ recommended for swap and models)
- **Memory**: 8GB+ RAM recommended
- **Power**: Use MAXN power mode for optimal performance

### Tested Configurations

| Device | JetPack Version | Cameras | Status |
|--------|----------------|---------|--------|
| Jetson Orin Nano 8GB | 5.1.1 | 2x IMX519 | ✅ Verified |
| Jetson Orin NX | 5.1.1 | 4x IMX519 | ✅ Verified |

## 💻 Software Requirements

- **JetPack**: 5.1.1 or higher (L4T R35.4.1)
- **Docker**: Docker runtime with NVIDIA container toolkit
- **CUDA**: 11.4+ (included in JetPack)
- **Python**: 3.8+ (provided in container)

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo.git
cd jetson-arducam-yolo
```

### 2. Install Camera Drivers

```bash
# Download and install Arducam drivers
wget https://github.com/ArduCAM/MIPI_Camera/releases/download/v0.0.3/install_full.sh
chmod +x install_full.sh
./install_full.sh -l  # List available cameras

# Install IMX519 driver
sudo ./install_full.sh -m imx519
sudo reboot
```

### 3. Verify Camera Installation

```bash
# Check camera devices
ls -l /dev/video*

# Verify argus daemon
sudo systemctl status nvargus-daemon

# Test camera with GStreamer
gst-launch-1.0 nvarguscamerasrc sensor-id=0 ! \
  'video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1' ! \
  nvvidconv ! nvegltransform ! nveglglessink -e
```

### 4. Build Docker Image

```bash
# Build the container
sudo docker build -t yolo_jp512:latest .
```

### 5. Run the Container

```bash
sudo docker run -d --name yolo_ctr \
  --runtime nvidia \
  --net=host \
  --restart unless-stopped \
  --device=/dev/video0 --device=/dev/video1 \
  -v $(pwd):/workspace \
  -w /workspace \
  yolo_jp512:latest
```

### 6. Access the Container

```bash
# Enter the container
sudo docker exec -it yolo_ctr bash

# Inside the container, test YOLOv8
python3 -c "from ultralytics import YOLO; print('YOLOv8 ready!')"
```

## 📖 Detailed Installation

For comprehensive installation instructions, please refer to:

- **[docs/INSTALLATION.md](docs/INSTALLATION.md)** - Complete installation guide with step-by-step instructions
- **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)** - Common issues and their solutions

## 📦 Usage

### Basic Object Detection

```python
from ultralytics import YOLO
import cv2

# Load YOLOv8 model
model = YOLO('yolov8n.pt')

# Open camera
cap = cv2.VideoCapture(0)

while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    # Run inference
    results = model(frame)
    
    # Display results
    annotated_frame = results[0].plot()
    cv2.imshow('YOLOv8', annotated_frame)
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
```

### Multi-Camera Processing

See [examples/multi_camera_detection.py](examples/multi_camera_detection.py) for a complete multi-camera implementation.

### More Examples

- **[examples/basic_detection.py](examples/basic_detection.py)** - Simple single-camera detection
- **[examples/gstreamer_pipeline.py](examples/gstreamer_pipeline.py)** - Hardware-accelerated video pipeline
- **[examples/tensorrt_export.py](examples/tensorrt_export.py)** - Export and optimize model for TensorRT

## 🔧 Troubleshooting

### Camera Not Detected

```bash
# Check I2C devices
sudo i2cdetect -y -r 7

# Check camera power
v4l2-ctl --list-devices

# Review system logs
dmesg | grep -i imx519
sudo journalctl -u nvargus-daemon -n 200 --no-pager
```

### Low Performance

```bash
# Set maximum performance mode
sudo nvpmodel -m 0
sudo jetson_clocks

# Check current power mode
sudo nvpmodel -q
```

### Out of Memory Errors

```bash
# Enable swap (8GB recommended)
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Make persistent
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

For more troubleshooting tips, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## 📁 Project Structure

```
jetson-arducam-yolo/
├── README.md                 # This file
├── Dockerfile                # Docker container definition
├── requirements.txt          # Python dependencies
├── .gitignore               # Git ignore rules
├── LICENSE                  # MIT License
├── docs/
│   ├── INSTALLATION.md      # Detailed installation guide
│   ├── TROUBLESHOOTING.md   # Common issues and solutions
│   └── USAGE.md             # Advanced usage examples
├── examples/
│   ├── basic_detection.py   # Simple detection example
│   ├── multi_camera_detection.py  # Multi-camera setup
│   ├── gstreamer_pipeline.py      # GStreamer integration
│   └── tensorrt_export.py         # TensorRT optimization
└── scripts/
    ├── setup_cameras.sh     # Camera setup automation
    └── test_installation.sh # Verify installation
```

## ⚡ Performance Tips

### 1. Use TensorRT Optimization

```python
from ultralytics import YOLO

# Export to TensorRT
model = YOLO('yolov8n.pt')
model.export(format='engine', device=0, half=True)

# Load TensorRT model
model = YOLO('yolov8n.engine')
```

### 2. Use Hardware-Accelerated GStreamer

Instead of `cv2.VideoCapture()`, use GStreamer pipeline for better performance:

```python
pipeline = (
    "nvarguscamerasrc sensor-id=0 ! "
    "video/x-raw(memory:NVMM),width=1280,height=720,framerate=30/1 ! "
    "nvvidconv ! video/x-raw,format=BGRx ! "
    "videoconvert ! video/x-raw,format=BGR ! appsink"
)
cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
```

### 3. Optimize Docker Runtime

Add to docker run command:
```bash
--ipc=host \
--shm-size=8g \
--ulimit memlock=-1 \
--ulimit stack=67108864
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **[dusty-nv](https://github.com/dusty-nv)** - For jetson-containers, jetson-inference, and jetson-utils
- **[JetsonHacks](https://github.com/jetsonhacks)** - For extensive Jetson tutorials and guides
- **[Arducam](https://www.arducam.com/)** - For camera hardware and drivers
- **[Ultralytics](https://github.com/ultralytics/ultralytics)** - For YOLOv8 implementation
- **NVIDIA** - For JetPack and L4T ecosystem

## 📚 Additional Resources

- [NVIDIA Jetson Developer Guide](https://developer.nvidia.com/embedded/learn/jetson-ai-certification-programs)
- [Arducam Documentation](https://docs.arducam.com/)
- [dusty-nv Jetson Containers](https://github.com/dusty-nv/jetson-containers)
- [JetsonHacks Blog](https://jetsonhacks.com/)
- [Ultralytics YOLOv8 Docs](https://docs.ultralytics.com/)

## 📞 Support

If you encounter any issues or have questions:

1. Check the [Troubleshooting Guide](docs/TROUBLESHOOTING.md)
2. Search [existing issues](https://github.com/Mertcan-Gelbal/jetson-arducam-yolo/issues)
3. Open a [new issue](https://github.com/Mertcan-Gelbal/jetson-arducam-yolo/issues/new) with detailed information

---

**Made with ❤️ for the Jetson Community**
