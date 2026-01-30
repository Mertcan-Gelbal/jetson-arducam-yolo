# Jetson Arducam AI Kit

A flexible, production-ready environment for running modern Computer Vision models (YOLOv8, YOLOv11, RT-DETR, EfficientNet, etc.) with Arducam hardware on NVIDIA Jetson.

## 🚀 Features

*   **Universal Model Support:** Run any model supported by Ultralytics and PyTorch (Object Detection, Segmentation, Pose Estimation, Classification).
*   **Dynamic Platform:** Automatically adapts to JetPack 5.x or 6.x.
*   **Dual Camera Support:** Full support for both **CSI/MIPI** (Arducam IMX series) and **USB Webcams** (Logitech, Intel Realsense).
*   **Hardware Acceleration:** Optimized GStreamer pipelines for CSI and V4L2 for USB.
*   **TensorRT Ready:** Tools to convert any supported model to TensorRT.

## 📂 Project Structure

```text
jetson-arducam-ai/
├── Dockerfile                  # Generic AI environment (PyTorch + Ultralytics)
├── scripts/
│   ├── build_docker.sh         # Builds 'jetson-arducam' image
│   ├── run_docker.sh           # Runs 'jetson-arducam-ctr' container
│   ├── setup_cameras.sh        # Universal camera driver installer
│   └── test_installation.sh    # System diagnostics
├── examples/
│   ├── basic_detection.py      # Inference demo (supports all YOLO versions)
│   ├── multi_camera_detection.py # Multi-stream threading example
│   ├── gstreamer_pipeline.py   # Low-latency ISP pipeline
│   └── tensorrt_export.py      # Model optimizer
└── docs/                       # Guides for Installation, Usage & Troubleshooting
```

## 🛠️ Installation

### Automated Setup (Recommended)
The master installer orchestrates driver verification, system checks, and Docker builds.

```bash
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo.git
cd jetson-arducam-yolo
./install.sh
```

### Manual Individual Steps
If you prefer manual control:
1.  **Drivers:** `./scripts/setup_cameras.sh`
2.  **Verify:** `./scripts/test_installation.sh`
3.  **Build:** `./scripts/build_docker.sh`
4.  **Run:** `./scripts/run_docker.sh`

## 🧠 Usage

The environment supports the entire Ultralytics ecosystem. You can swap models easily.

### Running Different Models

```bash
# Enter container
sudo docker exec -it jetson-arducam-ctr bash

# YOLOv8 Nano (Fastest)
python3 examples/basic_detection.py --model yolov8n.pt

# YOLOv8 Medium (Better Accuracy)
python3 examples/basic_detection.py --model yolov8m.pt

# YOLOv11 (New SOTA)
python3 examples/basic_detection.py --model yolo11n.pt

# RT-DETR (Transformer)
python3 examples/basic_detection.py --model rtdetr-l.pt
```

### Multi-Camera (Stereo/Array)
Support for synchronized processing of multiple camera streams:
```bash
python3 examples/multi_camera_detection.py --cameras 0 1
```

## ⚡ Performance

To unlock full performance on Jetson:

1.  **Maximize Clocks:** `sudo nvpmodel -m 0 && sudo jetson_clocks`
2.  **Use TensorRT:**
    ```bash
    # Convert any model
    python3 examples/tensorrt_export.py --model yolo11n.pt --export
    
    # Run optimized model
    python3 examples/basic_detection.py --model yolo11n.engine
    ```

## 🤝 Support
Open an issue for bugs or feature requests. See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for help.

## License
MIT License.
