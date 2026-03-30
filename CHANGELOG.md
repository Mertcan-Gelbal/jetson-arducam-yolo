# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Persist VisionDock **Settings → Remote host (IP)** to `~/.visiondock/app_prefs.json`; optional env **`JETSON_REMOTE`** overrides the saved value on startup.
- **`dev-notes/`** for local scratch notes (git-ignored except `dev-notes/README.md`).
- **`docs/DEVELOPMENT.md`** — repository layout and where to edit for common tasks.
- **`docs/CSI_CAMERA_FOCUS.md`** — CSI sensors (IMX219, IMX230, IMX477, IMX519, …), image vs motorized focus.

### Changed

- **GUI migrated to PySide6 (Qt 6):** `gui/main.py` uses `Signal`/`Property`, Qt 6 enums, `exec()`, updated global stylesheet (sliders, spin boxes, preview, nav gradient). **`start_gui.sh`** installs deps via **`gui/requirements.txt`** (pip PySide6 on Linux/macOS).
- **Documentation:** `docs/CSI_CAMERA_FOCUS.md` covers multiple CSI sensors (IMX219, IMX230, IMX477, IMX519, …); `IMX519_FOCUS.md` redirects. GUI labels generalized beyond IMX519-only wording.
- **`scripts/setup_cameras.sh`:** optional **Other** menu entry for custom Arducam module id (e.g. `imx230`).
- **`scripts/test_installation.sh`:** CSI detection checks several `imx*` / `ov*` patterns instead of IMX519 only.

---

## Earlier history

Changes before this file was introduced are not listed here; use `git log`.
