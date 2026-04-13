"""Shared CSI camera metadata for GUI and runtime."""

import glob
import subprocess

CSI_SENSOR_PROFILES = {
    "GENERIC_CSI": {
        "label": "Generic CSI",
        "note": "Use this when your exact CSI module is not listed. Sensor family alone does not define focus, zoom, or iris capability.",
        "recommended_capture": {"width": 1920, "height": 1080, "fps": 30},
        "default_resolution_index": 2,
        "default_focuser": "none",
    },
    "IMX219": {
        "label": "IMX219 (8MP)",
        "note": "8MP sensor family used in fixed-focus, manual-focus, motorized-focus, and PTZ kits. Do not infer optics controls from the sensor name alone.",
        "recommended_capture": {"width": 1920, "height": 1080, "fps": 30},
        "default_resolution_index": 2,
        "default_focuser": "none",
    },
    "IMX477": {
        "label": "IMX477 (12MP)",
        "note": "12MP sensor family often paired with interchangeable M12/CS/C lenses. Focus and aperture are lens-side decisions; some motorized/PTZ variants also exist.",
        "recommended_capture": {"width": 1920, "height": 1080, "fps": 30},
        "default_resolution_index": 2,
        "default_focuser": "none",
    },
    "IMX519": {
        "label": "IMX519 (16MP)",
        "note": "16MP sensor family with many autofocus stock-lens modules, but not every SKU exposes the same focus actuator. Zoom and iris are still module/lens dependent.",
        "recommended_capture": {"width": 1920, "height": 1080, "fps": 30},
        "default_resolution_index": 2,
        "default_focuser": "none",
    },
}


FOCUSER_PROFILES = {
    "none": {
        "label": "No focus motor",
        "note": "Use this for fixed-lens CSI modules. The focus scripts and I2C bus controls stay disabled.",
        "scripts": {},
    },
    "imx519_style": {
        "label": "Motorized focus (IMX519-style I2C)",
        "note": "Uses the bundled IMX519-style I2C focus scripts. Confirm your module matches the same focuser protocol before enabling it.",
        "scripts": {
            "focus": "focus_imx519.py",
            "autofocus": "autofocus_imx519.py",
        },
    },
}


def csi_sensor_profiles():
    return CSI_SENSOR_PROFILES


def csi_sensor_profile(sensor_model: str):
    key = str(sensor_model or "GENERIC_CSI").strip().upper()
    return CSI_SENSOR_PROFILES.get(key, CSI_SENSOR_PROFILES["GENERIC_CSI"])


def csi_sensor_label(sensor_model: str) -> str:
    return str(csi_sensor_profile(sensor_model).get("label") or "Generic CSI")


def csi_sensor_note(sensor_model: str) -> str:
    return str(csi_sensor_profile(sensor_model).get("note") or "")


def csi_sensor_recommended_capture(sensor_model: str):
    rec = dict(csi_sensor_profile(sensor_model).get("recommended_capture") or {})
    rec.setdefault("width", 1920)
    rec.setdefault("height", 1080)
    rec.setdefault("fps", 30)
    return rec


def csi_sensor_default_resolution_index(sensor_model: str) -> int:
    try:
        return int(csi_sensor_profile(sensor_model).get("default_resolution_index") or 2)
    except (TypeError, ValueError):
        return 2


def csi_sensor_default_focuser(sensor_model: str) -> str:
    return str(csi_sensor_profile(sensor_model).get("default_focuser") or "none")


def focuser_profiles():
    return FOCUSER_PROFILES


def focuser_profile(focuser_type: str):
    key = str(focuser_type or "none").strip().lower()
    return FOCUSER_PROFILES.get(key, FOCUSER_PROFILES["none"])


def focuser_label(focuser_type: str) -> str:
    return str(focuser_profile(focuser_type).get("label") or "No focus motor")


def focuser_note(focuser_type: str) -> str:
    return str(focuser_profile(focuser_type).get("note") or "")


def focuser_available(focuser_type: str) -> bool:
    return str(focuser_type or "none").strip().lower() != "none"


def focus_scripts_for_focuser(focuser_type: str):
    return dict(focuser_profile(focuser_type).get("scripts") or {})


def software_control_capabilities(focuser_type: str):
    has_focus = focuser_available(focuser_type)
    return {
        "focus": has_focus,
        "zoom": False,
        "aperture": False,
    }


def detect_sensor_model_from_text(text: str) -> str:
    raw = str(text or "").upper()
    for sensor in ("IMX519", "IMX477", "IMX219"):
        if sensor in raw:
            return sensor
    return ""


def detect_local_jetson_sensor_model() -> str:
    probes = []
    for path in glob.glob("/sys/class/video4linux/video*/name"):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                probes.append(handle.read().strip())
        except OSError:
            continue
    commands = [
        ["v4l2-ctl", "--list-devices"],
        ["dmesg"],
    ]
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if output:
            probes.append(output)
    for probe in probes:
        detected = detect_sensor_model_from_text(probe)
        if detected:
            return detected
    return ""
