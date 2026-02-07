#!/usr/bin/env python3
"""
Jetson AI Studio - Ultimate Edition
"""
import sys
import os
import psutil
import subprocess
import cv2
import time
import platform
import numpy as np

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QStackedWidget, QFrame,
    QScrollArea, QGridLayout, QMessageBox, QComboBox,
    QSizePolicy, QLayout, QFileDialog, QGraphicsDropShadowEffect, QSizeGrip
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize, QPoint, QRect, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QColor, QFont, QIcon, QImage, QPixmap

# =============================================================================
# THEMES & STYLES (Light/Dark Mode Engine)
# =============================================================================

class Theme:
    DARK = {
        "bg": "#121212", "sidebar": "#181818", "card": "#1e1e1e",
        "text": "#E0E0E0", "text_sec": "#A0A0A0", "accent": "#76b900",
        "border": "#333", "danger": "#cf6679", "input": "#2c2c2c"
    }
    LIGHT = {
        "bg": "#f5f5f7", "sidebar": "#ffffff", "card": "#ffffff",
        "text": "#1d1d1f", "text_sec": "#86868b", "accent": "#0071e3",
        "border": "#e5e5e5", "danger": "#ff3b30", "input": "#f0f0f0"
    }

def get_stylesheet(theme):
    return f"""
    QMainWindow {{ background-color: {theme['bg']}; color: {theme['text']}; }}
    QWidget {{ font-family: 'Segoe UI', 'Roboto', sans-serif; }}
    
    /* Sidebar */
    QFrame#Sidebar {{ background-color: {theme['sidebar']}; border-right: 1px solid {theme['border']}; }}
    QPushButton#NavButton {{
        text-align: left; padding: 12px 20px; border: none; border-radius: 8px;
        color: {theme['text_sec']}; font-weight: 500; font-size: 14px; background: transparent;
    }}
    QPushButton#NavButton:hover {{ background-color: {theme['input']}; color: {theme['text']}; }}
    QPushButton#NavButton:checked {{ background-color: {theme['input']}; color: {theme['accent']}; font-weight: bold; }}

    /* Cards - Clean, No Borders on labels */
    QFrame#Card, QFrame#AddCard {{
        background-color: {theme['card']};
        border: 1px solid {theme['border']};
        border-radius: 12px;
    }}
    QFrame#AddCard {{ border-style: dashed; }}
    QFrame#AddCard:hover {{ border-color: {theme['accent']}; }}
    
    QLabel {{ border: none; background: transparent; color: {theme['text']}; }}
    QLabel#SecondaryText {{ color: {theme['text_sec']}; font-size: 13px; }}
    
    /* Buttons */
    QPushButton {{
        background-color: {theme['input']}; color: {theme['text']};
        border: none; padding: 8px 16px; border-radius: 6px; font-weight: 600;
    }}
    QPushButton:hover {{ background-color: {theme['border']}; }}
    QPushButton#PrimaryButton {{ background-color: {theme['accent']}; color: #fff; }}
    QPushButton#PrimaryButton:hover {{ opacity: 0.9; }}
    QPushButton#DangerButton {{ background-color: {theme['danger']}; color: #fff; }}

    /* Inputs & Combos */
    QComboBox, QLineEdit {{
        background-color: {theme['input']}; border: 1px solid {theme['border']};
        border-radius: 6px; padding: 8px; color: {theme['text']};
    }}
    QScrollArea {{ border: none; background: transparent; }}
    """

# =============================================================================
# WORKER THREADS
# =============================================================================

class VideoThread(QThread):
    change_pixmap = pyqtSignal(np.ndarray)
    def __init__(self, src): super().__init__(); self.src = src; self.running = True
    def run(self):
        # Jetson optimized pipeline
        gst = (f"nvarguscamerasrc sensor-id={self.src} ! "
               "video/x-raw(memory:NVMM), width=1280, height=720, framerate=30/1 ! "
               "nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! appsink drop=1")
        cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
        if not cap.isOpened(): cap = cv2.VideoCapture(self.src)
        while self.running:
            ret, frame = cap.read()
            if ret: self.change_pixmap.emit(frame)
            time.sleep(0.03)
        cap.release()
    def stop(self): self.running = False; self.wait()

class MonitorThread(QThread):
    stats_updated = pyqtSignal(dict)
    def run(self):
        while True:
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            self.stats_updated.emit({
                'cpu': psutil.cpu_percent(),
                'ram_p': mem.percent,
                'ram_u': mem.used / (1024**3),
                'ram_t': mem.total / (1024**3),
                'disk': disk.percent
            })
            time.sleep(2)

# =============================================================================
# UI COMPONENTS
# =============================================================================

class OverlayDialog(QWidget):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.resize(parent.size())
        self.setAttribute(Qt.WA_Dialog)
        self.theme = parent.current_theme
        
        # Dimmed BG
        self.setStyleSheet("background-color: rgba(0, 0, 0, 180);")
        
        l = QVBoxLayout(self); l.setAlignment(Qt.AlignCenter)
        
        self.box = QFrame()
        self.box.setFixedWidth(500)
        self.box.setStyleSheet(f"""
            QFrame {{ background-color: {self.theme['card']}; border-radius: 16px; border: 1px solid {self.theme['border']}; }}
            QLabel {{ color: {self.theme['text']}; }}
        """)
        
        self.inner = QVBoxLayout(self.box); self.inner.setContentsMargins(30,30,30,30); self.inner.setSpacing(15)
        
        t = QLabel(title); t.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {self.theme['accent']};")
        t.setAlignment(Qt.AlignCenter)
        self.inner.addWidget(t)
        
        l.addWidget(self.box)
        self.hide()

    def close_modal(self): self.hide(); self.deleteLater()

class ResizableCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumSize(320, 240)
        QSizeGrip(self).setStyleSheet("background: transparent; width: 16px; height: 16px;")
        
        # Shadow Effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20); shadow.setColor(QColor(0,0,0,50)); shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

# =============================================================================
# PAGE WIDGETS
# =============================================================================

class CameraWidget(ResizableCard):
    removed = pyqtSignal(int)
    def __init__(self, idx, theme):
        super().__init__()
        self.idx = idx
        l = QVBoxLayout(self); l.setContentsMargins(0,0,0,0)
        
        # Hover Control Bar
        bar = QWidget(self); bar.setFixedHeight(40); bar.setStyleSheet("background: rgba(0,0,0,0.6); border-top-left-radius: 12px; border-top-right-radius: 12px;")
        bl = QHBoxLayout(bar); bl.setContentsMargins(10,0,10,0)
        lbl = QLabel(f"Camera {idx}"); lbl.setStyleSheet("color: white; font-weight: bold;")
        btn = QPushButton("×"); btn.setFixedSize(24,24)
        btn.setStyleSheet("background: transparent; color: #fff; font-size: 20px; border: none;")
        btn.clicked.connect(self.close)
        bl.addWidget(lbl); bl.addStretch(); bl.addWidget(btn)
        
        self.view = QLabel("Loading..."); self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background: #000; border-radius: 12px;")
        l.addWidget(bar); l.addWidget(self.view)
        
        self.t = VideoThread(idx)
        self.t.change_pixmap.connect(self.update_img)
        self.t.start()
        
    def update_img(self, cv_img):
        rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch*w, QImage.Format_RGB888)
        self.view.setPixmap(QPixmap.fromImage(qimg).scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def close(self): self.t.stop(); self.removed.emit(self.idx); self.deleteLater()

class DockerWidget(ResizableCard):
    removed = pyqtSignal(str)
    def __init__(self, name, script_path, theme):
        super().__init__()
        self.name = name
        self.script = script_path
        
        l = QVBoxLayout(self); l.setSpacing(10); l.setContentsMargins(20,20,20,20)
        
        # Header
        hl = QHBoxLayout()
        t = QLabel(name); t.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {theme['accent']};")
        x = QPushButton("×"); x.setFixedSize(24,24); x.clicked.connect(lambda: self.removed.emit(name))
        x.setStyleSheet(f"color: {theme['text_sec']}; background: transparent; font-size: 20px;")
        hl.addWidget(t); hl.addStretch(); hl.addWidget(x)
        l.addLayout(hl)
        
        # Info
        info = QLabel(f"Bind Mount: {os.path.basename(script_path)}\nStatus: Ready to Launch")
        info.setObjectName("SecondaryText")
        l.addWidget(info)
        
        # Actions
        btn = QPushButton("Run Container"); btn.setObjectName("PrimaryButton")
        btn.clicked.connect(self.run_container)
        l.addWidget(btn); l.addStretch()
        
    def run_container(self):
        # VOLUME MOUNT LOGIC
        # We mount the directory of the script to /app/ in the container
        host_dir = os.path.dirname(self.script)
        script_name = os.path.basename(self.script)
        
        cmd = f"gnome-terminal -- docker run -it --rm --runtime nvidia -v \"{host_dir}:/app\" -w /app l4t-pytorch python3 {script_name}"
        subprocess.Popen(cmd, shell=True)
        QMessageBox.information(self, "Launched", f"Running {script_name} in new terminal window.")

class SettingsWidget(QWidget):
    theme_toggled = pyqtSignal()
    def __init__(self, theme):
        super().__init__()
        l = QVBoxLayout(self); l.setSpacing(20)
        
        # Header
        l.addWidget(QLabel("Performance & Settings"))
        
        # Grid Stats
        grid = QGridLayout()
        self.cards = {}
        for i, (k, title) in enumerate([('cpu', 'CPU Load'), ('ram', 'Memory Usage'), ('disk', 'Disk Space')]):
            c = QFrame(); c.setObjectName("Card"); cl = QVBoxLayout(c)
            cl.addWidget(QLabel(title))
            val = QLabel("0%"); val.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {theme['text']};")
            cl.addWidget(val)
            bar = QProgressBar(); bar.setFixedHeight(6); bar.setTextVisible(False)
            cl.addWidget(bar)
            grid.addWidget(c, 0, i)
            self.cards[k] = (val, bar)
        l.addLayout(grid)
        
        # Device Info
        dev = QFrame(); dev.setObjectName("Card"); dl = QHBoxLayout(dev)
        inf = QLabel(f"Host: {platform.node()} | OS: {platform.system()} {platform.release()} | Python: {platform.python_version()}")
        inf.setObjectName("SecondaryText")
        dl.addWidget(inf)
        l.addWidget(dev)
        
        # Appearance
        app = QFrame(); app.setObjectName("Card"); al = QHBoxLayout(app)
        al.addWidget(QLabel("Appearance Mode"))
        al.addStretch()
        btn_theme = QPushButton("Toggle Dark/Light Mode")
        btn_theme.clicked.connect(self.theme_toggled.emit)
        al.addWidget(btn_theme)
        l.addWidget(app)
        l.addStretch()
        
        self.mon = MonitorThread()
        self.mon.stats_updated.connect(self.update_stats)
        self.mon.start()

    def update_stats(self, data):
        self.cards['cpu'][0].setText(f"{data['cpu']}%"); self.cards['cpu'][1].setValue(int(data['cpu']))
        self.cards['ram'][0].setText(f"{int(data['ram_p'])}%"); self.cards['ram'][1].setValue(int(data['ram_p']))
        self.cards['disk'][0].setText(f"{data['disk']}%"); self.cards['disk'][1].setValue(int(data['disk']))

# =============================================================================
# MAIN APP
# =============================================================================

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1300, 850)
        self.is_dark = True
        self.current_theme = Theme.DARK
        self.apply_theme()
        
        # Layout
        w = QWidget(); self.setCentralWidget(w); h = QHBoxLayout(w); h.setContentsMargins(0,0,0,0); h.setSpacing(0)
        
        # Sidebar
        self.sidebar = QFrame(); self.sidebar.setObjectName("Sidebar"); self.sidebar.setFixedWidth(260)
        sl = QVBoxLayout(self.sidebar); sl.setSpacing(10); sl.setContentsMargins(20,40,20,20)
        sl.addWidget(QLabel("Jetson Studio")); sl.children()[0].setStyleSheet(f"font-size: 24px; font-weight: 800; color: {self.current_theme['accent']}; margin-bottom: 20px;")
        
        self.navs = []
        for n, i in [("Cameras", 0), ("Docker Environments", 1), ("Settings", 2)]:
            b = QPushButton(n); b.setObjectName("NavButton"); b.setCheckable(True)
            b.clicked.connect(lambda c, x=i: self.switch(x))
            sl.addWidget(b); self.navs.append(b)
        sl.addStretch()
        h.addWidget(self.sidebar)
        
        # Content
        self.stack = QStackedWidget(); self.stack.setContentsMargins(30,30,30,30)
        h.addWidget(self.stack)
        
        # Pages
        self.create_pages()
        self.switch(0)

    def create_pages(self):
        # 1. Cameras
        p1 = QWidget(); l1 = QVBoxLayout(p1)
        l1.addWidget(QLabel("Active Cameras", styleSheet="font-size: 28px; font-weight: bold;"))
        self.flow_cam = self.create_flow(l1)
        self.add_btn(self.flow_cam, self.show_cam_modal)
        
        # 2. Docker
        p2 = QWidget(); l2 = QVBoxLayout(p2)
        l2.addWidget(QLabel("Docker Workspaces", styleSheet="font-size: 28px; font-weight: bold;"))
        self.flow_docker = self.create_flow(l2)
        self.add_btn(self.flow_docker, self.show_docker_modal)
        
        # 3. Settings
        self.p3 = SettingsWidget(self.current_theme)
        self.p3.theme_toggled.connect(self.toggle_theme)
        
        self.stack.addWidget(p1)
        self.stack.addWidget(p2)
        self.stack.addWidget(self.p3)

    def create_flow(self, layout):
        sa = QScrollArea(); sa.setWidgetResizable(True)
        w = QWidget(); fl = FlowLayout(w)
        sa.setWidget(w); layout.addWidget(sa)
        return fl

    def add_btn(self, flow, func):
        b = QFrame(); b.setObjectName("AddCard"); b.setFixedSize(320, 240); b.setCursor(Qt.PointingHandCursor)
        bl = QVBoxLayout(b); l = QLabel("+"); l.setAlignment(Qt.AlignCenter); l.setStyleSheet("font-size: 60px; font-weight: 100;")
        bl.addWidget(l)
        b.mousePressEvent = lambda e: func()
        flow.Button = b # Keep ref
        flow.addWidget(b)

    def switch(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.navs): b.setChecked(i == idx)

    def apply_theme(self):
        self.current_theme = Theme.DARK if self.is_dark else Theme.LIGHT
        self.setStyleSheet(get_stylesheet(self.current_theme))

    def toggle_theme(self):
        self.is_dark = not self.is_dark
        self.apply_theme()
        # Full reload might be needed for some sub-widgets, but stylesheet update handles most

    # MODALS
    def show_cam_modal(self):
        m = OverlayDialog(self, "Add New Camera")
        cb = QComboBox()
        import glob
        cams = glob.glob('/dev/video*')
        if not cams: cb.addItem("No devices found")
        else:
            for c in cams: cb.addItem(c, int(c.replace('/dev/video','').strip()))
        
        btn = QPushButton("Add to Grid"); btn.setObjectName("PrimaryButton")
        btn.clicked.connect(lambda: self.add_cam(m, cb.currentData()))
        
        m.inner.addWidget(QLabel("Select Device:"))
        m.inner.addWidget(cb); m.inner.addWidget(btn); m.inner.addWidget(QPushButton("Cancel", clicked=m.close_modal))
        m.show()

    def add_cam(self, m, idx):
        if idx is not None:
            w = CameraWidget(idx, self.current_theme)
            w.removed.connect(lambda i: w.deleteLater())
            self.flow_cam.addWidget(w); # Ideally re-order before plus
            # Simple re-add trick for order
            self.flow_cam.removeWidget(self.flow_cam.Button)
            self.flow_cam.addWidget(w)
            self.flow_cam.addWidget(self.flow_cam.Button)
        m.close_modal()

    def show_docker_modal(self):
        m = OverlayDialog(self, "New Docker Workspace")
        
        # File Selection
        self.sel_file = None
        lbl_file = QLabel("No file selected")
        btn_file = QPushButton("Select Python Script / File")
        
        def pick_file():
            f, _ = QFileDialog.getOpenFileName(self, "Select Script", os.path.expanduser("~"), "Python Files (*.py);;All Files (*)")
            if f: 
                self.sel_file = f
                lbl_file.setText(os.path.basename(f))
        
        btn_file.clicked.connect(pick_file)
        
        btn_run = QPushButton("Create Workspace"); btn_run.setObjectName("PrimaryButton")
        btn_run.clicked.connect(lambda: self.add_docker(m, self.sel_file))
        
        m.inner.addWidget(QLabel("Select Host File to Bind (Live Sync):"))
        m.inner.addWidget(btn_file)
        m.inner.addWidget(lbl_file)
        m.inner.addSpacing(10)
        m.inner.addWidget(btn_run)
        m.inner.addWidget(QPushButton("Cancel", clicked=m.close_modal))
        m.show()

    def add_docker(self, m, path):
        if not path:
            QMessageBox.warning(self, "Error", "Please select a file first."); return
        
        name = f"Workspace: {os.path.basename(path)}"
        w = DockerWidget(name, path, self.current_theme)
        w.removed.connect(lambda: w.deleteLater())
        
        self.flow_docker.removeWidget(self.flow_docker.Button)
        self.flow_docker.addWidget(w)
        self.flow_docker.addWidget(self.flow_docker.Button)
        m.close_modal()

# Re-use FlowLayout from previous snippet (It was solid)
class FlowLayout(QLayout):
    def __init__(self, parent=None): super().__init__(parent); self.idx = []
    def __del__(self): pass
    def addItem(self, i): self.idx.append(i)
    def count(self): return len(self.idx)
    def itemAt(self, i): return self.idx[i] if 0<=i<len(self.idx) else None
    def takeAt(self, i): return self.idx.pop(i) if 0<=i<len(self.idx) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))
    def hasHeightForWidth(self): return True
    def heightForWidth(self, w): return self.do(QRect(0,0,w,0), True)
    def setGeometry(self, r): super().setGeometry(r); self.do(r, False)
    def sizeHint(self): return QSize(500,500)
    def do(self, rect, test):
        x, y, lh, sp = rect.x(), rect.y(), 0, 20
        for item in self.idx:
            w = item.widget()
            nextX = x + item.sizeHint().width() + sp
            if nextX - sp > rect.right() and lh > 0:
                x, y, lh = rect.x(), y + lh + sp, 0
                nextX = x + item.sizeHint().width() + sp
            if not test: item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x, lh = nextX, max(lh, item.sizeHint().height())
        return y + lh - rect.y()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())
