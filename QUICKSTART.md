# Quick Start

## Prerequisites

*   NVIDIA Jetson device (Orin Nano/NX/AGX)
*   JetPack 5.x or 6.x
*   Camera (CSI or USB)
*   Internet connection

## ⚡ 1-Click Installation

We provide a master setup script that handles drivers, system verification, and Docker environment setup automatically.

```bash
# 1. Clone
git clone https://github.com/Mertcan-Gelbal/jetson-arducam-yolo.git
cd jetson-arducam-yolo

# 2. Run Installer
./install.sh
```

Follow the on-screen instructions. The script will:
1.  Setup Camera Drivers (CSI/USB)
2.  Verify System Requirements
3.  Build AI Environment (Docker)
4.  Launch the Container

---

## Manual Run (After Installation)

Once installed, you can easily start the system anytime:

```bash
# Start Container
./scripts/run_docker.sh

# Run Detection (Inside Container)
sudo docker exec -it jetson-arducam-ctr bash
python3 examples/basic_detection.py --source-type csi
```

## Performance

To enable maximum performance on the host:
```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```
