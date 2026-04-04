import sys, os, re, psutil, subprocess, cv2, time, platform, numpy as np, glob, random, string, threading, json, sqlite3, logging, socket, shlex
import urllib.request, urllib.error
from urllib.parse import urlparse
from datetime import datetime

APP_VERSION = "2.0.0"
from logging.handlers import RotatingFileHandler


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
    QSlider, QListView, QSpinBox, QDialog, QDialogButtonBox, QStyle, QSplashScreen,
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
    # Linux-specific fast discovery
    if platform.system() == "Linux":
        for d in glob.glob('/dev/video*'):
            try:
                idx = int(d.replace('/dev/video','').strip())
                if idx < 10: cams.append((f"Camera {idx} ({d})", idx))
            except: pass
        if cams: return cams

    # Platform-agnostic silent probing
    for i in range(1):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                cams.append((f"Camera {i} (default)", i))
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
            log.error("Terminal açma hatası: %s", e)

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
    GRIP = 22

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
        mid = QColor(self.palette().color(QPalette.ColorRole.Mid))
        mid.setAlpha(130 if dark else 100)
        p.setPen(QPen(mid, 1.2))
        margin = 5
        for i in range(3):
            d = margin + i * 3
            p.drawLine(w - d, h - margin, w - margin, h - d)


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
        cl.setContentsMargins(12, 10, 12, 12)
        cl.setSpacing(6)
        l.addWidget(self.cnt, 1)
        if is_docker:
            # Industrial Metadata Grid with Clean Identifier
            icon_box = QHBoxLayout(); icon_box.setSpacing(10)
            self.ico_lbl = QLabel("ID"); self.ico_lbl.setObjectName("AccentBadge"); self.ico_lbl.setFixedSize(36,36); self.ico_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
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
            self.view = QLabel("Starting feed..."); self.view.setObjectName("PreviewArea"); self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.view.setMinimumSize(1, 1)
            self._video_well.setMinimumHeight(200)
            vwl.addWidget(self.view)
            cl.addWidget(self._video_well, 1)
            self.grip = CardResizeHandle(self, self._video_well)
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
        g.move(max(0, well.width() - sz), max(0, well.height() - sz))
        g.raise_()

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
        if "://" in str(getattr(self, "sub_val", "")):
            add_item("Akış önizleme (çözünürlük / FPS)…", self._open_stream_tuning_dialog)
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
        dlg.setWindowTitle("Akış önizleme")
        dlg.setFixedWidth(448)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        lay.setContentsMargins(18, 18, 18, 16)
        det = ""
        if nw and nh:
            det = f"<br><b>Algılanan kaynak</b> — {nw}×{nh} (aynı en-boy oranında küçültmeler listelenir)."
            if fps_hint >= 5:
                det += f" Tahmini kaynak FPS: ~{fps_hint:.0f}."
        else:
            det = (
                "<br><i>Kaynak çözünürlüğü ölçülemedi</i> (ağ / kısa zaman aşımı). "
                "Genel 16:9 ön ayarlar gösterilir; canlı akış varken menüyü tekrar açmak genelde ölçümü iyileştirir."
            )
        info = QLabel(
            "<b>Önizleme çözünürlüğü</b> — seçilen boyuta <i>istemci tarafında</i> indirgenir; "
            "Jetson encoder çözünürlüğünü değiştirmez.<br>"
            "<b>FPS üst sınırı</b> — 0 = mümkün olan en akıcı; düşük CPU için sınırlayın."
            + det
        )
        info.setWordWrap(True)
        info.setObjectName("CaptionMuted")
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)
        combo = QComboBox()
        combo.setFixedHeight(36)
        if nw and nh:
            combo.addItem(f"Tam kaynak — {nw}×{nh} (yeniden örnekleme yok)", None)
            for w, h in _preview_resolution_choices(nw, nh):
                pct = max(1, min(99, int(round(100.0 * w * h / (nw * nh)))))
                combo.addItem(f"Önizleme {w}×{h}  (~%{pct})", (w, h))
        else:
            combo.addItem("Kaynak — yeniden boyutlandırma yok", None)
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
                combo.addItem(f"Kayıtlı ayar {w}×{h}", (w, h))
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
        lay.addWidget(QLabel("Önizleme çözünürlüğü"))
        lay.addWidget(combo)
        lay.addWidget(QLabel("Hedef FPS üst sınırı (0 = sınırsız)"))
        spin = QSpinBox()
        spin.setRange(0, 120)
        if fps_hint >= 8:
            spin.setMaximum(max(120, int(fps_hint) + 20))
            spin.setToolTip(f"Kaynak ~{fps_hint:.0f} fps bildirdi; üst sınır buna göre ayarlanabilir.")
        spin.setValue(cur_fps)
        spin.setFixedHeight(36)
        lay.addWidget(spin)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_b = bb.button(QDialogButtonBox.StandardButton.Ok)
        ok_b.setText("Uygula")
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
        sub = QLabel("Motorized lens: set I2C bus and lens position.")
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
            pos = slider.value()
            bus = bus_spin.value()
            set_camera_defaults(focus_position=pos, i2c_bus=bus)
            base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            script_path = os.path.join(base, "scripts", "focus_imx519.py")
            if os.path.exists(script_path):
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
                QMessageBox.information(self, "Focus", "scripts/focus_imx519.py not found.")
            dlg.close()

        apply_btn = QPushButton("Apply")
        apply_btn.setObjectName("FocusPrimaryBtn")
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.clicked.connect(apply_focus)
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
#  UI LAYOUT & THEME
# =============================================================================

class ThemeOps:
    """Single source palette + QSS — Obsidian Dark / Pearl Light premium themes."""

    @staticmethod
    def palette(is_dark: bool) -> dict:
        if is_dark:
            # ── Obsidian Dark ─────────────────────────────────────────────────
            return {
                "bg":                   "#07090F",   # Space black base
                "canvas":               "#0B0F1A",   # Slightly elevated surface
                "sb":                   "#090D18",   # Sidebar — near black
                "card":                 "#0F1629",   # Card — deep navy
                "txt":                  "#E8EEFF",   # Cool white text
                "sub":                  "#7B8DB0",   # Muted blue-gray
                "brd":                  "rgba(255,255,255,0.07)",   # Ultra-thin border
                "ibg":                  "#141C30",   # Input background
                "hov":                  "rgba(59,130,246,0.12)",    # Hover tint
                "ihov":                 "#1A2440",   # Input hover
                "ov_bg":                "rgba(5,8,15,0.92)",
                "card_txt":             "#EEF2FF",
                "preview_bg":           "#0A0E1A",
                "preview_br":           "rgba(100,140,220,0.28)",
                "surface_row":          "#111828",
                "surface_row_compact":  "#0F1525",
                "hairline":             "#1E2D4A",
                "card_header_bg":       "#131C30",
                "meta_panel_bg":        "#0D1525",
                "accent_soft_bg":       "rgba(59,130,246,0.12)",
                "accent_soft_br":       "rgba(99,160,255,0.32)",
                "ctx_menu_bg":          "#111828",
                "ctx_menu_hi":          "rgba(59,130,246,0.16)",
                "pop_edge":             "#1E2D4A",
                "zt_inset":             "#0A1020",
                "focus_shell_top":      "#141E35",
                "focus_shell_bot":      "#0C1525",
                "focus_border_hi":      "#6BA3FF",
                "focus_panel":          "rgba(255,255,255,0.05)",
                "focus_panel_br":       "rgba(107,163,255,0.28)",
                "accent_net":           "#34D399",   # Emerald — ZT IP / online
                "toast_scrim":          "rgba(5,8,15,0.96)",
            }
        # ── Pearl Light ───────────────────────────────────────────────────────
        return {
            "bg":                   "#F5F7FF",   # Very light indigo-white
            "canvas":               "#FAFBFF",   # Near white canvas
            "sb":                   "#EEF2FF",   # Indigo-tinted sidebar
            "card":                 "#FFFFFF",   # Pure white card
            "txt":                  "#0F172A",   # Deep navy text
            "sub":                  "#64748B",   # Slate gray secondary
            "brd":                  "rgba(15,23,42,0.08)",    # Ultra-thin border
            "ibg":                  "#F8FAFF",   # Input background
            "hov":                  "rgba(37,99,235,0.08)",   # Hover tint
            "ihov":                 "#EEF4FF",   # Input hover
            "ov_bg":                "rgba(240,245,255,0.90)",
            "card_txt":             "#1E293B",
            "preview_bg":           "#F0F4FF",
            "preview_br":           "rgba(37,99,235,0.18)",
            "surface_row":          "#F8FAFF",
            "surface_row_compact":  "#F3F6FF",
            "hairline":             "#E2E8F0",
            "card_header_bg":       "#F8FAFF",
            "meta_panel_bg":        "#F5F8FF",
            "accent_soft_bg":       "rgba(37,99,235,0.08)",
            "accent_soft_br":       "rgba(37,99,235,0.22)",
            "ctx_menu_bg":          "#FFFFFF",
            "ctx_menu_hi":          "rgba(37,99,235,0.10)",
            "pop_edge":             "#CBD5E1",   # Slate-300
            "zt_inset":             "#F5F8FF",
            "focus_shell_top":      "#FFFFFF",
            "focus_shell_bot":      "#F0F5FF",
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
        accent        = "#3B82F6" if is_dark else "#2563EB"   # Indigo-blue primary
        accent_hover  = "#60A5FA" if is_dark else "#3B82F6"   # Lighter on hover
        accent_pressed= "#1E40AF" if is_dark else "#1D4ED8"   # Deeper on press
        accent_deep   = "#1D4ED8" if is_dark else "#1E40AF"   # Gradient stop-1
        nav_idle      = "#8A9BBF" if is_dark else "#4A5568"
        search_ring   = "rgba(99,160,255,0.32)" if is_dark else "rgba(37,99,235,0.20)"
        card_hover_brd= "rgba(59,130,246,0.50)" if is_dark else "rgba(37,99,235,0.38)"
        if is_dark:
            sb_m_txt, sb_m_br, sb_m_bg = "#D4C4FD", "rgba(167,139,250,0.42)", "rgba(139,92,246,0.12)"
            sb_e_txt, sb_e_br, sb_e_bg = "#93C5FD", "rgba(99,160,255,0.40)",  "rgba(59,130,246,0.12)"
            sb_p_txt, sb_p_br, sb_p_bg = "#86EFAC", "rgba(52,211,153,0.42)",  "rgba(16,185,129,0.12)"
            docker_tag_txt = "#60A5FA"
            ai_hot         = "#3B82F6"
        else:
            sb_m_txt, sb_m_br, sb_m_bg = "#5B21B6", "rgba(91,33,182,0.30)",  "rgba(91,33,182,0.07)"
            sb_e_txt, sb_e_br, sb_e_bg = "#1D4ED8", "rgba(37,99,235,0.28)",  "rgba(37,99,235,0.08)"
            sb_p_txt, sb_p_br, sb_p_bg = "#047857", "rgba(4,120,87,0.28)",   "rgba(5,150,105,0.10)"
            docker_tag_txt = "#1D4ED8"
            ai_hot         = "#2563EB"

        return f"""
        QMainWindow {{ background-color: {bg}; }}
        QWidget {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'SF Pro Display', Roboto, 'Helvetica Neue', sans-serif; color: {txt}; font-size: 13px; letter-spacing: 0.12px; }}
        QWidget#MainCanvas {{ background-color: {canvas}; }}
        QFrame#Sidebar {{ background-color: {sb}; border-right: 1px solid {brd}; }}
        QFrame#Card, QFrame#InfoCard {{ background-color: {card}; border: 1px solid {pop_edge}; border-radius: 20px; }}
        QFrame#Card:hover, QFrame#InfoCard:hover {{ border-color: {card_hover_brd}; }}
        QFrame#ModalBox {{ background-color: {card}; border: 1px solid {pop_edge}; border-radius: 18px; }}
        QFrame#CardHeader {{ background-color: {card_header_bg}; border-top-left-radius: 16px; border-top-right-radius: 16px; border-bottom: 1px solid {pop_edge}; }}
        QFrame#MetaPanel {{ background-color: {meta_panel_bg}; border: 1px solid {pop_edge}; border-radius: 12px; }}
        QFrame#MediaCard {{ background-color: {surface_row}; border: 1px solid {pop_edge}; border-radius: 20px; }}
        QFrame#MediaCard:hover {{ border-color: {card_hover_brd}; }}
        QLabel#MediaThumb {{ background-color: {preview_bg}; border: 1px solid {pop_edge}; border-radius: 14px; color: {sub}; }}
        QLabel#MediaThumbPlaceholder {{
            background-color: {meta_panel_bg}; border: 2px dashed {pop_edge}; border-radius: 14px; color: {sub};
            font-size: 11px; font-weight: 800; letter-spacing: 0.12em;
        }}
        QLabel#MediaCardTitle {{ color: {txt}; font-size: 13px; font-weight: 800; border: none; background: transparent; }}
        QLabel#MediaCardMeta {{ color: {sub}; font-size: 11px; font-weight: 600; border: none; background: transparent; }}
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
            font-size: 9px;
            font-weight: 800;
            letter-spacing: 0.3px;
            border: none;
            background: transparent;
            min-width: 48px;
        }}
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
            color: #FFFFFF;
            border-radius: 12px;
            padding: 10px 28px;
            font-size: 14px;
            font-weight: 800;
            border: 1px solid rgba(255,255,255,0.18);
            min-height: 44px;
            min-width: 128px;
        }}
        QPushButton#FocusPrimaryBtn:hover {{ background: {accent_hover}; color: #FFFFFF; }}
        QPushButton#FocusPrimaryBtn:pressed {{ background: {accent_pressed}; color: #FFFFFF; }}
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
        QLabel#CaptionMuted {{ color: {sub}; font-size: 11px; border: none; background: transparent; }}
        QLabel#CaptionTiny {{ color: {sub}; font-size: 9px; border: none; background: transparent; }}
        QLabel#FolderSlug {{ color: {sub}; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; border: none; background: transparent; }}
        QLabel#MonoIp {{ font-family: 'SF Mono', ui-monospace, monospace; font-size: 12px; font-weight: 700; color: {accent_net}; border: none; background: transparent; min-width: 110px; }}
        QLabel#CaptionMutedSm {{ color: {sub}; font-size: 10px; font-weight: 700; border: none; background: transparent; }}
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
        QLineEdit, QComboBox, QSpinBox {{ background-color: {ibg}; border: 1px solid {pop_edge}; border-radius: 12px; padding: 10px 16px; color: {txt}; font-size: 14px; font-weight: 500; min-height: 40px; }}
        QSpinBox::up-button, QSpinBox::down-button {{ width: 24px; border: none; background: transparent; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {accent}; border-width: 1.5px; background-color: rgba(59,130,246,0.07); }}
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
        QPushButton#NavTab {{ border: none; border-radius: 12px; text-align: left; padding: 14px 20px; color: {nav_idle}; font-weight: 600; font-size: 14px; letter-spacing: 0.12px; min-height: 48px; }}
        QPushButton#NavTab:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {accent}, stop:1 {accent_deep}); color: #FFFFFF; border: 1px solid rgba(255,255,255,0.18); border-left: 3px solid rgba(255,255,255,0.65); font-weight: 800; }}
        QPushButton#NavTab:hover:!checked {{ background-color: {hov}; color: {txt}; border: 1px solid {pop_edge}; }}
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
        QPushButton#BtnPrimary {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {accent}, stop:1 {accent_deep}); color: #FFFFFF; border-radius: 10px; padding: 8px 18px; font-weight: 700; border: none; font-size: 13px; letter-spacing: 0.3px; min-height: 36px; }}
        QPushButton#BtnPrimary:hover {{ background: {accent_hover}; color: #FFFFFF; }}
        QPushButton#BtnPrimary:pressed {{ background: {accent_pressed}; color: #FFFFFF; }}
        QPushButton#BtnSecondary {{ background-color: {hov}; color: {txt}; border-radius: 10px; padding: 8px 16px; font-weight: 700; border: 1px solid {brd}; font-size: 13px; min-height: 36px; }}
        QPushButton#BtnSecondary:hover {{ background-color: {ihov}; border-color: {accent}; color: {accent}; }}
        QPushButton#BtnSecondary:pressed {{ background-color: {accent_soft_bg}; }}
        QPushButton#BtnOutline {{ background: transparent; color: {accent}; border-radius: 10px; padding: 8px 16px; font-weight: 700; border: 1.5px solid {accent}; font-size: 13px; min-height: 36px; }}
        QPushButton#BtnOutline:hover {{ background-color: {accent_soft_bg}; }}
        QPushButton#BtnSm {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {accent}, stop:1 {accent_deep}); color: #FFFFFF; border-radius: 8px; padding: 5px 14px; font-weight: 700; border: none; font-size: 12px; min-height: 30px; }}
        QPushButton#BtnSm:hover {{ background: {accent_hover}; }}
        QPushButton#BtnDanger {{ background-color: rgba(239, 68, 68, 0.12); color: #EF4444; border-radius: 10px; padding: 8px 14px; border: 1px solid rgba(239, 68, 68, 0.28); font-weight: 700; font-size: 13px; min-height: 36px; }}
        QPushButton#BtnDanger:hover {{ background-color: #EF4444; color: white; }}
        QPushButton#BtnDanger:pressed {{ background-color: #C93434; color: white; }}
        QPushButton#ShellBtn {{ border: 1px solid {brd}; border-radius: 10px; color: {txt}; font-size: 12px; font-weight: 600; background: {hov}; padding: 6px 14px; min-height: 30px; }}
        QPushButton#ShellBtn:hover {{ border-color: {accent}; color: {accent}; background: {accent_soft_bg}; }}
        QPushButton#ShellBtn:pressed {{ background: {ihov}; }}
        QPushButton#OpenAction {{ background-color: {accent_soft_bg}; color: {accent}; border-radius: 10px; font-size: 12px; font-weight: 700; border: 1px solid {accent_soft_br}; padding: 5px 14px; min-height: 30px; }}
        QPushButton#OpenAction:hover {{ background-color: {accent}; color: #FFFFFF; border-color: {accent}; }}
        QPushButton#RowGhost {{ border: 1px solid {brd}; border-radius: 8px; background: {hov}; color: {txt}; font-size: 11px; font-weight: 600; padding: 5px 12px; min-height: 30px; }}
        QPushButton#RowGhost:hover {{ border-color: {accent}; color: {accent}; background-color: {accent_soft_bg}; }}
        QPushButton#RowGhost:pressed {{ background-color: {ihov}; }}
        QPushButton#RowAccent {{ border: 1.5px solid {accent_soft_br}; border-radius: 8px; background: {accent_soft_bg}; color: {accent}; font-size: 11px; font-weight: 700; padding: 5px 12px; min-height: 30px; }}
        QPushButton#RowAccent:hover {{ background-color: {accent}; color: #FFFFFF; border-color: {accent}; }}
        QPushButton#RowAccent:pressed {{ background-color: {accent_deep}; color: #FFFFFF; }}
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
# Resolution presets: tuned for high-res Sony CSI modules (e.g. IMX519); IMX219/477/230 limits differ — see docs/CSI_CAMERA_FOCUS.md
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
        log.debug("probe_stream_native_geometry: %s", e)
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


def save_app_prefs_remote_host(ip):
    """Persist Settings → Remote host IP. Empty string clears remote (local Docker)."""
    try:
        d = load_app_prefs()
        d["remote_host_ip"] = (ip or "").strip()
        with open(_app_prefs_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception as e:
        logging.debug("save_app_prefs_remote_host: %s", e)


def _camera_defaults_path():
    return os.path.join(_visiondock_dir(), "camera_defaults.json")

def get_camera_defaults():
    """Global camera defaults (updated from Settings, persisted to disk)."""
    if not hasattr(get_camera_defaults, "_defaults"):
        get_camera_defaults._defaults = {
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
                if opts.get("focus_mode") == "fixed":
                    pos = int(opts.get("focus_position", 512))
                    bus = int(opts.get("i2c_bus", 10))
                    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "focus_imx519.py")
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

class App(QMainWindow):
    def __init__(self):
        super().__init__(); self.resize(1200, 800)
        self._production_mode = is_production_mode()
        self._remote_host_locked = is_remote_host_locked()
        _title = "VisionDock"
        if self._production_mode:
            _title += " · Production"
        self.setWindowTitle(_title)
        self.is_dark = True
        log.info(
            "Starting (production=%s remote_ip_locked=%s)",
            self._production_mode,
            self._remote_host_locked,
        )
        self.db = DBManager()
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
        # check_docker runs only on manual refresh; on startup load from DB only (avoid duplicate cards)
        QTimer.singleShot(800, self._show_onboarding_wizard)
        QTimer.singleShot(6000, self._check_for_updates)
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
        sb = QFrame(); sb.setObjectName("Sidebar"); sb.setFixedWidth(332)
        sl = QVBoxLayout(sb); sl.setContentsMargins(22, 32, 22, 20); sl.setSpacing(10)
        
        # Professional Logo Branding
        _logo_px = 92
        l_box = QHBoxLayout(); l_box.setContentsMargins(0, 0, 0, 28); l_box.setSpacing(18)
        logo_img = QLabel(); logo_img.setFixedSize(_logo_px, _logo_px)
        pix = QPixmap(resource_path("visiondock.svg"))
        if not pix.isNull():
            logo_img.setPixmap(
                pix.scaled(_logo_px, _logo_px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        l_box.addWidget(logo_img, 0, Qt.AlignmentFlag.AlignTop)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(5)
        brand_text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        logo_txt = QLabel("VisionDock")
        logo_txt.setObjectName("BrandTitle")
        brand_tag = QLabel("INDUSTRIAL VISION")
        brand_tag.setObjectName("BrandTagline")
        brand_text.addWidget(logo_txt)
        brand_text.addWidget(brand_tag)
        l_box.addLayout(brand_text)
        l_box.addStretch()
        sl.addLayout(l_box)
        
        self.eco_mode = False
        self.tabs = FadeStackedWidget(); self.navs = []
        # Order: Home, Cameras, Workspaces, Devices, Library, Settings
        nav_names = ["Home", "Cameras", "Workspaces", "Devices", "Library", "Settings"]
        for i, t in enumerate(nav_names):
            b = QPushButton(t); b.setObjectName("NavTab"); b.setCheckable(True); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(46); b.setMinimumWidth(210)
            b.clicked.connect(lambda _, x=i: self.switch(x)); sl.addWidget(b); self.navs.append(b)
        sl.addStretch(); host = QLabel(platform.node()); host.setObjectName("SidebarFootnote"); sl.addWidget(host)
        self.main.addWidget(sb); self.main.addWidget(self.tabs)
        # Stack order matches nav: 0=home, 1=cams, 2=docker, 3=devices, 4=library, 5=settings
        self.tabs.addWidget(self.page_home()); self.tabs.addWidget(self.page_cams()); self.tabs.addWidget(self.page_docker()); self.tabs.addWidget(self.page_devices()); self.tabs.addWidget(self.page_library()); self.tabs.addWidget(self.page_settings())
        self.navs[0].setChecked(True)

    def page_library(self):
        self._lib_paths = []
        self._lib_recording_dir = recordings_root_dir()
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(40, 40, 40, 40)
        l.setSpacing(0)
        head = QVBoxLayout()
        head.setSpacing(6)
        title_row = QHBoxLayout()
        pt = QLabel("Library")
        pt.setObjectName("PageTitle")
        title_row.addWidget(pt)
        title_row.addStretch()
        title_row.addWidget(make_icon_refresh_button(self.refresh_library, "Refresh"))
        head.addLayout(title_row)
        sub = QLabel("Newest media first. Filter by file or folder name.")
        sub.setObjectName("PageSubtitle")
        sub.setWordWrap(True)
        head.addWidget(sub)
        l.addLayout(head)
        l.addSpacing(14)

        hint = QLabel(
            "Stored under gui/recordings/ (one subfolder per camera; older flat files in the root still list here)."
        )
        hint.setObjectName("CaptionMuted")
        hint.setWordWrap(True)
        l.addWidget(hint)
        l.addSpacing(12)

        lib_card = QFrame()
        lib_card.setObjectName("InfoCard")
        lib_cl = QVBoxLayout(lib_card)
        lib_cl.setContentsMargins(20, 20, 20, 20)
        lib_cl.setSpacing(12)

        tool = QHBoxLayout()
        self.lib_search = QLineEdit()
        self.lib_search.setPlaceholderText("Filter by file or folder name...")
        self.lib_search.setMinimumWidth(200)
        self.lib_search.setAccessibleName("Filter library items by name")
        self.lib_search.textChanged.connect(lambda _: self._render_library_items())
        lib_search_shell = SearchFieldShell(self.lib_search)
        lib_search_shell.setMinimumWidth(280)
        tool.addWidget(lib_search_shell, 1)
        lib_cl.addLayout(tool)

        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setStyleSheet("background: transparent; border: none;")
        self.lib_widget = QWidget()
        self.lib_flow = FlowLayout(self.lib_widget)
        sa.setWidget(self.lib_widget)
        polish_scroll_area(sa)
        lib_cl.addWidget(sa)
        l.addWidget(lib_card, 1)
        QTimer.singleShot(400, self.refresh_library)
        return w

    def refresh_library(self):
        p = getattr(self, "_lib_recording_dir", None) or recordings_root_dir()
        self._lib_recording_dir = p
        self._lib_paths = collect_recording_file_paths(p)
        self._render_library_items()

    def _library_folder_label(self, fpath: str):
        """Subfolder name under recordings, or None for legacy root-level files."""
        base = os.path.normpath(getattr(self, "_lib_recording_dir", "") or "")
        if not base or not fpath:
            return None
        try:
            fpath = os.path.normpath(fpath)
            rel = os.path.relpath(fpath, base)
            if rel.startswith(".."):
                return None
            parts = rel.split(os.sep)
            if len(parts) >= 2:
                return parts[0]
        except ValueError:
            pass
        return None

    def _render_library_items(self):
        fl = getattr(self, "lib_flow", None)
        if fl is None:
            return
        while fl.count():
            it = fl.takeAt(0)
            w = it.widget() if it else None
            if w:
                w.deleteLater()

        full = list(getattr(self, "_lib_paths", []) or [])
        paths = list(full)
        paths.sort(key=lambda x: os.path.getmtime(x), reverse=True)

        q = (getattr(self, "lib_search", None) and self.lib_search.text() or "").strip().lower()
        if q:
            paths = [
                p
                for p in paths
                if q in os.path.basename(p).lower()
                or q in (self._library_folder_label(p) or "").lower()
            ]

        if not paths:
            rec = getattr(self, "_lib_recording_dir", "")
            if q and full:
                msg = "No files match your search."
            elif not os.path.isdir(rec):
                msg = "Recordings will appear after the first snapshot or REC from a camera."
            else:
                msg = "No files yet. Use Snapshot or REC on a camera card; each camera has its own folder."
            empty = QLabel(msg)
            empty.setObjectName("CaptionMuted")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(160)
            empty.setMinimumWidth(360)
            fl.addWidget(empty)
            self.lib_widget.updateGeometry()
            return

        for fpath in paths[:80]:
            fl.addWidget(self._make_media_card(fpath, self._library_folder_label(fpath)))
        self.lib_widget.updateGeometry()

    def _thumb_for_media(self, fpath: str, target_w: int, target_h: int):
        ext = os.path.splitext(fpath)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
            pix = QPixmap(fpath)
            if not pix.isNull():
                return pix.scaled(target_w, target_h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        if ext in (".avi", ".mp4", ".mov", ".mkv", ".webm"):
            try:
                cap = cv2.VideoCapture(fpath)
                ok, frame = cap.read()
                cap.release()
                if ok and frame is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, c = rgb.shape
                    if h > 0 and w > 0:
                        qimg = QImage(rgb.data, w, h, c * w, QImage.Format.Format_RGB888).copy()
                        pm = QPixmap.fromImage(qimg)
                        return pm.scaled(
                            target_w, target_h,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
            except Exception:
                pass
        return QPixmap()

    def _make_media_card(self, fpath: str, folder_label=None) -> QFrame:
        card = QFrame()
        card.setObjectName("MediaCard")
        card.setFixedSize(212, 292)
        vl = QVBoxLayout(card)
        vl.setContentsMargins(14, 14, 14, 14)
        vl.setSpacing(10)

        thumb = QLabel()
        thumb.setObjectName("MediaThumb")
        thumb.setFixedSize(184, 118)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pm = self._thumb_for_media(fpath, 184, 118)
        ext = os.path.splitext(fpath)[1].lower()
        if not pm.isNull():
            thumb.setPixmap(rounded_pixmap(pm, 12))
        else:
            thumb.setObjectName("MediaThumbPlaceholder")
            label = "VIDEO" if ext in (".avi", ".mp4", ".mov", ".mkv", ".webm") else "PHOTO" if ext in (".jpg", ".jpeg", ".png", ".webp") else "FILE"
            thumb.setText(label)
        vl.addWidget(thumb)

        if ext in (".avi", ".mp4", ".mov", ".mkv", ".webm"):
            _type_str = "VIDEO"
        elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
            _type_str = "PHOTO"
        else:
            _type_str = "FILE"
        type_badge = QLabel(_type_str)
        type_badge.setObjectName("MediaTypeTag")
        vl.addWidget(type_badge)

        base = os.path.basename(fpath)
        nm = QLabel(base)
        nm.setObjectName("MediaCardTitle")
        nm.setWordWrap(True)
        nm.setMaximumHeight(44)
        vl.addWidget(nm)
        if folder_label:
            fd = QLabel(folder_label)
            fd.setObjectName("FolderSlug")
            vl.addWidget(fd)

        try:
            st = os.stat(fpath)
            ts = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            sz_kb = max(1, st.st_size // 1024)
            sz_txt = f"{sz_kb:,} KB" if st.st_size < 1024 * 1024 else f"{st.st_size / (1024 * 1024):.1f} MB"
            meta_txt = f"{ts}  ·  {sz_txt}"
        except OSError:
            meta_txt = "—"
        meta = QLabel(meta_txt)
        meta.setObjectName("MediaCardMeta")
        vl.addWidget(meta)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def universal_open(p):
            plat = platform.system()
            if plat == "Darwin":
                subprocess.Popen(["open", p])
            elif plat == "Linux":
                subprocess.Popen(["xdg-open", p])
            elif plat == "Windows":
                os.startfile(p)

        ob = QPushButton("Open")
        ob.setObjectName("OpenAction")
        ob.setFixedHeight(30)
        ob.setCursor(Qt.CursorShape.PointingHandCursor)
        ob.clicked.connect(lambda _, p=fpath: universal_open(p))

        cp = QPushButton("Copy")
        cp.setObjectName("ShellBtn")
        cp.setFixedHeight(30)
        cp.setToolTip("Copy full file path to clipboard")
        cp.setCursor(Qt.CursorShape.PointingHandCursor)

        def _copy_path(p):
            QApplication.clipboard().setText(p)
            self.show_toast("Path copied to clipboard")

        cp.clicked.connect(lambda _, p=fpath: _copy_path(p))
        btn_row.addWidget(ob, 1)
        btn_row.addWidget(cp, 1)

        if not getattr(self, "_production_mode", False):
            rm = QPushButton("×")
            rm.setObjectName("IconCloseSm")
            rm.setFixedSize(34, 30)
            rm.setCursor(Qt.CursorShape.PointingHandCursor)

            def do_remove(path):
                try:
                    os.remove(path)
                    self.refresh_library()
                except OSError as e:
                    self.show_toast(f"Could not delete: {e}")

            rm.clicked.connect(lambda _, p=fpath: do_remove(p))
            btn_row.addWidget(rm, 0)

        vl.addLayout(btn_row)
        return card

    # ── HOME / DASHBOARD PAGE ─────────────────────────────────────────────────
    def page_home(self):
        """Dashboard page — ZeroTier status, device/camera summary, quick actions."""
        w = QWidget(); w.setObjectName("PageHome")
        root = QVBoxLayout(w); root.setContentsMargins(32, 28, 32, 28); root.setSpacing(24)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("Dashboard"); title.setObjectName("PageTitle")
        subtitle = QLabel("VisionDock overview"); subtitle.setObjectName("CaptionMuted")
        title_col = QVBoxLayout(); title_col.setSpacing(2)
        title_col.addWidget(title); title_col.addWidget(subtitle)
        hdr.addLayout(title_col); hdr.addStretch()
        refresh_btn = QPushButton("Refresh"); refresh_btn.setObjectName("ShellBtn")
        refresh_btn.setFixedHeight(32); refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_home_page)
        hdr.addWidget(refresh_btn)
        root.addLayout(hdr)

        # ── ZeroTier status card ───────────────────────────────────────────────
        zt_card = QFrame(); zt_card.setObjectName("HomeZtCard")
        zt_layout = QHBoxLayout(zt_card); zt_layout.setContentsMargins(20, 16, 20, 16); zt_layout.setSpacing(16)
        zt_text_col = QVBoxLayout(); zt_text_col.setSpacing(4)
        zt_title = QLabel("ZeroTier Network"); zt_title.setObjectName("HomeCardTitle")
        self._home_zt_status = QLabel("Checking…"); self._home_zt_status.setObjectName("HomeZtStatusLabel")
        zt_text_col.addWidget(zt_title); zt_text_col.addWidget(self._home_zt_status)
        zt_layout.addLayout(zt_text_col); zt_layout.addStretch()
        root.addWidget(zt_card)

        # ── Stats row ─────────────────────────────────────────────────────────
        stats_row = QHBoxLayout(); stats_row.setSpacing(16)

        def make_stat_card(label, attr_name):
            card = QFrame(); card.setObjectName("HomeStatCard")
            cl = QVBoxLayout(card); cl.setContentsMargins(20, 18, 20, 18); cl.setSpacing(4)
            val = QLabel("—"); val.setObjectName("HomeStatValue")
            lbl = QLabel(label); lbl.setObjectName("HomeStatLabel")
            cl.addWidget(val); cl.addWidget(lbl)
            setattr(self, attr_name, val)
            return card

        stats_row.addWidget(make_stat_card("Saved devices", "_home_stat_devices"))
        stats_row.addWidget(make_stat_card("Active Cameras", "_home_stat_cameras"))
        stats_row.addWidget(make_stat_card("Workspaces", "_home_stat_workspaces"))
        root.addLayout(stats_row)

        # ── Quick actions ─────────────────────────────────────────────────────
        qa_title = QLabel("Quick Actions"); qa_title.setObjectName("SectionTitle")
        root.addWidget(qa_title)
        qa_row = QHBoxLayout(); qa_row.setSpacing(16)

        def make_action_card(title_txt, hint_txt, cb):
            card = QFrame(); card.setObjectName("HomeActionCard")
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            cl = QVBoxLayout(card); cl.setContentsMargins(20, 18, 20, 18); cl.setSpacing(6)
            t = QLabel(title_txt); t.setObjectName("HomeActionTitle")
            h = QLabel(hint_txt); h.setObjectName("HomeActionHint"); h.setWordWrap(True)
            cl.addWidget(t); cl.addWidget(h)
            btn = QPushButton("Open"); btn.setObjectName("BtnPrimary")
            btn.setFixedHeight(30); btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(cb); cl.addWidget(btn)
            return card

        qa_row.addWidget(make_action_card("Cameras", "View and manage live camera streams.", lambda: self.switch(1)))
        qa_row.addWidget(make_action_card("Devices", "Connect to Jetson devices via ZeroTier.", lambda: self.switch(3)))
        qa_row.addWidget(make_action_card("Workspaces", "Manage Docker workspaces on Jetson.", lambda: self.switch(2)))
        root.addLayout(qa_row)

        root.addStretch()

        root.addStretch()

        # Populate stats immediately
        QTimer.singleShot(200, self.refresh_home_page)
        return w

    def refresh_home_page(self):
        """Refresh all stats on the Dashboard page."""
        # ZeroTier status
        zt_status = get_zerotier_status()
        zt_ips = get_zerotier_local_ips()
        if zt_status == "ONLINE":
            ip_str = zt_ips[0] if zt_ips else "No IP assigned"
            status_txt = f"Online — {ip_str}"
        elif zt_status == "OFFLINE":
            status_txt = "Offline — ZeroTier not connected"
        elif zt_status == "NOT INSTALLED":
            status_txt = "Not installed"
        else:
            status_txt = "Unknown"
        if hasattr(self, "_home_zt_status"):
            self._home_zt_status.setText(status_txt)

        # Device count
        try:
            devices = self.db.get_devices() if hasattr(self, "db") else []
            dev_count = len(devices)
        except Exception:
            dev_count = 0
        if hasattr(self, "_home_stat_devices"):
            self._home_stat_devices.setText(str(dev_count))

        # Camera count
        try:
            cam_count = len(getattr(self, "active_srcs", {}))
        except Exception:
            cam_count = 0
        if hasattr(self, "_home_stat_cameras"):
            self._home_stat_cameras.setText(str(cam_count))

        # Workspace count
        try:
            ws_count = len(getattr(self, "active_cids", {}))
        except Exception:
            ws_count = 0
        if hasattr(self, "_home_stat_workspaces"):
            self._home_stat_workspaces.setText(str(ws_count))

        # ── B6: ZT IP suggestion banner ───────────────────────────────────────
        if hasattr(self, "_home_zt_banner"):
            try:
                current_host = (load_app_prefs().get("remote_host_ip") or "").strip()
                show_banner = False
                suggested_ip = ""
                if zt_status == "ONLINE" and not current_host:
                    peers = get_zerotier_peers()
                    for p in peers:
                        addrs = p.get("assignedAddresses") or []
                        if not isinstance(addrs, list):
                            addrs = [addrs] if addrs else []
                        for a in addrs:
                            ip = str(a).split("/")[0].strip()
                            if ip:
                                suggested_ip = ip
                                show_banner = True
                                break
                        if show_banner:
                            break
                self._home_zt_banner.setVisible(show_banner)
                if show_banner and hasattr(self, "_home_zt_banner_ip"):
                    self._home_zt_banner_ip.setText(suggested_ip)
                    self._home_zt_set_btn.setProperty("_zt_suggested_ip", suggested_ip)
            except Exception:
                self._home_zt_banner.setVisible(False)

    def _apply_zt_banner_ip(self):
        """Apply the suggested ZT peer IP as the Docker remote host."""
        ip = ""
        if hasattr(self, "_home_zt_set_btn"):
            ip = (self._home_zt_set_btn.property("_zt_suggested_ip") or "").strip()
        if not ip and hasattr(self, "_home_zt_banner_ip"):
            ip = (self._home_zt_banner_ip.text() or "").strip()
        if not ip:
            return
        save_app_prefs_remote_host(ip)
        if hasattr(self, "node_ip"):
            self.node_ip.setText(ip)
        DockerManager.set_host(ip)
        if hasattr(self, "_home_zt_banner"):
            self._home_zt_banner.setVisible(False)
        self.show_toast(f"Remote host set to {ip}")

    # ── ONBOARDING WIZARD ─────────────────────────────────────────────────────
    def _show_onboarding_wizard(self):
        """Show first-run onboarding wizard if not completed."""
        prefs = load_app_prefs()
        if prefs.get("onboarding_complete"):
            return
        # Check if user already has cameras or devices — skip if so
        try:
            has_cameras = len(self.db.get_cameras()) > 0
            has_devices = len(self.db.get_devices()) > 0
            if has_cameras or has_devices:
                self._complete_onboarding(silent=True)
                return
        except Exception:
            pass
        self._onboarding_step = 0
        self._render_onboarding_step()

    def _render_onboarding_step(self):
        """Render the current onboarding step as a modal dialog."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        step = getattr(self, "_onboarding_step", 0)
        steps = [
            {
                "icon": "",
                "title": "Welcome to VisionDock",
                "body": (
                    "VisionDock lets you manage Jetson cameras and AI workspaces "
                    "over a secure ZeroTier network.\n\n"
                    "This quick setup will guide you through:\n"
                    "  1. Verifying your ZeroTier connection\n"
                    "  2. Connecting your first Jetson device\n"
                    "  3. Adding your first camera stream"
                ),
                "btn": "Get Started",
            },
            {
                "icon": "",
                "title": "Step 1 — ZeroTier Network",
                "body": (
                    "VisionDock uses ZeroTier to connect your Mac/PC to Jetson devices "
                    "over any network without port forwarding.\n\n"
                    "Make sure:\n"
                    "  • ZeroTier is installed and running on this machine\n"
                    "  • ZeroTier is installed and running on your Jetson\n"
                    "  • Both devices are joined to the same Network ID\n"
                    "  • Both devices are Authorized in ZeroTier Central\n\n"
                    f"Current status: {get_zerotier_status()}"
                ),
                "btn": "Next: Connect Device",
            },
            {
                "icon": "",
                "title": "Step 2 — Connect Your Jetson",
                "body": (
                    "Go to the Devices page to connect your Jetson.\n\n"
                    "  1. Click 'Devices' in the sidebar\n"
                    "  2. Find your Jetson in the ZeroTier peer list\n"
                    "  3. Use 'ZT IP Al' (SSH password once) for the virtual IP, or 'Save & Connect' to stay logged in\n"
                    "  4. Click 'Remote Host Yap' to set the Docker host to that ZeroTier IP\n\n"
                    "Your Jetson's ZeroTier virtual IP will be used for Docker and streams."
                ),
                "btn": "Next: Add Camera",
            },
            {
                "icon": "",
                "title": "Step 3 — Add Your First Camera",
                "body": (
                    "Go to the Cameras page to add a camera stream.\n\n"
                    "  1. Click 'Cameras' in the sidebar\n"
                    "  2. Click '+ Add Camera'\n"
                    "  3. Enter a name and RTSP/HTTP stream URL\n"
                    "     Example: rtsp://<ZeroTier-IP>:554/stream\n\n"
                    "You can also add cameras directly from a connected device "
                    "on the Devices page."
                ),
                "btn": "Finish Setup",
            },
        ]
        if step >= len(steps):
            self._complete_onboarding()
            return
        s = steps[step]
        dlg = QDialog(self)
        dlg.setWindowTitle("VisionDock Setup")
        dlg.setMinimumWidth(480)
        dlg.setModal(True)
        vl = QVBoxLayout(dlg); vl.setContentsMargins(32, 28, 32, 24); vl.setSpacing(16)
        # Title + body
        title_lbl = QLabel(s["title"]); title_lbl.setObjectName("PageTitle"); title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body_lbl = QLabel(s["body"]); body_lbl.setWordWrap(True); body_lbl.setObjectName("CaptionMuted")
        body_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
        # Progress indicator
        prog_lbl = QLabel(f"Step {step} of {len(steps) - 1}" if step > 0 else "")
        prog_lbl.setObjectName("CaptionMuted"); prog_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vl.addWidget(title_lbl); vl.addWidget(prog_lbl)
        vl.addSpacing(8); vl.addWidget(body_lbl); vl.addSpacing(8)
        # Buttons
        btn_row = QHBoxLayout()
        if step > 0:
            skip_btn = QPushButton("Skip Setup"); skip_btn.setObjectName("ShellBtn")
            skip_btn.clicked.connect(lambda: (dlg.accept(), self._complete_onboarding()))
            btn_row.addWidget(skip_btn)
        btn_row.addStretch()
        next_btn = QPushButton(s["btn"]); next_btn.setObjectName("BtnPrimary"); next_btn.setFixedHeight(36)
        def _on_next():
            dlg.accept()
            self._onboarding_step += 1
            if self._onboarding_step >= len(steps):
                self._complete_onboarding()
            else:
                self._render_onboarding_step()
        next_btn.clicked.connect(_on_next)
        btn_row.addWidget(next_btn)
        vl.addLayout(btn_row)
        dlg.exec()

    def _complete_onboarding(self, silent=False):
        """Mark onboarding as complete and persist."""
        try:
            prefs = load_app_prefs()
            prefs["onboarding_complete"] = True
            import json as _json
            p = _app_prefs_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                _json.dump(prefs, f, indent=2)
        except Exception as e:
            log.warning(f"Could not save onboarding state: {e}")
        if not silent:
            self.show_toast("Setup complete! You're ready to use VisionDock.")

    def page_cams(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(40, 40, 40, 40)
        l.setSpacing(0)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)
        pt = QLabel("Broadcasts")
        pt.setObjectName("PageTitle")
        title_block.addWidget(pt)
        ps = QLabel("Live preview, detection, snapshot, and record.")
        ps.setObjectName("PageSubtitle")
        ps.setWordWrap(True)
        title_block.addWidget(ps)
        l.addLayout(title_block)
        l.addSpacing(14)

        tools = QHBoxLayout()
        tools.setSpacing(12)
        self.cam_search = QLineEdit()
        self.cam_search.setPlaceholderText("Filter by camera name...")
        self.cam_search.setMinimumWidth(200)
        self.cam_search.setAccessibleName("Filter camera cards by name")
        self.cam_search.textChanged.connect(self.filter_cameras)
        cam_search_shell = SearchFieldShell(self.cam_search)
        cam_search_shell.setMinimumWidth(260)
        cam_search_shell.setMaximumWidth(440)
        tools.addWidget(cam_search_shell, 0)
        tools.addWidget(make_icon_refresh_button(self.refresh_cameras, "Reload cameras from database"))
        tools.addStretch(1)
        l.addLayout(tools)
        l.addSpacing(10)

        # --- Camera toolbar (RTSP builder + layout save/load) ---
        cam_toolbar = QHBoxLayout()
        cam_toolbar.setSpacing(8)
        _rtsp_btn = QPushButton("Build RTSP URL")
        _rtsp_btn.setObjectName("BtnSecondary")
        _rtsp_btn.setFixedHeight(28)
        _rtsp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _rtsp_btn.setToolTip("Build an RTSP stream URL from ZeroTier IP, port, and path components")
        _rtsp_btn.clicked.connect(self._show_rtsp_builder)
        cam_toolbar.addWidget(_rtsp_btn)
        cam_toolbar.addStretch(1)
        _save_layout_btn = QPushButton("Save Layout")
        _save_layout_btn.setObjectName("BtnSecondary")
        _save_layout_btn.setFixedHeight(28)
        _save_layout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _save_layout_btn.setToolTip("Save current camera card order as 'default' layout")
        _save_layout_btn.clicked.connect(lambda: self._save_cam_layout())
        cam_toolbar.addWidget(_save_layout_btn)
        _load_layout_btn = QPushButton("Load Layout")
        _load_layout_btn.setObjectName("BtnSecondary")
        _load_layout_btn.setFixedHeight(28)
        _load_layout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _load_layout_btn.setToolTip("Restore camera card order from saved 'default' layout")
        _load_layout_btn.clicked.connect(lambda: self._load_cam_layout())
        cam_toolbar.addWidget(_load_layout_btn)
        l.addLayout(cam_toolbar)
        l.addSpacing(8)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setStyleSheet("background: transparent; border: none;")
        self.cam_widget = QWidget()
        self.cf = FlowLayout(self.cam_widget)
        sa.setWidget(self.cam_widget)
        self.abc = self.create_add_btn(
            "New camera",
            self.modal_cam,
            "USB, CSI, RTSP, or file source.",
        )
        self.cf.addWidget(self.abc)
        polish_scroll_area(sa)
        l.addWidget(sa, 1)
        return w

    def page_docker(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(40, 40, 40, 40)
        l.setSpacing(0)

        title_block = QVBoxLayout()
        title_block.setSpacing(6)
        pt = QLabel("Workspaces")
        pt.setObjectName("PageTitle")
        title_block.addWidget(pt)
        ps = QLabel("Containers for AI and custom scripts.")
        ps.setObjectName("PageSubtitle")
        ps.setWordWrap(True)
        title_block.addWidget(ps)
        l.addLayout(title_block)
        l.addSpacing(14)

        tools = QHBoxLayout()
        tools.setSpacing(12)
        self.doc_search = QLineEdit()
        self.doc_search.setPlaceholderText("Filter by workspace name...")
        self.doc_search.setMinimumWidth(200)
        self.doc_search.setAccessibleName("Filter workspace cards by name")
        self.doc_search.textChanged.connect(self.filter_workspaces)
        doc_search_shell = SearchFieldShell(self.doc_search)
        doc_search_shell.setMinimumWidth(260)
        doc_search_shell.setMaximumWidth(440)
        tools.addWidget(doc_search_shell, 0)
        tools.addWidget(make_icon_refresh_button(self.refresh_ui, "Reload workspaces"))
        tools.addStretch(1)
        l.addLayout(tools)
        l.addSpacing(20)

        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setStyleSheet("background: transparent; border: none;")
        self.doc_widget = QWidget()
        self.df = FlowLayout(self.doc_widget)
        sa.setWidget(self.doc_widget)
        self.abd = self.create_add_btn(
            "New workspace",
            self.modal_doc,
            "Image template or folder mount.",
        )
        self.df.addWidget(self.abd)
        polish_scroll_area(sa)
        l.addWidget(sa, 1)
        return w

    def page_devices(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(40, 40, 40, 40)
        l.setSpacing(0)
        tb = QVBoxLayout()
        tb.setSpacing(6)
        pt = QLabel("Devices")
        pt.setObjectName("PageTitle")
        tb.addWidget(pt)
        ds = QLabel(
            "VisionDock’a katıldığınız ZeroTier ağının sanal IP aralığına (Ayarlar’daki ağ ataması ile aynı) göre cihazlar listelenir. "
            "Ham düğüm path ve relay adresleri yalnızca Ayarlar > ZeroTier bölümündedir. SSH şifresi oturum içindir."
        )
        ds.setObjectName("PageSubtitle")
        ds.setWordWrap(True)
        tb.addWidget(ds)
        l.addLayout(tb)
        l.addSpacing(10)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(make_icon_refresh_button(self.refresh_devices_page, "Refresh device list"))
        l.addLayout(h)
        l.addSpacing(16)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setStyleSheet("background: transparent; border: none;")
        dev_content = QWidget()
        dev_layout = QVBoxLayout(dev_content)
        dev_layout.setSpacing(16)
        dev_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        card = QFrame()
        card.setObjectName("InfoCard")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(22, 20, 22, 22)
        card_l.setSpacing(12)
        hdr = QHBoxLayout()
        sh = QLabel("Cihazlar")
        sh.setObjectName("SectionHeading")
        hdr.addWidget(sh)
        hdr.addStretch()
        add_dev_btn = QPushButton("Cihaz ekle")
        add_dev_btn.setObjectName("ShellBtn")
        add_dev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_dev_btn.clicked.connect(lambda: self._modal_add_device(None))
        hdr.addWidget(add_dev_btn)
        card_l.addLayout(hdr)
        self.dev_unified_host = QWidget()
        self.dev_unified_layout = QVBoxLayout(self.dev_unified_host)
        self.dev_unified_layout.setContentsMargins(0, 0, 0, 0)
        self.dev_unified_layout.setSpacing(10)
        card_l.addWidget(self.dev_unified_host)
        dev_layout.addWidget(card)
        ssh_trust = QLabel(
            "Şifreler kaydedilmez. Tam düğüm listesi Ayarlar sekmesindedir; bu tablo yalnızca aynı sanal ağ adreslerine uyan uçları gösterir. "
            "Eksik sanal IP için ⋯ veya Ayarlar’dan «ZT IP Al» kullanın."
        )
        ssh_trust.setObjectName("CaptionMuted")
        ssh_trust.setWordWrap(True)
        dev_layout.addWidget(ssh_trust)
        dev_layout.addStretch()
        sa.setWidget(dev_content)
        polish_scroll_area(sa)
        l.addWidget(sa)
        QTimer.singleShot(200, self.refresh_devices_page)
        self._zt_peer_refresh_timer = QTimer(w)
        self._zt_peer_refresh_timer.setInterval(30000)
        self._zt_peer_refresh_timer.timeout.connect(self.refresh_devices_page)
        self._zt_peer_refresh_timer.start()
        return w

    def _match_zt_peer_for_saved(self, name, host, leaf_peers):
        """Kayıtlı cihaz satırını listpeers kaydıyla eşleştir (path, kontrolör ZT IP, önbellek veya ZT- adı)."""
        h = (host or "").strip()
        for pr in leaf_peers:
            if (pr.get("ip") or "").strip() == h:
                return pr
            pals = pr.get("path_ips") or []
            if h in pals:
                return pr
            for z in pr.get("zt_from_controller") or []:
                if z == h:
                    return pr
            ck = self._zt_peer_cache_key(
                pr.get("address_full") or pr.get("address") or "",
                (pr.get("ip") or "").strip(),
            )
            if h in (self._zt_peer_cached_ips.get(ck) or []):
                return pr
        n = (name or "").strip()
        if n.upper().startswith("ZT-"):
            suf = n[3:].strip().lower()
            for pr in leaf_peers:
                addrf = (pr.get("address_full") or pr.get("address") or "").strip().lower()
                if addrf and (addrf.startswith(suf) or suf.startswith(addrf[: min(len(suf), len(addrf))])):
                    return pr
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
            return True, (msg[:900] if msg else "Komut gönderildi (log: /tmp/visiondock-stream.log)")
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
                "Yayına Ekle",
                "Önce bu cihaz için SSH oturumu açın («Bağlan…» veya ZeroTier satırında «Kaydet & Bağlan»).",
            )
            return
        client = self._ssh_sessions.get(ssh_session_key(ssh_host, ssh_user))
        zt_now = ssh_get_zerotier_ips(client) if client else []
        sip = zt_now[0] if zt_now else (stream_ip_hint or "").strip()
        lines = self._ssh_list_camera_lines(ssh_host, ssh_user)
        if not lines or (len(lines) == 1 and lines[0].lower().startswith("error")):
            QMessageBox.warning(
                self,
                "Kamera",
                "Uzak cihazda /dev/video veya v4l2 çıktısı alınamadı.\n\n"
                "• Kamera bağlı mı, başka süreç kullanıyor mu kontrol edin.\n"
                "• Jetson’da: ls /dev/video*  ve  v4l2-ctl --list-devices\n"
                "• RTSP için çoğu kurulumda mediamtx / jetson-utils veya ffmpeg gerekir.",
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Kamera — Yayına Ekle")
        dlg.setFixedWidth(540)
        dlg_l = QVBoxLayout(dlg)
        dlg_l.setContentsMargins(24, 22, 24, 22)
        dlg_l.setSpacing(14)
        info = QLabel(
            f"<b>{dev_name}</b> — yayın için kullanılacak IP: <b>{sip or '—'}</b><br>"
            "Kamera girişini seçin; gerekirse Jetson’da akış başlatma komutu çalıştırılır."
        )
        info.setWordWrap(True)
        dlg_l.addWidget(info)
        cam_combo = QComboBox()
        cam_combo.setFixedHeight(36)
        for ln in lines:
            cam_combo.addItem(ln[:80] + ("…" if len(ln) > 80 else ""), ln)
        dlg_l.addWidget(cam_combo)
        if cam_combo.count() == 0:
            dlg_l.addWidget(QLabel("Kamera satırı yok."))
            dlg.reject()
            return

        stream_mode_combo = QComboBox()
        stream_mode_combo.setFixedHeight(36)
        stream_mode_combo.addItem(
            "Jetson CSI — NVArgus + GStreamer → MJPEG (TCP) :5000 — önerilen",
            "csi_gst_http",
        )
        stream_mode_combo.addItem(
            "USB / UVC — FFmpeg + V4L2 YUYV → HTTP :5000 (CSI’da sık başarısız)",
            "usb_ffmpeg_http",
        )
        stream_mode_combo.addItem(
            "Uzak başlatma yok — yalnızca aşağıdaki URL (sunucu hazır)",
            "manual_url",
        )
        stream_mode_combo.setCurrentIndex(0)
        stream_mode_combo.setToolTip(
            "CSI yolu: libcamera/v4l2 RG10 (Ham Bayer) akışı için NVIDIA Argus kullanır; VisionDock’taki klasik "
            "YUYV+FFmpeg satırı bu donanımda güvenilir değildir. USB web kamerası için FFmpeg seçeneğini kullanın."
        )
        dlg_l.addWidget(stream_mode_combo)
        warn = QLabel(
            "<b>Connection refused</b>: 5000’de HTTP dinleyen yok (uzak komut çıkmış veya yanlış profil).<br>"
            "CSI sensörlerinde çoğunlukla yalnızca üstteki <b>NVArgus / GStreamer</b> profili mantıklıdır; "
            "YUYV+FFmpeg USB içindir.<br>"
            "Kamera meşgul: <code>fuser -v /dev/video0</code> · Log: <code>tail /tmp/visiondock-stream.log</code>"
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

        url_lbl = QLabel("Yayın URL’si")
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
                url_edit.setToolTip("Jetson’da hazır RTSP veya HTTP adresi (VLC / ffplay ile doğrulayın).")
                if not (url_edit.text() or "").strip() and sip:
                    url_edit.setText(f"rtsp://{sip}:8554/video{cam_idx_from_line(current_line())}")
            else:
                url_edit.setText(f"http://{sip}:5000/" if sip else "")
                url_edit.setReadOnly(True)
                if stream_mode_combo.currentData() == "csi_gst_http":
                    url_edit.setToolTip(
                        "Jetson’da GStreamer multipart MJPEG gönderir (tcpserversink :5000). "
                        "Adres http://… yazsa da VisionDock aynı portta TCP fallback dener."
                    )
                else:
                    url_edit.setToolTip(
                        "FFmpeg V4L2 YUYV Jetson’da :5000 açar; CSI kameralarda genelde uyumsuzdur."
                    )

        name_edit = QLineEdit(f"{dev_name} / {cam_idx_from_line(current_line())}")
        name_edit.setPlaceholderText("Yayın adı")
        dlg_l.addWidget(name_edit)

        def sync_name_from_cam(_=None):
            name_edit.setText(f"{dev_name} / {cam_idx_from_line(current_line())}")

        stream_mode_combo.currentIndexChanged.connect(sync_url_from_stream_mode)
        cam_combo.currentIndexChanged.connect(sync_name_from_cam)
        sync_url_from_stream_mode()

        mirror_name_edit = QLineEdit(f"{(dev_name or 'dev').lower().replace(' ', '_')}_mirror")
        mirror_name_edit.setPlaceholderText("Mirror adı (isteğe bağlı)")
        dlg_l.addWidget(mirror_name_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_b = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_b.setText("Yayına Ekle")
        ok_b.setObjectName("BtnPrimary")
        ok_b.setMinimumHeight(36)
        can_b = btns.button(QDialogButtonBox.StandardButton.Cancel)
        can_b.setText("İptal")
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
        if mode in ("csi_gst_http", "usb_ffmpeg_http"):
            dev_m = re.search(r"/dev/video\d+", line or "")
            dev_p = dev_m.group(0) if dev_m else f"/dev/video{cam_idx}"
            _http_port = 5000
            try:
                sens_id = min(max(int(cam_idx or "0"), 0), 7)
            except ValueError:
                sens_id = 0
            if mode == "csi_gst_http":
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
                remote_label = "Uzak GStreamer"
            else:
                # USB / UVC: klasik YUYV + FFmpeg; CSI RG10 sensörde güvenilir değildir — UI’da açıklandı
                inner = (
                    f"(command -v fuser >/dev/null 2>&1 && fuser -k {_http_port}/tcp 2>/dev/null); sleep 0.4; "
                    f"(command -v v4l2-ctl >/dev/null 2>&1 && "
                    f"v4l2-ctl -d {shlex.quote(dev_p)} "
                    f"--set-fmt-video=width=1280,height=720,pixelformat=YUYV 2>/dev/null); sleep 0.2; "
                    f"ffmpeg -loglevel warning -hide_banner -y -f v4l2 -thread_queue_size 4096 "
                    f"-input_format yuyv422 -video_size 1280x720 -framerate 20 "
                    f"-i {shlex.quote(dev_p)} -c:v mjpeg -q:v 5 "
                    f"-f mpjpeg -listen 1 -timeout 0 http://0.0.0.0:{_http_port}/"
                )
                remote_label = "Uzak FFmpeg"
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
                if mode == "csi_gst_http":
                    jetson_checks = (
                        "Jetson’da kontrol (NVArgus / GStreamer):\n"
                        "• tail -40 /tmp/visiondock-stream.log\n"
                        "• ss -lntp | grep :5000 (LISTEN görünmeli)\n"
                        "• gst-inspect-1.0 tcpserversink multipartmux nvjpegenc nvarguscamerasrc\n"
                        "• Log’da «syntax error» veya «unexpected token»: caps tırnağı eksik (uygulama güncel mi)\n"
                        "• CSI’da YUYV+FFmpeg profili çoğu donanımda 5000 açmaz — CSI profilinde kalın"
                    )
                else:
                    jetson_checks = (
                        "Jetson’da kontrol (FFmpeg / V4L2):\n"
                        "• tail -40 /tmp/visiondock-stream.log\n"
                        "• Kamera meşgul: sudo fuser -v " + dev_p + "\n"
                        "• v4l2-ctl -d " + dev_p + " --list-formats-ext\n"
                        "• Çıktı RG10 / Ham Bayer ise USB profili genelde işe yaramaz — CSI profiline geçin"
                    )
                if listening:
                    self.show_toast(
                        "Jetson’da :5000 dinleniyor (MJPEG); birkaç saniye içinde önizleme denenecek."
                    )
                    if suspicious:
                        QMessageBox.warning(
                            self,
                            remote_label,
                            "Port açık görünüyor ancak log’da hata izleri var. Sorun çıkarsa:\n" + jetson_checks,
                        )
                else:
                    QMessageBox.warning(
                        self,
                        remote_label,
                        "Jetson’da :5000 üzerinde dinleme yok; Mac’te «Connection refused» bu yüzdendir.\n\n"
                        + jetson_checks,
                    )
                    return
            else:
                QMessageBox.warning(self, "Uzak komut", detail[:500] if detail else "SSH komutu başarısız.")
                return
        url = url_edit.text().strip()
        cam_name = name_edit.text().strip() or f"{dev_name} cam"
        if not url:
            QMessageBox.warning(self, "Yayın", "Stream URL boş.")
            return
        self.add_cam_logic(cam_name, url, "Stream|STANDARD|AUTO")
        mirror_nm = mirror_name_edit.text().strip()
        if mirror_nm:
            self.add_cam_logic(mirror_nm, url, "Stream|STANDARD|AUTO")
            self.show_toast(f"Yayın eklendi: {cam_name}, {mirror_nm}")
        else:
            self.show_toast(f"Yayın eklendi: {cam_name}")
        self.switch(1)

    def _peer_row_yayina_ekle(self, path_ip: str, user_w: QLineEdit, node_addr: str, stream_guess: str):
        u = user_w.text().strip() or "jetson"
        if not path_ip:
            self.show_toast("Path IP yok")
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
            self.refresh_devices_page()
        zt_list = self._zt_peer_cached_ips.get(self._zt_peer_cache_key(node_addr, path_ip)) or []
        sip = zt_list[0] if zt_list else stream_guess
        label = f"ZT-{node_addr[:8]}" if node_addr and node_addr != "—" else path_ip
        self._show_yayina_ekle_camera_dialog(path_ip, u, label, sip)

    def refresh_devices_page(self):
        default_ssh_user = os.getenv("ZEROTIER_SSH_USER", "jetson")
        lay = self.dev_unified_layout
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        peers = get_zerotier_peers()
        leaf_peers = [p for p in peers if p.get("role") == "LEAF"]
        my_nid = get_zerotier_local_node_id()
        if my_nid:
            leaf_peers = [
                p
                for p in leaf_peers
                if (p.get("address_full") or p.get("address") or "").strip().lower() != my_nid
            ]
        n_deduped = self._dedupe_devices_by_zt_node(leaf_peers)
        if n_deduped:
            self.show_toast(f"{n_deduped} yinelenen cihaz kaydı ZT düğümüne göre birleştirildi.")
        devices_list = self.db.get_devices()
        zt_local = set(get_zerotier_local_ips())
        zt_prefs = get_zerotier_managed_ipv4_prefixes()
        leaf_peers_devices = [p for p in leaf_peers if self._peer_visible_on_devices_page(p, zt_prefs)]
        banner = QLabel(
            "Bu sayfa, Ayarlar’daki VisionDock ağı ile aynı sanal IP önekine sahip uçları ve kayıtlı cihazları gösterir. "
            "Tüm LEAF düğümler ve ham path adresleri yalnızca Ayarlar > ZeroTier listesindedir."
        )
        banner.setObjectName("CaptionMuted")
        banner.setWordWrap(True)
        lay.addWidget(banner)
        head = QWidget()
        hh = QHBoxLayout(head)
        hh.setContentsMargins(4, 0, 4, 6)
        for txt, mw in [
            ("Cihaz", 120),
            ("Ağ IP", 140),
            ("SSH", 72),
            ("Durum", 70),
        ]:
            lb = QLabel(txt)
            lb.setObjectName("CaptionMutedSm")
            lb.setMinimumWidth(mw)
            hh.addWidget(lb)
        lb_act = QLabel("Eylemler")
        lb_act.setObjectName("CaptionMutedSm")
        hh.addWidget(lb_act, 1)
        lay.addWidget(head)
        matched_node_addrs = set()
        for name, host, user, key_path in devices_list:
            if (host or "").strip() in zt_local:
                continue
            pr = self._match_zt_peer_for_saved(name, host, leaf_peers)
            if pr:
                aid = (pr.get("address_full") or pr.get("address") or "").strip().lower()
                if aid:
                    matched_node_addrs.add(aid)
            u = user or default_ssh_user
            if self._is_ssh_device_connected(host, u):
                rid = self._ensure_remote_zt_node_id(host, u)
                if rid:
                    matched_node_addrs.add(rid.lower())

        def add_device_row(row_cols, actions_layout):
            row = surface_row()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 8, 8, 8)
            rl.setSpacing(10)
            for w in row_cols:
                rl.addWidget(w, 0)
            rl.addLayout(actions_layout, 1)
            lay.addWidget(row)

        rows_placed = 0
        for name, host, user, key_path in devices_list:
            if (host or "").strip() in zt_local:
                continue
            u = user or default_ssh_user
            pr = self._match_zt_peer_for_saved(name, host, leaf_peers)
            path_ip = (pr.get("ip") or "").strip() if pr else ""
            node_disp = (pr.get("address") or "—") if pr else "—"
            node_id_full = ((pr.get("address_full") or pr.get("address") or "").strip() if pr else "")
            ssh_key = ssh_session_key(host, u)
            connected = self._is_ssh_device_connected(host, u)
            ssh_client = self._ssh_sessions.get(ssh_key)
            zt_live = ssh_get_zerotier_ips(ssh_client) if (connected and ssh_client) else []
            zt_ctrl = (pr.get("zt_from_controller") or []) if pr else []
            ck = (
                self._zt_peer_cache_key(node_id_full or node_disp, path_ip)
                if pr and path_ip
                else self._zt_peer_cache_key("", host)
            )
            zt_cached = self._zt_peer_cached_ips.get(ck) or []
            zt_bits = []
            zt_bits.extend(zt_ctrl or [])
            zt_bits.extend(zt_cached or [])
            zt_bits.extend(zt_live or [])
            seen_z = []
            for z in zt_bits:
                if z and z not in seen_z:
                    seen_z.append(z)
            seen_z = _sort_zt_ips_display_order(seen_z, zt_prefs)
            zt_cell = " ".join(seen_z) if seen_z else "—"
            display_host = self._devices_page_managed_display_ip(host, seen_z, path_ip, zt_prefs)
            relay_saved = (
                self._peer_ssh_relay_ip(pr, display_host, zt_prefs)
                if pr
                else (host or path_ip or "").strip()
            )
            hst = (host or "").strip()
            if not ip_matches_zt_managed_prefixes(display_host, zt_prefs) and not ip_matches_zt_managed_prefixes(
                hst, zt_prefs
            ):
                continue
            c_src = QLabel(name or "—")
            c_src.setObjectName("ListPrimary")
            c_src.setMinimumWidth(120)
            c_path = QLabel(display_host or "—")
            c_path.setObjectName("MonoMuted")
            c_path.setMinimumWidth(140)
            c_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            tip_p = []
            if node_id_full:
                tip_p.append(f"Node: {node_id_full}")
            if seen_z:
                tip_p.append(f"Sanal IP: {' '.join(seen_z)}")
            if relay_saved and relay_saved != display_host:
                tip_p.append(f"SSH path: {relay_saved}")
            elif zt_cell != "—" and not seen_z:
                tip_p.append(f"Sanal IP: {zt_cell}")
            c_path.setToolTip("\n".join(tip_p) if tip_p else "VisionDock ağı sanal IP (Ayarlar’daki önek ile aynı)")
            c_user = QLabel(u)
            c_user.setMinimumWidth(72)
            st = QLabel("Bağlı" if connected else "Kapalı")
            st.setObjectName("DeviceConnectedBadge" if connected else "DeviceOfflineBadge")
            st.setMinimumWidth(70)
            btns = QHBoxLayout()
            btns.setSpacing(6)
            if connected:
                dcb = QPushButton("Kes")
                dcb.setObjectName("BtnSecondary")
                dcb.setFixedHeight(28)
                dcb.clicked.connect(
                    lambda _, h=host, us=u: (self._ssh_disconnect_device(h, us), self.refresh_devices_page())
                )
                btns.addWidget(dcb)
            else:
                cob = QPushButton("Bağlan")
                cob.setObjectName("BtnPrimary")
                cob.setFixedHeight(28)
                cob.clicked.connect(lambda _, h=host, us=u: self._ssh_show_connect_dialog(h, us))
                btns.addWidget(cob)
            stream_ip = seen_z[0] if seen_z else host
            yb = QPushButton("Yayına Ekle")
            yb.setObjectName("BtnPrimary")
            yb.setFixedHeight(28)
            yb.setEnabled(connected)
            yb.clicked.connect(
                lambda _=False, h=host, us=u, nm=name or "Device", sp=stream_ip: self._show_yayina_ekle_camera_dialog(
                    h, us, nm, sp
                )
            )
            btns.addWidget(yb)
            ztip_target = relay_saved or host
            more = QToolButton()
            more.setText("⋯")
            more.setObjectName("BtnSecondary")
            more.setFixedSize(30, 28)
            more.setArrowType(Qt.ArrowType.NoArrow)
            more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            more.setStyleSheet(QTOOLBTN_HIDE_MENU_ARROW)
            more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            m = QMenu(self)
            _nid = node_id_full if node_id_full else (node_disp if node_disp != "—" else "")
            _zt = ztip_target
            _ur = u
            m.addAction("ZT sanal IP al…").triggered.connect(
                lambda _zt=_zt, _ur=_ur, _nid=_nid: self._fetch_peer_zt_virtual_ips(_zt, _ur, _nid)
            )
            act_dh = m.addAction("Docker host (ZT)…")
            act_dh.setEnabled(bool(seen_z))
            _z0 = seen_z[0] if seen_z else ""
            act_dh.triggered.connect(lambda _z=_z0: self._use_zt_virtual_ip_as_remote(_z))

            def _copy_saved():
                cmd = (
                    f"ssh -i {key_path} {u}@{host}"
                    if (key_path and os.path.exists(os.path.expanduser(key_path)))
                    else f"ssh {u}@{host}"
                )
                QApplication.clipboard().setText(cmd)
                self.show_toast("SSH kopyalandı")

            m.addSeparator()
            m.addAction("SSH kopyala").triggered.connect(_copy_saved)
            m.addAction("Terminal aç").triggered.connect(
                lambda: (open_ssh_in_terminal(u, host, key_path), self.show_toast("Terminal"))
            )
            m.addSeparator()
            m.addAction("Düzenle").triggered.connect(
                lambda: self._modal_add_device((name, host, u or "", key_path or ""))
            )

            def _dodel():
                self._ssh_disconnect_device(host, u)
                self.db.remove_device(host)
                self.refresh_devices_page()
                self.show_toast("Cihaz silindi")

            m.addAction("Sil").triggered.connect(_dodel)
            more.setMenu(m)
            btns.addWidget(more)
            add_device_row([c_src, c_path, c_user, st], btns)
            rows_placed += 1

        for pr in leaf_peers_devices:
            node_short = pr.get("address") or "—"
            node_full = (pr.get("address_full") or pr.get("address") or "").strip()
            if node_full.lower() in matched_node_addrs:
                continue
            ip = (pr.get("ip") or "").strip()
            zt_ctrl = pr.get("zt_from_controller") or []
            ck = self._zt_peer_cache_key(node_full or node_short, ip)
            zt_cached = self._zt_peer_cached_ips.get(ck) or []
            seen = []
            for z in list(zt_ctrl) + list(zt_cached):
                if z and z not in seen:
                    seen.append(z)
            seen = _sort_zt_ips_display_order(seen, zt_prefs)
            display_ip = self._devices_page_managed_display_ip("", seen, ip, zt_prefs)
            ssh_use = self._peer_ssh_relay_ip(pr, display_ip, zt_prefs) or display_ip or ip
            c_src = QLabel(f"Düğüm · {node_short}")
            c_src.setObjectName("CaptionMutedSm")
            c_src.setMinimumWidth(120)
            c_src.setToolTip("Kayıtlı değil — «Kaydet & Bağlan» ile listeye ekleyin.")
            c_path = QLabel(display_ip or "—")
            c_path.setObjectName("MonoIp")
            c_path.setMinimumWidth(140)
            c_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            t_lines = [f"Node: {node_full}"]
            if seen:
                t_lines.append(f"ZT: {' '.join(seen)}")
            if ssh_use and ssh_use != display_ip:
                t_lines.append(f"SSH path: {ssh_use}")
            c_path.setToolTip("\n".join(t_lines))
            user_edit = QLineEdit()
            user_edit.setPlaceholderText(default_ssh_user)
            user_edit.setMaximumWidth(88)
            user_edit.setFixedHeight(26)
            st = QLabel("Kayıtsız")
            st.setObjectName("CaptionMuted")
            st.setMinimumWidth(70)
            btns = QHBoxLayout()
            btns.setSpacing(6)

            def _copy_zt(su=ssh_use, ue=user_edit, d_u=default_ssh_user):
                usr = ue.text().strip() or d_u
                if su:
                    QApplication.clipboard().setText(f"ssh {usr}@{su}")
                    self.show_toast("SSH kopyalandı")

            def _term_zt(su=ssh_use, ue=user_edit, d_u=default_ssh_user):
                usr = ue.text().strip() or d_u
                if su:
                    open_ssh_in_terminal(usr, su)
                    self.show_toast("Terminal")

            def _save_conn(
                su=ssh_use,
                di=display_ip,
                sn=seen,
                ipx=ip,
                nf=node_full,
                ue=user_edit,
                d_u=default_ssh_user,
            ):
                if not su and not sn:
                    self.show_toast("Bağlantı adresi yok")
                    return
                usr = ue.text().strip() or d_u
                save_host = (di or (sn[0] if sn else "") or ipx).strip()
                if not save_host:
                    save_host = (su or "").strip()
                if not save_host:
                    self.show_toast("Kaydedilecek IP yok")
                    return
                if nf and nf not in ("—", ""):
                    dev_name = f"ZT-{nf[:8]}"
                else:
                    dev_name = f"ZT-{save_host.replace('.', '-')}"
                existing = [
                    d
                    for d in self.db.get_devices()
                    if d[1] == save_host or (ipx and d[1] == ipx) or (su and d[1] == su)
                ]
                if not existing:
                    self.db.save_device(dev_name, save_host, usr)
                    self.show_toast(f"Eklendi: {dev_name}")
                else:
                    self.show_toast(f"Kayıtlı: {existing[0][0]}")
                self._ssh_show_connect_dialog(save_host, usr, node_id=nf)

            save_conn_btn = QPushButton("Kaydet & Bağlan")
            save_conn_btn.setObjectName("BtnPrimary")
            save_conn_btn.setFixedHeight(28)
            save_conn_btn.setEnabled(bool(ssh_use or seen))
            save_conn_btn.clicked.connect(_save_conn)
            btns.addWidget(save_conn_btn)
            sg = display_ip or (seen[0] if seen else ip)
            ypb = QPushButton("Yayına Ekle")
            ypb.setObjectName("BtnPrimary")
            ypb.setFixedHeight(28)
            ypb.setEnabled(bool(ssh_use))
            ypb.clicked.connect(
                lambda _=False, su=ssh_use, ue=user_edit, nf=node_full, sgx=sg: self._peer_row_yayina_ekle(
                    su, ue, nf, sgx
                )
            )
            btns.addWidget(ypb)
            more_p = QToolButton()
            more_p.setText("⋯")
            more_p.setObjectName("BtnSecondary")
            more_p.setFixedSize(30, 28)
            more_p.setArrowType(Qt.ArrowType.NoArrow)
            more_p.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            more_p.setStyleSheet(QTOOLBTN_HIDE_MENU_ARROW)
            more_p.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            mp = QMenu(self)
            mp.addAction("ZT sanal IP al…").triggered.connect(
                lambda su=ssh_use, ue=user_edit, d_u=default_ssh_user, nf=node_full: self._fetch_peer_zt_virtual_ips(
                    su, ue.text().strip() or d_u, nf
                )
            )
            mp.addAction("SSH kopyala").triggered.connect(_copy_zt)
            mp.addAction("Terminal aç").triggered.connect(_term_zt)
            if seen:
                mp.addSeparator()
                zt0 = seen[0] if seen else ""
                mp.addAction("Docker host (ZT)…").triggered.connect(
                    lambda z=zt0: self._use_zt_virtual_ip_as_remote(z)
                )
            more_p.setMenu(mp)
            btns.addWidget(more_p)
            add_device_row([c_src, c_path, user_edit, st], btns)
            rows_placed += 1

        if rows_placed == 0:
            ef = QFrame()
            ef.setObjectName("SurfaceRow")
            ev = QVBoxLayout(ef)
            et = QLabel("Bu ağın sanal adres aralığında listelenecek uç veya kayıtlı cihaz yok.")
            et.setObjectName("EmptyStateTitle")
            et.setAlignment(Qt.AlignmentFlag.AlignCenter)
            es = QLabel(
                "«Cihaz ekle» ile kaydedin veya Ayarlar > ZeroTier’dan düğümlere «ZT IP Al» ile "
                "sanal IP alındıktan sonra burada görünürler. Ham path listesi yalnızca Ayarlar’dadır.\n\n"
                "Yalnızca yönetilen sanal IP’si bu öneğe uyan (ör. 192.168.192.x) kayıtlar gösterilir; "
                "eski path-only kayıtları burada gizlenir, veritabanından silebilir veya birleştirebilirsiniz."
            )
            es.setObjectName("EmptyStateSubtitle")
            es.setAlignment(Qt.AlignmentFlag.AlignCenter)
            es.setWordWrap(True)
            ev.addWidget(et)
            ev.addWidget(es)
            lay.addWidget(ef)

    def _modal_add_device(self, existing=None):
        if existing is not None and (not isinstance(existing, (tuple, list)) or len(existing) != 4):
            existing = None
        ov = QWidget(self); ov.setObjectName("Overlay"); ov.setGeometry(0, 0, self.width(), self.height())
        sl = QVBoxLayout(ov); sl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box = QFrame(); box.setObjectName("ModalBox"); box.setFixedWidth(420); bl = QVBoxLayout(box); bl.setSpacing(16)
        is_edit = existing is not None
        title = "Edit device" if is_edit else "Add device"
        bl.addWidget(QLabel(title, styleSheet="font-size:18px; font-weight:800; border:none;"))
        name_in = QLineEdit(); name_in.setPlaceholderText("Name (e.g. Jetson-Cam)"); bl.addWidget(name_in)
        host_in = QLineEdit(); host_in.setPlaceholderText("Host / IP"); bl.addWidget(host_in)
        user_in = QLineEdit(); user_in.setPlaceholderText("SSH user (default: jetson)"); user_in.setText("jetson"); bl.addWidget(user_in)
        key_in = QLineEdit(); key_in.setPlaceholderText("SSH private key path (optional, e.g. ~/.ssh/visiondock_zt)"); bl.addWidget(key_in)
        if is_edit:
            name_in.setText(existing[0] or ""); host_in.setText(existing[1] or ""); user_in.setText(existing[2] or "jetson"); key_in.setText(existing[3] or "")
        def save_dev():
            host = host_in.text().strip()
            if not host: QMessageBox.warning(self, "Device", "Enter a host."); return
            if is_edit:
                old_host = existing[1]
                self.db.update_device(old_host, name_in.text().strip(), host, user_in.text().strip() or "jetson", key_in.text().strip() or None)
                self.refresh_devices_page(); ov.deleteLater(); self.show_toast("Device updated")
            else:
                self.db.save_device(name_in.text().strip(), host, user_in.text().strip() or "jetson", key_in.text().strip() or None)
                self.refresh_devices_page(); ov.deleteLater(); self.show_toast("Device added")
        btn_hl = QHBoxLayout()
        can_btn = QPushButton("Cancel"); can_btn.setObjectName("BtnSecondary"); can_btn.clicked.connect(ov.deleteLater); btn_hl.addWidget(can_btn)
        btn_hl.addStretch()
        btn = QPushButton("Save"); btn.setObjectName("BtnPrimary"); btn.clicked.connect(save_dev); btn_hl.addWidget(btn)
        bl.addLayout(btn_hl)
        sl.addWidget(box)
        overlay_style = "background: rgba(0,0,0,0.6);" if getattr(self, "is_dark", True) else "background: rgba(255,255,255,0.5);"
        ov.setStyleSheet(f"QWidget#Overlay {{ {overlay_style} }}")
        ov.raise_(); ov.show()

    def _run_ssh_keygen(self):
        if getattr(self, "_production_mode", False):
            self.show_toast("SSH key generation is disabled in production mode.")
            log.info("SSH keygen skipped (production mode)")
            return
        key_path = os.path.join(os.path.expanduser("~"), ".ssh", "visiondock_zt")
        ssh_dir = os.path.dirname(key_path)
        os.makedirs(ssh_dir, exist_ok=True)
        if os.path.exists(key_path):
            self.show_toast(f"Key already exists: {key_path}")
            return
        try:
            subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", key_path, "-N", ""], check=True, capture_output=True, timeout=15)
            QApplication.clipboard().setText(key_path)
            self.show_toast(f"Key created; path copied: {key_path}. Add to device: ssh-copy-id -i {key_path}.pub user@host")
        except FileNotFoundError:
            QMessageBox.information(self, "SSH key", "ssh-keygen not found. Install OpenSSH on this system.")
        except subprocess.CalledProcessError as e:
            QMessageBox.warning(self, "SSH key", f"ssh-keygen error: {e.stderr.decode() if e.stderr else e}")

    def _is_ssh_device_connected(self, host, user):
        key = ssh_session_key(host, user)
        client = self._ssh_sessions.get(key)
        if client is None:
            return False
        try:
            t = client.get_transport()
            return t is not None and t.is_active()
        except Exception:
            return False

    def _ssh_disconnect_device(self, host, user):
        key = ssh_session_key(host, user)
        self._remote_zt_node_by_session.pop(key, None)
        old = self._ssh_sessions.pop(key, None)
        if old is not None:
            self.notify_info(f"SSH disconnected: {host}")
            try:
                old.close()
            except Exception:
                pass

    def _zt_peer_cache_key(self, node_id, path_ip: str) -> str:
        nid = (node_id or "").strip()
        if nid and nid != "—":
            return nid
        return f"path:{(path_ip or '').strip()}"

    def _ensure_remote_zt_node_id(self, host, user) -> str:
        """Bağlı oturumda uzak `zerotier-cli info` ile düğüm ID; tabloda peer yinelemesini önlemek için."""
        key = ssh_session_key(host, user)
        if key in self._remote_zt_node_by_session:
            return self._remote_zt_node_by_session[key]
        client = self._ssh_sessions.get(key)
        if not client:
            return ""
        try:
            out, _, _ = ssh_exec_text(client, "zerotier-cli info", timeout=10)
            nid = parse_zerotier_info_output(out or "")
            if nid:
                self._remote_zt_node_by_session[key] = nid
                return nid
        except Exception:
            pass
        return ""

    def _use_zt_virtual_ip_as_remote(self, zt_ip: str):
        """Docker/ayarlar için uzak host olarak ZT sanal IP'yi kaydet ve Settings sekmesine geç."""
        zt_ip = (zt_ip or "").strip()
        if not zt_ip:
            return
        save_app_prefs_remote_host(zt_ip)
        if getattr(self, "node_ip", None):
            self.node_ip.setText(zt_ip)
        DockerManager.set_host(zt_ip)
        self.switch(5)
        self.show_toast(f"Remote host (ZT) ayarlandı: {zt_ip}")

    def _ssh_password_dialog(self, host, user, node_id=None):
        """SSH şifre diyaloğu. İptal veya geçersiz hostta None; aksi halde (kullanıcı, şifre) döner."""
        if not (host or "").strip():
            QMessageBox.warning(self, "SSH", "Host boş — geçerli bir IP girin.")
            return None
        u = (user or "jetson").strip()
        dlg = QDialog(self)
        dlg.setWindowTitle("SSH Bağlantısı")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        pal = ThemeOps.palette(self.is_dark)
        dlg.setStyleSheet(
            f"QDialog {{ background-color: {pal['card']}; color: {pal['txt']}; }}"
            f"QLabel {{ color: {pal['txt']}; border: none; background: transparent; }}"
        )
        fl = QFormLayout(dlg)
        fl.setSpacing(14)
        fl.setContentsMargins(28, 24, 28, 24)
        ht = QLabel((host or "").strip())
        ht.setObjectName("MonoIp")
        ht.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fl.addRow("IP Adresi:", ht)
        if node_id and node_id not in ("—", "", None):
            nid_lbl = QLabel(node_id)
            nid_lbl.setObjectName("MonoMuted")
            nid_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            fl.addRow("ZT Node ID:", nid_lbl)
        user_edit = QLineEdit(u)
        user_edit.setPlaceholderText("jetson")
        user_edit.setToolTip("SSH bağlantısı için kullanıcı adı (değiştirilebilir)")
        fl.addRow("Kullanıcı:", user_edit)
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        pw.setPlaceholderText("SSH şifresi")
        pw.returnPressed.connect(dlg.accept)
        fl.addRow("Şifre:", pw)
        hint = QLabel(
            "Şifre yalnızca bu oturum için kullanılır ve kaydedilmez.\n"
            "Bilinmeyen host anahtarları güvenilir ağlarda (ZeroTier) otomatik kabul edilir."
        )
        hint.setObjectName("CaptionMuted")
        hint.setWordWrap(True)
        fl.addRow(hint)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Bağlan")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("İptal")
        fl.addRow(bb)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        final_user = user_edit.text().strip() or "jetson"
        return (final_user, pw.text())

    def _fetch_peer_zt_virtual_ips(self, path_ip: str, user: str, node_id: str, *, goto_devices: bool = False):
        """Path IP üzerinden SSH ile uzakta zerotier-cli çalıştırıp sanal IP'leri önbelleğe yazar."""
        if not (path_ip or "").strip():
            self.show_toast("IP bulunamadı — cihaz aktif bir path'e sahip değil")
            return
        path_ip = path_ip.strip()
        u = (user or "").strip() or "jetson"
        cache_key = self._zt_peer_cache_key(node_id, path_ip)

        def _after_fetch(ips: list, empty_msg: str):
            self.refresh_devices_page()
            if goto_devices:
                self.switch(3)
            if ips:
                self.show_toast(f"ZT sanal IP: {', '.join(ips)}")
            else:
                self.show_toast(empty_msg)

        if self._is_ssh_device_connected(path_ip, u):
            client = self._ssh_sessions.get(ssh_session_key(path_ip, u))
            if client:
                ips = ssh_get_zerotier_ips(client)
                self._zt_peer_cached_ips[cache_key] = ips
                _after_fetch(ips, "Uzak cihazda ZT adresi bulunamadı (zerotier-cli / ağ?)")
            return
        creds = self._ssh_password_dialog(path_ip, u, node_id)
        if creds is None:
            return
        final_user, password = creds
        self.show_toast(f"ZT IP'leri alınıyor: {final_user}@{path_ip}…")
        client, err = ssh_connect_with_password(path_ip, final_user, password)
        if err:
            QMessageBox.warning(self, "SSH bağlantısı başarısız", err)
            self.notify_error(f"SSH hatası: {path_ip} — {err[:80]}")
            return
        try:
            ips = ssh_get_zerotier_ips(client)
        finally:
            try:
                client.close()
            except Exception:
                pass
        self._zt_peer_cached_ips[cache_key] = ips
        _after_fetch(ips, "ZT adresi alınamadı — cihazda ZeroTier kurulu mu?")

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
        self.show_toast(f"Bağlanılıyor: {final_user}@{host.strip()}…")
        client, err = ssh_connect_with_password(host.strip(), final_user, password)
        if err:
            QMessageBox.warning(self, "SSH bağlantısı başarısız", err)
            self.notify_error(f"SSH hatası: {host} — {err[:80]}")
            return
        self._ssh_disconnect_device(host, final_user)
        self._ssh_sessions[ssh_session_key(host, final_user)] = client
        self.active_remote_host = host
        if hasattr(self, "node_ip"):
            self.node_ip.setText(host)
        DockerManager.set_host(host)
        self.show_toast(f"Bağlantı kuruldu: {final_user}@{host}")
        self.notify_success(f"SSH bağlandı: {host}")
        self.refresh_devices_page()

    def page_settings(self):
        sw = QScrollArea(); sw.setWidgetResizable(True); sw.setStyleSheet("background: transparent; border: none;")
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(40,40,40,40); l.setSpacing(40)
        sw.setWidget(w); polish_scroll_area(sw)
        
        # Section 1: Metrics
        l.addWidget(settings_title("Live system metrics"))
        metrics_card = QFrame()
        metrics_card.setObjectName("InfoCard")
        # Hidden node_ip for legacy logic compatibility
        self.node_ip = QLineEdit(); self.node_ip.hide()
        env_r = (os.getenv("JETSON_REMOTE") or "").strip()
        file_r = (load_app_prefs().get("remote_host_ip") or "").strip()
        self.node_ip.setText(env_r or file_r or "")
        ml = QHBoxLayout(metrics_card)
        ml.setSpacing(28)
        ml.setContentsMargins(28, 28, 28, 28)
        self.charts = [DonutChart("CPU","#EF4444"), DonutChart("RAM","#10B981"), DonutChart("Disk","#3B82F6"), DonutChart("GPU","#8B5CF6")]
        for ch in self.charts:
            ml.addWidget(ch)
        l.addWidget(metrics_card)

        # Section 2: Hardware Inventory
        l.addWidget(settings_title("Hardware summary"))
        hw = QFrame(); hw.setObjectName("InfoCard"); hl = QGridLayout(hw); hl.setContentsMargins(25,25,25,25); hl.setSpacing(30)
        inf = [("Architecture", platform.machine()), ("CPU cores", str(psutil.cpu_count())), ("OS", platform.system()), ("Graphics", get_gpu_info())]
        for i, (k, v) in enumerate(inf):
            lv = QVBoxLayout()
            lk = QLabel(k)
            lk.setObjectName("CaptionMutedSm")
            lv.addWidget(lk)
            vv = QLabel(v)
            vv.setObjectName("HWValue")
            lv.addWidget(vv)
            hl.addLayout(lv, 0, i)
        l.addWidget(hw)

        # B4 — ZeroTier Network Management card
        l.addWidget(settings_title("System health & ZeroTier management"))
        zt_mgmt_card = QFrame(); zt_mgmt_card.setObjectName("InfoCard")
        ztm_l = QVBoxLayout(zt_mgmt_card); ztm_l.setContentsMargins(25, 20, 25, 20); ztm_l.setSpacing(12)
        ztm_title = QLabel("Join / Leave a ZeroTier network")
        ztm_title.setObjectName("SectionHeading")
        ztm_l.addWidget(ztm_title)
        ztm_hint = QLabel(
            "Enter a 16-character Network ID and click Join. "
            "The device must be Authorized in ZeroTier Central before traffic flows."
        )
        ztm_hint.setObjectName("CaptionMuted"); ztm_hint.setWordWrap(True)
        ztm_l.addWidget(ztm_hint)
        ztm_row = QHBoxLayout(); ztm_row.setSpacing(8)
        self._zt_nwid_input = QLineEdit()
        self._zt_nwid_input.setPlaceholderText("Network ID  (e.g. 8056c2e21c000001)")
        self._zt_nwid_input.setMaxLength(16)
        self._zt_nwid_input.setObjectName("SettingsInput")
        self._zt_nwid_input.setFixedHeight(34)
        self._zt_nwid_input.setToolTip(
            "16-character ZeroTier Network ID.\n"
            "Find it at: my.zerotier.com → Networks → your network."
        )
        ztm_row.addWidget(self._zt_nwid_input, 1)
        _zt_join_btn = QPushButton("Join")
        _zt_join_btn.setObjectName("PrimaryBtn")
        _zt_join_btn.setFixedHeight(34)
        _zt_join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _zt_join_btn.clicked.connect(self._zt_join_network)
        ztm_row.addWidget(_zt_join_btn)
        _zt_leave_btn = QPushButton("Leave")
        _zt_leave_btn.setObjectName("DangerBtn")
        _zt_leave_btn.setFixedHeight(34)
        _zt_leave_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _zt_leave_btn.clicked.connect(self._zt_leave_network)
        ztm_row.addWidget(_zt_leave_btn)
        ztm_l.addLayout(ztm_row)
        _zt_central_lbl = QLabel('<a href="https://my.zerotier.com">Open ZeroTier Central ↗</a>')
        _zt_central_lbl.setObjectName("CaptionMuted")
        _zt_central_lbl.setOpenExternalLinks(True)
        ztm_l.addWidget(_zt_central_lbl)
        l.addWidget(zt_mgmt_card)

        # Settings backup / restore actions
        backup_card = QFrame(); backup_card.setObjectName("InfoCard")
        bcl = QVBoxLayout(backup_card); bcl.setContentsMargins(25, 20, 25, 20); bcl.setSpacing(10)
        btitle = QLabel("Settings backup")
        btitle.setObjectName("SectionHeading")
        bcl.addWidget(btitle)
        bhint = QLabel("Export current app settings to JSON and import later for restore/migration.")
        bhint.setObjectName("CaptionMuted")
        bhint.setWordWrap(True)
        bcl.addWidget(bhint)
        brow = QHBoxLayout(); brow.setSpacing(10)
        bex = QPushButton("Export Settings")
        bex.setObjectName("BtnSecondary")
        bex.setFixedHeight(32)
        bex.setCursor(Qt.CursorShape.PointingHandCursor)
        bex.clicked.connect(self.export_settings_json)
        bim = QPushButton("Import Settings")
        bim.setObjectName("BtnSecondary")
        bim.setFixedHeight(32)
        bim.setCursor(Qt.CursorShape.PointingHandCursor)
        bim.clicked.connect(self.import_settings_json)
        brow.addWidget(bex); brow.addWidget(bim); brow.addStretch()
        bcl.addLayout(brow)
        l.addWidget(backup_card)

        # Notifications history panel
        notif_card = QFrame(); notif_card.setObjectName("InfoCard")
        ncl = QVBoxLayout(notif_card); ncl.setContentsMargins(25, 20, 25, 20); ncl.setSpacing(10)
        ntitle = QLabel("Notifications")
        ntitle.setObjectName("SectionHeading")
        ncl.addWidget(ntitle)
        self._notif_list_box = QVBoxLayout()
        self._notif_list_box.setSpacing(6)
        ncl.addLayout(self._notif_list_box)
        nrow = QHBoxLayout()
        nclear = QPushButton("Clear")
        nclear.setObjectName("BtnSecondary")
        nclear.setFixedHeight(28)
        nclear.setCursor(Qt.CursorShape.PointingHandCursor)
        nclear.clicked.connect(lambda: [setattr(self, "_notifications", []), self._refresh_notifications_panel()])
        nrow.addWidget(nclear); nrow.addStretch()
        ncl.addLayout(nrow)
        l.addWidget(notif_card)
        self._refresh_notifications_panel()

        sys_info = QFrame(); sys_info.setObjectName("InfoCard"); sl_info = QVBoxLayout(sys_info); sl_info.setContentsMargins(25,25,25,25); sl_info.setSpacing(15)
        
        def add_status(lbl, val):
            row = QHBoxLayout(); row.setSpacing(24)
            ll = QLabel(lbl); ll.setObjectName("FormLabel")
            ll.setMinimumWidth(160)
            row.addWidget(ll)
            vl = QLabel(val)
            _ac = "#3B82F6" if getattr(self, "is_dark", True) else "#2563EB"
            vl.setStyleSheet(f"color: {_ac}; font-size: 13px; font-weight: 700; border: none;")
            row.addWidget(vl)
            row.addStretch()
            sl_info.addLayout(row)

        _docker_running = DockerManager.is_running()
        _docker_row = QHBoxLayout(); _docker_row.setSpacing(24)
        _docker_lbl = QLabel("Docker engine"); _docker_lbl.setObjectName("FormLabel"); _docker_lbl.setMinimumWidth(160)
        _docker_row.addWidget(_docker_lbl)
        self._docker_status_lbl = QLabel("ACTIVE" if _docker_running else "OFFLINE")
        self._docker_status_lbl.setStyleSheet(f"color: {'#30D158' if _docker_running else '#FF453A'}; font-size: 13px; font-weight: 700; border: none;")
        _docker_row.addWidget(self._docker_status_lbl)
        self._docker_start_btn = QPushButton("Start Docker")
        self._docker_start_btn.setObjectName("BtnSecondary"); self._docker_start_btn.setFixedHeight(28); self._docker_start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._docker_start_btn.setVisible(not _docker_running)
        self._docker_start_btn.clicked.connect(lambda: [DockerManager.start_service(), self.show_toast("Starting Docker…"), QTimer.singleShot(4500, self._refresh_docker_status)])
        _docker_row.addWidget(self._docker_start_btn)
        _docker_row.addStretch()
        sl_info.addLayout(_docker_row)
        cameras = list_cameras()
        add_status("Cameras", ", ".join(str(c[1]) for c in cameras) if cameras else "None")
        add_status("Platform", f"{platform.system()} {platform.machine()}")
        l.addWidget(sys_info)

        # Devices shortcut: SSH, ZeroTier, saved devices — quick link from Settings
        dev_shortcut = QFrame(); dev_shortcut.setObjectName("InfoCard"); dsl = QVBoxLayout(dev_shortcut); dsl.setContentsMargins(25,20,25,20); dsl.setSpacing(12)
        dsh = QLabel("Devices & Connectivity")
        dsh.setObjectName("SectionHeading")
        dsl.addWidget(dsh)
        dss = QLabel("Manage ZeroTier peers, saved SSH devices, and connection keys.")
        dss.setObjectName("CaptionMuted")
        dsl.addWidget(dss)
        go_dev = QPushButton("Manage connections"); go_dev.setObjectName("BtnPrimary"); go_dev.setCursor(Qt.CursorShape.PointingHandCursor); go_dev.setMaximumWidth(240); go_dev.setFixedHeight(38)
        go_dev.clicked.connect(lambda: self.switch(3)); dsl.addWidget(go_dev)
        l.addWidget(dev_shortcut)

        # ZeroTier: multiple networks list + device/peer count
        zt_networks = get_zerotier_networks()
        peer_count = get_zerotier_peer_count()
        zt_status = get_zerotier_status()
        zt_local_ips = get_zerotier_local_ips()
        if zt_networks or peer_count is not None or zt_status != "NOT INSTALLED":
            l.addWidget(hairline())
            zt_panel = QFrame()
            zt_panel.setObjectName("NetworkInset")
            zt_inner = QVBoxLayout(zt_panel)
            zt_inner.setContentsMargins(16, 16, 16, 16)
            zt_inner.setSpacing(14)
            # --- ZT daemon status header row ---
            zt_hdr = QHBoxLayout()
            net_label = QLabel("ZeroTier networks")
            net_label.setObjectName("NetworkPanelHeading")
            zt_hdr.addWidget(net_label)
            zt_hdr.addStretch(1)
            _status_color = {"ONLINE": "#30D158", "OFFLINE": "#FF453A", "NOT INSTALLED": "#8E8E93", "UNKNOWN": "#FF9F0A"}.get(zt_status, "#8E8E93")
            status_dot = QLabel(f"\u25cf {zt_status}")
            status_dot.setObjectName("ZtStatusDot")
            status_dot.setStyleSheet(f"color: {_status_color};")
            zt_hdr.addWidget(status_dot)
            zt_inner.addLayout(zt_hdr)
            # --- Local ZT virtual IPs (this machine's assignedAddresses) ---
            if zt_local_ips:
                local_ip_row = QHBoxLayout()
                local_ip_lbl = QLabel("Your ZeroTier IP:")
                local_ip_lbl.setObjectName("FormLabelSm")
                local_ip_row.addWidget(local_ip_lbl)
                local_ip_val = QLabel("  ".join(zt_local_ips))
                local_ip_val.setObjectName("MonoIp")
                local_ip_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                local_ip_row.addWidget(local_ip_val)
                local_ip_row.addStretch(1)
                _copy_lip_btn = QPushButton("Copy")
                _copy_lip_btn.setObjectName("BtnSecondary")
                _copy_lip_btn.setFixedHeight(24)
                _copy_lip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                _copy_lip_btn.clicked.connect(lambda _, ips=zt_local_ips: [QApplication.clipboard().setText(ips[0]), self.show_toast(f"Copied: {ips[0]}")])
                local_ip_row.addWidget(_copy_lip_btn)
                zt_inner.addLayout(local_ip_row)
            if zt_networks:
                for net in zt_networks:
                    nwid = net.get("nwid") or net.get("id") or "—"
                    name = net.get("name") or nwid[:16]
                    status = (net.get("status") or "—")
                    addrs = net.get("assignedAddresses") or []
                    if not isinstance(addrs, list):
                        addrs = [addrs] if addrs else []
                    ip_str = ", ".join(str(a) for a in addrs[:5]) if addrs else "—"
                    row = QFrame()
                    row.setObjectName("ZerotierNetRow")
                    rl = QVBoxLayout(row)
                    rl.setContentsMargins(14, 12, 14, 12)
                    rl.setSpacing(6)
                    nm = QLabel(name)
                    nm.setObjectName("ZtNetTitle")
                    rl.addWidget(nm)
                    subl = QLabel(f"IP: {ip_str}  ·  {status}")
                    subl.setObjectName("ZtNetDetail")
                    rl.addWidget(subl)
                    zt_inner.addWidget(row)
            else:
                nn = QLabel("No networks joined.")
                nn.setObjectName("CaptionMuted")
                zt_inner.addWidget(nn)
            dev_row = QHBoxLayout()
            pv = QLabel("Peers visible:")
            pv.setObjectName("FormLabelSm")
            dev_row.addWidget(pv)
            dev_row.addStretch()
            pc = QLabel(str(peer_count))
            _ac = "#3B82F6" if getattr(self, "is_dark", True) else "#2563EB"
            pc.setStyleSheet(f"font-size: 15px; font-weight: 800; color: {_ac}; border: none;")
            dev_row.addWidget(pc)
            zt_inner.addLayout(dev_row)
            peers_list = get_zerotier_peers()
            if peers_list:
                default_ssh_user = os.getenv("ZEROTIER_SSH_USER", "jetson")
                peer_hdr = QLabel("Connected devices (copy IP or SSH)")
                peer_hdr.setObjectName("NetworkPanelHeading")
                peer_hdr.setStyleSheet("padding-top: 4px;")
                zt_inner.addWidget(peer_hdr)
                for pr in peers_list:
                    role_badge = pr.get("role", "—")
                    addr = pr.get("address", "—")
                    lat = pr.get("latency") or 0
                    peer_ip = (pr.get("ip") or "").strip()
                    peer_row = QFrame()
                    peer_row.setObjectName("PeerListRow")
                    prl = QHBoxLayout(peer_row)
                    prl.setContentsMargins(14, 10, 14, 10)
                    prl.setSpacing(12)
                    dot = QLabel()
                    dot.setObjectName("PeerStatusDotOn" if pr.get("connected") else "PeerStatusDotOff")
                    prl.addWidget(dot, alignment=Qt.AlignmentFlag.AlignVCenter)
                    id_l = QLabel(addr)
                    id_l.setObjectName("PeerIdLabel")
                    prl.addWidget(id_l, alignment=Qt.AlignmentFlag.AlignVCenter)
                    role_l = QLabel(role_badge)
                    role_l.setObjectName("PeerRoleLabel")
                    prl.addWidget(role_l, alignment=Qt.AlignmentFlag.AlignVCenter)
                    ip_l = QLabel(peer_ip or "—")
                    ip_l.setObjectName("PeerIpLabel")
                    prl.addWidget(ip_l, alignment=Qt.AlignmentFlag.AlignVCenter)
                    lat_l = QLabel(f"{lat} ms")
                    lat_l.setObjectName("PeerLatencyLabel")
                    prl.addWidget(lat_l, alignment=Qt.AlignmentFlag.AlignVCenter)
                    prl.addStretch()
                    if peer_ip and role_badge == "LEAF":
                        ssh_cmd = f"ssh {default_ssh_user}@{peer_ip}"
                        copy_btn = QPushButton("Copy SSH")
                        copy_btn.setObjectName("BtnSecondary")
                        copy_btn.setFixedHeight(30)
                        copy_btn.setMinimumWidth(88)
                        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                        copy_btn.setToolTip(ssh_cmd)

                        def _copy_ssh(cmd=ssh_cmd):
                            QApplication.clipboard().setText(cmd)
                            app = self.window()
                            if app and hasattr(app, "show_toast"):
                                app.show_toast("SSH command copied")

                        copy_btn.clicked.connect(_copy_ssh)
                        prl.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
                        zt_fetch = QPushButton("ZT IP Al")
                        zt_fetch.setObjectName("BtnSecondary")
                        zt_fetch.setFixedHeight(30)
                        zt_fetch.setMinimumWidth(88)
                        zt_fetch.setCursor(Qt.CursorShape.PointingHandCursor)
                        zt_fetch.setToolTip(
                            "Path IP üzerinden SSH ile uzak zerotier-cli çalıştırır; sanal IP Devices sekmesinde görünür."
                        )
                        zt_fetch.clicked.connect(
                            lambda _v=False, pip=peer_ip, aid=addr: self._fetch_peer_zt_virtual_ips(
                                pip, default_ssh_user, aid, goto_devices=True
                            )
                        )
                        prl.addWidget(zt_fetch, alignment=Qt.AlignmentFlag.AlignVCenter)
                    zt_inner.addWidget(peer_row)
                leaf_hint = QLabel(
                    "LEAF = edge device. 'Copy SSH' copies ssh user@IP (default user: jetson; override with ZEROTIER_SSH_USER)."
                )
                leaf_hint.setObjectName("CaptionMuted")
                leaf_hint.setStyleSheet("font-size: 11px; font-weight: 600; padding-top: 4px;")
                zt_inner.addWidget(leaf_hint)
            l.addWidget(zt_panel)

        l.addWidget(settings_title("Global preferences"))
        pref_card = QFrame()
        pref_card.setObjectName("InfoCard")
        pref_l = QVBoxLayout(pref_card)
        pref_l.setContentsMargins(28, 24, 28, 24)
        pref_l.setSpacing(18)
        dh = QHBoxLayout()
        eco_l = QLabel("Eco mode (thermal guard)")
        eco_l.setObjectName("SettingsRowLabel")
        dh.addWidget(eco_l)
        ts = ToggleSwitch()
        ts.toggled.connect(self.toggle_eco)
        dh.addStretch()
        dh.addWidget(ts)
        pref_l.addLayout(dh)
        ph = QHBoxLayout()
        dark_l = QLabel("Dark theme")
        dark_l.setObjectName("SettingsRowLabel")
        ph.addWidget(dark_l)
        ps = ToggleSwitch()
        ps.toggled.connect(self.toggle_theme)
        ph.addStretch()
        ph.addWidget(ps)
        pref_l.addLayout(ph)
        l.addWidget(pref_card)
        l.addWidget(hairline())

        # Camera defaults (CSI / Jetson): compact form, no wide label/field gap; AE block hidden when lock off
        l.addWidget(settings_title("Camera defaults (CSI / Jetson)"))
        cam_row = QHBoxLayout()
        cam_row.addStretch(1)
        cam_def = QFrame()
        cam_def.setObjectName("InfoCard")
        cam_def.setMaximumWidth(640)
        cdl = QVBoxLayout(cam_def)
        cdl.setContentsMargins(25, 25, 25, 25)
        cdl.setSpacing(16)
        defs = get_camera_defaults()
        cam_form = QFormLayout()
        cam_form.setSpacing(14)
        cam_form.setHorizontalSpacing(16)
        cam_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cam_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.camera_res_combo = QComboBox()
        self.camera_res_combo.setView(QListView())
        self.camera_res_combo.setFixedHeight(40)
        self.camera_res_combo.setMaximumWidth(250)
        for i, preset in enumerate(CAMERA_RESOLUTION_PRESETS):
            self.camera_res_combo.addItem(preset[0], i)
        self.camera_res_combo.setCurrentIndex(min(defs["resolution_index"], self.camera_res_combo.count() - 1))
        self.camera_res_combo.currentIndexChanged.connect(lambda i: set_camera_defaults(resolution_index=i))
        cam_form.addRow(form_label("Resolution"), self.camera_res_combo)
        aelock_cell = QWidget()
        ael_h = QHBoxLayout(aelock_cell)
        ael_h.setContentsMargins(0, 0, 0, 0)
        self.camera_aelock_switch = ToggleSwitch()
        self.camera_aelock_switch.setChecked(defs.get("aelock", False))
        self.camera_aelock_switch.toggled.connect(lambda c: [set_camera_defaults(aelock=c), self._update_aelock_sliders_visibility()])
        ael_h.addWidget(self.camera_aelock_switch)
        ael_h.addStretch()
        cam_form.addRow(form_label("AE lock (manual exposure)"), aelock_cell)
        cdl.addLayout(cam_form)

        self.camera_exp_block = QWidget()
        ev = QVBoxLayout(self.camera_exp_block)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(10)
        ev.addWidget(form_label_sm("Exposure (when AE lock is on)"))
        self.camera_exposure_slider = QSlider(Qt.Orientation.Horizontal)
        self.camera_exposure_slider.setRange(100, 100000)
        self.camera_exposure_slider.setValue(defs.get("v4l2_exposure", 5000))
        self.camera_exposure_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.camera_exposure_slider.setTickInterval(10000)
        self.camera_exposure_slider.valueChanged.connect(lambda v: [set_camera_defaults(v4l2_exposure=v), self._apply_v4l2_exposure_gain()])
        ev.addWidget(self.camera_exposure_slider)
        ev.addWidget(form_label_sm("Gain (when AE lock is on)"))
        self.camera_gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.camera_gain_slider.setRange(1, 200)
        self.camera_gain_slider.setValue(defs.get("v4l2_gain", 16))
        self.camera_gain_slider.valueChanged.connect(lambda v: [set_camera_defaults(v4l2_gain=v), self._apply_v4l2_exposure_gain()])
        ev.addWidget(self.camera_gain_slider)
        exp_hint = QLabel("Applies to /dev/video0 when AE lock is on. Restart the stream after changing AE lock.")
        exp_hint.setObjectName("CaptionMuted")
        exp_hint.setWordWrap(True)
        ev.addWidget(exp_hint)
        cdl.addWidget(self.camera_exp_block)

        cdl.addWidget(hairline())
        cam_form2 = QFormLayout()
        cam_form2.setSpacing(14)
        cam_form2.setHorizontalSpacing(16)
        cam_form2.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        cam_form2.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.camera_i2c_spin = QSpinBox()
        self.camera_i2c_spin.setRange(0, 15)
        self.camera_i2c_spin.setValue(int(defs.get("i2c_bus", 10)))
        self.camera_i2c_spin.valueChanged.connect(lambda v: set_camera_defaults(i2c_bus=v))
        i2c_cell = QWidget()
        i2c_h = QHBoxLayout(i2c_cell)
        i2c_h.setContentsMargins(0, 0, 0, 0)
        i2c_h.addWidget(self.camera_i2c_spin)
        i2c_h.addStretch()
        cam_form2.addRow(form_label("I2C bus (focus motor)"), i2c_cell)
        self.camera_sensor_spin = QSpinBox()
        self.camera_sensor_spin.setRange(0, 3)
        self.camera_sensor_spin.setValue(int(defs.get("sensor_id", 0)))
        self.camera_sensor_spin.valueChanged.connect(lambda v: set_camera_defaults(sensor_id=v))
        sid_cell = QWidget()
        sid_h = QHBoxLayout(sid_cell)
        sid_h.setContentsMargins(0, 0, 0, 0)
        sid_h.addWidget(self.camera_sensor_spin)
        sid_h.addStretch()
        cam_form2.addRow(form_label("CSI sensor-id (autofocus)"), sid_cell)
        i2c_hint = QLabel("Match I2C bus to your Jetson port (see docs). i2cset may require root or the i2c group.")
        i2c_hint.setObjectName("CaptionMuted")
        i2c_hint.setWordWrap(True)
        cam_form2.addRow(i2c_hint)
        self.camera_focus_combo = QComboBox()
        self.camera_focus_combo.setView(QListView())
        self.camera_focus_combo.setFixedHeight(40)
        self.camera_focus_combo.setMaximumWidth(200)
        self.camera_focus_combo.addItem("Fixed", "fixed")
        self.camera_focus_combo.addItem("Manual", "manual")
        self.camera_focus_combo.addItem("Auto", "auto")
        fm = defs.get("focus_mode", "fixed")
        idx = {"fixed": 0, "manual": 1, "auto": 2}.get(fm, 0)
        self.camera_focus_combo.setCurrentIndex(idx)
        self.camera_focus_combo.currentIndexChanged.connect(
            lambda: [set_camera_defaults(focus_mode=self.camera_focus_combo.currentData()), self._update_focus_controls_visibility()]
        )
        cam_form2.addRow(form_label("Focus mode (motorized CSI)"), self.camera_focus_combo)
        cdl.addLayout(cam_form2)

        self.camera_focus_slider_label = form_label_sm("Fixed position (0=infinity):")
        fs_host = QWidget()
        fs_wrap = QVBoxLayout(fs_host)
        fs_wrap.setContentsMargins(0, 0, 0, 0)
        fs_wrap.setSpacing(8)
        fs_wrap.addWidget(self.camera_focus_slider_label)
        self.camera_focus_slider = QSlider(Qt.Orientation.Horizontal)
        self.camera_focus_slider.setRange(0, 1023)
        self.camera_focus_slider.setValue(defs.get("focus_position", 512))
        self.camera_focus_slider.valueChanged.connect(lambda v: set_camera_defaults(focus_position=v))
        
        slider_row = QHBoxLayout()
        slider_row.addWidget(self.camera_focus_slider)
        slider_row.addStretch()
        fs_wrap.addLayout(slider_row)
        cdl.addWidget(fs_host)

        btn_host = QWidget()
        btn_l = QHBoxLayout(btn_host)
        btn_l.setContentsMargins(0,0,0,0)
        self.camera_focus_apply_btn = QPushButton("Set fixed position")
        self.camera_focus_apply_btn.setObjectName("BtnPrimary")
        self.camera_focus_apply_btn.setMinimumHeight(44)
        self.camera_focus_apply_btn.setMaximumWidth(200)
        self.camera_focus_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.camera_focus_apply_btn.clicked.connect(self._apply_focus_imx519)
        btn_l.addWidget(self.camera_focus_apply_btn)
        btn_l.addStretch()
        cdl.addWidget(btn_host)
        focus_long = QLabel(
            "Fixed: one position at stream start. Manual: slider + button. Auto: scripts/autofocus_imx519.py. "
            "Stop live CSI preview when possible."
        )
        focus_long.setObjectName("CaptionMuted")
        focus_long.setWordWrap(True)
        cdl.addWidget(focus_long)
        cam_note = QLabel("New streams use these values. Changing AE lock or resolution needs a stream restart.")
        cam_note.setObjectName("CaptionMuted")
        cam_note.setWordWrap(True)
        cdl.addWidget(cam_note)
        cam_row.addWidget(cam_def, 0, Qt.AlignmentFlag.AlignTop)
        cam_row.addStretch(1)
        l.addLayout(cam_row)
        self._update_aelock_sliders_visibility()
        self._update_focus_controls_visibility()
        l.addWidget(hairline())

        # Section 4: Docker Image Management (Now at bottom)
        ih = QHBoxLayout()
        ih.addWidget(settings_title("Image repository"))
        ih.addStretch()
        ih.addWidget(make_icon_refresh_button(self.refresh_images, "Refresh Docker images"))
        l.addLayout(ih)
        
        ic = QFrame(); ic.setObjectName("InfoCard"); il = QVBoxLayout(ic); il.setContentsMargins(20,20,20,20); il.setSpacing(10)
        hl = QHBoxLayout()
        rep_h = QLabel("REPOSITORY")
        rep_h.setObjectName("CaptionMutedSm")
        sz_h = QLabel("SIZE")
        sz_h.setObjectName("CaptionMutedSm")
        sz_h.setStyleSheet("margin-right: 60px;")
        hl.addWidget(rep_h)
        hl.addStretch()
        hl.addWidget(sz_h)
        il.addLayout(hl)
        il.addWidget(hairline())
        self.img_list_layout = QVBoxLayout(); self.img_list_layout.setSpacing(8); il.addLayout(self.img_list_layout)
        self.refresh_images(); l.addWidget(ic)
        l.addStretch(); return sw

    def _update_aelock_sliders_visibility(self):
        on = getattr(self, "camera_aelock_switch", None) and self.camera_aelock_switch.isChecked()
        blk = getattr(self, "camera_exp_block", None)
        if blk is not None:
            blk.setVisible(on)

    def _update_focus_controls_visibility(self):
        """Update slider label and button text for Fixed / Manual / Auto focus modes."""
        mode = (get_camera_defaults().get("focus_mode") or "fixed")
        if getattr(self, "camera_focus_combo", None): mode = self.camera_focus_combo.currentData() or mode
        lbl = getattr(self, "camera_focus_slider_label", None)
        btn = getattr(self, "camera_focus_apply_btn", None)
        if lbl:
            if mode == "fixed": lbl.setText("Fixed position (0=infinity):")
            elif mode == "manual": lbl.setText("Focus position (manual):")
            else: lbl.setText("Focus position:")
        if btn:
            if mode == "fixed": btn.setText("Set fixed position")
            elif mode == "manual": btn.setText("Apply focus")
            else: btn.setText("Run autofocus")

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

    def _apply_focus_imx519(self):
        """Fixed/Manual: apply slider position via I2C. Auto: run autofocus script with confirmation."""
        d = get_camera_defaults()
        mode = d.get("focus_mode", "fixed")
        pos = int(d.get("focus_position", 512))
        bus = int(d.get("i2c_bus", 10))
        sensor_id = int(d.get("sensor_id", 0))
        base = os.path.dirname(os.path.dirname(__file__))
        if mode == "auto":
            autofocus_script = os.path.join(base, "scripts", "autofocus_imx519.py")
            if not os.path.exists(autofocus_script):
                QMessageBox.information(
                    self,
                    "Focus",
                    "Autofocus script scripts/autofocus_imx519.py not found. Use Manual mode or see docs/CSI_CAMERA_FOCUS.md.",
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
        script_path = os.path.join(base, "scripts", "focus_imx519.py")
        if not os.path.exists(script_path):
            QMessageBox.information(self, "Focus", "Script scripts/focus_imx519.py not found. See docs/CSI_CAMERA_FOCUS.md.")
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
    
    def toggle_eco(self, c):
        self.eco_mode = c
        self.show_toast(f"Thermal Guard: {'ENABLED' if c else 'DISABLED'}")
        
    def _record_notification(self, level: str, message: str):
        if not hasattr(self, "_notifications"):
            self._notifications = []
        row = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": (level or "info").lower(),
            "message": str(message or "").strip(),
        }
        self._notifications.insert(0, row)
        if len(self._notifications) > 120:
            self._notifications = self._notifications[:120]
        if hasattr(self, "_notif_list_box"):
            self._refresh_notifications_panel()

    def _show_toast_typed(self, txt, level="info"):
        self._record_notification(level, txt)
        prefix = {
            "info":    "[i]",
            "success": "[OK]",
            "warning": "[!]",
            "error":   "[ERR]",
        }.get((level or "info").lower(), "[i]")
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
        pal = ThemeOps.palette(self.is_dark)
        if state is None:
            self.remote_node_status_label.setText("Local")
            self.remote_node_status_label.setStyleSheet(
                f"font-size:13px;font-weight:700;color:{pal['sub']};border:none;background:transparent;"
            )
        elif state:
            self.remote_node_status_label.setText("Online")
            self.remote_node_status_label.setStyleSheet(
                f"font-size:13px;font-weight:700;color:{pal['accent_net']};border:none;background:transparent;"
            )
        else:
            self.remote_node_status_label.setText("Offline")
            self.remote_node_status_label.setStyleSheet(
                "font-size:13px;font-weight:700;color:#FF453A;border:none;background:transparent;"
            )

    def _schedule_remote_status_check(self):
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
        if online is True and prev is not True:
            host = (getattr(self, "remote_host_input", None) and self.remote_host_input.text().strip()) or "remote host"
            self.notify_success(f"Remote host online: {host}")
        elif online is False and prev is not False:
            host = (getattr(self, "remote_host_input", None) and self.remote_host_input.text().strip()) or "remote host"
            self.notify_error(f"Remote host unreachable: {host}")
        host = (self.node_ip.text() or "").strip()
        if host:
            QTimer.singleShot(25000, self._run_remote_status_check)

    def _on_remote_host_text_changed(self, t):
        DockerManager.set_host(t)
        self.show_toast(f"Node: {(t or '').strip() or 'Local'}")
        self._schedule_remote_status_check()
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
        color = "#30D158" if running else "#FF453A"
        if running and prev_docker is not True:
            self.notify_success("Docker engine is active")
        elif not running and prev_docker is not False:
            self.notify_error("Docker engine is offline")
        lbl.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 700; border: none;")
        if btn is not None:
            btn.setVisible(not running)
        if running:
            self.show_toast("Docker is running")

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
        if i == 3:  # Devices tab: refresh peer list
            QTimer.singleShot(0, getattr(self, "refresh_devices_page", lambda: None))
        if i == 4:  # Library tab: refresh recordings
            QTimer.singleShot(0, getattr(self, "refresh_library", lambda: None))
        if i == 5:  # Settings tab: refresh remote node status + Docker status
            self._schedule_remote_status_check()
            QTimer.singleShot(0, self._refresh_docker_status)
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
            self.refresh_devices_page()
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
            self.refresh_devices_page()
        else:
            self.notify_error(f"Leave failed: {out[:180] if out else 'unknown error'}")

    def _refresh_notifications_panel(self):
        box = getattr(self, "_notif_list_box", None)
        if box is None:
            return
        while box.count():
            it = box.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        rows = list(getattr(self, "_notifications", [])[:20])
        if not rows:
            empty = QLabel("No notifications yet.")
            empty.setObjectName("CaptionMuted")
            box.addWidget(empty)
            return
        for n in rows:
            lv = n.get("level", "info").upper()
            ts = n.get("ts", "—")
            msg = n.get("message", "")
            line = QLabel(f"[{ts}] {lv} — {msg}")
            line.setObjectName("MonoMuted")
            line.setWordWrap(True)
            line.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.addWidget(line)

    def _collect_settings_bundle(self):
        prefs = load_app_prefs()
        cam_defaults = get_camera_defaults()
        return {
            "version": 1,
            "exported_at": datetime.now().isoformat(),
            "app_prefs": prefs,
            "camera_defaults": cam_defaults,
            "ui_state": {
                "is_dark": bool(getattr(self, "is_dark", True)),
            },
        }

    def _apply_settings_bundle(self, data: dict):
        if not isinstance(data, dict):
            raise ValueError("Invalid settings file: root object must be JSON object.")
        app_prefs = data.get("app_prefs")
        cam_defaults = data.get("camera_defaults")
        ui_state = data.get("ui_state", {})
        if not isinstance(app_prefs, dict):
            raise ValueError("Invalid settings file: app_prefs is missing or invalid.")
        if not isinstance(cam_defaults, dict):
            raise ValueError("Invalid settings file: camera_defaults is missing or invalid.")
        with open(_app_prefs_path(), "w", encoding="utf-8") as f:
            json.dump(app_prefs, f, indent=2)
        current = get_camera_defaults()
        for k in list(current.keys()):
            if k in cam_defaults:
                current[k] = cam_defaults[k]
        save_camera_defaults_to_disk()
        if "is_dark" in ui_state:
            self.is_dark = bool(ui_state.get("is_dark"))
        self.apply_theme()
        self.refresh_home_page()
        self.refresh_devices_page()
        try:
            if hasattr(self, "node_ip"):
                new_ip = (load_app_prefs().get("remote_host_ip") or "").strip()
                self.node_ip.setText(new_ip)
                DockerManager.set_host(new_ip if new_ip else None)
        except Exception:
            pass

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
            "Ctrl+5":  lambda: self.switch(4),   # Library
            "Ctrl+6":  lambda: self.switch(5),   # Settings
            "Ctrl+R":  self.refresh_home_page,
            "Ctrl+K":  self.show_toast if hasattr(self, "show_toast") else lambda: None,
        }
        for seq, fn in _map.items():
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(fn)

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
        for ts in self.findChildren(ToggleSwitch):
            ts.update()
        for card in self.findChildren(ResizableCard):
            card._sync_card_shadow()
        for ch in getattr(self, "charts", []):
            ch.update()
        if getattr(self, "_remote_status_initialized", False):
            self._update_remote_status_label(getattr(self, "_remote_status_state", None))
    def modal_cam(self): self.show_overlay("New broadcast source", self.add_cam_logic, for_camera=True)
    def modal_doc(self): self.show_overlay("New workspace", self.add_doc_logic, for_camera=False)
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

    def refresh_ui(self):
        """Reload workspace cards from Docker."""
        self.active_cids.clear()
        for i in reversed(range(self.df.count())):
            w = self.df.itemAt(i).widget()
            if isinstance(w, ResizableCard):
                if getattr(w, "checker", None) and w.checker.isRunning(): w.checker.quit()
                w.deleteLater()
        self.df.removeWidget(self.abd); self.df.addWidget(self.abd); self.check_docker()
    def check_docker(self):
        """
        Refresh workspace cards from current Docker host.

        Conflict guard:
        - Docker returns 12-char IDs in some commands and 64-char in others.
          We normalize before DB lookup to avoid duplicate/ghost cards.
        """
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
        zt_cam_label = QLabel("Hızlı:")
        zt_cam_btn = QPushButton("Aktif cihaz URL'sini kullan")
        zt_cam_btn.setObjectName("ShellBtn")
        zt_cam_btn.setToolTip("Stream URL'sini aktif SSH cihazının IP'si ile doldur (RTSP)")
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
            name_input = QLineEdit(); name_input.setPlaceholderText("Stream name (e.g. Garden cam)")
            mode_combo = make_combo()
            mode_combo.addItem("Physical device", "Physical")
            mode_combo.addItem("Network stream", "Stream")
            mode_combo.addItem("AI workspace", "Container")
        else:
            name_input = QLineEdit(); name_input.setPlaceholderText("Workspace adı (ör. Jetson AI Lab)")
            # Genişletilmiş AI kütüphane kataloğu
            _AI_CATALOG = [
                {"name": "L4T PyTorch 2.0 (Jetson)",       "img": "nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3",  "desc": "PyTorch 2.0 — JetPack 5.x"},
                {"name": "L4T TensorFlow 2 (Jetson)",       "img": "nvcr.io/nvidia/l4t-tensorflow:r35.2.1-tf2-py3",   "desc": "TensorFlow 2 — JetPack 5.x"},
                {"name": "L4T ML (Jetson, tümleşik)",       "img": "nvcr.io/nvidia/l4t-ml:r35.2.1-py3",               "desc": "PyTorch + TF + scikit-learn"},
                {"name": "Ultralytics YOLOv8 (Jetson)",     "img": "ultralytics/ultralytics:latest-jetson",           "desc": "YOLOv8 — Jetson optimize"},
                {"name": "OpenCV + Python 3 (Jetson)",      "img": "nvcr.io/nvidia/l4t-base:r35.2.1",                  "desc": "Temel görüntü işleme"},
                {"name": "ONNX Runtime (Jetson GPU)",       "img": "nvcr.io/nvidia/l4t-jetpack:r35.2.1",              "desc": "ONNX Runtime + CUDA"},
                {"name": "ROS 2 Humble (Jetson)",           "img": "dustynv/ros:humble-ros-base-l4t-r35.2.1",         "desc": "ROS 2 + CUDA"},
                {"name": "PyTorch (CPU — Mac/PC test)",     "img": "pytorch/pytorch:latest",                          "desc": "CPU — Jetson'da GPU kullanmaz"},
                {"name": "Nginx Web (test)",                 "img": "nginx:latest",                                    "desc": "Web sunucu testi"},
            ]
            # Katalog + özel katalog birleştir
            _catalog_items = _AI_CATALOG + [i for i in CatalogManager.get_recommended()[0]
                                             if i.get("img") not in {x["img"] for x in _AI_CATALOG}]
            cat_combo = make_combo()
            for _ci in _catalog_items:
                cat_combo.addItem(f"{_ci['name']}  —  {_ci.get('desc','')}", _ci["img"])
            run_target_combo = make_combo(); run_target_combo.addItem("Yerel (bu makine)", "")
            remote_ip = getattr(self, "node_ip", None)
            if remote_ip and getattr(remote_ip, "text", None):
                ip = remote_ip.text().strip()
                if ip:
                    online = check_remote_node_reachable(ip, port=2375, timeout=2)
                    run_target_combo.addItem(
                        f"Uzak Jetson ({ip})  •  {'Çevrimiçi' if online else 'Çevrimdışı'}",
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
                    name_input.setText("Uzak Kamera")
            else:
                self.show_toast("Önce 'Devices' sayfasından bir cihaza bağlanın.")
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
                        QMessageBox.warning(ov, "Stream", "Bir stream URL'si girin. Kamera 'Devices' sayfasındaki cihazdaysa 'Aktif cihaz URL'sini kullan'ı tıklayın.")
                        return
                    if not name: name = f"Stream: {val[:15]}"
                elif m == "Container":
                    cid = container_combo.currentData(); script = script_input.text().strip()
                    if not cid:
                        QMessageBox.warning(ov, "AI workspace", "No container selected. Create a workspace first or ensure one is running.")
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

    def add_cam_logic(self, name, src, meta=None, save=True):
        if src is None or src == "" or src in self.active_srcs: return
        self.active_srcs.add(src)
        card = ResizableCard(name, meta or "", False); card.trigger_delete_modal.connect(self.show_delete_confirmation); card.removed.connect(lambda: [self.active_srcs.remove(src) if src in self.active_srcs else None, card.deleteLater()])
        card.db = self.db; card.sub_val = src
        card._layout_persist_src = src
        saved = self._camera_card_geom.get(src)
        if saved:
            cw, ch = saved
            card.resize(cw, ch)
        self.cf.removeWidget(self.abc); self.cf.addWidget(card); self.cf.addWidget(self.abc)
        if save: self.db.save_camera(name, src, meta or "")
        
        if str(src).startswith("docker://"):
            card.view.setText("AI engine starting...")
        elif src is not None:
            card.stream_meta = meta or ""
            self._attach_camera_thread(card, name, src, meta or "")
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
        self.show_toast("Akış ayarları kaydedildi; önizleme yeniden başlatıldı.")

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
        card.set_status_info("Hazırlanıyor…", "#3B82F6")
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
                card.set_status_info("Çalışıyor", "#30D158")
                card.start_monitoring()
                self.db.save_workspace(cn, img, cid_norm, host=target if use_remote else None)
                if use_remote:
                    self.notify_success(
                        f"Uzak container başlatıldı: {cn} "
                        f"({target}) — AI modülleri için terminali kullanın."
                    )
            else:
                card.set_status_info("Hata", "#FF453A")
                raw = str(o).strip() if o else ""
                if not raw:
                    short, detail = "Container oluşturulamadı.", ""
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
        if getattr(self, "_zt_peer_refresh_timer", None):
            self._zt_peer_refresh_timer.stop()
        rthr = getattr(self, "_remote_status_thread", None)
        if rthr is not None and rthr.isRunning():
            rthr.wait(4000)
            if rthr.isRunning():
                rthr.terminate()
                rthr.wait(1500)
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
    splash = w._show_splash_screen()
    w.show()
    if splash is not None:
        QTimer.singleShot(1700, w.raise_)
        QTimer.singleShot(1750, w.activateWindow)
    sys.exit(app.exec())




