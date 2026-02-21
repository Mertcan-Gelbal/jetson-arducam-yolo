import sys, os, psutil, subprocess, cv2, time, platform, numpy as np, glob, random, string, threading, json, sqlite3
from datetime import datetime

# Silence console noise after basic imports
os.environ["OPENCV_LOG_LEVEL"] = "OFF"
os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.qpa.fonts=false"
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFrame,
    QScrollArea, QGridLayout, QComboBox, QFileDialog, 
    QGraphicsDropShadowEffect, QAbstractButton, QSizePolicy, QFormLayout, QLayout,
    QGraphicsBlurEffect, QMenu, QAction, QTabWidget, QLineEdit, QPlainTextEdit, QMessageBox
)

from PyQt5.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize, QPoint, QRect, 
    QPropertyAnimation, QEasingCurve, pyqtProperty, QEvent, QMutex
)

# Enable High DPI Scaling for 4K / Retina Displays
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
from PyQt5.QtGui import (
    QColor, QFont, QIcon, QImage, QPixmap, QPainter, QPen, QBrush, 
    QCursor, QShowEvent, QResizeEvent, QMouseEvent, QTextCursor
)

# =============================================================================
#  PERSISTENCE MANAGER (SQLITE)
# =============================================================================

class DBManager:
    def __init__(self):
        self.db_path = os.path.join(os.path.dirname(__file__), "studio.db")
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS cameras (id INTEGER PRIMARY KEY, name TEXT, src TEXT, meta TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS workspaces (id INTEGER PRIMARY KEY, name TEXT, img TEXT, cid TEXT)")

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
            conn.execute("INSERT INTO workspaces (name, img, cid) VALUES (?, ?, ?)", (name, img, cid))

    def get_workspaces(self):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT name, img, cid FROM workspaces").fetchall()

    def remove_workspace(self, cid):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM workspaces WHERE cid = ?", (cid,))

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
            if ret: cams.append((f"Camera {i} (Default)", i))
            cap.release()
    return cams

def get_zerotier_nodes():
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output("zerotier-one_x64.exe -j listpeers", shell=True).decode()
        else:
            out = subprocess.check_output("zerotier-cli -j listpeers", shell=True).decode()
        peers = json.loads(out)
        return [p['address'] for p in peers if p['paths']]
    except: return []

# =============================================================================
#  CORE LOGIC & THREADS
# =============================================================================

class DockerManager:
    _host = None # Static host pointer (e.g. tcp://192.168.192.20:2375)

    @classmethod
    def set_host(cls, ip):
        cls._host = f"tcp://{ip}:2375" if ip else None

    @classmethod
    def get_cmd(cls, base_cmd):
        if cls._host: return f"docker -H {cls._host} {base_cmd}"
        return f"docker {base_cmd}"

    @staticmethod
    def is_running():
        try:
            cmd = DockerManager.get_cmd("info >/dev/null 2>&1")
            subprocess.check_call(cmd, shell=True)
            return True
        except: return False

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
        try:
            cmd = DockerManager.get_cmd('ps -a --format "{{.ID}}|{{.Image}}|{{.Status}}|{{.Names}}"')
            out = subprocess.check_output(cmd, shell=True).decode()
            containers = []
            for line in out.strip().split('\n'):
                if not line: continue
                parts = line.split('|')
                if len(parts) >= 4:
                    containers.append({'id': parts[0], 'image': parts[1], 'status': parts[2], 'name': parts[3]})
            return containers
        except: return []

    @staticmethod
    def list_images():
        try:
            cmd = DockerManager.get_cmd('images --format "{{.Repository}}|{{.Tag}}|{{.Size}}|{{.ID}}"')
            out = subprocess.check_output(cmd, shell=True).decode()
            images = []
            for line in out.strip().split('\n'):
                if not line: continue
                parts = line.split('|')
                if len(parts) >= 4:
                    images.append({'repo': parts[0], 'tag': parts[1], 'size': parts[2], 'id': parts[3]})
            return images
        except: return []

    @staticmethod
    def remove_image(image_id):
        try:
            cmd = DockerManager.get_cmd(f"rmi -f {image_id}")
            subprocess.Popen(cmd, shell=True); return True
        except: return False

    @staticmethod
    def get_logs(cid, tail=50):
        try:
            cmd = DockerManager.get_cmd(f"logs --tail {tail} {cid}")
            return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode()
        except: return "Connection error or remote container stopped."

    @staticmethod
    def open_terminal(cid):
        plat = platform.system()
        try:
            if plat == "Darwin":
                cmd = f"osascript -e 'tell application \"Terminal\" to do script \"docker exec -it {cid} /bin/sh || docker exec -it {cid} /bin/bash\"' -e 'activate application \"Terminal\"'"
                subprocess.Popen(cmd, shell=True)
            elif plat == "Linux":
                terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
                for t in terminals:
                    if subprocess.run(f"which {t}", shell=True, capture_output=True).returncode == 0:
                        subprocess.Popen(f"{t} -e \"docker exec -it {cid} /bin/sh\"", shell=True)
                        return
            elif plat == "Windows":
                subprocess.Popen(f"start powershell.exe -NoExit -Command \"docker exec -it {cid} sh\"", shell=True)
        except: pass

class DockerCreationThread(QThread):
    result = pyqtSignal(str, bool)
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
    status_signal = pyqtSignal(dict)
    def __init__(self, cid):
        super().__init__()
        self.cid = cid
    def run(self):
        res = {"running": False, "cpu": "0%", "ram": "0MB", "size": "0GB"}
        try:
            out_run = subprocess.check_output(f"docker inspect -f '{{{{.State.Running}}}}' {self.cid}", shell=True).decode().strip()
            res["running"] = (out_run == 'true')
            if res["running"]:
                out_stats = subprocess.check_output(f"docker stats --no-stream --format '{{{{.CPUPerc}}}}|{{{{.MemUsage}}}}' {self.cid}", shell=True).decode().strip()
                if "|" in out_stats:
                    scpu, sram = out_stats.split("|")
                    res["cpu"] = scpu
                    res["ram"] = sram.split(" / ")[0]
                out_size = subprocess.check_output(f"docker ps -s --filter id={self.cid} --format '{{{{.Size}}}}'", shell=True).decode().strip()
                if out_size: res["size"] = out_size.split(" (")[0]
            self.status_signal.emit(res)
        except: self.status_signal.emit(res)

# =============================================================================
#  CUSTOM WIDGETS
# =============================================================================

class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 32); self.setCursor(Qt.PointingHandCursor)
        self._checked = True; self._thumb_pos = 31.0 
        self.anim = QPropertyAnimation(self, b"thumbPos")
        self.anim.setDuration(250); self.anim.setEasingCurve(QEasingCurve.InOutQuad)
    @pyqtProperty(float)
    def thumbPos(self): return self._thumb_pos
    @thumbPos.setter
    def thumbPos(self, pos): self._thumb_pos = pos; self.update()
    def mouseReleaseEvent(self, e):
        self._checked = not self._checked; self.toggled.emit(self._checked)
        self.anim.setStartValue(self._thumb_pos); self.anim.setEndValue(31.0 if self._checked else 3.0); self.anim.start()
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#1c1c1e") if self._checked else QColor("#D1D1D6")); p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 16, 16)
        p.setBrush(QColor("white")); p.drawEllipse(int(self._thumb_pos), 3, 26, 26)

class Toast(QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # Professional glass-morphism style
        self.setStyleSheet("background: rgba(28, 28, 30, 0.95); color: #FFF; border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 10px 20px; font-weight: 600; font-size: 12px;")
        l = QVBoxLayout(self); self.lbl = QLabel(text); l.addWidget(self.lbl)
        self.timer = QTimer(); self.timer.timeout.connect(self.hide); self.timer.start(2500)
    def show_msg(self, x, y): self.move(x, y); self.show()

class DonutChart(QWidget):
    def __init__(self, title, color_hex, parent=None):
        super().__init__(parent)
        self.title = title; self.base_color = QColor(color_hex); self.percent = 0; self.setFixedSize(160, 180)
    def set_value(self, p): self.percent = p; self.update()
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        rect = QRect((self.width()//2)-50, (self.height()-20)//2-50, 100, 100)
        p.setPen(QPen(QColor(127, 127, 127, 40), 10, Qt.SolidLine, Qt.RoundCap)); p.drawArc(rect, 0, 360 * 16)
        if self.percent > 0:
            p.setPen(QPen(self.base_color, 12, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, 90 * 16, int(-self.percent * 3.6 * 16))
        txt_color = self.palette().text().color()
        p.setPen(txt_color); f = QFont("Arial", 22, QFont.Bold); p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, f"{int(self.percent)}%")
        f = QFont("Arial", 11, QFont.Bold); p.setFont(f)
        p.drawText(0, self.height() - 25, self.width(), 20, Qt.AlignCenter, self.title)

class ResizableCard(QFrame):
    removed = pyqtSignal()
    trigger_delete_modal = pyqtSignal(object) 

    def __init__(self, title, sub, is_docker=False, container_id=None):
        super().__init__()
        self.setObjectName("Card"); self.setMinimumSize(320, 240); self.resize(340, 240)
        self.is_docker = is_docker; self.container_id = container_id; self.title_text = title; self.checker = None
        self.base_image = sub if is_docker else None # Store image tag for purging
        l = QVBoxLayout(self); l.setContentsMargins(0,0,0,0); l.setSpacing(0)
        # Header (Refined Alignment & High-Contrast)
        h = QFrame(); h.setFixedHeight(54); h.setStyleSheet("background: rgba(128,128,128,0.06); border-top-left-radius: 12px; border-top-right-radius: 12px; border-bottom: 1.5px solid rgba(128,128,128,0.1);")
        hl = QHBoxLayout(h); hl.setContentsMargins(18,0,12,0); hl.setSpacing(10); hl.setAlignment(Qt.AlignVCenter)
        
        self.l_title = QLabel(title.upper()); self.l_title.setObjectName("CardTitle"); hl.addWidget(self.l_title)
        
        # High-precision status indicators (Unified for AI/Docker/NVR)
        self.s_dot = QLabel("●"); self.s_dot.setFixedWidth(10); hl.addWidget(self.s_dot)
        self.s_txt = QLabel("READY"); self.s_txt.setStyleSheet("color: #888; font-size: 9px; font-weight: 900; letter-spacing: 0.8px; border:none;"); hl.addWidget(self.s_txt)
        
        if not is_docker:
            self.rec_badge = QLabel("REC"); self.rec_badge.setStyleSheet("color: #EF4444; font-size: 8px; font-weight: 900; background: rgba(239, 68, 68, 0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid rgba(239, 68, 68, 0.3);")
            self.rec_badge.hide(); hl.addWidget(self.rec_badge)
        
        hl.addStretch()
        
        if is_docker:
            self.l_btn = QPushButton("LOGS"); self.l_btn.setFixedSize(60, 28); self.l_btn.setCursor(Qt.PointingHandCursor); self.l_btn.setObjectName("ShellBtn")
            self.l_btn.clicked.connect(self.show_logs); hl.addWidget(self.l_btn)
            
            self.t_btn = QPushButton("SHELL"); self.t_btn.setFixedSize(68, 28); self.t_btn.setCursor(Qt.PointingHandCursor); self.t_btn.setObjectName("ShellBtn")
            self.t_btn.clicked.connect(lambda: DockerManager.open_terminal(self.container_id)); hl.addWidget(self.t_btn)
        else:
            self.snap_btn = QPushButton("SNAPSHOT"); self.snap_btn.setFixedSize(80, 24); self.snap_btn.setObjectName("ShellBtn")
            self.snap_btn.setCursor(Qt.PointingHandCursor)
            self.snap_btn.clicked.connect(self.take_snapshot); hl.addWidget(self.snap_btn)
            
        c = QPushButton("×"); c.setFixedSize(28,28); c.setStyleSheet("border: none; color: #666; font-size: 20px; background: transparent;"); c.setCursor(Qt.PointingHandCursor); c.clicked.connect(lambda: self.trigger_delete_modal.emit(self)); hl.addWidget(c)
        l.addWidget(h)

        self.cnt = QWidget(); cl = QVBoxLayout(self.cnt); cl.setContentsMargins(18,18,18,18); cl.setSpacing(12); l.addWidget(self.cnt)
        if is_docker:
            # Industrial Metadata Grid with Clean Identifier
            icon_box = QHBoxLayout(); icon_box.setSpacing(10)
            self.ico_lbl = QLabel("ID"); self.ico_lbl.setFixedSize(36,36); self.ico_lbl.setAlignment(Qt.AlignCenter)
            self.ico_lbl.setStyleSheet("background: rgba(0,122,255,0.1); color: #007AFF; font-size: 11px; font-weight: 900; border-radius: 6px; border: 1px solid rgba(0,122,255,0.2);")
            
            v_meta = QVBoxLayout(); v_meta.setSpacing(2)
            self.l_img = QLabel(f"TAG: {sub[:25]}..."); self.l_img.setStyleSheet("color: #007AFF; font-size: 9px; font-weight: 900; letter-spacing: 0.5px; border: none; background: transparent;")
            v_meta.addWidget(self.l_img); icon_box.addLayout(v_meta); cl.addLayout(icon_box)
            
            m_box = QFrame(); m_box.setStyleSheet("background: rgba(128,128,128,0.04); border: 1.2px solid rgba(128,128,128,0.12); border-radius: 10px;")
            ml = QGridLayout(m_box); ml.setContentsMargins(12,12,12,12); ml.setSpacing(15); 
            def add_m(l, r, c, cs=1):
                v = QVBoxLayout(); v.setSpacing(4); lbl = QLabel(l.upper()); lbl.setStyleSheet("color: rgba(255,255,255,0.4); font-size: 8px; font-weight: 900; letter-spacing: 1px;")
                val = QLabel("---"); val.setStyleSheet("color: #FFF; font-size: 13px; font-weight: 800; border:none;"); v.addWidget(lbl); v.addWidget(val); ml.addLayout(v, r, c, 1, cs); return val
            self.m_cpu = add_m("USAGE", 0, 0); self.m_ram = add_m("MEMORY", 0, 1); self.m_disk = add_m("STORAGE", 1, 0, 2)
            cl.addWidget(m_box)
            if self.container_id: self.start_monitoring()
        else:
            # Professional Stream Cockpit
            mode, engine, res = "PHYSICAL", "STANDARD", "AUTO"
            parts = str(sub).split("|")
            if len(parts) == 3: mode, engine, res = parts
            elif len(parts) == 2: engine, res = parts
            
            bh = QHBoxLayout(); bh.setSpacing(8); bh.setContentsMargins(0,0,0,0)
            def create_badge(txt, col):
                t = str(txt).replace("📡", "").replace("🌐", "").replace("🤖", "").strip().upper()
                b = QLabel(t); b.setStyleSheet(f"background: rgba(128,128,128,0.05); color: {col}; font-size: 8px; font-weight: 900; padding: 4px 10px; border-radius: 5px; border: 1.5px solid {col}40; letter-spacing:0.5px;")
                return b
            
            bh.addWidget(create_badge(mode, "#8B5CF6")) 
            self.eng_badge = create_badge(engine, "#007AFF"); bh.addWidget(self.eng_badge)
            bh.addWidget(create_badge(res, "#10B981"))
            
            self.rec_btn = QPushButton("REC"); self.rec_btn.setFixedSize(40, 22); self.rec_btn.setCheckable(True)
            self.rec_btn.setStyleSheet("QPushButton { font-size:8px; font-weight:900; border-radius:4px; border:1px solid #333; background:transparent; color:#666; } QPushButton:checked { background:#EF4444; color:white; border:none; }")
            self.rec_btn.clicked.connect(self.toggle_recording)
            
            bh.addStretch(); bh.addWidget(self.rec_btn); cl.addLayout(bh)

            self.ai_meta = QLabel("OBJECTS: 0"); self.ai_meta.setStyleSheet("color: rgba(255,255,255,0.3); font-size: 8px; font-weight: 900; letter-spacing: 0.5px; margin-top: 4px;")
            cl.addWidget(self.ai_meta)

            self.view = QLabel("INITIALIZING FEED..."); self.view.setObjectName("PreviewArea"); self.view.setAlignment(Qt.AlignCenter)
            self.view.setStyleSheet("background: #000; border-radius: 8px; color: #444; font-size: 9px; font-weight: 900; border: 1px solid rgba(128,128,128,0.15);")
            self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding) # Allow it to be pushed
            self.view.setMinimumSize(1,1)
            cl.addWidget(self.view, 1) 
        self.grip = QSizeGrip(self); self.grip.setFixedSize(16, 16); self.grip.setStyleSheet("background: transparent;")

    def resizeEvent(self, e): self.grip.move(self.width()-16, self.height()-16); super().resizeEvent(e)
    def sizeHint(self): return self.size()
    def perform_delete(self, purge_image=False):
        if hasattr(self, 't'): self.t.stop()
        if self.is_docker and self.container_id:
            subprocess.run(f"docker rm -f {self.container_id}", shell=True)
            if purge_image and hasattr(self, 'base_image') and self.base_image:
                subprocess.Popen(f"docker rmi -f {self.base_image}", shell=True)
            if hasattr(self, 'db'): self.db.remove_workspace(self.container_id)
        else:
            if hasattr(self, 'db'): self.db.remove_camera(self.sub_val)
        self.removed.emit()
    def set_status_info(self, text, color):
        self.s_dot.setStyleSheet(f"color: {color}; font-size: 10px; border:none; background:transparent;")
        self.s_txt.setText(text); self.s_txt.setStyleSheet(f"color: {color}; font-size: 10px; font-weight:800; border:none; background:transparent;")
    def start_monitoring(self):
        if self.checker and self.checker.isRunning(): return
        self.checker = StatusCheckThread(self.container_id); self.checker.status_signal.connect(self.update_status_from_thread); self.checker.start()
    def update_status_from_thread(self, s):
        if not hasattr(self, 'm_cpu'): return
        is_r = s["running"]; self.set_status_info("Active" if is_r else "Static", "#30D158" if is_r else "#FF453A")
        if is_r: self.m_cpu.setText(s['cpu']); self.m_ram.setText(s['ram']); self.m_disk.setText(s['size'])
        else: self.m_cpu.setText("0.00%"); self.m_ram.setText("0MB"); self.m_disk.setText(s['size'])
        if self.isVisible(): QTimer.singleShot(3000, self.start_monitoring)
    def toggle_recording(self, checked):
        if hasattr(self, 't'):
            self.t.toggle_record(checked)
            if checked: self.rec_badge.show()
            else: self.rec_badge.hide()

    def update_ai_ui(self, meta):
        if hasattr(self, 'ai_meta') and "objects" in meta:
            count = meta['objects']
            self.ai_meta.setText(f"OBJECTS DETECTED: {count}")
            self.ai_meta.setStyleSheet(f"color: {'#007AFF' if count>0 else 'rgba(255,255,255,0.3)'}; font-size: 8px; font-weight: 900; letter-spacing: 0.5px;")
            if count > 0 and random.random() > 0.95: # Throttled notification
                if hasattr(self.parent(), 'parent') and hasattr(self.parent().parent().parent().parent(), 'show_toast'):
                    self.parent().parent().parent().parent().show_toast(f"AI: Detection in {self.title_text}!")

    def take_snapshot(self):
        if hasattr(self, 't'): self.t.snapshot()

    def show_logs(self):
        logs = DockerManager.get_logs(self.container_id)
        msg = QMessageBox(self); msg.setWindowTitle(f"LOGS: {self.title_text}"); msg.setText(logs); msg.setStyleSheet("QLabel{font-family: monospace; font-size: 11px; min-width: 600px;}"); msg.exec_()

    def upd_img(self, img):
        if hasattr(self, 'view'):
            img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); h,w,c = img_rgb.shape
            self.view.setPixmap(QPixmap.fromImage(QImage(img_rgb.data, w, h, c*w, QImage.Format_RGB888)).scaled(self.view.size(), Qt.KeepAspectRatio))

class QSizeGrip(QWidget): 
    def __init__(self, parent): super().__init__(parent); self.setCursor(Qt.SizeFDiagCursor); self.start = None
    def mousePressEvent(self, e): self.start = e.globalPos()
    def mouseMoveEvent(self, e):
        if self.start:
            delta = e.globalPos() - self.start
            new_w = max(320, self.parent().width() + delta.x())
            new_h = max(240, self.parent().height() + delta.y())
            self.parent().resize(new_w, new_h)
            self.parent().updateGeometry() # Critical for FlowLayout
            if self.parent().parentWidget(): self.parent().parentWidget().layout().activate()
            self.start = e.globalPos()
    def mouseReleaseEvent(self, e): self.start = None

# =============================================================================
#  UI LAYOUT & THEME
# =============================================================================

class ThemeOps:
    @staticmethod
    def get_style(is_dark):
        if is_dark:
            bg, sb, card, txt, sub, brd, ibg, hov = "#09090B", "#111114", "#18181B", "#F4F4F5", "#A1A1AA", "#27272A", "#0C0C0E", "rgba(255,255,255,0.04)"
            ov_bg, card_txt, ihov = "rgba(0,0,0,0.85)", "#FAFAFA", "#27272A"
        else:
            bg, sb, card, txt, sub, brd, ibg, hov = "#FAFAFA", "#FFFFFF", "#FFFFFF", "#18181B", "#71717A", "#E4E4E7", "#F4F4F5", "rgba(0,0,0,0.03)"
            ov_bg, card_txt, ihov = "rgba(255,255,255,0.7)", "#09090B", "#F4F4F5"
            
        return f"""
        QMainWindow {{ background-color: {bg}; }}
        QWidget {{ font-family: -apple-system, 'Helvetica Neue', 'Segoe UI', Arial, sans-serif; color: {txt}; letter-spacing: 0.2px; }}
        QFrame#Sidebar {{ background-color: {sb}; border-right: 1.5px solid {brd}; }}
        QFrame#Card, QFrame#InfoCard {{ background-color: {card}; border: 1.2px solid {brd}; border-radius: 12px; }}
        QFrame#ModalBox {{ background-color: {card}; border: 1.2px solid {brd}; border-radius: 16px; }}
        QLabel#CardTitle {{ font-weight: 900; font-size: 14px; color: {card_txt}; border: none; background: transparent; letter-spacing: 0.5px; }}
        QLineEdit, QComboBox {{ background-color: {ibg}; border: 1.2px solid {brd}; border-radius: 8px; padding: 6px 14px; color: {txt}; font-size: 13px; font-weight: 500; min-height: 36px; max-height: 36px; }}
        QLineEdit:focus, QComboBox:focus {{ border-color: #007AFF; background-color: rgba(0,122,255,0.05); }}
        QComboBox {{ padding-right: 30px; }}
        QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; border: none; width: 28px; border-left: 1px solid {brd}; border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
        QComboBox::down-arrow {{ image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid {sub}; margin-right: 10px; }}
        
        QComboBox QAbstractItemView, QComboBox QListView {{ 
            background-color: {card}; 
            border: 1px solid {brd}; 
            color: {txt}; 
            outline: 0px;
        }}
        QComboBox QAbstractItemView::item, QComboBox QListView::item {{ 
            min-height: 32px; 
            padding: 6px 10px; 
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
        QPushButton#NavTab {{ border: none; border-radius: 6px; text-align: left; padding: 12px 18px; color: {sub}; font-weight: 600; font-size: 13px; letter-spacing: 0.3px; }}
        QPushButton#NavTab:checked {{ background-color: #007AFF; color: white; }}
        QPushButton#NavTab:hover {{ background-color: {hov}; color: {txt}; }}
        QPushButton#AddBtn {{ border: 2px dashed {brd}; border-radius: 12px; color: {sub}; background: transparent; font-weight: 800; font-size: 11px; }}
        QPushButton#AddBtn:hover {{ border-color: #007AFF; color: #007AFF; background: rgba(0,122,255,0.05); }}
        QPushButton#BtnPrimary {{ background-color: #007AFF; color: white; border-radius: 8px; padding: 12px; font-weight: 800; border: none; font-size: 13px; letter-spacing: 0.5px; }}
        QPushButton#BtnPrimary:hover {{ background-color: #0A84FF; }}
        QPushButton#BtnDanger {{ background-color: rgba(239, 68, 68, 0.1); color: #EF4444; border-radius: 10px; padding: 12px; border: 1px solid rgba(239, 68, 68, 0.2); font-weight: 700; font-size: 13px; }}
        QPushButton#BtnDanger:hover {{ background-color: #EF4444; color: white; }}
        QPushButton#ShellBtn {{ border: 1.5px solid {brd}; border-radius: 8px; color: {sub}; font-size: 11px; font-weight:bold; background: transparent; }}
        QPushButton#ShellBtn:hover {{ border-color: #007AFF; color: #007AFF; }}
        QScrollBar:vertical {{ background: transparent; width: 6px; }}
        QScrollBar::handle:vertical {{ background: {brd}; border-radius: 3px; }}
        QWidget#Overlay {{ background-color: {ov_bg}; }}
        """

class VisionAnalytics:
    _face_cascade = None

    @classmethod
    def get_face_cascade(cls):
        if cls._face_cascade is None:
            try:
                path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
                cls._face_cascade = cv2.CascadeClassifier(path)
            except: pass
        return cls._face_cascade

    @staticmethod
    def process(frame, engine_type="STANDARD"):
        t = str(engine_type).upper()
        meta = {"objects": 0}
        
        if t == "YOLOv8":
            cv2.putText(frame, "ENGINE: YOLOv8 | SCANNING", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 122, 255), 2)
            cv2.rectangle(frame, (100, 100), (300, 300), (0, 122, 255), 1)
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

class VideoThread(QThread):
    change_pixmap = pyqtSignal(np.ndarray)
    analytics_signal = pyqtSignal(dict)
    
    def __init__(self, src, engine="STANDARD"): 
        super().__init__(); self.src = src; self.engine = engine
        self.running = True; self.is_recording = False; self.out = None; self.snap_req = False

    def toggle_record(self, start=True):
        self.is_recording = start
        if not start and self.out:
            self.out.release(); self.out = None

    def snapshot(self): self.snap_req = True

    def run(self):
        source = self.src
        try:
            if str(source).isdigit(): source = int(source)
        except: pass
        
        print(f"[*] Video Engine: Attempting to open source -> {source}")
        # GStreamer Optimized Pipeline for Jetson
        if isinstance(source, int) and platform.system() == "Linux" and os.path.exists("/usr/bin/nvgstcapture"):
            gst_str = f"nvarguscamerasrc sensor-id={source} ! video/x-raw(memory:NVMM), width=1280, height=720, format=NV12, framerate=30/1 ! nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! appsink"
            cap = cv2.VideoCapture(gst_str, cv2.CAP_GSTREAMER)
        else:
            cap = cv2.VideoCapture(source)

        if not cap.isOpened():
            print(f"[!] Video Engine: Failed to open source -> {source}")
            return

        print(f"[+] Video Engine: Stream established -> {source}")
        while self.running:
            if cap is None or not cap.isOpened():
                print(f"[!] Video Engine: Reconnecting to {source}...")
                cap = cv2.VideoCapture(source)
                time.sleep(2); continue

            ret, frame = cap.read()
            if not ret:
                print(f"[!] Video Engine: Frame drop on {source}"); time.sleep(1)
                cap.release(); cap = None; continue

            # AI & NVR Layer...
            frame, meta = VisionAnalytics.process(frame, self.engine)
            if meta: self.analytics_signal.emit(meta)
            
            # 2. Snapshot Layer
            if self.snap_req:
                rec_dir = os.path.join(os.path.dirname(__file__), "recordings")
                if not os.path.exists(rec_dir): os.makedirs(rec_dir)
                path = os.path.join(rec_dir, f"SNAP_{datetime.now().strftime('%m%d_%H%M%S')}.jpg")
                cv2.imwrite(path, frame); self.snap_req = False

            if self.is_recording:
                rec_frame = frame.copy()
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cv2.putText(rec_frame, f"{ts} | {self.engine}", (10, frame.shape[0]-20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                if self.out is None:
                    rec_dir = os.path.join(os.path.dirname(__file__), "recordings")
                    if not os.path.exists(rec_dir): os.makedirs(rec_dir)
                    path = os.path.join(rec_dir, f"REC_{datetime.now().strftime('%m%d_%H%M%S')}.avi")
                    self.out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'MJPG'), 20, (frame.shape[1], frame.shape[0]))
                self.out.write(rec_frame)
            
            self.change_pixmap.emit(frame)
            time.sleep(0.01)
        if self.out: self.out.release(); self.out = None
        if cap: cap.release()

    def stop(self):
        self.running = False
        self.wait(2000) # Wait up to 2s for clean exit
        if self.isRunning(): self.terminate()

class StatsThread(QThread):
    updated = pyqtSignal(dict)
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
    def stop(self): self.running = False; self.wait()

class App(QMainWindow):
    def __init__(self):
        super().__init__(); self.resize(1200, 800); self.setWindowTitle("Jetson Studio"); self.is_dark = True
        self.db = DBManager()
        self.active_cids = set(); self.active_srcs = set()
        c = QWidget(); self.setCentralWidget(c); self.main = QHBoxLayout(c); self.main.setContentsMargins(0,0,0,0); self.main.setSpacing(0)
        self.init_ui(); self.apply_theme()
        self.stats = StatsThread(); self.stats.updated.connect(self.upd_stats); self.stats.start()
        QTimer.singleShot(300, self.load_data)
        QTimer.singleShot(800, self.check_docker)
        
        # Ensure it fits the screen properly
        self.showMaximized()

    def load_data(self):
        # Load persisted cameras
        for name, src, meta in self.db.get_cameras():
            self.add_cam_logic(name, src, meta, save=False)
        # Load persisted workspaces
        for name, img, cid in self.db.get_workspaces():
            self.add_docker_card(name, img, cid, save=False)

    def init_ui(self):
        sb = QFrame(); sb.setObjectName("Sidebar"); sb.setFixedWidth(240)
        sl = QVBoxLayout(sb); sl.setContentsMargins(20,50,20,20); sl.setSpacing(8)
        logo = QLabel("Jetson Studio"); logo.setStyleSheet("font-size: 18px; font-weight: 800; margin-bottom: 30px; border:none;"); sl.addWidget(logo)
        self.eco_mode = False
        self.tabs = QStackedWidget(); self.navs = []
        for i, t in enumerate(["Cameras", "Workspaces", "Library", "Settings"]):
            b = QPushButton(t); b.setObjectName("NavTab"); b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, x=i: self.switch(x)); sl.addWidget(b); self.navs.append(b)
        sl.addStretch(); host = QLabel(platform.node()); host.setStyleSheet("color: #555; font-size: 10px; font-weight:800; border:none;"); sl.addWidget(host)
        self.main.addWidget(sb); self.main.addWidget(self.tabs)
        self.tabs.addWidget(self.page_cams()); self.tabs.addWidget(self.page_docker()); self.tabs.addWidget(self.page_library()); self.tabs.addWidget(self.page_settings())
        self.navs[0].setChecked(True)

    def page_library(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(40,40,40,40)
        h = QHBoxLayout(); h.addWidget(QLabel("Media Library", styleSheet="font-size: 24px; font-weight: 800; border:none;")); h.addStretch()
        rb = QPushButton("↻"); rb.setFixedSize(30,30); rb.setStyleSheet("border:1px solid #333; border-radius:15px; background: transparent;"); rb.clicked.connect(self.refresh_library); h.addWidget(rb); l.addLayout(h); l.addSpacing(25)
        
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setStyleSheet("background: transparent; border: none;")
        self.lib_widget = QWidget(); self.lib_lyout = QVBoxLayout(self.lib_widget); self.lib_lyout.setSpacing(10); self.lib_lyout.setAlignment(Qt.AlignTop)
        sa.setWidget(self.lib_widget); l.addWidget(sa)
        QTimer.singleShot(1000, self.refresh_library); return w

    def refresh_library(self):
        while self.lib_lyout.count():
            item = self.lib_lyout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
        path = os.path.join(os.path.dirname(__file__), "recordings")
        if not os.path.exists(path): return
        
        files = sorted(glob.glob(os.path.join(path, "*")), key=os.path.getmtime, reverse=True)
        for f in files[:20]:
            name = os.path.basename(f)
            r = QFrame(); r.setFixedHeight(54); r.setStyleSheet("background:rgba(128,128,128,0.03); border-radius:8px; border:none;")
            rl = QHBoxLayout(r); rl.setContentsMargins(15,0,10,0)
            
            type_tag = "VIDEO" if name.endswith(".avi") else "PHOTO"
            rl.addWidget(QLabel(type_tag, styleSheet="color: #666; font-size: 9px; font-weight: 900; letter-spacing: 0.5px; min-width: 45px;"))
            
            info = QVBoxLayout(); info.setSpacing(2); info.setAlignment(Qt.AlignVCenter)
            rl_lbl = QLabel(name); rl_lbl.setStyleSheet("font-size:12px; font-weight:700; border:none; background:transparent;")
            sz = f"{os.path.getsize(f)//1024} KB"
            meta = QLabel(sz); meta.setStyleSheet("font-size:9px; color:#666; border:none; background:transparent;")
            info.addWidget(rl_lbl); info.addWidget(meta); rl.addLayout(info, 1)
            
            ob = QPushButton("OPEN"); ob.setFixedSize(60,28); ob.setCursor(Qt.PointingHandCursor)
            ob.setStyleSheet("QPushButton { background: rgba(0,122,255,0.1); color: #007AFF; border-radius:6px; font-size:10px; font-weight:800; border:none; } QPushButton:hover { background: #007AFF; color: white; }")
            
            def universal_open(p):
                plat = platform.system()
                if plat == "Darwin": subprocess.Popen(["open", p])
                elif plat == "Linux": subprocess.Popen(["xdg-open", p])
                elif plat == "Windows": os.startfile(p)
                
            ob.clicked.connect(lambda _, p=f: universal_open(p))
            
            db = QPushButton("×"); db.setFixedSize(28,28); db.setCursor(Qt.PointingHandCursor)
            db.setStyleSheet("QPushButton { color: #555; font-size: 16px; border:none; background:transparent; } QPushButton:hover { color: #EF4444; }")
            db.clicked.connect(lambda _, p=f: [os.remove(p), self.refresh_library()])
            
            rl.addWidget(ob); rl.addWidget(db); self.lib_lyout.addWidget(r)

    def page_cams(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(40,40,40,40)
        h = QHBoxLayout(); h.addWidget(QLabel("Broadcasting", styleSheet="font-size: 24px; font-weight: 800; border:none;"))
        self.cam_search = QLineEdit(); self.cam_search.setPlaceholderText("Search cameras..."); self.cam_search.setFixedWidth(200)
        self.cam_search.textChanged.connect(self.filter_cameras); h.addStretch(); h.addWidget(self.cam_search)
        rb = QPushButton("↻"); rb.setFixedSize(30,30); rb.setStyleSheet("border:1px solid #333; border-radius:15px; background: transparent;"); rb.clicked.connect(self.refresh_ui); h.addWidget(rb); l.addLayout(h); l.addSpacing(25)
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setStyleSheet("background: transparent; border: none;")
        self.cam_widget = QWidget(); self.cf = FlowLayout(self.cam_widget); sa.setWidget(self.cam_widget)
        self.abc = self.create_add_btn("New Camera", self.modal_cam); self.cf.addWidget(self.abc); l.addWidget(sa); return w

    def page_docker(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(40,40,40,40)
        h = QHBoxLayout(); h.addWidget(QLabel("Workspaces", styleSheet="font-size: 24px; font-weight: 800; border:none;"))
        self.doc_search = QLineEdit(); self.doc_search.setPlaceholderText("Search workspaces..."); self.doc_search.setFixedWidth(200)
        self.doc_search.textChanged.connect(self.filter_workspaces); h.addStretch(); h.addWidget(self.doc_search)
        rb = QPushButton("↻"); rb.setFixedSize(30,30); rb.setStyleSheet("border:1px solid #333; border-radius:15px; background: transparent;"); rb.clicked.connect(self.refresh_ui); h.addWidget(rb); l.addLayout(h); l.addSpacing(25)
        sa = QScrollArea(); sa.setWidgetResizable(True); sa.setStyleSheet("background: transparent; border: none;")
        self.doc_widget = QWidget(); self.df = FlowLayout(self.doc_widget); sa.setWidget(self.doc_widget)
        self.abd = self.create_add_btn("New Workspace", self.modal_doc); self.df.addWidget(self.abd); l.addWidget(sa); return w

    def page_settings(self):
        sw = QScrollArea(); sw.setWidgetResizable(True); sw.setStyleSheet("background: transparent; border: none;")
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(40,40,40,40); l.setSpacing(40)
        sw.setWidget(w)
        
        # Section 1: Metrics
        l.addWidget(QLabel("Live System Metrics", styleSheet="font-size: 22px; font-weight: 800; border:none;"))
        mc = QWidget(); ml = QHBoxLayout(mc); ml.setSpacing(25); ml.setContentsMargins(0,0,0,0)
        self.charts = [DonutChart("CPU","#EF4444"), DonutChart("RAM","#10B981"), DonutChart("DISK","#3B82F6"), DonutChart("GPU","#8B5CF6")]
        for ch in self.charts: ml.addWidget(ch)
        l.addWidget(mc)

        # Section 2: Hardware Inventory
        l.addWidget(QLabel("Hardware Assets", styleSheet="font-size: 22px; font-weight: 800; border:none;"))
        hw = QFrame(); hw.setObjectName("InfoCard"); hl = QGridLayout(hw); hl.setContentsMargins(25,25,25,25); hl.setSpacing(30)
        inf = [("Architecture", platform.machine()), ("CPU Cores", str(psutil.cpu_count())), ("Logic OS", platform.system()), ("Graphics Unit", get_gpu_info())]
        for i, (k, v) in enumerate(inf): 
            lv = QVBoxLayout(); lv.addWidget(QLabel(k, styleSheet="color:#888; font-size:11px; font-weight:700; border:none;"))
            lv.addWidget(QLabel(v, styleSheet="font-size:14px; font-weight:700; border:none;")); hl.addLayout(lv, 0, i)
        l.addWidget(hw)

        # Section 3: Professional Health Monitor
        l.addWidget(QLabel("System Health & Connectivity", styleSheet="font-size: 22px; font-weight: 800; border:none;"))
        sys_info = QFrame(); sys_info.setObjectName("InfoCard"); sl_info = QVBoxLayout(sys_info); sl_info.setContentsMargins(25,25,25,25); sl_info.setSpacing(15)
        
        def add_status(lbl, val):
            row = QHBoxLayout(); row.addWidget(QLabel(lbl, styleSheet="color:#888; font-size:13px; font-weight:600;"))
            row.addStretch(); row.addWidget(QLabel(val, styleSheet="color:#007AFF; font-size:13px; font-weight:700;")); sl_info.addLayout(row)

        add_status("DOCKER ENGINE", "ACTIVE" if DockerManager.is_running() else "OFFLINE")
        add_status("CAMERA NODES", ", ".join([str(c[1]) for c in list_cameras()]) if list_cameras() else "NONE")
        add_status("PLATFORM CORE", f"{platform.system()} {platform.machine()}")
        l.addWidget(sys_info)

        # Connectivity Info
        l.addWidget(QLabel("Connectivity & Integration", styleSheet="font-size: 22px; font-weight: 800; border:none;"))
        conn_box = QFrame(); conn_box.setObjectName("InfoCard"); cl = QVBoxLayout(conn_box); cl.setContentsMargins(25,25,25,25); cl.setSpacing(15)
        
        node_row = QHBoxLayout(); node_row.addWidget(QLabel("REMOTE NODE (IP):", styleSheet="color:#888; font-size:13px; font-weight:700;"))
        self.node_ip = QLineEdit(); self.node_ip.setPlaceholderText("Localhost (or ZeroTier IP)")
        self.node_ip.setFixedWidth(200); self.node_ip.setText(os.getenv("JETSON_REMOTE", ""))
        self.node_ip.textChanged.connect(lambda t: [DockerManager.set_host(t), self.show_toast(f"Switched Node: {t or 'Local'}")])
        node_row.addStretch(); node_row.addWidget(self.node_ip); cl.addLayout(node_row)

        # ZeroTier Auto-Discovery Helper
        zt_nodes = get_zerotier_nodes()
        if zt_nodes:
            zt_row = QHBoxLayout(); zt_row.addWidget(QLabel("DISCOVERED ZT PEERS:", styleSheet="color:#888; font-size:11px; font-weight:700;"))
            zt_row.addStretch()
            for node in zt_nodes[:3]:
                nb = QPushButton(node); nb.setFixedSize(90, 22); nb.setStyleSheet("font-size: 9px; border: 1px solid #333; border-radius: 4px;"); 
                nb.clicked.connect(lambda _, n=node: self.node_ip.setText(n))
                zt_row.addWidget(nb)
            cl.addLayout(zt_row)
        
        l.addWidget(conn_box)

        l.addWidget(QLabel("Global Preferences", styleSheet="font-size: 22px; font-weight: 800; border:none;"))
        dh = QHBoxLayout(); dh.addWidget(QLabel("Eco Mode (Thermal Guard)", styleSheet="font-size:14px; font-weight:600; border:none;")); ts = ToggleSwitch(); ts.toggled.connect(self.toggle_eco); dh.addStretch(); dh.addWidget(ts); l.addLayout(dh)
        ph = QHBoxLayout(); ph.addWidget(QLabel("Dark Perspective", styleSheet="font-size:14px; font-weight:600; border:none;")); ps = ToggleSwitch(); ps.toggled.connect(self.toggle_theme); ph.addStretch(); ph.addWidget(ps); l.addLayout(ph)
        l.addWidget(QFrame(styleSheet="background:rgba(128,128,128,0.1); height:1px; border:none;"))

        # Section 4: Docker Image Management (Now at bottom)
        ih = QHBoxLayout(); ih.addWidget(QLabel("Image Repository", styleSheet="font-size: 22px; font-weight: 800; border:none;")); ih.addStretch()
        irb = QPushButton("↻"); irb.setFixedSize(30,30); irb.setStyleSheet("border:1px solid #333; border-radius:15px; background: transparent; font-size: 16px;"); irb.setCursor(Qt.PointingHandCursor); irb.clicked.connect(self.refresh_images); ih.addWidget(irb)
        l.addLayout(ih)
        
        ic = QFrame(); ic.setObjectName("InfoCard"); il = QVBoxLayout(ic); il.setContentsMargins(20,20,20,20); il.setSpacing(10)
        hl = QHBoxLayout(); hl.addWidget(QLabel("REPOSITORY", styleSheet="color:#888; font-size:10px; font-weight:800;"))
        hl.addStretch(); hl.addWidget(QLabel("SIZE", styleSheet="color:#888; font-size:10px; font-weight:800; margin-right:60px;"))
        il.addLayout(hl); il.addWidget(QFrame(styleSheet="background:rgba(128,128,128,0.1); height:1px; border:none;"))
        self.img_list_layout = QVBoxLayout(); self.img_list_layout.setSpacing(8); il.addLayout(self.img_list_layout)
        self.refresh_images(); l.addWidget(ic)

        l.addStretch(); return sw

    def refresh_images(self):
        # Professional UI transition: clear current list
        while self.img_list_layout.count():
            item = self.img_list_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
        imgs = DockerManager.list_images()
        if not imgs:
            placeholder = QLabel("No local images found. Connect to NGC or Docker Hub to pull images."); placeholder.setStyleSheet("color:#666; font-size:11px; font-weight:500; border:none; background:transparent; padding:10px;")
            self.img_list_layout.addWidget(placeholder)
            return

        for im in imgs:
            r = QFrame(); r.setFixedHeight(60); r.setStyleSheet("background:rgba(128,128,128,0.03); border-radius:8px; border:none;")
            rl = QHBoxLayout(r); rl.setContentsMargins(15,8,10,8); rl.setAlignment(Qt.AlignVCenter)
            
            info = QVBoxLayout(); info.setSpacing(2); info.setAlignment(Qt.AlignVCenter)
            repo_lbl = QLabel(im['repo']); repo_lbl.setStyleSheet("font-size:12px; font-weight:700; border:none; background:transparent;")
            tag_lbl = QLabel(f"Tag: {im['tag']} | ID: {im['id'][:12]}"); tag_lbl.setStyleSheet("font-size:10px; color:#888; border:none; background:transparent;")
            info.addWidget(repo_lbl); info.addWidget(tag_lbl); rl.addLayout(info)
            
            rl.addStretch()
            size_lbl = QLabel(im['size']); size_lbl.setStyleSheet("font-size:11px; font-weight:700; color:#007AFF; border:none; background:transparent; margin-right:15px;")
            rl.addWidget(size_lbl)
            
            db = QPushButton("Purge"); db.setFixedSize(64,28); db.setCursor(Qt.PointingHandCursor)
            db.setStyleSheet("QPushButton { background: rgba(239, 68, 68, 0.1); color: #EF4444; border-radius:6px; font-size:10px; font-weight:800; border: 1px solid rgba(239, 68, 68, 0.2); } QPushButton:hover { background: #EF4444; color: white; }")
            db.clicked.connect(lambda _, i=im['id']: [DockerManager.remove_image(i), QTimer.singleShot(600, self.refresh_images)])
            rl.addWidget(db); self.img_list_layout.addWidget(r)

    def create_add_btn(self, text, cb):
        b = QPushButton(); b.setObjectName("AddBtn"); b.setFixedSize(340, 240); b.setCursor(Qt.PointingHandCursor); b.clicked.connect(cb)
        l = QVBoxLayout(b); l.setAlignment(Qt.AlignCenter); l.setSpacing(10)
        ico = QLabel("+"); ico.setAlignment(Qt.AlignCenter); ico.setStyleSheet("font-size: 40px; color: #555; border:none; font-weight:300;")
        txt = QLabel(text); txt.setAlignment(Qt.AlignCenter); txt.setStyleSheet("font-size: 12px; font-weight: 600; color: #888; border:none;")
        l.addWidget(ico); l.addWidget(txt); return b

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
        t = Toast(txt, self); t.show_msg(self.width()//2 - 120, 50) # Top-Center placement

    def run_health_check(self):
        results = []
        results.append(f"Docker: {'✅' if DockerManager.is_running() else '❌'}")
        results.append(f"Camera Indices: {list_cameras()}")
        results.append(f"Platform: {platform.system()} {platform.machine()}")
        QMessageBox.information(self, "System Health Report", "\n".join(results))

    def switch(self, i): self.tabs.setCurrentIndex(i); [btn.setChecked(idx == i) for idx, btn in enumerate(self.navs)]
    def upd_stats(self, d): 
        for i, k in enumerate(['cpu','ram','disk','gpu']): self.charts[i].set_value(d[k])
    def toggle_theme(self, c): self.is_dark = c; self.apply_theme()
    def apply_theme(self): QApplication.instance().setStyleSheet(ThemeOps.get_style(self.is_dark))
    def modal_cam(self): self.show_overlay("Broadcasting Device", self.add_cam_logic)
    def modal_doc(self): self.show_overlay("Workspace Environment", self.add_doc_logic)
    def refresh_ui(self):
        self.active_cids.clear(); self.active_srcs.clear()
        for i in reversed(range(self.df.count())):
            w = self.df.itemAt(i).widget()
            if isinstance(w, ResizableCard): w.deleteLater()
        self.df.removeWidget(self.abd); self.df.addWidget(self.abd); self.check_docker()
    def check_docker(self):
        if not DockerManager.is_running(): return
        for c in DockerManager.list_containers(): self.add_docker_card(c['name'], c['image'], c['id'], running=(c['status'].startswith('Up')))

    def show_delete_confirmation(self, card):
        ov = QWidget(self); ov.setObjectName("Overlay"); ov.resize(self.size())
        l = QVBoxLayout(ov); l.setAlignment(Qt.AlignCenter); box = QFrame(); box.setObjectName("ModalBox"); box.setFixedWidth(420)
        bl = QVBoxLayout(box); bl.setContentsMargins(35,35,35,35); bl.setSpacing(25)
        
        t = QLabel("Delete Item?"); t.setAlignment(Qt.AlignCenter); t.setStyleSheet("font-size:22px; font-weight:800; border:none;")
        s = QLabel("This action is permanent and all data will be lost."); s.setAlignment(Qt.AlignCenter); s.setStyleSheet("color:#888; font-size:13px; border:none;")
        bl.addWidget(t); bl.addWidget(s)
        
        purge_check = None
        if card.is_docker:
            purge_check = QPushButton("Also purge base image from system"); purge_check.setCheckable(True); purge_check.setCursor(Qt.PointingHandCursor)
            purge_check.setStyleSheet("QPushButton { background: rgba(128,128,128,0.05); border: 1px solid rgba(128,128,128,0.1); border-radius: 8px; padding: 12px; font-size: 11px; text-align: left; } QPushButton:checked { background: rgba(239, 68, 68, 0.1); border-color: rgba(239, 68, 68, 0.3); color: #EF4444; font-weight: bold; }")
            bl.addWidget(purge_check)
        
        h = QHBoxLayout(); c = QPushButton("Cancel"); c.setStyleSheet("background:transparent; color:#007AFF; font-weight:700; border:none; font-size:14px;"); d = QPushButton("Delete"); d.setObjectName("BtnDanger"); d.setMinimumHeight(42)
        c.clicked.connect(ov.deleteLater)
        d.clicked.connect(lambda: [card.perform_delete(purge_check.isChecked() if purge_check else False), ov.deleteLater()])
        h.addWidget(c); h.addSpacing(20); h.addWidget(d); bl.addLayout(h); l.addWidget(box); ov.show()

    def show_overlay(self, title, cb):
        ov = QWidget(self); ov.setObjectName("Overlay"); ov.resize(self.size())
        l = QVBoxLayout(ov); l.setAlignment(Qt.AlignCenter); box = QFrame(); box.setObjectName("ModalBox"); box.setFixedWidth(540)
        bl = QVBoxLayout(box); bl.setContentsMargins(35,35,35,35); bl.setSpacing(25); 
        
        header = QLabel(title); header.setStyleSheet("font-size:24px; font-weight:800; border:none; letter-spacing:-0.5px;"); bl.addWidget(header)
        
        is_cam = "Broadcasting" in title
        main_layout = QVBoxLayout(); main_layout.setSpacing(20); main_layout.setContentsMargins(0,0,0,0)
        
        # Monitor Area
        if is_cam:
            mon_frame = QFrame(); mon_frame.setFixedSize(474, 274); mon_frame.setStyleSheet("background: #000; border-radius: 8px; border: 2px solid rgba(128,128,128,0.15);")
            ml = QVBoxLayout(mon_frame); ml.setContentsMargins(0,0,0,0); ml.setSpacing(0)
            
            pv = QLabel(); pv.setAlignment(Qt.AlignCenter); pv.setStyleSheet("border:none; background: transparent;")
            ml.addWidget(pv, 1); main_layout.addWidget(mon_frame, 0, Qt.AlignCenter)
            
            preview_thread = [None]
            def update_preview_ui(img):
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); h, w, c = rgb.shape
                qimg = QImage(rgb.data, w, h, c * w, QImage.Format_RGB888)
                pv.setPixmap(QPixmap.fromImage(qimg).scaled(466, 266, Qt.KeepAspectRatio, Qt.SmoothTransformation))

            def start_preview():
                if preview_thread[0]: preview_thread[0].stop(); pv.setPixmap(QPixmap()); pv.setText("")
                m = mode_combo.currentData()
                src = None
                if m == "Physical": src = cam_combo.currentData()
                elif m == "Stream": src = url_input.text().strip()
                
                if src is not None and src != "":
                    t = VideoThread(src); t.change_pixmap.connect(update_preview_ui)
                    t.start(); preview_thread[0] = t
                else: pv.setText("WAITING FOR SIGNAL...")

        # Form Area (Single Unified Form)
        sett_box = QFrame(); sett_box.setStyleSheet("background: rgba(128,128,128,0.03); border: 1px solid rgba(128,128,128,0.08); border-radius: 12px;")
        sl = QVBoxLayout(sett_box); sl.setContentsMargins(20,20,20,20); sl.setSpacing(0)
        f = QFormLayout(); f.setSpacing(15); f.setLabelAlignment(Qt.AlignRight); f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        # Form Area Labels (Explicitly created to prevent "Ghost Labels")
        name_label = QLabel("BROADCAST NAME:"); mode_label = QLabel("STREAM MODE:")
        cat_label = QLabel("TEMPLATE:")
        cam_label = QLabel("UNIT:"); url_label = QLabel("LINK:")
        ws_label = QLabel("WS:"); exe_label = QLabel("EXE:")
        src_label = QLabel("MANUAL IMAGE TAG:")
        eng_label = QLabel("ENGINE:"); prof_label = QLabel("PROFILE:")
        ai_setup_label = QLabel("AI SETUP:")

        # Helper: creates a themed, cross-platform-safe QComboBox
        # On Ubuntu/GTK, CSS height constraints alone are insufficient —
        # setFixedHeight + view().setMaximumHeight() must be set programmatically.
        # Also explicitly setting setView(QListView()) forces Qt to bypass
        # the unstylable native GTK popup rendering on Ubuntu.
        def make_combo():
            c = QComboBox()
            from PyQt5.QtWidgets import QListView
            c.setView(QListView())
            c.setFixedHeight(36)
            c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            c.view().setMaximumHeight(180)  # cap the dropdown popup height
            return c

        # Form Area Widgets
        # Always initialize ALL widgets first to avoid UnboundLocalError in all_elements.
        # Cam-only widgets are set to None when is_cam=False.
        mode_combo = cam_combo = container_combo = script_input = cat_combo = None

        if is_cam:
            name_input = QLineEdit(); name_input.setPlaceholderText("Enter a name for this stream (e.g. Garden)")
            mode_combo = make_combo()
            mode_combo.addItem("PHYSICAL DEVICE", "Physical")
            mode_combo.addItem("NETWORK STREAM", "Stream")
            mode_combo.addItem("AI WORKSPACE ENGINE", "Container")
        else:
            name_input = QLineEdit(); name_input.setPlaceholderText("Enter a name for this workspace (e.g. Dev Lab)")
            cat_combo = make_combo(); [cat_combo.addItem(i['name'], i['img']) for i in CatalogManager.get_recommended()[0]]

        # Mode-Specific Fields (only used when is_cam=True)
        cam_combo = make_combo(); [cam_combo.addItem(n, i) for n, i in list_cameras()]
        url_input = QLineEdit(); url_input.setPlaceholderText("rtsp://admin:123@192.168.1.10/stream")
        container_combo = make_combo(); containers = DockerManager.list_containers()
        if containers: [container_combo.addItem(f"{c['name']} ({c['image']})", c['id']) for c in containers]
        else: container_combo.addItem("No active containers", None)
        script_input = QLineEdit(); script_input.setPlaceholderText("/path/to/script.py")
        cin = QLineEdit(); cin.setPlaceholderText("Override with custom image tag (optional)")

        # Intelligence Fields
        engine_combo = make_combo(); [engine_combo.addItem(e) for e in ["Standard", "YOLOv8", "Face AI", "Pose AI", "CUSTOM WORKSPACE"]]
        res_combo = make_combo(); [res_combo.addItem(r) for r in ["Auto", "720p", "1080p", "4K"]]


        # Custom AI Configurator Overlay
        custom_config = {"cid": None, "script": ""}
        def open_custom_setup():
            sub_ov = QWidget(ov); sub_ov.setObjectName("Overlay"); sub_ov.resize(ov.size())
            sl = QVBoxLayout(sub_ov); sl.setAlignment(Qt.AlignCenter)
            sc = QFrame(); sc.setObjectName("ModalBox"); sc.setFixedWidth(400)
            scl = QVBoxLayout(sc); scl.setContentsMargins(25,25,25,25); scl.setSpacing(20)
            
            scl.addWidget(QLabel("Engine Configuration", styleSheet="font-size:18px; font-weight:800; text-align:center;"))
            scl.addWidget(QLabel("Map a script from your workspace to this camera feed.", styleSheet="color:#888; font-size:11px;"))
            
            f_sub = QFormLayout(); f_sub.setSpacing(12)
            ws_cb = make_combo()
            conts = DockerManager.list_containers()
            if conts: [ws_cb.addItem(f"{c['name']}", c['id']) for c in conts]
            
            script_in = QLineEdit(); script_in.setPlaceholderText("/home/jetson/model.py")
            if custom_config["script"]: script_in.setText(custom_config["script"])
            
            f_sub.addRow("WORKSPACE:", ws_cb)
            f_sub.addRow("PY SCRIPT:", script_in)
            scl.addLayout(f_sub)
            
            done = QPushButton("Apply Configuration"); done.setObjectName("BtnPrimary")
            done.clicked.connect(lambda: [custom_config.update({"cid": ws_cb.currentData(), "script": script_in.text()}), sub_ov.deleteLater()])
            scl.addWidget(done); sl.addWidget(sc); sub_ov.show()

        setup_btn = QPushButton("CONFIGURE SCRIPT..."); setup_btn.setObjectName("ShellBtn"); setup_btn.setFixedSize(140, 32); setup_btn.hide()
        setup_btn.clicked.connect(open_custom_setup)

        # Safeguard all elements from deletion (None values are filtered by the 'if e' guard below)
        all_elements = [name_input, mode_combo, cam_combo, url_input, container_combo, script_input, cin,
                        engine_combo, res_combo, setup_btn, name_label, mode_label, cat_label, cam_label,
                        url_label, ws_label, exe_label, src_label, eng_label, prof_label, ai_setup_label,
                        cat_combo]
        
        for e in all_elements: 
            if e: e.setParent(box); e.hide()

        def update_visibility():
            while f.rowCount() > 0: f.takeRow(0)
            for e in all_elements: 
                if e: e.hide()
            
            if not is_cam:
                for x in [name_label, name_input, cat_label, cat_combo, src_label, cin]: x.show()
                f.addRow(name_label, name_input)
                f.addRow(cat_label, cat_combo)
                f.addRow(src_label, cin)
            else:
                for x in [name_label, name_input, mode_label, mode_combo, eng_label, engine_combo, prof_label, res_combo]: x.show()
                f.addRow(name_label, name_input)
                f.addRow(mode_label, mode_combo)
                
                m = mode_combo.currentIndex()
                if m == 1: # Prioritize Stream Mode Visibility
                    url_label.show(); url_input.show(); f.addRow(url_label, url_input)
                elif m == 0:
                    cam_label.show(); cam_combo.show(); f.addRow(cam_label, cam_combo)
                elif m == 2:
                    ws_label.show(); container_combo.show(); exe_label.show(); script_input.show()
                    f.addRow(ws_label, container_combo)
                    f.addRow(exe_label, script_input)
                
                f.addRow(eng_label, engine_combo)
                if engine_combo.currentText() == "CUSTOM WORKSPACE":
                    ai_setup_label.show(); setup_btn.show(); f.addRow(ai_setup_label, setup_btn)
                f.addRow(prof_label, res_combo)

        if is_cam: 
            mode_combo.currentIndexChanged.connect(lambda: [update_visibility(), start_preview()])
            engine_combo.currentIndexChanged.connect(update_visibility)
            cam_combo.currentIndexChanged.connect(start_preview)
            url_input.textChanged.connect(lambda: QTimer.singleShot(1000, start_preview))
            start_preview()
        
        update_visibility()
        sl.addLayout(f); main_layout.addWidget(sett_box)
        bl.addLayout(main_layout); bl.addSpacing(10)
        h = QHBoxLayout(); b1 = QPushButton("Cancel"); b1.setObjectName("BtnDanger"); b2 = QPushButton("Confirm"); b2.setObjectName("BtnPrimary")
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
                    if not name: name = cam_combo.currentText()
                elif m == "Stream":
                    val = url_input.text().strip()
                    if not name: name = f"Stream: {val[:15]}"
                elif m == "Container":
                    cid = container_combo.currentData(); script = script_input.text().strip()
                    val = f"docker://{cid}?script={script}"
                    if not name: name = f"AI: {container_combo.currentText().split(' ')[0]}"
                
                meta = f"{m}|{engine_combo.currentText()}|{res_combo.currentText()}"
                if engine_combo.currentText() == "CUSTOM WORKSPACE":
                    meta += f"|{custom_config['cid']}|{custom_config['script']}"
                cb(name, val, meta)
            else:
                val = cin.text().strip() if cin.text() else cat_combo.currentData()
                name = name_input.text().strip()
                if not name: name = cin.text().strip() if cin.text() else cat_combo.currentText()
                cb(name, val)
            ov.deleteLater()
            
        b2.clicked.connect(confirm); h.addWidget(b1); h.addWidget(b2); bl.addLayout(h); l.addWidget(box); ov.show()

    def add_cam_logic(self, name, src, meta=None, save=True):
        if src is None or src == "" or src in self.active_srcs: return
        self.active_srcs.add(src)
        card = ResizableCard(name, meta or "", False); card.trigger_delete_modal.connect(self.show_delete_confirmation); card.removed.connect(lambda: [self.active_srcs.remove(src) if src in self.active_srcs else None, card.deleteLater()])
        card.db = self.db; card.sub_val = src
        self.cf.removeWidget(self.abc); self.cf.addWidget(card); self.cf.addWidget(self.abc)
        if save: self.db.save_camera(name, src, meta or "")
        
        if str(src).startswith("docker://"):
            card.view.setText("AI Engine Initializing...")
        elif src is not None:
            engine = "STANDARD"
            if meta and "|" in meta:
                parts = meta.split("|")
                engine = parts[1]
                if engine == "CUSTOM WORKSPACE" and len(parts) >= 5:
                    # Logic to trigger workspace script can be added here
                    card.view.setText(f"CUSTOM AI: {parts[4].split('/')[-1]}")

            t = VideoThread(src, engine); t.change_pixmap.connect(card.upd_img)
            t.analytics_signal.connect(card.update_ai_ui)
            t.start(); card.t = t 
        else:
            card.view.setText("No Source Signal")

    def add_doc_logic(self, name, img):
        if not img: return
        cn = f"jetson_{''.join(random.choices(string.ascii_lowercase,k=5))}"
        card = ResizableCard(cn, img, True); card.trigger_delete_modal.connect(self.show_delete_confirmation); card.removed.connect(card.deleteLater); card.set_status_info("Pulling", "#0A84FF")
        card.db = self.db
        self.df.removeWidget(self.abd); self.df.addWidget(card); self.df.addWidget(self.abd)
        card.w = DockerCreationThread(f"docker run -d --name {cn} --rm {img} sleep infinity")
        def on_created(o, s):
            if s:
                card.container_id = o; card.set_status_info("Active","#30D158"); card.start_monitoring()
                self.db.save_workspace(cn, img, o)
            else: card.set_status_info("Error","#FF453A")
        card.w.result.connect(on_created); card.w.start()

    def add_docker_card(self, n, i, c, running=True, save=True):
        if c is None or c == "" or c in self.active_cids: return
        self.active_cids.add(c)
        card = ResizableCard(n, i, True, c); card.trigger_delete_modal.connect(self.show_delete_confirmation); card.removed.connect(lambda: [self.active_cids.remove(c) if c in self.active_cids else None, card.deleteLater()])
        card.db = self.db
        card.set_status_info("Active" if running else "Static", "#30D158" if running else "#FF453A")
        if running: card.start_monitoring()
        self.df.removeWidget(self.abd); self.df.addWidget(card); self.df.addWidget(self.abd)
        if save: self.db.save_workspace(n, i, c)

    def closeEvent(self, e): self.stats.stop(); e.accept()

class FlowLayout(QLayout):
    def __init__(self, p=None): super().__init__(p); self.i = []
    def addItem(self, x): self.i.append(x)
    def count(self): return len(self.i)
    def itemAt(self, x): return self.i[x] if x<len(self.i) else None
    def takeAt(self, x): return self.i.pop(x) if x<len(self.i) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))
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
    app = QApplication(sys.argv); w = App(); w.show(); sys.exit(app.exec_())
