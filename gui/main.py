#!/usr/bin/env python3
"""
Jetson AI Studio - Professional
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
    QLabel, QPushButton, QStackedWidget, QFrame,
    QScrollArea, QGridLayout, QComboBox, QFileDialog, 
    QGraphicsDropShadowEffect, QAbstractButton, QSizePolicy, QFormLayout
)
from PyQt5.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize, QPoint, QRect, 
    QPropertyAnimation, QEasingCurve, pyqtProperty
)
from PyQt5.QtGui import (
    QColor, QFont, QIcon, QImage, QPixmap, QPainter, QPen, QBrush, QPainterPath, QLinearGradient
)

# =============================================================================
#  CUSTOM WIDGETS (CHARTS, TOGGLES)
# =============================================================================

class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(60, 32)
        self.setCursor(Qt.PointingHandCursor)
        self._thumb_pos = 4.0
        
        self.anim = QPropertyAnimation(self, b"thumbPos")
        self.anim.setDuration(250)
        self.anim.setEasingCurve(QEasingCurve.InOutQuad)

    @pyqtProperty(float)
    def thumbPos(self): return self._thumb_pos
    
    @thumbPos.setter
    def thumbPos(self, pos): 
        self._thumb_pos = pos
        self.update()

    def checkStateSet(self):
        start = 4.0 if self.isChecked() else 34.0
        end = 34.0 if self.isChecked() else 4.0
        self.anim.setStartValue(start)
        self.anim.setEndValue(end)
        self.anim.start()
        super().checkStateSet()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        # Track
        track_color = QColor("#34C759") if self.isChecked() else QColor("#3a3a3c")
        p.setBrush(track_color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 16, 16)
        
        # Thumb
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(int(self._thumb_pos), 4, 24, 24)

class DonutChart(QWidget):
    def __init__(self, title, color_hex, parent=None):
        super().__init__(parent)
        self.title = title
        self.color = QColor(color_hex)
        self.percent = 0
        self.setFixedSize(140, 160)

    def set_value(self, p):
        self.percent = p
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        rect = QRect(10, 10, 120, 120)
        
        # Background Circle
        pen = QPen(QColor("#2c2c2e"), 12)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        
        # Value Arc
        pen.setColor(self.color)
        span = int(-self.percent * 3.6 * 16)
        p.setPen(pen)
        p.drawArc(rect, 90 * 16, span)
        
        # Text
        p.setPen(QColor("#ffffff"))
        font = QFont("Arial", 18, QFont.Bold)
        p.setFont(font)
        p.drawText(rect, Qt.AlignCenter, f"{int(self.percent)}%")
        
        font.setPointSize(10)
        font.setBold(False)
        p.setPen(QColor("#8a8a8e"))
        p.setFont(font)
        p.drawText(0, 135, 140, 20, Qt.AlignCenter, self.title)

# =============================================================================
# THEME ENGINE
# =============================================================================

class Theme:
    DARK = {
        "bg": "#000000", "sidebar": "#121212", "card": "#18181A",
        "text": "#FFFFFF", "text_sec": "#8E8E93", "accent": "#0A84FF",
        "input": "#242426", "border": "#333333"
    }

STYLE = """
/* Global */
QMainWindow { background-color: #000000; }
QWidget { font-family: 'Segoe UI', system-ui, sans-serif; }

/* Sidebar Tabs (Pill Shape) */
QPushButton#NavButton {
    background: transparent;
    color: #8E8E93;
    border: none;
    border-radius: 20px;
    padding: 10px 20px;
    font-weight: 500;
    font-size: 14px;
    text-align: left;
}
QPushButton#NavButton:hover {
    background-color: rgba(255,255,255,0.05);
    color: white;
}
QPushButton#NavButton:checked {
    background-color: #0A84FF; /* Vivid Blue */
    color: white;
    font-weight: bold;
}

/* Modern Cards */
QFrame#Card, QFrame#AddCard {
    background-color: #18181A;
    border: 1px solid #2C2C2E;
    border-radius: 20px;
}
QFrame#AddCard { border-style: dashed; border-color: #444; }
QFrame#AddCard:hover { border-color: #0A84FF; background-color: rgba(10,132,255, 0.05); }

/* Inputs & Modals */
QLineEdit, QComboBox {
    background-color: #242426;
    border: 1px solid #38383A;
    border-radius: 8px;
    padding: 10px;
    color: white;
    font-size: 13px;
}
QLabel#ModalTitle { font-size: 20px; font-weight: bold; color: white; margin-bottom: 10px; }
QLabel#Label { font-size: 13px; color: #8E8E93; font-weight: 500; }

/* Buttons */
QPushButton#PrimaryButton {
    background-color: #0A84FF;
    color: white;
    border-radius: 8px;
    padding: 12px;
    font-weight: bold;
    border: none;
}
QPushButton#PrimaryButton:hover { background-color: #0077EA; }

QPushButton#SecondaryButton {
    background-color: #2C2C2E;
    color: white;
    border-radius: 8px;
    padding: 12px;
    border: none;
}
QPushButton#SecondaryButton:hover { background-color: #3A3A3C; }

QScrollArea { border: none; background: transparent; }
"""

# =============================================================================
# THREADS
# =============================================================================

class VideoThread(QThread):
    change_pixmap = pyqtSignal(np.ndarray)
    def __init__(self, src): super().__init__(); self.src = src; self.running = True
    def run(self):
        gst = (f"nvarguscamerasrc sensor-id={self.src} ! "
               "video/x-raw(memory:NVMM), width=640, height=480, framerate=30/1 ! "
               "nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! appsink drop=1")
        cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
        if not cap.isOpened(): cap = cv2.VideoCapture(self.src)
        while self.running:
            ret, frame = cap.read()
            if ret: self.change_pixmap.emit(frame)
            time.sleep(0.03)
        cap.release()
    def stop(self): self.running = False; self.wait()

class StatsThread(QThread):
    updated = pyqtSignal(dict)
    def run(self):
        while True:
            # Simulate GPU since psutil doesn't support Jetson GPU natively without tegrastats
            gpu_sim = np.random.randint(5, 30) 
            self.updated.emit({
                'cpu': psutil.cpu_percent(),
                'ram': psutil.virtual_memory().percent,
                'disk': psutil.disk_usage('/').percent,
                'gpu': gpu_sim
            })
            time.sleep(1.5)

# =============================================================================
# MAIN UI
# =============================================================================

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1100, 750)
        self.setWindowTitle("Jetson Studio Professional")
        self.setStyleSheet(STYLE)
        
        # Central Layout
        central = QWidget(); self.setCentralWidget(central)
        main_layout = QHBoxLayout(central); main_layout.setContentsMargins(0,0,0,0); main_layout.setSpacing(0)
        
        # 1. Sidebar
        self.setup_sidebar(main_layout)
        
        # 2. Content Area
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(30, 30, 30, 30)
        main_layout.addWidget(self.stack)
        
        self.page_1 = self.ui_cameras()
        self.page_2 = self.ui_docker()
        self.page_3 = self.ui_settings()
        
        self.stack.addWidget(self.page_1)
        self.stack.addWidget(self.page_2)
        self.stack.addWidget(self.page_3)
        
        # Monitor
        self.th_stats = StatsThread()
        self.th_stats.updated.connect(self.update_charts)
        self.th_stats.start()

    def setup_sidebar(self, layout):
        bar = QFrame(); bar.setFixedWidth(260); bar.setStyleSheet("background-color: #121212; border-right: 1px solid #222;")
        l = QVBoxLayout(bar); l.setSpacing(10); l.setContentsMargins(20, 50, 20, 20)
        
        title = QLabel("Jetson Studio")
        title.setStyleSheet("color: white; font-size: 22px; font-weight: 800; margin-bottom: 30px;")
        l.addWidget(title)
        
        self.tabs = []
        for name, idx in [("Cameras", 0), ("Docker Environments", 1), ("Settings", 2)]:
            btn = QPushButton(name)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, x=idx: self.set_tab(x))
            self.tabs.append(btn)
            l.addWidget(btn)
            
        l.addStretch()
        
        info = QLabel(f"Host: {platform.node()}\nOS: {platform.system()}")
        info.setStyleSheet("color: #555; font-size: 11px;")
        l.addWidget(info)
        
        layout.addWidget(bar)
        self.tabs[0].setChecked(True)

    def set_tab(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, t in enumerate(self.tabs): t.setChecked(i == idx)

    # --- PAGES ---

    def ui_cameras(self):
        p = QWidget()
        l = QVBoxLayout(p); l.setAlignment(Qt.AlignTop)
        
        l.addWidget(QLabel("Active Cameras", styleSheet="font-size: 28px; font-weight: bold; color: white;"))
        l.addWidget(QLabel("Real-time video feeds from CSI and USB sources.", styleSheet="color: #888; margin-bottom: 20px;"))
        
        sa = QScrollArea(); sa.setWidgetResizable(True)
        self.cam_con = QWidget(); self.cam_flow = FlowLayout(self.cam_con)
        
        # Add Button
        self.btn_add_cam = QPushButton("+")
        self.btn_add_cam.setFixedSize(300, 220)
        self.btn_add_cam.setObjectName("AddCard")
        self.btn_add_cam.setCursor(Qt.PointingHandCursor)
        self.btn_add_cam.setStyleSheet("""
            QPushButton#AddCard { border: 2px dashed #444; border-radius: 20px; font-size: 60px; color: #555; background: transparent; }
            QPushButton#AddCard:hover { border-color: #0A84FF; color: #0A84FF; background-color: rgba(10,132,255,0.05); }
        """)
        self.btn_add_cam.clicked.connect(self.modal_camera)
        
        self.cam_flow.addWidget(self.btn_add_cam)
        sa.setWidget(self.cam_con)
        l.addWidget(sa)
        return p

    def ui_docker(self):
        p = QWidget()
        l = QVBoxLayout(p); l.setAlignment(Qt.AlignTop)
        
        l.addWidget(QLabel("Docker Workspaces", styleSheet="font-size: 28px; font-weight: bold; color: white;"))
        l.addWidget(QLabel("Containerized environments with mapped local files.", styleSheet="color: #888; margin-bottom: 20px;"))
        
        sa = QScrollArea(); sa.setWidgetResizable(True)
        self.dock_con = QWidget(); self.dock_flow = FlowLayout(self.dock_con)
        
        self.btn_add_dock = QPushButton("+")
        self.btn_add_dock.setFixedSize(300, 180)
        self.btn_add_dock.setObjectName("AddCard")
        self.btn_add_dock.setCursor(Qt.PointingHandCursor)
        self.btn_add_dock.setStyleSheet("""
            QPushButton#AddCard { border: 2px dashed #444; border-radius: 20px; font-size: 60px; color: #555; background: transparent; }
            QPushButton#AddCard:hover { border-color: #0A84FF; color: #0A84FF; background-color: rgba(10,132,255,0.05); }
        """)
        self.btn_add_dock.clicked.connect(self.modal_docker)
        
        self.dock_flow.addWidget(self.btn_add_dock)
        sa.setWidget(self.dock_con)
        l.addWidget(sa)
        return p

    def ui_settings(self):
        p = QWidget()
        l = QVBoxLayout(p); l.setAlignment(Qt.AlignTop)
        
        l.addWidget(QLabel("System & Settings", styleSheet="font-size: 28px; font-weight: bold; color: white; margin-bottom: 30px;"))
        
        # Charts Row
        h = QHBoxLayout(); h.setSpacing(40); h.setAlignment(Qt.AlignLeft)
        self.chart_cpu = DonutChart("CPU Load", "#FF3B30")
        self.chart_ram = DonutChart("RAM Usage", "#32D74B")
        self.chart_dsk = DonutChart("Disk Space", "#0A84FF")
        self.chart_gpu = DonutChart("GPU (Sim)", "#BF5AF2")
        
        h.addWidget(self.chart_cpu); h.addWidget(self.chart_gpu)
        h.addWidget(self.chart_ram); h.addWidget(self.chart_dsk)
        l.addLayout(h)
        
        l.addSpacing(50)
        
        # Appearance
        l.addWidget(QLabel("Appearance", styleSheet="font-size: 18px; font-weight: bold; color: white; margin-bottom: 10px;"))
        
        row = QHBoxLayout(); row.setAlignment(Qt.AlignLeft)
        row.addWidget(QLabel("Dark / Light Mode", styleSheet="font-size: 14px; color: #ddd; margin-right: 20px;"))
        self.toggle = ToggleSwitch()
        self.toggle.setChecked(True)
        row.addWidget(self.toggle)
        
        l.addLayout(row)
        l.addStretch()
        return p

    def update_charts(self, d):
        self.chart_cpu.set_value(d['cpu'])
        self.chart_ram.set_value(d['ram'])
        self.chart_dsk.set_value(d['disk'])
        self.chart_gpu.set_value(d['gpu'])

    # --- MODALS ---

    def modal_camera(self):
        d = self.create_overlay("Add Camera")
        
        cb = QComboBox(); 
        import glob
        cams = glob.glob('/dev/video*')
        if not cams: cb.addItem("No cameras found")
        else:
            for c in cams: cb.addItem(c, int(c.replace('/dev/video','').strip()))
            
        d.form.addRow(QLabel("Select Interface:", objectName="Label"), cb)
        
        btn = QPushButton("Connect Camera"); btn.setObjectName("PrimaryButton"); btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.add_cam_logic(d, cb.currentData()))
        d.layout.addWidget(btn)
        
        d.show()

    def add_cam_logic(self, d, idx):
        if idx is not None:
            w = CameraCard(idx); w.removed.connect(lambda: w.deleteLater())
            self.cam_flow.removeWidget(self.btn_add_cam)
            self.cam_flow.addWidget(w)
            self.cam_flow.addWidget(self.btn_add_cam)
        d.close_me()

    def modal_docker(self):
        d = self.create_overlay("New Docker Workspace")
        
        # 1. Environment
        cb_env = QComboBox()
        cb_env.addItems(["L4T PyTorch (Default)", "L4T ML", "Base Container"])
        d.form.addRow(QLabel("Environment Source:", objectName="Label"), cb_env)
        
        # 2. File Picking
        self.sel_path = None
        path_lbl = QLabel("No file/folder selected"); path_lbl.setStyleSheet("color: #666; font-style: italic;")
        
        btn_pick = QPushButton("Add Files / Folder"); btn_pick.setCursor(Qt.PointingHandCursor)
        btn_pick.clicked.connect(lambda: self.pick_file(path_lbl))
        
        d.form.addRow(QLabel("Target Path:", objectName="Label"), path_lbl)
        d.form.addRow(QLabel(""), btn_pick) # Offset
        
        # Action
        btn = QPushButton("Create Environment"); btn.setObjectName("PrimaryButton"); btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.add_dock_logic(d, cb_env.currentText()))
        d.layout.addWidget(btn)
        
        d.show()

    def pick_file(self, lbl):
        # Native Dialog allowed now (Thread safe call)
        path, _ = QFileDialog.getOpenFileName(self, "Select Script or File", os.path.expanduser("~"))
        if path:
            self.sel_path = path
            lbl.setText(os.path.basename(path))
            lbl.setStyleSheet("color: #0A84FF; font-weight: bold;")

    def add_dock_logic(self, d, env):
        if self.sel_path:
            w = DockerCard(env, self.sel_path)
            w.removed.connect(lambda: w.deleteLater())
            self.dock_flow.removeWidget(self.btn_add_dock)
            self.dock_flow.addWidget(w)
            self.dock_flow.addWidget(self.btn_add_dock)
        d.close_me()

    def create_overlay(self, title):
        o = Overlay(self)
        o.title.setText(title)
        return o

# =============================================================================
# HELPERS (Layouts, Cards)
# =============================================================================

class Overlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.resize(parent.size())
        self.setStyleSheet("background-color: rgba(0,0,0,0.8);")
        
        l = QVBoxLayout(self); l.setAlignment(Qt.AlignCenter)
        
        self.box = QFrame(); self.box.setFixedWidth(500)
        self.box.setStyleSheet("background-color: #18181A; border-radius: 16px; border: 1px solid #333;")
        
        # Content Layout
        self.layout = QVBoxLayout(self.box); self.layout.setContentsMargins(40, 40, 40, 40); self.layout.setSpacing(20)
        
        self.title = QLabel("Title"); self.title.setObjectName("ModalTitle"); self.title.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.title)
        
        self.form = QFormLayout(); self.form.setVerticalSpacing(15)
        self.layout.addLayout(self.form)
        
        cancel = QPushButton("Cancel"); cancel.setObjectName("SecondaryButton"); cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.close_me)
        self.layout.addSpacing(10)
        self.layout.addWidget(cancel)
        
        l.addWidget(self.box)
        
        # Intro Anim
        self.eff = QGraphicsDropShadowEffect(self.box); self.eff.setBlurRadius(50); self.box.setGraphicsEffect(self.eff)

    def close_me(self): self.deleteLater()

class CameraCard(QFrame):
    removed = pyqtSignal()
    def __init__(self, idx):
        super().__init__()
        self.setFixedSize(300, 220); self.setObjectName("Card")
        l = QVBoxLayout(self); l.setContentsMargins(0,0,0,0)
        
        # Header
        h = QFrame(); h.setFixedHeight(40); h.setStyleSheet("background: rgba(255,255,255,0.05); border-bottom: 1px solid #333;")
        hl = QHBoxLayout(h); hl.setContentsMargins(10,0,10,0)
        hl.addWidget(QLabel(f"Camera {idx}", styleSheet="color: white; font-weight: bold;"))
        x = QPushButton("×"); x.setFixedSize(24,24); x.clicked.connect(self.close_c); x.setStyleSheet("border:none; color:#888; font-size:18px;")
        hl.addWidget(x, 0, Qt.AlignRight)
        l.addWidget(h)
        
        self.vid = QLabel("Loading..."); self.vid.setAlignment(Qt.AlignCenter); self.vid.setStyleSheet("background: black; border-bottom-left-radius: 18px; border-bottom-right-radius: 18px;")
        l.addWidget(self.vid)
        
        self.t = VideoThread(idx); self.t.change_pixmap.connect(self.upd)
        self.t.start()
        
    def upd(self, im):
        im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB); h,w,c = im.shape
        px = QPixmap.fromImage(QImage(im.data, w, h, c*w, QImage.Format_RGB888))
        self.vid.setPixmap(px.scaled(self.vid.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def close_c(self): self.t.stop(); self.removed.emit()

class DockerCard(QFrame):
    removed = pyqtSignal()
    def __init__(self, env, path):
        super().__init__()
        self.setFixedSize(300, 180); self.setObjectName("Card")
        l = QVBoxLayout(self); l.setContentsMargins(20,20,20,20)
        
        # Head
        h = QHBoxLayout()
        h.addWidget(QLabel("🐍", styleSheet="font-size:24px;"))
        h.addWidget(QLabel(env, styleSheet="font-weight:bold; color:white; font-size:15px;"))
        x = QPushButton("×"); x.setFixedSize(20,20); x.clicked.connect(self.removed.emit); x.setStyleSheet("border:none; color:#666;")
        h.addWidget(x, 0, Qt.AlignRight)
        l.addLayout(h)
        
        l.addWidget(QLabel(f"Source: {os.path.basename(path)}", styleSheet="color:#888; font-size:12px;"))
        
        btn = QPushButton("Run Container"); btn.setObjectName("PrimaryButton"); btn.clicked.connect(lambda: self.run(path))
        l.addWidget(btn)

    def run(self, path):
        d = os.path.dirname(path); f = os.path.basename(path)
        subprocess.Popen(f"gnome-terminal -- docker run -it --rm --runtime nvidia -v \"{d}:/app\" -w /app l4t-pytorch python3 {f}", shell=True)

class FlowLayout(QLayout):
    def __init__(self, p=None): super().__init__(p); self.i = []
    def __del__(self): pass
    def addItem(self, x): self.i.append(x)
    def count(self): return len(self.i)
    def itemAt(self, x): return self.i[x] if x<len(self.i) else None
    def takeAt(self, x): return self.i.pop(x) if x<len(self.i) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))
    def hasHeightForWidth(self): return True
    def heightForWidth(self, w): return self.do(QRect(0,0,w,0), True)
    def setGeometry(self, r): super().setGeometry(r); self.do(r, False)
    def sizeHint(self): return QSize(100,100)
    def do(self, r, t):
        x, y, lh, sp = r.x(), r.y(), 0, 20
        for it in self.i:
            next_x = x + it.sizeHint().width() + sp
            if next_x - sp > r.right() and lh > 0:
                x, y, lh = r.x(), y + lh + sp, 0
                next_x = x + it.sizeHint().width() + sp
            if not t: it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x, lh = next_x, max(lh, it.sizeHint().height())
        return y + lh - r.y()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())
