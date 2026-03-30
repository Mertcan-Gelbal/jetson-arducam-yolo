# Development and update workflow

This guide summarizes **what each part of the repo does** and **where to make typical edits**. For private notes, use `dev-notes/` (not tracked by git).

---

## Repository layout

| Path | Purpose |
|------|---------|
| `install.sh` | Jetson install flow: `--drivers`, `--verify`, `--build`, or full run |
| `scripts/` | Camera setup (multiple sensors), Docker helpers, `focus_imx519.py`, `autofocus_imx519.py`, tests |
| `gui/main.py` | VisionDock Studio (**PySide6** / Qt 6): camera cards, Docker, Settings, remote host |
| `gui/requirements.txt` | **PySide6**, psutil, OpenCV headless, numpy |
| `examples/` | Detection, GStreamer, TensorRT samples |
| `docs/` | User and installation documentation |
| `docker-compose.yml` | `jetson-ai` service, container name `jetson-arducam-ctr` |

---

## Common change targets

| Topic | Where to look |
|-------|----------------|
| CSI motorized focus, I2C bus, camera defaults | `gui/main.py` (`get_camera_defaults`, `set_camera_defaults`, Settings “Camera defaults”), `scripts/focus_imx519.py` (IMX519-style focuser) |
| Remote Jetson (Docker TCP 2375) | `gui/main.py` (`DockerManager`, `check_remote_node_reachable`, `app_prefs.json`) |
| Docs / usage text | `docs/*.md` and the documentation table in `README.md` if needed |
| Drivers / install | `scripts/setup_cameras.sh`, `docs/INSTALLATION.md` |

---

## Local app data (`~/.visiondock/`)

| File | Contents |
|------|----------|
| `camera_defaults.json` | Resolution, AE lock, focus mode, I2C bus, sensor-id, etc. |
| `app_prefs.json` | `remote_host_ip` — remote Docker host from Settings (optional) |

If **`JETSON_REMOTE`** is set in the environment, it **wins** over the file for the initial remote IP for that session.

---

## GUI toolkit: PySide6 (Qt 6)

| Topic | Notes |
|-------|--------|
| **Why PySide6** | Official Qt for Python, **LGPL**, tracks **Qt 6** (modern rendering, HiDPI, style). Visually similar apps can look sharper with updated stylesheet + Qt6 fusion/default fusion improvements. |
| **vs PyQt5** | PyQt5 is Qt 5 and GPL/commercial Riverbank license. Migration updates enums (`Qt.AlignmentFlag`, `QMessageBox.StandardButton`, …), `Signal`/`Property`, `exec()` instead of `exec_()`, `globalPosition().toPoint()` on mouse events. |
| **Jetson** | Install GUI deps with **pip** from `gui/requirements.txt` (PySide6 provides manylinux/aarch64 wheels on supported pip platforms). If a board lacks a wheel, use Arducam/Jetson forum or pin an older PySide6. |

## Running the GUI

- **All platforms:** `./start_gui.sh` (installs **PySide6** via pip if missing) or `python3 -m pip install -r gui/requirements.txt` then `python3 gui/main.py`
- Headless CI / smoke: `QT_QPA_PLATFORM=offscreen python3 -c "import sys; sys.path.insert(0,'gui'); import main"`

Live CSI preview requires **Jetson + Linux**; on a dev laptop you usually use the **remote Docker** workflow only.

---

## Release notes

Add user-facing bullets under **`[Unreleased]`** in the root **`CHANGELOG.md`**. When you cut a release, rename the section (e.g. `## [1.0.0] - 2026-03-28`).

---

## Workflow readiness checklist

The update/development flow is **ready to use** when these are in place:

- [x] Remote Docker IP persisted in `app_prefs.json`; optional `JETSON_REMOTE` override.
- [x] `start_gui.sh` handles dependencies on Linux/Jetson vs macOS.
- [x] `docs/DEVELOPMENT.md` documents where to edit.
- [x] `CHANGELOG.md` template with `[Unreleased]` entries.
- [x] `dev-notes/` for private notes; `dev-notes/README.md` explains usage.
- [x] `README.md` links DEVELOPMENT, CHANGELOG, and dev-notes.

After each meaningful change: update **`CHANGELOG.md`** → `[Unreleased]`.
