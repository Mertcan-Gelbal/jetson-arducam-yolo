# VisionDock: Universal Industrial AI Terminal

**VisionDock** is a professional, production-ready graphical interface and runtime framework designed to bridge AI Computer Vision models with industrial hardware. Designed explicitly for manufacturing lines, lab environments, and quality control operators, VisionDock provides a seamless, zero-code environment for deploying high-performance AI monitoring.

## 🚀 Key Features

*   **Operator-First Dashboard:** A clean, minimal GUI designed for non-technical production line operators. Track inspections, review historical logs, and monitor live cameras seamlessly.
*   **Universal Camera Support:** Ready-out-of-the-box compatibility with standard USB webcams, industrial IP streams, and high-framerate CSI device cameras.
*   **Hardware Integration:** Built-in support for physical factory triggers (foot pedals, optical sensors) and status indicator integration (Green/Red LED towers, PLCs) via native GPIO integration.
*   **Robust Environment:** Runs isolated AI stacks without cluttering the local machine, preventing downtime, and maintaining secure operator sessions.

## 🖥 Starting the Operator Dashboard

On your monitoring PC or industrial tablet (Windows, macOS, or Linux), you do not need to install heavy backend AI libraries. Just launch the operator dashboard using the provided startup script.

It will automatically initialize its own secure environment and prepare the application:

```bash
chmod +x start_gui.sh
./start_gui.sh
```

*(Advanced setup: You can manually run `python3 -m pip install -r gui/requirements.txt` followed by `python3 gui/main.py`)*

## ✅ Operator Flow (Product View)

For day-to-day production usage, the expected user journey is:

1. **Open `Settings`** and confirm runtime endpoint, role mode, and automation profile.
2. **Open `Inspection`** and verify camera preview/runtime health before triggering.
3. **Open `Results`** for decision review and historical traceability.

This keeps operator actions focused while advanced commissioning controls remain gated under Engineering mode.

## 🏭 System Architecture

The VisionDock platform is modular to ensure high availability on the factory floor:

*   **`start_gui.sh`**: One-click startup script for the desktop operator panel.
*   **`gui/`**: Contains the **VisionDock Studio**—the streamlined industrial frontend.
*   **`runtime/`**: Contains the inspection services that interface with cameras and GPIO modules.
*   **`install.sh`**: The baseline setup script required for configuring local camera drivers on initial deployment targets.
*   **`Dockerfile`**: Defines the highly optimized, containerized engine for deep learning.

## 📚 Administrator Documentation

For system integrators and IT departments setting up the underlying frameworks, refer to our technical configuration guides safely stored in the `docs/` folder:

| Documentation | Description |
|-------|-------------|
| [PRODUCTION DEPLOYMENT](docs/PRODUCTION_DEPLOYMENT.md) | Deployment, scaling, and operational best practices for factory floors |
| [CAMERA MODULE GUIDANCE](docs/CAMERA_MODULE_GUIDANCE.md) | Sensor-family vs module vs lens decisions for IMX219 / IMX477 / IMX519 systems |
| [HARDWARE & FOCUS](docs/CSI_CAMERA_FOCUS.md) | Calibration instructions for autofocus and motorized lenses |
| [TROUBLESHOOTING](docs/TROUBLESHOOTING.md) | Resolution steps for backend network and driver anomalies |
| [INSTALLATION](docs/INSTALLATION.md) | Initial setup of Deep Learning drivers for Jetson/Edge targets |

## 🛠 Support & Licensing

For technical assistance or system integration consulting, please consult the internal documentation or contact your designated IT support administrator. VisionDock is built for stability, scale, and high-throughput vision monitoring.

---
**License:** Proprietary / Commercial Standard (MIT)
