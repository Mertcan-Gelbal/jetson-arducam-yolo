# Jetson Arducam AI Kit

A flexible, production-ready environment for running modern Computer Vision models (YOLOv8, YOLOv11, RT-DETR, EfficientNet, etc.) with Arducam hardware on NVIDIA Jetson devices.

## Features

*   **Universal Model Support:** Run any model supported by Ultralytics and PyTorch.
*   **Dynamic Platform:** Automatically adapts base images for JetPack 5.x or 6.x systems.
*   **Dual Camera Support:** Full support for both **CSI/MIPI** (Arducam IMX series) and **USB Webcams**.
*   **Hardware Acceleration:** Optimized GStreamer pipelines for CSI cameras and V4L2 for USB devices.
*   **TensorRT Ready:** Native tools to convert models to TensorRT for maximum performance.

## Project Structure

```text
jetson-arducam-ai/
├── install.sh                  # Modular master installer
├── Dockerfile                  # Self-configuring AI environment
├── scripts/
│   ├── setup_cameras.sh        # Camera driver setup wizard
│   ├── build_docker.sh         # Dynamic image builder
│   ├── run_docker.sh           # Hardware-passthrough launcher
│   ├── test_installation.sh    # System health diagnostics
│   └── download_models.sh      # Pre-download popular weights
├── examples/                   # Production-ready Python examples
└── docs/                       # Comprehensive guides
```

## Installation

### Recommended Automated Setup
The modular installer orchestrates the entire workflow. You can run all steps at once or trigger specific modules.

```bash
# Full sequence (Recommended for first timers)
./install.sh --all

# Modular options
./install.sh --drivers   # Only camera setup
./install.sh --build     # Rebuild AI environment
./install.sh --run       # Start the container
```

## Usage

### Basic Inference
Once the container is running, use our optimized wrappers inside the shell:

```bash
# Enter container
sudo docker exec -it jetson-arducam-ctr bash

# CSI Camera (Default Arducam Setup)
python3 examples/basic_detection.py --model yolo11n.pt --source-type csi

# USB Camera
python3 examples/basic_detection.py --model yolo11n.pt --source-type usb --camera 0
```

### Expected Performance (Reference)
Performance measured on Orin Nano (8GB) with TensorRT optimization:

| Model | Framework | Resolution | FPS (Avg) |
| :--- | :--- | :--- | :--- |
| YOLO11n | TensorRT | 640x640 | 45-60 |
| YOLOv8n | TensorRT | 640x640 | 55-70 |
| YOLO11s | TensorRT | 640x640 | 25-35 |
| YOLOv8m | PyTorch | 640x640 | 8-12 |

## Performance Tuning
To unlock maximum power on your Jetson device:

1.  **System Clocks:** `sudo nvpmodel -m 0 && sudo jetson_clocks`
2.  **Optimization:** Use `./examples/tensorrt_export.py` to convert PyTorch models to TensorRT.

## Support
Detailed documentation is available in the `docs/` folder. Please open a GitHub issue for technical bugs or hardware compatibility questions.

## License
MIT License.
