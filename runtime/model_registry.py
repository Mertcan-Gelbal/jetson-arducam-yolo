import json
import os
from datetime import datetime

from runtime.package_loader import get_package, list_packages


def _visiondock_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), ".visiondock")
    os.makedirs(path, exist_ok=True)
    return path


def registry_state_path() -> str:
    return os.path.join(_visiondock_dir(), "model_registry_state.json")


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_state():
    path = registry_state_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def save_state(state: dict):
    path = registry_state_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state or {}, f, indent=2)


def list_available_packages():
    return list_packages()


def get_active_package():
    state = load_state()
    package_id = str(state.get("active_package_id") or "").strip()
    if not package_id:
        return None
    return get_package(package_id)


def get_previous_package():
    state = load_state()
    package_id = str(state.get("previous_active_package_id") or "").strip()
    if not package_id:
        return None
    return get_package(package_id)


def active_model_payload():
    package = get_active_package()
    if not package:
        return None
    return {
        "name": package.get("package_name") or package.get("model_name") or "Deployed model",
        "version": package.get("version") or "unassigned",
        "package_id": package.get("package_id"),
        "model_name": package.get("model_name") or package.get("package_name") or "",
    }


def activate_package(package_id: str):
    target = str(package_id or "").strip()
    package = get_package(target)
    if not package:
        raise FileNotFoundError(f"Package not found: {target}")
    state = load_state()
    current_active = str(state.get("active_package_id") or "").strip()
    if current_active and current_active != target:
        state["previous_active_package_id"] = current_active
    state["active_package_id"] = target
    state["activated_at"] = _iso_now()
    save_state(state)
    return package


def rollback_active_package():
    state = load_state()
    previous = str(state.get("previous_active_package_id") or "").strip()
    current = str(state.get("active_package_id") or "").strip()
    if not previous:
        raise FileNotFoundError("No previous package is available for rollback.")
    package = get_package(previous)
    if not package:
        raise FileNotFoundError(f"Previous package is missing: {previous}")
    state["active_package_id"] = previous
    state["previous_active_package_id"] = current or ""
    state["activated_at"] = _iso_now()
    save_state(state)
    return package
