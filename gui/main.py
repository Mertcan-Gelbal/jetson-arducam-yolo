import sys, os, re, psutil, subprocess, cv2, time, platform, numpy as np, glob, random, string, threading, json, sqlite3, logging, socket
from datetime import datetime
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
    QSlider, QListView, QSpinBox, QDialog, QDialogButtonBox, QStyle,
)

from PySide6.QtCore import (
    Qt, QTimer, QThread, Signal, QSize, QPoint, QPointF, QRect, QUrl,
    QPropertyAnimation, QEasingCurve, Property, QEvent, QMutex,
)
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QImage, QPixmap, QPainter, QPainterPath, QPen, QBrush,
    QCursor, QShowEvent, QResizeEvent, QMouseEvent, QTextCursor, QDesktopServices, QPalette,
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

    def save_camera(self, name, src, meta):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO cameras (name, src, meta) VALUES (?, ?, ?)", (name, src, meta))

    def get_cameras(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT name, src, meta FROM cameras").fetchall()

    def remove_camera(self, src):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cameras WHERE src = ?", (src,))

    def save_workspace(self, name, img, cid):
        with sqlite3.connect(self.db_path) as conn:
            # One DB row per container: delete any prior row first (avoid duplicates / double cards)
            short = (cid or "")[:12]
            conn.execute("DELETE FROM workspaces WHERE cid = ? OR cid LIKE ?", (cid, short + "%"))
            conn.execute("INSERT INTO workspaces (name, img, cid) VALUES (?, ?, ?)", (name, img, cid))

    def get_workspaces(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT name, img, cid FROM workspaces").fetchall()

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
            conn.execute("DELETE FROM devices WHERE host = ?", (old_host,))
            conn.execute("INSERT INTO devices (name, host, user, key_path) VALUES (?, ?, ?, ?)", (name or "Device", host or "", user or "jetson", key_path or ""))

    def remove_device(self, host):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM devices WHERE host = ?", (host,))

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
    return ips


def get_zerotier_peers():
    """Parse ZeroTier `listpeers -j` output into peer rows (LEAF = device, PLANET = infra).
    NOTE: The 'ip' field contains the physical path IP (LAN/WAN), NOT the ZT virtual IP.
    ZT virtual IPs for peers are not available from the local CLI; use get_zerotier_local_ips()
    for this machine's own ZT virtual IP. Obtain the Jetson's ZT virtual IP from the device itself.
    """
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output("zerotier-one_x64.exe -j listpeers", shell=True).decode()
        else:
            out = subprocess.check_output("zerotier-cli -j listpeers", shell=True).decode()
        raw = json.loads(out)
        if not isinstance(raw, list):
            return []
        result = []
        for p in raw:
            addr = p.get("address") or p.get("nodeId") or ""
            if not isinstance(addr, str): addr = str(addr)
            role = (p.get("role") or "LEAF")
            latency = p.get("latency") if isinstance(p.get("latency"), (int, float)) else 0
            version = p.get("version") or ""
            paths = p.get("paths") or []
            ip_str = ""
            for path_el in paths:
                ip_str = _extract_ip_from_path(path_el)
                if ip_str:
                    break
            if not ip_str and paths and isinstance(paths[0], str):
                ip_str = _extract_ip_from_path(paths[0])
            result.append({
                "address": addr[:10] if addr else "—",
                "role": role,
                "latency": latency,
                "version": str(version)[:20] if version else "—",
                "connected": len(paths) > 0,
                "ip": (ip_str or "").strip(),
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
        return ips
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
    def open_terminal(cid):
        plat = platform.system()
        exec_cmd = DockerManager.get_cmd(f"exec -it {cid} /bin/sh")
        if "/bin/sh" in exec_cmd:
            exec_cmd_alt = DockerManager.get_cmd(f"exec -it {cid} /bin/bash")
        else:
            exec_cmd_alt = exec_cmd
        try:
            if plat == "Darwin":
                script = f"{exec_cmd} || {exec_cmd_alt}"
                cmd = f"osascript -e 'tell application \"Terminal\" to do script \"{script}\"' -e 'activate application \"Terminal\"'"
                subprocess.Popen(cmd, shell=True)
            elif plat == "Linux":
                terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
                for t in terminals:
                    if subprocess.run(f"which {t}", shell=True, capture_output=True).returncode == 0:
                        subprocess.Popen(f"{t} -e \"{exec_cmd}\"", shell=True)
                        return
            elif plat == "Windows":
                subprocess.Popen(f"start powershell.exe -NoExit -Command \"{exec_cmd}\"", shell=True)
        except: pass

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
            track = QColor("#007AFF")
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
        accent = "#0A84FF" if is_dark else "#007AFF"
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

    def __init__(self, size=32, color="#007AFF", parent=None):
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
            self.fps_lbl.setFixedWidth(54); hl.addWidget(self.fps_lbl)

        hl.addStretch()
        
        if is_docker:
            self.l_btn = QPushButton("LOGS"); self.l_btn.setFixedSize(68, 28); self.l_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.l_btn.setObjectName("ShellBtn")
            self.l_btn.clicked.connect(self.show_logs); hl.addWidget(self.l_btn)
            
            self.t_btn = QPushButton("SHELL"); self.t_btn.setFixedSize(72, 28); self.t_btn.setCursor(Qt.CursorShape.PointingHandCursor); self.t_btn.setObjectName("ShellBtn")
            self.t_btn.clicked.connect(lambda: DockerManager.open_terminal(self.container_id)); hl.addWidget(self.t_btn)
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
        self.rec_btn.setText(f"● {m:02d}:{s:02d}")

    def update_fps(self, fps):
        lbl = getattr(self, "fps_lbl", None)
        if lbl is None:
            return
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
            btn.setText("✓ Saved")
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
            app.switch(4)
        elif app and hasattr(app, "tabs") and hasattr(app.tabs, "setCurrentIndex"):
            app.tabs.setCurrentIndex(4)
        if app and hasattr(app, "show_toast"):
            app.show_toast("Opened Settings")

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
        QMessageBox#LogViewerBox QPushButton:hover {{ border-color: #007AFF; color: #007AFF; }}
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
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, c = img_rgb.shape
            if h < 1 or w < 1:
                return
            qimg = QImage(img_rgb.data, w, h, c * w, QImage.Format.Format_RGB888).copy()
            target = self.view.size()
            if target.width() < 32 or target.height() < 24:
                target = QSize(320, 240)
            pix = QPixmap.fromImage(qimg).scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            r = max(8, min(18, min(pix.width(), pix.height()) // 12))
            self.view.setPixmap(rounded_pixmap(pix, r))
            if not self.is_docker:
                self._last_frame = img.copy()
        except Exception:
            pass

# =============================================================================
#  UI LAYOUT & THEME
# =============================================================================

class ThemeOps:
    """Single source palette + QSS (dark: elevated contrast, layered surfaces)."""

    @staticmethod
    def palette(is_dark: bool) -> dict:
        if is_dark:
            return {
                "bg": "#06080F",
                "canvas": "#070A12",
                "sb": "#0B0F1A",
                "card": "#121826",
                "txt": "#E8ECF4",
                "sub": "#8E96AE",
                "brd": "rgba(120, 155, 230, 0.2)",
                "ibg": "#1A2130",
                "hov": "rgba(80, 145, 255, 0.14)",
                "ihov": "#222A3A",
                "ov_bg": "rgba(4, 6, 12, 0.94)",
                "card_txt": "#F2F5FA",
                "preview_bg": "#080C14",
                "preview_br": "rgba(100, 140, 220, 0.32)",
                "surface_row": "#0F1520",
                "surface_row_compact": "#0D121C",
                "hairline": "#2A3448",
                "card_header_bg": "#151C2C",
                "meta_panel_bg": "#0E1522",
                "accent_soft_bg": "rgba(10, 132, 255, 0.16)",
                "accent_soft_br": "rgba(90, 170, 255, 0.38)",
                "ctx_menu_bg": "#141B2A",
                "ctx_menu_hi": "rgba(10, 132, 255, 0.22)",
                "pop_edge": "#4A5F85",
                "zt_inset": "#0A0E18",
                "focus_shell_top": "#1A2438",
                "focus_shell_bot": "#0C101C",
                "focus_border_hi": "#6CB4FF",
                "focus_panel": "rgba(255,255,255,0.07)",
                "focus_panel_br": "rgba(120, 185, 255, 0.35)",
                "accent_net": "#30D158",
                "toast_scrim": "rgba(6, 8, 15, 0.92)",
            }
        return {
            "bg": "#D0D6E2",
            "canvas": "#D8DEEA",
            "sb": "#CCD3E0",
            "card": "#E6EBF4",
            "txt": "#1A2030",
            "sub": "#5A6274",
            "brd": "rgba(20, 40, 80, 0.11)",
            "ibg": "#F4F6FB",
            "hov": "rgba(0, 100, 220, 0.08)",
            "ihov": "#D0D8E6",
            "ov_bg": "rgba(220, 226, 236, 0.96)",
            "card_txt": "#1C2433",
            "preview_bg": "#DCE2EE",
            "preview_br": "rgba(25, 45, 90, 0.15)",
            "surface_row": "#DFE5F0",
            "surface_row_compact": "#D6DDEA",
            "hairline": "#BFC8D8",
            "card_header_bg": "#E2E8F2",
            "meta_panel_bg": "#DDE4F0",
            "accent_soft_bg": "rgba(0, 122, 255, 0.12)",
            "accent_soft_br": "rgba(0, 122, 255, 0.28)",
            "ctx_menu_bg": "#F0F3FA",
            "ctx_menu_hi": "rgba(0, 122, 255, 0.14)",
            "pop_edge": "#8A9AB8",
            "zt_inset": "#E4EAF4",
            "focus_shell_top": "#F4F7FD",
            "focus_shell_bot": "#E2E9F4",
            "focus_border_hi": "#007AFF",
            "focus_panel": "rgba(255,255,255,0.85)",
            "focus_panel_br": "rgba(0, 100, 220, 0.22)",
            "accent_net": "#15803D",
            "toast_scrim": "rgba(248, 250, 252, 0.96)",
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
        nav_idle = "#B4BDD4" if is_dark else "#4A5568"
        search_ring = "rgba(130, 165, 230, 0.42)" if is_dark else "rgba(40, 70, 130, 0.28)"
        card_hover_brd = "rgba(10, 132, 255, 0.55)" if is_dark else "rgba(0, 100, 220, 0.45)"
        if is_dark:
            sb_m_txt, sb_m_br, sb_m_bg = "#D4C4FD", "rgba(167, 139, 250, 0.42)", "rgba(139, 92, 246, 0.12)"
            sb_e_txt, sb_e_br, sb_e_bg = "#7DD3FC", "rgba(56, 189, 248, 0.45)", "rgba(10, 132, 255, 0.12)"
            sb_p_txt, sb_p_br, sb_p_bg = "#86EFAC", "rgba(52, 211, 153, 0.45)", "rgba(16, 185, 129, 0.12)"
            docker_tag_txt = "#5AC8FA"
            ai_hot = "#0A84FF"
        else:
            sb_m_txt, sb_m_br, sb_m_bg = "#5B21B6", "rgba(91, 33, 182, 0.32)", "rgba(91, 33, 182, 0.07)"
            sb_e_txt, sb_e_br, sb_e_bg = "#0369A1", "rgba(3, 105, 161, 0.30)", "rgba(0, 122, 255, 0.08)"
            sb_p_txt, sb_p_br, sb_p_bg = "#047857", "rgba(4, 120, 87, 0.30)", "rgba(5, 150, 105, 0.10)"
            docker_tag_txt = "#0066CC"
            ai_hot = "#0058D0"

        return f"""
        QMainWindow {{ background-color: {bg}; }}
        QWidget {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'SF Pro Display', Roboto, 'Helvetica Neue', sans-serif; color: {txt}; font-size: 13px; letter-spacing: 0.15px; }}
        QWidget#MainCanvas {{ background-color: {canvas}; }}
        QFrame#Sidebar {{ background-color: {sb}; border-right: 1px solid {brd}; }}
        QFrame#Card, QFrame#InfoCard {{ background-color: {card}; border: 2px solid {pop_edge}; border-radius: 18px; }}
        QFrame#Card:hover, QFrame#InfoCard:hover {{ border-color: {card_hover_brd}; }}
        QFrame#ModalBox {{ background-color: {card}; border: 2px solid {pop_edge}; border-radius: 16px; }}
        QFrame#CardHeader {{ background-color: {card_header_bg}; border-top-left-radius: 14px; border-top-right-radius: 14px; border-bottom: 1px solid {pop_edge}; }}
        QFrame#MetaPanel {{ background-color: {meta_panel_bg}; border: 1px solid {pop_edge}; border-radius: 12px; }}
        QFrame#MediaCard {{ background-color: {surface_row}; border: 2px solid {pop_edge}; border-radius: 20px; }}
        QFrame#MediaCard:hover {{ border-color: {card_hover_brd}; }}
        QLabel#MediaThumb {{ background-color: {preview_bg}; border: 1px solid {pop_edge}; border-radius: 14px; color: {sub}; }}
        QLabel#MediaThumbPlaceholder {{
            background-color: {meta_panel_bg}; border: 2px dashed {pop_edge}; border-radius: 14px; color: {sub};
            font-size: 11px; font-weight: 800; letter-spacing: 0.12em;
        }}
        QLabel#MediaCardTitle {{ color: {txt}; font-size: 13px; font-weight: 800; border: none; background: transparent; }}
        QLabel#MediaCardMeta {{ color: {sub}; font-size: 11px; font-weight: 600; border: none; background: transparent; }}
        QFrame#SettingsInset {{ background-color: {zt_inset}; border: 2px solid {pop_edge}; border-radius: 12px; }}
        QFrame#MonPreview {{ background-color: #000000; border-radius: 16px; border: 2px solid {pop_edge}; }}
        QFrame#VideoWell {{
            background-color: {meta_panel_bg};
            border: 2px solid {pop_edge};
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
        QFrame#SurfaceRow {{ background-color: {surface_row}; border-radius: 14px; border: none; }}
        QFrame#SurfaceRow:hover {{ background-color: {ihov}; }}
        QFrame#SurfaceRowCompact {{ background-color: {surface_row_compact}; border-radius: 12px; border: none; }}
        QFrame#Hairline {{ background-color: {hairline}; border: none; max-height: 1px; min-height: 1px; }}
        QFrame#FocusPopupRoot {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {focus_shell_top}, stop:1 {focus_shell_bot});
            border: 1px solid {focus_border_hi};
            border-radius: 20px;
        }}
        QFrame#FocusAccentBar {{
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0058D0, stop:0.45 #0A84FF, stop:1 #5AC8FA);
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
            border: 2px solid {pop_edge};
            border-radius: 12px;
            padding: 10px 22px;
            font-size: 14px;
            font-weight: 700;
            min-height: 44px;
            min-width: 108px;
        }}
        QPushButton#FocusSecondaryBtn:hover {{ border-color: {focus_border_hi}; color: {txt}; background-color: {hov}; }}
        QPushButton#FocusPrimaryBtn {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #3A94FF, stop:1 #0066DD);
            color: #FFFFFF;
            border-radius: 12px;
            padding: 10px 28px;
            font-size: 14px;
            font-weight: 800;
            border: 1px solid rgba(255,255,255,0.22);
            min-height: 44px;
            min-width: 128px;
        }}
        QPushButton#FocusPrimaryBtn:hover {{ background: #4FA3FF; color: #FFFFFF; }}
        QPushButton#FocusPrimaryBtn:pressed {{ background: #0058C4; color: #FFFFFF; }}
        QPushButton#BtnModalCancel {{
            background-color: transparent;
            color: {sub};
            border: 2px solid {pop_edge};
            border-radius: 12px;
            padding: 10px 22px;
            font-size: 14px;
            font-weight: 700;
            min-height: 44px;
            min-width: 108px;
        }}
        QPushButton#BtnModalCancel:hover {{ border-color: #007AFF; color: {txt}; background-color: {hov}; }}
        QSlider#FocusSlider::groove:horizontal {{ height: 10px; background: {brd}; border-radius: 5px; border: 1px solid {pop_edge}; }}
        QSlider#FocusSlider::handle:horizontal {{
            width: 24px; height: 24px; margin: -8px 0;
            background: #0A84FF;
            border-radius: 12px;
            border: 3px solid {card};
        }}
        QSlider#FocusSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #0058D0, stop:1 #0A84FF); border-radius: 5px; height: 10px; }}
        QLabel#BrandTagline {{ color: {sub}; font-size: 12px; font-weight: 700; letter-spacing: 0.14em; border: none; background: transparent; text-transform: none; }}
        QLabel#PageSubtitle {{ color: {sub}; font-size: 13px; font-weight: 600; border: none; background: transparent; letter-spacing: 0.02em; max-width: 720px; }}
        QLabel#AddBtnIcon {{ font-size: 44px; font-weight: 300; color: {sub}; border: none; background: transparent; }}
        QLabel#AddBtnTitle {{ font-size: 15px; font-weight: 800; color: {txt}; border: none; background: transparent; letter-spacing: -0.25px; }}
        QLabel#AddBtnHint {{ font-size: 11px; font-weight: 600; color: {sub}; border: none; background: transparent; }}
        QFrame#ContextMenuShell {{ background: transparent; border: none; }}
        QFrame#ContextMenuPanel {{ background-color: {ctx_menu_bg}; border: 2px solid {pop_edge}; border-radius: 14px; }}
        QFrame#NetworkInset {{ background-color: {zt_inset}; border: 2px solid {pop_edge}; border-radius: 14px; }}
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
        QLabel#AccentBadge {{ background-color: {accent_soft_bg}; color: #007AFF; font-size: 11px; font-weight: 900; border-radius: 10px; border: 1px solid {accent_soft_br}; padding: 2px; }}
        QLabel#StatKey {{ color: {sub}; font-size: 8px; font-weight: 900; letter-spacing: 1px; border: none; background: transparent; }}
        QLabel#StatValue {{ color: {txt}; font-size: 13px; font-weight: 800; border: none; background: transparent; }}
        QLabel#SectionHeading {{ font-size: 17px; font-weight: 800; color: {txt}; border: none; background: transparent; letter-spacing: -0.15px; padding: 4px 0 10px 0; }}
        QLabel#ModalTitle {{ font-size: 24px; font-weight: 800; color: {txt}; border: none; background: transparent; letter-spacing: -0.5px; }}
        QLabel#ModalSubtitle {{ color: {sub}; font-size: 13px; font-weight: 600; border: none; background: transparent; letter-spacing: 0.02em; }}
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
        QLineEdit, QComboBox, QSpinBox {{ background-color: {ibg}; border: 2px solid {pop_edge}; border-radius: 12px; padding: 10px 16px; color: {txt}; font-size: 14px; font-weight: 500; min-height: 40px; }}
        QSpinBox::up-button, QSpinBox::down-button {{ width: 24px; border: none; background: transparent; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: #007AFF; border-width: 2px; background-color: rgba(0,122,255,0.08); }}
        QFrame#SearchShell {{
            background-color: {ibg};
            border: 2px solid {search_ring};
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
        }}
        QComboBox QAbstractItemView::item, QComboBox QListView::item {{
            min-height: 38px;
            padding: 10px 14px;
            background-color: {card};
            color: {txt};
            border: none;
        }}
        QComboBox QAbstractItemView::item:selected, QComboBox QListView::item:selected {{
            background-color: #007AFF;
            color: white;
            border: none;
        }}
        QComboBox QAbstractItemView::item:hover, QComboBox QListView::item:hover {{
            background-color: {ihov};
            color: {txt};
            border: none;
        }}
        QSlider::groove:horizontal {{ height: 6px; background: {brd}; border-radius: 4px; }}
        QSlider::handle:horizontal {{ width: 20px; height: 20px; margin: -7px 0; background: #007AFF; border-radius: 10px; border: 2px solid {card}; }}
        QSlider::sub-page:horizontal {{ background: #007AFF; border-radius: 4px; height: 6px; }}
        QPushButton#NavTab {{ border: none; border-radius: 12px; text-align: left; padding: 14px 20px; color: {nav_idle}; font-weight: 600; font-size: 14px; letter-spacing: 0.15px; min-height: 48px; }}
        QPushButton#NavTab:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #0A84FF, stop:1 #0058D0); color: white; border: 2px solid rgba(255,255,255,0.28); border-left: 4px solid rgba(255,255,255,0.80); font-weight: 800; }}
        QPushButton#NavTab:hover:!checked {{ background-color: {hov}; color: {txt}; border: 1px solid {pop_edge}; }}
        QPushButton#IconRefresh {{
            min-width: 42px; max-width: 42px; min-height: 42px; max-height: 42px;
            border-radius: 21px; border: 2px solid {pop_edge}; background-color: {hov}; color: {txt};
            font-size: 11px; font-weight: 700; padding: 0;
        }}
        QPushButton#IconRefresh:hover {{ border-color: #007AFF; color: #007AFF; background-color: rgba(0,122,255,0.1); }}
        QPushButton#IconRefresh:pressed {{ background-color: rgba(0,122,255,0.18); }}
        QLabel#BrandTitle {{ font-size: 32px; font-weight: 800; color: {txt}; letter-spacing: -0.72px; border: none; background: transparent; }}
        QLabel#PageTitle {{ font-size: 28px; font-weight: 800; color: {txt}; letter-spacing: -0.5px; border: none; background: transparent; }}
        QLabel#SettingsBlockTitle {{ font-size: 22px; font-weight: 800; color: {txt}; border: none; background: transparent; }}
        QLabel#SettingsRowLabel {{ font-size: 14px; font-weight: 600; color: {txt}; border: none; background: transparent; }}
        QLabel#SidebarFootnote {{ color: {sub}; font-size: 10px; font-weight: 700; border: none; background: transparent; }}
        QLabel#MediaTypeTag {{ color: {sub}; font-size: 9px; font-weight: 900; letter-spacing: 0.5px; min-width: 45px; border: none; background: transparent; }}
        QLabel#ListPrimary {{ font-size: 12px; font-weight: 700; color: {txt}; border: none; background: transparent; }}
        QLabel#HWValue {{ font-size: 14px; font-weight: 700; color: {txt}; border: none; background: transparent; }}
        QLabel#DockSize {{ color: #007AFF; font-size: 11px; font-weight: 700; border: none; background: transparent; margin-right: 15px; }}
        QPushButton#AddBtn {{ border: 2px dashed {pop_edge}; border-radius: 20px; color: {sub}; background-color: transparent; font-weight: 800; font-size: 11px; padding: 14px; }}
        QPushButton#AddBtn:hover {{ border-color: #007AFF; color: #007AFF; background-color: rgba(0,122,255,0.08); }}
        QPushButton#BtnPrimary {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0A84FF, stop:1 #0066DD); color: #FFFFFF; border-radius: 14px; padding: 12px 18px; font-weight: 800; border: none; font-size: 13px; letter-spacing: 0.4px; min-height: 40px; }}
        QPushButton#BtnPrimary:hover {{ background: #3A94FF; color: #FFFFFF; }}
        QPushButton#BtnPrimary:pressed {{ background: #0058C4; color: #FFFFFF; }}
        QPushButton#BtnDanger {{ background-color: rgba(239, 68, 68, 0.12); color: #EF4444; border-radius: 14px; padding: 12px; border: 1px solid rgba(239, 68, 68, 0.25); font-weight: 700; font-size: 13px; }}
        QPushButton#BtnDanger:hover {{ background-color: #EF4444; color: white; }}
        QPushButton#BtnDanger:pressed {{ background-color: #C93434; color: white; }}
        QPushButton#ShellBtn {{ border: 1px solid {brd}; border-radius: 10px; color: {sub}; font-size: 10px; font-weight: 800; background: transparent; padding: 6px 10px; }}
        QPushButton#ShellBtn:hover {{ border-color: #007AFF; color: #007AFF; background: rgba(0,122,255,0.05); }}
        QPushButton#OpenAction {{ background-color: rgba(0,122,255,0.14); color: #007AFF; border-radius: 10px; font-size: 10px; font-weight: 800; border: 1px solid rgba(0,122,255,0.3); padding: 4px 12px; min-height: 26px; }}
        QPushButton#OpenAction:hover {{ background-color: #007AFF; color: white; border-color: #007AFF; }}
        QPushButton#RowGhost {{ border: 1px solid {brd}; border-radius: 10px; background: transparent; color: {sub}; font-size: 9px; font-weight: 700; padding: 4px 10px; min-height: 26px; }}
        QPushButton#RowGhost:hover {{ border-color: #007AFF; color: #007AFF; background-color: {hov}; }}
        QPushButton#RowAccent {{ border: 1px solid rgba(0,122,255,0.42); border-radius: 10px; background: transparent; color: #007AFF; font-size: 9px; font-weight: 700; padding: 4px 10px; min-height: 26px; }}
        QPushButton#RowAccent:hover {{ background-color: rgba(0,122,255,0.12); }}
        QPushButton#IconCloseSm {{ border: none; background: transparent; color: {sub}; font-size: 17px; min-width: 28px; min-height: 28px; }}
        QPushButton#IconCloseSm:hover {{ color: #EF4444; }}
        QPushButton#CardHeaderAction {{
            border: 1px solid {brd}; border-radius: 12px; background-color: {accent_soft_bg}; color: {txt};
            font-size: 11px; font-weight: 700; padding: 0 14px; min-height: 32px;
        }}
        QPushButton#CardHeaderAction:hover {{ border-color: {accent_soft_br}; background-color: {hov}; color: #007AFF; }}
        QPushButton#CardMenuBtn {{
            border: 1px solid {brd}; border-radius: 12px; background-color: transparent; color: {sub};
            font-size: 13px; font-weight: 800; letter-spacing: 0.5px; min-width: 40px; min-height: 30px;
        }}
        QPushButton#CardMenuBtn:hover {{ color: #007AFF; border-color: {accent_soft_br}; background-color: {accent_soft_bg}; }}
        QPushButton#RecToggle {{ font-size: 8px; font-weight: 900; border-radius: 8px; border: 1px solid {brd}; background: transparent; color: {sub}; padding: 2px 6px; min-height: 22px; }}
        QPushButton#RecToggle:checked {{ background-color: #EF4444; color: white; border-color: #EF4444; }}
        QPushButton#BtnTextLink {{ background: transparent; color: #007AFF; font-weight: 700; font-size: 13px; border: none; padding: 10px 8px; }}
        QPushButton#BtnTextLink:hover {{ color: #409CFF; }}
        QPushButton#ImgPurgeBtn {{ background-color: rgba(239, 68, 68, 0.12); color: #EF4444; border-radius: 10px; font-size: 10px; font-weight: 800; border: 1px solid rgba(239, 68, 68, 0.28); padding: 4px 10px; min-height: 26px; }}
        QPushButton#ImgPurgeBtn:hover {{ background-color: #EF4444; color: white; border-color: #EF4444; }}
        QPushButton#RowMuted {{ border: 1px solid {brd}; border-radius: 10px; background: transparent; color: {sub}; font-size: 9px; font-weight: 700; padding: 4px 10px; min-height: 26px; }}
        QPushButton#RowMuted:hover {{ border-color: {sub}; color: {txt}; background-color: {hov}; }}
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
        QMessageBox QPushButton:hover {{ border-color: #007AFF; color: #007AFF; background-color: rgba(0,122,255,0.08); }}
        QStackedWidget {{ background-color: transparent; }}
        QWidget#Overlay {{ background-color: {ov_bg}; }}
        QPlainTextEdit {{ background-color: {ibg}; border: 1px solid {brd}; border-radius: 14px; color: {txt}; padding: 8px; font-family: 'SF Mono', 'Consolas', monospace; font-size: 12px; }}
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
        use_gst = isinstance(sid, int) and platform.system() == "Linux" and os.path.exists("/usr/bin/nvgstcapture")
        if use_gst:
            opts = {**get_camera_defaults(), **(self.camera_options or {})}
            idx = opts.get("resolution_index", 0)
            idx = min(max(0, idx), len(CAMERA_RESOLUTION_PRESETS) - 1)
            w, h, fps = CAMERA_RESOLUTION_PRESETS[idx][1], CAMERA_RESOLUTION_PRESETS[idx][2], CAMERA_RESOLUTION_PRESETS[idx][3]
            aelock = opts.get("aelock", False)
            gst_str = build_gstreamer_pipeline(sid, width=w, height=h, fps=fps, aelock=aelock)
            return cv2.VideoCapture(gst_str, cv2.CAP_GSTREAMER)
        return cv2.VideoCapture(source)

    def run(self):
        source = self.src
        try:
            if str(source).isdigit(): source = int(source)
        except Exception:
            pass
        
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
        while self.running:
            if cap is None or not cap.isOpened():
                self.connection_signal.emit("reconnecting")
                log.warning("Reconnecting to %s", source)
                cap = self._open_capture(source)
                time.sleep(2); continue

            ret, frame = cap.read()
            if not ret:
                log.debug("Frame drop on %s", source)
                time.sleep(1)
                cap.release(); cap = None; continue

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
        for name, img, cid in self.db.get_workspaces():
            ckey = (cid or "")[:12]
            running = ckey in running_ids
            self.add_docker_card(name, img, cid, running=running, save=False)

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
        # Order: Cameras, Workspaces, Devices (next), Library, Settings
        nav_names = ["Cameras", "Workspaces", "Devices", "Library", "Settings"]
        for i, t in enumerate(nav_names):
            b = QPushButton(t); b.setObjectName("NavTab"); b.setCheckable(True); b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(46); b.setMinimumWidth(210)
            b.clicked.connect(lambda _, x=i: self.switch(x)); sl.addWidget(b); self.navs.append(b)
        sl.addStretch(); host = QLabel(platform.node()); host.setObjectName("SidebarFootnote"); sl.addWidget(host)
        self.main.addWidget(sb); self.main.addWidget(self.tabs)
        # Stack order matches nav: 0=cams, 1=docker, 2=devices, 3=library, 4=settings
        self.tabs.addWidget(self.page_cams()); self.tabs.addWidget(self.page_docker()); self.tabs.addWidget(self.page_devices()); self.tabs.addWidget(self.page_library()); self.tabs.addWidget(self.page_settings())
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
            "Connect to saved Jetson hosts over SSH (password in-app, not stored). "
            "When connected, list cameras on the device. ZeroTier peers and SSH keys are below."
        )
        ds.setObjectName("PageSubtitle")
        ds.setWordWrap(True)
        tb.addWidget(ds)
        l.addLayout(tb)
        l.addSpacing(16)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(make_icon_refresh_button(self.refresh_devices_page, "Refresh device list"))
        l.addLayout(h)
        l.setSpacing(28)
        default_ssh_user = os.getenv("ZEROTIER_SSH_USER", "jetson")
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setStyleSheet("background: transparent; border: none;")
        dev_content = QWidget(); dev_layout = QVBoxLayout(dev_content); dev_layout.setSpacing(24); dev_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.dev_zerotier_container = QWidget(); self.dev_zt_layout = QVBoxLayout(self.dev_zerotier_container); self.dev_zt_layout.setContentsMargins(0,0,0,0)
        self.dev_saved_container = QWidget(); self.dev_saved_layout = QVBoxLayout(self.dev_saved_container); self.dev_saved_layout.setContentsMargins(0,0,0,0)
        self.dev_sshkey_container = QWidget(); self.dev_sshkey_layout = QVBoxLayout(self.dev_sshkey_container); self.dev_sshkey_layout.setContentsMargins(0,0,0,0)
        zt_title = QLabel("ZeroTier devices")
        zt_title.setObjectName("SectionHeading")
        dev_layout.addWidget(zt_title)
        zt_card = QFrame()
        zt_card.setObjectName("InfoCard")
        zt_card_l = QVBoxLayout(zt_card)
        zt_card_l.setContentsMargins(22, 20, 22, 22)
        zt_card_l.setSpacing(10)
        zt_card_l.addWidget(self.dev_zerotier_container)
        dev_layout.addWidget(zt_card)
        zt_saved_hint = QLabel(
            "Add a ZeroTier peer IP under Saved devices to connect with password here and refresh the camera list."
        )
        zt_saved_hint.setObjectName("CaptionMuted")
        zt_saved_hint.setWordWrap(True)
        dev_layout.addWidget(zt_saved_hint)
        kayit_title = QLabel("Saved devices")
        kayit_title.setObjectName("SectionHeading")
        kayit_h = QHBoxLayout()
        kayit_h.addWidget(kayit_title)
        kayit_h.addStretch()
        add_dev_btn = QPushButton("Add device"); add_dev_btn.setObjectName("ShellBtn"); add_dev_btn.setCursor(Qt.CursorShape.PointingHandCursor); add_dev_btn.clicked.connect(lambda: self._modal_add_device(None))
        kayit_h.addWidget(add_dev_btn); dev_layout.addLayout(kayit_h)
        ssh_trust = QLabel(
            "Passwords are never saved. Disconnect or quit the app to end SSH. "
            "First-time host keys are accepted automatically—use only on trusted networks (e.g. ZeroTier)."
        )
        ssh_trust.setObjectName("CaptionMuted")
        ssh_trust.setWordWrap(True)
        dev_layout.addWidget(ssh_trust)
        saved_card = QFrame()
        saved_card.setObjectName("InfoCard")
        saved_card_l = QVBoxLayout(saved_card)
        saved_card_l.setContentsMargins(22, 20, 22, 22)
        saved_card_l.setSpacing(10)
        saved_card_l.addWidget(self.dev_saved_container)
        dev_layout.addWidget(saved_card)
        ssh_title = QLabel("SSH key")
        ssh_title.setObjectName("SectionHeading")
        dev_layout.addWidget(ssh_title)
        key_btn = QPushButton("Generate key (ssh-keygen)"); key_btn.setObjectName("ShellBtn"); key_btn.setCursor(Qt.CursorShape.PointingHandCursor); key_btn.clicked.connect(self._run_ssh_keygen)
        self.dev_sshkey_layout.addWidget(key_btn)
        if getattr(self, "_production_mode", False):
            key_btn.hide()
        ssh_help = QLabel("Use 'Open in terminal' for password login. For key login, add your public key to the device (ssh-copy-id).")
        ssh_help.setObjectName("CaptionMuted")
        self.dev_sshkey_layout.addWidget(ssh_help)
        ssh_card = QFrame()
        ssh_card.setObjectName("InfoCard")
        ssh_card_l = QVBoxLayout(ssh_card)
        ssh_card_l.setContentsMargins(22, 20, 22, 22)
        ssh_card_l.setSpacing(12)
        ssh_card_l.addWidget(self.dev_sshkey_container)
        dev_layout.addWidget(ssh_card)
        sa.setWidget(dev_content); polish_scroll_area(sa); l.addWidget(sa)
        QTimer.singleShot(200, self.refresh_devices_page)
        # Periodic ZT peer refresh every 30 seconds
        self._zt_peer_refresh_timer = QTimer(w)
        self._zt_peer_refresh_timer.setInterval(30000)
        self._zt_peer_refresh_timer.timeout.connect(self.refresh_devices_page)
        self._zt_peer_refresh_timer.start()
        return w

    def refresh_devices_page(self):
        default_ssh_user = os.getenv("ZEROTIER_SSH_USER", "jetson")
        while self.dev_zt_layout.count():
            item = self.dev_zt_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        peers = get_zerotier_peers()
        leaf_peers = [p for p in peers if p.get("role") == "LEAF"]
        if not leaf_peers:
            empty_zt = QLabel("No devices visible on ZeroTier. Join a network and wait for peers.")
            empty_zt.setObjectName("CaptionMuted")
            self.dev_zt_layout.addWidget(empty_zt)
        else:
            # ZT peer listesi için uyarı notu: gösterilen IP fiziksel path IP'sidir
            path_ip_warn = QLabel(
                "⚠ Aşağıdaki IP'ler ZeroTier sanal IP'si değil, fiziksel bağlantı (LAN/WAN) IP'sidir. "
                "Gerçek ZT sanal IP için cihaza SSH ile bağlanın → 'Saved devices' bölümünden ZT IP'yi alın."
            )
            path_ip_warn.setObjectName("CaptionMuted")
            path_ip_warn.setWordWrap(True)
            path_ip_warn.setStyleSheet("color: #FF9F0A; font-size: 11px; font-weight: 600; padding: 4px 0 8px 0;")
            self.dev_zt_layout.addWidget(path_ip_warn)
            header_w = QWidget(); header_w.setStyleSheet("border:none; background:transparent;"); hl = QHBoxLayout(header_w); hl.setContentsMargins(12,0,12,6)
            ip_h = QLabel("Path IP (fiziksel) ⚠")
            ip_h.setObjectName("CaptionMutedSm")
            ip_h.setToolTip("Bu IP ZeroTier sanal IP'si değil, fiziksel LAN/WAN IP'sidir.\nZT sanal IP için cihaza SSH bağlayın.")
            ip_h.setMinimumWidth(140)
            hl.addWidget(ip_h)
            u_h = QLabel("SSH user")
            u_h.setObjectName("CaptionMutedSm")
            u_h.setMinimumWidth(100)
            hl.addWidget(u_h)
            n_h = QLabel("Node")
            n_h.setObjectName("CaptionMutedSm")
            hl.addWidget(n_h)
            hl.addStretch(); self.dev_zt_layout.addWidget(header_w)
            for pr in leaf_peers:
                ip = (pr.get("ip") or "").strip()
                addr = pr.get("address", "—")
                row = surface_row()
                rl = QHBoxLayout(row); rl.setContentsMargins(12,10,12,10)
                ip_l = QLabel(ip or "—")
                ip_l.setObjectName("MonoIp")
                rl.addWidget(ip_l)
                user_edit = QLineEdit(); user_edit.setPlaceholderText("user"); user_edit.setText(default_ssh_user); user_edit.setFixedWidth(100)
                rl.addWidget(user_edit)
                addr_l = QLabel(addr)
                addr_l.setObjectName("MonoMuted")
                rl.addWidget(addr_l)
                def _copy_zt(u_edit, ip_addr):
                    cmd = f"ssh {(u_edit.text() or 'jetson')}@{ip_addr}"
                    QApplication.clipboard().setText(cmd); self.show_toast("SSH command copied")
                def _term_zt(u_edit, ip_addr):
                    open_ssh_in_terminal(u_edit.text() or "jetson", ip_addr); self.show_toast("Terminal opened")
                def _use_as_remote(ip_addr):
                    if getattr(self, "node_ip", None):
                        self.node_ip.setText(ip_addr)
                    self.switch(4)
                    self.show_toast(f"Remote host set: {ip_addr}")
                def _save_and_connect(u_edit, ip_addr, node_addr):
                    """Peer'ı saved devices'a kaydet ve SSH bağlantı dialogunu aç."""
                    if not ip_addr:
                        self.show_toast("IP bulunamadı — cihaz aktif bir path'e sahip değil")
                        return
                    usr = u_edit.text().strip() or "jetson"
                    dev_name = f"ZT-{node_addr[:6]}" if node_addr and node_addr != "—" else f"ZT-{ip_addr}"
                    # Zaten kayıtlı mı kontrol et
                    existing = [d for d in self.db.get_devices() if d[1] == ip_addr]
                    if not existing:
                        self.db.save_device(dev_name, ip_addr, usr)
                        self.show_toast(f"Cihaz kaydedildi: {dev_name}")
                    else:
                        self.show_toast(f"Zaten kayıtlı: {existing[0][0]}")
                    # SSH bağlantı dialogunu aç
                    self._ssh_show_connect_dialog(ip_addr, usr)
                copy_btn = QPushButton("Copy command")
                copy_btn.setObjectName("RowGhost")
                copy_btn.setFixedHeight(28)
                copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                copy_btn.clicked.connect(lambda _, ue=user_edit, ipa=ip: _copy_zt(ue, ipa) if ipa else None)
                term_btn = QPushButton("Open in terminal")
                term_btn.setObjectName("RowAccent")
                term_btn.setFixedHeight(28)
                term_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                term_btn.clicked.connect(lambda _, ue=user_edit, ipa=ip: _term_zt(ue, ipa) if ipa else None)
                use_host_btn = QPushButton("Use as Remote Host")
                use_host_btn.setObjectName("RowAccent")
                use_host_btn.setFixedHeight(28)
                use_host_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                use_host_btn.setToolTip("Set this IP as the Docker remote host in Settings")
                use_host_btn.setEnabled(bool(ip))
                use_host_btn.clicked.connect(lambda _, ipa=ip: _use_as_remote(ipa) if ipa else None)
                save_conn_btn = QPushButton("Save & Connect")
                save_conn_btn.setObjectName("BtnPrimary")
                save_conn_btn.setFixedHeight(28)
                save_conn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                save_conn_btn.setToolTip(
                    "Bu peer'ı Saved Devices'a kaydet ve SSH bağlantısı kur.\n"
                    "Bağlandıktan sonra ZT sanal IP ve kamera listesi görünür."
                )
                save_conn_btn.setEnabled(bool(ip))
                save_conn_btn.clicked.connect(
                    lambda _, ue=user_edit, ipa=ip, na=addr: _save_and_connect(ue, ipa, na)
                )
                rl.addWidget(copy_btn); rl.addWidget(term_btn); rl.addWidget(use_host_btn); rl.addWidget(save_conn_btn)
                self.dev_zt_layout.addWidget(row)
        while self.dev_saved_layout.count():
            item = self.dev_saved_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        devices_list = self.db.get_devices()
        if not devices_list:
            hint = QLabel('No saved devices yet. Use "Add device" to store IP/host and SSH details.')
            hint.setObjectName("CaptionMuted")
            hint.setStyleSheet("padding: 8px 0;")
            self.dev_saved_layout.addWidget(hint)
        for name, host, user, key_path in devices_list:
            u = user or "jetson"
            connected = self._is_ssh_device_connected(host, u)
            card = QFrame()
            card.setObjectName("InfoCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(22, 18, 22, 20)
            cl.setSpacing(12)
            row_top = QHBoxLayout()
            nm = QLabel(name or "Device")
            nm.setObjectName("ListPrimary")
            row_top.addWidget(nm)
            row_top.addStretch()
            st = QLabel("Connected" if connected else "Not connected")
            pal = ThemeOps.palette(self.is_dark)
            if connected:
                st.setStyleSheet(
                    "color:#30D158;font-weight:800;font-size:11px;letter-spacing:0.06em;border:none;background:transparent;"
                )
            else:
                st.setStyleSheet(
                    f"color:{pal['sub']};font-weight:700;font-size:11px;letter-spacing:0.04em;border:none;background:transparent;"
                )
            row_top.addWidget(st)
            cl.addLayout(row_top)
            meta = QLabel(f"{host}  ·  SSH {u}")
            meta.setObjectName("MonoIp")
            meta.setStyleSheet(f"font-size:12px;border:none;background:transparent;color:{pal['txt']};")
            cl.addWidget(meta)
            primary_row = QHBoxLayout()
            primary_row.setSpacing(10)
            if connected:
                dcb = QPushButton("Disconnect")
                dcb.setObjectName("RowGhost")
                dcb.setFixedHeight(34)
                dcb.setCursor(Qt.CursorShape.PointingHandCursor)
                dcb.clicked.connect(lambda _, h=host, us=u: (self._ssh_disconnect_device(h, us), self.refresh_devices_page()))
                primary_row.addWidget(dcb)
            else:
                conb = QPushButton("Connect…")
                conb.setObjectName("BtnPrimary")
                conb.setFixedHeight(36)
                conb.setMinimumWidth(140)
                conb.setCursor(Qt.CursorShape.PointingHandCursor)
                conb.clicked.connect(lambda _, h=host, us=u: self._ssh_show_connect_dialog(h, us))
                primary_row.addWidget(conb)
            primary_row.addStretch()
            cl.addLayout(primary_row)
            if connected:
                # --- ZeroTier sanal IP bölümü ---
                ssh_key = ssh_session_key(host, u)
                ssh_client = self._ssh_sessions.get(ssh_key)
                zt_ips = ssh_get_zerotier_ips(ssh_client) if ssh_client else []
                if zt_ips:
                    zt_head = QLabel("ZeroTier Virtual IP")
                    zt_head.setObjectName("NetworkPanelHeading")
                    cl.addWidget(zt_head)
                    zt_inner = QFrame()
                    zt_inner.setObjectName("FocusInner")
                    zt_il = QHBoxLayout(zt_inner)
                    zt_il.setContentsMargins(12, 10, 12, 10)
                    zt_il.setSpacing(10)
                    zt_ip_lbl = QLabel("  ".join(zt_ips))
                    zt_ip_lbl.setObjectName("MonoIp")
                    zt_il.addWidget(zt_ip_lbl)
                    zt_il.addStretch()
                    for zt_ip in zt_ips:
                        def _use_zt_ip(ip=zt_ip):
                            if getattr(self, "node_ip", None):
                                self.node_ip.setText(ip)
                            self.switch(4)
                            self.show_toast(f"Remote host (ZT) ayarlandı: {ip}")
                        use_zt_btn = QPushButton(f"Use as Remote Host")
                        use_zt_btn.setObjectName("RowAccent")
                        use_zt_btn.setFixedHeight(28)
                        use_zt_btn.setToolTip(f"Docker remote host olarak ZT sanal IP'yi ayarla: {zt_ip}")
                        use_zt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                        use_zt_btn.clicked.connect(_use_zt_ip)
                        zt_il.addWidget(use_zt_btn)
                    cl.addWidget(zt_inner)

                # --- Kameralar bölümü ---
                cam_head = QLabel("Cameras on device")
                cam_head.setObjectName("NetworkPanelHeading")
                cl.addWidget(cam_head)
                inner = QFrame()
                inner.setObjectName("FocusInner")
                il = QVBoxLayout(inner)
                il.setContentsMargins(12, 12, 12, 12)
                il.setSpacing(6)
                lines = self._ssh_list_camera_lines(host, u)
                # Yayın eklemek için kullanılacak IP: ZT sanal IP varsa onu, yoksa host IP'sini kullan
                stream_ip = zt_ips[0] if zt_ips else host
                if not lines:
                    empty = QLabel("No entries returned. Use page refresh after fixing the device.")
                    empty.setObjectName("CaptionMuted")
                    il.addWidget(empty)
                else:
                    for line in lines:
                        cam_row = QHBoxLayout()
                        cam_row.setSpacing(8)
                        ln = QLabel(line)
                        ln.setObjectName("MonoMuted")
                        ln.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                        ln.setWordWrap(True)
                        cam_row.addWidget(ln, 1)
                        # "Add to Broadcasts" butonu: RTSP URL ile kamera ekle
                        def _add_cam_from_ssh(cam_line=line, sip=stream_ip, dev_name=name):
                            # Kamera indeksini /dev/video0 → 0 olarak çıkar
                            import re as _re
                            m = _re.search(r"/dev/video(\d+)", cam_line)
                            cam_idx = m.group(1) if m else "0"
                            default_url = f"rtsp://{sip}:8554/video{cam_idx}"
                            # Küçük dialog: URL düzenlenebilir
                            dlg = QDialog(self)
                            dlg.setWindowTitle("Add Camera to Broadcasts")
                            dlg.setFixedWidth(480)
                            dlg_l = QVBoxLayout(dlg)
                            dlg_l.setContentsMargins(20, 20, 20, 20)
                            dlg_l.setSpacing(12)
                            info = QLabel(
                                f"<b>{dev_name}</b> cihazındaki <b>{cam_line}</b> kamerasını Broadcasts'e ekle.<br>"
                                f"ZT sanal IP: <b>{sip}</b><br><br>"
                                "RTSP URL'yi düzenleyin (Jetson'da çalışan RTSP sunucusuna göre):"
                            )
                            info.setWordWrap(True)
                            dlg_l.addWidget(info)
                            url_edit = QLineEdit(default_url)
                            url_edit.setPlaceholderText("rtsp://10.x.x.x:8554/stream")
                            dlg_l.addWidget(url_edit)
                            name_edit = QLineEdit(f"{dev_name} — {cam_line}")
                            name_edit.setPlaceholderText("Kamera adı")
                            dlg_l.addWidget(name_edit)
                            btns = QDialogButtonBox(
                                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                            )
                            btns.accepted.connect(dlg.accept)
                            btns.rejected.connect(dlg.reject)
                            dlg_l.addWidget(btns)
                            if dlg.exec() == QDialog.DialogCode.Accepted:
                                url = url_edit.text().strip()
                                cam_name = name_edit.text().strip() or f"{dev_name} cam"
                                if url:
                                    self.add_cam_logic(cam_name, url, "Stream|STANDARD|AUTO")
                                    self.switch(0)
                                    self.show_toast(f"Kamera eklendi: {cam_name}")
                        add_btn = QPushButton("Add to Broadcasts")
                        add_btn.setObjectName("RowAccent")
                        add_btn.setFixedHeight(26)
                        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                        add_btn.setToolTip(f"Bu kamerayı Broadcasts sayfasına RTSP stream olarak ekle (ZT IP: {stream_ip})")
                        add_btn.clicked.connect(_add_cam_from_ssh)
                        cam_row.addWidget(add_btn)
                        il.addLayout(cam_row)
                cl.addWidget(inner)
            util_row = QHBoxLayout()
            util_row.setSpacing(8)
            copy_btn = QPushButton("Copy SSH")
            copy_btn.setObjectName("RowGhost")
            copy_btn.setFixedHeight(28)
            copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            def _copy_saved(h=host, us=u, k=key_path):
                cmd = f"ssh -i {k} {us}@{h}" if (k and os.path.exists(os.path.expanduser(k))) else f"ssh {us}@{h}"
                QApplication.clipboard().setText(cmd)
                self.show_toast("SSH command copied")

            copy_btn.clicked.connect(_copy_saved)
            term_btn = QPushButton("Terminal")
            term_btn.setObjectName("RowAccent")
            term_btn.setFixedHeight(28)
            term_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            term_btn.clicked.connect(
                lambda _, h=host, us=u, k=key_path: (open_ssh_in_terminal(us, h, k), self.show_toast("Terminal opened"))
            )
            edit_btn = QPushButton("Edit")
            edit_btn.setObjectName("RowMuted")
            edit_btn.setFixedHeight(28)
            edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            edit_btn.clicked.connect(
                lambda _, n=name, h=host, us=u, k=key_path: self._modal_add_device((n, h, us or "", k or ""))
            )
            del_btn = QPushButton("Remove")
            del_btn.setObjectName("RowMuted")
            del_btn.setFixedHeight(28)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            def _del(h, us):
                self._ssh_disconnect_device(h, us)
                self.db.remove_device(h)
                self.refresh_devices_page()
                self.show_toast("Device removed")

            del_btn.clicked.connect(lambda _, h=host, us=u: _del(h, us))
            util_row.addWidget(copy_btn)
            util_row.addWidget(term_btn)
            util_row.addWidget(edit_btn)
            util_row.addWidget(del_btn)
            util_row.addStretch()
            cl.addLayout(util_row)
            self.dev_saved_layout.addWidget(card)

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
        btn = QPushButton("Save"); btn.setObjectName("BtnPrimary"); btn.clicked.connect(save_dev); bl.addWidget(btn)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(ov.deleteLater); bl.addWidget(cancel)
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
        old = self._ssh_sessions.pop(key, None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass

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

    def _ssh_show_connect_dialog(self, host, user):
        if not (host or "").strip():
            QMessageBox.warning(self, "SSH", "Host is empty.")
            return
        u = (user or "jetson").strip()
        dlg = QDialog(self)
        dlg.setWindowTitle("SSH — connect")
        dlg.setModal(True)
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
        fl.addRow("Host:", ht)
        fl.addRow("User:", QLabel(u))
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        pw.setPlaceholderText("SSH password")
        fl.addRow("Password:", pw)
        hint = QLabel("Password is used only for this connection and is not saved.")
        hint.setObjectName("CaptionMuted")
        hint.setWordWrap(True)
        fl.addRow(hint)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        fl.addRow(bb)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        password = pw.text()
        client, err = ssh_connect_with_password(host.strip(), u, password)
        if err:
            QMessageBox.warning(self, "SSH connection failed", err)
            return
        self._ssh_disconnect_device(host, u)
        self._ssh_sessions[ssh_session_key(host, u)] = client
        self.show_toast("Connected")
        self.refresh_devices_page()

    def page_settings(self):
        sw = QScrollArea(); sw.setWidgetResizable(True); sw.setStyleSheet("background: transparent; border: none;")
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(40,40,40,40); l.setSpacing(40)
        sw.setWidget(w); polish_scroll_area(sw)
        
        # Section 1: Metrics
        l.addWidget(settings_title("Live system metrics"))
        metrics_card = QFrame()
        metrics_card.setObjectName("InfoCard")
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

        # Section 3: Professional Health Monitor
        l.addWidget(settings_title("System health & connectivity"))
        sys_info = QFrame(); sys_info.setObjectName("InfoCard"); sl_info = QVBoxLayout(sys_info); sl_info.setContentsMargins(25,25,25,25); sl_info.setSpacing(15)
        
        def add_status(lbl, val):
            row = QHBoxLayout()
            ll = QLabel(lbl)
            ll.setObjectName("FormLabel")
            row.addWidget(ll)
            row.addStretch()
            vl = QLabel(val)
            vl.setStyleSheet("color: #007AFF; font-size: 13px; font-weight: 700; border: none;")
            row.addWidget(vl)
            sl_info.addLayout(row)

        # Dynamic Docker status row — color-coded + "Start Docker" button when offline
        _docker_running = DockerManager.is_running()
        _docker_row = QHBoxLayout()
        _docker_lbl = QLabel("Docker engine"); _docker_lbl.setObjectName("FormLabel")
        _docker_row.addWidget(_docker_lbl); _docker_row.addStretch()
        self._docker_status_lbl = QLabel("ACTIVE" if _docker_running else "OFFLINE")
        self._docker_status_lbl.setStyleSheet(
            f"color: {'#30D158' if _docker_running else '#FF453A'}; font-size: 13px; font-weight: 700; border: none;"
        )
        _docker_row.addWidget(self._docker_status_lbl)
        self._docker_start_btn = QPushButton("Start Docker")
        self._docker_start_btn.setObjectName("RowGhost")
        self._docker_start_btn.setFixedHeight(28)
        self._docker_start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._docker_start_btn.setToolTip("Launch Docker Desktop / daemon on this machine")
        self._docker_start_btn.setVisible(not _docker_running)
        def _start_docker_clicked():
            DockerManager.start_service()
            self.show_toast("Starting Docker…")
            QTimer.singleShot(4500, self._refresh_docker_status)
        self._docker_start_btn.clicked.connect(_start_docker_clicked)
        _docker_row.addWidget(self._docker_start_btn)
        sl_info.addLayout(_docker_row)
        cameras = list_cameras()
        add_status("Cameras", ", ".join(str(c[1]) for c in cameras) if cameras else "None")
        add_status("Platform", f"{platform.system()} {platform.machine()}")
        l.addWidget(sys_info)

        # Devices shortcut: SSH, ZeroTier, saved devices — quick link from Settings
        dev_shortcut = QFrame(); dev_shortcut.setObjectName("InfoCard"); dsl = QVBoxLayout(dev_shortcut); dsl.setContentsMargins(25,20,25,20); dsl.setSpacing(12)
        dsh = QLabel("Devices")
        dsh.setObjectName("SectionHeading")
        dsl.addWidget(dsh)
        dss = QLabel("ZeroTier peers, saved SSH devices, and ssh-keygen.")
        dss.setObjectName("CaptionMuted")
        dsl.addWidget(dss)
        go_dev = QPushButton("Open Devices page"); go_dev.setObjectName("ShellBtn"); go_dev.setCursor(Qt.CursorShape.PointingHandCursor); go_dev.setMaximumWidth(240)
        go_dev.clicked.connect(lambda: self.switch(2)); dsl.addWidget(go_dev)
        l.addWidget(dev_shortcut)

        # Network & remote host (English, professional)
        l.addWidget(settings_title("Network & remote host"))
        conn_box = QFrame(); conn_box.setObjectName("InfoCard"); cl = QVBoxLayout(conn_box); cl.setContentsMargins(25,25,25,25); cl.setSpacing(18)

        node_row = QHBoxLayout()
        node_row.addWidget(form_label("Remote host (IP):"))
        self.node_ip = QLineEdit()
        self.node_ip.setPlaceholderText("e.g. 10.144.1.5")
        self.node_ip.setFixedWidth(220)
        env_r = (os.getenv("JETSON_REMOTE") or "").strip()
        file_r = "" if env_r else (load_app_prefs().get("remote_host_ip") or "").strip()
        start_ip = env_r or file_r
        self.node_ip.setText(start_ip)
        if start_ip:
            DockerManager.set_host(start_ip)
        self._remote_pref_timer = QTimer(self)
        self._remote_pref_timer.setSingleShot(True)
        self._remote_pref_timer.timeout.connect(self._persist_remote_host_pref)
        self.node_ip.textChanged.connect(self._on_remote_host_text_changed)
        if getattr(self, "_remote_host_locked", False):
            self.node_ip.setReadOnly(True)
            self.node_ip.setToolTip("Remote IP locked (VISIONDOCK_LOCK_REMOTE_HOST). Unset the variable to edit.")
        node_row.addStretch()
        node_row.addWidget(self.node_ip)
        cl.addLayout(node_row)

        status_row = QHBoxLayout()
        status_row.addWidget(form_label("Status:"))
        self.remote_node_status_label = QLabel("—"); self.remote_node_status_label.setStyleSheet("font-size:13px; font-weight:700;")
        status_row.addStretch(); status_row.addWidget(self.remote_node_status_label); cl.addLayout(status_row)
        self._remote_status_timer = None
        self._remote_status_thread = None

        # Remote Docker setup guide — how to expose port 2375 on Jetson
        cl.addWidget(hairline())
        rdg = QFrame(); rdg.setObjectName("SettingsInset")
        rdg_l = QVBoxLayout(rdg); rdg_l.setContentsMargins(16, 14, 16, 14); rdg_l.setSpacing(8)
        rdg_title = QLabel("Remote Docker — Jetson setup"); rdg_title.setObjectName("FormLabel")
        rdg_l.addWidget(rdg_title)
        rdg_hint = QLabel(
            "Run on Jetson to expose the Docker API on port 2375 (ZeroTier network). "
            "Then enter the Jetson ZeroTier IP above as Remote host."
        )
        rdg_hint.setObjectName("CaptionMuted"); rdg_hint.setWordWrap(True)
        rdg_l.addWidget(rdg_hint)
        _daemon_json = '{"hosts":["unix:///var/run/docker.sock","tcp://0.0.0.0:2375"]}'
        _daemon_cmd = (
            f"echo '{_daemon_json}' | sudo tee /etc/docker/daemon.json "
            "&& sudo systemctl restart docker"
        )
        rdg_cmd_lbl = QLabel(_daemon_cmd)
        rdg_cmd_lbl.setObjectName("MonoMuted"); rdg_cmd_lbl.setWordWrap(True)
        rdg_cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rdg_l.addWidget(rdg_cmd_lbl)
        _copy_rdg_btn = QPushButton("Copy command"); _copy_rdg_btn.setObjectName("RowGhost")
        _copy_rdg_btn.setFixedHeight(28); _copy_rdg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _copy_rdg_btn.clicked.connect(
            lambda: [QApplication.clipboard().setText(_daemon_cmd), self.show_toast("Command copied")]
        )
        rdg_l.addWidget(_copy_rdg_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        cl.addWidget(rdg)

        # ZeroTier: multiple networks list + device/peer count
        zt_networks = get_zerotier_networks()
        peer_count = get_zerotier_peer_count()
        zt_status = get_zerotier_status()
        zt_local_ips = get_zerotier_local_ips()
        if zt_networks or peer_count is not None or zt_status != "NOT INSTALLED":
            cl.addWidget(hairline())
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
            status_dot.setStyleSheet(f"color: {_status_color}; font-size: 11px; font-weight: 600;")
            zt_hdr.addWidget(status_dot)
            zt_inner.addLayout(zt_hdr)
            # --- Local ZT virtual IPs (this machine's assignedAddresses) ---
            if zt_local_ips:
                local_ip_row = QHBoxLayout()
                local_ip_lbl = QLabel("Your ZeroTier IP:")
                local_ip_lbl.setStyleSheet("font-size: 12px; color: #8E8E93;")
                local_ip_row.addWidget(local_ip_lbl)
                local_ip_val = QLabel("  ".join(zt_local_ips))
                local_ip_val.setStyleSheet("font-size: 12px; font-weight: 600;")
                local_ip_val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                local_ip_row.addWidget(local_ip_val)
                local_ip_row.addStretch(1)
                _copy_lip_btn = QPushButton("Copy")
                _copy_lip_btn.setObjectName("RowGhost")
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
            pc.setStyleSheet("font-size: 15px; font-weight: 800; color: #007AFF; border: none;")
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
                        copy_btn.setObjectName("RowGhost")
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
                    zt_inner.addWidget(peer_row)
                leaf_hint = QLabel(
                    "LEAF = edge device. 'Copy SSH' copies ssh user@IP (default user: jetson; override with ZEROTIER_SSH_USER)."
                )
                leaf_hint.setObjectName("CaptionMuted")
                leaf_hint.setStyleSheet("font-size: 11px; font-weight: 600; padding-top: 4px;")
                zt_inner.addWidget(leaf_hint)
            cl.addWidget(zt_panel)

        l.addWidget(conn_box)

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
        for i, preset in enumerate(CAMERA_RESOLUTION_PRESETS):
            self.camera_res_combo.addItem(preset[0], i)
        self.camera_res_combo.setCurrentIndex(min(defs["resolution_index"], self.camera_res_combo.count() - 1))
        self.camera_res_combo.currentIndexChanged.connect(lambda i: set_camera_defaults(resolution_index=i))
        cam_form.addRow(form_label("Resolution"), self.camera_res_combo)
        aelock_cell = QWidget()
        ael_h = QHBoxLayout(aelock_cell)
        ael_h.setContentsMargins(0, 0, 0, 0)
        ael_h.addStretch()
        self.camera_aelock_switch = ToggleSwitch()
        self.camera_aelock_switch.setChecked(defs.get("aelock", False))
        self.camera_aelock_switch.toggled.connect(lambda c: [set_camera_defaults(aelock=c), self._update_aelock_sliders_visibility()])
        ael_h.addWidget(self.camera_aelock_switch)
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
        i2c_h.addStretch()
        i2c_h.addWidget(self.camera_i2c_spin)
        cam_form2.addRow(form_label("I2C bus (focus motor)"), i2c_cell)
        self.camera_sensor_spin = QSpinBox()
        self.camera_sensor_spin.setRange(0, 3)
        self.camera_sensor_spin.setValue(int(defs.get("sensor_id", 0)))
        self.camera_sensor_spin.valueChanged.connect(lambda v: set_camera_defaults(sensor_id=v))
        sid_cell = QWidget()
        sid_h = QHBoxLayout(sid_cell)
        sid_h.setContentsMargins(0, 0, 0, 0)
        sid_h.addStretch()
        sid_h.addWidget(self.camera_sensor_spin)
        cam_form2.addRow(form_label("CSI sensor-id (autofocus)"), sid_cell)
        i2c_hint = QLabel("Match I2C bus to your Jetson port (see docs). i2cset may require root or the i2c group.")
        i2c_hint.setObjectName("CaptionMuted")
        i2c_hint.setWordWrap(True)
        cam_form2.addRow(i2c_hint)
        self.camera_focus_combo = QComboBox()
        self.camera_focus_combo.setView(QListView())
        self.camera_focus_combo.setFixedHeight(40)
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
        fs_wrap.addWidget(self.camera_focus_slider)
        cdl.addWidget(fs_host)
        self.camera_focus_apply_btn = QPushButton("Set fixed position")
        self.camera_focus_apply_btn.setObjectName("BtnPrimary")
        self.camera_focus_apply_btn.setMinimumHeight(44)
        self.camera_focus_apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.camera_focus_apply_btn.clicked.connect(self._apply_focus_imx519)
        cdl.addWidget(self.camera_focus_apply_btn)
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
        
    def show_toast(self, txt):
        t = Toast(txt, self, self.is_dark)
        t.adjustSize()
        tw = t.sizeHint().width() or 280
        # Bottom-center of the main window, 80px above the bottom edge
        local_x = max(24, (self.width() - tw) // 2)
        local_y = self.height() - 90
        gp = self.mapToGlobal(QPoint(local_x, local_y))
        t.show_msg(gp.x(), gp.y())

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
        self._update_remote_status_label(online)
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
        lbl.setText("ACTIVE" if running else "OFFLINE")
        color = "#30D158" if running else "#FF453A"
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
        if i == 3:
            QTimer.singleShot(0, getattr(self, "refresh_library", lambda: None))
        if i == 4:  # Settings tab: refresh remote node status + Docker status
            self._schedule_remote_status_check()
            QTimer.singleShot(0, self._refresh_docker_status)
    def upd_stats(self, d): 
        for i, k in enumerate(['cpu','ram','disk','gpu']): self.charts[i].set_value(d[k])
    def toggle_theme(self, c): self.is_dark = c; self.apply_theme()
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
        zt_cam_label = QLabel("Quick:")
        zt_cam_btn = QPushButton("Use remote host URL")
        zt_cam_btn.setObjectName("ShellBtn")
        zt_cam_btn.setToolTip("Fill stream URL with rtsp://<Settings remote IP>:554/stream (ZeroTier camera)")
        zt_cam_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        # Helper: creates a themed, cross-platform-safe QComboBox
        # On Ubuntu/GTK, CSS height constraints alone are insufficient —
        # setFixedHeight + view().setMaximumHeight() must be set programmatically.
        # Also explicitly setting setView(QListView()) forces Qt to bypass
        # the unstylable native GTK popup rendering on Ubuntu.
        def make_combo():
            c = QComboBox()
            c.setView(QListView())
            c.setFixedHeight(40)
            c.setMaxVisibleItems(8)
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            c.view().setMaximumHeight(168)
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
            name_input = QLineEdit(); name_input.setPlaceholderText("Workspace name (e.g. Dev lab)")
            cat_combo = make_combo(); [cat_combo.addItem(i['name'], i['img']) for i in CatalogManager.get_recommended()[0]]
            run_target_combo = make_combo(); run_target_combo.addItem("Local", "")
            remote_ip = getattr(self, "node_ip", None)
            if remote_ip and getattr(remote_ip, "text", None):
                ip = remote_ip.text().strip()
                if ip:
                    online = check_remote_node_reachable(ip, port=2375, timeout=2)
                    run_target_combo.addItem(f"Remote ({ip}) • {'Online' if online else 'Offline'}", ip)

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
                    name_input.setText("ZeroTier camera")
            else:
                self.show_toast("Set Remote host (IP) in Settings first.")
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
                        QMessageBox.warning(ov, "Stream", "Enter a stream URL. Use \"Use remote host URL\" if the camera is on the device set in Settings.")
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
            engine = "STANDARD"
            target_size = None
            camera_options = dict(get_camera_defaults())
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
            if not str(src).startswith("docker://") and str(src).strip().isdigit():
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
            t.snap_done_signal.connect(card.on_snap_done)
            t.start(); card.t = t
        else:
            card.view.setText("No source signal")

    def _docker_safe_name(self, name):
        """User-provided name -> valid Docker container name ( [a-zA-Z0-9_.-], max 63 chars )."""
        if not (name or "").strip():
            return f"jetson_{''.join(random.choices(string.ascii_lowercase, k=5))}"
        s = re.sub(r"[^a-zA-Z0-9_.-]", "_", (name or "").strip())
        s = s.strip("_") or "workspace"
        return s[:63] if len(s) > 63 else s

    def add_doc_logic(self, name, img, target=None):
        if not img: return
        cn = self._docker_safe_name(name)
        card = ResizableCard(pretty_workspace_title(cn), img, True); card.trigger_delete_modal.connect(self.show_delete_confirmation); card.removed.connect(card.deleteLater); card.set_status_info("Pulling", "#0A84FF")
        card.db = self.db
        self.df.removeWidget(self.abd); self.df.addWidget(card); self.df.addWidget(self.abd)
        use_remote = target and str(target).strip()
        if use_remote:
            run_cmd = DockerManager.get_cmd(f"run -d --name {cn} --restart unless-stopped -w /workspace {img} sleep infinity", host_override=target)
        else:
            ws_base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "workspaces"))
            ws_dir = os.path.join(ws_base, cn); os.makedirs(ws_dir, exist_ok=True)
            run_cmd = DockerManager.get_cmd(f"run -d --name {cn} --restart unless-stopped -v \"{ws_dir}:/workspace\" -w /workspace {img} sleep infinity", host_override=target or "")
        card.w = DockerCreationThread(run_cmd)
        def on_created(o, s):
            if s:
                cid_norm = self._norm_cid(o)
                card.container_id = cid_norm
                card.set_status_info("Running","#30D158"); card.start_monitoring()
                self.db.save_workspace(cn, img, cid_norm)
            else:
                card.set_status_info("Error","#FF453A")
                raw = str(o).strip() if o else ""
                if not raw:
                    short, detail = "Container creation failed.", ""
                else:
                    short, detail = docker_cli_error_for_ui(raw)
                dlg = QMessageBox(self)
                dlg.setIcon(QMessageBox.Icon.Warning)
                dlg.setWindowTitle("Workspace")
                dlg.setText(short)
                if detail:
                    dlg.setDetailedText(detail)
                dlg.exec()
        card.w.result.connect(on_created); card.w.start()

    def _norm_cid(self, cid):
        """Normalize container id for matching (Docker may return 12- or 64-char ids)."""
        return (cid or "")[:12] if (cid or "") else ""

    def add_docker_card(self, n, i, c, running=True, save=True):
        if c is None or c == "": return
        ckey = self._norm_cid(c)
        if ckey and ckey in self.active_cids: return
        if ckey: self.active_cids.add(ckey)
        card = ResizableCard(pretty_workspace_title(n), i, True, c); card.trigger_delete_modal.connect(self.show_delete_confirmation); card.removed.connect(lambda: [self.active_cids.discard(self._norm_cid(c)), card.deleteLater()])
        card.db = self.db
        card.set_status_info("Running" if running else "Stopped", "#30D158" if running else "#FF453A")
        if running: card.start_monitoring()
        self.df.removeWidget(self.abd); self.df.addWidget(card); self.df.addWidget(self.abd)
        if save: self.db.save_workspace(n, i, c)

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
    w.show()
    sys.exit(app.exec())




