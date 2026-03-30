# CSI cameras — image pipeline and motorized focus

VisionDock targets **NVIDIA Jetson + Arducam (or compatible) MIPI CSI** modules. The **video path** is the same family for many Sony sensors (**IMX219**, **IMX230**, **IMX477**, **IMX519**, **IMX708**, etc.): `nvarguscamerasrc` / Argus, plus the driver you install for your exact module and L4T version.

**Motorized focus** is **not** universal: it applies only to modules that include an I2C-controlled focuser (e.g. many **IMX519** motorized kits). Fixed-lens modules (**IMX219**, **IMX477**, **IMX230** without a focus motor, etc.) do not use the focus scripts below.

---

## Sensor overview (typical use)

| Sensor | Notes |
|--------|--------|
| **IMX219** | Common 8MP module; usually **fixed focus** — no I2C focus motor in standard kits. |
| **IMX230** | Treat like other Arducam/Jetson CSI sensors: match **driver + `install_full.sh -m …`** to Arducam’s matrix for your L4T; focus only if your **specific SKU** includes a motor. |
| **IMX477** | 12MP HQ-style; often **fixed** unless you bought a **motorized** variant. |
| **IMX519** | 16MP; **motorized focus** kits are common — this repo’s `focus_imx519.py` / GUI focus UI are aimed at that I2C pattern. |
| **IMX708**, **OV9281**, **OV7251** | CSI image via the same Jetson stack; focus only if the hardware supports it. |

Always confirm **your exact Arducam SKU** (fixed vs motorized) and **L4T** match on [Arducam MIPI_Camera releases](https://github.com/ArduCAM/MIPI_Camera/releases).

---

## Bring-up order (any CSI sensor)

Keep this order: **driver + image first**, then **I2C focus** (if applicable).

| # | Step | Action |
|---|------|--------|
| 1 | Hardware | Correct CSI ribbon orientation, seated connector, module matches Jetson port (CAM0/CAM1). |
| 2 | Driver | `./install.sh --drivers` or `./scripts/setup_cameras.sh` — pick **your** model (IMX219, IMX477, IMX519, …). **Reboot** if the installer says so. |
| 3 | Image | `dmesg` / `lsmod` for your sensor name; `/usr/bin/nvgstcapture` present; **Physical** camera live in the GUI. |
| 4 | I2C (motorized only) | `i2c-tools`; `i2cdetect -y <bus>` — expect focuser address (often **0x0c** on IMX519-style kits; **yours may differ**). |
| 5 | Focus CLI (motorized only) | `python3 scripts/focus_imx519.py --bus <bus> --position 512` after verifying address/script match your module. |
| 6 | GUI | **Settings → Camera defaults** and card menu **Focus…** (same I2C scripts). |

---

## I2C bus (Jetson port)

| Platform | CAM0 | CAM1 |
|----------|------|------|
| Jetson Nano B01 | 7 | 8 |
| Xavier NX / Orin Nano | 10 | 9 |
| Orin NX | 9 | 10 |

Wrong bus is the most common failure. Use the **same** bus in Settings and in the focus script.

---

## VisionDock / GUI

- Run from repo root: `./start_gui.sh`
- **Settings → Camera defaults (CSI / Jetson):** resolution, AE lock, exposure/gain apply to the CSI path; **I2C bus**, **CSI sensor-id**, and **focus modes** matter when you have a **motorized** module compatible with the bundled scripts.
- **Focus…** on a physical card: quick position + bus (no effect on fixed-lens cameras except harmless I2C attempts if misconfigured).
- Saved settings: `~/.visiondock/camera_defaults.json`

---

## Scripts (motorized / IMX519-style I2C)

The filenames keep **`imx519`** for compatibility; they implement a **generic I2C focuser write** used on typical **IMX519 motorized** boards. Other motorized Arducam modules **may** share the same protocol — confirm with Arducam docs or `i2cdetect`.

```bash
python3 scripts/focus_imx519.py --bus 10 --position 512
python3 scripts/autofocus_imx519.py --bus 10 --sensor-id 0
```

`i2cset` usually requires **root** or **i2c** group. Edit **`0x0c`** / write pattern in `focus_imx519.py` if your focuser differs.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| No image | Driver model vs hardware, L4T, ribbon, `dmesg`, `setup_cameras.sh` choice. |
| `i2cset failed` | Module may be **fixed focus**; wrong bus; wrong **I2C address** in script. |
| Image OK, focus N/A | Expected for **IMX219 / IMX477 / many IMX230** fixed kits — disable or ignore focus UI. |
| Autofocus errors | Stop live CSI preview; correct `--sensor-id`; Jetson-only OpenCV+GStreamer path. |

Also: [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [INSTALLATION.md](INSTALLATION.md).

---

## External links

- [Arducam Jetson cameras](https://docs.arducam.com/Nvidia-Jetson-Camera/)
- [IMX519 wiki](https://docs.arducam.com/Nvidia-Jetson-Camera/Native-Camera/imx519/)
- [Motorized focus quick start](https://docs.arducam.com/Nvidia-Jetson-Camera/Motorized-Focus-Camera/quick-start/)
- [Jetson_IMX519_Focus_Example](https://github.com/ArduCAM/Jetson_IMX519_Focus_Example) (I2C reference)

---

## Legacy filename

Older links to **`IMX519_FOCUS.md`** still work: that file redirects here.
