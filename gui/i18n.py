"""Minimal UI string registry (English-first)."""

from typing import Dict


_EN: Dict[str, str] = {
    # Navigation / home
    "nav.dashboard": "Dashboard",
    "nav.inspection": "Inspection",
    "nav.models": "Models",
    "nav.devices": "Devices",
    "nav.results": "Results",
    "nav.settings": "Settings",
    "home.subtitle": "Access and manage module",
    "home.card.inspection": "Inspection",
    "home.card.models": "Model Packages",
    "home.card.devices": "Devices",
    "home.card.results": "Results",
    # Notifications
    "notif.thermal_guard_on": "Thermal guard enabled.",
    "notif.thermal_guard_off": "Thermal guard disabled.",
    "notif.remote_online": "Runtime endpoint reachable: {host}",
    "notif.remote_unreachable": "Runtime endpoint unreachable: {host}",
    "notif.docker_active": "Docker service is active.",
    "notif.docker_offline": "Docker service is unavailable.",
    "notif.none_active": "No active notifications.",
    "notif.system_normal": "System is normal. No notifications.",
    "notif.stored_single": "{count} notification stored. Latest: [{ts}] {message}",
    "notif.stored_multi": "{count} notifications stored. Latest: [{ts}] {message}",
    "notif.profile_autofix_info": "Profile settings auto-corrected.",
    "notif.profile_autofix_warning": "Profile settings auto-corrected. Save completed safely.",
}


def t(key: str, default: str = "", **kwargs) -> str:
    msg = _EN.get(key, default or key)
    if kwargs:
        try:
            return msg.format(**kwargs)
        except Exception:
            return msg
    return msg

