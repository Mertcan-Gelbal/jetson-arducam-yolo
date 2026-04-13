import json
import os


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _visiondock_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), ".visiondock")
    os.makedirs(path, exist_ok=True)
    return path


def inspection_profile_path() -> str:
    return os.path.join(_visiondock_dir(), "inspection_profile.json")


def inspection_defaults_path() -> str:
    return os.path.join(_project_root(), "config", "inspection_profile_defaults.json")


def load_json(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return fallback


def _deep_merge(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def default_inspection_profile():
    return load_json(inspection_defaults_path(), {})


def load_inspection_profile():
    defaults = default_inspection_profile()
    saved = load_json(inspection_profile_path(), {})
    merged = _deep_merge(defaults, saved)
    if isinstance(merged, dict):
        if not merged.get("camera_name") and merged.get("station_name"):
            merged["camera_name"] = merged.get("station_name")
        if not merged.get("station_name") and merged.get("camera_name"):
            merged["station_name"] = merged.get("camera_name")
    return merged


def save_inspection_profile(profile: dict) -> None:
    with open(inspection_profile_path(), "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
