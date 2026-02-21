# VisionDock: Jetson AI Ecosystem

VisionDock is a professional, production-ready suite designed to bridge modern Computer Vision models with NVIDIA Jetson hardware. It provides a seamless, automated environment for deploying high-performance AI applications with specialized support for Arducam CSI and USB imaging systems.

## Key Capabilities

*   **Automated Environment:** Intelligent detection of JetPack 5.x/6.x to configure the optimal AI stack.
*   **Hardware-Accelerated Imaging:** Native integration with DeepStream-compatible GStreamer pipelines for zero-latency CSI and USB video feeds.
*   **Universal Model Support:** Out-of-the-box compatibility with YOLOv8, YOLOv11, and RT-DETR via Ultralytics and PyTorch.
*   **Production Architecture:** Containerized deployment using Docker and Docker Compose with full hardware passthrough (CUDA, TensorRT, NVENC).

## Quick Start (Automated Installer)

The core of the VisionDock deployment is the `install.sh` script. This modular installer orchestrates the entire system setup, ensuring all hardware drivers and software dependencies are perfectly aligned.

```bash
# Clone the repository
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo
cd jetson-arducam-yolo

# Run the master installer
chmod +x install.sh
./install.sh --all
```

### What `install.sh` Provides:
1.  **System Diagnostics:** Verifies JetPack version, CUDA availability, and hardware health.
2.  **Driver Management:** Automatically installs and configures Arducam CSI camera drivers (IMX series).
3.  **Environment Orchestration:** Builds a custom, lightweight Docker environment tailored to your specific Jetson model.
4.  **Hardware Passthrough:** Configures the system to allow high-performance access to GPU and ISP resources from within containers.

## Project Architecture

*   **`install.sh`**: The primary entry point for full-system initialization.
*   **`Dockerfile`**: Defines the optimized, hardware-accelerated AI environment.
*   **`gui/`**: Contains the **VisionDock Studio**—a professional desktop interface for managingカメラ streams and AI workspaces.
*   **`scripts/`**: Modular utilities for camera setup, image building, and health checks.
*   **`examples/`**: Production-ready Python templates for inference and spatial analytics.

## Support
For technical support or commercial inquiries, please refer to the internal documentation in the `docs/` folder or open a GitHub issue. Developed for professional AI engineers and vision researchers.

---
**License:** MIT License.
