import sys, os, re, psutil, subprocess, cv2, time, platform, numpy as np, glob, random, string, threading, json, sqlite3, logging, socket, shlex, posixpath
import urllib.request, urllib.error
from urllib.parse import urlparse, urlencode
from datetime import datetime

APP_VERSION = "2.0.0"
from logging.handlers import RotatingFileHandler

_GUI_DIR = os.path.dirname(os.path.abspath(__file__))
if _GUI_DIR not in sys.path:
    sys.path.insert(0, _GUI_DIR)


def _env_truthy(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def is_production_mode() -> bool:
    """Factory / production panel: destructive UI actions disabled via environment."""
    return _env_truthy("VISIONDOCK_PRODUCTION") or _env_truthy("VISIONDOCK_FACTORY_MODE")


def is_remote_host_locked() -> bool:
    """Remote Docker host IP field read-only (fixed-line deployments)."""
    return _env_truthy("VISIONDOCK_LOCK_REMOTE_HOST")


def _setup_logging():
    level_name = (os.environ.get("VISIONDOCK_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    log_dir = os.environ.get("VISIONDOCK_LOG_DIR") or os.path.join(os.path.expanduser("~"), ".visiondock")
    os.makedirs(log_dir, exist_ok=True)
    default_file = os.path.join(log_dir, "visiondock.log")
    log_file = os.environ.get("VISIONDOCK_LOG_PATH") or default_file
    try:
        max_bytes = int(os.environ.get("VISIONDOCK_LOG_MAX_BYTES") or str(5 * 1024 * 1024))
        backup_count = int(os.environ.get("VISIONDOCK_LOG_BACKUP_COUNT") or "3")
    except ValueError:
        max_bytes, backup_count = 5 * 1024 * 1024, 3
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(level)
    root.addHandler(fh)
    if _env_truthy("VISIONDOCK_LOG_CONSOLE"):
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        ch.setLevel(level)
        root.addHandler(ch)


_setup_logging()
log = logging.getLogger("visiondock")

# ⋯ menüsünde Qt’nin ek ok göstergesini kapatır (ToolIndicator yanında chevron kalmasın).
QTOOLBTN_HIDE_MENU_ARROW = (
    "QToolButton::menu-indicator { image: none; width: 0; height: 0; margin: 0; padding: 0; border: none; }"
)

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    p = os.path.join(base_path, relative_path)
    if not os.path.exists(p) and base_path.endswith("gui"):
        p = os.path.join(os.path.dirname(base_path), relative_path)
    return p

# Silence console noise after basic imports
os.environ["OPENCV_LOG_LEVEL"] = "OFF"
os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.qpa.fonts=false"


def _run_script_checked(args, cwd, timeout=30):
    """Run a subprocess script; return (success, combined stderr+stdout for errors)."""
    try:
        r = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"Timed out after {timeout}s"
    except Exception as e:
        return False, str(e)
    combined = ((r.stderr or "").strip() + "\n" + (r.stdout or "").strip()).strip()
    if r.returncode != 0:
        return False, combined or f"Exit code {r.returncode}"
    return True, combined


from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFrame,
    QScrollArea, QGridLayout, QComboBox, QFileDialog,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QAbstractButton, QSizePolicy, QFormLayout, QLayout,
    QGraphicsBlurEffect, QMenu, QTabWidget, QLineEdit, QPlainTextEdit, QMessageBox,
    QSlider, QListView, QSpinBox, QDoubleSpinBox, QDialog, QDialogButtonBox, QStyle, QSplashScreen,
    QCheckBox,
    QToolTip, QToolButton,
)

from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QSize, QPoint, QPointF, QRect, QUrl,
    QPropertyAnimation, QEasingCurve, Property, QEvent, QMutex,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QImage, QPixmap, QPainter, QPainterPath, QPen, QBrush,
    QCursor, QShowEvent, QResizeEvent, QMouseEvent, QTextCursor, QDesktopServices, QPalette, QShortcut,
)
# PySide6: use scripts/build_release.py hidden imports for PyInstaller

from runtime.package_loader import (
    build_package_from_workspace,
    get_package as get_local_model_package,
    list_packages as list_local_model_packages,
    packages_root_dir as local_model_packages_root_dir,
    scan_workspace_candidates,
)
from runtime.model_registry import (
    activate_package as activate_local_model_package,
    get_active_package as get_local_active_model_package,
    rollback_active_package as rollback_local_model_package,
)
from runtime.inspection_profile_controller import (
    build_profile_from_ui,
    normalize_loaded_profile,
    validate_profile,
)
from runtime.camera_profiles import (
    csi_sensor_default_focuser,
    csi_sensor_default_resolution_index,
    csi_sensor_label,
    csi_sensor_note,
    csi_sensor_profiles,
    csi_sensor_recommended_capture,
    focus_scripts_for_focuser,
    focuser_available,
    focuser_label,
    focuser_note,
    focuser_profiles,
)
from page_inspection import build_inspection_page
from page_models import build_models_page
from page_results import build_results_page
from page_settings import build_settings_page
from i18n import t


def camera_recordings_slug(display_name) -> str:
    """Filesystem-safe folder name under gui/recordings/ for each camera."""
    raw = (display_name or "").strip()
    if not raw:
        return "general"
    s = re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw).strip("._-")
    s = s[:72] if len(s) > 72 else s
    return (s or "general").lower()


def recordings_root_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")


def workspaces_root_dir() -> str:
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "workspaces"))
    os.makedirs(path, exist_ok=True)
    return path


def workspace_dir_for_name(name: str) -> str:
    return os.path.join(workspaces_root_dir(), str(name or "").strip())


def inspection_results_root_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), ".visiondock", "results")
    os.makedirs(path, exist_ok=True)
    return path


def inspection_results_index_path() -> str:
    return os.path.join(inspection_results_root_dir(), "index.jsonl")


def inspection_latest_result_path() -> str:
    return os.path.join(inspection_results_root_dir(), "latest.json")


def load_inspection_result_records(limit=None):
    path = inspection_results_index_path()
    if not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        return []
    rows.reverse()
    if limit is not None:
        try:
            rows = rows[: max(0, int(limit))]
        except (TypeError, ValueError):
            pass
    return rows


def load_runtime_result_records(url: str, limit=60):
    if not url:
        return []
    query = urlencode({"limit": max(1, int(limit or 60))})
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}{query}", headers={"User-Agent": f"VisionDock/{APP_VERSION}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        records = payload.get("records") if isinstance(payload, dict) else []
        return records if isinstance(records, list) else []
    except Exception:
        return []


# Snapshots / REC / common library extensions only (ignore .DS_Store, sidecars, etc.)
_RECORDING_MEDIA_EXT = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
    ".avi", ".mp4", ".mov", ".mkv", ".webm", ".mpeg", ".mpg",
})


def _is_recording_media_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in _RECORDING_MEDIA_EXT


def collect_recording_file_paths(base_dir: str) -> list:
    """Legacy flat files in base_dir plus one level of per-camera subfolders; hidden entries skipped."""
    out = []
    if not os.path.isdir(base_dir):
        return out
    try:
        for name in os.listdir(base_dir):
            if name.startswith("."):
                continue
            p = os.path.join(base_dir, name)
            if os.path.isfile(p) and _is_recording_media_file(p):
                out.append(p)
            elif os.path.isdir(p):
                try:
                    for sub in os.listdir(p):
                        if sub.startswith("."):
                            continue
                        f = os.path.join(p, sub)
                        if os.path.isfile(f) and _is_recording_media_file(f):
                            out.append(f)
                except OSError:
                    pass
    except OSError:
        pass
    return out


def rounded_pixmap(pix: QPixmap, radius: int) -> QPixmap:
    """Clip pixmap to rounded rect (live preview / modal camera preview)."""
    if pix.isNull() or radius < 1:
        return pix
    out = QPixmap(pix.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, float(pix.width()), float(pix.height()), float(radius), float(radius))
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


def search_magnifier_pixmap(px: int = 24, color=None) -> QPixmap:
    """Crisp vector-style magnifier for search fields (high visibility on dark/light UI)."""
    c = color or QColor(10, 132, 255)
    d = max(18, px)
    pm = QPixmap(d, d)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(c)
    pen.setWidthF(max(2.0, d / 11.0))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    cx, cy = d * 0.4, d * 0.4
    pr = d * 0.22
    p.drawEllipse(QPointF(cx, cy), pr, pr)
    x1 = cx + pr * 0.65
    y1 = cy + pr * 0.65
    x2 = d * 0.9
    y2 = d * 0.9
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    p.end()
    return pm


class SearchFieldShell(QFrame):
    """Rounded shell with magnifier badge + line edit so search is immediately recognizable."""

    def __init__(self, line_edit: QLineEdit, parent=None):
        super().__init__(parent)
        self.setObjectName("SearchShell")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._ed = line_edit
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 14, 5)
        lay.setSpacing(12)
        badge = QLabel()
        badge.setObjectName("SearchIconBadge")
        badge.setFixedSize(46, 42)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(search_magnifier_pixmap(26, QColor(10, 132, 255)))
        badge.setToolTip("Search")
        badge.setScaledContents(False)
        line_edit.setObjectName("SearchField")
        # Native clear glyph on some platforms looks informal; keep field minimal.
        line_edit.setClearButtonEnabled(False)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(line_edit, 1, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, e):
        self._ed.setFocus()
        super().mousePressEvent(e)


# =============================================================================
#  PERSISTENCE MANAGER (SQLITE)
# =============================================================================

class DBManager:
    def __init__(self):
        # Use a persistent user directory for the database
        home = os.path.expanduser("~")
        data_dir = os.path.join(home, ".visiondock")
        os.makedirs(data_dir, exist_ok=True)
        self.db_path = os.path.join(data_dir, "studio.db")
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS cameras (id INTEGER PRIMARY KEY, name TEXT, src TEXT, meta TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS workspaces (id INTEGER PRIMARY KEY, name TEXT, img TEXT, cid TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS devices (id INTEGER PRIMARY KEY, name TEXT, host TEXT, user TEXT, key_path TEXT)")
            # Migration: Add host column to workspaces if missing
            try:
                conn.execute("ALTER TABLE workspaces ADD COLUMN host TEXT")
            except sqlite3.OperationalError:
                pass # column exists

    def save_camera(self, name, src, meta):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO cameras (name, src, meta) VALUES (?, ?, ?)", (name, src, meta))

    def get_cameras(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT name, src, meta FROM cameras").fetchall()

    def remove_camera(self, src):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cameras WHERE src = ?", (src,))

    def update_camera_meta(self, src, meta):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE cameras SET meta = ? WHERE src = ?", (meta, src))

    def save_workspace(self, name, img, cid, host=None):
        with sqlite3.connect(self.db_path) as conn:
            # One DB row per container: delete any prior row first (avoid duplicates / double cards)
            short = (cid or "")[:12]
            conn.execute("DELETE FROM workspaces WHERE cid = ? OR cid LIKE ?", (cid, short + "%"))
            conn.execute("INSERT INTO workspaces (name, img, cid, host) VALUES (?, ?, ?, ?)", (name, img, cid, host))

    def get_workspaces(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT name, img, cid, host FROM workspaces").fetchall()

    def get_workspace_by_cid(self, cid):
        """Return saved (name, img) for container id (short or full), or None."""
        if not cid:
            return None
        with sqlite3.connect(self.db_path) as conn:
            short = (cid or "")[:12]
            row = conn.execute(
                "SELECT name, img FROM workspaces WHERE cid = ? OR cid LIKE ? LIMIT 1",
                (cid, short + "%")
            ).fetchone()
            return (row[0], row[1]) if row else None

    def get_devices(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT name, host, user, key_path FROM devices").fetchall()

    def save_device(self, name, host, user, key_path=None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO devices (name, host, user, key_path) VALUES (?, ?, ?, ?)", (name or "Device", host or "", user or "jetson", key_path or ""))

    def update_device(self, old_host, name, host, user, key_path=None):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM devices WHERE rowid = (SELECT rowid FROM devices WHERE host = ? LIMIT 1)",
                (old_host,),
            )
            conn.execute("INSERT INTO devices (name, host, user, key_path) VALUES (?, ?, ?, ?)", (name or "Device", host or "", user or "jetson", key_path or ""))

    def remove_device(self, host):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM devices WHERE rowid = (SELECT rowid FROM devices WHERE host = ? LIMIT 1)",
                (host,),
            )

    def remove_workspace(self, cid):
        with sqlite3.connect(self.db_path) as conn:
            short = (cid or "")[:12]
            conn.execute("DELETE FROM workspaces WHERE cid = ? OR cid LIKE ?", (cid, short + "%"))

# =============================================================================
#  CATALOG MANAGER (DYNAMIC)
# =============================================================================

class CatalogManager:
    DEFAULT_CATALOG = {
        "jetson": [
            {"name": "L4T PyTorch", "img": "nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3", "desc": "PyTorch 2.0 (Jetson)"},
            {"name": "L4T ML", "img": "nvcr.io/nvidia/l4t-ml:r35.2.1-py3", "desc": "All-in-One ML"},
            {"name": "DeepStream", "img": "nvcr.io/nvidia/deepstream-l4t:6.2-base", "desc": "Video Analytics"}
        ],
        "desktop": [
            {"name": "Python 3.9 (Light)", "img": "python:3.9-slim", "desc": "Fast & Multi-arch Support"},
            {"name": "Ubuntu 22.04", "img": "ubuntu:22.04", "desc": "Standard Linux Base"},
            {"name": "Nginx Web", "img": "nginx:latest", "desc": "Web Server Test"},
            {"name": "PyTorch (CPU)", "img": "pytorch/pytorch:latest", "desc": "Warning: May be slow on Mac (amd64)"}
        ]
    }

    @staticmethod
    def get_images():
        cat_path = os.path.join(os.path.dirname(__file__), "catalog.json")
        try:
            if os.path.exists(cat_path):
                with open(cat_path, "r") as f:
                    data = json.load(f)
                    if data: return data
        except: pass
        return CatalogManager.DEFAULT_CATALOG

    @staticmethod
    def get_recommended():
        arch = platform.machine()
        system = platform.system()
        catalog = CatalogManager.get_images()
        if arch == "aarch64" and system == "Linux":
            return catalog.get("jetson", []), "Jetson (NVIDIA NGC)"
        else:
            return catalog.get("desktop", []), f"{system} ({arch})"

def get_gpu_info():
    try:
        sys_plat = platform.system()
        if sys_plat == "Darwin": return "Apple Silicon GPU"
        elif sys_plat == "Linux":
            if os.path.exists("/usr/bin/tegrastats"): return "NVIDIA Tegra (Jetson)"
            out = subprocess.check_output("lspci | grep -i vga", shell=True).decode()
            if "NVIDIA" in out: return "NVIDIA GPU"
    except: pass
    return "Integrated / Unknown"

def list_cameras():
    cams = []
    if platform.system() == "Linux":
        for d in sorted(glob.glob('/dev/video*')):
            try:
                idx = int(d.replace('/dev/video','').strip())
                if idx % 2 != 0 and idx > 0: continue
                name = f"Internal CSI Camera {idx}" if idx < 2 else f"External USB Camera {idx}"
                cams.append((name, idx))
            except: pass
    if not cams:
        for i in range(2):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                cams.append((f"Local Camera {i}", i))
                cap.release()
    return cams

def get_zerotier_networks():
    """Returns list of {nwid, name, status, assignedAddresses[]} for each joined network."""
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output("zerotier-one_x64.exe -j listnetworks", shell=True).decode()
        else:
            out = subprocess.check_output("zerotier-cli -j listnetworks", shell=True).decode()
        data = json.loads(out)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if data.get("nwid"):
                return [data]
            if "networks" in data and isinstance(data["networks"], list):
                return data["networks"]
            return []
        return []
    except Exception:
        return []


def get_zerotier_peer_count():
    """Number of peers currently visible (LEAF nodes)."""
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output("zerotier-one_x64.exe -j listpeers", shell=True).decode()
        else:
            out = subprocess.check_output("zerotier-cli -j listpeers", shell=True).decode()
        peers = json.loads(out)
        return sum(1 for p in peers if p.get("paths"))
    except Exception:
        return 0


def _extract_ip_from_path(path_el):
    """Extract IP from a path element (dict or 'ip/port' / 'ip:port' string)."""
    if path_el is None:
        return ""
    s = ""
    if isinstance(path_el, dict):
        s = (path_el.get("address") or path_el.get("physicalAddress") or path_el.get("addr") or path_el.get("ip") or "")
    elif isinstance(path_el, str):
        s = path_el
    s = str(s).strip()
    if "/" in s:
        s = s.split("/")[0].strip()
    if ":" in s and s.count(":") >= 1:
        s = s.split(":")[0].strip()
    if s and (s.replace(".", "").isdigit() or (s.count(".") == 3 and all(x.isdigit() for x in s.split(".")))):
        return s
    return s if s else ""


def _is_private_ipv4(ip: str) -> bool:
    try:
        parts = (ip or "").strip().split(".")
        if len(parts) != 4:
            return False
        a, b, c, d = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
        if a == 10:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 192 and b == 168:
            return True
        return False
    except (ValueError, TypeError):
        return False


def _zt_json_list(val):
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val] if val else []


def _ipv4_cidr_to_dotted_prefix(cidr: str) -> str:
    """IPv4 CIDR veya 'a.b.c.d' → startswith eşlemesi için önek (örn. 195.168.191.5/24 → 195.168.191.)."""
    s = (cidr or "").strip()
    if not s or ":" in s:
        return ""
    if "/" in s:
        ip_part, mask_s = s.split("/", 1)
    else:
        ip_part, mask_s = s, "24"
    try:
        mask = int(mask_s)
    except ValueError:
        mask = 24
    parts = ip_part.strip().split(".")
    if len(parts) != 4:
        return ""
    try:
        a, b, c, d = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
    except ValueError:
        return ""
    if not all(0 <= x <= 255 for x in (a, b, c, d)):
        return ""
    if mask >= 24:
        return f"{a}.{b}.{c}."
    if mask >= 16:
        return f"{a}.{b}."
    if mask >= 8:
        return f"{a}."
    return ""


def get_zerotier_managed_ipv4_prefixes():
    """listnetworks'taki atamalar, rotalar ve IP havuzlarından yönetilen IPv4 önekleri.
    ZeroTier Central'da tanımlı ağ/moon aralığı (ör. 195.168.191.0/24) buradan gelir.
    Hiçbiri çıkmazsa 192.168.192. ile geriye uyumlu varsayılan.
    """
    out = []
    seen = set()

    def add_pref(p: str):
        p = (p or "").strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    try:
        for net in get_zerotier_networks():
            for addr in _zt_json_list(net.get("assignedAddresses")):
                pre = _ipv4_cidr_to_dotted_prefix(str(addr))
                if pre:
                    add_pref(pre)
            for r in _zt_json_list(net.get("routes")):
                if isinstance(r, dict):
                    pre = _ipv4_cidr_to_dotted_prefix(str(r.get("target") or ""))
                    if pre:
                        add_pref(pre)
            for pool in _zt_json_list(net.get("ipAssignmentPools")):
                if not isinstance(pool, dict):
                    continue
                start = pool.get("ipRangeStart") or pool.get("start") or ""
                start_s = str(start).strip()
                if not start_s:
                    continue
                if "/" in start_s:
                    pre = _ipv4_cidr_to_dotted_prefix(start_s)
                else:
                    parts = start_s.split("/")[0].strip().split(".")
                    pre = f"{parts[0]}.{parts[1]}.{parts[2]}." if len(parts) == 4 else ""
                if pre:
                    add_pref(pre)
    except Exception:
        pass
    if not out:
        out.append("192.168.192.")
    return out


def ip_matches_zt_managed_prefixes(ip: str, prefixes: list) -> bool:
    if not prefixes:
        return False
    ip = (ip or "").strip()
    return any(ip.startswith(p) for p in prefixes if p)


def _sort_zt_ips_display_order(ips: list, prefixes: list = None) -> list:
    """Ağ ayarlarından gelen yönetilen öneklerle eşleşen sanal IP'leri öne alır."""
    prefs = prefixes if prefixes is not None else get_zerotier_managed_ipv4_prefixes()
    ips = [str(x).strip() for x in (ips or []) if x and str(x).strip()]
    managed = [x for x in ips if ip_matches_zt_managed_prefixes(x, prefs)]
    rest = [x for x in ips if x not in managed]
    return managed + rest


def _peer_assigned_virtual_ips(peer_dict):
    """ZeroTier listpeers kaydında varsa kontrolörün atadığı sanal IP'ler (çoğu kurulumda boş)."""
    ips = []
    for key in ("ipAssignments", "ipAssignment"):
        v = peer_dict.get(key)
        if v is None:
            continue
        if isinstance(v, list):
            for x in v:
                ip = str(x).split("/")[0].strip()
                if ip and ip not in ips:
                    ips.append(ip)
        else:
            ip = str(v).split("/")[0].strip()
            if ip and ip not in ips:
                ips.append(ip)
    addrs = peer_dict.get("assignedAddresses") or []
    if not isinstance(addrs, list):
        addrs = [addrs] if addrs else []
    for addr in addrs:
        ip = str(addr).split("/")[0].strip()
        if ip and ip not in ips:
            ips.append(ip)
    return ips


def _peer_preferred_path_ip(paths, prefixes: list = None):
    """Path IP: önce listnetworks ile aynı yönetilen ZT önekine düşen adres, sonra preferred, RFC1918, diğer."""
    prefs = prefixes if prefixes is not None else get_zerotier_managed_ipv4_prefixes()
    if not paths:
        return "", []
    seen = []
    managed_ip = ""
    preferred_ip = ""
    private_ip = ""
    any_ip = ""
    for path_el in paths:
        ip = _extract_ip_from_path(path_el)
        if not ip:
            continue
        if ip not in seen:
            seen.append(ip)
        d = path_el if isinstance(path_el, dict) else None
        if d and d.get("preferred") and not preferred_ip:
            preferred_ip = ip
        if ip_matches_zt_managed_prefixes(ip, prefs) and not managed_ip:
            managed_ip = ip
        elif _is_private_ipv4(ip) and not private_ip:
            private_ip = ip
        if not any_ip:
            any_ip = ip
    chosen = managed_ip or preferred_ip or private_ip or any_ip
    return chosen, seen


def get_zerotier_status():
    """Returns ZeroTier daemon status: 'ONLINE', 'OFFLINE', or 'NOT INSTALLED'.
    Runs `zerotier-cli info` which outputs: '200 info <nodeId> <version> ONLINE'
    """
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output("zerotier-one_x64.exe info", shell=True, timeout=5).decode()
        else:
            out = subprocess.check_output("zerotier-cli info", shell=True, timeout=5).decode()
        upper = out.upper()
        if "ONLINE" in upper:
            return "ONLINE"
        if "OFFLINE" in upper:
            return "OFFLINE"
        return "UNKNOWN"
    except FileNotFoundError:
        return "NOT INSTALLED"
    except Exception:
        return "OFFLINE"


def parse_zerotier_info_output(text: str) -> str:
    """zerotier-cli info çıktısından düğüm kimliği (ör. ... info a126540037 1.16.0 ONLINE)."""
    parts = (text or "").strip().split()
    if "info" in parts:
        i = parts.index("info")
        if i + 1 < len(parts):
            return parts[i + 1].strip().lower()
    return ""


def get_zerotier_local_node_id():
    """zerotier-cli info / zerotier-one_x64.exe info çıktısından bu makinenin ZT düğüm kimliği.
    Örnek satır: 200 info a126540037 1.16.0 ONLINE
    Peer listesinde yerel cihazı gizlemek için kullanılır.
    """
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output("zerotier-one_x64.exe info", shell=True, timeout=5).decode(
                errors="replace"
            )
        else:
            out = subprocess.check_output("zerotier-cli info", shell=True, timeout=5).decode(errors="replace")
        return parse_zerotier_info_output(out)
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        pass
    except Exception:
        pass
    return ""


def get_zerotier_local_ips():
    """Returns list of ZeroTier virtual IPs assigned to THIS machine.
    Source: listnetworks → assignedAddresses (e.g. ['10.144.1.5/24']).
    These are the ZT virtual IPs, not physical LAN/WAN addresses.
    """
    ips = []
    try:
        for net in get_zerotier_networks():
            addrs = net.get("assignedAddresses") or []
            if not isinstance(addrs, list):
                addrs = [addrs] if addrs else []
            for addr in addrs:
                ip = str(addr).split("/")[0].strip()
                if ip and ip not in ips:
                    ips.append(ip)
    except Exception:
        pass
    return _sort_zt_ips_display_order(ips)


def get_zerotier_peers():
    """Parse ZeroTier `listpeers -j` output into peer rows (LEAF = device, PLANET = infra).
    `ip`: SSH için tercih edilen path IP (tercihli path veya özel ağ adresi).
    `zt_from_controller`: Yerel listede varsa kontrolörün bildirdiği sanal IP'ler (çoğu kurulumda boş).
    Uzak cihazın gerçek ZT sanal IP'si için SSH üzerinde zerotier-cli gerekir (ZT IP Al).
    """
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output("zerotier-one_x64.exe -j listpeers", shell=True).decode()
        else:
            out = subprocess.check_output("zerotier-cli -j listpeers", shell=True).decode()
        raw = json.loads(out)
        if not isinstance(raw, list):
            return []
        zt_prefs = get_zerotier_managed_ipv4_prefixes()
        result = []
        for p in raw:
            addr = p.get("address") or p.get("nodeId") or ""
            if not isinstance(addr, str):
                addr = str(addr)
            role = (p.get("role") or "LEAF").upper()
            if role in ("PLANET", "MOON"):
                continue
            latency = p.get("latency") if isinstance(p.get("latency"), (int, float)) else 0
            version = p.get("version") or ""
            paths = p.get("paths") or []
            ip_str, path_list = _peer_preferred_path_ip(paths, zt_prefs)
            if not ip_str and paths and isinstance(paths[0], str):
                ip_str = _extract_ip_from_path(paths[0])
            zt_ctrl = _peer_assigned_virtual_ips(p)
            moon_zt = [x for x in zt_ctrl if ip_matches_zt_managed_prefixes(x, zt_prefs)]
            if moon_zt:
                ip_out = moon_zt[0]
            elif zt_ctrl:
                ip_out = zt_ctrl[0]
            else:
                ip_out = (ip_str or "").strip()
            result.append({
                "address": addr[:10] if addr else "—",
                "address_full": addr if addr else "",
                "role": role,
                "latency": latency,
                "version": str(version)[:20] if version else "—",
                "connected": len(paths) > 0,
                "ip": ip_out,
                "path_ips": path_list,
                "zt_from_controller": zt_ctrl,
            })
        return result
    except Exception:
        return []


def ssh_get_zerotier_ips(client):
    """SSH ile bağlı uzak cihazda zerotier-cli çalıştırarak ZT sanal IP'lerini döndürür.
    `listpeers`'daki fiziksel path IP'den farklı olarak bu fonksiyon gerçek ZT sanal IP'yi verir.
    Döndürülen IP'ler CIDR notasyonu olmadan (örn. '10.144.1.5') gelir.
    """
    try:
        out, err, code = ssh_exec_text(
            client,
            "zerotier-cli -j listnetworks 2>/dev/null || echo '[]'",
            timeout=15,
        )
        raw = (out or "").strip()
        if not raw or raw == "[]":
            return []
        data = json.loads(raw)
        ips = []
        for net in (data if isinstance(data, list) else []):
            addrs = net.get("assignedAddresses") or []
            if not isinstance(addrs, list):
                addrs = [addrs] if addrs else []
            for addr in addrs:
                ip = str(addr).split("/")[0].strip()
                if ip and ip not in ips:
                    ips.append(ip)
        return _sort_zt_ips_display_order(ips)
    except Exception:
        return []


def open_ssh_in_terminal(user, host, key_path=None):
    """Open ssh user@host in the system terminal (password prompt OK). Uses ssh -i when key_path is set."""
    if not host or not host.strip():
        return False
    base = f"ssh {user}@{host.strip()}"
    if key_path and os.path.exists(os.path.expanduser(key_path)):
        base = f"ssh -i {os.path.expanduser(key_path)} {user}@{host.strip()}"
    cmd_hold = f"{base}; echo ''; read -p 'Press Enter to exit...'"
    plat = platform.system()
    try:
        if plat == "Darwin":
            subprocess.Popen(["osascript", "-e", f'tell app "Terminal" to do script "{base}"'])
        elif plat == "Linux":
            for term in ["gnome-terminal", "konsole", "xterm"]:
                try:
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--", "bash", "-c", cmd_hold])
                    elif term == "konsole":
                        subprocess.Popen([term, "-e", f"bash -c '{cmd_hold}'"])
                    else:
                        subprocess.Popen([term, "-e", base])
                    return True
                except FileNotFoundError:
                    continue
            subprocess.Popen(["xterm", "-e", base])
        elif plat == "Windows":
            subprocess.Popen(f"start cmd /k ssh {user}@{host.strip()}", shell=True)
        return True
    except Exception:
        return False


def check_remote_node_reachable(host, port=2375, timeout=2):
    """Return True if Docker TCP port on Jetson/remote host is reachable (e.g. ZeroTier IP)."""
    if not host or not host.strip():
        return False
    host = host.strip()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False

# =============================================================================
#  CORE LOGIC & THREADS
# =============================================================================

def docker_cli_error_for_ui(stderr: str) -> tuple[str, str]:
    """Map raw docker stderr to (short message, optional technical detail for Details…)."""
    msg = (stderr or "").strip()
    low = msg.lower()
    if "docker.sock" in low or "docker api" in low or "failed to connect to the docker" in low:
        short = (
            "Docker is not reachable on this computer.\n\n"
            "• macOS: start Docker Desktop and wait until it is running.\n"
            "• Or use Run target → Remote with your Jetson / edge IP (Docker must run there)."
        )
        return short, msg
    if "connection refused" in low or "cannot connect" in low or "no route to host" in low:
        return (
            "Could not reach the Docker host. Check the IP address and that Docker is running on that machine.",
            msg,
        )
    if "no such image" in low or "pull access denied" in low:
        return ("Image not found or not pulled on the target host.", msg)
    if len(msg) > 500:
        return (msg[:500].rstrip() + "...", msg)
    return (msg, "")


def pretty_workspace_title(name: str) -> str:
    """Display name for workspace cards (container names often use underscores)."""
    if not name:
        return name
    s = str(name).strip().replace("__", " ").replace("_", " ")
    return s[:72]


class DockerManager:
    _host = None # Static host pointer (e.g. tcp://192.168.192.20:2375)

    @classmethod
    def set_host(cls, ip):
        cls._host = f"tcp://{ip}:2375" if ip else None

    @classmethod
    def get_cmd(cls, base_cmd, host_override=None):
        """host_override: None = use current Settings host, '' = local, 'IP' = that host."""
        if host_override is not None:
            h = f"tcp://{host_override}:2375" if (host_override and str(host_override).strip()) else None
        else:
            h = cls._host
        return f"docker -H {h} {base_cmd}" if h else f"docker {base_cmd}"

    @staticmethod
    def _docker_run(cmd: str, *, capture: bool = False, timeout: float = 25.0):
        """Run docker CLI; never inherit stderr (avoids spam when daemon/socket is missing)."""
        try:
            if capture:
                r = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    encoding="utf-8",
                    errors="replace",
                )
                return r.returncode, (r.stdout or ""), (r.stderr or "")
            r = subprocess.run(
                cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            return r.returncode, "", ""
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            log.debug("docker CLI: %s", e)
            return -1, "", str(e)

    @staticmethod
    def is_running():
        code, _, _ = DockerManager._docker_run(DockerManager.get_cmd("info"), capture=False, timeout=8)
        return code == 0

    @staticmethod
    def start_service():
        plat = platform.system()
        try:
            if plat == "Darwin": subprocess.Popen("open -a Docker", shell=True); return True
            elif plat == "Linux": subprocess.Popen("systemctl start docker", shell=True); return True
            elif plat == "Windows": subprocess.Popen("start \"\" \"C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe\"", shell=True); return True
        except: pass
        return False

    @staticmethod
    def list_containers():
        cmd = DockerManager.get_cmd('ps -a --format "{{.ID}}|{{.Image}}|{{.Status}}|{{.Names}}"')
        code, out, _ = DockerManager._docker_run(cmd, capture=True, timeout=20)
        if code != 0:
            return []
        containers = []
        for line in out.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                containers.append({"id": parts[0], "image": parts[1], "status": parts[2], "name": parts[3]})
        return containers

    @staticmethod
    def list_images():
        cmd = DockerManager.get_cmd('images --format "{{.Repository}}|{{.Tag}}|{{.Size}}|{{.ID}}"')
        code, out, _ = DockerManager._docker_run(cmd, capture=True, timeout=25)
        if code != 0:
            return []
        images = []
        for line in out.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 4:
                images.append({"repo": parts[0], "tag": parts[1], "size": parts[2], "id": parts[3]})
        return images

    @staticmethod
    def remove_image(image_id):
        cmd = DockerManager.get_cmd(f"rmi -f {image_id}")
        code, _, _ = DockerManager._docker_run(cmd, capture=False, timeout=120)
        return code == 0

    @staticmethod
    def get_logs(cid, tail=50):
        cmd = DockerManager.get_cmd(f"logs --tail {tail} {cid}")
        code, out, err = DockerManager._docker_run(cmd, capture=True, timeout=45)
        if code != 0:
            return "Connection error or remote container stopped."
        return (out or err or "").strip() or "Connection error or remote container stopped."

    @staticmethod
    def open_terminal(cid, host_override=None, cmd_override=None):
        plat = platform.system()
        base_exec = f"exec -it {cid}"
        shell = "/bin/bash" if (cmd_override and "bash" in str(cmd_override)) else "/bin/sh"
        
        # Eğer cmd_override varsa direkt o komutu çalıştır, yoksa shell aç
        inner_cmd = cmd_override if cmd_override else shell
        
        docker_cmd = DockerManager.get_cmd(f"{base_exec} {inner_cmd}", host_override=host_override)
        
        try:
            if plat == "Darwin":
                script = f"{docker_cmd}"
                cmd = f"osascript -e 'tell application \"Terminal\" to do script \"{script}\"' -e 'activate application \"Terminal\"'"
                subprocess.Popen(cmd, shell=True)
            elif plat == "Linux":
                terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
                for t in terminals:
                    if subprocess.run(f"which {t}", shell=True, capture_output=True).returncode == 0:
                        subprocess.Popen(f"{t} -e \"{docker_cmd}\"", shell=True)
                        return
            elif plat == "Windows":
                subprocess.Popen(f"start powershell.exe -NoExit -Command \"{docker_cmd}\"", shell=True)
        except Exception as e:
            log.error("Terminal launch error: %s", e)

class DockerCreationThread(QThread):
    result = Signal(str, bool)
    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd
    def run(self):
        try:
            p = subprocess.run(self.cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if p.returncode == 0: self.result.emit(p.stdout.strip(), True)
            else: self.result.emit(p.stderr.strip(), False)
        except Exception as e: self.result.emit(str(e), False)

class StatusCheckThread(QThread):
    status_signal = Signal(dict)
    def __init__(self, cid):
        super().__init__()
        self.cid = cid
    def run(self):
        res = {"running": False, "cpu": "0%", "ram": "0MB", "size": "0GB"}
        try:
            cmd_run = DockerManager.get_cmd(f"inspect -f '{{{{.State.Running}}}}' {self.cid}")
            code, out_run, _ = DockerManager._docker_run(cmd_run, capture=True, timeout=12)
            if code != 0:
                self.status_signal.emit(res)
                return
            out_run = (out_run or "").strip()
            res["running"] = out_run == "true"
            if res["running"]:
                cmd_stats = DockerManager.get_cmd(f"stats --no-stream --format '{{{{.CPUPerc}}}}|{{{{.MemUsage}}}}' {self.cid}")
                _, out_stats, _ = DockerManager._docker_run(cmd_stats, capture=True, timeout=18)
                out_stats = (out_stats or "").strip()
                if "|" in out_stats:
                    scpu, sram = out_stats.split("|")
                    res["cpu"] = scpu
                    res["ram"] = sram.split(" / ")[0]
                cmd_size = DockerManager.get_cmd(f"ps -s --filter id={self.cid} --format '{{{{.Size}}}}'")
                _, out_size, _ = DockerManager._docker_run(cmd_size, capture=True, timeout=15)
                out_size = (out_size or "").strip()
                if out_size:
                    res["size"] = out_size.split(" (")[0]
            self.status_signal.emit(res)
        except Exception:
            self.status_signal.emit(res)


class RemoteNodeStatusThread(QThread):
    """Background check: is the remote Jetson (camera host) reachable on Docker port."""
    status_signal = Signal(bool)  # True=online, False=offline

    def __init__(self, host):
        super().__init__()
        self.host = (host or "").strip()

    def run(self):
        ok = check_remote_node_reachable(self.host) if self.host else False
        self.status_signal.emit(ok)


class InspectionRuntimeRequestThread(QThread):
    result_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, url: str, method="GET", payload=None, timeout=3.0):
        super().__init__()
        self.url = str(url or "").strip()
        self.method = (method or "GET").strip().upper() or "GET"
        self.payload = payload if isinstance(payload, dict) else None
        self.timeout = max(0.5, float(timeout or 3.0))

    def run(self):
        if not self.url:
            self.error_signal.emit("Runtime URL is empty.")
            return
        headers = {"User-Agent": f"VisionDock/{APP_VERSION}"}
        body = None
        if self.payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(self.payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=headers, method=self.method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw or "{}")
            if not isinstance(data, dict):
                raise ValueError("Runtime response is not a JSON object.")
            self.result_signal.emit(data)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            self.error_signal.emit(detail or f"HTTP {exc.code}")
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            self.error_signal.emit(str(reason or exc))
        except Exception as exc:
            self.error_signal.emit(str(exc))


# =============================================================================
#  CUSTOM WIDGETS
# =============================================================================

class ToggleSwitch(QWidget):
    toggled = Signal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 32); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = True; self._thumb_pos = 31.0 
        self.anim = QPropertyAnimation(self, b"thumbPos")
        self.anim.setDuration(250); self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
    @Property(float)
    def thumbPos(self): return self._thumb_pos
    @thumbPos.setter
    def thumbPos(self, pos): self._thumb_pos = pos; self.update()
    def isChecked(self): return self._checked
    def setChecked(self, checked):
        if self._checked == bool(checked): return
        self._checked = bool(checked); self._thumb_pos = 31.0 if self._checked else 3.0; self.update()
    def mouseReleaseEvent(self, e):
        self._checked = not self._checked; self.toggled.emit(self._checked)
        self.anim.setStartValue(self._thumb_pos); self.anim.setEndValue(31.0 if self._checked else 3.0); self.anim.start()
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        wnd = self.window()
        dark = getattr(wnd, "is_dark", True)
        if self._checked:
            track = QColor("#3B82F6")
        else:
            track = QColor("#3A3A3C") if dark else QColor("#D1D1D6")
        p.setBrush(track); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 16, 16)
        p.setBrush(QColor("#FFFFFF")); p.drawEllipse(int(self._thumb_pos), 3, 26, 26)

class Toast(QWidget):
    def __init__(self, text, parent=None, is_dark=True):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        pal = ThemeOps.palette(is_dark)
        accent = "#3B82F6" if is_dark else "#2563EB"
        self.setStyleSheet(
            f"""
            QWidget#Toast {{
                background-color: {pal['toast_scrim']};
                border: 1px solid {pal['pop_edge']};
                border-radius: 14px;
                border-left: 3px solid {accent};
            }}
            QLabel#ToastText {{
                color: {pal['txt']};
                font-size: 13px;
                font-weight: 600;
                border: none;
                background: transparent;
                padding: 12px 24px 12px 20px;
            }}
            """
        )
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        self.lbl = QLabel(text)
        self.lbl.setObjectName("ToastText")
        self.lbl.setWordWrap(True)
        self.lbl.setMaximumWidth(440)
        l.addWidget(self.lbl)

        # Fade-in animation
        self._fade_in = QPropertyAnimation(self, b"windowOpacity")
        self._fade_in.setDuration(220)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Slide-up animation
        self._slide = QPropertyAnimation(self, b"pos")
        self._slide.setDuration(280)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Fade-out animation
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(600)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self.anim.finished.connect(self.hide)

        self.timer = QTimer()
        self.timer.timeout.connect(self.anim.start)
        self.timer.start(2800)

    def show_msg(self, x, y):
        self.move(x, y + 20)
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_in.start()
        self._slide.setStartValue(QPoint(x, y + 20))
        self._slide.setEndValue(QPoint(x, y))
        self._slide.start()

class FadeStackedWidget(QStackedWidget):
    """
    Safe page switcher (no QGraphicsOpacityEffect on heavy pages).

    NOTE:
    Previous implementation used QGraphicsOpacityEffect during transitions.
    On some Qt/driver combinations this can trigger repeated warnings:
      - QPainter::begin: A paint device can only be painted by one painter at a time.
      - QWidgetEffectSourcePrivate::pixmap: Painter not active
    To keep the UI stable and production-safe, we use direct switching.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._switching = False
        self._pending_index = -1

    def setCurrentIndex(self, index):
        if index == self.currentIndex():
            return
        if self._switching:
            self._pending_index = index
            return
        self._switching = True
        super().setCurrentIndex(index)
        self._switching = False
        self._flush_pending()

    def _flush_pending(self):
        if self._pending_index >= 0 and self._pending_index != self.currentIndex():
            idx = self._pending_index
            self._pending_index = -1
            self.setCurrentIndex(idx)


class LoadingSpinner(QWidget):
    """Smooth rotating arc spinner — use start()/stop() to show/hide."""

    def __init__(self, size=32, color="#3B82F6", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(16)  # ~60 fps
        self.setFixedSize(size, size)
        self.hide()

    def start(self):
        self._angle = 0
        self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sz = min(self.width(), self.height()) - 4
        rect = QRect((self.width() - sz) // 2, (self.height() - sz) // 2, sz, sz)
        # Track ring
        track = QColor(self._color)
        track.setAlpha(38)
        p.setPen(QPen(track, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 0, 360 * 16)
        # Spinner arc
        p.setPen(QPen(self._color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, (90 - self._angle) * 16, -270 * 16)


class DonutChart(QWidget):
    def __init__(self, title, color_hex, parent=None):
        super().__init__(parent)
        self.title = title
        self.base_color = QColor(color_hex)
        self._percent = 0
        self._anim_val = 0.0
        self.setFixedSize(160, 180)
        self._anim = QPropertyAnimation(self, b"animVal")
        self._anim.setDuration(700)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    @Property(float)
    def animVal(self):
        return self._anim_val

    @animVal.setter
    def animVal(self, v):
        self._anim_val = v
        self.update()

    def set_value(self, p):
        self._percent = p
        self._anim.stop()
        self._anim.setStartValue(self._anim_val)
        self._anim.setEndValue(float(p))
        self._anim.start()
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRect((self.width() // 2) - 50, (self.height() - 20) // 2 - 50, 100, 100)
        wnd = self.window()
        dark = getattr(wnd, "is_dark", True) if wnd is not None else True
        pal = ThemeOps.palette(dark)
        track = QColor(pal["sub"])
        track.setAlpha(52 if dark else 64)
        p.setPen(QPen(track, 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 0, 360 * 16)
        val = self._anim_val
        if val > 0:
            # Gradient arc color based on value
            arc_color = QColor(self.base_color)
            p.setPen(QPen(arc_color, 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, int(-val * 3.6 * 16))
        txt_color = self.palette().text().color()
        p.setPen(txt_color)
        f = QFont(self.font())
        f.setPointSize(22)
        f.setWeight(QFont.Weight.Bold)
        p.setFont(f)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{int(val)}%")
        f.setPointSize(11)
        f.setWeight(QFont.Weight.Normal)
        p.setFont(f)
        p.drawText(0, self.height() - 25, self.width(), 20, Qt.AlignmentFlag.AlignCenter, self.title)

class CardResizeHandle(QWidget):
    """Sağ alt köşede kart boyutunu sürükleyerek değiştirir; metin/tooltip yok, sadece hafif çizgiler."""
    GRIP = 32

    def __init__(self, card: "ResizableCard", overlay_parent: QWidget):
        super().__init__(overlay_parent)
        self._card = card
        self.setFixedSize(self.GRIP, self.GRIP)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self._drag_start = None

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_start = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_start is None:
            return
        c = self._card
        if c is None:
            return
        delta = e.globalPosition().toPoint() - self._drag_start
        new_w = max(c.minimumWidth(), c.width() + delta.x())
        new_h = max(c.minimumHeight(), c.height() + delta.y())
        c.resize(new_w, new_h)
        c.updateGeometry()
        if c.parentWidget() and c.parentWidget().layout():
            c.parentWidget().layout().activate()
        self._drag_start = e.globalPosition().toPoint()

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag_start = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        dark = True
        wnd = self.window()
        if wnd is not None:
            dark = getattr(wnd, "is_dark", True)
        mid = QColor("#FFFFFF") if dark else QColor("#000000")
        mid.setAlpha(255 if dark else 200)
        p.setPen(QPen(mid, 2.0))
        margin = 0
        for i in range(3):
            d = margin + i * 5
            p.drawLine(w - d, h - 2, w - 2, h - d)


class ResizableCard(QFrame):
    removed = Signal()
    trigger_delete_modal = Signal(object) 

    def __init__(self, title, sub, is_docker=False, container_id=None):
        super().__init__()
        self.setObjectName("Card")
        if is_docker:
            self.setMinimumSize(320, 240)
            self.resize(340, 240)
        else:
            self.setMinimumSize(320, 320)
            self.resize(420, 460)
        self._card_shadow = QGraphicsDropShadowEffect()
        self._card_shadow.setBlurRadius(40)
        self._card_shadow.setColor(QColor(18, 28, 48, 58))
        self._card_shadow.setOffset(0, 12)
        self.setGraphicsEffect(self._card_shadow)
        self.is_docker = is_docker; self.container_id = container_id; self.title_text = title; self.checker = None
        self.base_image = sub if is_docker else None # Store image tag for purging
        l = QVBoxLayout(self); l.setContentsMargins(0,0,0,0); l.setSpacing(0)
        h = QFrame(); h.setObjectName("CardHeader"); h.setFixedHeight(52)
        hl = QHBoxLayout(h); hl.setContentsMargins(16, 0, 12, 0); hl.setSpacing(10); hl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        
        self.l_title = QLabel(title.upper()); self.l_title.setObjectName("CardTitle"); hl.addWidget(self.l_title)
        
        # High-precision status indicators (Unified for AI/Docker/NVR)
        self.s_dot = QLabel("●"); self.s_dot.setFixedWidth(10); self.s_dot.setStyleSheet("background: transparent; border: none;")
        hl.addWidget(self.s_dot)
        self.s_txt = QLabel("READY"); self.s_txt.setObjectName("CardStatus"); hl.addWidget(self.s_txt)
        
        if not is_docker:
            self.rec_badge = QLabel("REC"); self.rec_badge.setObjectName("RecBadgeChip")
            self.rec_badge.hide(); hl.addWidget(self.rec_badge)
            self.fps_lbl = QLabel(""); self.fps_lbl.setObjectName("FpsLabel")
            self.fps_lbl.setFixedWidth(78); hl.addWidget(self.fps_lbl)

        hl.addStretch()
        
        if is_docker:
            self.l_btn = QPushButton("LOGS"); self.l_btn.setFixedSize(68, 28); self.l_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.l_btn.setObjectName("ShellBtn")
            self.l_btn.clicked.connect(self.show_logs); hl.addWidget(self.l_btn)
            
            self.t_btn = QPushButton("SHELL"); self.t_btn.setFixedSize(72, 28); self.t_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.t_btn.setObjectName("ShellBtn")
            self.t_btn.clicked.connect(lambda: DockerManager.open_terminal(self.container_id, getattr(self, "remote_host", None))); hl.addWidget(self.t_btn)
            
            self.ai_btn = QPushButton("AI LAUNCH"); self.ai_btn.setFixedSize(92, 28); self.ai_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.ai_btn.setObjectName("BtnSm")
            self.ai_btn.clicked.connect(self._show_ai_launch_menu); hl.addWidget(self.ai_btn)
        else:
            self._is_physical_camera = str(sub).split("|")[0].strip() == "Physical" if (sub and "|" in str(sub)) else False
            self.preview_btn = QPushButton("Start")
            self.preview_btn.setFixedSize(74, 32)
            self.preview_btn.setObjectName("BtnSecondary")
            self.preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.preview_btn.clicked.connect(self.toggle_preview_stream)
            hl.addWidget(self.preview_btn)
            self.snap_btn = QPushButton("Snapshot"); self.snap_btn.setFixedSize(98, 32); self.snap_btn.setObjectName("CardHeaderAction")
            self.snap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.snap_btn.clicked.connect(self.take_snapshot); hl.addWidget(self.snap_btn)
            self.menu_btn = QPushButton("..."); self.menu_btn.setObjectName("CardMenuBtn"); self.menu_btn.setFixedSize(40, 30); self.menu_btn.setToolTip("Camera options")
            self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.menu_btn.clicked.connect(self._show_camera_options_menu); hl.addWidget(self.menu_btn)
            
        c = QPushButton("×"); c.setObjectName("IconCloseSm"); c.setFixedSize(28,28); c.setCursor(Qt.CursorShape.PointingHandCursor); c.clicked.connect(lambda: self.trigger_delete_modal.emit(self)); hl.addWidget(c)
        l.addWidget(h)

        self.cnt = QWidget()
        self.cnt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        cl = QVBoxLayout(self.cnt)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        l.addWidget(self.cnt, 1)
        if is_docker:
            # Industrial Metadata Grid with Clean Identifier
            icon_box = QHBoxLayout(); icon_box.setSpacing(10)
            self.ico_lbl = QLabel("◈"); self.ico_lbl.setObjectName("AccentBadge"); self.ico_lbl.setFixedSize(36,36); self.ico_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            v_meta = QVBoxLayout(); v_meta.setSpacing(2)
            tag_display = (sub[:25] + "...") if len(sub) > 25 else sub
            self.l_img = QLabel(f"TAG: {tag_display}")
            self.l_img.setObjectName("DockerTagLine")
            self.l_img.setWordWrap(True); self.l_img.setMaximumWidth(280)
            v_meta.addWidget(self.l_img); icon_box.addLayout(v_meta); cl.addLayout(icon_box)
            
            m_box = QFrame(); m_box.setObjectName("MetaPanel")
            ml = QGridLayout(m_box); ml.setContentsMargins(12,12,12,12); ml.setSpacing(15); 
            def add_m(l, r, c, cs=1):
                v = QVBoxLayout(); v.setSpacing(4); lbl = QLabel(l.upper()); lbl.setObjectName("StatKey")
                val = QLabel("---"); val.setObjectName("StatValue"); v.addWidget(lbl); v.addWidget(val); ml.addLayout(v, r, c, 1, cs); return val
            self.m_cpu = add_m("USAGE", 0, 0); self.m_ram = add_m("MEMORY", 0, 1); self.m_disk = add_m("STORAGE", 1, 0, 2)
            cl.addWidget(m_box)
            if self.container_id: self.start_monitoring()
            self.grip = None
        else:
            # Professional Stream Cockpit
            mode, engine, res = "PHYSICAL", "STANDARD", "AUTO"
            parts = str(sub).split("|")
            if len(parts) == 3: mode, engine, res = parts
            elif len(parts) == 2: engine, res = parts
            
            bh = QHBoxLayout(); bh.setSpacing(8); bh.setContentsMargins(0, 0, 0, 0)

            def stream_badge(txt, role):
                t = str(txt).replace("📡", "").replace("🌐", "").replace("🤖", "").strip().upper()
                b = QLabel(t)
                oid = {"mode": "StreamBadgeMode", "engine": "StreamBadgeEngine", "profile": "StreamBadgeProfile"}.get(role, "StreamBadgeEngine")
                b.setObjectName(oid)
                return b

            bh.addWidget(stream_badge(mode, "mode"))
            self.eng_badge = stream_badge(engine, "engine")
            bh.addWidget(self.eng_badge)
            bh.addWidget(stream_badge(res, "profile"))
            
            self.rec_btn = QPushButton("REC"); self.rec_btn.setObjectName("RecToggle"); self.rec_btn.setFixedSize(52, 28); self.rec_btn.setCheckable(True)
            self.rec_btn.clicked.connect(self.toggle_recording)
            
            bh.addStretch(); bh.addWidget(self.rec_btn); cl.addLayout(bh)

            self.ai_meta = QLabel("Objects: 0")
            self.ai_meta.setObjectName("AiMetaLine")
            self.ai_meta.setWordWrap(True); self.ai_meta.setMaximumWidth(300)
            cl.addWidget(self.ai_meta)

            self._video_well = QFrame()
            self._video_well.setObjectName("VideoWell")
            vwl = QVBoxLayout(self._video_well)
            vwl.setContentsMargins(6, 6, 6, 6)
            vwl.setSpacing(0)
            self.view = QLabel("Preview paused. Click Start."); self.view.setObjectName("PreviewArea"); self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.view.setMinimumSize(1, 1)
            self._video_well.setMinimumHeight(200)
            vwl.addWidget(self.view)
            cl.addWidget(self._video_well, 1)
            self.grip = CardResizeHandle(self, self)
            self._video_well.installEventFilter(self)

    def _sync_card_shadow(self):
        eff = getattr(self, "_card_shadow", None)
        if eff is None:
            return
        w = self.window()
        dark = getattr(w, "is_dark", True) if w is not None else True
        if dark:
            eff.setColor(QColor(8, 14, 28, 82))
            eff.setBlurRadius(38)
            eff.setOffset(0, 11)
        else:
            eff.setColor(QColor(22, 38, 62, 18))
            eff.setBlurRadius(26)
            eff.setOffset(0, 7)

    def showEvent(self, e: QShowEvent):
        super().showEvent(e)
        self._sync_card_shadow()
        self._position_resize_handle()

    def eventFilter(self, obj, ev):
        if obj is getattr(self, "_video_well", None) and ev.type() == QEvent.Type.Resize:
            self._position_resize_handle()
        return super().eventFilter(obj, ev)

    def _show_ai_launch_menu(self):
        m = QMenu(self)
        actions = [
            ("Run YOLOv8 Inference", "python3 -m ultralytics predict model=yolov8n.pt source=0 show=False"),
            ("Run PyTorch CUDA Test", "python3 -c \"import torch; print('CUDA:', torch.cuda.is_available())\""),
            ("Run Camera Info (v4l2-ctl)", "v4l2-ctl --list-devices"),
            ("Run Model.py (Custom)", "python3 model.py"),
            ("Custom Command...", None)
        ]
        host = getattr(self, "remote_host", None)
        for label, cmd in actions:
            if cmd:
                a = m.addAction(label)
                a.triggered.connect(lambda checked, c=cmd: DockerManager.open_terminal(self.container_id, host, cmd_override=c))
            else:
                m.addSeparator()
                a = m.addAction(label)
                a.triggered.connect(self._prompt_custom_ai_cmd)
        m.exec(QCursor.pos())

    def _prompt_custom_ai_cmd(self):
        txt, ok = QInputDialog.getText(self, "Custom AI Command", "Enter command to run in container:", QLineEdit.EchoMode.Normal, "python3 ")
        if ok and txt.strip():
            DockerManager.open_terminal(self.container_id, getattr(self, "remote_host", None), cmd_override=txt.strip())

    def _position_resize_handle(self):
        g = getattr(self, "grip", None)
        well = getattr(self, "_video_well", None)
        if g is None or well is None:
            return
        sz = CardResizeHandle.GRIP
        g.move(self.width() - sz - 5, self.height() - sz - 5)
        g.raise_(); g.show()

    def resizeEvent(self, e):
        self._position_resize_handle()
        if getattr(self, "_last_frame", None) is not None and hasattr(self, "view") and not self.is_docker:
            last = self._last_frame.copy()
            card_ref = self
            def _redraw():
                if getattr(card_ref, "view", None) and getattr(card_ref, "_last_frame", None) is not None:
                    card_ref.upd_img(last)
            QTimer.singleShot(0, _redraw)
        super().resizeEvent(e)
        if not self.is_docker:
            app = self.window()
            src = getattr(self, "_layout_persist_src", None)
            if app is not None and src and hasattr(app, "_schedule_persist_camera_card_geom"):
                app._schedule_persist_camera_card_geom(src, self.width(), self.height())

    def sizeHint(self): return self.size()
    def perform_delete(self, purge_image=False):
        if purge_image and is_production_mode():
            log.info("Production mode: skipping base image purge")
            purge_image = False
        # Stop recording timer before destroying card
        rt = getattr(self, "_rec_timer", None)
        if rt is not None and rt.isActive():
            rt.stop()
        if hasattr(self, "t") and getattr(self, "t", None):
            self.t.stop()
        ck = getattr(self, "checker", None)
        if ck is not None:
            if ck.isRunning():
                ck.quit()
                ck.wait(2000)
            self.checker = None
        if self.is_docker and self.container_id:
            cmd_rm = DockerManager.get_cmd(f"rm -f {self.container_id}")
            DockerManager._docker_run(cmd_rm, capture=False, timeout=30)
            if purge_image and hasattr(self, 'base_image') and self.base_image:
                cmd_rmi = DockerManager.get_cmd(f"rmi -f {self.base_image}")
                DockerManager._docker_run(cmd_rmi, capture=False, timeout=120)
            if hasattr(self, 'db'): self.db.remove_workspace(self.container_id)
        else:
            if hasattr(self, 'db'): self.db.remove_camera(self.sub_val)
            app = self.window()
            if app is not None and hasattr(app, "forget_camera_card_geom") and getattr(self, "sub_val", None):
                app.forget_camera_card_geom(self.sub_val)
            if app is not None and hasattr(app, "_refresh_camera_preview_summary"):
                app._refresh_camera_preview_summary()
        self.removed.emit()
    def set_status_info(self, text, color):
        self.s_dot.setStyleSheet(f"color: {color}; font-size: 10px; border:none; background:transparent;")
        self.s_txt.setText(text)
        self.s_txt.setStyleSheet(f"color: {color}; font-size: 9px; font-weight:800; letter-spacing:0.8px; border:none; background:transparent;")
    def start_monitoring(self):
        if self.checker and self.checker.isRunning(): return
        self.checker = StatusCheckThread(self.container_id); self.checker.status_signal.connect(self.update_status_from_thread); self.checker.start()
    def update_status_from_thread(self, s):
        if not hasattr(self, 'm_cpu'): return
        is_r = s["running"]; self.set_status_info("Running" if is_r else "Stopped", "#30D158" if is_r else "#FF453A")
        if is_r: self.m_cpu.setText(s['cpu']); self.m_ram.setText(s['ram']); self.m_disk.setText(s['size'])
        else: self.m_cpu.setText("0.00%"); self.m_ram.setText("0MB"); self.m_disk.setText(s['size'])
        if self.isVisible(): QTimer.singleShot(3000, self.start_monitoring)
    def toggle_recording(self, checked):
        if hasattr(self, 't'):
            self.t.toggle_record(checked)
            if checked:
                self.rec_badge.show()
                self._rec_start_time = time.monotonic()
                if not hasattr(self, '_rec_timer'):
                    self._rec_timer = QTimer(self)
                    self._rec_timer.timeout.connect(self._update_rec_time)
                self._rec_timer.start(1000)
                if hasattr(self, '_video_well'):
                    self._video_well.setObjectName("VideoWellRecording")
                    self._video_well.style().unpolish(self._video_well)
                    self._video_well.style().polish(self._video_well)
            else:
                self.rec_badge.hide()
                if hasattr(self, '_rec_timer'):
                    self._rec_timer.stop()
                self.rec_btn.setText("REC")
                if hasattr(self, '_video_well'):
                    self._video_well.setObjectName("VideoWell")
                    self._video_well.style().unpolish(self._video_well)
                    self._video_well.style().polish(self._video_well)

    def update_ai_ui(self, meta):
        if not hasattr(self, 'ai_meta'):
            return
        count = meta.get('objects', 0)
        classes = meta.get('classes') or {}
        if classes:
            parts = [f"{v} {k}" for k, v in sorted(classes.items(), key=lambda x: -x[1])[:5]]
            text = ", ".join(parts)
        else:
            text = f"Objects: {count}" if count else "Objects: 0"
        self.ai_meta.setText(text)
        self.ai_meta.setObjectName("AiMetaLineActive" if count > 0 else "AiMetaLine")
        prev = getattr(self, "_toast_detection_prev", 0)
        if _env_truthy("VISIONDOCK_TOAST_DETECTIONS") and count > 0 and prev == 0:
            try:
                app = self.window()
                if app and hasattr(app, "show_toast"):
                    app.show_toast(f"Detection: {self.title_text}")
            except Exception:
                pass
        self._toast_detection_prev = count

    def _update_rec_time(self):
        if not hasattr(self, '_rec_start_time'):
            return
        elapsed = int(time.monotonic() - self._rec_start_time)
        m, s = divmod(elapsed, 60)
        self.rec_btn.setText(f"REC {m:02d}:{s:02d}")

    def update_fps(self, fps):
        lbl = getattr(self, "fps_lbl", None)
        if lbl is None:
            return
        if fps < 12:
            lbl.setText(f"{fps:.1f} FPS")
        else:
            lbl.setText(f"{fps:.0f} FPS")

    def update_connection_status(self, status):
        colors = {
            "connecting":   ("#FF9F0A", "CONNECTING"),
            "connected":    ("#30D158", "LIVE"),
            "reconnecting": ("#FF9F0A", "RECONNECTING"),
        }
        color, text = colors.get(status, ("#8E8E93", status.upper()))
        self.set_status_info(text, color)
        if hasattr(self, "preview_btn"):
            self.preview_btn.setText("Stop" if status == "connected" else "Start")

    def toggle_preview_stream(self):
        app = self.window()
        if app is None:
            return
        is_running = bool(getattr(self, "t", None) and getattr(self.t, "isRunning", lambda: False)())
        if is_running and hasattr(app, "_stop_camera_preview"):
            app._stop_camera_preview(self)
        elif hasattr(app, "_start_camera_preview"):
            app._start_camera_preview(self)

    def on_snap_done(self, path):
        app = self.window()
        if app and hasattr(app, "show_toast"):
            app.show_toast(f"Snapshot: {os.path.basename(path)}")
        btn = getattr(self, "snap_btn", None)
        if btn:
            orig_text = btn.text()
            btn.setText("Saved")
            btn.setStyleSheet("color: #30D158; font-weight: 800;")
            QTimer.singleShot(1500, lambda: [btn.setText(orig_text), btn.setStyleSheet("")])

    def take_snapshot(self):
        if hasattr(self, 't'): self.t.snapshot()

    def _show_camera_options_menu(self):
        old = getattr(self, "_ctx_menu_popup", None)
        if old is not None:
            try:
                old.close()
                old.deleteLater()
            except Exception:
                pass
        app_win = self.window()
        dark = getattr(app_win, "is_dark", True)
        pop = QFrame()
        pop.setObjectName("ContextMenuShell")
        pop.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        pop.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._ctx_menu_popup = pop
        outer = QVBoxLayout(pop)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("ContextMenuPanel")
        panel.setFixedWidth(268)
        sh = QGraphicsDropShadowEffect(panel)
        sh.setBlurRadius(26)
        sh.setOffset(0, 8)
        sh.setColor(QColor(0, 0, 0, 88 if dark else 52))
        panel.setGraphicsEffect(sh)
        vl = QVBoxLayout(panel)
        vl.setContentsMargins(8, 10, 8, 10)
        vl.setSpacing(0)
        cap = QLabel("CAMERA")
        cap.setObjectName("ContextMenuCaption")
        vl.addWidget(cap)

        def add_item(text, handler):
            b = QPushButton(text)
            b.setObjectName("ContextMenuItem")
            b.setCursor(Qt.CursorShape.PointingHandCursor)

            def go():
                pop.close()
                pop.deleteLater()
                self._ctx_menu_popup = None
                handler()

            b.clicked.connect(go)
            vl.addWidget(b)

        if getattr(self, "_is_physical_camera", False):
            add_item("Focus...", self._open_focus_dialog)
        is_running = bool(getattr(self, "t", None) and self.t.isRunning())
        if is_running:
            add_item("Stop preview", lambda: self.window() and hasattr(self.window(), "_stop_camera_preview") and self.window()._stop_camera_preview(self))
        else:
            add_item("Start preview", lambda: self.window() and hasattr(self.window(), "_start_camera_preview") and self.window()._start_camera_preview(self))
        if "://" in str(getattr(self, "sub_val", "")):
            add_item("Stream preview (resolution / FPS)...", self._open_stream_tuning_dialog)
        add_item("More in Settings...", self._open_settings_tab)
        outer.addWidget(panel)
        app = QApplication.instance()
        if app:
            pop.setStyleSheet(app.styleSheet())
        pop.adjustSize()
        g = self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomLeft())
        pop.move(g + QPoint(0, 6))
        pop.show()

    def _open_settings_tab(self):
        app = self.window()
        if app and hasattr(app, "switch"):
            app.switch(5)
        elif app and hasattr(app, "tabs") and hasattr(app.tabs, "setCurrentIndex"):
            app.tabs.setCurrentIndex(5)
        if app and hasattr(app, "_settings_tabs"):
            try:
                app._settings_tabs.setCurrentIndex(0)
            except Exception:
                pass
        if app and hasattr(app, "show_toast"):
            app.show_toast("Opened Settings")

    def _open_stream_tuning_dialog(self):
        app_win = self.window()
        if app_win is None or not hasattr(app_win, "_apply_stream_tuning"):
            return
        ov = _parse_stream_meta_overrides(getattr(self, "stream_meta", "") or "")
        cur_wh = ov.get("stream_pv")
        cur_fps = int(ov.get("stream_max_fps") or 0)
        src = str(getattr(self, "sub_val", "") or "")

        cached = getattr(self, "_last_native_wh", None)
        nw = nh = None
        fps_hint = 0.0
        if cached and isinstance(cached, (tuple, list)) and len(cached) == 2:
            try:
                nw, nh = int(cached[0]), int(cached[1])
            except (TypeError, ValueError):
                nw = nh = None
        if nw is None or nh is None:
            nw, nh, fps_hint = _probe_stream_native_geometry(src)
        else:
            nw, nh, fps_hint = nw, nh, 0.0

        dlg = QDialog(app_win)
        dlg.setWindowTitle("Stream preview")
        dlg.setFixedWidth(448)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        lay.setContentsMargins(18, 18, 18, 16)
        det = ""
        if nw and nh:
            det = f"<br><b>Detected source</b> — {nw}×{nh} (downsized options keep aspect ratio)."
            if fps_hint >= 5:
                det += f" Estimated source FPS: ~{fps_hint:.0f}."
        else:
            det = (
                "<br><i>Source resolution could not be measured</i> (network / short timeout). "
                "Generic 16:9 presets are shown; opening this menu while stream is active usually improves probing."
            )
        info = QLabel(
            "<b>Preview resolution</b> — scales on the <i>client side</i>; "
            "does not change Jetson encoder output.<br>"
            "<b>FPS limit</b> — 0 = maximum smoothness; limit when CPU usage is high."
            + det
        )
        info.setWordWrap(True)
        info.setObjectName("CaptionMuted")
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)
        combo = QComboBox()
        combo.setFixedHeight(36)
        if nw and nh:
            combo.addItem(f"Full source — {nw}×{nh} (no resampling)", None)
            for w, h in _preview_resolution_choices(nw, nh):
                pct = max(1, min(99, int(round(100.0 * w * h / (nw * nh)))))
                combo.addItem(f"Preview {w}×{h}  (~%{pct})", (w, h))
        else:
            combo.addItem("Source — no resize", None)
            for label, wh in (
                ("1280 × 720", (1280, 720)),
                ("960 × 540", (960, 540)),
                ("854 × 480", (854, 480)),
                ("640 × 480", (640, 480)),
                ("640 × 360", (640, 360)),
            ):
                combo.addItem(label, wh)
        if cur_wh and len(cur_wh) == 2:
            found = False
            for i in range(combo.count()):
                d = combo.itemData(i)
                if d is not None and d[0] == cur_wh[0] and d[1] == cur_wh[1]:
                    found = True
                    break
            if not found:
                w, h = int(cur_wh[0]), int(cur_wh[1])
                combo.addItem(f"Saved setting {w}×{h}", (w, h))
        sel_ix = 0
        for i in range(combo.count()):
            d = combo.itemData(i)
            if d is None and cur_wh is None:
                sel_ix = i
                break
            if d is not None and cur_wh is not None and d[0] == cur_wh[0] and d[1] == cur_wh[1]:
                sel_ix = i
                break
        combo.setCurrentIndex(sel_ix)
        lay.addWidget(QLabel("Preview resolution"))
        lay.addWidget(combo)
        lay.addWidget(QLabel("Target FPS limit (0 = unlimited)"))
        spin = QSpinBox()
        spin.setRange(0, 120)
        if fps_hint >= 8:
            spin.setMaximum(max(120, int(fps_hint) + 20))
            spin.setToolTip(f"Source reported ~{fps_hint:.0f} fps; tune upper limit accordingly.")
        spin.setValue(cur_fps)
        spin.setFixedHeight(36)
        lay.addWidget(spin)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_b = bb.button(QDialogButtonBox.StandardButton.Ok)
        ok_b.setText("Apply")
        ok_b.setObjectName("BtnPrimary")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        app_win._apply_stream_tuning(self, combo.currentData(), spin.value())

    def _open_focus_dialog(self):
        """Floating focus dialog; colors follow ThemeOps (same layers as main UI)."""
        app_win = self.window()
        d = get_camera_defaults()
        dark = getattr(app_win, "is_dark", True)
        sensor_model = str(d.get("sensor_model") or "GENERIC_CSI").strip().upper()
        focuser_type = str(d.get("focuser_type") or csi_sensor_default_focuser(sensor_model)).strip().lower()
        scripts = focus_scripts_for_focuser(focuser_type)
        focus_enabled = focuser_available(focuser_type)

        dlg = QDialog(app_win)
        dlg.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        dlg.setModal(False)
        dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        dlg.setFixedWidth(428)

        root = QFrame(dlg)
        root.setObjectName("FocusPopupRoot")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sh = QGraphicsDropShadowEffect(root)
        sh.setBlurRadius(14)
        sh.setOffset(0, 5)
        sh.setColor(QColor(0, 0, 0, 88 if dark else 52))
        root.setGraphicsEffect(sh)

        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.addWidget(root)

        root_ly = QVBoxLayout(root)
        root_ly.setContentsMargins(0, 0, 0, 0)
        root_ly.setSpacing(0)
        accent = QFrame()
        accent.setObjectName("FocusAccentBar")
        accent.setFixedHeight(4)
        root_ly.addWidget(accent)

        shell = QWidget()
        layout = QVBoxLayout(shell)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(16)
        root_ly.addWidget(shell)

        t = QLabel("Focus (CSI / I2C)")
        t.setObjectName("FocusDlgTitle")
        layout.addWidget(t)
        sub = QLabel(
            f"{csi_sensor_label(sensor_model)}. {focuser_label(focuser_type)}. "
            + ("Set I2C bus and lens position." if focus_enabled else "This configuration is currently fixed lens.")
        )
        sub.setObjectName("FocusDlgSubtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        bus_row = QHBoxLayout()
        bus_lbl = QLabel("I2C bus")
        bus_lbl.setObjectName("FocusDlgMuted")
        bus_row.addWidget(bus_lbl)
        bus_row.addStretch()
        bus_spin = QSpinBox()
        bus_spin.setRange(0, 15)
        bus_spin.setValue(int(d.get("i2c_bus", 10)))
        bus_spin.setFixedWidth(100)
        bus_spin.setMinimumHeight(42)
        bus_spin.setEnabled(focus_enabled)
        bus_row.addWidget(bus_spin)
        layout.addLayout(bus_row)

        body = QFrame()
        body.setObjectName("FocusInner")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(16, 16, 16, 16)
        bl.setSpacing(14)

        pos_row = QHBoxLayout()
        pos_lbl = QLabel("Position (0 = far ... 1023 = near)")
        pos_lbl.setObjectName("FocusDlgMuted")
        pos_val = QLabel(str(int(d.get("focus_position", 512))))
        pos_val.setObjectName("FocusValueChip")
        pos_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pos_row.addWidget(pos_lbl, 1)
        pos_row.addWidget(pos_val, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bl.addLayout(pos_row)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("FocusSlider")
        slider.setRange(0, 1023)
        slider.setValue(int(d.get("focus_position", 512)))
        slider.valueChanged.connect(lambda v: pos_val.setText(str(int(v))))
        slider.setEnabled(focus_enabled)
        bl.addWidget(slider)
        layout.addWidget(body)

        layout.addWidget(hairline())

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("FocusSecondaryBtn")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(dlg.close)
        btn_row.addWidget(cancel)
        btn_row.addStretch()

        def apply_focus():
            if not focus_enabled:
                dlg.close()
                return
            pos = slider.value()
            bus = bus_spin.value()
            set_camera_defaults(focus_position=pos, i2c_bus=bus)
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            focus_name = scripts.get("focus") or ""
            script_path = os.path.join(base, "scripts", focus_name) if focus_name else ""
            if focus_name and os.path.exists(script_path):
                ok, msg = _run_script_checked(
                    [sys.executable, script_path, "--bus", str(bus), "--position", str(pos)],
                    cwd=base,
                    timeout=10,
                )
                if ok:
                    if app_win and hasattr(app_win, "show_toast"):
                        app_win.show_toast(f"Focus set to {pos} (bus {bus})")
                else:
                    QMessageBox.warning(
                        self,
                        "Focus",
                        "Focus script failed (check I2C bus, i2cset permissions; docs/CSI_CAMERA_FOCUS.md).\n\n"
                        + (msg or "Unknown error"),
                    )
            else:
                QMessageBox.information(self, "Focus", "Focus script for the selected focuser was not found.")
            dlg.close()

        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("FocusPrimaryBtn")
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(apply_focus)
        apply_btn.setEnabled(focus_enabled)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)

        app_inst = QApplication.instance()
        if app_inst:
            dlg.setStyleSheet(app_inst.styleSheet())

        dlg.adjustSize()
        anchor = self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomRight())
        dlg.move(anchor - QPoint(dlg.width(), 0))
        dlg.show()

    def show_logs(self):
        logs = DockerManager.get_logs(self.container_id)
        msg = QMessageBox(self)
        msg.setObjectName("LogViewerBox")
        msg.setWindowTitle(f"Logs: {self.title_text}")
        max_log_len = 12000
        if len(logs) > max_log_len:
            logs = logs[-max_log_len:] + "\n\n... [truncated]"
        msg.setText(logs)
        wnd = self.window()
        dark = getattr(wnd, "is_dark", True) if wnd is not None else True
        pal = ThemeOps.palette(dark)
        app = QApplication.instance()
        base = app.styleSheet() if app else ""
        extra = f"""
        QMessageBox#LogViewerBox {{ background-color: {pal['card']}; }}
        QMessageBox#LogViewerBox QLabel {{
            font-family: 'SF Mono', 'Consolas', 'Menlo', monospace;
            font-size: 11px;
            color: {pal['txt']};
            min-width: 440px;
            max-width: 680px;
        }}
        QMessageBox#LogViewerBox QPushButton {{
            min-width: 88px;
            padding: 8px 16px;
            border-radius: 10px;
            background-color: {pal['ibg']};
            color: {pal['txt']};
            border: 1px solid {pal['pop_edge']};
            font-weight: 600;
            font-size: 12px;
        }}
        QMessageBox#LogViewerBox QPushButton:hover {{ border-color: #3B82F6; color: #3B82F6; }}
        """
        msg.setStyleSheet(base + extra)
        for lbl in msg.findChildren(QLabel):
            lbl.setWordWrap(True)
            lbl.setMaximumWidth(680)
        msg.exec()

    def upd_img(self, img):
        if not hasattr(self, 'view') or img is None or img.size == 0:
            return
        try:
            if len(img.shape) != 3 or img.shape[2] != 3:
                return
            img_rgb = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            h, w, c = img_rgb.shape
            if h < 1 or w < 1:
                return
            bpl = int(img_rgb.strides[0])
            qimg = QImage(img_rgb.data, w, h, bpl, QImage.Format.Format_RGB888).copy()
            target = self.view.size()
            if target.width() < 32 or target.height() < 24:
                target = QSize(320, 240)
            pix = QPixmap.fromImage(qimg).scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            r = max(8, min(18, min(pix.width(), pix.height()) // 12))
            self.view.setPixmap(rounded_pixmap(pix, r))
            if not self.is_docker:
                self._last_frame = img.copy()
                self._last_native_wh = (int(w), int(h))
        except Exception:
            pass

# =============================================================================
#  UI HELPERS & COMPONENTS
# =============================================================================

class CollapsibleSection(QFrame):
    """A professional collapsible panel for decluttering industrial UIs."""
    def __init__(self, title, parent=None, is_collapsed=False):
        super().__init__(parent)
        self.setObjectName("CollapsibleSection")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        # Header
        self.header = QFrame()
        self.header.setObjectName("CollapsibleHeader")
        self.header.setFixedHeight(40)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(15, 0, 15, 0)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("CollapsibleTitle")
        
        self.toggle_btn = QLabel("▼") # Arrow indicator
        self.toggle_btn.setObjectName("CollapsibleArrow")
        
        hl.addWidget(self.title_label)
        hl.addStretch()
        hl.addWidget(self.toggle_btn)

        # Content area
        self.content = QFrame()
        self.content.setObjectName("CollapsibleContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        self.layout.addWidget(self.header)
        self.layout.addWidget(self.content)

        self.header.mousePressEvent = self.toggle
        
        if is_collapsed:
            self.content.setVisible(False)
            self.toggle_btn.setText("▶")

    def toggle(self, event=None):
        if self.content.isVisible():
            self.content.setVisible(False)
            self.toggle_btn.setText("▶")
        else:
            self.content.setVisible(True)
            self.toggle_btn.setText("▼")

    def addWidget(self, widget):
        self.content_layout.addWidget(widget)

    def addLayout(self, layout):
        self.content_layout.addLayout(layout)

# =============================================================================
#  UI LAYOUT & THEME
# =============================================================================

class ThemeOps:
    """Single source palette + QSS — Obsidian Dark / Pearl Light premium themes."""

    @staticmethod
    def palette(is_dark: bool) -> dict:
        if is_dark:
            # ── Obsidian Dark ─────────────────────────────────────────────────
            return {
                "bg":                   "#0B0F17",   # Softer deep navy base
                "canvas":               "#101622",   # Main canvas
                "sb":                   "#0D1420",   # Sidebar shell
                "card":                 "#141D2B",   # Card shell
                "txt":                  "#E8EEFF",   # Cool white text
                "sub":                  "#93A1BC",   # Muted copy
                "brd":                  "rgba(255,255,255,0.10)",
                "ibg":                  "#182233",
                "hov":                  "rgba(96,165,250,0.11)",
                "ihov":                 "#1C2A3E",
                "ov_bg":                "rgba(5,8,15,0.92)",
                "card_txt":             "#EEF2FF",
                "preview_bg":           "#0D1420",
                "preview_br":           "rgba(132,170,235,0.30)",
                "surface_row":          "#162133",
                "surface_row_compact":  "#121B2A",
                "hairline":             "#283851",
                "card_header_bg":       "#172235",
                "meta_panel_bg":        "#111B2B",
                "accent_soft_bg":       "rgba(59,130,246,0.12)",
                "accent_soft_br":       "rgba(99,160,255,0.32)",
                "ctx_menu_bg":          "#152033",
                "ctx_menu_hi":          "rgba(59,130,246,0.16)",
                "pop_edge":             "#30455F",
                "zt_inset":             "#111B2B",
                "focus_shell_top":      "#1A2740",
                "focus_shell_bot":      "#121C2E",
                "focus_border_hi":      "#6BA3FF",
                "focus_panel":          "rgba(255,255,255,0.05)",
                "focus_panel_br":       "rgba(107,163,255,0.28)",
                "accent_net":           "#34D399",   # Emerald — ZT IP / online
                "toast_scrim":          "rgba(5,8,15,0.96)",
            }
        # ── Pearl Light ───────────────────────────────────────────────────────
        return {
            "bg":                   "#F3F6FB",   # Soft cool gray
            "canvas":               "#F8FAFD",   # Main canvas
            "sb":                   "#EAF0F8",   # Sidebar shell
            "card":                 "#FFFFFF",   # Pure white card
            "txt":                  "#0F172A",   # Deep navy text
            "sub":                  "#596980",   # Secondary copy
            "brd":                  "rgba(15,23,42,0.10)",
            "ibg":                  "#F4F7FC",
            "hov":                  "rgba(37,99,235,0.07)",
            "ihov":                 "#EDF3FB",
            "ov_bg":                "rgba(240,245,255,0.90)",
            "card_txt":             "#1E293B",
            "preview_bg":           "#EEF3FA",
            "preview_br":           "rgba(37,99,235,0.20)",
            "surface_row":          "#F2F6FC",
            "surface_row_compact":  "#EDF2F9",
            "hairline":             "#D5DFEC",
            "card_header_bg":       "#F4F8FD",
            "meta_panel_bg":        "#F1F6FC",
            "accent_soft_bg":       "rgba(37,99,235,0.08)",
            "accent_soft_br":       "rgba(37,99,235,0.22)",
            "ctx_menu_bg":          "#FFFFFF",
            "ctx_menu_hi":          "rgba(37,99,235,0.10)",
            "pop_edge":             "#C2CFDF",
            "zt_inset":             "#EFF4FB",
            "focus_shell_top":      "#FFFFFF",
            "focus_shell_bot":      "#EFF4FB",
            "focus_border_hi":      "#3B82F6",
            "focus_panel":          "rgba(255,255,255,0.98)",
            "focus_panel_br":       "rgba(37,99,235,0.18)",
            "accent_net":           "#059669",   # Emerald-600 — ZT IP / online
            "toast_scrim":          "rgba(255,255,255,0.98)",
        }

    tokens = palette

    @staticmethod
    def get_style(is_dark):
        p = ThemeOps.palette(is_dark)
        bg = p["bg"]
        canvas = p["canvas"]
        sb = p["sb"]
        card = p["card"]
        txt = p["txt"]
        sub = p["sub"]
        brd = p["brd"]
        ibg = p["ibg"]
        hov = p["hov"]
        ihov = p["ihov"]
        ov_bg = p["ov_bg"]
        card_txt = p["card_txt"]
        preview_bg = p["preview_bg"]
        preview_br = p["preview_br"]
        preview_inset = "#0A0D14" if is_dark else preview_bg
        surface_row = p["surface_row"]
        surface_row_compact = p["surface_row_compact"]
        hairline = p["hairline"]
        card_header_bg = p["card_header_bg"]
        meta_panel_bg = p["meta_panel_bg"]
        accent_soft_bg = p["accent_soft_bg"]
        accent_soft_br = p["accent_soft_br"]
        ctx_menu_bg = p["ctx_menu_bg"]
        ctx_menu_hi = p["ctx_menu_hi"]
        pop_edge = p["pop_edge"]
        zt_inset = p["zt_inset"]
        focus_shell_top = p["focus_shell_top"]
        focus_shell_bot = p["focus_shell_bot"]
        focus_border_hi = p["focus_border_hi"]
        focus_panel = p["focus_panel"]
        focus_panel_br = p["focus_panel_br"]
        accent_net = p["accent_net"]
        # ── Premium accent tokens ─────────────────────────────────────────────
        accent        = "#4A86E8" if is_dark else "#2F6FD3"
        accent_vivid  = "#5F7DEB" if is_dark else "#4A69DF"
        accent_hover  = "#76A5F0" if is_dark else "#5A8CE8"
        accent_pressed= "#2D56B4" if is_dark else "#2758B8"
        accent_deep   = "#365FC8" if is_dark else "#2C5FC5"
        nav_idle      = "#A4B2CB" if is_dark else "#526176"
        search_ring   = "rgba(95,125,235,0.24)" if is_dark else "rgba(74,105,223,0.20)"
        card_hover_brd= "rgba(95,125,235,0.36)" if is_dark else "rgba(74,105,223,0.28)"
        if is_dark:
            sb_m_txt, sb_m_br, sb_m_bg = "#CBD6FF", "rgba(120,146,236,0.38)", "rgba(95,125,235,0.11)"
            sb_e_txt, sb_e_br, sb_e_bg = "#93C5FD", "rgba(99,160,255,0.40)",  "rgba(59,130,246,0.12)"
            sb_p_txt, sb_p_br, sb_p_bg = "#86EFAC", "rgba(52,211,153,0.42)",  "rgba(16,185,129,0.12)"
            docker_tag_txt = "#60A5FA"
            ai_hot         = "#3B82F6"
        else:
            sb_m_txt, sb_m_br, sb_m_bg = "#3448A8", "rgba(52,72,168,0.28)",  "rgba(52,72,168,0.07)"
            sb_e_txt, sb_e_br, sb_e_bg = "#1D4ED8", "rgba(37,99,235,0.28)",  "rgba(37,99,235,0.08)"
            sb_p_txt, sb_p_br, sb_p_bg = "#047857", "rgba(4,120,87,0.28)",   "rgba(5,150,105,0.10)"
            docker_tag_txt = "#1D4ED8"
            ai_hot         = "#2563EB"

        return f"""
        QMainWindow {{ background-color: {bg}; }}
        QWidget {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'SF Pro Display', Roboto, 'Helvetica Neue', sans-serif; color: {txt}; font-size: 13px; letter-spacing: 0.12px; }}
        QWidget#MainCanvas {{ background-color: {canvas}; }}
        QFrame#Sidebar {{ background-color: {sb}; border-right: 1px solid {brd}; }}
        QFrame#Sidebar[compact="true"] {{ border-right: 1px solid {pop_edge}; }}
        QLabel#SidebarScopeBadge {{
            color: {accent};
            border: 1px solid {accent_soft_br};
            background-color: {accent_soft_bg};
            border-radius: 10px;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.08em;
            padding: 5px 8px;
        }}
        QLabel#CollapsibleTitle {{
            font-size: 13px;
            font-weight: 700;
            color: {sub};
            border: none;
            background: transparent;
        }}
        QLabel#CollapsibleArrow {{
            font-size: 12px;
            color: {sub};
            border: none;
            background: transparent;
        }}
        QLabel#RoleLockHint {{
            color: {sub};
            font-size: 11px;
            font-weight: 700;
            border: 1px solid {pop_edge};
            border-radius: 10px;
            background-color: {meta_panel_bg};
            padding: 6px 10px;
        }}
        QFrame#Card, QFrame#InfoCard {{ background-color: {card}; border: 1px solid {pop_edge}; border-radius: 18px; }}
        QFrame#Card:hover, QFrame#InfoCard:hover {{ border-color: {card_hover_brd}; }}
        QTabWidget#SettingsTabs::pane {{ border: none; background: transparent; margin-top: 10px; }}
        QTabWidget#SettingsTabs QTabBar::tab {{
            background-color: {hov};
            color: {sub};
            border: 1px solid {brd};
            border-radius: 12px;
            padding: 10px 18px;
            margin-right: 8px;
            font-size: 12px;
            font-weight: 700;
            min-height: 18px;
        }}
        QTabWidget#SettingsTabs QTabBar::tab:selected {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:0.64 {accent_vivid}, stop:1 {accent_deep});
            color: #FFFFFF;
            border-color: rgba(255,255,255,0.18);
        }}
        QTabWidget#SettingsTabs QTabBar::tab:hover:!selected {{ border-color: {accent}; color: {accent}; }}
        QPushButton#SettingsHubBtn {{
            background-color: {hov};
            color: {txt};
            border: 1px solid {brd};
            border-radius: 14px;
            padding: 12px 14px;
            text-align: left;
            font-size: 11px;
            font-weight: 620;
            min-height: 82px;
        }}
        QPushButton#SettingsHubBtn:hover {{
            border-color: {accent_soft_br};
            background-color: {surface_row};
        }}
        QPushButton#SettingsHubBtn:pressed {{
            background-color: {meta_panel_bg};
            border-color: {accent};
        }}
        QFrame#SettingsSummaryCard {{
            background-color: {surface_row};
            border: 1px solid {pop_edge};
            border-radius: 16px;
        }}
        QFrame#ModalBox {{ background-color: {card}; border: 1px solid {pop_edge}; border-radius: 18px; }}
        QFrame#CardHeader {{ background-color: {card_header_bg}; border-top-left-radius: 16px; border-top-right-radius: 16px; border-bottom: 1px solid {pop_edge}; }}
        QFrame#MetaPanel {{ background-color: {meta_panel_bg}; border: 1px solid {pop_edge}; border-radius: 12px; }}
        QFrame#MediaCard {{ background-color: {surface_row}; border: 1px solid {pop_edge}; border-radius: 18px; }}
        QFrame#MediaCard:hover {{ border-color: {card_hover_brd}; }}
        QLabel#MediaThumb {{ background-color: {preview_bg}; border: 1px solid {pop_edge}; border-radius: 14px; color: {sub}; }}
        QLabel#MediaThumbPlaceholder {{
            background-color: {meta_panel_bg}; border: 2px dashed {pop_edge}; border-radius: 14px; color: {sub};
            font-size: 11px; font-weight: 800; letter-spacing: 0.12em;
        }}
        QLabel#MediaCardTitle {{ color: {txt}; font-size: 13px; font-weight: 760; border: none; background: transparent; }}
        QLabel#MediaCardMeta {{ color: {sub}; font-size: 12px; font-weight: 600; border: none; background: transparent; }}
        QFrame#ResultPreviewFrame {{ background-color: {preview_inset}; border-radius: 11px; border: 1px solid {pop_edge}; }}
        QLabel#ResultPlaceholder {{ font-size: 20px; font-weight: 780; border: none; background: transparent; }}
        QLabel#ResultCameraName {{ color: {txt}; font-size: 13px; font-weight: 780; border: none; background: transparent; }}
        QLabel#ResultDecisionMeta {{ font-size: 11px; font-weight: 760; border: none; background: transparent; }}
        QLabel#ResultMetaCompact {{ color: {sub}; font-size: 10px; font-weight: 600; border: none; background: transparent; }}
        QFrame#HomeBadgeFrame {{ background-color: {meta_panel_bg}; border: 1px solid {pop_edge}; border-radius: 10px; }}
        QLabel#HomeBadgeText {{ color: {accent}; font-size: 14px; font-weight: 900; border: none; background: transparent; }}
        QLabel#HomeCardTitle {{ color: {txt}; font-size: 17px; font-weight: 800; border: none; background: transparent; }}
        QFrame#SettingsInset {{ background-color: {zt_inset}; border: 1px solid {pop_edge}; border-radius: 14px; }}
        QFrame#MonPreview {{ background-color: #000000; border-radius: 16px; border: 1px solid {pop_edge}; }}
        QFrame#VideoWell {{
            background-color: {meta_panel_bg};
            border: 1px solid {pop_edge};
            border-radius: 18px;
        }}
        QFrame#VideoWellRecording {{
            background-color: {meta_panel_bg};
            border: 2px solid rgba(239, 68, 68, 0.70);
            border-radius: 18px;
        }}
        QLabel#FpsLabel {{
            color: {sub};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.3px;
            border: none;
            background: transparent;
            min-width: 48px;
        }}
        QLabel#RuntimeNameLabel {{ color: {txt}; font-size: 16px; font-weight: 750; border: none; background: transparent; }}
        QLabel#RuntimeMetricValue {{ color: {txt}; font-size: 14px; font-weight: 700; border: none; background: transparent; }}
        QLabel#StatusPill {{
            font-size: 12px;
            font-weight: 700;
            border-radius: 10px;
            padding: 5px 10px;
            border: 1px solid {pop_edge};
            background-color: {surface_row};
            color: {sub};
        }}
        QLabel#StatusPill[stateTone="success"] {{ color: #30D158; border-color: rgba(48,209,88,0.35); background-color: rgba(48,209,88,0.12); }}
        QLabel#StatusPill[stateTone="danger"] {{ color: #FF453A; border-color: rgba(255,69,58,0.35); background-color: rgba(255,69,58,0.12); }}
        QLabel#StatusPill[stateTone="neutral"] {{ color: {sub}; border-color: {pop_edge}; background-color: {surface_row}; }}
        QLabel#StateValueLabel {{ color: {accent}; font-size: 13px; font-weight: 700; border: none; background: transparent; }}
        QPushButton#ModalToggleCheck {{
            background-color: {meta_panel_bg}; border: 1px solid {brd}; border-radius: 12px; padding: 12px; font-size: 11px; text-align: left; color: {txt};
        }}
        QPushButton#ModalToggleCheck:checked {{
            background-color: rgba(239, 68, 68, 0.12); border-color: rgba(239, 68, 68, 0.35); color: #EF4444; font-weight: bold;
        }}
        QFrame#SurfaceRow {{ background-color: {surface_row}; border-radius: 14px; border: 1px solid {pop_edge}; }}
        QFrame#SurfaceRow:hover {{ background-color: {ihov}; border-color: {card_hover_brd}; }}
        QFrame#SurfaceRowCompact {{ background-color: {surface_row_compact}; border-radius: 12px; border: none; }}
        QFrame#Hairline {{ background-color: {hairline}; border: none; max-height: 1px; min-height: 1px; }}
        QFrame#FocusPopupRoot {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {focus_shell_top}, stop:1 {focus_shell_bot});
            border: 1px solid {focus_border_hi};
            border-radius: 20px;
        }}
        QFrame#FocusAccentBar {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent_deep}, stop:0.5 {accent}, stop:1 {accent_hover});
            border: none;
            border-top-left-radius: 19px;
            border-top-right-radius: 19px;
            min-height: 4px;
            max-height: 4px;
        }}
        QFrame#FocusInner {{
            background-color: {focus_panel};
            border: 1px solid {focus_panel_br};
            border-radius: 14px;
        }}
        QLabel#FocusDlgTitle {{ color: {txt}; font-size: 20px; font-weight: 800; border: none; background: transparent; letter-spacing: -0.45px; }}
        QLabel#FocusDlgSubtitle {{ color: {sub}; font-size: 12px; font-weight: 600; border: none; background: transparent; letter-spacing: 0.02em; }}
        QLabel#FocusDlgMuted {{ color: {sub}; font-size: 13px; font-weight: 600; border: none; background: transparent; }}
        QLabel#FocusValueChip {{
            background-color: rgba(10, 132, 255, 0.2);
            border: 1px solid rgba(100, 180, 255, 0.5);
            border-radius: 10px;
            padding: 8px 16px;
            color: {txt};
            font-size: 15px;
            font-weight: 800;
            min-width: 52px;
        }}
        QPushButton#FocusSecondaryBtn {{
            background-color: transparent;
            color: {sub};
            border: 1px solid {pop_edge};
            border-radius: 12px;
            padding: 10px 22px;
            font-size: 14px;
            font-weight: 700;
            min-height: 44px;
            min-width: 108px;
        }}
        QPushButton#FocusSecondaryBtn:hover {{ border-color: {focus_border_hi}; color: {txt}; background-color: {hov}; }}
        QPushButton#FocusPrimaryBtn {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {accent}, stop:1 {accent_deep});
            color: #FFFFFF !important;
            border-radius: 12px;
            padding: 10px 28px;
            font-size: 14px;
            font-weight: 800;
            border: 1px solid rgba(255,255,255,0.18);
            min-height: 44px;
            min-width: 128px;
        }}
        QPushButton#FocusPrimaryBtn:hover {{ background: {accent_hover}; color: #FFFFFF !important; }}
        QPushButton#FocusPrimaryBtn:pressed {{ background: {accent_pressed}; color: #FFFFFF !important; }}
        QPushButton#BtnModalCancel {{
            background-color: transparent;
            color: {sub};
            border: 1px solid {pop_edge};
            border-radius: 12px;
            padding: 10px 22px;
            font-size: 14px;
            font-weight: 700;
            min-height: 44px;
            min-width: 108px;
        }}
        QPushButton#BtnModalCancel:hover {{ border-color: {accent}; color: {txt}; background-color: {hov}; }}
        QSlider#FocusSlider::groove:horizontal {{ height: 10px; background: {brd}; border-radius: 5px; border: 1px solid {pop_edge}; }}
        QSlider#FocusSlider::handle:horizontal {{
            width: 24px; height: 24px; margin: -8px 0;
            background: {accent};
            border-radius: 12px;
            border: 3px solid {card};
        }}
        QSlider#FocusSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent_deep}, stop:1 {accent}); border-radius: 5px; height: 10px; }}
        QLabel#BrandTagline {{ color: {sub}; font-size: 11px; font-weight: 700; letter-spacing: 0.16em; border: none; background: transparent; text-transform: none; }}
        QLabel#PageSubtitle {{ color: {sub}; font-size: 13px; font-weight: 500; border: none; background: transparent; letter-spacing: 0.01em; max-width: 720px; }}
        QLabel#AddBtnIcon {{ font-size: 44px; font-weight: 300; color: {sub}; border: none; background: transparent; }}
        QLabel#AddBtnTitle {{ font-size: 15px; font-weight: 800; color: {txt}; border: none; background: transparent; letter-spacing: -0.25px; }}
        QLabel#AddBtnHint {{ font-size: 11px; font-weight: 600; color: {sub}; border: none; background: transparent; }}
        QFrame#ContextMenuShell {{ background: transparent; border: none; }}
        QFrame#ContextMenuPanel {{ background-color: {ctx_menu_bg}; border: 1px solid {pop_edge}; border-radius: 16px; }}
        QFrame#NetworkInset {{ background-color: {zt_inset}; border: 1px solid {pop_edge}; border-radius: 16px; }}
        QFrame#PeerListRow {{ background-color: {surface_row}; border: 1px solid {pop_edge}; border-radius: 10px; min-height: 52px; }}
        QFrame#PeerListRow:hover {{ border-color: {card_hover_brd}; background-color: {ihov}; }}
        QFrame#ZerotierNetRow {{ background-color: {surface_row}; border: 1px solid {pop_edge}; border-radius: 10px; }}
        QFrame#ZerotierNetRow:hover {{ border-color: {card_hover_brd}; background-color: {ihov}; }}
        QLabel#NetworkPanelHeading {{ color: {txt}; font-size: 13px; font-weight: 800; letter-spacing: 0.06em; border: none; background: transparent; padding: 2px 0 6px 0; }}
        QLabel#PeerStatusDotOn {{ min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px; border-radius: 5px; background-color: #30D158; border: 1px solid #1F9E45; }}
        QLabel#PeerStatusDotOff {{ min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px; border-radius: 5px; background-color: #5C6578; border: 1px solid #3D4558; }}
        QLabel#PeerIdLabel {{ color: {txt}; font-size: 13px; font-weight: 700; font-family: 'SF Mono', ui-monospace, monospace; border: none; background: transparent; }}
        QLabel#PeerRoleLabel {{ color: #9B7AFF; font-size: 11px; font-weight: 800; letter-spacing: 0.04em; border: none; background: transparent; min-width: 52px; }}
        QLabel#PeerIpLabel {{ color: {accent_net}; font-size: 13px; font-weight: 700; font-family: 'SF Mono', ui-monospace, monospace; border: none; background: transparent; }}
        QLabel#PeerLatencyLabel {{ color: {sub}; font-size: 12px; font-weight: 600; border: none; background: transparent; min-width: 56px; }}
        QLabel#ZtNetTitle {{ color: {txt}; font-size: 15px; font-weight: 800; border: none; background: transparent; letter-spacing: -0.2px; }}
        QLabel#ZtNetDetail {{ color: {sub}; font-size: 12px; font-weight: 600; border: none; background: transparent; }}
        QPushButton#ContextMenuItem {{
            background: transparent; color: {txt}; border: none; border-radius: 14px;
            padding: 12px 20px; font-size: 14px; font-weight: 600; text-align: left;
        }}
        QPushButton#ContextMenuItem:hover {{ background-color: {ctx_menu_hi}; color: {txt}; }}
        QLabel#ContextMenuCaption {{ color: {sub}; font-size: 11px; font-weight: 700; letter-spacing: 0.12em;
            padding: 10px 20px 4px 20px; border: none; background: transparent; }}
        QLabel#CardTitle {{ font-weight: 800; font-size: 11px; color: {card_txt}; border: none; background: transparent; letter-spacing: 0.6px; }}
        QLabel#CardStatus {{ color: {sub}; font-size: 9px; font-weight: 900; letter-spacing: 0.8px; border: none; background: transparent; }}
        QLabel#AccentBadge {{ background-color: {accent_soft_bg}; color: {accent}; font-size: 11px; font-weight: 900; border-radius: 10px; border: 1px solid {accent_soft_br}; padding: 2px; }}
        QLabel#StatKey {{ color: {sub}; font-size: 8px; font-weight: 900; letter-spacing: 1px; border: none; background: transparent; }}
        QLabel#StatValue {{ color: {txt}; font-size: 13px; font-weight: 800; border: none; background: transparent; }}
        QLabel#SectionHeading {{ font-size: 18px; font-weight: 800; color: {txt}; border: none; background: transparent; letter-spacing: -0.22px; padding: 4px 0 10px 0; }}
        QLabel#ModalTitle {{ font-size: 26px; font-weight: 800; color: {txt}; border: none; background: transparent; letter-spacing: -0.55px; }}
        QLabel#ModalSubtitle {{ color: {sub}; font-size: 13px; font-weight: 500; border: none; background: transparent; letter-spacing: 0.01em; }}
        QLabel#StreamBadgeMode {{ color: {sb_m_txt}; font-size: 10px; font-weight: 800; padding: 5px 10px; border-radius: 8px; border: 1px solid {sb_m_br}; letter-spacing: 0.35px; background-color: {sb_m_bg}; }}
        QLabel#StreamBadgeEngine {{ color: {sb_e_txt}; font-size: 10px; font-weight: 800; padding: 5px 10px; border-radius: 8px; border: 1px solid {sb_e_br}; letter-spacing: 0.35px; background-color: {sb_e_bg}; }}
        QLabel#StreamBadgeProfile {{ color: {sb_p_txt}; font-size: 10px; font-weight: 800; padding: 5px 10px; border-radius: 8px; border: 1px solid {sb_p_br}; letter-spacing: 0.35px; background-color: {sb_p_bg}; }}
        QLabel#RecBadgeChip {{ color: #F87171; font-size: 8px; font-weight: 900; background-color: rgba(239, 68, 68, 0.15); padding: 2px 7px; border-radius: 4px; border: 1px solid rgba(239, 68, 68, 0.40); }}
        QPushButton#RecToggle {{ background-color: rgba(239,68,68,0.10); border: 1px solid rgba(239,68,68,0.35); border-radius: 8px; color: #F87171; font-size: 9px; font-weight: 900; letter-spacing: 0.4px; }}
        QPushButton#RecToggle:checked {{ background-color: rgba(239,68,68,0.22); border: 1px solid rgba(239,68,68,0.65); color: #FF453A; }}
        QPushButton#RecToggle:hover {{ background-color: rgba(239,68,68,0.18); }}
        QLabel#DockerTagLine {{ color: {docker_tag_txt}; font-size: 9px; font-weight: 900; letter-spacing: 0.45px; border: none; background: transparent; }}
        QLabel#AiMetaLine {{ color: {sub}; font-size: 11px; font-weight: 800; letter-spacing: 0.2px; border: none; background: transparent; }}
        QLabel#AiMetaLineActive {{ color: {ai_hot}; font-size: 11px; font-weight: 900; letter-spacing: 0.2px; border: none; background: transparent; }}
        QLabel#FormLabel {{ color: {sub}; font-size: 13px; font-weight: 700; border: none; background: transparent; }}
        QLabel#FormLabelSm {{ color: {sub}; font-size: 12px; font-weight: 700; border: none; background: transparent; }}
        QLabel#CaptionMuted {{ color: {sub}; font-size: 12px; border: none; background: transparent; }}
        QLabel#CaptionTiny {{ color: {sub}; font-size: 9px; border: none; background: transparent; }}
        QLabel#FolderSlug {{ color: {sub}; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; border: none; background: transparent; }}
        QLabel#MonoIp {{ font-family: 'SF Mono', ui-monospace, monospace; font-size: 12px; font-weight: 700; color: {accent_net}; border: none; background: transparent; min-width: 110px; }}
        QLabel#CaptionMutedSm {{ color: {sub}; font-size: 11px; font-weight: 650; border: none; background: transparent; }}
        QLabel#MonoMuted {{ color: {sub}; font-size: 10px; font-family: monospace; border: none; background: transparent; }}
        QLabel#PreviewHint {{ color: {sub}; font-size: 11px; font-weight: 700; letter-spacing: 0.15px; border: none; background: transparent; }}
        QLabel#PreviewArea {{
            background-color: {preview_inset};
            border-radius: 14px;
            border: 1px solid {preview_br};
            color: {sub};
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.35px;
        }}
        QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background-color: {ibg}; border: 1px solid {pop_edge}; border-radius: 12px; padding: 10px 16px; color: {txt}; font-size: 14px; font-weight: 500; min-height: 40px; }}
        QLineEdit[roleLocked="true"], QComboBox[roleLocked="true"], QSpinBox[roleLocked="true"], QDoubleSpinBox[roleLocked="true"] {{
            border: 1px dashed {accent_soft_br};
            background-color: {meta_panel_bg};
            color: {sub};
        }}
        QCheckBox[roleLocked="true"] {{
            color: {sub};
        }}
        QWidget[roleLocked="true"] {{
            opacity: 0.95;
        }}
        QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 24px; border: none; background: transparent; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {accent}; border-width: 1.5px; background-color: rgba(59,130,246,0.07); }}
        QFrame#SearchShell {{
            background-color: {ibg};
            border: 1px solid {search_ring};
            border-radius: 14px;
            min-height: 48px;
        }}
        QFrame#SearchShell:hover {{ border-color: rgba(90, 160, 255, 0.55); }}
        QLabel#SearchIconBadge {{
            background-color: rgba(10, 132, 255, 0.14);
            border: 1px solid rgba(100, 180, 255, 0.38);
            border-radius: 12px;
            min-width: 46px;
            max-width: 46px;
            min-height: 42px;
            max-height: 42px;
        }}
        QLineEdit#SearchField {{
            background: transparent;
            border: none;
            padding: 10px 8px;
            min-height: 40px;
            color: {txt};
            font-size: 14px;
            font-weight: 500;
        }}
        QLineEdit#SearchField:focus {{ border: none; background: transparent; }}
        QComboBox {{ padding-right: 44px; min-height: 40px; }}
        QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; border: none; width: 40px; border-left: 1px solid {pop_edge}; border-top-right-radius: 11px; border-bottom-right-radius: 11px; background-color: {ibg}; }}
        QComboBox::down-arrow {{ image: none; border-left: 6px solid transparent; border-right: 6px solid transparent; border-top: 8px solid {txt}; margin-right: 14px; }}
        QComboBox QAbstractItemView, QComboBox QListView {{
            background-color: {card};
            border: 1px solid {brd};
            color: {txt};
            outline: 0px;
            border-radius: 12px;
            selection-background-color: {accent};
            selection-color: #ffffff;
            show-decoration-selected: 1;
        }}
        QComboBox QAbstractItemView::item, QComboBox QListView::item {{
            min-height: 38px;
            padding: 10px 14px;
            background-color: {card};
            color: {txt};
            border: none;
            margin: 0px;
        }}
        QComboBox QAbstractItemView::item:selected, QComboBox QListView::item:selected {{
            background-color: {accent};
            color: #ffffff;
            border: none;
        }}
        QComboBox QAbstractItemView::item:hover, QComboBox QListView::item:hover {{
            background-color: {ihov};
            color: {txt};
            border: none;
        }}
        QSlider::groove:horizontal {{ height: 6px; background: {brd}; border-radius: 4px; }}
        QSlider::handle:horizontal {{ width: 20px; height: 20px; margin: -7px 0; background: {accent}; border-radius: 10px; border: 2px solid {card}; }}
        QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 4px; height: 6px; }}
        QPushButton#NavTab {{ border: 1px solid transparent; border-radius: 12px; text-align: left; padding: 13px 18px; color: {nav_idle}; font-weight: 680; font-size: 13px; letter-spacing: 0.10px; min-height: 46px; }}
        QPushButton#NavTab[compact="true"] {{
            text-align: center;
            padding: 12px 8px;
            font-size: 12px;
            font-weight: 760;
            letter-spacing: 0.4px;
            min-height: 44px;
            min-width: 58px;
        }}
        QPushButton#NavTab:checked {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:0.60 {accent_vivid}, stop:1 {accent_deep});
            color: #FFFFFF !important;
            border: 1px solid rgba(255,255,255,0.20);
            border-left: 2px solid rgba(255,255,255,0.55);
            font-weight: 800;
        }}
        QPushButton#NavTab[compact="true"]:checked {{
            border-left: 1px solid rgba(255,255,255,0.24);
            border-bottom: 2px solid rgba(255,255,255,0.60);
        }}
        QPushButton#NavTab:hover:!checked {{ background-color: {hov}; color: {txt}; border: 1px solid {pop_edge}; }}
        QPushButton#SidebarToggle {{
            border: 1px solid {pop_edge};
            border-radius: 10px;
            background-color: {hov};
            color: {sub};
            font-size: 14px;
            font-weight: 820;
            min-height: 34px;
            padding: 0 10px;
        }}
        QPushButton#SidebarToggle:hover {{ border-color: {accent_soft_br}; color: {txt}; background-color: {ihov}; }}
        QPushButton#SidebarToggle:pressed {{ background-color: {meta_panel_bg}; }}
        QPushButton#IconRefresh {{
            min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
            border-radius: 10px; border: 1px solid {brd}; background-color: {hov}; color: {txt};
            font-size: 16px; font-weight: 700; padding: 0;
        }}
        QPushButton#IconRefresh:hover {{ border-color: {accent}; color: {accent}; background-color: {accent_soft_bg}; }}
        QPushButton#IconRefresh:pressed {{ background-color: {ihov}; }}
        QLabel#BrandTitle {{ font-size: 34px; font-weight: 800; color: {txt}; letter-spacing: -0.8px; border: none; background: transparent; }}
        QLabel#PageTitle {{ font-size: 30px; font-weight: 800; color: {txt}; letter-spacing: -0.65px; border: none; background: transparent; }}
        QLabel#SettingsBlockTitle {{ font-size: 22px; font-weight: 800; color: {txt}; letter-spacing: -0.3px; border: none; background: transparent; }}
        QLabel#SettingsRowLabel {{ font-size: 14px; font-weight: 600; color: {txt}; border: none; background: transparent; }}
        QLabel#SidebarFootnote {{ color: {sub}; font-size: 10px; font-weight: 700; border: none; background: transparent; }}
        QLabel#MediaTypeTag {{ color: {sub}; font-size: 9px; font-weight: 900; letter-spacing: 0.5px; min-width: 45px; border: none; background: transparent; }}
        QLabel#ListPrimary {{ font-size: 12px; font-weight: 700; color: {txt}; border: none; background: transparent; }}
        QLabel#HWValue {{ font-size: 14px; font-weight: 700; color: {txt}; border: none; background: transparent; }}
        QLabel#DockSize {{ color: {accent}; font-size: 11px; font-weight: 700; border: none; background: transparent; margin-right: 15px; }}
        QPushButton#AddBtn {{ border: 1px dashed {pop_edge}; border-radius: 20px; color: {sub}; background-color: transparent; font-weight: 800; font-size: 11px; padding: 14px; }}
        QPushButton#AddBtn:hover {{ border-color: {accent}; color: {accent}; background-color: rgba(59,130,246,0.08); }}
        QPushButton#BtnPrimary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:0.65 {accent_vivid}, stop:1 {accent_deep}); color: #FFFFFF !important; border-radius: 10px; padding: 8px 18px; font-weight: 720; border: 1px solid rgba(255,255,255,0.14); font-size: 13px; letter-spacing: 0.2px; min-height: 36px; }}
        QPushButton#BtnPrimary:hover {{ background: {accent_hover}; color: #FFFFFF !important; }}
        QPushButton#BtnPrimary:pressed {{ background: {accent_pressed}; color: #FFFFFF !important; }}
        QPushButton#BtnSecondary {{ background-color: {hov}; color: {txt}; border-radius: 10px; padding: 8px 16px; font-weight: 650; border: 1px solid {pop_edge}; font-size: 13px; min-height: 36px; }}
        QPushButton#BtnSecondary:hover {{ background-color: {ihov}; border-color: {accent_soft_br}; color: {txt}; }}
        QPushButton#BtnSecondary:pressed {{ background-color: {accent_soft_bg}; }}
        QPushButton#BtnOutline {{ background: transparent; color: {accent}; border-radius: 10px; padding: 8px 16px; font-weight: 700; border: 1.5px solid {accent}; font-size: 13px; min-height: 36px; }}
        QPushButton#BtnOutline:hover {{ background-color: {accent_soft_bg}; }}
        QPushButton#BtnSm {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:0.65 {accent_vivid}, stop:1 {accent_deep}); color: #FFFFFF !important; border-radius: 8px; padding: 5px 14px; font-weight: 700; border: none; font-size: 12px; min-height: 30px; }}
        QLabel#DeviceCardAccent {{ color: {accent}; font-size: 12px; font-weight: 760; border: none; background: transparent; }}
        QLabel#DeviceCamName {{ color: {txt}; font-size: 12px; font-weight: 650; border: none; background: transparent; }}
        QLabel#DeviceCardStatus {{ font-size: 11px; font-weight: 800; border: none; background: transparent; }}
        QPushButton#BtnSm:hover {{ background: {accent_hover}; }}
        QPushButton#BtnDanger {{ background-color: rgba(239, 68, 68, 0.12); color: #EF4444; border-radius: 10px; padding: 8px 14px; border: 1px solid rgba(239, 68, 68, 0.28); font-weight: 700; font-size: 13px; min-height: 36px; }}
        QPushButton#BtnDanger:hover {{ background-color: #EF4444; color: white; }}
        QPushButton#BtnDanger:pressed {{ background-color: #C93434; color: white; }}
        QPushButton#ShellBtn {{ border: 1px solid {brd}; border-radius: 10px; color: {txt}; font-size: 12px; font-weight: 600; background: {hov}; padding: 6px 14px; min-height: 30px; }}
        QPushButton#ShellBtn:hover {{ border-color: {accent}; color: {accent}; background: {accent_soft_bg}; }}
        QPushButton#ShellBtn:pressed {{ background: {ihov}; }}
        QPushButton#OpenAction {{ background-color: {accent_soft_bg}; color: {accent}; border-radius: 10px; font-size: 12px; font-weight: 700; border: 1px solid {accent_soft_br}; padding: 5px 14px; min-height: 30px; }}
        QPushButton#OpenAction:hover {{ background-color: {accent}; color: #FFFFFF !important; border-color: {accent}; }}
        QPushButton#RowGhost {{ border: 1px solid {brd}; border-radius: 8px; background: {hov}; color: {txt}; font-size: 11px; font-weight: 600; padding: 5px 12px; min-height: 30px; }}
        QPushButton#RowGhost:hover {{ border-color: {accent}; color: {accent}; background-color: {accent_soft_bg}; }}
        QPushButton#RowGhost:pressed {{ background-color: {ihov}; }}
        QPushButton#RowAccent {{ border: 1.5px solid {accent_soft_br}; border-radius: 8px; background: {accent_soft_bg}; color: {accent}; font-size: 11px; font-weight: 700; padding: 5px 12px; min-height: 30px; }}
        QPushButton#RowAccent:hover {{ background-color: {accent}; color: #FFFFFF !important; border-color: {accent}; }}
        QPushButton#RowAccent:pressed {{ background-color: {accent_deep}; color: #FFFFFF !important; }}
        QPushButton#IconCloseSm {{ border: none; background: transparent; color: {sub}; font-size: 17px; min-width: 28px; min-height: 28px; }}
        QPushButton#IconCloseSm:hover {{ color: #EF4444; }}
        QPushButton#CardHeaderAction {{
            border: 1px solid {brd}; border-radius: 12px; background-color: {accent_soft_bg}; color: {txt};
            font-size: 11px; font-weight: 700; padding: 0 14px; min-height: 32px;
        }}
        QPushButton#CardHeaderAction:hover {{ border-color: {accent_soft_br}; background-color: {hov}; color: {accent}; }}
        QPushButton#CardMenuBtn {{
            border: 1px solid {brd}; border-radius: 12px; background-color: transparent; color: {sub};
            font-size: 13px; font-weight: 800; letter-spacing: 0.5px; min-width: 40px; min-height: 30px;
        }}
        QPushButton#CardMenuBtn:hover {{ color: {accent}; border-color: {accent_soft_br}; background-color: {accent_soft_bg}; }}
        QPushButton#RecToggle {{ font-size: 8px; font-weight: 900; border-radius: 8px; border: 1px solid {brd}; background: transparent; color: {sub}; padding: 2px 6px; min-height: 22px; }}
        QPushButton#RecToggle:checked {{ background-color: #EF4444; color: white; border-color: #EF4444; }}
        QPushButton#BtnTextLink {{ background: transparent; color: {accent}; font-weight: 700; font-size: 13px; border: none; padding: 10px 8px; }}
        QPushButton#BtnTextLink:hover {{ color: {accent_hover}; }}
        QPushButton#ImgPurgeBtn {{ background-color: rgba(239, 68, 68, 0.12); color: #EF4444; border-radius: 10px; font-size: 10px; font-weight: 800; border: 1px solid rgba(239, 68, 68, 0.28); padding: 4px 10px; min-height: 26px; }}
        QPushButton#ImgPurgeBtn:hover {{ background-color: #EF4444; color: white; border-color: #EF4444; }}
        QPushButton#RowMuted {{ border: 1px solid {brd}; border-radius: 8px; background: transparent; color: {sub}; font-size: 11px; font-weight: 600; padding: 5px 12px; min-height: 30px; }}
        QPushButton#RowMuted:hover {{ border-color: {sub}; color: {txt}; background-color: {hov}; }}
        QPushButton#RowMuted:pressed {{ background-color: {ihov}; }}
        QScrollBar:vertical {{ background: transparent; width: 9px; margin: 4px 2px 4px 0; }}
        QScrollBar::handle:vertical {{ background: {brd}; border-radius: 6px; min-height: 36px; }}
        QScrollBar::handle:vertical:hover {{ background: {sub}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; width: 0; }}
        QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 0 4px 2px 4px; }}
        QScrollBar::handle:horizontal {{ background: {brd}; border-radius: 6px; min-width: 36px; }}
        QScrollBar::handle:horizontal:hover {{ background: {sub}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ height: 0; width: 0; }}
        QMenu {{ background-color: {ctx_menu_bg}; color: {txt}; border: 1px solid {brd}; border-radius: 18px; padding: 8px; }}
        QMenu::item {{ padding: 12px 24px; border-radius: 12px; background: transparent; min-height: 22px; font-weight: 600; }}
        QMenu::item:selected {{ background-color: {ctx_menu_hi}; color: {txt}; }}
        QMenu::separator {{ height: 1px; background: {brd}; margin: 8px 14px; }}
        QToolTip {{
            background-color: {card}; color: {txt}; border: 1px solid {brd};
            border-radius: 10px; padding: 8px 12px; font-size: 12px; font-weight: 500;
        }}
        QMessageBox {{ background-color: {bg}; }}
        QMessageBox QLabel {{ color: {txt}; font-size: 13px; min-width: 240px; }}
        QMessageBox QPushButton {{
            min-width: 88px; padding: 8px 18px; border-radius: 12px; border: 1px solid {brd};
            background-color: {ibg}; color: {txt}; font-weight: 600; font-size: 12px;
        }}
        QMessageBox QPushButton:hover {{ border-color: {accent}; color: {accent}; background-color: rgba(59,130,246,0.08); }}
        QStackedWidget {{ background-color: transparent; }}
        QWidget#Overlay {{ background-color: {ov_bg}; }}
        QPlainTextEdit {{ background-color: {ibg}; border: 1px solid {brd}; border-radius: 14px; color: {txt}; padding: 8px; font-family: 'SF Mono', 'Consolas', monospace; font-size: 12px; }}
        QLabel#EmptyStateTitle {{ color: {sub}; font-size: 17px; font-weight: 700; border: none; background: transparent; letter-spacing: -0.2px; }}
        QLabel#EmptyStateSubtitle {{ color: {sub}; font-size: 13px; font-weight: 500; border: none; background: transparent; }}
        QFrame#ZtWarningFrame {{ background-color: rgba(255,159,10,0.10); border: 1px solid rgba(255,159,10,0.35); border-radius: 12px; }}
        QLabel#ZtWarningText {{ color: #FF9F0A; font-size: 12px; font-weight: 600; border: none; background: transparent; }}
        QLabel#ZtStatusDot {{ font-size: 11px; font-weight: 600; border: none; background: transparent; }}
        QLabel#DeviceConnectedBadge {{ color: #30D158; font-size: 10px; font-weight: 800; letter-spacing: 0.06em; background-color: rgba(48,209,88,0.12); border: 1px solid rgba(48,209,88,0.30); border-radius: 8px; padding: 2px 8px; }}
        QLabel#DeviceOfflineBadge {{ color: {sub}; font-size: 10px; font-weight: 700; letter-spacing: 0.04em; background-color: transparent; border: 1px solid {pop_edge}; border-radius: 8px; padding: 2px 8px; }}
        QFrame#HomeZtCard {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {card}, stop:1 {zt_inset});
            border: 1px solid {pop_edge};
            border-radius: 20px;
        }}
        QFrame#HomeZtCard:hover {{ border-color: {card_hover_brd}; }}
        QFrame#HomeStatCard {{ background-color: {card}; border: 1px solid {pop_edge}; border-radius: 18px; }}
        QFrame#HomeStatCard:hover {{ border-color: {card_hover_brd}; }}
        QFrame#HomeActionCard {{ background-color: {card}; border: 1px solid {pop_edge}; border-radius: 18px; }}
        QFrame#HomeActionCard:hover {{ border-color: {card_hover_brd}; }}
        QLabel#HomeCardTitle {{ color: {txt}; font-size: 15px; font-weight: 700; letter-spacing: -0.2px; border: none; background: transparent; }}
        QLabel#HomeZtStatusLabel {{ font-size: 13px; font-weight: 600; border: none; background: transparent; }}
        QLabel#HomeStatValue {{ color: {txt}; font-size: 32px; font-weight: 800; letter-spacing: -0.5px; border: none; background: transparent; }}
        QLabel#HomeStatLabel {{ color: {sub}; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; border: none; background: transparent; }}
        QLabel#HomeActionTitle {{ color: {txt}; font-size: 13px; font-weight: 700; letter-spacing: -0.1px; border: none; background: transparent; }}
        QLabel#HomeActionHint {{ color: {sub}; font-size: 11px; font-weight: 400; border: none; background: transparent; }}
        QLabel#SectionTitle {{ color: {txt}; font-size: 16px; font-weight: 700; letter-spacing: -0.2px; border: none; background: transparent; }}
        """


def polish_scroll_area(sa):
    """Show QScrollArea frameless to match theme (no QScroller to avoid drag conflicts inside widgets)."""
    sa.setFrameShape(QFrame.Shape.NoFrame)


def make_icon_refresh_button(slot, tooltip="Refresh"):
    b = QPushButton()
    b.setObjectName("IconRefresh")
    b.setFixedSize(42, 42)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setToolTip(tooltip)
    st = QApplication.style()
    if st is not None:
        ic = st.standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        if not ic.isNull():
            b.setIcon(ic)
            b.setIconSize(QSize(20, 20))
    if b.icon().isNull():
        b.setText("Reload")
    b.clicked.connect(slot)
    return b


def hairline():
    h = QFrame()
    h.setObjectName("Hairline")
    h.setFixedHeight(1)
    h.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return h


def surface_row(compact=False):
    r = QFrame()
    r.setObjectName("SurfaceRowCompact" if compact else "SurfaceRow")
    return r


def ssh_session_key(host: str, user: str) -> tuple:
    return ((host or "").strip(), (user or "jetson").strip())


def ssh_connect_with_password(host: str, user: str, password: str, port: int = 22, timeout: int = 18):
    """Return (paramiko.SSHClient | None, error_message | None). Password is not stored."""
    try:
        import paramiko
    except ImportError:
        return None, "paramiko is not installed. Run: python3 -m pip install -r gui/requirements.txt"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=(host or "").strip(),
            port=int(port),
            username=(user or "jetson").strip(),
            password=password,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
            banner_timeout=timeout,
            auth_timeout=timeout,
        )
        return client, None
    except Exception as e:
        try:
            client.close()
        except Exception:
            pass
        return None, str(e) or type(e).__name__


def ssh_exec_text(client, cmd, timeout=25):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out_b = stdout.read()
    err_b = stderr.read()
    code = stdout.channel.recv_exit_status()
    return out_b.decode(errors="replace"), err_b.decode(errors="replace"), code


def _sftp_ensure_remote_dir(sftp, remote_dir: str):
    path = str(remote_dir or "").strip()
    if not path:
        return
    parts = []
    cur = path
    while cur not in ("", "/", "."):
        parts.append(cur)
        cur = posixpath.dirname(cur)
        if cur == path:
            break
    for target in reversed(parts):
        try:
            sftp.stat(target)
        except OSError:
            try:
                sftp.mkdir(target)
            except OSError:
                pass


def sftp_upload_tree(client, local_dir: str, remote_dir: str):
    root = os.path.abspath(local_dir)
    if not os.path.isdir(root):
        raise FileNotFoundError(root)
    sftp = client.open_sftp()
    try:
        _sftp_ensure_remote_dir(sftp, remote_dir)
        for base, dirs, files in os.walk(root):
            rel = os.path.relpath(base, root)
            rel = "" if rel == "." else rel
            remote_base = remote_dir if not rel else posixpath.join(remote_dir, rel.replace(os.sep, "/"))
            _sftp_ensure_remote_dir(sftp, remote_base)
            for d in dirs:
                _sftp_ensure_remote_dir(sftp, posixpath.join(remote_base, d))
            for name in files:
                local_path = os.path.join(base, name)
                remote_path = posixpath.join(remote_base, name)
                sftp.put(local_path, remote_path)
    finally:
        sftp.close()


def form_label(text):
    w = QLabel(text)
    w.setObjectName("FormLabel")
    return w


def form_label_sm(text):
    w = QLabel(text)
    w.setObjectName("FormLabelSm")
    return w


def settings_title(text):
    w = QLabel(text)
    w.setObjectName("SettingsBlockTitle")
    return w


def _models_page_helpers():
    return {
        "FlowLayout": FlowLayout,
        "ToggleSwitch": ToggleSwitch,
        "form_label": form_label,
        "polish_scroll_area": polish_scroll_area,
    }


def _settings_page_helpers():
    return {
        "CAMERA_RESOLUTION_PRESETS": CAMERA_RESOLUTION_PRESETS,
        "CollapsibleSection": CollapsibleSection,
        "DockerManager": DockerManager,
        "DonutChart": DonutChart,
        "ToggleSwitch": ToggleSwitch,
        "csi_sensor_default_focuser": csi_sensor_default_focuser,
        "csi_sensor_default_resolution_index": csi_sensor_default_resolution_index,
        "csi_sensor_profiles": csi_sensor_profiles,
        "csi_sensor_recommended_capture": csi_sensor_recommended_capture,
        "focuser_profiles": focuser_profiles,
        "form_label": form_label,
        "form_label_sm": form_label_sm,
        "get_camera_defaults": get_camera_defaults,
        "get_gpu_info": get_gpu_info,
        "get_zerotier_local_ips": get_zerotier_local_ips,
        "get_zerotier_networks": get_zerotier_networks,
        "get_zerotier_peer_count": get_zerotier_peer_count,
        "get_zerotier_peers": get_zerotier_peers,
        "get_zerotier_status": get_zerotier_status,
        "hairline": hairline,
        "jetson_board_catalog": jetson_board_catalog,
        "load_app_prefs": load_app_prefs,
        "make_icon_refresh_button": make_icon_refresh_button,
        "polish_scroll_area": polish_scroll_area,
        "set_camera_defaults": set_camera_defaults,
        "settings_title": settings_title,
    }


def _inspection_page_helpers():
    return {
        "FlowLayout": FlowLayout,
        "polish_scroll_area": polish_scroll_area,
    }


def _results_page_helpers():
    return {
        "FlowLayout": FlowLayout,
        "make_icon_refresh_button": make_icon_refresh_button,
        "polish_scroll_area": polish_scroll_area,
    }


class VisionAnalytics:
    _face_cascade = None
    _yolo_model = None
    _yolo_available = None

    @classmethod
    def get_face_cascade(cls):
        if cls._face_cascade is None:
            try:
                path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
                cls._face_cascade = cv2.CascadeClassifier(path)
            except: pass
        return cls._face_cascade

    @classmethod
    def get_yolo(cls):
        if VisionAnalytics._yolo_available is False:
            return None
        if VisionAnalytics._yolo_model is None:
            try:
                from ultralytics import YOLO
                VisionAnalytics._yolo_model = YOLO("yolo11n.pt")
                VisionAnalytics._yolo_available = True
            except Exception:
                VisionAnalytics._yolo_available = False
                return None
        return VisionAnalytics._yolo_model

    @staticmethod
    def process(frame, engine_type="STANDARD"):
        t = str(engine_type).upper()
        meta = {"objects": 0, "classes": {}}
        
        if t == "YOLOv8":
            model = VisionAnalytics.get_yolo()
            if model is not None:
                try:
                    results = model(frame, conf=0.25, iou=0.45, verbose=False)
                    if results and len(results) > 0:
                        r = results[0]
                        if r.boxes is not None:
                            meta["objects"] = len(r.boxes)
                            names = r.names or {}
                            for cls_id in r.boxes.cls.cpu().int().tolist():
                                name = names.get(cls_id, "object")
                                meta["classes"][name] = meta["classes"].get(name, 0) + 1
                        annotated = r.plot()
                        if annotated is not None and annotated.size > 0:
                            frame[:] = annotated
                except Exception:
                    meta["objects"] = 0
            else:
                cv2.putText(frame, "YOLO: Load model (yolo11n.pt) failed", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 122, 255), 2)
        elif t == "FACE AI":
            cascade = VisionAnalytics.get_face_cascade()
            if cascade and not cascade.empty():
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = cascade.detectMultiScale(gray, 1.3, 5)
                meta["objects"] = len(faces)
                for (x, y, w, h) in faces:
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (255, 45, 85), 2)
                    cv2.putText(frame, "HUMAN FACE", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 45, 85), 2)
            else:
                cv2.putText(frame, "FACE ENGINE: OFFLINE", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 45, 85), 2)
        elif t == "POSE AI":
            cv2.putText(frame, "ENGINE: POSE AI | TRACKING", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (48, 209, 88), 2)
            cv2.line(frame, (50, 50), (150, 150), (48, 209, 88), 2)
            
        return frame, meta

def _parse_stream_meta_overrides(meta: str) -> dict:
    """Meta içinde spv:WxH (istemci önizleme yeniden boyutlandırma), sfp:N (saniyedeki üst sınır, 0=yok)."""
    out = {}
    if not meta:
        return out
    for seg in meta.split("|"):
        if seg.startswith("spv:"):
            m = re.match(r"spv:(\d+)x(\d+)", seg, re.I)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                if w > 0 and h > 0:
                    out["stream_pv"] = (w, h)
        elif seg.startswith("sfp:"):
            try:
                v = int(seg.split(":", 1)[1])
                if v >= 0:
                    out["stream_max_fps"] = v
            except (ValueError, IndexError):
                pass
    return out


def _rebuild_camera_meta_stream(meta: str, wh, max_fps: int) -> str:
    raw = (meta or "Stream|STANDARD|AUTO").split("|")
    parts = [p for p in raw if p and not p.startswith("spv:") and not p.startswith("sfp:")]
    head = parts[:3] if len(parts) >= 3 else ["Stream", "STANDARD", "AUTO"]
    extra = parts[3:] if len(parts) > 3 else []
    tail = []
    if wh and wh[0] > 0 and wh[1] > 0:
        tail.append(f"spv:{wh[0]}x{wh[1]}")
    if max_fps and max_fps > 0:
        tail.append(f"sfp:{int(max_fps)}")
    return "|".join(head + extra + tail)


def _profile_to_size(profile):
    """PROFILE combo (Auto, 720p, 1080p, 4K) -> (width, height) or None for Auto (no resize)."""
    if not profile or str(profile).strip().lower() == "auto":
        return None
    p = str(profile).strip().upper()
    if "720" in p: return (1280, 720)
    if "1080" in p: return (1920, 1080)
    if "4K" in p or "4k" in p: return (3840, 2160)
    return None

# -----------------------------------------------------------------------------
# Jetson CSI / nvarguscamerasrc pipeline defaults
# Resolution presets: tuned for high-res Sony CSI modules (e.g. CSI/USB Camera); IMX219/477/230 limits differ — see docs/CSI_CAMERA_FOCUS.md
# Focus: fixed = apply saved position when stream starts; manual = slider + Apply;
#   auto = run autofocus script (stop live preview first when possible).
# -----------------------------------------------------------------------------
CAMERA_RESOLUTION_PRESETS = [
    ("720p (1280×720, 30 fps)", 1280, 720, 30),
    ("720p 60 fps", 1280, 720, 60),
    ("1080p (1920×1080, 30 fps)", 1920, 1080, 30),
    ("1080p 60 fps", 1920, 1080, 60),
    ("4K (3840×2160, 15 fps)", 3840, 2160, 15),
]


def _profile_key_to_csi_preset_index(profile):
    """Map modal 'Video resolution' to CAMERA_RESOLUTION_PRESETS index, or None = use Settings default."""
    if not profile or str(profile).strip().lower() == "auto":
        return None
    p = str(profile).strip().upper()
    want_w, want_h = None, None
    if "720" in p:
        want_w, want_h = 1280, 720
    elif "1080" in p:
        want_w, want_h = 1920, 1080
    elif "4K" in p or "4k" in p:
        want_w, want_h = 3840, 2160
    else:
        return None
    for i, pr in enumerate(CAMERA_RESOLUTION_PRESETS):
        if pr[1] == want_w and pr[2] == want_h:
            return i
    return 0


def _gst_tcp_multipart_mjpeg_client(host: str, port: int) -> str:
    """Jetson tcpserversink + multipartmux çıkışını masaüstünde okumak için GStreamer boru hattı."""
    return (
        f"tcpclientsrc host={host} port={port} num-buffers=-1 ! "
        "queue max-size-buffers=1 max-size-time=0 max-size-bytes=0 leaky=downstream ! "
        "multipartdemux ! jpegparse ! jpegdec ! videoconvert ! video/x-raw,format=BGR ! "
        "appsink drop=1 max-buffers=1 sync=false"
    )


def _video_capture_read_latest(cap, max_flush: int = 64):
    """Biçim: düşük gecikme canlı önizleme — kuyruk birikmesinde yalnızca en güncel kare decode edilir."""
    if cap is None or not cap.isOpened():
        return False, None
    if not cap.grab():
        return False, None
    flushed = 0
    while flushed < max_flush and cap.grab():
        flushed += 1
    return cap.retrieve()


def _try_set_stream_buffer(cap, n: int = 1) -> None:
    """Ağ ve USB kaynaklarında biriken gecikmeyi azaltmak için (OpenCV/FFmpeg destekliyse)."""
    try:
        if cap is not None and cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, n)
    except Exception:
        pass


def _open_remote_tcp_mjpeg(host: str, port: int):
    """Jetson tcpserversink + multipartmux ile uyumlu ham TCP okuma."""
    tcp_url = f"tcp://{host}:{port}"
    cap = cv2.VideoCapture(tcp_url, cv2.CAP_FFMPEG)
    if cap.isOpened():
        _try_set_stream_buffer(cap, 1)
        return cap
    try:
        gst = _gst_tcp_multipart_mjpeg_client(host, port)
        gcap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
        if gcap.isOpened():
            _try_set_stream_buffer(gcap, 1)
            return gcap
    except Exception:
        pass
    cap2 = cv2.VideoCapture(tcp_url)
    if cap2.isOpened():
        _try_set_stream_buffer(cap2, 1)
        return cap2
    return None


def open_cv_capture_remote(source):
    """Uzak akış: HTTP MJPEG, RTSP ve Jetson multipart TCP (OpenCV + isteğe bağlı GStreamer)."""
    s = (source or "").strip()
    if not s or "://" not in s:
        return None
    u = urlparse(s if "://" in s else f"http://{s}")
    scheme = (u.scheme or "").lower()
    host = u.hostname or ""
    port = u.port
    if not host:
        return None
    if scheme == "tcp" and port:
        c = _open_remote_tcp_mjpeg(host, port)
        if c is not None:
            return c
        cap = cv2.VideoCapture(s, cv2.CAP_FFMPEG)
        if cap.isOpened():
            _try_set_stream_buffer(cap, 1)
            return cap
        cap2 = cv2.VideoCapture(s)
        if cap2.isOpened():
            _try_set_stream_buffer(cap2, 1)
            return cap2
        return None
    if scheme in ("http", "https"):
        path = (u.path or "").rstrip("/")
        # VisionDock Jetson yayını http://host:5000/ olsa da sunucu tarafı çoğunlukla ham multipart TCP'dir;
        # önce TCP denenir (FFmpeg parse + daha stabil FPS).
        jetsonish_5000 = port == 5000 and (path == "" or path == "/")
        if jetsonish_5000:
            c = _open_remote_tcp_mjpeg(host, port)
            if c is not None:
                log.info("Remote capture (TCP multipart preferred for :5000): %s", host)
                return c
        cap = cv2.VideoCapture(s, cv2.CAP_FFMPEG)
        if cap.isOpened():
            _try_set_stream_buffer(cap, 1)
            return cap
        cap = cv2.VideoCapture(s)
        if cap.isOpened():
            _try_set_stream_buffer(cap, 1)
            return cap
        if port == 5000 and not jetsonish_5000:
            c = _open_remote_tcp_mjpeg(host, port)
            if c is not None:
                return c
    return None


def _probe_stream_native_geometry(src: str, timeout_sec: float = 4.0):
    """Kaynağı kısa süre açıp gerçek kare boyutu ve (varsa) FPS ipucunu okur."""
    cap = None
    try:
        s = str(src or "").strip()
        if not s:
            return None, None, 0.0
        if "://" in s:
            cap = open_cv_capture_remote(s)
            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(s, cv2.CAP_FFMPEG)
        else:
            try:
                cap = cv2.VideoCapture(int(s))
            except ValueError:
                cap = cv2.VideoCapture(s, cv2.CAP_FFMPEG)
        if cap is None or not cap.isOpened():
            return None, None, 0.0
        _try_set_stream_buffer(cap, 1)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if w >= 160 and h >= 120:
            return w, h, fps
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_sec:
            ret, frame = _video_capture_read_latest(cap, max_flush=20)
            if not ret or frame is None or frame.size == 0:
                ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                hh, ww = frame.shape[:2]
                if ww >= 160 and hh >= 120:
                    return ww, hh, fps
            time.sleep(0.04)
        return None, None, fps
    except Exception as e:
        log.debug("probe_stream_native_geome%s", e)
        return None, None, 0.0
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


def _even_dim(v: float) -> int:
    v = int(round(v))
    return max(2, (v // 2) * 2)


def _preview_resolution_choices(nw: int, nh: int):
    """Kaynak en-boy oranına uyan, kaynaktan büyük olmayan önizleme boyutları (azalan alan)."""
    if nw < 32 or nh < 32:
        return []
    ar = nw / max(nh, 1)
    tol = 0.034
    seen = set()
    out = []

    def add(wf, hf):
        w, h = _even_dim(wf), _even_dim(hf)
        if w < 160 or h < 120 or w > nw or h > nh:
            return
        if abs(w / max(h, 1) - ar) > tol * max(1.0, ar):
            return
        if (w, h) in seen:
            return
        seen.add((w, h))
        out.append((w, h))

    for k in (0.75, 0.5625, 0.5, 0.375, 0.25):
        add(nw * k, nh * k)
    for cw, ch in (
        (3840, 2160),
        (2560, 1440),
        (1920, 1080),
        (1600, 900),
        (1280, 720),
        (1024, 576),
        (960, 540),
        (854, 480),
        (800, 450),
        (640, 480),
        (640, 360),
        (480, 270),
        (320, 180),
    ):
        add(cw, ch)
    out.sort(key=lambda t: -(t[0] * t[1]))
    return [(w, h) for w, h in out if (w, h) != (nw, nh)]


def build_gstreamer_pipeline(sensor_id, width=1280, height=720, fps=30, aelock=False):
    """Build nvarguscamerasrc GStreamer pipeline string for Jetson CSI.
    aelock=True locks auto-exposure; manual exposure/gain can be set via V4L2.
    """
    aelock_str = " aelock=1" if aelock else ""
    return (
        f"nvarguscamerasrc sensor-id={sensor_id}{aelock_str} ! "
        f"video/x-raw(memory:NVMM), width={width}, height={height}, format=NV12, framerate={fps}/1 ! "
        "nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! appsink"
    )

def _visiondock_dir():
    d = os.path.join(os.path.expanduser("~"), ".visiondock")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _app_prefs_path():
    return os.path.join(_visiondock_dir(), "app_prefs.json")


def load_app_prefs():
    try:
        p = _app_prefs_path()
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_app_prefs(data: dict):
    try:
        with open(_app_prefs_path(), "w", encoding="utf-8") as f:
            json.dump(data or {}, f, indent=2)
    except Exception as e:
        logging.debug("save_app_prefs: %s", e)


def save_app_prefs_remote_host(ip):
    """Persist Settings → Remote host IP. Empty string clears remote (local Docker)."""
    try:
        d = load_app_prefs()
        d["remote_host_ip"] = (ip or "").strip()
        save_app_prefs(d)
    except Exception as e:
        logging.debug("save_app_prefs_remote_host: %s", e)


def save_app_prefs_sidebar_compact(compact: bool):
    """Persist sidebar compact/expanded preference."""
    try:
        d = load_app_prefs()
        d["sidebar_compact"] = bool(compact)
        save_app_prefs(d)
    except Exception as e:
        logging.debug("save_app_prefs_sidebar_compact: %s", e)


def save_app_prefs_flag(key: str, value):
    """Persist a generic boolean app preference flag."""
    try:
        d = load_app_prefs()
        d[str(key)] = bool(value)
        save_app_prefs(d)
    except Exception as e:
        logging.debug("save_app_prefs_flag(%s): %s", key, e)


def save_app_prefs_value(key: str, value):
    """Persist a generic app preference value."""
    try:
        d = load_app_prefs()
        d[str(key)] = value
        save_app_prefs(d)
    except Exception as e:
        logging.debug("save_app_prefs_value(%s): %s", key, e)


def _load_json_file(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def _deep_merge_dicts(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dicts(out[key], value)
        else:
            out[key] = value
    return out


def _inspection_defaults_path():
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "inspection_profile_defaults.json"))


def _jetson_pin_catalog_path():
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "jetson_pin_catalog.json"))


def default_inspection_profile():
    return _load_json_file(_inspection_defaults_path(), {})


def _inspection_profile_path():
    return os.path.join(_visiondock_dir(), "inspection_profile.json")


def load_inspection_profile():
    defaults = default_inspection_profile()
    saved = _load_json_file(_inspection_profile_path(), {})
    merged = _deep_merge_dicts(defaults, saved)
    if isinstance(merged, dict):
        if not merged.get("camera_name") and merged.get("station_name"):
            merged["camera_name"] = merged.get("station_name")
        if not merged.get("station_name") and merged.get("camera_name"):
            merged["station_name"] = merged.get("camera_name")
    return merged


def save_inspection_profile(profile: dict):
    try:
        with open(_inspection_profile_path(), "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)
    except Exception as e:
        logging.debug("save_inspection_profile: %s", e)


def normalize_runtime_host(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    return (parsed.hostname or raw.strip().strip("/")).strip()


def format_runtime_host_for_url(host: str) -> str:
    text = normalize_runtime_host(host)
    if ":" in text and not text.startswith("["):
        return f"[{text}]"
    return text


def load_jetson_pin_catalog():
    if not hasattr(load_jetson_pin_catalog, "_cache"):
        load_jetson_pin_catalog._cache = _load_json_file(_jetson_pin_catalog_path(), {"boards": []})
    return load_jetson_pin_catalog._cache


def jetson_board_catalog():
    data = load_jetson_pin_catalog()
    boards = data.get("boards") if isinstance(data, dict) else []
    return boards if isinstance(boards, list) else []


def jetson_board_pins(board_id: str):
    for board in jetson_board_catalog():
        if board.get("id") == board_id:
            pins = board.get("pins")
            return pins if isinstance(pins, list) else []
    return []


def jetson_board_pin_info(board_id: str, board_pin):
    if board_pin is None:
        return None
    try:
        bp = int(board_pin)
    except (TypeError, ValueError):
        return None
    for pin in jetson_board_pins(board_id):
        if int(pin.get("board_pin") or 0) == bp:
            return pin
    return None


def format_jetson_pin_label(pin: dict):
    board_pin = pin.get("board_pin")
    cvm = pin.get("cvm") or "GPIO"
    tegra = pin.get("tegra_soc") or ""
    parts = [f"BOARD {board_pin}", str(cvm)]
    if tegra:
        parts.append(str(tegra))
    if pin.get("pwm"):
        parts.append("PWM")
    return " · ".join(parts)


def inspection_pin_display(board_id: str, board_pin):
    info = jetson_board_pin_info(board_id, board_pin)
    if not info:
        return "Disabled"
    return format_jetson_pin_label(info)


def _camera_defaults_path():
    return os.path.join(_visiondock_dir(), "camera_defaults.json")

def get_camera_defaults():
    """Global camera defaults (updated from Settings, persisted to disk)."""
    if not hasattr(get_camera_defaults, "_defaults"):
        get_camera_defaults._defaults = {
            "sensor_model": "GENERIC_CSI",
            "focuser_type": "none",
            "resolution_index": 0,
            "aelock": False,
            "focus_mode": "fixed",  # fixed | manual | auto
            "focus_position": 512,  # 0..1023
            "i2c_bus": 10,  # Motorized focuser I2C bus when present (platform-specific; see docs/CSI_CAMERA_FOCUS.md)
            "sensor_id": 0,  # nvarguscamerasrc sensor-id for autofocus capture
            "v4l2_exposure": 5000,
            "v4l2_gain": 16,
        }
        try:
            p = _camera_defaults_path()
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                for k in get_camera_defaults._defaults:
                    if k in loaded: get_camera_defaults._defaults[k] = loaded[k]
        except Exception:
            pass
    return get_camera_defaults._defaults

def save_camera_defaults_to_disk():
    """Persist current defaults to ~/.visiondock/camera_defaults.json."""
    try:
        d = get_camera_defaults()
        dirpath = os.path.dirname(_camera_defaults_path())
        os.makedirs(dirpath, exist_ok=True)
        with open(_camera_defaults_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception as e:
        logging.debug("save_camera_defaults_to_disk: %s", e)

def set_camera_defaults(
    sensor_model=None,
    focuser_type=None,
    resolution_index=None,
    aelock=None,
    focus_mode=None,
    focus_position=None,
    i2c_bus=None,
    sensor_id=None,
    v4l2_exposure=None,
    v4l2_gain=None,
):
    d = get_camera_defaults()
    if sensor_model is not None:
        d["sensor_model"] = str(sensor_model or "GENERIC_CSI").strip().upper() or "GENERIC_CSI"
    if focuser_type is not None:
        d["focuser_type"] = str(focuser_type or "none").strip().lower() or "none"
    if resolution_index is not None:
        d["resolution_index"] = resolution_index
    if aelock is not None:
        d["aelock"] = bool(aelock)
    if focus_mode is not None:
        d["focus_mode"] = str(focus_mode) if str(focus_mode) in ("fixed", "manual", "auto") else d.get("focus_mode", "fixed")
    if focus_position is not None:
        d["focus_position"] = int(max(0, min(1023, focus_position)))
    if i2c_bus is not None:
        d["i2c_bus"] = int(max(0, min(15, int(i2c_bus))))
    if sensor_id is not None:
        d["sensor_id"] = int(max(0, min(3, int(sensor_id))))
    if v4l2_exposure is not None:
        d["v4l2_exposure"] = int(v4l2_exposure)
    if v4l2_gain is not None:
        d["v4l2_gain"] = int(v4l2_gain)
    save_camera_defaults_to_disk()

class VideoThread(QThread):
    change_pixmap = Signal(np.ndarray)
    analytics_signal = Signal(dict)
    fps_signal = Signal(float)           # rolling FPS (emitted ~1/s)
    connection_signal = Signal(str)      # "connecting" | "connected" | "reconnecting"
    snap_done_signal = Signal(str)       # absolute path of saved snapshot
    
    def __init__(self, src, engine="STANDARD", target_size=None, camera_options=None, recording_label=None):
        super().__init__()
        self.src = src
        self.engine = engine
        self.target_size = target_size
        self.camera_options = camera_options or {}
        self.recording_label = recording_label
        self.running = True
        self.is_recording = False
        self.out = None
        self.snap_req = False

    def _camera_output_dir(self) -> str:
        base = recordings_root_dir()
        sub = camera_recordings_slug(self.recording_label)
        d = os.path.join(base, sub)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            log.warning("Could not create camera recordings dir %s: %s", d, e)
            os.makedirs(base, exist_ok=True)
            return base
        return d

    def toggle_record(self, start=True):
        self.is_recording = start
        if not start and self.out:
            self.out.release(); self.out = None

    def snapshot(self): self.snap_req = True

    def _open_capture(self, source):
        """Open VideoCapture for the source; CSI cameras on Linux use GStreamer pipeline when applicable."""
        try:
            sid = int(source) if str(source).strip().isdigit() else source
        except Exception:
            sid = source
        if isinstance(sid, str) and "://" in sid and not str(sid).strip().isdigit():
            remote = open_cv_capture_remote(sid)
            if remote is not None:
                log.info("Opened remote capture with specialized backend: %s", sid[:80])
                return remote
            # open_cv_capture_remote başarısızsa klasik yolu dene
            fb = cv2.VideoCapture(sid, cv2.CAP_FFMPEG)
            if fb.isOpened():
                _try_set_stream_buffer(fb, 1)
                return fb
        use_gst = isinstance(sid, int) and platform.system() == "Linux" and os.path.exists("/usr/bin/nvgstcapture")
        if use_gst:
            opts = {**get_camera_defaults(), **(self.camera_options or {})}
            idx = opts.get("resolution_index", 0)
            idx = min(max(0, idx), len(CAMERA_RESOLUTION_PRESETS) - 1)
            w, h, fps = CAMERA_RESOLUTION_PRESETS[idx][1], CAMERA_RESOLUTION_PRESETS[idx][2], CAMERA_RESOLUTION_PRESETS[idx][3]
            aelock = opts.get("aelock", False)
            gst_str = build_gstreamer_pipeline(sid, width=w, height=h, fps=fps, aelock=aelock)
            return cv2.VideoCapture(gst_str, cv2.CAP_GSTREAMER)
        cap_fb = cv2.VideoCapture(source)
        if cap_fb.isOpened():
            _try_set_stream_buffer(cap_fb, 1)
        return cap_fb

    def run(self):
        source = self.src
        try:
            if str(source).isdigit(): source = int(source)
        except Exception:
            pass
        src_is_remote = isinstance(source, str) and "://" in str(source)
        consec_fail = 0
        # Ardışık tam okuma hatası: tolere edilen uzun “bozuk akış” penceresi yok; birkaç hatadan sonra yeniden bağlan.
        fail_limit_remote = max(2, min(6, int(os.environ.get("VISIONDOCK_REMOTE_FAIL_STREAK", "4"))))
        fail_limit_local = 4

        log.info("Opening video source: %s", source)
        cap = self._open_capture(source)

        if not cap.isOpened():
            log.warning("Failed to open video source: %s", source)
            return

        self.connection_signal.emit("connected")
        log.info("Stream established: %s", source)
        # Fixed focus: CSI digit source, Linux, focus_mode=fixed — apply once with configured I2C bus
        try:
            sid = int(source) if isinstance(source, int) else (int(source) if str(source).strip().isdigit() else None)
            if sid is not None and platform.system() == "Linux":
                opts = {**get_camera_defaults(), **(self.camera_options or {})}
                focuser_type = str(opts.get("focuser_type") or csi_sensor_default_focuser(opts.get("sensor_model"))).strip().lower()
                script_name = focus_scripts_for_focuser(focuser_type).get("focus")
                if opts.get("focus_mode") == "fixed" and focuser_available(focuser_type) and script_name:
                    pos = int(opts.get("focus_position", 512))
                    bus = int(opts.get("i2c_bus", 10))
                    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", script_name)
                    root = os.path.dirname(os.path.dirname(__file__))
                    if os.path.exists(script_path):
                        r = subprocess.run(
                            [sys.executable, script_path, "--bus", str(bus), "--position", str(pos)],
                            cwd=root,
                            capture_output=True,
                            timeout=5,
                            text=True,
                            errors="replace",
                        )
                        if r.returncode != 0:
                            err = ((r.stderr or "") + (r.stdout or "")).strip()
                            logging.warning("Fixed focus script failed (bus=%s pos=%s): %s", bus, pos, err or r.returncode)
        except Exception as e:
            logging.debug("fixed focus apply: %s", e)
        _fps_t0 = time.monotonic()
        _fps_count = 0
        _pace_last = time.monotonic()
        while self.running:
            if cap is None or not cap.isOpened():
                self.connection_signal.emit("reconnecting")
                log.warning("Reconnecting to %s", source)
                cap = self._open_capture(source)
                if cap is not None and cap.isOpened():
                    self.connection_signal.emit("connected")
                consec_fail = 0
                time.sleep(1.2)
                continue

            if src_is_remote:
                ret, frame = _video_capture_read_latest(cap)
            else:
                ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                consec_fail += 1
                log.debug("Frame drop on %s (streak=%s)", source, consec_fail)
                lim = fail_limit_remote if src_is_remote else fail_limit_local
                if consec_fail < lim:
                    time.sleep(0.008)
                    continue
                try:
                    cap.release()
                except Exception:
                    pass
                cap = None
                consec_fail = 0
                time.sleep(0.2)
                continue

            consec_fail = 0

            if self.target_size and len(self.target_size) == 2 and frame is not None:
                frame = cv2.resize(frame, (self.target_size[0], self.target_size[1]), interpolation=cv2.INTER_LINEAR)

            # AI & NVR Layer...
            frame, meta = VisionAnalytics.process(frame, self.engine)
            if meta: self.analytics_signal.emit(meta)
            
            # 2. Snapshot Layer (per-camera subfolder under gui/recordings/<slug>/)
            if self.snap_req:
                rec_dir = self._camera_output_dir()
                path = os.path.join(rec_dir, f"SNAP_{datetime.now().strftime('%m%d_%H%M%S')}.jpg")
                cv2.imwrite(path, frame)
                self.snap_req = False
                self.snap_done_signal.emit(path)

            if self.is_recording:
                rec_frame = frame.copy()
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cv2.putText(rec_frame, f"{ts} | {self.engine}", (10, frame.shape[0]-20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                if self.out is None:
                    rec_dir = self._camera_output_dir()
                    path = os.path.join(rec_dir, f"REC_{datetime.now().strftime('%m%d_%H%M%S')}.avi")
                    self.out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'MJPG'), 20, (frame.shape[1], frame.shape[0]))
                self.out.write(rec_frame)
            
            # Rolling FPS — emit once per second
            _fps_count += 1
            _now = time.monotonic()
            if _now - _fps_t0 >= 1.0:
                self.fps_signal.emit(_fps_count / (_now - _fps_t0))
                _fps_t0 = _now
                _fps_count = 0
            self.change_pixmap.emit(frame)
            smf = float(self.camera_options.get("stream_max_fps") or 0)
            nowp = time.monotonic()
            if smf > 0:
                period = 1.0 / smf
                dt = nowp - _pace_last
                if dt < period:
                    time.sleep(period - dt)
                _pace_last = time.monotonic()
            else:
                _pace_last = nowp
                if not src_is_remote:
                    time.sleep(0.01)
        if self.out: self.out.release(); self.out = None
        if cap: cap.release()

    def stop(self):
        self.running = False
        self.wait(2000) # Wait up to 2s for clean exit
        if self.isRunning(): self.terminate()

class StatsThread(QThread):
    updated = Signal(dict)
    def __init__(self): super().__init__(); self.running = True
    def run(self):
        while self.running:
            try:
                stats = {
                    'cpu': psutil.cpu_percent(),
                    'ram': psutil.virtual_memory().percent,
                    'disk': psutil.disk_usage('/').percent,
                    'gpu': 0, 'temp': 0
                }
                # Jetson Specific Metrics
                if os.path.exists("/usr/bin/tegrastats"):
                    try:
                        out = subprocess.check_output("tegrastats --single", shell=True).decode()
                        if "GR3D_FREQ" in out:
                            stats['gpu'] = int(out.split("GR3D_FREQ")[1].split("%")[0].strip())
                    except: pass
                elif platform.system() == "Darwin":
                    stats['gpu'] = np.random.randint(5, 15) # Simulated for M-series
                
                self.updated.emit(stats); time.sleep(2)
            except: pass
    def stop(self):
        self.running = False
        if not self.wait(5000):
            self.terminate()
            self.wait(1500)

class HardwareWorker(QThread):
    finished = Signal(dict)
    def run(self):
        try:
            # Gather data in background to avoid UI lag
            cams = list_cameras() or []
            peers = get_zerotier_peers() or []
            local_ips = get_zerotier_local_ips() or []
            devices = DBManager().get_devices() or []
            my_nid = get_zerotier_local_node_id() or ""
            managed_prefixes = get_zerotier_managed_ipv4_prefixes() or []
            
            self.finished.emit({
                "cams": cams,
                "peers": peers,
                "local_ips": local_ips,
                "devices": devices,
                "my_nid": my_nid,
                "zt_prefs": managed_prefixes
            })
        except Exception as e:
            print(f"HardwareWorker error: {e}")
            self.finished.emit({})

class App(QMainWindow):

    def __init__(self):
        super().__init__(); self.resize(1200, 800)
        self._production_mode = is_production_mode()
        self._remote_host_locked = is_remote_host_locked()
        _title = "VisionDock"
        if self._production_mode:
            _title += " · Production"
        self.setWindowTitle(_title)
        self.is_dark = True # Forced Industrial Dark
        log.info(
            "Starting (production=%s remote_ip_locked=%s)",
            self._production_mode,
            self._remote_host_locked,
        )
        self.db = DBManager()
        prefs = load_app_prefs()
        self._camera_card_geom = self._load_camera_card_geom()
        self._camera_geom_save_timer = None
        self._ssh_sessions = {}
        self._cam_reconnect_notify_ts = {}
        # ZeroTier peer satırında "ZT IP Al" ile çekilen sanal IP'ler (anahtar: node id veya path:ip)
        self._zt_peer_cached_ips = {}
        # Bağlı SSH oturumunda uzakta zerotier-cli info → düğüm ID (yinelenen peer satırını gizlemek için)
        self._remote_zt_node_by_session = {}
        self.active_remote_host = None # Global active node IP for Docker/Cameras
        self.active_cids = set(); self.active_srcs = set()
        self._camera_preview_enabled_sources = set()
        self._sidebar_compact = bool(prefs.get("sidebar_compact", True))
        self._sidebar_width_compact = 108
        self._sidebar_width_expanded = 286
        self._nav_specs = []
        self._auto_camera_preview_on_launch = bool(prefs.get("auto_camera_preview_on_launch", False))
        self._background_health_checks_enabled = bool(prefs.get("background_health_checks_enabled", False))
        self._check_remote_on_settings_open = bool(prefs.get("check_remote_on_settings_open", False))
        self._scheduler_policy = str(prefs.get("scheduler_policy", "manual")).strip().lower()
        if self._scheduler_policy not in ("manual", "balanced", "full", "custom"):
            self._scheduler_policy = "manual"
        self._ui_role_mode = str(prefs.get("ui_role_mode", "operator")).strip().lower()
        if self._ui_role_mode not in ("operator", "engineering"):
            self._ui_role_mode = "operator"
        self._confirm_engineering_mode_switch = bool(
            prefs.get("confirm_engineering_mode_switch", True)
        )
        self._operator_quick_tour_seen = bool(prefs.get("operator_quick_tour_seen", False))
        self._operator_tour_timers = []
        self._show_setup_wizard_on_launch = bool(prefs.get("show_setup_wizard_on_launch", False))
        self._devices_last_scan_ts = 0.0
        self._devices_scan_ttl_sec = 45.0
        self._devices_auto_refresh_enabled = bool(prefs.get("devices_auto_refresh_enabled", False))
        self._inspection_runtime_state = {}
        self._inspection_runtime_online = None
        self._inspection_runtime_last_error = ""
        c = QWidget()
        c.setObjectName("MainCanvas")
        self.setCentralWidget(c)
        self.main = QHBoxLayout(c)
        self.main.setContentsMargins(0, 0, 0, 0)
        self.main.setSpacing(0)
        self.init_ui(); self.apply_theme()
        self.stats = StatsThread(); self.stats.updated.connect(self.upd_stats); self.stats.start()
        QTimer.singleShot(300, self.load_data)
        QTimer.singleShot(600, self._schedule_remote_status_check)
        # Setup wizard is opt-in on launch; avoid blocking startup for operators.
        if self._show_setup_wizard_on_launch:
            QTimer.singleShot(800, self._show_onboarding_wizard)
        updates_enabled = bool(self._background_health_checks_enabled)
        if self._scheduler_policy == "manual":
            updates_enabled = False
        elif self._scheduler_policy in ("balanced", "full"):
            updates_enabled = True
        if updates_enabled:
            QTimer.singleShot(6000, self._check_for_updates)
        # Start runtime health polling immediately so camera state is ready
        # before the user opens the Cameras page for the first time.
        self._inspection_runtime_timer = QTimer(self)
        self._inspection_runtime_timer.setInterval(5000)
        self._inspection_runtime_timer.timeout.connect(self._safe_refresh_camera_runtime)
        if self._scheduler_policy in ("manual", "balanced", "full"):
            self._apply_scheduler_policy(self._scheduler_policy, persist=False, notify=False)
        self._apply_background_health_checks_policy(run_initial_check=True)
        self._devices_auto_refresh_timer = QTimer(self)
        self._devices_auto_refresh_timer.setInterval(60000)
        self._devices_auto_refresh_timer.timeout.connect(self._on_devices_auto_refresh_tick)
        self._apply_devices_auto_refresh_policy()
        self._setup_shortcuts()
        
        # Ensure it fits the screen properly
        self.showMaximized()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        for ch in self.findChildren(QWidget):
            if ch.objectName() == "Overlay" and ch.isVisible():
                ch.setGeometry(0, 0, self.width(), self.height())

    def _camera_card_geom_path(self) -> str:
        d = os.path.join(os.path.expanduser("~"), ".visiondock")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "camera_card_sizes.json")

    def _load_camera_card_geom(self) -> dict:
        p = self._camera_card_geom_path()
        try:
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            out = {}
            for k, v in raw.items():
                if isinstance(v, dict) and "w" in v and "h" in v:
                    w, h = int(v["w"]), int(v["h"])
                    out[str(k)] = (max(280, min(w, 2400)), max(260, min(h, 1800)))
            return out
        except Exception:
            return {}

    def _save_camera_card_geom(self):
        p = self._camera_card_geom_path()
        try:
            raw = {k: {"w": w, "h": h} for k, (w, h) in self._camera_card_geom.items()}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2)
        except Exception as e:
            log.debug("save camera card geom: %s", e)

    def _schedule_persist_camera_card_geom(self, src, w, h):
        if not src:
            return
        self._camera_card_geom[src] = (
            max(280, min(int(w), 2400)),
            max(260, min(int(h), 1800)),
        )
        if self._camera_geom_save_timer is None:
            self._camera_geom_save_timer = QTimer(self)
            self._camera_geom_save_timer.setSingleShot(True)
            self._camera_geom_save_timer.timeout.connect(self._save_camera_card_geom)
        self._camera_geom_save_timer.stop()
        self._camera_geom_save_timer.start(400)

    def forget_camera_card_geom(self, src):
        if not src:
            return
        self._camera_card_geom.pop(src, None)
        self._save_camera_card_geom()

    def load_data(self):
        # A1: Global tooltip font polish
        QToolTip.setFont(QFont("-apple-system", 12))
        # Load persisted cameras
        for name, src, meta in self.db.get_cameras():
            self.add_cam_logic(name, src, meta, save=False)
        # Load persisted workspaces; Running/Stopped from actual Docker state
        try:
            running_ids = set()
            for c in DockerManager.list_containers():
                cid = (c.get("id") or "")[:12]
                if cid and (c.get("status") or "").lower().startswith("up"):
                    running_ids.add(cid)
        except Exception:
            running_ids = set()
        for name, img, cid, host in self.db.get_workspaces():
            ckey = (cid or "")[:12]
            running = ckey in running_ids
            self.add_docker_card(name, img, cid, running=running, save=False, host=host)

    def init_ui(self):
        self.sidebar_frame = QFrame()
        self.sidebar_frame.setObjectName("Sidebar")
        self.sidebar_frame.setFixedWidth(
            self._sidebar_width_compact if self._sidebar_compact else self._sidebar_width_expanded
        )
        self.sidebar_frame.setProperty("compact", self._sidebar_compact)
        sl = QVBoxLayout(self.sidebar_frame)
        sl.setContentsMargins(14, 18, 14, 16)
        sl.setSpacing(10)

        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(0, 0, 0, 10)
        logo_row.setSpacing(10)
        self._sidebar_logo_img = QLabel()
        self._sidebar_logo_img.setFixedSize(44, 44)
        pix = QPixmap(resource_path("visiondock.svg"))
        if not pix.isNull():
            self._sidebar_logo_img.setPixmap(
                pix.scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        logo_row.addWidget(self._sidebar_logo_img, 0, Qt.AlignmentFlag.AlignTop)

        self._sidebar_brand_wrap = QWidget()
        brand_text = QVBoxLayout(self._sidebar_brand_wrap)
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(2)
        self._sidebar_brand_title = QLabel("VisionDock")
        self._sidebar_brand_title.setObjectName("BrandTitle")
        self._sidebar_brand_tag = QLabel("INDUSTRIAL VISION")
        self._sidebar_brand_tag.setObjectName("BrandTagline")
        brand_text.addWidget(self._sidebar_brand_title)
        brand_text.addWidget(self._sidebar_brand_tag)
        logo_row.addWidget(self._sidebar_brand_wrap, 1)
        sl.addLayout(logo_row)

        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 6)
        self._sidebar_toggle_btn = QPushButton("→")
        self._sidebar_toggle_btn.setObjectName("SidebarToggle")
        self._sidebar_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sidebar_toggle_btn.clicked.connect(self._toggle_sidebar_compact_mode)
        toggle_row.addWidget(self._sidebar_toggle_btn)
        sl.addLayout(toggle_row)

        self._sidebar_scope_label = QLabel("FLEET READY")
        self._sidebar_scope_label.setObjectName("SidebarScopeBadge")
        sl.addWidget(self._sidebar_scope_label, 0, Qt.AlignmentFlag.AlignLeft)

        self.eco_mode = False
        self.tabs = FadeStackedWidget(); self.navs = []
        # Order: Dashboard, Inspection, Models, Devices, Results, Settings
        self._nav_specs = [
            ("DB", t("nav.dashboard", "Dashboard")),
            ("IN", t("nav.inspection", "Inspection")),
            ("MD", t("nav.models", "Models")),
            ("DV", t("nav.devices", "Devices")),
            ("RS", t("nav.results", "Results")),
            ("ST", t("nav.settings", "Settings")),
        ]
        for i, (indicator, nav_label) in enumerate(self._nav_specs):
            b = QPushButton(nav_label)
            b.setObjectName("NavTab")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(46)
            b.setToolTip(nav_label)
            b.setProperty("navIndicator", indicator)
            b.setProperty("navLabel", nav_label)
            b.clicked.connect(lambda _, x=i: self.switch(x))
            sl.addWidget(b)
            self.navs.append(b)
        sl.addStretch()
        self._sidebar_host_label = QLabel(platform.node())
        self._sidebar_host_label.setObjectName("SidebarFootnote")
        sl.addWidget(self._sidebar_host_label)
        self.main.addWidget(self.sidebar_frame)
        self.main.addWidget(self.tabs)
        # Stack order matches nav: 0=home, 1=cams, 2=docker, 3=devices, 4=library, 5=settings
        self.tabs.addWidget(self.page_home()); self.tabs.addWidget(self.page_cams()); self.tabs.addWidget(self.page_docker()); self.tabs.addWidget(self.page_devices()); self.tabs.addWidget(self.page_library()); self.tabs.addWidget(self.page_settings())
        self._apply_sidebar_mode(refresh_labels=True)
        self.navs[0].setChecked(True)

    def _toggle_sidebar_compact_mode(self):
        self._sidebar_compact = not bool(getattr(self, "_sidebar_compact", True))
        save_app_prefs_sidebar_compact(self._sidebar_compact)
        self._apply_sidebar_mode(refresh_labels=True)

    def _apply_sidebar_mode(self, refresh_labels=False):
        compact = bool(getattr(self, "_sidebar_compact", True))
        sidebar = getattr(self, "sidebar_frame", None)
        if sidebar is not None:
            sidebar.setFixedWidth(self._sidebar_width_compact if compact else self._sidebar_width_expanded)
            sidebar.setProperty("compact", compact)
            style = sidebar.style()
            if style is not None:
                style.unpolish(sidebar)
                style.polish(sidebar)
            sidebar.update()

        toggle_btn = getattr(self, "_sidebar_toggle_btn", None)
        if toggle_btn is not None:
            toggle_btn.setText("→" if compact else "←")
            toggle_btn.setToolTip("Expand sidebar" if compact else "Collapse sidebar")

        brand_wrap = getattr(self, "_sidebar_brand_wrap", None)
        if brand_wrap is not None:
            brand_wrap.setVisible(not compact)
        host_lbl = getattr(self, "_sidebar_host_label", None)
        if host_lbl is not None:
            host_lbl.setVisible(not compact)
        scope_lbl = getattr(self, "_sidebar_scope_label", None)
        if scope_lbl is not None:
            scope_lbl.setText("FLEET" if compact else "FLEET READY")

        for btn in getattr(self, "navs", []):
            if refresh_labels:
                full_label = str(btn.property("navLabel") or "").strip()
                indicator = str(btn.property("navIndicator") or "").strip()
                btn.setText(indicator if compact else full_label)
                btn.setToolTip(full_label)
            btn.setProperty("compact", compact)
            bstyle = btn.style()
            if bstyle is not None:
                bstyle.unpolish(btn)
                bstyle.polish(btn)
            btn.update()

    def _apply_background_health_checks_policy(self, run_initial_check=False):
        enabled = bool(getattr(self, "_background_health_checks_enabled", False))
        timer = getattr(self, "_inspection_runtime_timer", None)
        if timer is not None:
            if enabled and not timer.isActive():
                timer.start()
            elif not enabled and timer.isActive():
                timer.stop()
        if enabled and run_initial_check:
            QTimer.singleShot(1200, self._safe_refresh_camera_runtime)

    def _scheduler_presets(self):
        return {
            "manual": {
                "background_health_checks_enabled": False,
                "auto_camera_preview_on_launch": False,
                "check_remote_on_settings_open": False,
            },
            "balanced": {
                "background_health_checks_enabled": True,
                "auto_camera_preview_on_launch": False,
                "check_remote_on_settings_open": False,
            },
            "full": {
                "background_health_checks_enabled": True,
                "auto_camera_preview_on_launch": True,
                "check_remote_on_settings_open": True,
            },
        }

    def _apply_scheduler_policy(self, policy: str, persist=True, notify=False):
        policy_norm = str(policy or "").strip().lower()
        presets = self._scheduler_presets()
        if policy_norm not in presets:
            return
        p = presets[policy_norm]
        self._background_health_checks_enabled = bool(p["background_health_checks_enabled"])
        self._auto_camera_preview_on_launch = bool(p["auto_camera_preview_on_launch"])
        self._check_remote_on_settings_open = bool(p["check_remote_on_settings_open"])
        self._scheduler_policy = policy_norm
        if persist:
            save_app_prefs_value("scheduler_policy", self._scheduler_policy)
            save_app_prefs_flag("background_health_checks_enabled", self._background_health_checks_enabled)
            save_app_prefs_flag("auto_camera_preview_on_launch", self._auto_camera_preview_on_launch)
            save_app_prefs_flag("check_remote_on_settings_open", self._check_remote_on_settings_open)
        self._apply_background_health_checks_policy(run_initial_check=self._background_health_checks_enabled)
        self._refresh_scheduler_profile_control()
        self._refresh_settings_hub()
        if notify:
            self.notify_info(f"Scheduler profile applied: {self._scheduler_policy}.")

    def set_scheduler_profile(self, policy: str):
        self._apply_scheduler_policy(policy, persist=True, notify=True)

    def _mark_scheduler_policy_custom(self):
        if getattr(self, "_scheduler_policy", "manual") == "custom":
            return
        self._scheduler_policy = "custom"
        save_app_prefs_value("scheduler_policy", "custom")
        self._refresh_scheduler_profile_control()

    def _refresh_scheduler_profile_control(self):
        combo = getattr(self, "_maintenance_scheduler_profile_combo", None)
        target = getattr(self, "_scheduler_policy", "manual")
        if combo is not None:
            idx = combo.findData(target)
            if idx < 0:
                idx = combo.findData("custom")
            if idx >= 0:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
        summary = getattr(self, "_maintenance_scheduler_summary_label", None)
        if summary is not None:
            text_by_profile = {
                "manual": "Manual: no background checks, no preview auto-start, no automatic remote check.",
                "balanced": "Balanced: background checks enabled, preview auto-start off, Settings-open check off.",
                "full": "Full: background checks, preview auto-start, and Settings-open remote checks enabled.",
                "custom": "Custom: one or more automation switches differ from the standard presets.",
            }
            summary.setText(text_by_profile.get(target, text_by_profile["custom"]))

    def toggle_background_health_checks(self, enabled):
        self._mark_scheduler_policy_custom()
        self._background_health_checks_enabled = bool(enabled)
        save_app_prefs_flag("background_health_checks_enabled", self._background_health_checks_enabled)
        self._apply_background_health_checks_policy(run_initial_check=self._background_health_checks_enabled)
        self.notify_info(
            "Background runtime checks enabled."
            if self._background_health_checks_enabled
            else "Background runtime checks disabled."
        )

    def toggle_camera_preview_autostart(self, enabled):
        self._mark_scheduler_policy_custom()
        self._auto_camera_preview_on_launch = bool(enabled)
        save_app_prefs_flag("auto_camera_preview_on_launch", self._auto_camera_preview_on_launch)
        self.notify_info(
            "Camera preview auto-start enabled."
            if self._auto_camera_preview_on_launch
            else "Camera preview auto-start disabled."
        )

    def toggle_remote_check_on_settings_open(self, enabled):
        self._mark_scheduler_policy_custom()
        self._check_remote_on_settings_open = bool(enabled)
        save_app_prefs_flag("check_remote_on_settings_open", self._check_remote_on_settings_open)
        self.notify_info(
            "Remote status check on Settings open enabled."
            if self._check_remote_on_settings_open
            else "Remote status check on Settings open disabled."
        )

    def toggle_setup_wizard_on_launch(self, enabled):
        self._show_setup_wizard_on_launch = bool(enabled)
        save_app_prefs_flag("show_setup_wizard_on_launch", self._show_setup_wizard_on_launch)
        self.notify_info(
            "Production Setup will open on app launch."
            if self._show_setup_wizard_on_launch
            else "Production Setup auto-open disabled."
        )

    def _apply_devices_auto_refresh_policy(self):
        timer = getattr(self, "_devices_auto_refresh_timer", None)
        if timer is None:
            return
        if bool(getattr(self, "_devices_auto_refresh_enabled", False)):
            if not timer.isActive():
                timer.start()
        else:
            timer.stop()

    def _on_devices_auto_refresh_tick(self):
        if not bool(getattr(self, "_devices_auto_refresh_enabled", False)):
            return
        tabs = getattr(self, "tabs", None)
        if tabs is None or tabs.currentIndex() != 3:
            return
        self.refresh_devices_page(force=True)

    def toggle_devices_auto_refresh(self, enabled):
        self._devices_auto_refresh_enabled = bool(enabled)
        save_app_prefs_flag("devices_auto_refresh_enabled", self._devices_auto_refresh_enabled)
        self._apply_devices_auto_refresh_policy()
        self.notify_info(
            "Devices auto-refresh enabled (1 minute interval)."
            if self._devices_auto_refresh_enabled
            else "Devices auto-refresh disabled."
        )

    def set_ui_role_mode(self, role: str):
        role_norm = str(role or "").strip().lower()
        if role_norm not in ("operator", "engineering"):
            role_norm = "operator"
        current_role = getattr(self, "_ui_role_mode", "operator")
        if (
            role_norm == "engineering"
            and current_role != "engineering"
            and bool(getattr(self, "_confirm_engineering_mode_switch", True))
        ):
            mb = QMessageBox(self)
            mb.setIcon(QMessageBox.Icon.Warning)
            mb.setWindowTitle("Switch to Engineering mode")
            mb.setText(
                "Engineering mode unlocks advanced runtime, network, and hardware controls.\n"
                "Use it only for commissioning and maintenance."
            )
            mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            mb.setDefaultButton(QMessageBox.StandardButton.No)
            noask = QCheckBox("Do not ask again")
            mb.setCheckBox(noask)
            if mb.exec() != QMessageBox.StandardButton.Yes:
                role_switch = getattr(self, "_settings_role_switch", None)
                if role_switch is not None:
                    role_switch.blockSignals(True)
                    role_switch.setChecked(False)
                    role_switch.blockSignals(False)
                return
            if noask.isChecked():
                self._confirm_engineering_mode_switch = False
                save_app_prefs_flag("confirm_engineering_mode_switch", False)
        if role_norm == getattr(self, "_ui_role_mode", "operator"):
            self._apply_settings_role_mode()
            return
        self._ui_role_mode = role_norm
        save_app_prefs_value("ui_role_mode", self._ui_role_mode)
        if self._ui_role_mode != "operator":
            self._cancel_operator_quick_tour()
        self._apply_settings_role_mode()
        if self._ui_role_mode == "operator":
            tabs = getattr(self, "tabs", None)
            if tabs is not None and tabs.currentIndex() == 5:
                self._show_operator_quick_tour_once()
        self.notify_info(
            "Engineering mode enabled."
            if self._ui_role_mode == "engineering"
            else "Operator mode enabled."
        )

    def _apply_settings_role_mode(self):
        engineering_mode = bool(getattr(self, "_ui_role_mode", "operator") == "engineering")
        role_switch = getattr(self, "_settings_role_switch", None)
        if role_switch is not None:
            role_switch.blockSignals(True)
            role_switch.setChecked(engineering_mode)
            role_switch.blockSignals(False)
        advanced = getattr(self, "_settings_advanced_section", None)
        if advanced is not None and hasattr(advanced, "content") and hasattr(advanced, "toggle_btn"):
            advanced.content.setVisible(engineering_mode)
            advanced.toggle_btn.setText("▼" if engineering_mode else "▶")
        eng_btn = getattr(self, "_settings_hub_engineering_btn", None)
        if eng_btn is not None:
            eng_btn.setVisible(engineering_mode)
        tabs = getattr(self, "_settings_tabs", None)
        eng_idx = getattr(self, "_settings_engineering_tab_index", None)
        if tabs is not None and isinstance(eng_idx, int):
            try:
                tabs.tabBar().setTabVisible(eng_idx, engineering_mode)
            except Exception:
                pass
            if (not engineering_mode) and tabs.currentIndex() == eng_idx:
                tabs.setCurrentIndex(0)
        role_hint = getattr(self, "_settings_role_hint_label", None)
        if role_hint is not None:
            if engineering_mode:
                role_hint.setText(
                    "Engineering mode: advanced runtime, network, and hardware controls are unlocked."
                )
            else:
                role_hint.setText(
                    "Operator mode: advanced runtime, network, and hardware controls remain locked for safety."
                )
        lock_hint = getattr(self, "_settings_lock_hint_label", None)
        if lock_hint is not None:
            lock_hint.setVisible(not engineering_mode)
        self._apply_operator_role_restrictions()

    def _apply_operator_role_restrictions(self):
        engineering_mode = bool(getattr(self, "_ui_role_mode", "operator") == "engineering")
        restricted_names = (
            "remote_host_input",
            "_zt_nwid_input",
            "_zt_join_btn",
            "_zt_leave_btn",
            "_maintenance_import_btn",
            "_inspection_camera_name_input",
            "_inspection_board_combo",
            "_inspection_backend_combo",
            "_inspection_trigger_mode_combo",
            "_inspection_runtime_host_input",
            "_inspection_runtime_port_spin",
            "_inspection_active_level_combo",
            "_inspection_camera_backend_combo",
            "_inspection_camera_model_combo",
            "_inspection_camera_focuser_combo",
            "_inspection_camera_sensor_spin",
            "_inspection_camera_device_spin",
            "_inspection_camera_width_spin",
            "_inspection_camera_height_spin",
            "_inspection_camera_fps_spin",
            "_inspection_camera_burst_spin",
            "_inspection_camera_source_input",
            "_inspection_gpio_enable_switch",
            "_inspection_trigger_pin_combo",
            "_inspection_pass_pin_combo",
            "_inspection_fail_pin_combo",
            "_inspection_fault_pin_combo",
            "_inspection_busy_pin_combo",
            "_inspection_camera_apply_preset_btn",
            "_inspection_pin_reload_btn",
        )
        for name in restricted_names:
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(engineering_mode)
                widget.setProperty("roleLocked", (not engineering_mode))
                if not engineering_mode:
                    widget.setToolTip("This control requires Engineering mode.")
                else:
                    widget.setToolTip("")
                style = widget.style()
                if style is not None:
                    style.unpolish(widget)
                    style.polish(widget)
                widget.update()
            lock_labels = getattr(self, "_settings_lock_labels", {}) or {}
            lock_lbl = lock_labels.get(name)
            if lock_lbl is not None:
                base = str(lock_lbl.property("baseText") or "").strip() or lock_lbl.text().replace("🔒 ", "")
                lock_lbl.setText(base if engineering_mode else f"🔒 {base}")

    def _show_operator_quick_tour_once(self):
        self._cancel_operator_quick_tour()
        if getattr(self, "_ui_role_mode", "operator") != "operator":
            return
        if bool(getattr(self, "_operator_quick_tour_seen", False)):
            return
        self._operator_quick_tour_seen = True
        save_app_prefs_flag("operator_quick_tour_seen", True)
        self.notify_info("Operator mode is active. Advanced controls remain locked for safety.")
        self._queue_operator_tour_message(900, "Use 'Manual' automation profile for the most predictable runtime behavior.")
        self._queue_operator_tour_message(1800, "Keep camera preview auto-start off unless your line requires continuous visual monitoring.")
        self._queue_operator_tour_message(2700, "Need commissioning changes? Switch to Engineering mode in Settings.")

    def _queue_operator_tour_message(self, delay_ms: int, message: str):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._emit_operator_tour_message(message))
        self._operator_tour_timers.append(timer)
        timer.start(max(0, int(delay_ms)))

    def _emit_operator_tour_message(self, message: str):
        if getattr(self, "_ui_role_mode", "operator") != "operator":
            return
        self.notify_info(message)

    def _cancel_operator_quick_tour(self):
        timers = list(getattr(self, "_operator_tour_timers", []) or [])
        for timer in timers:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
        self._operator_tour_timers = []

    def page_home(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(40, 40, 40, 40); l.setSpacing(0)
        pt = QLabel(t("nav.dashboard", "Dashboard")); pt.setObjectName("PageTitle"); l.addWidget(pt); l.addSpacing(30)
        
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setStyleSheet("background:transparent; border:none;")
        cnt = QWidget(); fl = FlowLayout(cnt); fl.setContentsMargins(0,0,0,0); fl.setSpacing(20)
        
        def mk_card(title, tag, page_idx):
            c = QFrame(); c.setObjectName("InfoCard")
            c.setMinimumSize(286, 182)
            c.setMaximumSize(334, 210)
            c.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            cl = QVBoxLayout(c); cl.setContentsMargins(25,25,25,25); cl.setSpacing(15)
            
            # Stylized Badge instead of Emoji
            bh = QHBoxLayout()
            badge = QFrame(); badge.setFixedSize(40, 40)
            badge.setObjectName("HomeBadgeFrame")
            bl = QVBoxLayout(badge); bl.setAlignment(Qt.AlignmentFlag.AlignCenter); bl.setContentsMargins(0,0,0,0)
            final_obj = QLabel(tag); final_obj.setObjectName("HomeBadgeText"); bl.addWidget(final_obj)
            bh.addWidget(badge); bh.addStretch(); cl.addLayout(bh)
            
            cl.addStretch()
            tl = QLabel(title); tl.setObjectName("HomeCardTitle"); cl.addWidget(tl)
            sl = QLabel(t("home.subtitle", "Access and manage module")); sl.setObjectName("CaptionMutedSm"); cl.addWidget(sl)
            
            c.setCursor(Qt.CursorShape.PointingHandCursor)
            c.mousePressEvent = lambda e: self.switch(page_idx)
            return c

        fl.addWidget(mk_card(t("home.card.inspection", "Inspection"), "CAM", 1))
        fl.addWidget(mk_card(t("home.card.models", "Model Packages"), "AI", 2))
        fl.addWidget(mk_card(t("home.card.devices", "Devices"), "NET", 3))
        fl.addWidget(mk_card(t("home.card.results", "Results"), "HIS", 4))
        
        sa.setWidget(cnt); l.addWidget(sa, 1); return w

    def page_cams(self):
        return build_inspection_page(self, _inspection_page_helpers())

    def refresh_cams(self):
        self.refresh_cameras()

    def page_docker(self):
        return build_models_page(self, _models_page_helpers())

    def page_devices(self):
        """Unified Hardware Hub - Device-Centric Grid"""
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(40, 40, 40, 40); l.setSpacing(0)
        hdr = QHBoxLayout()
        pt = QLabel("Devices"); pt.setObjectName("PageTitle"); hdr.addWidget(pt)
        hdr.addStretch(1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setObjectName("BtnSecondary")
        refresh_btn.setFixedHeight(32)
        refresh_btn.clicked.connect(lambda: self.refresh_devices_page(force=True))
        hdr.addWidget(refresh_btn)
        l.addLayout(hdr); l.addSpacing(22)
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setStyleSheet("background: transparent; border: none;")
        cnt = QWidget(); self.dev_hub_layout = FlowLayout(cnt); self.dev_hub_layout.setContentsMargins(0, 0, 0, 0); self.dev_hub_layout.setSpacing(25)
        sa.setWidget(cnt); l.addWidget(sa, 1)
        QTimer.singleShot(100, lambda: self.refresh_devices_page(force=True))
        return w

    def page_library(self):
        return build_results_page(self, _results_page_helpers())

    def _delete_selected_result(self):
        btn = getattr(self, "_results_detail_delete_btn", None)
        inspection_id = btn.property("inspection_id") if btn else ""
        if not inspection_id:
            self.notify_warning("No result selected to delete")
            return
            
        if self._runtime_is_local():
            import runtime.storage as st
            self.notify_success(f"Deleting local record {inspection_id}...")
            st.delete_result(inspection_id)
            self.refresh_library()
            return
        if getattr(self, "_results_delete_thread", None) and self._results_delete_thread.isRunning():
            return
            
        url = self._inspection_runtime_url(f"/result/{inspection_id}")
        self._results_delete_thread = InspectionRuntimeRequestThread(url, method="DELETE", timeout=5.0)
        self._results_delete_thread.result_signal.connect(lambda r: [self.notify_success("Record deleted"), self.refresh_library()])
        self._results_delete_thread.error_signal.connect(lambda e: self.notify_error(f"Failed to delete: {e}"))
        self._results_delete_thread.start()

    def _delete_result_record(self, inspection_id: str):
        target = str(inspection_id or "").strip()
        if not target:
            self.notify_warning("No result selected to delete")
            return
        if self._runtime_is_local():
            import runtime.storage as st
            st.delete_result(target)
            self.notify_success(f"Deleted result {target}")
            self.refresh_library()
            return
        if getattr(self, "_results_delete_thread", None) and self._results_delete_thread.isRunning():
            return
        url = self._inspection_runtime_url(f"/result/{target}")
        self._results_delete_thread = InspectionRuntimeRequestThread(url, method="DELETE", timeout=5.0)
        self._results_delete_thread.result_signal.connect(lambda _r: [self.notify_success(f"Deleted result {target}"), self.refresh_library()])
        self._results_delete_thread.error_signal.connect(lambda e: self.notify_error(f"Failed to delete: {e}"))
        self._results_delete_thread.start()

    def refresh_library(self):
        lay = getattr(self, "media_hub_layout", None)
        if not lay:
            return
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

        if self._runtime_is_local():
            records = load_inspection_result_records(limit=60)
        else:
            records = load_runtime_result_records(self._inspection_runtime_url("/results"), limit=60)
            if not records:
                records = load_inspection_result_records(limit=60)
        if records:
            for record in records:
                decision = str(record.get("decision") or "unknown").strip().lower()
                image_path = str(record.get("image_path") or "").strip()
                camera_name = record.get("camera_name") or record.get("station_name") or "Runtime Node"
                ts = self._format_result_timestamp(record.get("captured_at"))
                defects = record.get("defect_classes") or []
                decision_color = {
                    "pass": "#30D158",
                    "fail": "#FF453A",
                    "fault": "#FF453A",
                    "uncertain": "#FF9F0A",
                }.get(decision, "#A1A1AA")

                card = QFrame()
                card.setObjectName("MediaCard")
                card.setMinimumWidth(252)
                card.setMaximumWidth(292)
                card.setFixedHeight(318)
                card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                cl = QVBoxLayout(card)
                cl.setContentsMargins(14, 14, 14, 14)
                cl.setSpacing(9)

                preview = QFrame()
                preview.setObjectName("ResultPreviewFrame")
                preview.setFixedSize(222, 128)
                pv_l = QVBoxLayout(preview)
                pv_l.setContentsMargins(0, 0, 0, 0)
                pv_l.setAlignment(Qt.AlignmentFlag.AlignCenter)
                if image_path and os.path.isfile(image_path):
                    pix = QPixmap(image_path).scaled(
                        222,
                        128,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    img = QLabel()
                    img.setPixmap(pix)
                    pv_l.addWidget(img)
                else:
                    placeholder = QLabel((decision or "unknown").upper())
                    placeholder.setObjectName("ResultPlaceholder")
                    placeholder.setStyleSheet(f"color: {decision_color};")
                    pv_l.addWidget(placeholder)
                cl.addWidget(preview)

                name_lbl = QLabel(str(camera_name)[:30])
                name_lbl.setObjectName("ResultCameraName")
                cl.addWidget(name_lbl)

                meta_lbl = QLabel(f"{(decision or 'unknown').upper()} • {ts}")
                meta_lbl.setObjectName("ResultDecisionMeta")
                meta_lbl.setStyleSheet(f"color: {decision_color};")
                cl.addWidget(meta_lbl)

                inspection_cfg = load_inspection_profile().get("inspection") or {}
                if defects:
                    details = defects[:2]
                elif decision == "pass":
                    details = [inspection_cfg.get("pass_display_label") or "Pass"]
                elif decision == "fail":
                    details = [inspection_cfg.get("fail_display_label") or "Fail"]
                elif decision == "uncertain":
                    details = [inspection_cfg.get("review_display_label") or "Review required"]
                else:
                    details = ["No detail"]
                details_lbl = QLabel(" · ".join(str(x) for x in details))
                details_lbl.setObjectName("ResultMetaCompact")
                details_lbl.setWordWrap(True)
                cl.addWidget(details_lbl)

                dur = int(record.get("duration_ms") or 0)
                recipe_name = str(record.get("recipe_name") or "default_recipe")
                model_ver = str(record.get("model_version") or "unassigned")
                dur_lbl = QLabel(f"Recipe: {recipe_name} • {dur} ms")
                dur_lbl.setObjectName("ResultMetaCompact")
                dur_lbl.setWordWrap(True)
                cl.addWidget(dur_lbl)
                model_lbl = QLabel(f"Model: {model_ver}")
                model_lbl.setObjectName("ResultMetaCompact")
                model_lbl.setWordWrap(True)
                cl.addWidget(model_lbl)

                cl.addStretch()
                btn_row = QHBoxLayout()
                btn_row.setSpacing(8)
                open_btn = QPushButton("Open")
                open_btn.setObjectName("BtnSm")
                target_path = image_path if image_path and os.path.isfile(image_path) else record.get("record_path")
                open_btn.clicked.connect(lambda _=False, p=target_path: p and QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
                btn_row.addWidget(open_btn)
                delete_btn = QPushButton("Delete")
                delete_btn.setObjectName("BtnSecondary")
                delete_btn.clicked.connect(lambda _=False, iid=record.get("inspection_id"): self._delete_result_record(iid))
                btn_row.addWidget(delete_btn)
                cl.addLayout(btn_row)
                lay.addWidget(card)
            return

        final_obj = QLabel("No inspection results yet. Trigger the runtime from the Inspection page or run a hardware trigger.")
        final_obj.setObjectName("CaptionMuted")
        lay.addWidget(final_obj)
        return None

    def _pick_canonical_device_row(self, rows: list, prefixes: list) -> tuple:
        """Aynı ZT düğümü için tutulacak kayıt: özel ad, yönetilen IP aralığı, SSH anahtarı öncelikli."""

        def score(row):
            name, host, user, key_path = row
            s = 0
            n = (name or "").strip()
            if n and not n.upper().startswith("ZT-"):
                s += 100
            h = (host or "").strip()
            if ip_matches_zt_managed_prefixes(h, prefixes):
                s += 50
            if (key_path or "").strip():
                s += 25
            return s

        return max(rows, key=score)

    def _dedupe_devices_by_zt_node(self, leaf_peers) -> int:
        """Veritabanında aynı ZeroTier düğüm kimliğine denk kayıtları tek satırda birleştirir."""
        devices = self.db.get_devices()
        if len(devices) < 2:
            return 0
        groups = {}
        for row in devices:
            name, host, user, key_path = row
            pr = self._match_zt_peer_for_saved(name, host, leaf_peers)
            if not pr:
                continue
            nid = (pr.get("address_full") or pr.get("address") or "").strip().lower()
            if not nid or nid == "—":
                continue
            groups.setdefault(nid, []).append(row)
        prefixes = get_zerotier_managed_ipv4_prefixes()
        removed = 0
        for _nid, rows in groups.items():
            if len(rows) < 2:
                continue
            canonical = self._pick_canonical_device_row(rows, prefixes)
            name_c, host_c, user_c, kp_c = canonical
            merged_kp = (kp_c or "").strip()
            if not merged_kp:
                for r in rows:
                    mk = (r[3] or "").strip()
                    if mk:
                        merged_kp = mk
                        break
            best_host = (host_c or "").strip()
            for r in rows:
                h = (r[1] or "").strip()
                if not h:
                    continue
                if ip_matches_zt_managed_prefixes(h, prefixes) and not ip_matches_zt_managed_prefixes(
                    best_host, prefixes
                ):
                    best_host = h
            kp_final = merged_kp or (kp_c or "").strip() or ""
            if best_host != (host_c or "").strip() or (
                kp_final and kp_final != (kp_c or "").strip()
            ):
                self.db.update_device(host_c, name_c, best_host, user_c or "jetson", kp_final or None)
                host_c = best_host
            for r in rows:
                if r[1] == host_c:
                    continue
                h, u = r[1], r[2] or "jetson"
                self._ssh_disconnect_device(h, u)
                self.db.remove_device(r[1])
                removed += 1
        if removed:
            log.info("devices deduped by ZT node: removed %d duplicate row(s)", removed)
        return removed

    def _devices_page_managed_display_ip(self, host: str, seen_z: list, path_fallback: str, prefixes: list) -> str:
        """Tabloda gösterilecek adres: önce listnetworks aralığına uyan sanal IP, sonra host, sonra diğer."""
        ordered = _sort_zt_ips_display_order(list(seen_z or []), prefixes)
        for z in ordered:
            if ip_matches_zt_managed_prefixes(z, prefixes):
                return z
        h = (host or "").strip()
        if ip_matches_zt_managed_prefixes(h, prefixes):
            return h
        pf = (path_fallback or "").strip()
        if ip_matches_zt_managed_prefixes(pf, prefixes):
            return pf
        if ordered:
            return ordered[0]
        return h or pf or ""

    def _peer_ssh_relay_ip(self, pr: dict, display_ip: str, prefixes: list) -> str:
        """İlk SSH path’i: mümkünse sanal ağ dışı (relay/LAN) adres; yoksa gösterilen sanal IP."""
        ip = (pr.get("ip") or "").strip()
        if ip and not ip_matches_zt_managed_prefixes(ip, prefixes):
            return ip
        for p in pr.get("path_ips") or []:
            ps = str(p).strip()
            if ps and not ip_matches_zt_managed_prefixes(ps, prefixes):
                return ps
        return (display_ip or ip or "").strip()

    def _peer_visible_on_devices_page(self, pr: dict, prefixes: list) -> bool:
        """Ayarlar’daki ham path tekrarını önle: yalnızca bu VisionDock ağının sanal önekine düşen uçlar."""
        for z in pr.get("zt_from_controller") or []:
            if ip_matches_zt_managed_prefixes(str(z).strip(), prefixes):
                return True
        ip = (pr.get("ip") or "").strip()
        if ip_matches_zt_managed_prefixes(ip, prefixes):
            return True
        for p in pr.get("path_ips") or []:
            if ip_matches_zt_managed_prefixes(str(p).strip(), prefixes):
                return True
        ck = self._zt_peer_cache_key(
            (pr.get("address_full") or pr.get("address") or "").strip(),
            ip,
        )
        for c in self._zt_peer_cached_ips.get(ck) or []:
            if ip_matches_zt_managed_prefixes(str(c).strip(), prefixes):
                return True
        return False

    def _ssh_run_remote_stream_command(self, ssh_host, ssh_user, cmd: str) -> tuple[bool, str]:
        """Uzakta yayın komutunu çalıştırır; çıktı kısa özet döner."""
        key = ssh_session_key(ssh_host, ssh_user)
        client = self._ssh_sessions.get(key)
        if not client:
            return False, "SSH oturumu yok"
        try:
            wrapped = f"nohup bash -lc {shlex.quote(cmd)} </dev/null >/tmp/visiondock-stream.log 2>&1 &"
            _, err, _ = ssh_exec_text(client, wrapped, timeout=18)
            time.sleep(1.2)
            tail, _, _ = ssh_exec_text(client, "tail -n 20 /tmp/visiondock-stream.log 2>/dev/null || true", timeout=8)
            msg = ((err or "").strip() + "\n" + (tail or "").strip()).strip()
            return True, (msg[:900] if msg else "Command sent (log: /tmp/visiondock-stream.log)")
        except Exception as e:
            return False, str(e)

    def _ssh_verify_tcp_listen(self, client, port: int, attempts: int = 7, gap: float = 0.5) -> bool:
        """Uzakta 127.0.0.1:port için TCP bağlantısı (ffmpeg dinliyor mu)."""
        probe = (
            f"import socket,sys;s=socket.socket();s.settimeout(2.5);"
            f"r=s.connect_ex(('127.0.0.1',{port}));s.close();sys.exit(0 if r==0 else 1)"
        )
        for _ in range(attempts):
            try:
                _, _, code = ssh_exec_text(
                    client,
                    f"python3 -c {shlex.quote(probe)} 2>/dev/null || "
                    f"(command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | grep -qE ':{port}\\s')",
                    timeout=12,
                )
                if code == 0:
                    return True
            except Exception:
                pass
            time.sleep(gap)
        return False

    def _show_yayina_ekle_camera_dialog(self, ssh_host, ssh_user, dev_name, stream_ip_hint: str):
        """SSH üzerinden kamera listesi; yayına ekleme ve isteğe bağlı uzak akış komutu."""
        if not self._is_ssh_device_connected(ssh_host, ssh_user):
            QMessageBox.information(
                self,
                "Add Stream",
                "Open an SSH session for this device first ('Connect…' or 'Save & Connect' from the ZeroTier row).",
            )
            return
        client = self._ssh_sessions.get(ssh_session_key(ssh_host, ssh_user))
        zt_now = ssh_get_zerotier_ips(client) if client else []
        sip = zt_now[0] if zt_now else (stream_ip_hint or "").strip()
        lines = self._ssh_list_camera_lines(ssh_host, ssh_user)
        if not lines or (len(lines) == 1 and lines[0].lower().startswith("error")):
            QMessageBox.warning(
                self,
                "Camera",
                "Unable to read /dev/video or v4l2 output on the remote device.\n\n"
                "• Confirm camera connection and that no other process is holding the device.\n"
                "• On Jetson: ls /dev/video* and v4l2-ctl --list-devices\n"
                "• RTSP setups usually require mediamtx / jetson-utils or ffmpeg.",
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Camera — Add Stream")
        dlg.setFixedWidth(540)
        dlg_l = QVBoxLayout(dlg)
        dlg_l.setContentsMargins(24, 22, 24, 22)
        dlg_l.setSpacing(14)
        info = QLabel(
            f"<b>{dev_name}</b> — runtime IP for stream: <b>{sip or '—'}</b><br>"
            "Choose camera input; VisionDock can run the remote stream command if needed."
        )
        info.setWordWrap(True)
        dlg_l.addWidget(info)
        cam_combo = QComboBox()
        cam_combo.setFixedHeight(36)
        for ln in lines:
            cam_combo.addItem(ln[:80] + ("…" if len(ln) > 80 else ""), ln)
        dlg_l.addWidget(cam_combo)
        if cam_combo.count() == 0:
            dlg_l.addWidget(QLabel("No camera lines detected."))
            dlg.reject()
            return

        stream_mode_combo = QComboBox()
        stream_mode_combo.setFixedHeight(36)
        stream_mode_combo.addItem(
            "Jetson CSI — NVArgus + GStreamer -> MJPEG (TCP) :5000 (recommended)",
            "csi_gst_http",
        )
        stream_mode_combo.addItem(
            "Manual / advanced URL (only if a stream server already exists)",
            "manual_url",
        )
        stream_mode_combo.setCurrentIndex(0)
        stream_mode_combo.setToolTip(
            "Recommended production path is Jetson CSI + NVIDIA Argus + GStreamer stream. "
            "Manual URL is for preconfigured advanced stream servers only."
        )
        dlg_l.addWidget(stream_mode_combo)
        warn = QLabel(
            "<b>Connection refused</b>: nothing is listening on port 5000 (remote command failed or wrong profile).<br>"
            "For production lines, use the <b>NVArgus / GStreamer</b> profile above.<br>"
            "Camera busy check: <code>fuser -v /dev/video0</code> · Log: <code>tail /tmp/visiondock-stream.log</code>"
        )
        warn.setObjectName("CaptionMuted")
        warn.setTextFormat(Qt.TextFormat.RichText)
        warn.setWordWrap(True)
        warn.setOpenExternalLinks(False)
        dlg_l.addWidget(warn)

        def cam_idx_from_line(line: str) -> str:
            m = re.search(r"/dev/video(\d+)", line or "")
            return m.group(1) if m else "0"

        def current_line():
            return cam_combo.currentData() or cam_combo.currentText()

        url_lbl = QLabel("Stream URL")
        url_lbl.setObjectName("FormLabel")
        dlg_l.addWidget(url_lbl)
        url_edit = QLineEdit(f"http://{sip}:5000/" if sip else "")
        url_edit.setPlaceholderText("http://192.168.192.x:5000/")
        dlg_l.addWidget(url_edit)

        def stream_mode_is_manual() -> bool:
            return stream_mode_combo.currentData() == "manual_url"

        def sync_url_from_stream_mode(_=None):
            if stream_mode_is_manual():
                url_edit.setReadOnly(False)
                url_edit.setToolTip("Pre-existing RTSP or HTTP stream URL on Jetson (verify with VLC / ffplay).")
                if not (url_edit.text() or "").strip() and sip:
                    url_edit.setText(f"rtsp://{sip}:8554/video{cam_idx_from_line(current_line())}")
            else:
                url_edit.setText(f"http://{sip}:5000/" if sip else "")
                url_edit.setReadOnly(True)
                url_edit.setToolTip(
                    "Jetson sends multipart MJPEG via GStreamer (tcpserversink :5000). "
                    "Even if URL is http://..., VisionDock also tries TCP fallback on the same port."
                )

        name_edit = QLineEdit(f"{dev_name} / {cam_idx_from_line(current_line())}")
        name_edit.setPlaceholderText("Stream name")
        dlg_l.addWidget(name_edit)

        def sync_name_from_cam(_=None):
            name_edit.setText(f"{dev_name} / {cam_idx_from_line(current_line())}")

        stream_mode_combo.currentIndexChanged.connect(sync_url_from_stream_mode)
        cam_combo.currentIndexChanged.connect(sync_name_from_cam)
        sync_url_from_stream_mode()

        mirror_name_edit = QLineEdit(f"{(dev_name or 'dev').lower().replace(' ', '_')}_mirror")
        mirror_name_edit.setPlaceholderText("Mirror name (optional)")
        dlg_l.addWidget(mirror_name_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_b = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_b.setText("Add Stream")
        ok_b.setObjectName("BtnPrimary")
        ok_b.setMinimumHeight(36)
        can_b = btns.button(QDialogButtonBox.StandardButton.Cancel)
        can_b.setText("Cancel")
        can_b.setObjectName("BtnSecondary")
        can_b.setMinimumHeight(36)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        dlg_l.addWidget(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        line = current_line()
        cam_idx = cam_idx_from_line(line)
        mode = stream_mode_combo.currentData()
        if mode == "csi_gst_http":
            _http_port = 5000
            try:
                sens_id = min(max(int(cam_idx or "0"), 0), 7)
            except ValueError:
                sens_id = 0
            # caps içindeki () bash -lc'de özel anlam taşır — tek tırnak zorunlu.
            # souphttpserversink çoğu Jetson imajında yok; tcpserversink + multipart ile
            # open_cv_capture_remote http://… için TCP fallback uyumludur.
            inner = (
                f"(command -v fuser >/dev/null 2>&1 && fuser -k {_http_port}/tcp 2>/dev/null); sleep 0.45; "
                "if ! command -v gst-launch-1.0 >/dev/null 2>&1; then echo NO_GST_LAUNCH; exit 1; fi; "
                f"gst-launch-1.0 -e nvarguscamerasrc sensor-id={sens_id} ! "
                "'video/x-raw(memory:NVMM),width=1280,height=720,format=NV12,framerate=60/1' ! "
                "nvjpegenc quality=75 idct-method=1 ! "
                "queue max-size-buffers=2 max-size-time=0 leaky=downstream ! "
                "multipartmux ! "
                f"tcpserversink host=0.0.0.0 port={_http_port} sync=false blocksize=65536"
            )
            remote_label = "Remote GStreamer"
            ok_run, detail = self._ssh_run_remote_stream_command(ssh_host, ssh_user, inner)
            sess = self._ssh_sessions.get(ssh_session_key(ssh_host, ssh_user))
            listening = bool(sess and self._ssh_verify_tcp_listen(sess, _http_port)) if ok_run else False
            if ok_run:
                log.info("remote stream cmd result: %s", detail[:500])
                low = (detail or "").lower()
                suspicious = any(
                    w in low
                    for w in (
                        "error",
                        "cannot",
                        "failed",
                        "busy",
                        "no such device",
                        "invalid argument",
                        "permission denied",
                        "no such element",
                        "not found",
                        "syntax error",
                        "unexpected token",
                    )
                )
                jetson_checks = (
                    "Jetson checks (NVArgus / GStreamer):\n"
                    "• tail -40 /tmp/visiondock-stream.log\n"
                    "• ss -lntp | grep :5000 (LISTEN expected)\n"
                    "• gst-inspect-1.0 tcpserversink multipartmux nvjpegenc nvarguscamerasrc\n"
                    "• If log shows 'syntax error' or 'unexpected token': caps quoting issue (ensure app is current)\n"
                    "• For production, use CSI / NVArgus profile only"
                )
                if listening:
                    self.show_toast(
                        "Jetson is listening on :5000 (MJPEG); preview should start in a few seconds."
                    )
                    if suspicious:
                        QMessageBox.warning(
                            self,
                            remote_label,
                            "Port is open but logs contain error traces. If stream fails:\n" + jetson_checks,
                        )
                else:
                    QMessageBox.warning(
                        self,
                        remote_label,
                        "Nothing is listening on Jetson :5000; this is why macOS may show 'Connection refused'.\n\n"
                        + jetson_checks,
                    )
                    return
            else:
                QMessageBox.warning(self, "Remote command", detail[:500] if detail else "SSH command failed.")
                return
        url = url_edit.text().strip()
        cam_name = name_edit.text().strip() or f"{dev_name} cam"
        if not url:
            QMessageBox.warning(self, "Stream", "Stream URL is empty.")
            return
        self.add_cam_logic(cam_name, url, "Stream|STANDARD|AUTO")
        mirror_nm = mirror_name_edit.text().strip()
        if mirror_nm:
            self.add_cam_logic(mirror_nm, url, "Stream|STANDARD|AUTO")
            self.show_toast(f"Stream added: {cam_name}, {mirror_nm}")
        else:
            self.show_toast(f"Stream added: {cam_name}")
        self.switch(1)

    def _peer_row_yayina_ekle(self, path_ip: str, user_w: QLineEdit, node_addr: str, stream_guess: str):
        u = user_w.text().strip() or "jetson"
        if not path_ip:
            self.show_toast("Path IP is missing.")
            return
        if not self._is_ssh_device_connected(path_ip, u):
            creds = self._ssh_password_dialog(path_ip, u, node_addr)
            if creds is None:
                return
            fu, pw = creds
            client, err = ssh_connect_with_password(path_ip.strip(), fu, pw)
            if err:
                QMessageBox.warning(self, "SSH", err)
                return
            self._ssh_disconnect_device(path_ip, fu)
            self._ssh_sessions[ssh_session_key(path_ip, fu)] = client
            u = fu
            self.refresh_devices_page(force=True)
        zt_list = self._zt_peer_cached_ips.get(self._zt_peer_cache_key(node_addr, path_ip)) or []
        sip = zt_list[0] if zt_list else stream_guess
        label = f"ZT-{node_addr[:8]}" if node_addr and node_addr != "—" else path_ip
        self._show_yayina_ekle_camera_dialog(path_ip, u, label, sip)

    def refresh_devices_page(self, force: bool = False):
        # Prevent multiple parallel pokes
        if getattr(self, "_hw_worker_active", False): return
        lay = getattr(self, "dev_hub_layout", None)
        # Reuse recent scan data to avoid expensive re-scan on every tab switch.
        if (not force) and lay is not None and lay.count() > 0:
            try:
                age = time.time() - float(getattr(self, "_devices_last_scan_ts", 0.0) or 0.0)
            except Exception:
                age = 999999.0
            if age < float(getattr(self, "_devices_scan_ttl_sec", 45.0) or 45.0):
                return
        self._hw_worker_active = True
        
        # Show immediate placeholder if empty
        if lay and lay.count() == 0:
            tmp = QLabel("Scanning local and remote inspection devices...")
            tmp.setObjectName("CaptionMuted")
            lay.addWidget(tmp)

        self._hw_worker = HardwareWorker()
        self._hw_worker.finished.connect(self._on_hardware_ready)
        self._hw_worker.start()

    def _on_hardware_ready(self, data):
        self._hw_worker_active = False
        if not data: return
        self._devices_last_scan_ts = time.time()
        lay = getattr(self, "dev_hub_layout", None)
        if not lay: return
        
        # Clear
        while lay.count():
            it = lay.takeAt(0); w = it.widget()
            if w: w.deleteLater()

        def make_premium_card(title, subtitle, status_txt, is_online=True, cameras=None, is_remote=False, remote_ip=None):
            cameras = cameras or []
            c = QFrame(); c.setObjectName("InfoCard")
            c.setMinimumWidth(360)
            c.setMaximumWidth(520)
            c.setMinimumHeight(220)
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            cl = QVBoxLayout(c); cl.setContentsMargins(22, 22, 22, 22); cl.setSpacing(14)
            
            hh = QHBoxLayout(); th = QLabel(title); th.setObjectName("SectionHeading")
            hh.addWidget(th); hh.addStretch()
            st_dot = QLabel(f"● {status_txt}"); clr = "#30D158" if is_online else "#FF453A"
            st_dot.setObjectName("DeviceCardStatus")
            st_dot.setStyleSheet(f"color: {clr};")
            hh.addWidget(st_dot); cl.addLayout(hh)
            
            sub = QLabel(subtitle); sub.setObjectName("CaptionMutedSm"); cl.addWidget(sub)
            cl.addWidget(hairline())
            
            if is_remote:
                row = QFrame(); row.setObjectName("SettingsInset"); row.setFixedHeight(50)
                rl = QHBoxLayout(row); rl.setContentsMargins(15, 0, 15, 0)
                tmp = QLabel("Remote Runtime")
                tmp.setObjectName("DeviceCardAccent")
                rl.addWidget(tmp)
                rl.addStretch()
                ab = QPushButton("Use as runtime"); ab.setObjectName("BtnPrimary")
                ab.setFixedWidth(126)
                if remote_ip:
                    ab.clicked.connect(lambda _=False, ip=remote_ip: self._apply_runtime_host_selection(ip))
                ab.setEnabled(bool(remote_ip) and is_online)
                rl.addWidget(ab); cl.addWidget(row)
            
            if cameras:
                for cam in cameras:
                    crow = QFrame(); crow.setObjectName("SettingsInset"); crow.setFixedHeight(44)
                    crl = QHBoxLayout(crow); crl.setContentsMargins(14, 0, 14, 0); crl.setSpacing(12)
                    ico_container = QFrame(); ico_container.setFixedSize(24, 24)
                    ico_container.setStyleSheet("background: #111; border-radius: 6px; border: 1px solid #222;")
                    icl = QVBoxLayout(ico_container); icl.setContentsMargins(0,0,0,0); icl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    f_obj = QLabel("C"); f_obj.setStyleSheet("font-size: 10px; font-weight: 900; color: #0A84FF;"); icl.addWidget(f_obj)
                    crl.addWidget(ico_container)
                    n_lbl = QLabel(f"{cam[:18]}")
                    n_lbl.setObjectName("DeviceCamName")
                    crl.addWidget(n_lbl); crl.addStretch()
                    ub = QPushButton("Open inspection")
                    ub.setObjectName("BtnSecondary")
                    ub.setFixedWidth(126)
                    ub.setCursor(Qt.CursorShape.PointingHandCursor)
                    ub.clicked.connect(lambda _=False: self.switch(1)); crl.addWidget(ub)
                    cl.addWidget(crow)
            elif not is_remote:
                tmp = QLabel("No local camera sources detected."); tmp.setObjectName("CaptionMuted"); cl.addWidget(tmp)
            cl.addStretch(); return c

        # A. Local System
        local_cams = [f"{n}" for n, i in data.get("cams", [])]
        lay.addWidget(make_premium_card("Local Inspection Host", "This computer / primary runtime host", "Online", True, local_cams))

        # B. Remote ZeroTier Nodes
        peers = data.get("peers", [])
        my_nid = data.get("my_nid", "")
        for p in peers:
            if p.get("role") != "LEAF": continue
            nid = (p.get("address") or "").strip()
            if nid == my_nid: continue
            ips = p.get("zt_from_controller", [])
            display_ip = ips[0] if ips else p.get("ip", "—")
            is_online = (p.get("status") == "ONLINE")
            lay.addWidget(
                make_premium_card(
                    f"Runtime {nid[:6].upper()}",
                    f"ZeroTier IP: {display_ip}",
                    "Active" if is_online else "Offline",
                    is_online,
                    [],
                    is_remote=True,
                    remote_ip=display_ip,
                )
            )

        return None

    def _ssh_password_dialog(self, host: str, user: str, node_id: str = None):
        username = (user or "jetson").strip() or "jetson"
        dlg = QDialog(self)
        dlg.setWindowTitle("SSH Connection")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        pal = ThemeOps.palette(self.is_dark)
        dlg.setStyleSheet(
            f"QDialog {{ background-color: {pal['card']}; color: {pal['txt']}; }}"
            f"QLabel {{ color: {pal['txt']}; border: none; background: transparent; }}"
        )
        form = QFormLayout(dlg)
        form.setSpacing(14)
        form.setContentsMargins(28, 24, 28, 24)

        host_label = QLabel((host or "").strip())
        host_label.setObjectName("MonoIp")
        host_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Host", host_label)
        if node_id and node_id not in ("—", "", None):
            node_label = QLabel(str(node_id))
            node_label.setObjectName("MonoMuted")
            node_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow("Node ID", node_label)

        user_edit = QLineEdit(username)
        user_edit.setPlaceholderText("jetson")
        form.addRow("User", user_edit)

        password_edit = QLineEdit()
        password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        password_edit.setPlaceholderText("SSH password")
        password_edit.returnPressed.connect(dlg.accept)
        form.addRow("Password", password_edit)

        hint = QLabel(
            "The password is used only for this session and is never stored.\n"
            "Unknown host keys are accepted automatically for trusted private networks such as ZeroTier."
        )
        hint.setObjectName("CaptionMuted")
        hint.setWordWrap(True)
        form.addRow(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return (user_edit.text().strip() or "jetson", password_edit.text())

    def _fetch_peer_zt_virtual_ips(self, path_ip: str, user: str, node_id: str, *, goto_devices: bool = False):
        """Path IP üzerinden SSH ile uzakta zerotier-cli çalıştırıp sanal IP'leri önbelleğe yazar."""
        if not (path_ip or "").strip():
            self.show_toast("IP not found — device has no active path.")
            return
        path_ip = path_ip.strip()
        u = (user or "").strip() or "jetson"
        cache_key = self._zt_peer_cache_key(node_id, path_ip)

        def _after_fetch(ips: list, empty_msg: str):
            self.refresh_devices_page(force=True)
            if goto_devices:
                self.switch(3)
            if ips:
                self.show_toast(f"ZT virtual IP: {', '.join(ips)}")
            else:
                self.show_toast(empty_msg)

        if self._is_ssh_device_connected(path_ip, u):
            client = self._ssh_sessions.get(ssh_session_key(path_ip, u))
            if client:
                ips = ssh_get_zerotier_ips(client)
                self._zt_peer_cached_ips[cache_key] = ips
                _after_fetch(ips, "No ZT address found on remote device (zerotier-cli / network issue?).")
            return
        creds = self._ssh_password_dialog(path_ip, u, node_id)
        if creds is None:
            return
        final_user, password = creds
        self.show_toast(f"Fetching ZT IPs: {final_user}@{path_ip}...")
        client, err = ssh_connect_with_password(path_ip, final_user, password)
        if err:
            QMessageBox.warning(self, "SSH connection failed", err)
            self.notify_error(f"SSH error: {path_ip} - {err[:80]}")
            return
        try:
            ips = ssh_get_zerotier_ips(client)
        finally:
            try:
                client.close()
            except Exception:
                pass
        self._zt_peer_cached_ips[cache_key] = ips
        _after_fetch(ips, "Failed to fetch ZT address — is ZeroTier installed on the device?")

    def _disconnect_all_ssh_sessions(self):
        for key in list(self._ssh_sessions.keys()):
            self._ssh_disconnect_device(key[0], key[1])

    def _ssh_list_camera_lines(self, host, user):
        """Requires an active session. Returns list of display lines."""
        key = ssh_session_key(host, user)
        client = self._ssh_sessions.get(key)
        if client is None:
            return []
        try:
            out, err, code = ssh_exec_text(
                client,
                "sh -c 'ls -1 /dev/video* 2>/dev/null | sort -u'",
                timeout=20,
            )
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            if lines:
                return lines
            out2, err2, _ = ssh_exec_text(client, "v4l2-ctl --list-devices 2>/dev/null || true", timeout=20)
            if (out2 or "").strip():
                return [ln for ln in out2.strip().splitlines() if ln.strip()]
            if (err or "").strip():
                return [f"(stderr) {err.strip()[:200]}"]
            return ["No /dev/video* nodes found. Check camera, drivers, or permissions on the device."]
        except Exception as e:
            return [f"Error: {e}"]

    def _ssh_show_connect_dialog(self, host, user, node_id=None):
        """SSH bağlantı dialogu — IP, Node ID, kullanıcı ve şifreyi göster; otomatik host key onayı."""
        creds = self._ssh_password_dialog(host, user, node_id)
        if creds is None:
            return
        final_user, password = creds
        self.show_toast(f"Connecting: {final_user}@{host.strip()}...")
        client, err = ssh_connect_with_password(host.strip(), final_user, password)
        if err:
            QMessageBox.warning(self, "SSH connection failed", err)
            self.notify_error(f"SSH error: {host} - {err[:80]}")
            return
        self._ssh_disconnect_device(host, final_user)
        self._ssh_sessions[ssh_session_key(host, final_user)] = client
        self.active_remote_host = host
        if hasattr(self, "node_ip"):
            self.node_ip.setText(host)
        DockerManager.set_host(host)
        self.show_toast(f"Connected: {final_user}@{host}")
        self.notify_success(f"SSH connected: {host}")
        self.refresh_devices_page(force=True)

    def _show_onboarding_wizard(self, force: bool = False):
        prefs = load_app_prefs()
        profile = load_inspection_profile()
        runtime_cfg = profile.get("runtime") or {}
        needs_setup = bool(force) or (not prefs.get("product_onboarding_done"))
        if not needs_setup:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Production Setup")
        dlg.resize(560, 420)
        dlg.setSizeGripEnabled(True)
        dlg.setModal(True)
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(16)

        title = QLabel("Prepare The Runtime Workspace")
        title.setObjectName("SectionHeading")
        outer.addWidget(title)

        intro = QLabel(
            "Configure the minimum production fields so the operator panel points to the correct runtime, "
            "camera backend, and inspection name."
        )
        intro.setObjectName("CaptionMuted")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(12)
        form.setHorizontalSpacing(16)

        name_edit = QLineEdit(profile.get("camera_name") or "")
        name_edit.setPlaceholderText("Primary Runtime Node")
        host_edit = QLineEdit(normalize_runtime_host(runtime_cfg.get("host") or "") or "127.0.0.1")
        host_edit.setPlaceholderText("127.0.0.1 or ZeroTier/LAN runtime endpoint")

        backend_combo = QComboBox()
        backend_combo.setFixedHeight(38)
        backend_options = [
            ("Jetson CSI (Argus)", "jetson_csi_argus"),
            ("OpenCV device", "opencv_device"),
            ("Image file (test)", "image_file"),
            ("Video file (test)", "video_file"),
            ("Mock frame", "mock_frame"),
        ]
        for label, value in backend_options:
            backend_combo.addItem(label, value)
        target_backend = ((profile.get("camera") or {}).get("backend") or "jetson_csi_argus").strip()
        idx = 0
        for i in range(backend_combo.count()):
            if backend_combo.itemData(i) == target_backend:
                idx = i
                break
        backend_combo.setCurrentIndex(idx)

        gpio_box = QCheckBox("Enable GPIO / tower lights")
        gpio_box.setChecked(bool((profile.get("gpio") or {}).get("enabled")))

        form.addRow(form_label("Runtime name"), name_edit)
        form.addRow(form_label("Runtime host"), host_edit)
        form.addRow(form_label("Capture backend"), backend_combo)
        form.addRow(form_label("Hardware outputs"), gpio_box)
        outer.addLayout(form)

        sensor_note = QLabel(
            "CSI sensor family is not an operator choice. The Jetson runtime should detect the installed module; "
            "focus, zoom, and iris depend on the exact camera SKU and lens package, not only on IMX219 / IMX477 / IMX519."
        )
        sensor_note.setObjectName("CaptionMuted")
        sensor_note.setWordWrap(True)
        outer.addWidget(sensor_note)

        checklist = QLabel(
            "Recommended line setup:\n"
            "1. At least one runtime endpoint is reachable.\n"
            "2. CSI modules such as IMX219 / IMX477 / IMX519 use Jetson CSI (Argus).\n"
            "3. Model package is exported from AI Models and activated on the runtime endpoint.\n"
            "4. Operators focus on Inspection, Results, and basic Settings."
        )
        checklist.setObjectName("CaptionMuted")
        checklist.setWordWrap(True)
        outer.addWidget(checklist)

        btns = QDialogButtonBox()
        apply_btn = btns.addButton("Apply Setup", QDialogButtonBox.ButtonRole.AcceptRole)
        skip_btn = btns.addButton("Skip", QDialogButtonBox.ButtonRole.RejectRole)
        settings_btn = btns.addButton("Open Settings", QDialogButtonBox.ButtonRole.ActionRole)
        apply_btn.setObjectName("BtnPrimary")
        settings_btn.setObjectName("BtnSecondary")
        skip_btn.setObjectName("BtnSecondary")
        dlg._open_settings_only = False

        def _open_settings_from_wizard():
            dlg._open_settings_only = True
            dlg.accept()

        settings_btn.clicked.connect(_open_settings_from_wizard)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        outer.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            prefs["product_onboarding_done"] = True
            save_app_prefs(prefs)
            return

        if getattr(dlg, "_open_settings_only", False):
            prefs["product_onboarding_done"] = True
            save_app_prefs(prefs)
            self.switch(5)
            return

        if hasattr(self, "_inspection_camera_name_input"):
            self._inspection_camera_name_input.setText((name_edit.text() or "").strip() or "VisionDock Runtime Node")
        if hasattr(self, "_inspection_runtime_host_input"):
            self._inspection_runtime_host_input.setText(normalize_runtime_host(host_edit.text()) or "127.0.0.1")
        if hasattr(self, "_inspection_camera_backend_combo"):
            for i in range(self._inspection_camera_backend_combo.count()):
                if self._inspection_camera_backend_combo.itemData(i) == backend_combo.currentData():
                    self._inspection_camera_backend_combo.setCurrentIndex(i)
                    break
        if hasattr(self, "_inspection_gpio_enable_switch"):
            self._inspection_gpio_enable_switch.setChecked(gpio_box.isChecked())
        self._save_inspection_profile_from_ui()
        prefs["product_onboarding_done"] = True
        save_app_prefs(prefs)
        self.notify_success("Production runtime setup updated.")
        self.switch(1)

    def page_settings(self):
        return build_settings_page(self, _settings_page_helpers())

    def _update_aelock_sliders_visibility(self):
        on = getattr(self, "camera_aelock_switch", None) and self.camera_aelock_switch.isChecked()
        blk = getattr(self, "camera_exp_block", None)
        if blk is not None:
            blk.setVisible(on)

    def _selected_camera_sensor_model(self):
        combo = getattr(self, "camera_sensor_model_combo", None)
        if combo is not None:
            return str(combo.currentData() or "GENERIC_CSI").strip().upper() or "GENERIC_CSI"
        return str(get_camera_defaults().get("sensor_model") or "GENERIC_CSI").strip().upper() or "GENERIC_CSI"

    def _selected_camera_focuser_type(self):
        combo = getattr(self, "camera_focuser_combo", None)
        sensor_model = self._selected_camera_sensor_model()
        if combo is not None:
            return str(combo.currentData() or csi_sensor_default_focuser(sensor_model)).strip().lower() or "none"
        defaults = get_camera_defaults()
        return str(defaults.get("focuser_type") or csi_sensor_default_focuser(sensor_model)).strip().lower() or "none"

    def _on_camera_sensor_model_changed(self):
        sensor_model = self._selected_camera_sensor_model()
        set_camera_defaults(sensor_model=sensor_model)
        self._refresh_camera_sensor_profile_hint()

    def _refresh_camera_sensor_profile_hint(self):
        sensor_model = self._selected_camera_sensor_model()
        focuser_type = self._selected_camera_focuser_type()
        rec = csi_sensor_recommended_capture(sensor_model)
        hint = getattr(self, "_camera_sensor_profile_hint", None)
        if hint is not None:
            hint.setText(
                f"{csi_sensor_label(sensor_model)}: {csi_sensor_note(sensor_model)} "
                f"Recommended start point: {int(rec['width'])}x{int(rec['height'])} @ {int(rec['fps'])} fps. "
                f"Focus actuator: {focuser_label(focuser_type)}. {focuser_note(focuser_type)}"
            )
        commissioning_hint = getattr(self, "_camera_commissioning_hint", None)
        if commissioning_hint is not None:
            if getattr(self, "_production_mode", False):
                commissioning_hint.setText(
                    "Production mode: sensor family and focus actuator are commissioning-only. Daily operators should not change them."
                )
            else:
                commissioning_hint.setText(
                    "Use this only during engineering bring-up. Sensor family helps with recommended defaults and validation, "
                    "but focus, zoom, and iris remain dependent on the exact camera module and lens."
                )
        self._update_focus_controls_visibility()

    def _apply_camera_sensor_recommendation(self):
        sensor_model = self._selected_camera_sensor_model()
        rec = csi_sensor_recommended_capture(sensor_model)
        set_camera_defaults(
            sensor_model=sensor_model,
            focuser_type=self._selected_camera_focuser_type(),
            resolution_index=csi_sensor_default_resolution_index(sensor_model),
        )
        if getattr(self, "camera_res_combo", None):
            idx = min(csi_sensor_default_resolution_index(sensor_model), self.camera_res_combo.count() - 1)
            self.camera_res_combo.setCurrentIndex(max(0, idx))
        self.notify_success(
            f"Applied recommended CSI preset for {csi_sensor_label(sensor_model)}: "
            f"{int(rec['width'])}x{int(rec['height'])} @ {int(rec['fps'])} fps."
        )
        self._refresh_camera_sensor_profile_hint()

    def _update_focus_controls_visibility(self):
        """Update slider label and button text for Fixed / Manual / Auto focus modes."""
        mode = (get_camera_defaults().get("focus_mode") or "fixed")
        if getattr(self, "camera_focus_combo", None): mode = self.camera_focus_combo.currentData() or mode
        focuser_type = self._selected_camera_focuser_type()
        focus_enabled = focuser_available(focuser_type)
        lbl = getattr(self, "camera_focus_slider_label", None)
        btn = getattr(self, "camera_focus_apply_btn", None)
        combo = getattr(self, "camera_focus_combo", None)
        slider = getattr(self, "camera_focus_slider", None)
        i2c_spin = getattr(self, "camera_i2c_spin", None)
        detail = getattr(self, "_camera_focus_long_label", None)
        if combo is not None:
            combo.setEnabled(focus_enabled)
        if slider is not None:
            slider.setEnabled(focus_enabled)
        if i2c_spin is not None:
            i2c_spin.setEnabled(focus_enabled)
        if lbl:
            if not focus_enabled:
                lbl.setText("Fixed lens configuration")
            elif mode == "fixed":
                lbl.setText("Fixed position (0=infinity):")
            elif mode == "manual":
                lbl.setText("Focus position (manual):")
            else:
                lbl.setText("Focus position:")
        if btn:
            btn.setEnabled(focus_enabled)
            if not focus_enabled:
                btn.setText("No focus motor")
            elif mode == "fixed":
                btn.setText("Set fixed position")
            elif mode == "manual":
                btn.setText("Apply focus")
            else:
                btn.setText("Run autofocus")
        if detail:
            if not focus_enabled:
                detail.setText(
                    "This CSI setup is configured as fixed lens. Focus scripts stay disabled until you choose a motorized focuser."
                )
            else:
                detail.setText(
                    "Fixed: one position at stream start. Manual: slider + button. Auto: runs the selected I2C focuser script. "
                    "Stop live CSI preview when possible."
                )

    def _selected_inspection_sensor_model(self):
        combo = getattr(self, "_inspection_camera_model_combo", None)
        if combo is not None:
            return str(combo.currentData() or "GENERIC_CSI").strip().upper() or "GENERIC_CSI"
        profile = load_inspection_profile()
        camera_cfg = profile.get("camera") or {}
        return str(camera_cfg.get("sensor_model") or "GENERIC_CSI").strip().upper() or "GENERIC_CSI"

    def _selected_inspection_focuser_type(self):
        combo = getattr(self, "_inspection_camera_focuser_combo", None)
        sensor_model = self._selected_inspection_sensor_model()
        if combo is not None:
            return str(combo.currentData() or csi_sensor_default_focuser(sensor_model)).strip().lower() or "none"
        profile = load_inspection_profile()
        camera_cfg = profile.get("camera") or {}
        return str(camera_cfg.get("focuser_type") or csi_sensor_default_focuser(sensor_model)).strip().lower() or "none"

    def _on_inspection_camera_sensor_model_changed(self):
        if getattr(self, "_inspection_ui_loading", False):
            return
        self._save_inspection_profile_from_ui()
        self._refresh_inspection_camera_profile_hint()

    def _refresh_inspection_camera_profile_hint(self):
        hint = getattr(self, "_inspection_camera_profile_hint", None)
        if hint is None:
            return
        sensor_model = self._selected_inspection_sensor_model()
        focuser_type = self._selected_inspection_focuser_type()
        rec = csi_sensor_recommended_capture(sensor_model)
        hint.setText(
            f"{csi_sensor_label(sensor_model)}: {csi_sensor_note(sensor_model)} "
            f"Recommended starting capture: {int(rec['width'])}x{int(rec['height'])} @ {int(rec['fps'])} fps. "
            f"Focus actuator: {focuser_label(focuser_type)}. {focuser_note(focuser_type)}"
        )
        self._refresh_inspection_runtime_sensor_hint()

    def _refresh_inspection_runtime_sensor_hint(self):
        lbl = getattr(self, "_inspection_runtime_sensor_hint", None)
        if lbl is None:
            return
        current = getattr(self, "_inspection_runtime_state", {}) or {}
        configured_sensor = self._selected_inspection_sensor_model()
        detected_sensor = str(current.get("detected_sensor_model") or "").strip().upper()
        match = current.get("camera_sensor_match")
        if detected_sensor:
            if match is False:
                lbl.setText(
                    f"Runtime detected {detected_sensor}, but commissioning metadata is {configured_sensor}. "
                    "Fix this mismatch before production."
                )
            elif match is True:
                lbl.setText(
                    f"Runtime detected {detected_sensor}. Commissioning metadata matches the installed sensor family."
                )
            else:
                lbl.setText(
                    f"Runtime detected {detected_sensor}. Sensor family is verified by the Jetson runtime."
                )
            return
        if getattr(self, "_production_mode", False):
            lbl.setText(
                "Production mode: leave sensor family and focus actuator locked. Runtime detection will populate when the Jetson runtime is reachable."
            )
        else:
            lbl.setText(
                "Engineering mode: set sensor family only for commissioning. Zoom and aperture are lens-side properties and are not controlled from this product."
            )

    def _refresh_sock_detection_profile_hint(self):
        lbl = getattr(self, "_inspection_sock_profile_hint", None)
        if lbl is None:
            return
        recipe = (getattr(self, "_inspection_recipe_name_input", None) and self._inspection_recipe_name_input.text().strip()) or "default_recipe"
        product = (getattr(self, "_inspection_product_label_input", None) and self._inspection_product_label_input.text().strip()) or "Inspection target"
        defect_catalog = []
        if getattr(self, "_inspection_defect_catalog_input", None):
            defect_catalog = [part.strip() for part in self._inspection_defect_catalog_input.text().split(",") if part.strip()]
        pass_thr = float(getattr(self, "_inspection_pass_threshold_spin", None).value() if getattr(self, "_inspection_pass_threshold_spin", None) else 0.35)
        fail_thr = float(getattr(self, "_inspection_fail_threshold_spin", None).value() if getattr(self, "_inspection_fail_threshold_spin", None) else 0.65)
        roi_enabled = bool(getattr(self, "_inspection_roi_enable_switch", None) and self._inspection_roi_enable_switch.isChecked())
        if roi_enabled:
            roi_text = (
                f"ROI {self._inspection_roi_x_spin.value():.2f}, {self._inspection_roi_y_spin.value():.2f}, "
                f"{self._inspection_roi_w_spin.value():.2f}, {self._inspection_roi_h_spin.value():.2f}"
            )
        else:
            roi_text = "Full frame"
        defect_text = ", ".join(defect_catalog[:4]) if defect_catalog else "No defect catalog defined"
        lbl.setText(
            f"{product} profile `{recipe}`. Thresholds: PASS <= {pass_thr:.2f}, FAIL >= {fail_thr:.2f}. "
            f"Region: {roi_text}. Defects: {defect_text}."
        )

    def _select_settings_tab(self, index: int):
        tabs = getattr(self, "_settings_tabs", None)
        if tabs is None:
            return
        try:
            target = int(index)
        except (TypeError, ValueError):
            return
        if 0 <= target < tabs.count():
            tabs.setCurrentIndex(target)

    def _refresh_settings_hub(self):
        profile = load_inspection_profile()
        runtime_cfg = profile.get("runtime") or {}
        camera_cfg = profile.get("camera") or {}
        gpio_cfg = profile.get("gpio") or {}
        runtime_host = normalize_runtime_host(runtime_cfg.get("host") or "") or "127.0.0.1"
        runtime_port = int(runtime_cfg.get("port") or 8787)
        trigger_mode = str(profile.get("trigger_mode") or "manual").strip()
        trigger_text = {
            "manual": "Manual trigger",
            "input_pin": "Input pin trigger",
            "continuous": "Continuous preview",
        }.get(trigger_mode, trigger_mode.replace("_", " ").title())
        capture_backend = str(camera_cfg.get("backend") or "mock_frame").replace("_", " ").title()
        zt_status = get_zerotier_status()
        zt_count = len(get_zerotier_networks() or [])
        fleet_nodes = 0
        try:
            fleet_nodes = len({str(row[1]).strip() for row in (self.db.get_devices() or []) if str(row[1]).strip()})
        except Exception:
            fleet_nodes = 0
        docker_state = "ACTIVE" if DockerManager.is_running() else "OFFLINE"
        active_pkg = get_local_active_model_package() or {}
        active_pkg_text = active_pkg.get("package_id") or active_pkg.get("version") or "No active package"

        if getattr(self, "_settings_hub_station_btn", None):
            self._settings_hub_station_btn.setText(
                "Runtime Hub\n"
                f"{runtime_host}:{runtime_port} • {fleet_nodes + 1} endpoints • {trigger_text}"
            )
        if getattr(self, "_settings_hub_network_btn", None):
            suffix = "network" if zt_count == 1 else "networks"
            self._settings_hub_network_btn.setText(
                "Remote Access\n"
                f"ZeroTier {zt_status} • {zt_count} {suffix}"
            )
        if getattr(self, "_settings_hub_system_btn", None):
            self._settings_hub_system_btn.setText(
                "Diagnostics\n"
                f"Docker {docker_state} • {platform.system()} {platform.machine()}"
            )
        if getattr(self, "_settings_hub_maintenance_btn", None):
            self._settings_hub_maintenance_btn.setText(
                "Maintenance\n"
                "Backup • Notifications • Preferences"
            )
        if getattr(self, "_settings_hub_models_btn", None):
            self._settings_hub_models_btn.setText(
                "Models & Workspaces\n"
                f"Active package: {active_pkg_text}"
            )
        if getattr(self, "_settings_hub_engineering_btn", None):
            self._settings_hub_engineering_btn.setText(
                "Engineering\n"
                "Commissioning and edge controls"
            )
        self._apply_settings_role_mode()

        maintenance_summary = getattr(self, "_maintenance_snapshot_label", None)
        if maintenance_summary is not None:
            notif_count = len(getattr(self, "_notifications", []) or [])
            maintenance_summary.setText(
                f"Notifications: {notif_count} • Theme: {'Dark' if getattr(self, 'is_dark', True) else 'Light'} • "
                f"Mode: {'Engineering' if getattr(self, '_ui_role_mode', 'operator') == 'engineering' else 'Operator'}\n"
                f"Scheduler: {str(getattr(self, '_scheduler_policy', 'manual')).title()} • "
                f"Checks: {'On' if getattr(self, '_background_health_checks_enabled', False) else 'Off'} • "
                f"Auto preview: {'On' if getattr(self, '_auto_camera_preview_on_launch', False) else 'Off'} • "
                f"Settings check: {'On' if getattr(self, '_check_remote_on_settings_open', False) else 'Off'} • "
                f"Devices auto-refresh: {'On' if getattr(self, '_devices_auto_refresh_enabled', False) else 'Off'} • "
                f"Setup on launch: {'On' if getattr(self, '_show_setup_wizard_on_launch', False) else 'Off'}"
            )
        eco_switch = getattr(self, "_maintenance_eco_switch", None)
        if eco_switch is not None and eco_switch.isChecked() != bool(getattr(self, "eco_mode", False)):
            eco_switch.blockSignals(True)
            eco_switch.setChecked(bool(getattr(self, "eco_mode", False)))
            eco_switch.blockSignals(False)
        theme_switch = getattr(self, "_maintenance_theme_switch", None)
        if theme_switch is not None and theme_switch.isChecked() != bool(getattr(self, "is_dark", True)):
            theme_switch.blockSignals(True)
            theme_switch.setChecked(bool(getattr(self, "is_dark", True)))
            theme_switch.blockSignals(False)
        devices_refresh_switch = getattr(self, "_maintenance_devices_auto_refresh_switch", None)
        if devices_refresh_switch is not None and devices_refresh_switch.isChecked() != bool(getattr(self, "_devices_auto_refresh_enabled", False)):
            devices_refresh_switch.blockSignals(True)
            devices_refresh_switch.setChecked(bool(getattr(self, "_devices_auto_refresh_enabled", False)))
            devices_refresh_switch.blockSignals(False)
        setup_wizard_switch = getattr(self, "_maintenance_setup_wizard_on_launch_switch", None)
        if setup_wizard_switch is not None and setup_wizard_switch.isChecked() != bool(getattr(self, "_show_setup_wizard_on_launch", False)):
            setup_wizard_switch.blockSignals(True)
            setup_wizard_switch.setChecked(bool(getattr(self, "_show_setup_wizard_on_launch", False)))
            setup_wizard_switch.blockSignals(False)
        bg_switch = getattr(self, "_maintenance_bg_checks_switch", None)
        if bg_switch is not None and bg_switch.isChecked() != bool(getattr(self, "_background_health_checks_enabled", False)):
            bg_switch.blockSignals(True)
            bg_switch.setChecked(bool(getattr(self, "_background_health_checks_enabled", False)))
            bg_switch.blockSignals(False)
        auto_switch = getattr(self, "_maintenance_camera_autostart_switch", None)
        if auto_switch is not None and auto_switch.isChecked() != bool(getattr(self, "_auto_camera_preview_on_launch", False)):
            auto_switch.blockSignals(True)
            auto_switch.setChecked(bool(getattr(self, "_auto_camera_preview_on_launch", False)))
            auto_switch.blockSignals(False)
        settings_open_switch = getattr(self, "_maintenance_check_on_settings_open_switch", None)
        if settings_open_switch is not None and settings_open_switch.isChecked() != bool(getattr(self, "_check_remote_on_settings_open", False)):
            settings_open_switch.blockSignals(True)
            settings_open_switch.setChecked(bool(getattr(self, "_check_remote_on_settings_open", False)))
            settings_open_switch.blockSignals(False)
        self._refresh_scheduler_profile_control()

        remote_summary = getattr(self, "_remote_access_summary_label", None)
        if remote_summary is not None:
            remote_mode = "Primary runtime" if not runtime_host or runtime_host == "127.0.0.1" else "Distributed runtime"
            remote_summary.setText(
                f"{remote_mode} • Host: {runtime_host or '127.0.0.1'}\n"
                f"Use Devices to manage additional edge nodes and ZeroTier inventory for fleet-wide operations ({fleet_nodes} registered nodes)."
            )
        runtime_chip = getattr(self, "_fleet_runtime_chip", None)
        if runtime_chip is not None:
            runtime_online = getattr(self, "_inspection_runtime_online", None)
            if runtime_online is True:
                self._set_status_pill(runtime_chip, "Runtime Online", "success")
            elif runtime_online is False:
                self._set_status_pill(runtime_chip, "Runtime Offline", "danger")
            else:
                self._set_status_pill(runtime_chip, "Runtime Unchecked", "neutral")
        nodes_chip = getattr(self, "_fleet_nodes_chip", None)
        if nodes_chip is not None:
            nodes_text = f"{fleet_nodes + 1} Endpoints" if (fleet_nodes + 1) != 1 else "1 Endpoint"
            self._set_status_pill(nodes_chip, nodes_text, "neutral")
        zt_chip = getattr(self, "_fleet_zt_chip", None)
        if zt_chip is not None:
            zt_tone = "success" if str(zt_status).upper() == "ONLINE" else ("danger" if str(zt_status).upper() in ("OFFLINE", "NOT INSTALLED") else "neutral")
            self._set_status_pill(zt_chip, f"ZT {zt_status}", zt_tone)

        diagnostics_summary = getattr(self, "_diagnostics_snapshot_label", None)
        if diagnostics_summary is not None:
            runtime_state = "reachable" if getattr(self, "_inspection_runtime_online", None) is True else "unreachable"
            if getattr(self, "_inspection_runtime_online", None) is None:
                runtime_state = "not checked yet"
            disk = psutil.disk_usage("/")
            diagnostics_summary.setText(
                f"Docker: {docker_state} • Runtime: {runtime_state}\n"
                f"Disk free: {int(disk.free / (1024**3))} GB • Platform: {platform.system()} {platform.machine()}"
            )

        snapshot = getattr(self, "_settings_station_snapshot", None)
        if snapshot is not None:
            station_name = str(profile.get("camera_name") or "VisionDock Runtime Node")
            gpio_text = "LED enabled" if gpio_cfg.get("enabled") else "LED disabled"
            snapshot.setText(
                f"{station_name}\n"
                f"Runtime endpoint: {runtime_host}:{runtime_port} • Capture: {capture_backend} • {trigger_text}\n"
                f"I/O: {gpio_text} • Output backend: {profile.get('output_backend') or 'mock'}"
            )

    def refresh_settings_page(self):
        if hasattr(self, "_sync_inspection_profile_ui"):
            try:
                self._sync_inspection_profile_ui()
            except Exception:
                pass
        if hasattr(self, "_refresh_docker_status"):
            try:
                self._refresh_docker_status()
            except Exception:
                pass
        if hasattr(self, "_refresh_notifications_panel"):
            try:
                self._refresh_notifications_panel()
            except Exception:
                pass
        self._refresh_settings_hub()

    def _apply_camera_commissioning_constraints(self):
        locked = bool(getattr(self, "_production_mode", False))
        for widget_name in (
            "camera_sensor_model_combo",
            "camera_focuser_combo",
            "camera_sensor_preset_btn",
            "_inspection_camera_model_combo",
            "_inspection_camera_focuser_combo",
            "_inspection_camera_apply_preset_btn",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(not locked)
        self._refresh_camera_sensor_profile_hint()
        self._refresh_inspection_runtime_sensor_hint()

    def _apply_inspection_camera_recommendation(self):
        sensor_model = self._selected_inspection_sensor_model()
        rec = csi_sensor_recommended_capture(sensor_model)
        if getattr(self, "_inspection_camera_width_spin", None):
            self._inspection_camera_width_spin.setValue(int(rec["width"]))
        if getattr(self, "_inspection_camera_height_spin", None):
            self._inspection_camera_height_spin.setValue(int(rec["height"]))
        if getattr(self, "_inspection_camera_fps_spin", None):
            self._inspection_camera_fps_spin.setValue(int(rec["fps"]))
        self._save_inspection_profile_from_ui()
        self.notify_success(
            f"Applied recommended inspection capture for {csi_sensor_label(sensor_model)}: "
            f"{int(rec['width'])}x{int(rec['height'])} @ {int(rec['fps'])} fps."
        )
        self._refresh_inspection_camera_profile_hint()

    def _inspection_pin_combo_value(self, combo):
        if combo is None:
            return None
        data = combo.currentData()
        if data in (None, "", "none"):
            return None
        try:
            return int(data)
        except (TypeError, ValueError):
            return None

    def _populate_inspection_pin_combo(self, combo, board_id: str, selected_pin, none_label: str):
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(none_label, None)
        pins = jetson_board_pins(board_id)
        for pin in pins:
            combo.addItem(format_jetson_pin_label(pin), int(pin.get("board_pin")))
        target = None
        try:
            if selected_pin is not None:
                target = int(selected_pin)
        except (TypeError, ValueError):
            target = None
        idx = 0
        if target is not None:
            for i in range(combo.count()):
                if combo.itemData(i) == target:
                    idx = i
                    break
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _refresh_inspection_pin_summary(self):
        lbl = getattr(self, "_inspection_pin_summary", None)
        if lbl is None:
            return
        board_id = (getattr(self, "_inspection_board_combo", None) and self._inspection_board_combo.currentData()) or ""
        gpio_enabled = bool(getattr(self, "_inspection_gpio_enable_switch", None) and self._inspection_gpio_enable_switch.isChecked())
        backend = (getattr(self, "_inspection_backend_combo", None) and self._inspection_backend_combo.currentText()) or "Mock output"
        sensor_model = self._selected_inspection_sensor_model()
        focuser_type = self._selected_inspection_focuser_type()
        parts = [
            f"Backend: {backend}",
            f"Camera={((getattr(self, '_inspection_camera_backend_combo', None) and self._inspection_camera_backend_combo.currentText()) or 'Mock frame')}",
            f"Sensor={csi_sensor_label(sensor_model)}",
            f"Focus={focuser_label(focuser_type)}",
            f"GPIO: {'enabled' if gpio_enabled else 'disabled'}",
            f"PASS={inspection_pin_display(board_id, self._inspection_pin_combo_value(getattr(self, '_inspection_pass_pin_combo', None)))}",
            f"FAIL={inspection_pin_display(board_id, self._inspection_pin_combo_value(getattr(self, '_inspection_fail_pin_combo', None)))}",
            f"FAULT={inspection_pin_display(board_id, self._inspection_pin_combo_value(getattr(self, '_inspection_fault_pin_combo', None)))}",
            f"BUSY={inspection_pin_display(board_id, self._inspection_pin_combo_value(getattr(self, '_inspection_busy_pin_combo', None)))}",
        ]
        parts.append(
            "Capture="
            f"{int(getattr(self, '_inspection_camera_width_spin', None).value() if getattr(self, '_inspection_camera_width_spin', None) else 1920)}x"
            f"{int(getattr(self, '_inspection_camera_height_spin', None).value() if getattr(self, '_inspection_camera_height_spin', None) else 1080)} @ "
            f"{int(getattr(self, '_inspection_camera_fps_spin', None).value() if getattr(self, '_inspection_camera_fps_spin', None) else 30)} fps"
        )
        trig_mode = (getattr(self, "_inspection_trigger_mode_combo", None) and self._inspection_trigger_mode_combo.currentText()) or "Manual trigger"
        trig_pin = inspection_pin_display(board_id, self._inspection_pin_combo_value(getattr(self, "_inspection_trigger_pin_combo", None)))
        parts.append(f"Trigger={trig_mode} ({trig_pin})")
        lbl.setText(" | ".join(parts))

    def _refresh_inspection_pin_combos(self, selected=None):
        if getattr(self, "_inspection_ui_loading", False):
            return
        board_id = (getattr(self, "_inspection_board_combo", None) and self._inspection_board_combo.currentData()) or ""
        selected = selected or {}
        self._populate_inspection_pin_combo(getattr(self, "_inspection_pass_pin_combo", None), board_id, selected.get("pass_pin"), "Disabled")
        self._populate_inspection_pin_combo(getattr(self, "_inspection_fail_pin_combo", None), board_id, selected.get("fail_pin"), "Disabled")
        self._populate_inspection_pin_combo(getattr(self, "_inspection_fault_pin_combo", None), board_id, selected.get("fault_pin"), "Disabled")
        self._populate_inspection_pin_combo(getattr(self, "_inspection_busy_pin_combo", None), board_id, selected.get("busy_pin"), "Disabled")
        self._populate_inspection_pin_combo(getattr(self, "_inspection_trigger_pin_combo", None), board_id, selected.get("trigger_pin"), "Disabled")

    def _sync_inspection_profile_ui(self):
        if not hasattr(self, "_inspection_camera_name_input"):
            return
        profile = load_inspection_profile()
        runtime_cfg = profile.get("runtime") or {}
        runtime_host = normalize_runtime_host(runtime_cfg.get("host") or "")
        if not runtime_host:
            runtime_host = (getattr(self, "node_ip", None) and self.node_ip.text().strip()) or "127.0.0.1"
        state = normalize_loaded_profile(profile, runtime_host, csi_sensor_default_focuser)
        self._inspection_ui_loading = True
        try:
            self._inspection_camera_name_input.setText(state["camera_name"])
            board_id = state["board_model"]
            if getattr(self, "_inspection_board_combo", None):
                idx = 0
                for i in range(self._inspection_board_combo.count()):
                    if self._inspection_board_combo.itemData(i) == board_id:
                        idx = i
                        break
                self._inspection_board_combo.setCurrentIndex(idx)
            backend = state["output_backend"]
            if getattr(self, "_inspection_backend_combo", None):
                idx = 0
                for i in range(self._inspection_backend_combo.count()):
                    if self._inspection_backend_combo.itemData(i) == backend:
                        idx = i
                        break
                self._inspection_backend_combo.setCurrentIndex(idx)
            trigger_mode = state["trigger_mode"]
            if getattr(self, "_inspection_trigger_mode_combo", None):
                idx = 0
                for i in range(self._inspection_trigger_mode_combo.count()):
                    if self._inspection_trigger_mode_combo.itemData(i) == trigger_mode:
                        idx = i
                        break
                self._inspection_trigger_mode_combo.setCurrentIndex(idx)
            gpio_cfg = state["gpio"]
            if getattr(self, "_inspection_gpio_enable_switch", None):
                self._inspection_gpio_enable_switch.setChecked(bool(gpio_cfg.get("enabled")))
            active_level = gpio_cfg.get("active_level") or "high"
            if getattr(self, "_inspection_active_level_combo", None):
                idx = 0
                for i in range(self._inspection_active_level_combo.count()):
                    if self._inspection_active_level_combo.itemData(i) == active_level:
                        idx = i
                        break
                self._inspection_active_level_combo.setCurrentIndex(idx)
            if getattr(self, "_inspection_runtime_host_input", None):
                self._inspection_runtime_host_input.setText(state["runtime_host"])
            if getattr(self, "_inspection_runtime_port_spin", None):
                self._inspection_runtime_port_spin.setValue(int(state["runtime_port"]))
            camera_cfg = state["camera"]
            if getattr(self, "_inspection_camera_backend_combo", None):
                idx = 0
                for i in range(self._inspection_camera_backend_combo.count()):
                    if self._inspection_camera_backend_combo.itemData(i) == camera_cfg["backend"]:
                        idx = i
                        break
                self._inspection_camera_backend_combo.setCurrentIndex(idx)
            if getattr(self, "_inspection_camera_model_combo", None):
                idx = 0
                wanted_model = str(camera_cfg["sensor_model"]).strip().upper()
                for i in range(self._inspection_camera_model_combo.count()):
                    if self._inspection_camera_model_combo.itemData(i) == wanted_model:
                        idx = i
                        break
                self._inspection_camera_model_combo.setCurrentIndex(idx)
            if getattr(self, "_inspection_camera_focuser_combo", None):
                idx = 0
                wanted_focuser = str(camera_cfg["focuser_type"]).strip().lower()
                for i in range(self._inspection_camera_focuser_combo.count()):
                    if self._inspection_camera_focuser_combo.itemData(i) == wanted_focuser:
                        idx = i
                        break
                self._inspection_camera_focuser_combo.setCurrentIndex(idx)
            if getattr(self, "_inspection_camera_sensor_spin", None):
                self._inspection_camera_sensor_spin.setValue(int(camera_cfg["sensor_id"]))
            if getattr(self, "_inspection_camera_device_spin", None):
                self._inspection_camera_device_spin.setValue(int(camera_cfg["device_index"]))
            if getattr(self, "_inspection_camera_width_spin", None):
                self._inspection_camera_width_spin.setValue(int(camera_cfg["capture_width"]))
            if getattr(self, "_inspection_camera_height_spin", None):
                self._inspection_camera_height_spin.setValue(int(camera_cfg["capture_height"]))
            if getattr(self, "_inspection_camera_fps_spin", None):
                self._inspection_camera_fps_spin.setValue(int(camera_cfg["framerate"]))
            if getattr(self, "_inspection_camera_burst_spin", None):
                self._inspection_camera_burst_spin.setValue(int(state["inspection"]["frame_vote_count"]))
            if getattr(self, "_inspection_camera_source_input", None):
                self._inspection_camera_source_input.setText(str(camera_cfg["source_path"]))
            inspection_cfg = state["inspection"]
            if getattr(self, "_inspection_profile_name_input", None):
                self._inspection_profile_name_input.setText(str(inspection_cfg["profile_name"]))
            if getattr(self, "_inspection_recipe_name_input", None):
                self._inspection_recipe_name_input.setText(str(inspection_cfg["recipe_name"]))
            if getattr(self, "_inspection_product_label_input", None):
                self._inspection_product_label_input.setText(str(inspection_cfg["product_label"]))
            if getattr(self, "_inspection_expected_label_input", None):
                self._inspection_expected_label_input.setText(str(inspection_cfg["expected_object_label"]))
            if getattr(self, "_inspection_pass_display_input", None):
                self._inspection_pass_display_input.setText(str(inspection_cfg["pass_display_label"]))
            if getattr(self, "_inspection_fail_display_input", None):
                self._inspection_fail_display_input.setText(str(inspection_cfg["fail_display_label"]))
            if getattr(self, "_inspection_defect_catalog_input", None):
                defect_catalog = inspection_cfg["defect_catalog"]
                self._inspection_defect_catalog_input.setText(", ".join(str(x) for x in defect_catalog if str(x).strip()))
            if getattr(self, "_inspection_pass_threshold_spin", None):
                self._inspection_pass_threshold_spin.setValue(float(inspection_cfg["pass_threshold"]))
            if getattr(self, "_inspection_fail_threshold_spin", None):
                self._inspection_fail_threshold_spin.setValue(float(inspection_cfg["fail_threshold"]))
            if getattr(self, "_inspection_hard_fail_threshold_spin", None):
                self._inspection_hard_fail_threshold_spin.setValue(float(inspection_cfg["hard_fail_threshold"]))
            roi_cfg = inspection_cfg["roi"]
            if getattr(self, "_inspection_roi_enable_switch", None):
                self._inspection_roi_enable_switch.setChecked(bool(roi_cfg.get("enabled")))
            if getattr(self, "_inspection_roi_x_spin", None):
                self._inspection_roi_x_spin.setValue(float(roi_cfg.get("x") or 0.0))
            if getattr(self, "_inspection_roi_y_spin", None):
                self._inspection_roi_y_spin.setValue(float(roi_cfg.get("y") or 0.0))
            if getattr(self, "_inspection_roi_w_spin", None):
                self._inspection_roi_w_spin.setValue(float(roi_cfg.get("width") or 1.0))
            if getattr(self, "_inspection_roi_h_spin", None):
                self._inspection_roi_h_spin.setValue(float(roi_cfg.get("height") or 1.0))
            if getattr(self, "_inspection_save_fail_switch", None):
                self._inspection_save_fail_switch.setChecked(bool(inspection_cfg.get("save_fail_frames", True)))
            if getattr(self, "_inspection_save_uncertain_switch", None):
                self._inspection_save_uncertain_switch.setChecked(bool(inspection_cfg.get("save_uncertain_frames", True)))
            self._inspection_ui_loading = False
            self._refresh_inspection_pin_combos(gpio_cfg)
            self._refresh_inspection_pin_summary()
            self._refresh_inspection_camera_profile_hint()
            self._refresh_sock_detection_profile_hint()
            self._apply_camera_commissioning_constraints()
            self._refresh_settings_hub()
        finally:
            self._inspection_ui_loading = False

    def _save_inspection_profile_from_ui(self):
        if getattr(self, "_inspection_ui_loading", False):
            return
        if not hasattr(self, "_inspection_camera_name_input"):
            return
        profile = load_inspection_profile()
        ui = {
            "camera_name": (self._inspection_camera_name_input.text() or "").strip(),
            "board_model": (self._inspection_board_combo.currentData() or "JETSON_ORIN_NANO") if getattr(self, "_inspection_board_combo", None) else "JETSON_ORIN_NANO",
            "output_backend": (self._inspection_backend_combo.currentData() or "mock") if getattr(self, "_inspection_backend_combo", None) else "mock",
            "trigger_mode": (self._inspection_trigger_mode_combo.currentData() or "manual") if getattr(self, "_inspection_trigger_mode_combo", None) else "manual",
            "runtime_port": int(self._inspection_runtime_port_spin.value()) if getattr(self, "_inspection_runtime_port_spin", None) else 8787,
            "runtime_host": normalize_runtime_host(getattr(self, "_inspection_runtime_host_input", None) and self._inspection_runtime_host_input.text()) or "127.0.0.1",
            "camera_backend": (self._inspection_camera_backend_combo.currentData() or "mock_frame") if getattr(self, "_inspection_camera_backend_combo", None) else "mock_frame",
            "sensor_model": (self._inspection_camera_model_combo.currentData() or "GENERIC_CSI") if getattr(self, "_inspection_camera_model_combo", None) else "GENERIC_CSI",
            "focuser_type": (self._inspection_camera_focuser_combo.currentData() or "") if getattr(self, "_inspection_camera_focuser_combo", None) else "",
            "sensor_id": int(self._inspection_camera_sensor_spin.value()) if getattr(self, "_inspection_camera_sensor_spin", None) else 0,
            "device_index": int(self._inspection_camera_device_spin.value()) if getattr(self, "_inspection_camera_device_spin", None) else 0,
            "capture_width": int(self._inspection_camera_width_spin.value()) if getattr(self, "_inspection_camera_width_spin", None) else 1920,
            "capture_height": int(self._inspection_camera_height_spin.value()) if getattr(self, "_inspection_camera_height_spin", None) else 1080,
            "framerate": int(self._inspection_camera_fps_spin.value()) if getattr(self, "_inspection_camera_fps_spin", None) else 30,
            "burst_count": int(self._inspection_camera_burst_spin.value()) if getattr(self, "_inspection_camera_burst_spin", None) else 3,
            "source_path": (self._inspection_camera_source_input.text() or "").strip() if getattr(self, "_inspection_camera_source_input", None) else "",
            "profile_name": (self._inspection_profile_name_input.text() or "").strip() if getattr(self, "_inspection_profile_name_input", None) else "",
            "recipe_name": (self._inspection_recipe_name_input.text() or "").strip() if getattr(self, "_inspection_recipe_name_input", None) else "",
            "product_label": (self._inspection_product_label_input.text() or "").strip() if getattr(self, "_inspection_product_label_input", None) else "",
            "expected_object_label": (self._inspection_expected_label_input.text() or "").strip() if getattr(self, "_inspection_expected_label_input", None) else "",
            "pass_display_label": (self._inspection_pass_display_input.text() or "").strip() if getattr(self, "_inspection_pass_display_input", None) else "",
            "fail_display_label": (self._inspection_fail_display_input.text() or "").strip() if getattr(self, "_inspection_fail_display_input", None) else "",
            "defect_catalog": (self._inspection_defect_catalog_input.text() or "").strip() if getattr(self, "_inspection_defect_catalog_input", None) else "",
            "pass_threshold": float(self._inspection_pass_threshold_spin.value()) if getattr(self, "_inspection_pass_threshold_spin", None) else 0.35,
            "fail_threshold": float(self._inspection_fail_threshold_spin.value()) if getattr(self, "_inspection_fail_threshold_spin", None) else 0.65,
            "hard_fail_threshold": float(self._inspection_hard_fail_threshold_spin.value()) if getattr(self, "_inspection_hard_fail_threshold_spin", None) else 0.9,
            "save_fail_frames": bool(getattr(self, "_inspection_save_fail_switch", None) and self._inspection_save_fail_switch.isChecked()),
            "save_uncertain_frames": bool(getattr(self, "_inspection_save_uncertain_switch", None) and self._inspection_save_uncertain_switch.isChecked()),
            "roi_enabled": bool(getattr(self, "_inspection_roi_enable_switch", None) and self._inspection_roi_enable_switch.isChecked()),
            "roi_x": float(self._inspection_roi_x_spin.value()) if getattr(self, "_inspection_roi_x_spin", None) else 0.0,
            "roi_y": float(self._inspection_roi_y_spin.value()) if getattr(self, "_inspection_roi_y_spin", None) else 0.0,
            "roi_width": float(self._inspection_roi_w_spin.value()) if getattr(self, "_inspection_roi_w_spin", None) else 1.0,
            "roi_height": float(self._inspection_roi_h_spin.value()) if getattr(self, "_inspection_roi_h_spin", None) else 1.0,
            "gpio_enabled": bool(getattr(self, "_inspection_gpio_enable_switch", None) and self._inspection_gpio_enable_switch.isChecked()),
            "gpio_active_level": (self._inspection_active_level_combo.currentData() or "high") if getattr(self, "_inspection_active_level_combo", None) else "high",
            "pass_pin": self._inspection_pin_combo_value(getattr(self, "_inspection_pass_pin_combo", None)),
            "fail_pin": self._inspection_pin_combo_value(getattr(self, "_inspection_fail_pin_combo", None)),
            "fault_pin": self._inspection_pin_combo_value(getattr(self, "_inspection_fault_pin_combo", None)),
            "busy_pin": self._inspection_pin_combo_value(getattr(self, "_inspection_busy_pin_combo", None)),
            "trigger_pin": self._inspection_pin_combo_value(getattr(self, "_inspection_trigger_pin_combo", None)),
        }
        profile = build_profile_from_ui(
            profile,
            ui,
            csi_sensor_default_focuser,
            csi_sensor_label,
        )
        profile, warnings = validate_profile(profile)
        save_inspection_profile(profile)
        if warnings:
            if _env_truthy("VISIONDOCK_NOTIFY_PROFILE_SANITIZE"):
                self.notify_warning(t("notif.profile_autofix_warning", "Profile settings auto-corrected. Save completed safely."))
            elif not _env_truthy("VISIONDOCK_SILENT_PROFILE_SANITIZE"):
                self.notify_info(t("notif.profile_autofix_info", "Profile settings auto-corrected."))
        self._refresh_inspection_pin_summary()
        self._refresh_inspection_camera_profile_hint()
        self._refresh_sock_detection_profile_hint()
        self._refresh_settings_hub()
        if hasattr(self, "_update_camera_runtime_widgets") and hasattr(self, "_camera_runtime_status_badge"):
            try:
                if getattr(self, "_inspection_runtime_online", None) is False:
                    self._update_camera_runtime_widgets(error=getattr(self, "_inspection_runtime_last_error", "") or "Runtime unreachable.")
                else:
                    self._update_camera_runtime_widgets(state=getattr(self, "_inspection_runtime_state", {}) or None)
            except Exception:
                pass

    def _on_inspection_board_changed(self, _index):
        if getattr(self, "_inspection_ui_loading", False):
            return
        prev = load_inspection_profile().get("gpio") or {}
        self._refresh_inspection_pin_combos(prev)
        self._save_inspection_profile_from_ui()

    def _apply_v4l2_exposure_gain(self):
        if not (getattr(self, "camera_aelock_switch", None) and self.camera_aelock_switch.isChecked()):
            return
        d = get_camera_defaults()
        dev = "/dev/video0"
        if platform.system() != "Linux": return
        try:
            subprocess.run(["v4l2-ctl", "-d", dev, "--set-ctrl", f"exposure_time_absolute={d['v4l2_exposure']}"], capture_output=True, timeout=2)
        except Exception:
            try:
                subprocess.run(["v4l2-ctl", "-d", dev, "--set-ctrl", f"exposure={d['v4l2_exposure']}"], capture_output=True, timeout=2)
            except Exception:
                pass
        try:
            subprocess.run(["v4l2-ctl", "-d", dev, "--set-ctrl", f"gain={d['v4l2_gain']}"], capture_output=True, timeout=2)
        except Exception:
            pass

    def _apply_focus_csi_camera(self):
        """Fixed/Manual: apply slider position via I2C. Auto: run autofocus script with confirmation."""
        d = get_camera_defaults()
        mode = d.get("focus_mode", "fixed")
        pos = int(d.get("focus_position", 512))
        bus = int(d.get("i2c_bus", 10))
        sensor_id = int(d.get("sensor_id", 0))
        focuser_type = str(d.get("focuser_type") or csi_sensor_default_focuser(d.get("sensor_model"))).strip().lower()
        if not focuser_available(focuser_type):
            QMessageBox.information(
                self,
                "Focus",
                "The selected CSI configuration is set to fixed lens. Change Focus actuator to a motorized focuser only if your exact module supports it.",
            )
            return
        scripts = focus_scripts_for_focuser(focuser_type)
        base = os.path.dirname(os.path.dirname(__file__))
        if mode == "auto":
            autofocus_name = scripts.get("autofocus") or ""
            autofocus_script = os.path.join(base, "scripts", autofocus_name) if autofocus_name else ""
            if not autofocus_name or not os.path.exists(autofocus_script):
                QMessageBox.information(
                    self,
                    "Focus",
                    "Autofocus script for the selected focuser was not found. Use Manual mode or see docs/CSI_CAMERA_FOCUS.md.",
                )
                return
            ans = QMessageBox.question(
                self,
                "Autofocus",
                "If a live CSI preview is running, autofocus may fail because the camera stack often allows only one active pipeline.\n\n"
                "Stop the camera card first for reliable results.\n\nContinue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            ok, msg = _run_script_checked(
                [
                    sys.executable,
                    autofocus_script,
                    "--bus",
                    str(bus),
                    "--sensor-id",
                    str(sensor_id),
                ],
                cwd=base,
                timeout=120,
            )
            if ok:
                self.show_toast("Autofocus finished")
            else:
                QMessageBox.warning(self, "Focus", "Autofocus failed.\n\n" + (msg or "Unknown error"))
            return
        focus_name = scripts.get("focus") or ""
        script_path = os.path.join(base, "scripts", focus_name) if focus_name else ""
        if not focus_name or not os.path.exists(script_path):
            QMessageBox.information(self, "Focus", "Focus script for the selected focuser was not found. See docs/CSI_CAMERA_FOCUS.md.")
            return
        ok, msg = _run_script_checked(
            [sys.executable, script_path, "--bus", str(bus), "--position", str(pos)],
            cwd=base,
            timeout=10,
        )
        if ok:
            self.show_toast(f"Focus ({mode}) set to {pos} (bus {bus})")
        else:
            QMessageBox.warning(
                self,
                "Focus",
                "Focus script failed (check I2C bus, i2cset permissions).\n\n" + (msg or "Unknown error"),
            )

    def refresh_images(self):
        # Professional UI transition: clear current list
        while self.img_list_layout.count():
            item = self.img_list_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
        imgs = DockerManager.list_images()
        if not imgs:
            placeholder = QLabel("No local images found. Pull from NGC or Docker Hub.")
            placeholder.setObjectName("CaptionMuted")
            placeholder.setStyleSheet("padding: 10px;")
            self.img_list_layout.addWidget(placeholder)
            return

        for im in imgs:
            r = surface_row()
            r.setFixedHeight(60)
            rl = QHBoxLayout(r); rl.setContentsMargins(15,8,10,8); rl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            
            info = QVBoxLayout(); info.setSpacing(2); info.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            repo_lbl = QLabel(im["repo"])
            repo_lbl.setObjectName("ListPrimary")
            tag_lbl = QLabel(f"Tag: {im['tag']}  ·  ID: {im['id'][:12]}")
            tag_lbl.setObjectName("MonoMuted")
            info.addWidget(repo_lbl); info.addWidget(tag_lbl); rl.addLayout(info)
            
            rl.addStretch()
            size_lbl = QLabel(im["size"])
            size_lbl.setObjectName("DockSize")
            rl.addWidget(size_lbl)
            
            if not getattr(self, "_production_mode", False):
                db = QPushButton("Remove")
                db.setObjectName("ImgPurgeBtn")
                db.setFixedSize(64, 28)
                db.setCursor(Qt.CursorShape.PointingHandCursor)
                db.clicked.connect(lambda _, i=im['id']: [DockerManager.remove_image(i), QTimer.singleShot(600, self.refresh_images)])
                rl.addWidget(db)
            self.img_list_layout.addWidget(r)

    def create_add_btn(self, text, cb, hint=None):
        b = QPushButton()
        b.setObjectName("AddBtn")
        b.setFixedSize(372, 252)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(cb)
        lay = QVBoxLayout(b)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        lay.setContentsMargins(20, 24, 20, 24)
        ico = QLabel("+")
        ico.setObjectName("AddBtnIcon")
        ico.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tit = QLabel(text)
        tit.setObjectName("AddBtnTitle")
        tit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tit.setWordWrap(True)
        lay.addWidget(ico)
        lay.addWidget(tit)
        if hint:
            hi = QLabel(hint)
            hi.setObjectName("AddBtnHint")
            hi.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hi.setWordWrap(True)
            hi.setMaximumWidth(320)
            lay.addWidget(hi)
        return b

    def filter_cameras(self, t):
        for i in range(self.cf.count()):
            w = self.cf.itemAt(i).widget()
            if isinstance(w, ResizableCard): w.setVisible(t.lower() in w.title_text.lower())

    def filter_workspaces(self, t):
        for i in range(self.df.count()):
            w = self.df.itemAt(i).widget()
            if isinstance(w, ResizableCard): w.setVisible(t.lower() in w.title_text.lower())

    def _set_combo_items(self, combo, items, empty_label="Select...", empty_data=""):
        if combo is None:
            return
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(empty_label, empty_data)
        for label, data in items:
            combo.addItem(label, data)
        idx = 0
        for i in range(combo.count()):
            if combo.itemData(i) == current:
                idx = i
                break
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _package_version_suggestion(self):
        return "v" + datetime.now().strftime("%Y.%m.%d.%H%M")

    def _runtime_target_host(self):
        profile = load_inspection_profile()
        runtime_cfg = profile.get("runtime") or {}
        host = normalize_runtime_host(runtime_cfg.get("host") or "")
        if not host:
            host = (getattr(self, "node_ip", None) and self.node_ip.text().strip()) or "127.0.0.1"
        return host or "127.0.0.1"

    def _runtime_target_port(self):
        profile = load_inspection_profile()
        runtime_cfg = profile.get("runtime") or {}
        try:
            return int(runtime_cfg.get("port") or 8787)
        except (TypeError, ValueError):
            return 8787

    def _runtime_is_local(self):
        host = self._runtime_target_host().lower()
        return host in ("127.0.0.1", "localhost", "::1") or host == platform.node().lower()

    def _apply_runtime_host_selection(self, host: str):
        target = normalize_runtime_host(host)
        if not target:
            self.notify_warning("No runtime host selected.")
            return
        if getattr(self, "_inspection_runtime_host_input", None):
            self._inspection_runtime_host_input.setText(target)
        if getattr(self, "node_ip", None):
            self.node_ip.setText(target)
        save_app_prefs_remote_host(target)
        DockerManager.set_host(target)
        self._save_inspection_profile_from_ui()
        self.notify_success(f"Runtime host selected: {target}")
        self._refresh_workspace_package_panel()
        self._refresh_camera_runtime_card(force=True)

    def _local_workspace_rows(self):
        rows = []
        if not hasattr(self, "db"):
            return rows
        for name, img, cid, host in self.db.get_workspaces():
            if (host or "").strip():
                continue
            ws_dir = workspace_dir_for_name(name)
            rows.append({
                "name": name,
                "image": img,
                "cid": (cid or "")[:12],
                "host": host or "",
                "workspace_dir": ws_dir,
                "exists": os.path.isdir(ws_dir),
            })
        return rows

    def _selected_workspace_export_record(self):
        target_name = (getattr(self, "_workspace_export_workspace_combo", None) and self._workspace_export_workspace_combo.currentData()) or ""
        for row in self._local_workspace_rows():
            if row["name"] == target_name:
                return row
        return None

    def _refresh_workspace_export_candidates(self):
        record = self._selected_workspace_export_record()
        scan = {"model_artifacts": [], "label_files": [], "recipe_files": [], "runtime_files": []}
        if record and record.get("exists"):
            scan = scan_workspace_candidates(record["workspace_dir"])
        self._workspace_export_scan = scan

        def _pairs(values):
            return [(v, v) for v in values]

        self._set_combo_items(getattr(self, "_workspace_export_model_combo", None), _pairs(scan.get("model_artifacts", [])), "Select model artifact", "")
        self._set_combo_items(getattr(self, "_workspace_export_labels_combo", None), _pairs(scan.get("label_files", [])), "No labels file", "")
        self._set_combo_items(getattr(self, "_workspace_export_recipe_combo", None), _pairs(scan.get("recipe_files", [])), "No recipe file", "")
        self._set_combo_items(getattr(self, "_workspace_export_runtime_combo", None), _pairs(scan.get("runtime_files", [])), "No runtime config", "")
        if getattr(self, "_workspace_export_model_combo", None) and self._workspace_export_model_combo.count() > 1 and not self._workspace_export_model_combo.currentData():
            self._workspace_export_model_combo.setCurrentIndex(1)

        if getattr(self, "_workspace_export_name_input", None) and record:
            if not self._workspace_export_name_input.text().strip():
                self._workspace_export_name_input.setText(str(record["name"]).replace("_", "-"))
        if getattr(self, "_workspace_export_version_input", None) and not self._workspace_export_version_input.text().strip():
            self._workspace_export_version_input.setText(self._package_version_suggestion())

    def _refresh_workspace_package_panel(self):
        local_workspaces = self._local_workspace_rows()
        workspace_items = []
        for row in local_workspaces:
            suffix = "" if row.get("exists") else " (missing folder)"
            workspace_items.append((f"{pretty_workspace_title(row['name'])}{suffix}", row["name"]))
        self._set_combo_items(
            getattr(self, "_workspace_export_workspace_combo", None),
            workspace_items,
            "Select local workspace",
            "",
        )
        if getattr(self, "_workspace_export_workspace_combo", None) and self._workspace_export_workspace_combo.count() > 1 and not self._workspace_export_workspace_combo.currentData():
            self._workspace_export_workspace_combo.setCurrentIndex(1)
        self._refresh_workspace_export_candidates()

        local_packages = list_local_model_packages()
        self._workspace_local_packages_cache = {str(pkg.get("package_id")): pkg for pkg in local_packages}
        local_items = [
            (
                f"{pkg.get('package_name') or pkg.get('package_id')} · {pkg.get('version') or 'unassigned'}",
                pkg.get("package_id"),
            )
            for pkg in local_packages
        ]
        self._set_combo_items(getattr(self, "_workspace_local_package_combo", None), local_items, "Select exported package", "")

        runtime_desc = f"Runtime endpoint: {self._runtime_target_host()}:{self._runtime_target_port()}"
        if hasattr(self, "_workspace_package_runtime_label"):
            self._workspace_package_runtime_label.setText(runtime_desc)
        self._refresh_runtime_package_inventory()

    def _refresh_runtime_package_inventory(self):
        if getattr(self, "_workspace_runtime_inventory_thread", None) and self._workspace_runtime_inventory_thread.isRunning():
            return
        self._workspace_runtime_inventory_thread = InspectionRuntimeRequestThread(
            self._inspection_runtime_url("/models"),
            method="GET",
            timeout=4.0,
        )
        self._workspace_runtime_inventory_thread.result_signal.connect(self._on_runtime_package_inventory_result)
        self._workspace_runtime_inventory_thread.error_signal.connect(self._on_runtime_package_inventory_error)
        self._workspace_runtime_inventory_thread.start()

    def _apply_runtime_package_inventory(self, packages=None, active_package=None, previous_package=None, error=""):
        packages = packages or []
        self._workspace_runtime_packages_cache = {
            str(pkg.get("package_id")): pkg for pkg in packages if isinstance(pkg, dict)
        }
        runtime_items = [
            (
                f"{pkg.get('package_name') or pkg.get('package_id')} · {pkg.get('version') or 'unassigned'}",
                pkg.get("package_id"),
            )
            for pkg in packages
        ]
        self._set_combo_items(getattr(self, "_workspace_runtime_package_combo", None), runtime_items, "Select runtime package", "")

        active = active_package if isinstance(active_package, dict) else None
        if active:
            text = f"{active.get('package_name') or active.get('package_id')}\n{active.get('version') or 'unassigned'}"
            if hasattr(self, "_workspace_package_active_label"): self._set_runtime_badge(self._workspace_package_active_label, text, "success")
        else:
            tone = "danger" if error else "neutral"
            label = "NO ACTIVE PACKAGE" if not error else "RUNTIME OFFLINE"
            if hasattr(self, "_workspace_package_active_label"): self._set_runtime_badge(self._workspace_package_active_label, label, tone)

        local_count = len(getattr(self, "_workspace_local_packages_cache", {}) or {})
        runtime_count = len(self._workspace_runtime_packages_cache)
        if hasattr(self, "_workspace_package_inventory_label"):
            self._workspace_package_inventory_label.setText(f"Packages: {local_count} local / {runtime_count} runtime")
        if hasattr(self, "_workspace_package_status"):
            if error:
                self._workspace_package_status.setText(f"Runtime package inventory unavailable: {error}")
            elif active:
                prev_txt = ""
                if isinstance(previous_package, dict) and previous_package.get("package_id"):
                    prev_txt = f" Previous: {previous_package.get('package_name') or previous_package.get('package_id')}."
                self._workspace_package_status.setText(
                    f"Active runtime package: {active.get('package_name') or active.get('package_id')} "
                    f"{active.get('version') or 'unassigned'}.{prev_txt}"
                )
            else:
                self._workspace_package_status.setText("Runtime has no active package yet. Export and activate one from a workspace.")

    def _on_runtime_package_inventory_result(self, payload):
        packages = payload.get("packages") if isinstance(payload.get("packages"), list) else []
        active_package = payload.get("active_package")
        previous_package = payload.get("previous_package")
        self._apply_runtime_package_inventory(packages, active_package, previous_package, error="")

    def _on_runtime_package_inventory_error(self, message):
        if self._runtime_is_local():
            active_package = get_local_active_model_package()
            self._apply_runtime_package_inventory(
                list_local_model_packages(),
                active_package,
                None,
                error="",
            )
            if hasattr(self, "_workspace_package_status"):
                self._workspace_package_status.setText(
                    "Runtime inventory request failed; showing local package registry instead. Start the runtime to sync live state."
                )
            return
        self._apply_runtime_package_inventory([], None, None, error=str(message or "Unknown error"))

    def _selected_local_package(self):
        package_id = (getattr(self, "_workspace_local_package_combo", None) and self._workspace_local_package_combo.currentData()) or ""
        return self._workspace_local_packages_cache.get(str(package_id or ""))

    def _selected_runtime_package_id(self):
        package_id = (getattr(self, "_workspace_runtime_package_combo", None) and self._workspace_runtime_package_combo.currentData()) or ""
        if package_id:
            return str(package_id)
        local_pkg = self._selected_local_package()
        return str(local_pkg.get("package_id")) if isinstance(local_pkg, dict) else ""

    def _export_selected_workspace_package(self):
        record = self._selected_workspace_export_record()
        if not record:
            self.notify_warning("Select a local workspace to export.")
            return
        if not record.get("exists"):
            self.notify_error("Selected workspace folder does not exist on disk.")
            return
        artifact_rel = (getattr(self, "_workspace_export_model_combo", None) and self._workspace_export_model_combo.currentData()) or ""
        if not artifact_rel:
            self.notify_warning("Select a model artifact from the workspace.")
            return
        package_name = (getattr(self, "_workspace_export_name_input", None) and self._workspace_export_name_input.text()) or record["name"]
        version = (getattr(self, "_workspace_export_version_input", None) and self._workspace_export_version_input.text()) or ""
        try:
            metadata = build_package_from_workspace(
                workspace_name=record["name"],
                workspace_dir=record["workspace_dir"],
                version=version.strip(),
                artifact_relpath=str(artifact_rel),
                package_name=(package_name or record["name"]).strip(),
                label_relpath=(getattr(self, "_workspace_export_labels_combo", None) and self._workspace_export_labels_combo.currentData()) or None,
                recipe_relpath=(getattr(self, "_workspace_export_recipe_combo", None) and self._workspace_export_recipe_combo.currentData()) or None,
                runtime_relpath=(getattr(self, "_workspace_export_runtime_combo", None) and self._workspace_export_runtime_combo.currentData()) or None,
                source_image=record.get("image"),
                container_id=record.get("cid"),
            )
        except Exception as exc:
            self.notify_error(f"Package export failed: {exc}")
            return
        self.notify_success(f"Package exported: {metadata.get('package_id')}")
        if getattr(self, "_workspace_export_version_input", None):
            self._workspace_export_version_input.setText(self._package_version_suggestion())
        self._refresh_workspace_package_panel()
        package_id = metadata.get("package_id")
        if package_id and getattr(self, "_workspace_local_package_combo", None):
            for i in range(self._workspace_local_package_combo.count()):
                if self._workspace_local_package_combo.itemData(i) == package_id:
                    self._workspace_local_package_combo.setCurrentIndex(i)
                    break

    def _find_runtime_ssh_transport(self):
        host = self._runtime_target_host()
        norm_host = normalize_runtime_host(host)
        if not norm_host or self._runtime_is_local():
            return {"mode": "local", "host": norm_host}
        for key, client in getattr(self, "_ssh_sessions", {}).items():
            session_host = key[0] if isinstance(key, tuple) else ""
            session_user = key[1] if isinstance(key, tuple) and len(key) > 1 else "jetson"
            if normalize_runtime_host(session_host) == norm_host:
                return {"mode": "session", "host": norm_host, "user": session_user, "client": client}
            try:
                zt_ips = ssh_get_zerotier_ips(client)
            except Exception:
                zt_ips = []
            if norm_host in [normalize_runtime_host(ip) for ip in zt_ips]:
                return {"mode": "session", "host": norm_host, "user": session_user, "client": client}
        if hasattr(self, "db"):
            key_rows = []
            for _name, dev_host, dev_user, key_path in self.db.get_devices():
                if normalize_runtime_host(dev_host) == norm_host:
                    return {
                        "mode": "key" if key_path and os.path.exists(os.path.expanduser(key_path)) else "saved",
                        "host": norm_host,
                        "user": dev_user or "jetson",
                        "key_path": os.path.expanduser(key_path) if key_path else "",
                    }
                if key_path and os.path.exists(os.path.expanduser(key_path)):
                    key_rows.append((dev_user or "jetson", os.path.expanduser(key_path)))
            if len(key_rows) == 1:
                user, key_path = key_rows[0]
                return {"mode": "key", "host": norm_host, "user": user, "key_path": key_path}
        return {"mode": "missing", "host": norm_host}

    def _deploy_selected_package_to_runtime(self):
        package = self._selected_local_package()
        if not package:
            self.notify_warning("Select an exported local package first.")
            return
        if self._runtime_is_local():
            self.notify_success("Local runtime uses exported packages directly. Deploy step is not required.")
            self._refresh_runtime_package_inventory()
            return

        package_dir = str(package.get("package_dir") or "").strip()
        package_id = str(package.get("package_id") or "").strip()
        if not package_dir or not os.path.isdir(package_dir):
            self.notify_error("Selected package directory is missing.")
            return

        transport = self._find_runtime_ssh_transport()
        remote_root = ".visiondock/model_packages"
        remote_package_dir = posixpath.join(remote_root, package_id)
        try:
            if transport.get("mode") == "session":
                client = transport["client"]
                ssh_exec_text(client, "mkdir -p ~/.visiondock/model_packages", timeout=20)
                sftp_upload_tree(client, package_dir, remote_package_dir)
            elif transport.get("mode") == "key":
                host = transport["host"]
                user = transport.get("user") or "jetson"
                key_path = transport.get("key_path") or ""
                subprocess.run(
                    ["ssh", "-i", key_path, f"{user}@{host}", "mkdir -p ~/.visiondock/model_packages"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                subprocess.run(
                    ["scp", "-r", "-i", key_path, package_dir, f"{user}@{host}:~/.visiondock/model_packages/"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            else:
                self.notify_error(
                    "Remote deploy requires either an active SSH session from Devices or a saved SSH key for the runtime host."
                )
                return
        except Exception as exc:
            self.notify_error(f"Package deploy failed: {exc}")
            return

        self.notify_success(f"Package deployed to runtime host: {package_id}")
        self._refresh_runtime_package_inventory()

    def _set_workspace_package_action_busy(self, kind, busy):
        mapping = {
            "activate": (getattr(self, "_workspace_activate_btn", None), "Activate Package", "Activating…"),
            "rollback": (getattr(self, "_workspace_rollback_btn", None), "Rollback", "Rolling back…"),
        }
        btn, idle_txt, busy_txt = mapping.get(kind, (None, "", ""))
        if btn is not None:
            btn.setEnabled(not busy)
            btn.setText(busy_txt if busy else idle_txt)

    def _start_workspace_package_runtime_action(self, kind, path, payload=None):
        if getattr(self, "_workspace_package_action_thread", None) and self._workspace_package_action_thread.isRunning():
            return False
        self._set_workspace_package_action_busy(kind, True)
        self._workspace_package_action_thread = InspectionRuntimeRequestThread(
            self._inspection_runtime_url(path),
            method="POST",
            payload=payload or {},
            timeout=5.0,
        )
        self._workspace_package_action_thread.result_signal.connect(lambda data, k=kind: self._on_workspace_package_action_result(k, data))
        self._workspace_package_action_thread.error_signal.connect(lambda msg, k=kind: self._on_workspace_package_action_error(k, msg))
        self._workspace_package_action_thread.start()
        return True

    def _activate_selected_runtime_package(self):
        package_id = self._selected_runtime_package_id()
        if not package_id:
            self.notify_warning("Select a runtime package to activate.")
            return
        self._workspace_pending_local_package_id = package_id
        self._start_workspace_package_runtime_action("activate", "/activate", {"package_id": package_id})

    def _rollback_active_runtime_package(self):
        self._workspace_pending_local_package_id = ""
        self._start_workspace_package_runtime_action("rollback", "/rollback", {})

    def _on_workspace_package_action_result(self, kind, payload):
        self._set_workspace_package_action_busy(kind, False)
        self._workspace_pending_local_package_id = ""
        if kind == "activate":
            package = payload.get("package") if isinstance(payload.get("package"), dict) else {}
            self.notify_success(f"Runtime package activated: {package.get('package_id') or 'selected package'}")
        elif kind == "rollback":
            package = payload.get("package") if isinstance(payload.get("package"), dict) else {}
            self.notify_success(f"Runtime rolled back to: {package.get('package_id') or 'previous package'}")
        self._refresh_runtime_package_inventory()
        self._refresh_camera_runtime_card(force=True)

    def _on_workspace_package_action_error(self, kind, message):
        self._set_workspace_package_action_busy(kind, False)
        if self._runtime_is_local():
            try:
                if kind == "activate":
                    package_id = getattr(self, "_workspace_pending_local_package_id", "") or self._selected_runtime_package_id()
                    activate_local_model_package(package_id)
                    self.notify_success(f"Package activated in local regis{package_id}")
                elif kind == "rollback":
                    package = rollback_local_model_package()
                    self.notify_success(f"Rolled back in local regis{package.get('package_id')}")
                self._workspace_pending_local_package_id = ""
                self._refresh_runtime_package_inventory()
                self.notify_info("Runtime is offline; activation will be visible after the service reloads.")
                return
            except Exception:
                pass
        self._workspace_pending_local_package_id = ""
        self.notify_error(f"Runtime {kind} failed: {message}")
        self._refresh_runtime_package_inventory()

    def _set_runtime_badge(self, label, text, tone="neutral"):
        if label is None:
            return
        palette = {
            "success": ("#30D158", "rgba(48, 209, 88, 0.14)", "#30D158"),
            "warning": ("#FF9F0A", "rgba(255, 159, 10, 0.14)", "#FF9F0A"),
            "danger": ("#FF453A", "rgba(255, 69, 58, 0.16)", "#FF453A"),
            "info": ("#3B82F6", "rgba(59, 130, 246, 0.16)", "#3B82F6"),
            "neutral": ("#A1A1AA", "rgba(161, 161, 170, 0.12)", "#4B5563"),
        }
        fg, bg, border = palette.get(tone, palette["neutral"])
        label.setText(text)
        label.setStyleSheet(
            "font-size: 12px; font-weight: 800; "
            f"color: {fg}; background: {bg}; border: 1px solid {border}; "
            "border-radius: 12px; padding: 6px 12px;"
        )

    def _set_runtime_metric_value(self, label, text, tone="default"):
        if label is None:
            return
        pal = ThemeOps.palette(getattr(self, "is_dark", True))
        colors = {
            "default": pal["txt"],
            "success": "#30D158",
            "warning": "#FF9F0A",
            "danger": "#FF453A",
            "info": "#76A8FA" if getattr(self, "is_dark", True) else "#2E6FDE",
            "muted": pal["sub"],
        }
        label.setText(text)
        if getattr(label, "objectName", lambda: "")() != "RuntimeMetricValue":
            label.setObjectName("RuntimeMetricValue")
        label.setStyleSheet(
            "font-size: 14px; font-weight: 700; border: none; background: transparent; "
            f"color: {colors.get(tone, colors['default'])};"
        )

    def _set_status_pill(self, label, text, tone="neutral"):
        if label is None:
            return
        label.setText(text)
        label.setObjectName("StatusPill")
        label.setProperty("stateTone", tone)
        style = label.style()
        if style is not None:
            style.unpolish(label)
            style.polish(label)
        label.update()

    def _format_runtime_timestamp(self, value):
        """Format an ISO-8601 timestamp for compact display (shared by camera and results panels)."""
        raw = str(value or "").strip()
        if not raw:
            return "—"
        try:
            return datetime.fromisoformat(raw).strftime("%d %b %H:%M:%S")
        except Exception:
            return raw.replace("T", " ")

    # Alias used by the Results page (same behaviour, single implementation)
    _format_result_timestamp = _format_runtime_timestamp

    def _camera_source_kind(self, src):
        text = str(src or "").strip().lower()
        if text.startswith("rtsp://"):
            return "RTSP stream"
        if text.startswith("http://") or text.startswith("https://"):
            return "HTTP stream"
        if text.startswith("tcp://"):
            return "TCP stream"
        if text.startswith("docker://"):
            return "Workspace pipeline"
        if text.isdigit():
            return "Local device"
        return "Camera source"

    def _camera_preview_summary_text(self):
        try:
            cameras = self.db.get_cameras() if hasattr(self, "db") else []
        except Exception:
            cameras = []
        if not cameras:
            return "No camera source configured"
        first_name, first_src, _ = cameras[0]
        summary = f"{first_name} · {self._camera_source_kind(first_src)}"
        if len(cameras) > 1:
            summary += f" + {len(cameras) - 1} more"
        return summary

    def _refresh_camera_preview_summary(self):
        if not hasattr(self, "_camera_runtime_preview_value"):
            return
        state_preview = ((getattr(self, "_inspection_runtime_state", None) or {}).get("preview_source") or "").strip()
        summary = self._camera_preview_summary_text()
        if state_preview and state_preview.lower() != "not configured":
            text = f"{state_preview}\nStudio: {summary}"
        else:
            text = summary
        self._set_runtime_metric_value(self._camera_runtime_preview_value, text, "default" if "No camera" not in text else "muted")

    def _inspection_runtime_profile(self):
        return load_inspection_profile()

    def _inspection_runtime_url(self, path="/state"):
        profile = self._inspection_runtime_profile()
        runtime_cfg = profile.get("runtime") or {}
        host = normalize_runtime_host(runtime_cfg.get("host") or "")
        if not host:
            host = (getattr(self, "node_ip", None) and self.node_ip.text().strip()) or "127.0.0.1"
        host = format_runtime_host_for_url(host or "127.0.0.1")
        try:
            port = int(runtime_cfg.get("port") or 8787)
        except (TypeError, ValueError):
            port = 8787
        route = path if str(path or "").startswith("/") else f"/{path}"
        return f"http://{host}:{port}{route}"

    def _safe_refresh_camera_runtime(self):
        """Timer callback that is safe to call before the Cameras page is built.

        When called at startup the camera card widgets do not exist yet;
        ``_refresh_camera_runtime_card`` guards against this with the
        ``hasattr`` check, but we also skip spawning the HTTP thread when the
        runtime URL can't be determined (e.g. no profile loaded yet).
        """
        try:
            url = self._inspection_runtime_url("/state")
        except Exception:
            return
        if not url:
            return
        self._refresh_camera_runtime_card()

    def _refresh_camera_runtime_card(self, force=False):
        self._refresh_camera_preview_summary()
        if not hasattr(self, "_camera_runtime_status_badge"):
            return
        try:
            self._camera_runtime_endpoint.setText(self._inspection_runtime_url("/state"))
        except (RuntimeError, AttributeError):
            return
        if getattr(self, "_inspection_runtime_thread", None) and self._inspection_runtime_thread.isRunning():
            return
        self._inspection_runtime_thread = InspectionRuntimeRequestThread(
            self._inspection_runtime_url("/state"),
            method="GET",
            timeout=3.5,
        )
        self._inspection_runtime_thread.result_signal.connect(self._on_camera_runtime_state)
        self._inspection_runtime_thread.error_signal.connect(self._on_camera_runtime_error)
        self._inspection_runtime_thread.start()

    def _update_camera_runtime_widgets(self, state=None, error=None):
        try:
            profile = self._inspection_runtime_profile()
            current = state or getattr(self, "_inspection_runtime_state", {}) or {}
            camera_name = current.get("camera_name") or profile.get("camera_name") or "VisionDock Runtime Node"
            if hasattr(self, "_camera_runtime_name"):
                self._camera_runtime_name.setText(camera_name)
            if hasattr(self, "_camera_runtime_endpoint"):
                self._camera_runtime_endpoint.setText(self._inspection_runtime_url("/state"))

            if error:
                self._set_runtime_badge(self._camera_runtime_status_badge, "OFFLINE", "danger")
                self._camera_runtime_detail.setText(f"Runtime unreachable: {error}")
                self._set_runtime_metric_value(self._camera_runtime_model_value, "No response", "muted")
                self._set_runtime_metric_value(self._camera_runtime_decision_value, "—", "muted")
                self._set_runtime_metric_value(self._camera_runtime_trigger_value, (profile.get("trigger_mode") or "manual").replace("_", " ").title(), "default")
                backend = profile.get("output_backend") or "mock"
                gpio_enabled = bool((profile.get("gpio") or {}).get("enabled"))
                self._set_runtime_metric_value(self._camera_runtime_gpio_value, f"{backend}\nGPIO {'enabled' if gpio_enabled else 'disabled'}", "muted")
                self._set_runtime_metric_value(self._camera_runtime_count_value, "—", "muted")
                self._set_runtime_metric_value(self._camera_runtime_last_trigger_value, "—", "muted")
                self._refresh_inspection_runtime_sensor_hint()
                self._refresh_camera_preview_summary()
                return

            runtime_status = (current.get("runtime_status") or "idle").strip().lower()
            tone = {"idle": "success", "ready": "success", "busy": "warning", "fault": "danger", "starting": "info"}.get(runtime_status, "neutral")
            badge_text = {"idle": "READY", "busy": "BUSY", "fault": "FAULT", "starting": "STARTING"}.get(runtime_status, (runtime_status or "unknown").upper())
            self._set_runtime_badge(self._camera_runtime_status_badge, badge_text, tone)
            configured_sensor = str(current.get("camera_sensor_model") or ((profile.get("camera") or {}).get("sensor_model") or "GENERIC_CSI")).strip().upper() or "GENERIC_CSI"
            sensor_text = csi_sensor_label(configured_sensor)
            detected_sensor = str(current.get("detected_sensor_model") or "").strip().upper()
            sensor_match = current.get("camera_sensor_match")
            camera_backend = current.get("camera_backend") or ((profile.get("camera") or {}).get("backend") or "mock_frame")
            model_adapter = current.get("model_adapter") or "unconfigured"
            recipe_name = str((profile.get("inspection") or {}).get("recipe_name") or "default_recipe")
            detail_parts = [sensor_text, str(camera_backend).replace("_", " "), f"adapter {model_adapter}", recipe_name]
            if detected_sensor:
                if sensor_match is False:
                    detail_parts.append(f"detected {detected_sensor} (mismatch)")
                else:
                    detail_parts.append(f"detected {detected_sensor}")
            self._camera_runtime_detail.setText(" · ".join(detail_parts))
            self._refresh_inspection_runtime_sensor_hint()

            active_model = current.get("active_model") if isinstance(current.get("active_model"), dict) else {}
            model_name = active_model.get("name") or "No model deployed"
            model_version = active_model.get("version") or "unassigned"
            self._set_runtime_metric_value(self._camera_runtime_model_value, f"{model_name}\n{model_version}", "default" if model_version != "unassigned" else "muted")

            last_result = current.get("last_result") if isinstance(current.get("last_result"), dict) else {}
            inspection_cfg = profile.get("inspection") or {}
            decision = (last_result.get("decision") or "").strip().lower()
            defects = last_result.get("defect_classes") or []
            duration_ms = last_result.get("duration_ms")
            decision_tone = {"pass": "success", "fail": "danger", "uncertain": "warning", "fault": "danger"}.get(decision, "muted")
            decision_text = decision.upper() if decision else "No inspection yet"
            if defects: decision_text += f"\\n{', '.join(str(x) for x in defects[:2])}"
            elif decision == "pass": decision_text += f"\\n{inspection_cfg.get('pass_display_label') or 'Pass'}"
            elif decision == "fail": decision_text += f"\\n{inspection_cfg.get('fail_display_label') or 'Fail'}"
            elif decision == "uncertain": decision_text += f"\\n{inspection_cfg.get('review_display_label') or 'Review required'}"
            if duration_ms: decision_text += f" · {duration_ms} ms"
            self._set_runtime_metric_value(self._camera_runtime_decision_value, decision_text, decision_tone)

            trigger_mode = (current.get("trigger_mode") or profile.get("trigger_mode") or "manual").replace("_", " ").title()
            self._set_runtime_metric_value(self._camera_runtime_trigger_value, trigger_mode, "default")

            effective_backend = current.get("effective_output_backend") or current.get("output_backend") or profile.get("output_backend") or "mock"
            gpio_enabled = bool(current.get("gpio_enabled"))
            self._set_runtime_metric_value(self._camera_runtime_gpio_value, f"{effective_backend}\nGPIO {'enabled' if gpio_enabled else 'disabled'}", "default" if gpio_enabled else "muted")

            count_val = str(current.get("inspection_count")) if current.get("inspection_count") is not None else "0"
            self._set_runtime_metric_value(self._camera_runtime_count_value, count_val, "info")
            self._set_runtime_metric_value(self._camera_runtime_last_trigger_value, self._format_runtime_timestamp(current.get("last_trigger_at")), "default")
            self._refresh_camera_preview_summary()
        except Exception:
            pass
    def _on_camera_runtime_state(self, payload):
        state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
        if not isinstance(state, dict):
            self._on_camera_runtime_error("Runtime returned an invalid state payload.")
            return
        was_online = self._inspection_runtime_online
        self._inspection_runtime_online = True
        self._inspection_runtime_last_error = ""
        self._inspection_runtime_state = state
        self._update_camera_runtime_widgets(state=state)
        if was_online is False:
            self.notify_success("Inspection runtime reachable again.")

    def _on_camera_runtime_error(self, message):
        was_online = self._inspection_runtime_online
        self._inspection_runtime_online = False
        self._inspection_runtime_last_error = str(message or "").strip() or "Unknown runtime error."
        self._update_camera_runtime_widgets(error=self._inspection_runtime_last_error)
        if was_online is True:
            self.notify_error(f"Inspection runtime unreachable: {self._inspection_runtime_last_error}")

    def _set_camera_action_busy(self, kind, busy):
        if kind == "trigger" and getattr(self, "_camera_runtime_trigger_btn", None):
            self._camera_runtime_trigger_btn.setEnabled(not busy)
            self._camera_runtime_trigger_btn.setText("Triggering…" if busy else "Manual Trigger")
        if kind == "reload" and getattr(self, "_camera_runtime_reload_btn", None):
            self._camera_runtime_reload_btn.setEnabled(not busy)
            self._camera_runtime_reload_btn.setText("Reloading…" if busy else "Reload Profile")

    def _start_camera_runtime_action(self, kind, path, payload=None, timeout=4.0):
        if getattr(self, "_inspection_runtime_action_thread", None) and self._inspection_runtime_action_thread.isRunning():
            return False
        self._set_camera_action_busy(kind, True)
        self._inspection_runtime_action_thread = InspectionRuntimeRequestThread(
            self._inspection_runtime_url(path),
            method="POST",
            payload=payload or {},
            timeout=timeout,
        )
        self._inspection_runtime_action_thread.result_signal.connect(lambda data, k=kind: self._on_camera_runtime_action_result(k, data))
        self._inspection_runtime_action_thread.error_signal.connect(lambda msg, k=kind: self._on_camera_runtime_action_error(k, msg))
        self._inspection_runtime_action_thread.start()
        return True

    def _on_camera_runtime_action_result(self, kind, payload):
        self._set_camera_action_busy(kind, False)
        state = payload.get("state") if isinstance(payload.get("state"), dict) else None
        if isinstance(state, dict):
            self._on_camera_runtime_state(state)
        else:
            self._refresh_camera_runtime_card(force=True)
        if kind == "trigger":
            if hasattr(self, "refresh_library"):
                self.refresh_library()
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            decision = (result.get("decision") or "unknown").upper()
            self.notify_success(f"Inspection trigger completed: {decision}")
        elif kind == "reload":
            self.notify_success("Inspection runtime profile reloaded.")

    def _on_camera_runtime_action_error(self, kind, message):
        self._set_camera_action_busy(kind, False)
        self.notify_error(f"Runtime {kind} failed: {message}")
        self._refresh_camera_runtime_card(force=True)

    def _trigger_inspection_runtime(self):
        self._start_camera_runtime_action(
            "trigger",
            "/trigger",
            payload={"source": "visiondock_gui"},
            timeout=12.0,
        )

    def _reload_inspection_runtime_profile(self):
        self._start_camera_runtime_action(
            "reload",
            "/reload",
            payload={},
            timeout=4.0,
        )
    
    def toggle_eco(self, c):
        self.eco_mode = c
        self.notify_info(t("notif.thermal_guard_on", "Thermal guard enabled.") if c else t("notif.thermal_guard_off", "Thermal guard disabled."))
        
    def _record_notification(self, level: str, message: str):
        if not hasattr(self, "_notifications"):
            self._notifications = []
        msg = str(message or "").strip()
        lvl = (level or "info").lower()
        now = datetime.now()
        now_s = now.strftime("%Y-%m-%d %H:%M:%S")
        # Aynı mesaj kısa aralıkta tekrar geldiyse tek kayıtta biriktir.
        if self._notifications:
            top = self._notifications[0]
            if top.get("level") == lvl and top.get("message") == msg:
                try:
                    prev_ts = datetime.strptime(top.get("ts") or "", "%Y-%m-%d %H:%M:%S")
                except Exception:
                    prev_ts = None
                if prev_ts is None or (now - prev_ts).total_seconds() <= 90:
                    top["count"] = int(top.get("count") or 1) + 1
                    top["ts"] = now_s
                    if hasattr(self, "_notif_list_box"):
                        self._refresh_notifications_panel()
                    return
        row = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": lvl,
            "message": msg,
            "count": 1,
        }
        self._notifications.insert(0, row)
        if len(self._notifications) > 120:
            self._notifications = self._notifications[:120]
        if hasattr(self, "_notif_list_box"):
            self._refresh_notifications_panel()

    def _show_toast_typed(self, txt, level="info", dedupe_window_ms=None):
        txt = str(txt or "").strip()
        lvl = (level or "info").lower()
        if not txt:
            return
        if not hasattr(self, "_toast_last_by_key"):
            self._toast_last_by_key = {}
        if dedupe_window_ms is None:
            dedupe_window_ms = 2200 if lvl == "info" else (3200 if lvl == "warning" else 0)
        if dedupe_window_ms > 0:
            key = f"{lvl}|{txt}"
            now_ms = int(time.monotonic() * 1000)
            last_ms = int(self._toast_last_by_key.get(key) or 0)
            if now_ms - last_ms < dedupe_window_ms:
                self._record_notification(lvl, txt)
                return
            self._toast_last_by_key[key] = now_ms
        self._record_notification(lvl, txt)
        prefix = {
            "info":    "[i]",
            "success": "[OK]",
            "warning": "[!]",
            "error":   "[ERR]",
        }.get(lvl, "[i]")
        t = Toast(f"{prefix} {txt}", self, self.is_dark)
        t.adjustSize()
        tw = t.sizeHint().width() or 280
        # Bottom-center of the main window, 80px above the bottom edge
        local_x = max(24, (self.width() - tw) // 2)
        local_y = max(24, self.height() - 80 - t.sizeHint().height())
        g = self.mapToGlobal(QPoint(local_x, local_y))
        t.show_msg(g.x(), g.y())

    def show_toast(self, txt):
        self._show_toast_typed(txt, "info")

    def notify_info(self, txt):
        self._show_toast_typed(txt, "info")

    def notify_success(self, txt):
        self._show_toast_typed(txt, "success")

    def notify_warning(self, txt):
        self._show_toast_typed(txt, "warning")

    def notify_error(self, txt):
        self._show_toast_typed(txt, "error")

    def _update_remote_status_label(self, state):
        """state: None=local, True=online, False=offline"""
        self._remote_status_initialized = True
        self._remote_status_state = state
        if not getattr(self, "remote_node_status_label", None):
            return
        if state is None:
            self._set_status_pill(self.remote_node_status_label, "Local", "neutral")
        elif state:
            self._set_status_pill(self.remote_node_status_label, "Online", "success")
        else:
            self._set_status_pill(self.remote_node_status_label, "Offline", "danger")

    def _schedule_remote_status_check(self, force=False):
        if (not force) and (not bool(getattr(self, "_background_health_checks_enabled", False))):
            return
        if not getattr(self, "_remote_status_timer", None):
            self._remote_status_timer = QTimer(self)
            self._remote_status_timer.setSingleShot(True)
            self._remote_status_timer.timeout.connect(self._run_remote_status_check)
        self._remote_status_timer.stop()
        self._remote_status_timer.start(400)

    def _run_remote_status_check(self):
        host = (self.node_ip.text() or "").strip()
        if not host:
            self._update_remote_status_label(None)
            return
        if getattr(self, "_remote_status_thread", None) and self._remote_status_thread.isRunning():
            return
        self._remote_status_thread = RemoteNodeStatusThread(host)
        self._remote_status_thread.status_signal.connect(self._on_remote_node_status)
        self._remote_status_thread.start()

    def _on_remote_node_status(self, online):
        prev = getattr(self, "_remote_was_online", None)
        self._remote_was_online = online
        self._update_remote_status_label(online)
        self._refresh_settings_hub()
        if online is True and prev is not True:
            host = (getattr(self, "remote_host_input", None) and self.remote_host_input.text().strip()) or "remote host"
            self.notify_success(t("notif.remote_online", "Runtime endpoint reachable: {host}", host=host))
        elif online is False and prev is not False:
            host = (getattr(self, "remote_host_input", None) and self.remote_host_input.text().strip()) or "remote host"
            self.notify_error(t("notif.remote_unreachable", "Runtime endpoint unreachable: {host}", host=host))
        host = (self.node_ip.text() or "").strip()
        if host and bool(getattr(self, "_background_health_checks_enabled", False)):
            QTimer.singleShot(25000, self._run_remote_status_check)

    def _on_remote_host_text_changed(self, t):
        DockerManager.set_host(t)
        self._schedule_remote_status_check()
        self._refresh_settings_hub()
        if getattr(self, "_remote_pref_timer", None):
            self._remote_pref_timer.stop()
            self._remote_pref_timer.start(600)

    def _persist_remote_host_pref(self):
        if getattr(self, "node_ip", None):
            save_app_prefs_remote_host(self.node_ip.text() or "")

    def _refresh_docker_status(self):
        """Refresh the Docker engine status label and Start button in Settings."""
        lbl = getattr(self, "_docker_status_lbl", None)
        btn = getattr(self, "_docker_start_btn", None)
        if lbl is None:
            return
        running = DockerManager.is_running()
        prev_docker = getattr(self, "_docker_was_running", None)
        self._docker_was_running = running
        lbl.setText("ACTIVE" if running else "OFFLINE")
        if running and prev_docker is not True:
            self.notify_success(t("notif.docker_active", "Docker service is active."))
        elif not running and prev_docker is not False:
            self.notify_error(t("notif.docker_offline", "Docker service is unavailable."))
        self._set_status_pill(lbl, "ACTIVE" if running else "OFFLINE", "success" if running else "danger")
        if btn is not None:
            btn.setVisible(not running)
        self._refresh_settings_hub()

    def run_health_check(self):
        results = []
        results.append(f"Docker: {'ONLINE' if DockerManager.is_running() else 'OFFLINE'}")
        results.append(f"Camera indices: {list_cameras()}")
        results.append(f"Platform: {platform.system()} {platform.machine()}")
        mb = QMessageBox(self)
        mb.setWindowTitle("System health report")
        mb.setText("\n".join(results))
        mb.setStyleSheet("QLabel{ min-width: 320px; max-width: 520px; }")
        for lbl in mb.findChildren(QLabel):
            lbl.setWordWrap(True)
        mb.exec()

    def switch(self, i):
        self.tabs.setCurrentIndex(i)
        [btn.setChecked(idx == i) for idx, btn in enumerate(self.navs)]
        if i == 0:  # Home tab: refresh dashboard stats
            QTimer.singleShot(0, self.refresh_home_page)
        if i == 1:  # Cameras tab: refresh runtime status
            QTimer.singleShot(0, self._refresh_camera_runtime_card)
        if i == 2:  # Workspaces tab: refresh package manager state
            QTimer.singleShot(0, getattr(self, "_refresh_workspace_package_panel", lambda: None))
        if i == 3:  # Devices tab: refresh peer list
            QTimer.singleShot(0, getattr(self, "refresh_devices_page", lambda: None))
        if i == 4:  # Results tab: refresh inspection history
            QTimer.singleShot(0, getattr(self, "refresh_library", lambda: None))
        if i == 5:  # Settings tab: refresh remote node status + Docker status
            if bool(getattr(self, "_check_remote_on_settings_open", False)):
                self._schedule_remote_status_check(force=True)
            QTimer.singleShot(0, self._refresh_docker_status)
            QTimer.singleShot(0, self._refresh_settings_hub)
            QTimer.singleShot(120, self._show_operator_quick_tour_once)
    def upd_stats(self, d): 
        for i, k in enumerate(['cpu','ram','disk','gpu']): self.charts[i].set_value(d[k])
    def toggle_theme(self, c): self.is_dark = c; self.apply_theme()
    def _zt_join_network(self):
        nwid = (self._zt_nwid_input.text().strip() if hasattr(self, "_zt_nwid_input") else "")
        if len(nwid) != 16 or not re.fullmatch(r"[0-9a-fA-F]{16}", nwid):
            self.notify_warning("Please enter a valid 16-char ZeroTier Network ID")
            return
        ok, out = run_cmd(["zerotier-cli", "join", nwid], timeout=12)
        if ok:
            self.notify_success(f"Join requested: {nwid}")
            self.refresh_settings_page()
            self.refresh_home_page()
            self.refresh_devices_page(force=True)
        else:
            self.notify_error(f"Join failed: {out[:180] if out else 'unknown error'}")

    def _zt_leave_network(self):
        nwid = (self._zt_nwid_input.text().strip() if hasattr(self, "_zt_nwid_input") else "")
        if len(nwid) != 16 or not re.fullmatch(r"[0-9a-fA-F]{16}", nwid):
            self.notify_warning("Enter the 16-char Network ID to leave")
            return
        ok, out = run_cmd(["zerotier-cli", "leave", nwid], timeout=12)
        if ok:
            self.notify_info(f"Leave requested: {nwid}")
            self.refresh_settings_page()
            self.refresh_home_page()
            self.refresh_devices_page(force=True)
        else:
            self.notify_error(f"Leave failed: {out[:180] if out else 'unknown error'}")

    def _refresh_notifications_panel(self):
        box = getattr(self, "_notif_list_box", None)
        if box is None: return
        while box.count():
            it = box.takeAt(0); w = it.widget()
            if w: w.deleteLater()
        all_rows = list(getattr(self, "_notifications", []) or [])
        rows = all_rows[:6]
        summary = getattr(self, "_notif_summary_label", None)
        if summary is not None:
            if not all_rows:
                summary.setText(t("notif.none_active", "No active notifications."))
            else:
                newest = all_rows[0]
                count = len(all_rows)
                key = "notif.stored_single" if count == 1 else "notif.stored_multi"
                summary.setText(t(key, "{count} notifications stored. Latest: [{ts}] {message}", count=count, ts=newest.get("ts", "—"), message=newest.get("message", "")))
        if not rows:
            lbl = QLabel(t("notif.system_normal", "System is normal. No notifications.")); lbl.setObjectName("CaptionMuted"); box.addWidget(lbl)
            self._refresh_settings_hub()
            return
        for n in rows:
            lv, ts, msg = n.get("level", "info").lower(), n.get("ts", "—"), n.get("message", "")
            cnt = int(n.get("count") or 1)
            if cnt > 1:
                msg = f"{msg} (x{cnt})"
            color = {"success": "#30D158", "error": "#FF453A", "warning": "#FF9F0A"}.get(lv, "#888888")
            
            row = QFrame(); row.setStyleSheet(f"background:rgba(255,255,255,0.03); border-radius:6px; margin-bottom:2px;")
            rl = QHBoxLayout(row); rl.setContentsMargins(12,8,12,8); rl.setSpacing(10)
            
            dot = QLabel("●"); dot.setStyleSheet(f"color: {color}; font-size: 10px;")
            txt = QLabel(f"[{ts}] {msg}"); txt.setStyleSheet("color: #BBBBBB; font-family: 'SF Mono', monospace; font-size: 11px;")
            txt.setWordWrap(True)
            
            rl.addWidget(dot); rl.addWidget(txt, 1); box.addWidget(row)
        self._refresh_settings_hub()

    def _collect_settings_bundle(self):
        prefs = load_app_prefs()
        cam_defaults = get_camera_defaults()
        inspection_profile = load_inspection_profile()
        return {
            "version": 1,
            "exported_at": datetime.now().isoformat(),
            "app_prefs": prefs,
            "camera_defaults": cam_defaults,
            "inspection_profile": inspection_profile,
            "ui_state": {
                "is_dark": bool(getattr(self, "is_dark", True)),
                "sidebar_compact": bool(getattr(self, "_sidebar_compact", True)),
                "background_health_checks_enabled": bool(getattr(self, "_background_health_checks_enabled", False)),
                "auto_camera_preview_on_launch": bool(getattr(self, "_auto_camera_preview_on_launch", False)),
                "check_remote_on_settings_open": bool(getattr(self, "_check_remote_on_settings_open", False)),
                "scheduler_policy": str(getattr(self, "_scheduler_policy", "manual")),
                "ui_role_mode": str(getattr(self, "_ui_role_mode", "operator")),
                "confirm_engineering_mode_switch": bool(getattr(self, "_confirm_engineering_mode_switch", True)),
                "operator_quick_tour_seen": bool(getattr(self, "_operator_quick_tour_seen", False)),
                "devices_auto_refresh_enabled": bool(getattr(self, "_devices_auto_refresh_enabled", False)),
                "show_setup_wizard_on_launch": bool(getattr(self, "_show_setup_wizard_on_launch", False)),
            },
        }

    def _apply_settings_bundle(self, data: dict):
        if not isinstance(data, dict):
            raise ValueError("Invalid settings file: root object must be JSON object.")
        app_prefs = data.get("app_prefs")
        cam_defaults = data.get("camera_defaults")
        inspection_profile = data.get("inspection_profile")
        ui_state = data.get("ui_state", {})
        if not isinstance(app_prefs, dict):
            raise ValueError("Invalid settings file: app_prefs is missing or invalid.")
        if not isinstance(cam_defaults, dict):
            raise ValueError("Invalid settings file: camera_defaults is missing or invalid.")
        if inspection_profile is not None and not isinstance(inspection_profile, dict):
            raise ValueError("Invalid settings file: inspection_profile is invalid.")
        with open(_app_prefs_path(), "w", encoding="utf-8") as f:
            json.dump(app_prefs, f, indent=2)
        current = get_camera_defaults()
        for k in list(current.keys()):
            if k in cam_defaults:
                current[k] = cam_defaults[k]
        save_camera_defaults_to_disk()
        if inspection_profile is not None:
            merged_profile = _deep_merge_dicts(default_inspection_profile(), inspection_profile)
            save_inspection_profile(merged_profile)
        if "is_dark" in ui_state:
            self.is_dark = bool(ui_state.get("is_dark"))
        if "sidebar_compact" in ui_state:
            self._sidebar_compact = bool(ui_state.get("sidebar_compact"))
            save_app_prefs_sidebar_compact(self._sidebar_compact)
        if "background_health_checks_enabled" in ui_state:
            self._background_health_checks_enabled = bool(ui_state.get("background_health_checks_enabled"))
            save_app_prefs_flag("background_health_checks_enabled", self._background_health_checks_enabled)
        if "auto_camera_preview_on_launch" in ui_state:
            self._auto_camera_preview_on_launch = bool(ui_state.get("auto_camera_preview_on_launch"))
            save_app_prefs_flag("auto_camera_preview_on_launch", self._auto_camera_preview_on_launch)
        if "check_remote_on_settings_open" in ui_state:
            self._check_remote_on_settings_open = bool(ui_state.get("check_remote_on_settings_open"))
            save_app_prefs_flag("check_remote_on_settings_open", self._check_remote_on_settings_open)
        if "scheduler_policy" in ui_state:
            self._scheduler_policy = str(ui_state.get("scheduler_policy") or "manual").strip().lower()
            if self._scheduler_policy not in ("manual", "balanced", "full", "custom"):
                self._scheduler_policy = "manual"
            save_app_prefs_value("scheduler_policy", self._scheduler_policy)
        if "ui_role_mode" in ui_state:
            self._ui_role_mode = str(ui_state.get("ui_role_mode") or "operator").strip().lower()
            if self._ui_role_mode not in ("operator", "engineering"):
                self._ui_role_mode = "operator"
            save_app_prefs_value("ui_role_mode", self._ui_role_mode)
        if "confirm_engineering_mode_switch" in ui_state:
            self._confirm_engineering_mode_switch = bool(ui_state.get("confirm_engineering_mode_switch"))
            save_app_prefs_flag("confirm_engineering_mode_switch", self._confirm_engineering_mode_switch)
        if "operator_quick_tour_seen" in ui_state:
            self._operator_quick_tour_seen = bool(ui_state.get("operator_quick_tour_seen"))
            save_app_prefs_flag("operator_quick_tour_seen", self._operator_quick_tour_seen)
        if "devices_auto_refresh_enabled" in ui_state:
            self._devices_auto_refresh_enabled = bool(ui_state.get("devices_auto_refresh_enabled"))
            save_app_prefs_flag("devices_auto_refresh_enabled", self._devices_auto_refresh_enabled)
        if "show_setup_wizard_on_launch" in ui_state:
            self._show_setup_wizard_on_launch = bool(ui_state.get("show_setup_wizard_on_launch"))
            save_app_prefs_flag("show_setup_wizard_on_launch", self._show_setup_wizard_on_launch)
        self.apply_theme()
        if self._scheduler_policy in ("manual", "balanced", "full"):
            self._apply_scheduler_policy(self._scheduler_policy, persist=False, notify=False)
        self._apply_background_health_checks_policy(run_initial_check=self._background_health_checks_enabled)
        self._apply_devices_auto_refresh_policy()
        self._apply_settings_role_mode()
        self.refresh_home_page()
        self.refresh_devices_page(force=True)
        try:
            if hasattr(self, "node_ip"):
                new_ip = (load_app_prefs().get("remote_host_ip") or "").strip()
                self.node_ip.setText(new_ip)
                DockerManager.set_host(new_ip if new_ip else None)
        except Exception:
            pass
        self._sync_inspection_profile_ui()

    def export_settings_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export VisionDock settings",
            os.path.join(os.path.expanduser("~"), "visiondock-settings.json"),
            "JSON Files (*.json)",
        )
        if not path:
            return
        try:
            data = self._collect_settings_bundle()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.notify_success(f"Settings exported: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "Export settings", f"Export failed:\n{e}")

    def import_settings_json(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import VisionDock settings",
            os.path.expanduser("~"),
            "JSON Files (*.json)",
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._apply_settings_bundle(data)
            self.notify_success("Settings imported successfully.")
        except Exception as e:
            QMessageBox.warning(self, "Import settings", f"Import failed:\n{e}")
            self.notify_error("Settings import failed")

    def _show_splash_screen(self):
        app = QApplication.instance()
        if app is None:
            return None
        pm = QPixmap(560, 300)
        pm.fill(QColor("#0B0F17" if self.is_dark else "#EEF3FB"))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        logo = QPixmap(resource_path("visiondock.svg"))
        if not logo.isNull():
            lp = logo.scaled(86, 86, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(32, 34, lp)
        p.setPen(QColor("#EAF0FA" if self.is_dark else "#182338"))
        f1 = QFont()
        f1.setPointSize(26)
        f1.setWeight(QFont.Weight.Bold)
        p.setFont(f1)
        p.drawText(134, 82, "VisionDock")
        p.setPen(QColor("#9BA8C0" if self.is_dark else "#61708B"))
        f2 = QFont()
        f2.setPointSize(11)
        f2.setWeight(QFont.Weight.Medium)
        p.setFont(f2)
        p.drawText(136, 112, "Industrial Vision Platform")

        splash = QSplashScreen(pm)
        splash.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        splash.show()
        app.processEvents()
        QTimer.singleShot(1800, splash.close)
        return splash

    # ── A2: Keyboard shortcuts ─────────────────────────────────────────────
    def _setup_shortcuts(self):
        """Register global keyboard shortcuts."""
        from PySide6.QtGui import QKeySequence
        _map = {
            "Ctrl+,":  lambda: self.switch(5),   # Settings
            "Ctrl+1":  lambda: self.switch(0),   # Home
            "Ctrl+2":  lambda: self.switch(1),   # Cameras
            "Ctrl+3":  lambda: self.switch(2),   # Workspaces
            "Ctrl+4":  lambda: self.switch(3),   # Devices
            "Ctrl+5":  lambda: self.switch(4),   # Results
            "Ctrl+6":  lambda: self.switch(5),   # Settings
            "Ctrl+R":  self.refresh_home_page,
            "Ctrl+K":  self.show_toast if hasattr(self, "show_toast") else lambda: None,
        }
        for seq, fn in _map.items():
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)

    def refresh_home_page(self):
        """Re-scan current active view based on tab index"""
        idx = self.tabs.currentIndex()
        if idx == 0: pass # Dashboard is static
        elif idx == 1: self.refresh_cams()
        elif idx == 2: self.check_docker()
        elif idx == 3: self.refresh_devices_page()
        elif idx == 4: self.refresh_library()
        elif idx == 5: pass # Settings

    # ── C8: Update check ───────────────────────────────────────────────────
    def _check_for_updates(self):
        """Non-blocking update check against GitHub releases API."""
        def _worker():
            try:
                url = "https://api.github.com/repos/visiondock/visiondock/releases/latest"
                req = urllib.request.Request(url, headers={"User-Agent": f"VisionDock/{APP_VERSION}"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                tag = (data.get("tag_name") or "").lstrip("v")
                if tag and tag != APP_VERSION:
                    QTimer.singleShot(0, lambda: self.notify_info(f"Update available: v{tag}  (current: v{APP_VERSION})"))
            except Exception:
                pass  # silent — no network or repo not public yet
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    # ── C9: Log viewer ─────────────────────────────────────────────────────
    def _refresh_log_viewer(self):
        """Reload last 200 lines of the VisionDock log file into the log viewer widget."""
        widget = getattr(self, "_log_viewer_widget", None)
        if widget is None:
            return
        log_dir = os.environ.get("VISIONDOCK_LOG_DIR") or os.path.join(os.path.expanduser("~"), ".visiondock")
        log_file = os.environ.get("VISIONDOCK_LOG_PATH") or os.path.join(log_dir, "visiondock.log")
        try:
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                text = "".join(lines[-200:])
            else:
                text = "(Log file not found)"
        except Exception as e:
            text = f"(Error reading log: {e})"
        widget.setPlainText(text)
        # Scroll to bottom
        cursor = widget.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        widget.setTextCursor(cursor)

    # ── C10: Docker container health ───────────────────────────────────────
    def _refresh_docker_containers(self):
        """Refresh Docker container health table in Settings."""
        box = getattr(self, "_docker_containers_box", None)
        if box is None:
            return
        # Clear existing rows
        while box.count():
            item = box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            containers = DockerManager.list_containers()
        except Exception:
            containers = []
        if not containers:
            lbl = QLabel("No containers found (Docker may be offline or no containers running)")
            lbl.setObjectName("CaptionMuted")
            lbl.setWordWrap(True)
            box.addWidget(lbl)
            return
        for c in containers:
            row = QFrame()
            row.setObjectName("SettingsInset")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(12, 8, 12, 8)
            rl.setSpacing(10)
            name_lbl = QLabel(c.get("name") or c.get("id", "?")[:12])
            name_lbl.setObjectName("FormLabel")
            name_lbl.setFixedWidth(180)
            rl.addWidget(name_lbl)
            img_lbl = QLabel(c.get("image", "—"))
            img_lbl.setObjectName("CaptionMuted")
            img_lbl.setFixedWidth(200)
            rl.addWidget(img_lbl)
            status_str = c.get("status", "—")
            status_lbl = QLabel(status_str)
            is_up = "up" in status_str.lower() or "running" in status_str.lower()
            status_lbl.setObjectName("DeviceConnectedBadge" if is_up else "DeviceOfflineBadge")
            rl.addWidget(status_lbl)
            rl.addStretch(1)
            box.addWidget(row)

    # ── D11: RTSP URL builder ──────────────────────────────────────────────
    def _show_rtsp_builder(self):
        """Show a dialog to build an RTSP URL from components."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Build RTSP URL")
        dlg.setMinimumWidth(480)
        l = QVBoxLayout(dlg)
        l.setSpacing(12)
        l.setContentsMargins(20, 20, 20, 20)

        title = QLabel("RTSP URL Builder")
        title.setObjectName("SectionTitle")
        l.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)

        host_edit = QLineEdit()
        host_edit.setPlaceholderText("10.x.x.x  (ZeroTier IP)")
        prefs = load_app_prefs()
        host_edit.setText(prefs.get("remote_host", ""))
        form.addRow("Host / IP:", host_edit)

        port_edit = QLineEdit("554")
        port_edit.setPlaceholderText("554")
        form.addRow("Port:", port_edit)

        path_edit = QLineEdit("/stream")
        path_edit.setPlaceholderText("/stream  or  /cam/realmonitor?channel=1&subtype=0")
        form.addRow("Path:", path_edit)

        user_edit = QLineEdit()
        user_edit.setPlaceholderText("admin  (leave blank if none)")
        form.addRow("Username:", user_edit)

        pass_edit = QLineEdit()
        pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        pass_edit.setPlaceholderText("password  (leave blank if none)")
        form.addRow("Password:", pass_edit)

        l.addLayout(form)

        preview_lbl = QLabel()
        preview_lbl.setObjectName("MonoMuted")
        preview_lbl.setWordWrap(True)
        preview_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        l.addWidget(preview_lbl)

        def _update_preview():
            h = host_edit.text().strip()
            p = port_edit.text().strip() or "554"
            path = path_edit.text().strip() or "/"
            if not path.startswith("/"):
                path = "/" + path
            u = user_edit.text().strip()
            pw = pass_edit.text().strip()
            if u and pw:
                creds = f"{u}:{pw}@"
            elif u:
                creds = f"{u}@"
            else:
                creds = ""
            url = f"rtsp://{creds}{h}:{p}{path}"
            preview_lbl.setText(url)

        for w in (host_edit, port_edit, path_edit, user_edit, pass_edit):
            w.textChanged.connect(_update_preview)
        _update_preview()

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Copy URL")
        copy_btn.setObjectName("PrimaryBtn")
        copy_btn.clicked.connect(lambda: [
            QApplication.clipboard().setText(preview_lbl.text()),
            self.show_toast("RTSP URL copied"),
        ])
        btn_row.addWidget(copy_btn)

        add_btn = QPushButton("Add as Camera")
        add_btn.setObjectName("PrimaryBtn")
        def _add():
            url = preview_lbl.text().strip()
            if url and url != "rtsp://:554/":
                name = f"RTSP {host_edit.text().strip()}"
                self.add_cam_logic(name, url)
                self.notify_success(f"Camera added: {name}")
                dlg.accept()
        add_btn.clicked.connect(_add)
        btn_row.addWidget(add_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("BtnSecondary")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        l.addLayout(btn_row)

        dlg.exec()

    # ── Eski grid sütun tercihi (FlowLayout artık satır sayısı kullanmıyor) ─
    def _set_cam_cols(self, n: int):
        """Uyumluluk: prefs'e yazılır; kamera alanı FlowLayout ile akış düzenindedir."""
        self.cam_grid_cols = n
        prefs = load_app_prefs()
        prefs["cam_grid_cols"] = n
        try:
            prefs_path = os.path.join(os.path.expanduser("~"), ".visiondock", "app_prefs.json")
            with open(prefs_path, "w") as f:
                json.dump(prefs, f, indent=2)
        except Exception:
            pass
        if hasattr(self, "cam_widget") and self.cam_widget is not None:
            self.cam_widget.updateGeometry()

    # ── D12: Snapshot interval ─────────────────────────────────────────────
    def _set_snap_interval(self, src: str, minutes: int):
        """Schedule periodic snapshots for a camera source."""
        if not hasattr(self, "_snap_timers"):
            self._snap_timers = {}
        # Cancel existing timer
        if src in self._snap_timers:
            self._snap_timers[src].stop()
            del self._snap_timers[src]
        if minutes <= 0:
            return
        interval_ms = minutes * 60 * 1000
        t = QTimer(self)
        t.setInterval(interval_ms)
        def _do_snap():
            snap_dir = os.path.join(os.path.expanduser("~"), ".visiondock", "snapshots")
            os.makedirs(snap_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_src = re.sub(r"[^\w]", "_", src)[:40]
            out_path = os.path.join(snap_dir, f"{safe_src}_{ts}.jpg")
            try:
                cap = cv2.VideoCapture(src)
                ret, frame = cap.read()
                cap.release()
                if ret:
                    cv2.imwrite(out_path, frame)
                    self.notify_info(f"Snapshot saved: {os.path.basename(out_path)}")
            except Exception as e:
                log.warning(f"Snapshot failed for {src}: {e}")
        t.timeout.connect(_do_snap)
        t.start()
        self._snap_timers[src] = t
        # Persist interval to prefs
        prefs = load_app_prefs()
        snap_prefs = prefs.get("snap_interval_min", {})
        snap_prefs[src] = minutes
        prefs["snap_interval_min"] = snap_prefs
        try:
            prefs_path = os.path.join(os.path.expanduser("~"), ".visiondock", "app_prefs.json")
            with open(prefs_path, "w") as f:
                json.dump(prefs, f, indent=2)
        except Exception:
            pass

    # ── D13: Camera layout save/load ───────────────────────────────────────
    def _save_cam_layout(self, name: str = "default"):
        """Persist current camera order to app_prefs cam_layouts."""
        if not hasattr(self, "cf") or self.cf is None:
            return
        order = []
        for i in range(self.cf.count()):
            item = self.cf.itemAt(i)
            if item and item.widget():
                w = item.widget()
                src = getattr(w, "_cam_src", None)
                if src:
                    order.append(src)
        prefs = load_app_prefs()
        layouts = prefs.get("cam_layouts", {})
        layouts[name] = order
        prefs["cam_layouts"] = layouts
        try:
            prefs_path = os.path.join(os.path.expanduser("~"), ".visiondock", "app_prefs.json")
            with open(prefs_path, "w") as f:
                json.dump(prefs, f, indent=2)
        except Exception as e:
            self.notify_error(f"Layout save failed: {e}")
            return
        self.notify_success(f"Layout '{name}' saved ({len(order)} cameras)")

    def _load_cam_layout(self, name: str = "default"):
        """Reorder camera cards to match a saved layout."""
        prefs = load_app_prefs()
        layouts = prefs.get("cam_layouts", {})
        order = layouts.get(name)
        if not order:
            self.notify_warning(f"No saved layout named '{name}'")
            return
        if not hasattr(self, "cf") or self.cf is None:
            return
        # Build src→widget map
        src_map = {}
        for i in range(self.cf.count()):
            item = self.cf.itemAt(i)
            if item and item.widget():
                w = item.widget()
                src = getattr(w, "_cam_src", None)
                if src:
                    src_map[src] = w
        # Re-add in saved order
        for src in order:
            if src in src_map:
                self.cf.removeWidget(src_map[src])
                self.cf.addWidget(src_map[src])
        self.notify_success(f"Layout '{name}' loaded")

    def apply_theme(self):
        QApplication.instance().setStyleSheet(ThemeOps.get_style(self.is_dark))
        self._apply_sidebar_mode(refresh_labels=False)
        for ts in self.findChildren(ToggleSwitch):
            ts.update()
        for card in self.findChildren(ResizableCard):
            card._sync_card_shadow()
        for ch in getattr(self, "charts", []):
            ch.update()
        if getattr(self, "_remote_status_initialized", False):
            self._update_remote_status_label(getattr(self, "_remote_status_state", None))
        if hasattr(self, "_camera_runtime_status_badge"):
            if getattr(self, "_inspection_runtime_online", None) is False:
                try:
                    self._update_camera_runtime_widgets(error=getattr(self, "_inspection_runtime_last_error", "") or "Runtime unreachable.")
                except Exception: pass
            else:
                try:
                    self._update_camera_runtime_widgets(state=getattr(self, "_inspection_runtime_state", {}) or None)
                except Exception: pass
    def modal_cam(self): self.show_overlay("Add inspection source", self.add_cam_logic, for_camera=True)
    def modal_doc(self): self.show_overlay("New model workspace", self.add_doc_logic, for_camera=False)
    def refresh_cameras(self):
        """Reload broadcast camera cards from the database."""
        self.active_srcs.clear()
        for i in reversed(range(self.cf.count())):
            w = self.cf.itemAt(i).widget()
            if isinstance(w, ResizableCard):
                if getattr(w, "t", None): w.t.stop()
                w.deleteLater()
        self.cf.removeWidget(self.abc); self.cf.addWidget(self.abc)
        for name, src, meta in self.db.get_cameras():
            self.add_cam_logic(name, src, meta, save=False)
        self._refresh_camera_preview_summary()

    def refresh_ui(self):
        """Reload workspace cards from Docker."""
        if not hasattr(self, "df") or getattr(self, "df", None) is None or not hasattr(self, "abd"):
            return
        self.active_cids.clear()
        for i in reversed(range(self.df.count())):
            w = self.df.itemAt(i).widget()
            if isinstance(w, ResizableCard):
                if getattr(w, "checker", None) and w.checker.isRunning(): w.checker.quit()
                w.deleteLater()
        self.df.removeWidget(self.abd); self.df.addWidget(self.abd); self.check_docker()
        if hasattr(self, "_refresh_workspace_package_panel"):
            self._refresh_workspace_package_panel()
    def check_docker(self):
        """
        Refresh workspace cards from current Docker host.

        Conflict guard:
        - Docker returns 12-char IDs in some commands and 64-char in others.
          We normalize before DB lookup to avoid duplicate/ghost cards.
        """
        if not hasattr(self, "df") or getattr(self, "df", None) is None or not hasattr(self, "abd"):
            return
        if not DockerManager.is_running():
            return
        for c in DockerManager.list_containers():
            cid_raw = c['id']
            cid = self._norm_cid(cid_raw)
            saved = self.db.get_workspace_by_cid(cid) if getattr(self, 'db', None) else None
            name = saved[0] if saved else c['name']
            img = saved[1] if saved else c['image']
            self.add_docker_card(name, img, cid, running=(c['status'].startswith('Up')), save=False)

    def show_delete_confirmation(self, card):
        ov = QWidget(self); ov.setObjectName("Overlay"); ov.resize(self.size())
        l = QVBoxLayout(ov); l.setAlignment(Qt.AlignmentFlag.AlignCenter); box = QFrame(); box.setObjectName("ModalBox"); box.setFixedWidth(420)
        bl = QVBoxLayout(box); bl.setContentsMargins(40, 40, 40, 40); bl.setSpacing(24)
        
        t = QLabel("Delete item?"); t.setAlignment(Qt.AlignmentFlag.AlignCenter); t.setObjectName("ModalTitle")
        s = QLabel("This action is permanent and related data will be lost.")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setObjectName("CaptionMuted")
        s.setWordWrap(True)
        bl.addWidget(t); bl.addWidget(s)
        
        purge_check = None
        if card.is_docker and not getattr(self, "_production_mode", False):
            purge_check = QPushButton("Also remove base image from host")
            purge_check.setObjectName("ModalToggleCheck")
            purge_check.setCheckable(True)
            purge_check.setCursor(Qt.CursorShape.PointingHandCursor)
            bl.addWidget(purge_check)
        
        h = QHBoxLayout()
        c = QPushButton("Cancel")
        c.setObjectName("BtnTextLink")
        d = QPushButton("Delete")
        d.setObjectName("BtnDanger")
        d.setMinimumHeight(42)
        c.clicked.connect(ov.deleteLater)
        d.clicked.connect(lambda: [card.perform_delete(purge_check.isChecked() if purge_check else False), ov.deleteLater()])
        h.addWidget(c); h.addSpacing(20); h.addWidget(d); bl.addLayout(h); l.addWidget(box); ov.show()

    def show_overlay(self, title, cb, for_camera=False):
        ov = QWidget(self); ov.setObjectName("Overlay"); ov.resize(self.size())
        l = QVBoxLayout(ov); l.setAlignment(Qt.AlignmentFlag.AlignCenter); box = QFrame(); box.setObjectName("ModalBox"); box.setFixedWidth(540)
        if for_camera:
            box.setMinimumHeight(620)
        bl = QVBoxLayout(box); bl.setContentsMargins(40, 40, 40, 40); bl.setSpacing(28)
        
        header = QLabel(title); header.setObjectName("ModalTitle"); bl.addWidget(header)
        sub_hdr = QLabel(
            "Pick source, device or URL, engine, and one video resolution (capture + preview)."
            if for_camera
            else "Template or custom image, run target, then confirm."
        )
        sub_hdr.setObjectName("ModalSubtitle")
        sub_hdr.setWordWrap(True)
        bl.addWidget(sub_hdr)
        bl.addSpacing(12)

        is_cam = for_camera
        main_layout = QVBoxLayout(); main_layout.setSpacing(20); main_layout.setContentsMargins(0,0,0,0)
        
        # Monitor Area
        if is_cam:
            mon_frame = QFrame()
            mon_frame.setObjectName("MonPreview")
            mon_frame.setFixedSize(474, 274)
            ml = QVBoxLayout(mon_frame); ml.setContentsMargins(0,0,0,0); ml.setSpacing(0)
            
            pv = QLabel(); pv.setAlignment(Qt.AlignmentFlag.AlignCenter); pv.setStyleSheet("border:none; background: transparent;")
            ml.addWidget(pv, 1); main_layout.addWidget(mon_frame, 0, Qt.AlignmentFlag.AlignCenter)
            
            preview_thread = [None]
            def update_preview_ui(img):
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); h, w, c = rgb.shape
                qimg = QImage(rgb.data, w, h, c * w, QImage.Format.Format_RGB888)
                pm = QPixmap.fromImage(qimg).scaled(
                    466, 266, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                pv.setPixmap(rounded_pixmap(pm, 14))

            def start_preview():
                if preview_thread[0]: preview_thread[0].stop(); pv.setPixmap(QPixmap()); pv.setText("")
                m = mode_combo.currentData()
                src = None
                if m == "Physical": src = cam_combo.currentData()
                elif m == "Stream": src = url_input.text().strip()
                
                if src is not None and src != "":
                    t = VideoThread(src, recording_label="Preview")
                    t.change_pixmap.connect(update_preview_ui)
                    t.start(); preview_thread[0] = t
                else: pv.setText("Waiting for signal...")

        # Form Area (Single Unified Form)
        sett_box = QFrame()
        sett_box.setObjectName("SettingsInset")
        sl = QVBoxLayout(sett_box); sl.setContentsMargins(24, 24, 24, 24); sl.setSpacing(0)
        f = QFormLayout()
        f.setSpacing(20)
        f.setHorizontalSpacing(14)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        
        # Form Area Labels (Explicitly created to prevent "Ghost Labels")
        name_label = QLabel("Name:")
        mode_label = QLabel("Source type:")
        cat_label = QLabel("Template:")
        cam_label = QLabel("Device:")
        url_label = QLabel("URL:")
        ws_label = QLabel("Workspace:")
        exe_label = QLabel("Script:")
        src_label = QLabel("Custom image tag:")
        run_target_label = QLabel("Run target:")
        run_target_label.setToolTip("Run on this machine (Local) or a remote host.")
        eng_label = QLabel("Engine:")
        prof_label = QLabel("Video resolution")
        prof_label.setToolTip(
            "Single setting for this feed: Auto follows Settings → Camera defaults for CSI capture; "
            "720p / 1080p / 4K sets both Jetson pipeline size (when applicable) and preview processing."
        )
        ai_setup_label = QLabel("AI setup:")
        zt_cam_label = QLabel("Quick action:")
        zt_cam_btn = QPushButton("Use active device RTSP")
        zt_cam_btn.setObjectName("ShellBtn")
        zt_cam_btn.setToolTip("Fill the stream URL with the active Jetson device IP.")
        zt_cam_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        # Helper: creates a themed, cross-platform-safe QComboBox
        # On Ubuntu/GTK, CSS height constraints alone are insufficient —
        # setFixedHeight + view().setMaximumHeight() must be set programmatically.
        # Also explicitly setting setView(QListView()) forces Qt to bypass
        # the unstylable native GTK popup rendering on Ubuntu.
        def make_combo():
            c = QComboBox()
            v = QListView()
            c.setView(v)
            c.setFixedHeight(40)
            c.setMaxVisibleItems(8)
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            v.setMaximumHeight(168)
            v.setUniformItemSizes(True)
            v.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
            return c

        # Form Area Widgets
        # Always initialize ALL widgets first to avoid UnboundLocalError in all_elements.
        mode_combo = cam_combo = container_combo = script_input = cat_combo = run_target_combo = None

        if is_cam:
            name_input = QLineEdit(); name_input.setPlaceholderText("Inspection source name (e.g. Front project camera)")
            mode_combo = make_combo()
            mode_combo.addItem("Physical device", "Physical")
            mode_combo.addItem("Network stream", "Stream")
            if not getattr(self, "_production_mode", False):
                mode_combo.addItem("Workspace preview", "Container")
        else:
            name_input = QLineEdit(); name_input.setPlaceholderText("Workspace name (e.g. Jetson defect lab)")
            # Genişletilmiş AI kütüphane kataloğu
            _AI_CATALOG = [
                {"name": "L4T PyTorch 2.0 (Jetson)",       "img": "nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3",  "desc": "PyTorch 2.0 — JetPack 5.x"},
                {"name": "L4T TensorFlow 2 (Jetson)",       "img": "nvcr.io/nvidia/l4t-tensorflow:r35.2.1-tf2-py3",   "desc": "TensorFlow 2 — JetPack 5.x"},
                {"name": "L4T ML (Jetson, integrated)",      "img": "nvcr.io/nvidia/l4t-ml:r35.2.1-py3",               "desc": "PyTorch + TF + scikit-learn"},
                {"name": "Ultralytics YOLOv8 (Jetson)",     "img": "ultralytics/ultralytics:latest-jetson",           "desc": "YOLOv8 — Jetson optimize"},
                {"name": "OpenCV + Python 3 (Jetson)",      "img": "nvcr.io/nvidia/l4t-base:r35.2.1",                  "desc": "Base image processing"},
                {"name": "ONNX Runtime (Jetson GPU)",       "img": "nvcr.io/nvidia/l4t-jetpack:r35.2.1",              "desc": "ONNX Runtime + CUDA"},
                {"name": "ROS 2 Humble (Jetson)",           "img": "dustynv/ros:humble-ros-base-l4t-r35.2.1",         "desc": "ROS 2 + CUDA"},
                {"name": "PyTorch (CPU — Mac/PC test)",     "img": "pytorch/pytorch:latest",                          "desc": "CPU only — no Jetson GPU acceleration"},
                {"name": "Nginx Web (test)",                 "img": "nginx:latest",                                    "desc": "Web server test"},
            ]
            # Katalog + özel katalog birleştir
            _catalog_items = _AI_CATALOG + [i for i in CatalogManager.get_recommended()[0]
                                             if i.get("img") not in {x["img"] for x in _AI_CATALOG}]
            cat_combo = make_combo()
            for _ci in _catalog_items:
                cat_combo.addItem(f"{_ci['name']}  —  {_ci.get('desc','')}", _ci["img"])
            run_target_combo = make_combo(); run_target_combo.addItem("Local (this machine)", "")
            remote_ip = getattr(self, "node_ip", None)
            if remote_ip and getattr(remote_ip, "text", None):
                ip = remote_ip.text().strip()
                if ip:
                    online = check_remote_node_reachable(ip, port=2375, timeout=2)
                    run_target_combo.addItem(
                        f"Remote Jetson ({ip})  •  {'Online' if online else 'Offline'}",
                        ip
                    )
            # Bağlı SSH session'larından da Jetson IP'leri ekle
            for _sk, _sc in getattr(self, "_ssh_sessions", {}).items():
                try:
                    _sh = _sk[0] if isinstance(_sk, tuple) else str(_sk)
                    _zt_ips = ssh_get_zerotier_ips(_sc)
                    _label_ip = _zt_ips[0] if _zt_ips else _sh
                    _already = any(run_target_combo.itemData(i) == _label_ip
                                   for i in range(run_target_combo.count()))
                    if not _already:
                        run_target_combo.addItem(f"SSH Jetson ({_label_ip})", _label_ip)
                except Exception:
                    pass

        # Mode-Specific Fields (only used when is_cam=True)
        cam_combo = make_combo(); [cam_combo.addItem(n, i) for n, i in list_cameras()]
        url_input = QLineEdit()
        _remote_ip = (getattr(self, "node_ip", None) and getattr(self.node_ip, "text", None) and self.node_ip.text().strip()) or ""
        if _remote_ip:
            url_input.setPlaceholderText(f"e.g. rtsp://{_remote_ip}:554/stream (ZeroTier)")
        else:
            url_input.setPlaceholderText("e.g. rtsp://<remote_ip>:554/stream or rtsp://user:pass@host/stream")
        container_combo = make_combo(); containers = DockerManager.list_containers()
        if containers: [container_combo.addItem(f"{c['name']} ({c['image']})", c['id']) for c in containers]
        else: container_combo.addItem("No active containers", None)
        script_input = QLineEdit(); script_input.setPlaceholderText("/path/to/script.py")
        cin = QLineEdit(); cin.setPlaceholderText("Custom image tag (optional)")

        # Intelligence fields (display label vs UserData key stored in DB meta)
        engine_combo = make_combo()
        for _lbl, _data in [
            ("Standard", "Standard"),
            ("YOLOv8", "YOLOv8"),
            ("Face AI", "Face AI"),
            ("Pose AI", "Pose AI"),
            ("Custom workspace", "CUSTOM WORKSPACE"),
        ]:
            engine_combo.addItem(_lbl, _data)
        res_combo = make_combo()
        for _lbl, _data in [("Auto", "Auto"), ("720p", "720p"), ("1080p", "1080p"), ("4K", "4K")]:
            res_combo.addItem(_lbl, _data)


        # Custom AI Configurator Overlay
        custom_config = {"cid": None, "script": ""}
        def open_custom_setup():
            sub_ov = QWidget(ov); sub_ov.setObjectName("Overlay"); sub_ov.resize(ov.size())
            sl = QVBoxLayout(sub_ov); sl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sc = QFrame(); sc.setObjectName("ModalBox"); sc.setFixedWidth(400)
            scl = QVBoxLayout(sc); scl.setContentsMargins(25,25,25,25); scl.setSpacing(20)
            
            eng_hdr = QLabel("Engine configuration")
            eng_hdr.setObjectName("SectionHeading")
            eng_hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scl.addWidget(eng_hdr)
            eng_sub = QLabel("Map a script from your workspace to this camera feed.")
            eng_sub.setObjectName("CaptionMuted")
            eng_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scl.addWidget(eng_sub)
            
            f_sub = QFormLayout(); f_sub.setSpacing(12)
            ws_cb = make_combo()
            conts = DockerManager.list_containers()
            if conts: [ws_cb.addItem(f"{c['name']}", c['id']) for c in conts]
            
            script_in = QLineEdit(); script_in.setPlaceholderText("/home/jetson/model.py")
            if custom_config["script"]: script_in.setText(custom_config["script"])
            
            f_sub.addRow("Workspace:", ws_cb)
            f_sub.addRow("Python script:", script_in)
            scl.addLayout(f_sub)
            
            done = QPushButton("Apply configuration"); done.setObjectName("BtnPrimary")
            done.clicked.connect(lambda: [custom_config.update({"cid": ws_cb.currentData(), "script": script_in.text()}), sub_ov.deleteLater()])
            scl.addWidget(done); sl.addWidget(sc); sub_ov.show()

        setup_btn = QPushButton("Configure script..."); setup_btn.setObjectName("ShellBtn"); setup_btn.setFixedSize(160, 32); setup_btn.hide()
        setup_btn.clicked.connect(open_custom_setup)

        # ZeroTier camera: fill URL from Settings remote host IP
        def fill_remote_stream_url():
            ip = (getattr(self, "node_ip", None) and self.node_ip.text().strip()) or ""
            if ip:
                url_input.setText(f"rtsp://{ip}:554/stream")
                if not name_input.text().strip():
                    name_input.setText("Remote Inspection Camera")
            else:
                self.show_toast("Connect a device from Devices first.")
        zt_cam_btn.clicked.connect(fill_remote_stream_url)

        # Safeguard all elements from deletion (None values are filtered by the 'if e' guard below)
        all_elements = [name_input, mode_combo, cam_combo, url_input, container_combo, script_input, cin,
                        engine_combo, res_combo, setup_btn, name_label, mode_label, cat_label, cam_label,
                        url_label, ws_label, exe_label, src_label, eng_label, prof_label, ai_setup_label,
                        zt_cam_label, zt_cam_btn, cat_combo, run_target_label, run_target_combo]
        
        for e in all_elements: 
            if e: e.setParent(box); e.hide()

        def update_visibility():
            while f.rowCount() > 0: f.takeRow(0)
            for e in all_elements: 
                if e: e.hide()
            
            if not is_cam:
                for x in [name_label, name_input, cat_label, cat_combo, run_target_label, run_target_combo, src_label, cin]: x.show()
                f.addRow(name_label, name_input)
                f.addRow(cat_label, cat_combo)
                f.addRow(run_target_label, run_target_combo)
                f.addRow(src_label, cin)
            else:
                for x in [name_label, name_input, mode_label, mode_combo, eng_label, engine_combo, prof_label, res_combo]: x.show()
                f.addRow(name_label, name_input)
                f.addRow(mode_label, mode_combo)

                m = mode_combo.currentIndex()
                if m == 1:
                    url_label.show(); url_input.show(); f.addRow(url_label, url_input)
                    if (getattr(self, "node_ip", None) and self.node_ip.text().strip()):
                        zt_cam_label.show(); zt_cam_btn.show(); f.addRow(zt_cam_label, zt_cam_btn)
                    else:
                        zt_cam_label.hide(); zt_cam_btn.hide()
                elif m == 0:
                    cam_label.show(); cam_combo.show(); f.addRow(cam_label, cam_combo)
                elif m == 2:
                    pass
                if m == 2:
                    ws_label.show(); container_combo.show(); exe_label.show(); script_input.show()
                    f.addRow(ws_label, container_combo)
                    f.addRow(exe_label, script_input)

                f.addRow(prof_label, res_combo)
                f.addRow(eng_label, engine_combo)
                if engine_combo.currentData() == "CUSTOM WORKSPACE":
                    ai_setup_label.show(); setup_btn.show(); f.addRow(ai_setup_label, setup_btn)

        if is_cam: 
            mode_combo.currentIndexChanged.connect(lambda: [update_visibility(), start_preview()])
            engine_combo.currentIndexChanged.connect(update_visibility)
            cam_combo.currentIndexChanged.connect(start_preview)
            url_input.textChanged.connect(lambda: QTimer.singleShot(1000, start_preview))
            start_preview()
        
        update_visibility()
        sl.addLayout(f); main_layout.addWidget(sett_box)
        bl.addLayout(main_layout); bl.addSpacing(10)
        h = QHBoxLayout()
        b1 = QPushButton("Cancel")
        b1.setObjectName("BtnModalCancel")
        b2 = QPushButton("Confirm")
        b2.setObjectName("BtnPrimary")
        b1.setMinimumHeight(44)
        b2.setMinimumHeight(44)
        def close_ov():
            if is_cam and preview_thread[0]: preview_thread[0].stop()
            ov.deleteLater()
        b1.clicked.connect(close_ov)
        
        def confirm():
            if is_cam and preview_thread[0]: preview_thread[0].stop()
            if is_cam:
                m = mode_combo.currentData()
                name = name_input.text().strip()
                val = ""
                if m == "Physical":
                    val = cam_combo.currentData()
                    if val is None:
                        QMessageBox.warning(ov, "Camera", "No camera selected or no cameras found.")
                        return
                    if not name: name = cam_combo.currentText()
                elif m == "Stream":
                    val = url_input.text().strip()
                    if not val:
                        QMessageBox.warning(ov, "Stream", "Enter a stream URL. If the camera belongs to the active Jetson, use 'Use active device RTSP'.")
                        return
                    if not name: name = f"Stream: {val[:15]}"
                elif m == "Container":
                    cid = container_combo.currentData(); script = script_input.text().strip()
                    if not cid:
                        QMessageBox.warning(ov, "Workspace preview", "No workspace container selected. Create one first or ensure it is running.")
                        return
                    val = f"docker://{cid}?script={script}"
                    if not name: name = f"AI: {container_combo.currentText().split(' ')[0]}"
                
                eng_key = engine_combo.currentData() or engine_combo.currentText()
                res_key = res_combo.currentData() or res_combo.currentText()
                meta = f"{m}|{eng_key}|{res_key}"
                if eng_key == "CUSTOM WORKSPACE":
                    meta += f"|{custom_config['cid']}|{custom_config['script']}"
                cb(name, val, meta)
            else:
                val = cin.text().strip() if cin.text() else (cat_combo.currentData() or "")
                name = name_input.text().strip()
                if not name: name = cin.text().strip() if cin.text() else (cat_combo.currentText() or "Workspace")
                if not val:
                    QMessageBox.warning(ov, "Workspace", "Select a template or enter an image tag.")
                    return
                run_target = (run_target_combo.currentData() or "") if (run_target_combo and not is_cam) else ""
                # Remote Docker selection can change while modal is open; guard stale/offline host.
                if run_target:
                    if not check_remote_node_reachable(run_target, port=2375, timeout=2):
                        QMessageBox.warning(
                            ov,
                            "Remote Docker",
                            f"Selected remote host {run_target}:2375 is not reachable.\n"
                            "Check ZeroTier connectivity and Docker daemon exposure, then try again."
                        )
                        return
                cb(name, val, run_target)
            ov.deleteLater()
            
        b2.clicked.connect(confirm)
        h.addWidget(b1)
        h.addStretch(1)
        h.addWidget(b2)
        bl.addLayout(h)
        l.addWidget(box)
        ov.show()

    def add_cam_logic(self, name, src, meta=None, save=True, start_preview=None):
        if src is None or src == "" or src in self.active_srcs: return
        self.active_srcs.add(src)
        card = ResizableCard(name, meta or "", False); card.trigger_delete_modal.connect(self.show_delete_confirmation); card.removed.connect(lambda: [self.active_srcs.remove(src) if src in self.active_srcs else None, self._camera_preview_enabled_sources.discard(src), card.deleteLater()])
        card.db = self.db; card.sub_val = src
        card._layout_persist_src = src
        saved = self._camera_card_geom.get(src)
        if saved:
            cw, ch = saved
            card.resize(cw, ch)
        self.cf.removeWidget(self.abc); self.cf.addWidget(card); self.cf.addWidget(self.abc)
        if save: self.db.save_camera(name, src, meta or "")
        self._refresh_camera_preview_summary()

        if str(src).startswith("docker://"):
            card.view.setText("AI engine starting...")
        elif src is not None:
            card.stream_meta = meta or ""
            should_start = bool(start_preview) if start_preview is not None else (
                (src in getattr(self, "_camera_preview_enabled_sources", set()))
                or bool(getattr(self, "_auto_camera_preview_on_launch", False))
            )
            if should_start:
                self._start_camera_preview(card)
            else:
                card.view.setText("Preview paused. Click Start.")
                card.set_status_info("PAUSED", "#8E8E93")
                if hasattr(card, "preview_btn"):
                    card.preview_btn.setText("Start")
        else:
            card.view.setText("No source signal")

    def _apply_stream_tuning(self, card, wh, max_fps: int):
        """RTSP/HTTP/TCP yayın kartı: istemci önizleme çözünürlüğü ve isteğe bağlı FPS üst sınırı."""
        src = getattr(card, "sub_val", None)
        if not src:
            return
        name = getattr(card, "title_text", "") or "Camera"
        old = getattr(card, "stream_meta", "") or ""
        new_meta = _rebuild_camera_meta_stream(old, wh, int(max_fps))
        card.stream_meta = new_meta
        try:
            self.db.update_camera_meta(src, new_meta)
        except Exception as e:
            log.warning("update_camera_meta failed: %s", e)
        self._attach_camera_thread(card, name, src, new_meta)
        self.show_toast("Stream settings saved; preview restarted.")

    def _start_camera_preview(self, card):
        if card is None:
            return
        src = getattr(card, "sub_val", None)
        if not src:
            return
        name = getattr(card, "title_text", "") or "Camera"
        meta = getattr(card, "stream_meta", "") or ""
        self._camera_preview_enabled_sources.add(src)
        self._attach_camera_thread(card, name, src, meta)
        if hasattr(card, "preview_btn"):
            card.preview_btn.setText("Stop")

    def _stop_camera_preview(self, card):
        if card is None:
            return
        src = getattr(card, "sub_val", None)
        if src:
            self._camera_preview_enabled_sources.discard(src)
        t = getattr(card, "t", None)
        if t is not None:
            t.stop()
            try:
                t.wait(1800)
            except Exception:
                pass
            card.t = None
        if hasattr(card, "view"):
            card.view.setText("Preview paused. Click Start.")
        if hasattr(card, "preview_btn"):
            card.preview_btn.setText("Start")
        if hasattr(card, "fps_lbl"):
            card.fps_lbl.setText("")
        card.set_status_info("PAUSED", "#8E8E93")

    def _attach_camera_thread(self, card, name, src, meta):
        """Kamera görüntü iş parçacığını (yeniden) bağlar."""
        if str(src).startswith("docker://"):
            return
        t_old = getattr(card, "t", None)
        if t_old is not None:
            t_old.stop()
            try:
                t_old.wait(2500)
            except Exception:
                pass
            card.t = None

        engine = "STANDARD"
        target_size = None
        camera_options = dict(get_camera_defaults())
        meta = meta or ""
        card.stream_meta = meta
        if meta and "|" in meta:
            parts = meta.split("|")
            engine = parts[1]
            legacy_res = False
            for seg in parts:
                if isinstance(seg, str) and seg.startswith("res_idx:"):
                    try:
                        idx = int(seg.split(":")[1])
                        if 0 <= idx < len(CAMERA_RESOLUTION_PRESETS):
                            camera_options["resolution_index"] = idx
                            legacy_res = True
                    except (ValueError, IndexError):
                        pass
                    break
            if len(parts) >= 3:
                target_size = _profile_to_size(parts[2])
                if not legacy_res:
                    csi_ix = _profile_key_to_csi_preset_index(parts[2])
                    if csi_ix is not None:
                        camera_options["resolution_index"] = csi_ix
            if engine == "CUSTOM WORKSPACE" and len(parts) >= 5:
                card.view.setText(f"Custom AI: {parts[4].split('/')[-1]}")
        stream_ov = _parse_stream_meta_overrides(meta)
        if stream_ov.get("stream_pv"):
            target_size = stream_ov["stream_pv"]
        if stream_ov.get("stream_max_fps") is not None:
            camera_options["stream_max_fps"] = stream_ov["stream_max_fps"]
        if str(src).strip().isdigit():
            try:
                camera_options["sensor_id"] = int(str(src).strip())
            except ValueError:
                pass

        t = VideoThread(
            src,
            engine,
            target_size=target_size,
            camera_options=camera_options,
            recording_label=name,
        )
        t.change_pixmap.connect(card.upd_img)
        t.analytics_signal.connect(card.update_ai_ui)
        t.fps_signal.connect(card.update_fps)
        t.connection_signal.connect(card.update_connection_status)

        def _on_cam_conn(status, _name=name, _src=src):
            cam_name = _name or _src or "camera"
            if status == "reconnecting":
                now = time.monotonic()
                key = str(_name or "") + "|" + str(_src or "")
                last = getattr(self, "_cam_reconnect_notify_ts", {}) or {}
                if now - float(last.get(key, 0.0)) < 14.0:
                    return
                last[key] = now
                self._cam_reconnect_notify_ts = last
                self.notify_warning(f"Camera reconnecting: {cam_name}")
            elif status == "disconnected":
                self.notify_error(f"Camera disconnected: {cam_name}")
            elif status == "connected":
                ck = str(_name or "") + "|" + str(_src or "")
                rmap = getattr(self, "_cam_reconnect_notify_ts", None)
                if isinstance(rmap, dict) and ck in rmap:
                    rmap.pop(ck, None)
                once = getattr(self, "_cam_connect_toast_once", set())
                if ck not in once:
                    once.add(ck)
                    self._cam_connect_toast_once = once
                    self.notify_success(f"Camera connected: {cam_name}")

        t.connection_signal.connect(_on_cam_conn)
        t.snap_done_signal.connect(card.on_snap_done)
        t.start()
        card.t = t

    def _docker_safe_name(self, name):
        """User-provided name -> valid Docker container name ( [a-zA-Z0-9_.-], max 63 chars )."""
        if not (name or "").strip():
            return f"jetson_{''.join(random.choices(string.ascii_lowercase, k=5))}"
        s = re.sub(r"[^a-zA-Z0-9_.-]", "_", (name or "").strip())
        s = s.strip("_") or "workspace"
        return s[:63] if len(s) > 63 else s

    def add_doc_logic(self, name, img, target=None):
        """Docker container oluştur — yerel veya uzak Jetson hedefine.

        target=None veya '' → yerel Docker
        target='10.x.x.x'  → uzak Jetson üzerinde Docker TCP (port 2375)
        """
        if not img:
            return
        cn = self._docker_safe_name(name)
        card = ResizableCard(pretty_workspace_title(cn), img, True)
        card.trigger_delete_modal.connect(self.show_delete_confirmation)
        card.removed.connect(card.deleteLater)
        card.set_status_info("Preparing...", "#3B82F6")
        card.db = self.db
        self.df.removeWidget(self.abd)
        self.df.addWidget(card)
        self.df.addWidget(self.abd)
        card.remote_host = target if (target and str(target).strip()) else None

        use_remote = target and str(target).strip()
        if use_remote:
            # Uzak Jetson: Docker TCP üzerinden
            # GPU erişimi + video cihazları + restart politikası
            _video_devs = " ".join(f"--device /dev/video{i}" for i in range(4))
            run_cmd = DockerManager.get_cmd(
                f"run -d --name {cn} --restart unless-stopped "
                f"--runtime nvidia "
                f"{_video_devs} "
                f"--network host "
                f"-e DISPLAY=:0 "
                f"-w /workspace "
                f"{img} sleep infinity",
                host_override=target
            )
        else:
            # Yerel docker
            ws_base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "workspaces"))
            ws_dir = os.path.join(ws_base, cn)
            os.makedirs(ws_dir, exist_ok=True)
            run_cmd = DockerManager.get_cmd(
                f"run -d --name {cn} --restart unless-stopped "
                f"-v \"{ws_dir}:/workspace\" "
                f"-w /workspace "
                f"{img} sleep infinity",
                host_override=""
            )

        card.w = DockerCreationThread(run_cmd)

        def on_created(o, s):
            if s:
                cid_norm = self._norm_cid(o)
                card.container_id = cid_norm
                card.set_status_info("Running", "#30D158")
                card.start_monitoring()
                self.db.save_workspace(cn, img, cid_norm, host=target if use_remote else None)
                if hasattr(self, "_refresh_workspace_package_panel"):
                    self._refresh_workspace_package_panel()
                if use_remote:
                    self.notify_success(
                        f"Remote container started: {cn} "
                        f"({target}) — use terminal for AI modules."
                    )
            else:
                card.set_status_info("Error", "#FF453A")
                raw = str(o).strip() if o else ""
                if not raw:
                    short, detail = "Container could not be created.", ""
                else:
                    short, detail = docker_cli_error_for_ui(raw)
                dlg = QMessageBox(self)
                dlg.setIcon(QMessageBox.Icon.Warning)
                dlg.setWindowTitle("Workspace")
                dlg.setText(short)
                if detail:
                    dlg.setDetailedText(detail)
                dlg.exec()

        card.w.result.connect(on_created)
        card.w.start()

    def _norm_cid(self, cid):
        """Normalize container id for matching (Docker may return 12- or 64-char ids)."""
        return (cid or "")[:12] if (cid or "") else ""

    def add_docker_card(self, n, i, c, running=True, save=True, host=None):
        if c is None or c == "": return
        ckey = self._norm_cid(c)
        if ckey and ckey in self.active_cids: return
        if ckey: self.active_cids.add(ckey)
        card = ResizableCard(pretty_workspace_title(n), i, True, c); card.trigger_delete_modal.connect(self.show_delete_confirmation); card.removed.connect(lambda: [self.active_cids.discard(self._norm_cid(c)), card.deleteLater()])
        card.remote_host = host
        card.db = self.db
        card.set_status_info("Running" if running else "Stopped", "#30D158" if running else "#FF453A")
        if running: card.start_monitoring()
        self.df.removeWidget(self.abd); self.df.addWidget(card); self.df.addWidget(self.abd)
        if save: self.db.save_workspace(n, i, c, host=host)

    def closeEvent(self, e):
        self._disconnect_all_ssh_sessions()
        self._persist_remote_host_pref()
        if getattr(self, "_remote_status_timer", None):
            self._remote_status_timer.stop()
        if getattr(self, "_inspection_runtime_timer", None):
            self._inspection_runtime_timer.stop()
        if getattr(self, "_zt_peer_refresh_timer", None):
            self._zt_peer_refresh_timer.stop()
        rthr = getattr(self, "_remote_status_thread", None)
        if rthr is not None and rthr.isRunning():
            rthr.wait(4000)
            if rthr.isRunning():
                rthr.terminate()
                rthr.wait(1500)
        irthr = getattr(self, "_inspection_runtime_thread", None)
        if irthr is not None and irthr.isRunning():
            irthr.wait(2500)
            if irthr.isRunning():
                irthr.terminate()
                irthr.wait(1000)
        iathr = getattr(self, "_inspection_runtime_action_thread", None)
        if iathr is not None and iathr.isRunning():
            iathr.wait(2500)
            if iathr.isRunning():
                iathr.terminate()
                iathr.wait(1000)
        for card in self.findChildren(ResizableCard):
            t = getattr(card, "t", None)
            if t is not None and t.isRunning():
                try:
                    t.stop()
                except Exception:
                    pass
            ck = getattr(card, "checker", None)
            if ck is not None and ck.isRunning():
                ck.quit()
                ck.wait(2500)
            dw = getattr(card, "w", None)
            if dw is not None and isinstance(dw, DockerCreationThread) and dw.isRunning():
                dw.wait(4000)
                if dw.isRunning():
                    dw.terminate()
                    dw.wait(1500)
        self.stats.stop()
        e.accept()

class FlowLayout(QLayout):
    def __init__(self, p=None): super().__init__(p); self.i = []
    def addItem(self, x): self.i.append(x)
    def count(self): return len(self.i)
    def itemAt(self, x): return self.i[x] if x<len(self.i) else None
    def takeAt(self, x): return self.i.pop(x) if x<len(self.i) else None
    def expandingDirections(self): return Qt.Orientations()
    def heightForWidth(self, w): return self.do(QRect(0,0,w,0), True)
    def setGeometry(self, r): super().setGeometry(r); self.do(r, False)
    def sizeHint(self): return QSize(100,100)
    def do(self, r, t):
        x, y, lh, sp = r.x(), r.y(), 0, 25
        for it in self.i:
            next_x = x + it.sizeHint().width() + sp
            if next_x - sp > r.right() and lh > 0: x, y, lh = r.x(), y + lh + sp, 0; next_x = x + it.sizeHint().width() + sp
            if not t: it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x, lh = next_x, max(lh, it.sizeHint().height())
        return y + lh - r.y()

if __name__ == "__main__":
    from PySide6.QtGui import QGuiApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    w = App()
        # Splash disabled for clean first launch
        # splash = w._show_splash_screen()
    w.show()
    sys.exit(app.exec())
