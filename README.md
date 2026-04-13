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
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo visiondock
cd visiondock

# Run the master installer
chmod +x install.sh
./install.sh
```

### What `install.sh` Provides:
1.  **System Diagnostics:** Verifies JetPack version, CUDA availability, and hardware health.
2.  **Driver Management:** Automatically installs and configures Arducam CSI camera drivers (IMX series).
3.  **Environment Orchestration:** Builds a custom, lightweight Docker environment tailored to your specific Jetson model.
4.  **Hardware Passthrough:** Configures the system to allow high-performance access to GPU and ISP resources from within containers.

## Project Architecture

*   **`install.sh`**: The primary entry point for full-system initialization.
*   **`Dockerfile`**: Defines the optimized, hardware-accelerated AI environment.
*   **`gui/`**: Contains **VisionDock Studio**—a professional desktop interface for managing camera streams and AI workspaces.
*   **`runtime/`**: Contains the inspection runtime skeleton for Jetson-owned camera and GPIO workflows.
*   **`config/`**: Static config assets such as Jetson pin catalogs and inspection defaults.
*   **`scripts/`**: Modular utilities for camera setup, image building, and health checks.
*   **`examples/`**: Production-ready Python templates for inference and spatial analytics.

## Troubleshooting: CSI Camera Driver Installation

If the automated installer fails with **"Automatic installation failed"** or **"Could not find download link for fallback version"**, this means your Jetson's L4T (Linux for Tegra) kernel version does not have an exact match in Arducam's driver repository.

### Solution 1: Manual Driver Installation (Recommended)

1. Check your current L4T version:
   ```bash
   dpkg-query --showformat='${Version}' --show nvidia-l4t-kernel
   ```

2. Visit the [Arducam MIPI Camera Releases](https://github.com/ArduCAM/MIPI_Camera/releases) page.

3. Download the `.deb` driver package that is **closest** to your L4T version. Match the `tegra-XX.X` portion of the filename (e.g., if your version is `35.6.4`, look for a package with `35.6.x`).

4. Install manually and reboot:
   ```bash
   sudo dpkg -i <downloaded_arducam_package>.deb
   sudo reboot
   ```

### Solution 2: Reflash with a Compatible JetPack

If no compatible driver exists for your current system, you can reflash your Jetson with a JetPack version that has official Arducam support using [NVIDIA SDK Manager](https://developer.nvidia.com/sdk-manager).

### Solution 3: Contact Arducam Support

*   **Email:** support@arducam.com
*   **GitHub Issues:** [ArduCAM/MIPI_Camera](https://github.com/ArduCAM/MIPI_Camera/issues)

Include your L4T version (`dpkg-query --showformat='${Version}' --show nvidia-l4t-kernel`) and Jetson model name in your support request.

## Documentation

| Guide | Description |
|-------|-------------|
| [USAGE](docs/USAGE.md) | Usage, examples, remote Jetson from a dev PC (ZeroTier, Docker) |
| [INSTALLATION](docs/INSTALLATION.md) | Install Jetson, camera, Docker |
| [TROUBLESHOOTING](docs/TROUBLESHOOTING.md) | Troubleshooting |
| [CSI cameras & focus](docs/CSI_CAMERA_FOCUS.md) | IMX219/230/477/519 and others — pipeline, I2C focus when motorized |
| [MEDICAL SOCK CAMERA ARCHITECTURE](docs/MEDICAL_SOCK_CAMERA_ARCHITECTURE.md) | Production camera method choice for Jetson + IMX519 |
| [PRODUCTION DEPLOYMENT](docs/PRODUCTION_DEPLOYMENT.md) | Hardening and deployment instructions for factory-floor lines |

## Support
For technical support or commercial inquiries, please refer to the internal documentation in the `docs/` folder or open a GitHub issue. Developed for professional AI engineers and vision researchers.

---
**License:** MIT License.
