from typing import Any, Callable, Dict

_ALLOWED_OUTPUT_BACKENDS = {"mock", "jetson_gpio"}
_ALLOWED_TRIGGER_MODES = {"manual", "input_pin", "continuous"}
_ALLOWED_CAMERA_BACKENDS = {
    "jetson_csi_argus",
    "opencv_device",
    "image_file",
    "video_file",
    "mock_frame",
}
_ALLOWED_GPIO_ACTIVE_LEVELS = {"high", "low"}


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _as_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return bool(default)
    return bool(v)


def _as_text(v: Any, default: str = "") -> str:
    s = str(v or "").strip()
    return s if s else default


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    x = _as_int(v, default)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _clamp_float(v: Any, lo: float, hi: float, default: float) -> float:
    x = _as_float(v, default)
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def validate_profile(profile: Dict[str, Any]) -> tuple[Dict[str, Any], list[str]]:
    """Clamp/sanitize profile to keep runtime and UI resilient.

    Returns (sanitized_profile, warnings). Warnings are user-friendly and non-fatal.
    """
    p = dict(profile or {})
    warnings: list[str] = []
    runtime_cfg = dict(p.get("runtime") or {})
    camera_cfg = dict(p.get("camera") or {})
    inspection_cfg = dict(p.get("inspection") or {})
    gpio_cfg = dict(p.get("gpio") or {})
    roi_cfg = dict(inspection_cfg.get("roi") or {})

    p["camera_name"] = _as_text(p.get("camera_name"), "VisionDock Inspection Station")
    p["board_model"] = _as_text(p.get("board_model"), "JETSON_ORIN_NANO")

    out_backend = _as_text(p.get("output_backend"), "mock")
    if out_backend not in _ALLOWED_OUTPUT_BACKENDS:
        warnings.append("Output backend was reset to the default value.")
        out_backend = "mock"
    p["output_backend"] = out_backend

    trigger_mode = _as_text(p.get("trigger_mode"), "manual")
    if trigger_mode not in _ALLOWED_TRIGGER_MODES:
        warnings.append("Trigger mode was reset to the default value.")
        trigger_mode = "manual"
    p["trigger_mode"] = trigger_mode

    host = _as_text(runtime_cfg.get("host"), "127.0.0.1")
    port = _clamp_int(runtime_cfg.get("port"), 1024, 65535, 8787)
    runtime_cfg["host"] = host
    runtime_cfg["port"] = port
    p["runtime"] = runtime_cfg

    cam_backend = _as_text(camera_cfg.get("backend"), "mock_frame")
    if cam_backend not in _ALLOWED_CAMERA_BACKENDS:
        warnings.append("Camera backend was reset to the default value.")
        cam_backend = "mock_frame"
    sensor_model = _as_text(camera_cfg.get("sensor_model"), "GENERIC_CSI").upper()
    focuser_type = _as_text(camera_cfg.get("focuser_type"), "none").lower()
    camera_cfg["backend"] = cam_backend
    camera_cfg["sensor_model"] = sensor_model
    camera_cfg["focuser_type"] = focuser_type
    camera_cfg["sensor_id"] = _clamp_int(camera_cfg.get("sensor_id"), 0, 7, 0)
    camera_cfg["device_index"] = _clamp_int(camera_cfg.get("device_index"), 0, 15, 0)
    camera_cfg["capture_width"] = _clamp_int(camera_cfg.get("capture_width"), 320, 7680, 1920)
    camera_cfg["capture_height"] = _clamp_int(camera_cfg.get("capture_height"), 240, 4320, 1080)
    camera_cfg["display_width"] = _clamp_int(
        camera_cfg.get("display_width", camera_cfg.get("capture_width")), 320, 7680, 1920
    )
    camera_cfg["display_height"] = _clamp_int(
        camera_cfg.get("display_height", camera_cfg.get("capture_height")), 240, 4320, 1080
    )
    camera_cfg["framerate"] = _clamp_int(camera_cfg.get("framerate"), 1, 120, 30)
    camera_cfg["burst_count"] = _clamp_int(camera_cfg.get("burst_count"), 1, 9, 3)
    camera_cfg["source_path"] = _as_text(camera_cfg.get("source_path"), "")
    p["camera"] = camera_cfg

    pass_t = _clamp_float(inspection_cfg.get("pass_threshold"), 0.0, 1.0, 0.35)
    fail_t = _clamp_float(inspection_cfg.get("fail_threshold"), 0.0, 1.0, 0.65)
    hard_t = _clamp_float(inspection_cfg.get("hard_fail_threshold"), 0.0, 1.0, 0.9)
    if fail_t < pass_t:
        warnings.append("FAIL threshold was raised to match PASS threshold.")
        fail_t = pass_t
    if hard_t < fail_t:
        warnings.append("Hard-fail threshold was raised to match FAIL threshold.")
        hard_t = fail_t

    roi_cfg["enabled"] = _as_bool(roi_cfg.get("enabled"), False)
    roi_cfg["x"] = _clamp_float(roi_cfg.get("x"), 0.0, 1.0, 0.0)
    roi_cfg["y"] = _clamp_float(roi_cfg.get("y"), 0.0, 1.0, 0.0)
    roi_cfg["width"] = _clamp_float(roi_cfg.get("width"), 0.0, 1.0, 1.0)
    roi_cfg["height"] = _clamp_float(roi_cfg.get("height"), 0.0, 1.0, 1.0)

    defects = [str(x).strip() for x in (inspection_cfg.get("defect_catalog") or []) if str(x).strip()]
    # Stable dedupe, keep first 32 labels.
    dd = []
    seen = set()
    for d in defects:
        if d in seen:
            continue
        seen.add(d)
        dd.append(d)
        if len(dd) >= 32:
            break

    inspection_cfg["profile_name"] = _as_text(inspection_cfg.get("profile_name"), "default_project")
    inspection_cfg["recipe_name"] = _as_text(inspection_cfg.get("recipe_name"), "default_recipe")
    inspection_cfg["product_label"] = _as_text(inspection_cfg.get("product_label"), "Inspection target")
    inspection_cfg["expected_object_label"] = _as_text(
        inspection_cfg.get("expected_object_label"), "target"
    )
    inspection_cfg["pass_display_label"] = _as_text(inspection_cfg.get("pass_display_label"), "Pass")
    inspection_cfg["fail_display_label"] = _as_text(inspection_cfg.get("fail_display_label"), "Fail")
    inspection_cfg["defect_catalog"] = dd
    inspection_cfg["frame_vote_count"] = _clamp_int(
        inspection_cfg.get("frame_vote_count"), 1, 9, _as_int(camera_cfg.get("burst_count"), 3)
    )
    inspection_cfg["pass_threshold"] = pass_t
    inspection_cfg["fail_threshold"] = fail_t
    inspection_cfg["hard_fail_threshold"] = hard_t
    inspection_cfg["save_fail_frames"] = _as_bool(inspection_cfg.get("save_fail_frames"), True)
    inspection_cfg["save_uncertain_frames"] = _as_bool(inspection_cfg.get("save_uncertain_frames"), True)
    inspection_cfg["roi"] = roi_cfg
    p["inspection"] = inspection_cfg

    active = _as_text(gpio_cfg.get("active_level"), "high")
    if active not in _ALLOWED_GPIO_ACTIVE_LEVELS:
        warnings.append("GPIO active level was reset to the default value.")
        active = "high"
    gpio_cfg["enabled"] = _as_bool(gpio_cfg.get("enabled"), False)
    gpio_cfg["active_level"] = active
    p["gpio"] = gpio_cfg

    return p, warnings


def normalize_loaded_profile(
    profile: Dict[str, Any],
    runtime_host_fallback: str,
    default_focuser_for_sensor: Callable[[Any], str],
) -> Dict[str, Any]:
    p = dict(profile or {})
    runtime_cfg = dict(p.get("runtime") or {})
    camera_cfg = dict(p.get("camera") or {})
    inspection_cfg = dict(p.get("inspection") or {})
    gpio_cfg = dict(p.get("gpio") or {})
    roi_cfg = dict(inspection_cfg.get("roi") or {})

    sensor_model = _as_text(camera_cfg.get("sensor_model"), "GENERIC_CSI").upper()
    focuser = _as_text(
        camera_cfg.get("focuser_type"),
        default_focuser_for_sensor(sensor_model),
    ).lower()

    normalized = {
        "camera_name": _as_text(p.get("camera_name"), ""),
        "board_model": _as_text(p.get("board_model"), ""),
        "output_backend": _as_text(p.get("output_backend"), "mock"),
        "trigger_mode": _as_text(p.get("trigger_mode"), "manual"),
        "runtime_host": _as_text(runtime_cfg.get("host"), runtime_host_fallback or "127.0.0.1"),
        "runtime_port": _as_int(runtime_cfg.get("port"), 8787),
        "camera": {
            "backend": _as_text(camera_cfg.get("backend"), "mock_frame"),
            "sensor_model": sensor_model,
            "focuser_type": focuser,
            "sensor_id": _as_int(camera_cfg.get("sensor_id"), 0),
            "device_index": _as_int(camera_cfg.get("device_index"), 0),
            "capture_width": _as_int(camera_cfg.get("capture_width"), 1920),
            "capture_height": _as_int(camera_cfg.get("capture_height"), 1080),
            "framerate": _as_int(camera_cfg.get("framerate"), 30),
            "burst_count": _as_int(camera_cfg.get("burst_count"), 3),
            "source_path": _as_text(camera_cfg.get("source_path"), ""),
        },
        "inspection": {
            "profile_name": _as_text(inspection_cfg.get("profile_name"), "default_project"),
            "recipe_name": _as_text(inspection_cfg.get("recipe_name"), "default_recipe"),
            "product_label": _as_text(inspection_cfg.get("product_label"), "Inspection target"),
            "expected_object_label": _as_text(inspection_cfg.get("expected_object_label"), "target"),
            "pass_display_label": _as_text(inspection_cfg.get("pass_display_label"), "Pass"),
            "fail_display_label": _as_text(inspection_cfg.get("fail_display_label"), "Fail"),
            "defect_catalog": [
                str(x).strip() for x in (inspection_cfg.get("defect_catalog") or []) if str(x).strip()
            ],
            "frame_vote_count": _as_int(
                inspection_cfg.get("frame_vote_count"),
                _as_int(camera_cfg.get("burst_count"), 3),
            ),
            "pass_threshold": _as_float(inspection_cfg.get("pass_threshold"), 0.35),
            "fail_threshold": _as_float(inspection_cfg.get("fail_threshold"), 0.65),
            "hard_fail_threshold": _as_float(inspection_cfg.get("hard_fail_threshold"), 0.9),
            "save_fail_frames": _as_bool(inspection_cfg.get("save_fail_frames"), True),
            "save_uncertain_frames": _as_bool(inspection_cfg.get("save_uncertain_frames"), True),
            "roi": {
                "enabled": _as_bool(roi_cfg.get("enabled"), False),
                "x": _as_float(roi_cfg.get("x"), 0.0),
                "y": _as_float(roi_cfg.get("y"), 0.0),
                "width": _as_float(roi_cfg.get("width"), 1.0),
                "height": _as_float(roi_cfg.get("height"), 1.0),
            },
        },
        "gpio": {
            "enabled": _as_bool(gpio_cfg.get("enabled"), False),
            "active_level": _as_text(gpio_cfg.get("active_level"), "high"),
            "pass_pin": gpio_cfg.get("pass_pin"),
            "fail_pin": gpio_cfg.get("fail_pin"),
            "fault_pin": gpio_cfg.get("fault_pin"),
            "busy_pin": gpio_cfg.get("busy_pin"),
            "trigger_pin": gpio_cfg.get("trigger_pin"),
        },
    }
    checked, _warnings = validate_profile(normalized)
    return checked


def build_profile_from_ui(
    existing_profile: Dict[str, Any],
    ui: Dict[str, Any],
    default_focuser_for_sensor: Callable[[Any], str],
    sensor_label_for_model: Callable[[Any], str],
) -> Dict[str, Any]:
    profile = dict(existing_profile or {})

    profile["camera_name"] = _as_text(ui.get("camera_name"), "VisionDock Inspection Station")
    profile["board_model"] = _as_text(ui.get("board_model"), "JETSON_ORIN_NANO")
    profile["output_backend"] = _as_text(ui.get("output_backend"), "mock")
    profile["trigger_mode"] = _as_text(ui.get("trigger_mode"), "manual")

    runtime_cfg = dict(profile.get("runtime") or {})
    runtime_cfg["port"] = _as_int(ui.get("runtime_port"), 8787)
    runtime_cfg["host"] = _as_text(ui.get("runtime_host"), "127.0.0.1")
    profile["runtime"] = runtime_cfg

    camera_cfg = dict(profile.get("camera") or {})
    camera_cfg["backend"] = _as_text(ui.get("camera_backend"), "mock_frame")
    camera_cfg["sensor_model"] = _as_text(ui.get("sensor_model"), "GENERIC_CSI").upper()
    camera_cfg["focuser_type"] = _as_text(
        ui.get("focuser_type"),
        default_focuser_for_sensor(camera_cfg.get("sensor_model")),
    ).lower()
    camera_cfg["sensor_id"] = _as_int(ui.get("sensor_id"), 0)
    camera_cfg["device_index"] = _as_int(ui.get("device_index"), 0)
    camera_cfg["capture_width"] = _as_int(ui.get("capture_width"), 1920)
    camera_cfg["capture_height"] = _as_int(ui.get("capture_height"), 1080)
    camera_cfg["display_width"] = _as_int(ui.get("capture_width"), 1920)
    camera_cfg["display_height"] = _as_int(ui.get("capture_height"), 1080)
    camera_cfg["framerate"] = _as_int(ui.get("framerate"), 30)
    camera_cfg["burst_count"] = _as_int(ui.get("burst_count"), 3)
    camera_cfg["source_path"] = _as_text(ui.get("source_path"), "")

    backend_name = camera_cfg.get("backend") or "mock_frame"
    if backend_name == "jetson_csi_argus":
        camera_cfg["preview_source_label"] = (
            f"Jetson CSI {sensor_label_for_model(camera_cfg.get('sensor_model'))} sensor {camera_cfg['sensor_id']} via Argus "
            f"({camera_cfg['capture_width']}x{camera_cfg['capture_height']} @ {camera_cfg['framerate']} fps)"
        )
    elif backend_name == "opencv_device":
        camera_cfg["preview_source_label"] = f"OpenCV device {camera_cfg['device_index']}"
    elif backend_name in ("image_file", "video_file"):
        camera_cfg["preview_source_label"] = camera_cfg["source_path"] or backend_name.replace("_", " ").title()
    else:
        camera_cfg["preview_source_label"] = "Development mock frame"
    profile["camera"] = camera_cfg

    inspection_cfg = dict(profile.get("inspection") or {})
    inspection_cfg["profile_name"] = _as_text(ui.get("profile_name"), inspection_cfg.get("profile_name") or "")
    inspection_cfg["recipe_name"] = _as_text(ui.get("recipe_name"), inspection_cfg.get("recipe_name") or "")
    inspection_cfg["product_label"] = _as_text(ui.get("product_label"), inspection_cfg.get("product_label") or "")
    inspection_cfg["expected_object_label"] = _as_text(
        ui.get("expected_object_label"),
        inspection_cfg.get("expected_object_label") or "",
    )
    inspection_cfg["pass_display_label"] = _as_text(
        ui.get("pass_display_label"),
        inspection_cfg.get("pass_display_label") or "",
    )
    inspection_cfg["fail_display_label"] = _as_text(
        ui.get("fail_display_label"),
        inspection_cfg.get("fail_display_label") or "",
    )
    defect_catalog_text = _as_text(ui.get("defect_catalog"), "")
    inspection_cfg["defect_catalog"] = [part.strip() for part in defect_catalog_text.split(",") if part.strip()]
    inspection_cfg["frame_vote_count"] = _as_int(ui.get("burst_count"), inspection_cfg.get("frame_vote_count") or 3)
    inspection_cfg["pass_threshold"] = _as_float(ui.get("pass_threshold"), inspection_cfg.get("pass_threshold") or 0.35)
    inspection_cfg["fail_threshold"] = _as_float(ui.get("fail_threshold"), inspection_cfg.get("fail_threshold") or 0.65)
    inspection_cfg["hard_fail_threshold"] = _as_float(
        ui.get("hard_fail_threshold"),
        inspection_cfg.get("hard_fail_threshold") or 0.9,
    )
    inspection_cfg["save_fail_frames"] = _as_bool(ui.get("save_fail_frames"), True)
    inspection_cfg["save_uncertain_frames"] = _as_bool(ui.get("save_uncertain_frames"), True)
    inspection_cfg["roi"] = {
        "enabled": _as_bool(ui.get("roi_enabled"), False),
        "x": _as_float(ui.get("roi_x"), 0.0),
        "y": _as_float(ui.get("roi_y"), 0.0),
        "width": _as_float(ui.get("roi_width"), 1.0),
        "height": _as_float(ui.get("roi_height"), 1.0),
    }
    profile["inspection"] = inspection_cfg

    gpio_cfg = dict(profile.get("gpio") or {})
    gpio_cfg["enabled"] = _as_bool(ui.get("gpio_enabled"), False)
    gpio_cfg["active_level"] = _as_text(ui.get("gpio_active_level"), "high")
    gpio_cfg["pass_pin"] = ui.get("pass_pin")
    gpio_cfg["fail_pin"] = ui.get("fail_pin")
    gpio_cfg["fault_pin"] = ui.get("fault_pin")
    gpio_cfg["busy_pin"] = ui.get("busy_pin")
    gpio_cfg["trigger_pin"] = ui.get("trigger_pin")
    profile["gpio"] = gpio_cfg

    checked, _warnings = validate_profile(profile)
    return checked
