# Jetson Arducam AI Kit

A flexible, production-ready environment for running modern Computer Vision models (YOLOv8, YOLOv11, RT-DETR, EfficientNet, etc.) with Arducam hardware on NVIDIA Jetson devices.

## Features

*   **Universal Model Support:** Run any model supported by Ultralytics and PyTorch (Object Detection, Segmentation, Pose Estimation, Classification).
*   **Dynamic Platform:** Automatically adapts base images for JetPack 5.x or 6.x systems.
*   **Dual Camera Support:** Full support for both **CSI/MIPI** (Arducam IMX series) and **USB Webcams** (Logitech, Intel Realsense, etc.).
*   **Hardware Acceleration:** Optimized GStreamer pipelines for CSI cameras and V4L2 for USB devices.
*   **TensorRT Ready:** Included tools to convert any supported model to TensorRT for maximum inference speed.

## Project Structure

```text
jetson-arducam-ai/
├── install.sh                  # Master installer (Drivers -> System Check -> Docker Build)
├── Dockerfile                  # AI environment definition (Auto-updates to match system)
├── scripts/
│   ├── setup_cameras.sh        # Camera driver wizard (CSI & USB)
│   ├── build_docker.sh         # Smart Docker builder
│   ├── run_docker.sh           # Container launcher with hardware access
│   └── test_installation.sh    # System verification tool
├── examples/
│   ├── basic_detection.py      # Main inference script (supports USB & CSI)
│   ├── multi_camera_detection.py # Multi-stream threading example
│   └── tensorrt_export.py      # Model optimization tool
└── docs/                       # Detailed documentation
```

## Installation

### Automated Setup (Recommended)
The master installer orchestrates driver verification, system compatibility checks, and Docker image creation in a single workflow.

```bash
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo.git
cd jetson-arducam-yolo
./install.sh
```

### Manual Steps
If you prefer granular control over the installation process:

1.  **Drivers:** `./scripts/setup_cameras.sh` - Installs camera drivers or verifies USB connection.
2.  **Verify:** `./scripts/test_installation.sh` - Checks system health and requirements.
3.  **Build:** `./scripts/build_docker.sh` - Creates the Docker environment specific to your JetPack version.
4.  **Run:** `./scripts/run_docker.sh` - Starts the container with hardware passthrough.

## Usage

The environment accepts standard Ultralytics commands and our custom Python wrappers.

### Basic Inference

```bash
# 1. Enter the container
sudo docker exec -it jetson-arducam-ctr bash

# 2. Run Object Detection (CSI Camera - Default)
python3 examples/basic_detection.py --model yolov8n.pt

# 3. Run Object Detection (USB Webcam)
python3 examples/basic_detection.py --model yolov8n.pt --source-type usb --camera 0
```

### Advanced Models

You can easily swap models to test accuracy vs. speed trade-offs:

```bash
# YOLOv11 (New SOTA)
python3 examples/basic_detection.py --model yolo11n.pt

# RT-DETR (Transformer-based)
python3 examples/basic_detection.py --model rtdetr-l.pt
```

## Performance Optimization

To achieve real-time performance on Jetson edge devices:

1.  **Maximize System Clocks:**
    ```bash
    sudo nvpmodel -m 0
    sudo jetson_clocks
    ```

2.  **Use TensorRT Acceleration:**
    Convert PyTorch models to TensorRT engines for 2-3x faster inference.
    ```bash
    # Convert
    python3 examples/tensorrt_export.py --model yolo11n.pt --export
    
    # Run
    python3 examples/basic_detection.py --model yolo11n.engine
    ```

## Support

For detailed guides and troubleshooting, refer to the `docs/` directory:
*   [Installation Guide](docs/INSTALLATION.md)
*   [Troubleshooting Guide](docs/TROUBLESHOOTING.md)
*   [Check GitHub Issues](https://github.com/Mertcan-Gelbal/jetson-arducam-yolo/issues)

## License
MIT License.
