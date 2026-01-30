# Jetson Arducam YOLOv8

Production-ready Docker environment for running YOLOv8 object detection with Arducam multi-camera arrays on NVIDIA Jetson devices.

## Features

*   **Dynamic JetPack Support:** Automatically detects L4T version (JetPack 5.x/6.x) and builds the appropriate container.
*   **Multi-Camera:** Threaded support for Arducam IMX519 stereo and multi-camera arrays.
*   **Hardware Acceleration:** GStreamer pipelines optimized for NVIDIA Jetson ISP and NVDEC.
*   **TensorRT:** Examples for converting YOLOv8 models to TensorRT for 2-3x performance boost.
*   **Production Ready:** Docker container configured with auto-restart, privileged access, and volume mounts.

## Project Structure

```text
jetson-arducam-yolo/
├── Dockerfile                  # Dynamic base image selection
├── scripts/
│   ├── build_docker.sh         # Builds image based on system L4T version
│   ├── run_docker.sh           # Runs container with device mounts
│   ├── setup_cameras.sh        # Installs Arducam drivers
│   └── test_installation.sh    # Verifies system status
├── examples/
│   ├── basic_detection.py      # Basic YOLOv8 inference
│   ├── multi_camera_detection.py # Multi-threaded multi-camera example
│   ├── gstreamer_pipeline.py   # Optimized GStreamer pipeline
│   └── tensorrt_export.py      # TensorRT conversion tool
└── docs/                       # Detailed documentation
```

## Installation

### 1. Requirements

*   NVIDIA Jetson (Orin Nano, Orin NX, AGX Orin)
*   JetPack 5.1.1+ (L4T R35.4.1+)
*   Docker with NVIDIA Runtime

### 2. Quick Setup

```bash
# Clone
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo.git
cd jetson-arducam-yolo

# Install Drivers
./scripts/setup_cameras.sh

# Build (Auto-detects JetPack version)
./scripts/build_docker.sh

# Run
./scripts/run_docker.sh
```

## Usage

### Basic Detection

```bash
sudo docker exec -it jetson-yolo-ctr python3 examples/basic_detection.py --camera 0
```

### Multi-Camera (Stereo/Quad)

```bash
sudo docker exec -it jetson-yolo-ctr python3 examples/multi_camera_detection.py --cameras 0 1
```

### TensorRT Optimization

Using TensorRT significantly improves FPS.

1.  **Export Model:**
    ```bash
    python3 examples/tensorrt_export.py --model yolov8n.pt --export
    ```

2.  **Run with Engine:**
    ```bash
    python3 examples/basic_detection.py --model yolov8n.engine
    ```

## Troubleshooting

*   **Camera not detected:** Ensure `setup_cameras.sh` was run and system rebooted. Check `ls /dev/video*`.
*   **Permissions:** Use `sudo` or add user to docker group.
*   **Performance:** Run `sudo nvpmodel -m 0` and `sudo jetson_clocks` on host.

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for detailed solutions.

## License

MIT License. See [LICENSE](LICENSE) file.
