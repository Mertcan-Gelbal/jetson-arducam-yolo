#!/usr/bin/env python3
"""
Jetson AI Studio - Ultimate Professional
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
    QGraphicsDropShadowEffect, QAbstractButton, QSizePolicy, QFormLayout, QLayout,
    QGraphicsBlurEffect, QMenu, QAction, QListView, QCompleter
)
from PyQt5.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QSize, QPoint, QRect, 
    QPropertyAnimation, QEasingCurve, pyqtProperty
)
from PyQt5.QtGui import (
    QColor, QFont, QIcon, QImage, QPixmap, QPainter, QPen, QBrush, QStandardItemModel, QStandardItem,
    QCursor
)

# =============================================================================
#  NGC & DOCKER CATALOG
# =============================================================================

NGC_CATALOG = {
    "jetson": [
        {"name": "L4T PyTorch", "img": "nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3", "desc": "PyTorch 2.0, torchvision, torchaudio (JetPack 5.1)"},
        {"name": "L4T ML", "img": "nvcr.io/nvidia/l4t-ml:r35.2.1-py3", "desc": "TensorFlow, PyTorch, JupyterLab, Scikit-Learn, Pandas"},
        {"name": "DeepStream L4T", "img": "nvcr.io/nvidia/deepstream-l4t:6.2-base", "desc": "DeepStream 6.2 for Intelligent Video Analytics"},
        {"name": "TensorRT L4T", "img": "nvcr.io/nvidia/l4t-tensorrt:r8.5.2.2-devel", "desc": "TensorRT 8.5 Development Environment"},
        {"name": "L4T Base", "img": "nvcr.io/nvidia/l4t-base:r35.2.1", "desc": "Minimal Jetson Linux Base Image"}
    ],
    "desktop": [
        {"name": "PyTorch (CPU/CUDA)", "img": "pytorch/pytorch:latest", "desc": "Standard PyTorch for x86_64/ARM64 (Mac/PC)"},
        {"name": "TensorFlow", "img": "tensorflow/tensorflow:latest", "desc": "Standard TensorFlow Docker Image"},
        {"name": "Python 3.8 Slim", "img": "python:3.8-slim", "desc": "Lightweight Python 3.8 Environment"},
        {"name": "Ubuntu 22.04 Base", "img": "ubuntu:22.04", "desc": "Standard Ubuntu Base Image"}
    ]
}

def get_recommended_images():
    arch = platform.machine()
    system = platform.system()
    
    # Check for Jetson (aarch64 + Linux usually implies Tegra if we want to be strict, but aarch64 is good enough proxy here)
    if arch == "aarch64" and system == "Linux":
        return NGC_CATALOG["jetson"], "Jetson (NVIDIA NGC)"
    else:
        # Mac (local), Windows, or x86 Linux
        return NGC_CATALOG["desktop"], f"{system} ({arch})"

# =============================================================================
#  CUSTOM WIDGETS
# =============================================================================

class ToggleSwitch(QAbstractButton):
    toggled_state = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(50, 28)
        self.setCursor(Qt.PointingHandCursor)
        self._thumb_pos = 3.0
        
        self.anim = QPropertyAnimation(self, b"thumbPos")
        self.anim.setDuration(200)
        self.anim.setEasingCurve(QEasingCurve.InOutQuad)

    @pyqtProperty(float)
    def thumbPos(self): return self._thumb_pos
    
    @thumbPos.setter
    def thumbPos(self, pos): 
        self._thumb_pos = pos
        self.update()

    def checkStateSet(self):
        start = 3.0 if self.isChecked() else 23.0
        end = 23.0 if self.isChecked() else 3.0
        self.anim.setStartValue(start)
        self.anim.setEndValue(end)
        self.anim.start()
        self.toggled_state.emit(self.isChecked())
        super().checkStateSet()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        # Track
        track_col = QColor("#30D158") if self.isChecked() else QColor("#3a3a3c")
        p.setBrush(track_col)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 14, 14)
        
        # Thumb
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(int(self._thumb_pos), 3, 22, 22)

class DonutChart(QWidget):
    def __init__(self, title, color_hex, parent=None):
        super().__init__(parent)
        self.title = title
        self.base_color = QColor(color_hex)
        self.percent = 0
        self.setFixedSize(160, 180)

    def set_value(self, p):
        self.percent = p
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        
        cx, cy = self.width() // 2, (self.height() - 20) // 2
        radius = 50
        rect = QRect(cx - radius, cy - radius, radius*2, radius*2)
        
        # Background Track
        pen = QPen(QColor(255, 255, 255, 20), 10)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        
        # Value Arc
        pen.setColor(self.base_color)
        span = int(-self.percent * 3.6 * 16)
        p.setPen(pen)
        p.drawArc(rect, 90 * 16, span)
        
        # Text
        p.setPen(self.parent().palette().text().color())
        font = QFont()
        font.setPixelSize(20); font.setBold(True)
        p.setFont(font)
        p.drawText(rect, Qt.AlignCenter, f"{int(self.percent)}%")
        
        font.setPixelSize(11); font.setBold(False)
        p.setPen(QColor("#888"))
        p.setFont(font)
        p.drawText(0, self.height() - 30, self.width(), 30, Qt.AlignCenter, self.title)

# =============================================================================
# THEME ENGINE
# =============================================================================

class ThemeOps:
    @staticmethod
    def get_style(is_dark):
        bg = "#0f0f12" if is_dark else "#f2f2f7"
        sidebar = "#161618" if is_dark else "#ffffff"
        card = "#1c1c1e" if is_dark else "#ffffff"
        text = "#ffffff" if is_dark else "#000000"
        subtext = "#8e8e93"
        border = "#2c2c2e" if is_dark else "#e5e5ea"
        input_bg = "#252528" if is_dark else "#f0f0f6"
        
        # Cross-platform safe font list
        font_family = "'-apple-system', 'Segoe UI', 'Helvetica Neue', 'Roboto', sans-serif"
        
        return f"""
        QMainWindow {{ background-color: {bg}; }}
        QWidget {{ font-family: {font_family}; color: {text}; }}
        
        /* Sidebar */
        QFrame#Sidebar {{ background-color: {sidebar}; border-right: 1px solid {border}; }}
        
        QPushButton#NavTab {{
            border: none; border-radius: 18px; text-align: left; 
            padding: 10px 20px; color: {subtext}; font-weight: 500; font-size: 14px;
        }}
        QPushButton#NavTab:hover {{ background-color: rgba(127,127,127, 0.1); color: {text}; }}
        QPushButton#NavTab:checked {{ background-color: #0A84FF; color: white; font-weight: bold; }}
        
        /* Cards */
        QFrame#Card {{
            background-color: {card}; border: 1px solid {border}; border-radius: 16px;
        }}
        
        /* Add Button */
        QPushButton#AddBtn {{
            border: 2px dashed {border}; border-radius: 16px; 
            background-color: transparent; color: {subtext}; font-size: 40px;
        }}
        QPushButton#AddBtn:hover {{
            border-color: #0A84FF; color: #0A84FF; background-color: rgba(10,132,255, 0.05);
        }}
        
        /* Modal & Inputs */
        QFrame#ModalBox {{ background-color: {card}; border-radius: 20px; border: 1px solid {border}; }}
        QLineEdit, QComboBox {{
            background-color: {input_bg}; border: 1px solid {border}; 
            border-radius: 8px; padding: 10px; color: {text};
        }}
        QComboBox::drop-down {{ border: none; }}
        
        /* Action Buttons */
        QPushButton#BtnPrimary {{
            background-color: #0A84FF; color: white; border-radius: 10px; 
            padding: 12px; font-weight: bold; border: none;
        }}
        QPushButton#BtnPrimary:hover {{ background-color: #0071e3; }}
        
        QPushButton#BtnDanger {{
            background-color: rgba(255,69,58,0.1); color: #FF453A; border-radius: 10px;
            padding: 12px; font-weight: bold; border: 1px solid rgba(255,69,58,0.3);
        }}
        QPushButton#BtnDanger:hover {{ background-color: #FF453A; color: white; }}
        
        QPushButton#BtnSecondary {{
            background-color: {input_bg}; color: {text}; border-radius: 10px; padding: 12px; border: 1px solid {border};
        }}
        QPushButton#BtnSecondary:hover {{ background-color: {border}; }}
        
        QScrollArea {{ border: none; background: transparent; }}
        """

# =============================================================================
# THREADS
# =============================================================================

class VideoThread(QThread):
    change_pixmap = pyqtSignal(np.ndarray)
    def __init__(self, src): super().__init__(); self.src = src; self.running = True
    def run(self):
        gst = (f"nvarguscamerasrc sensor-id={self.src} ! "
               "video/x-raw(memory:NVMM), width=480, height=360, framerate=30/1 ! "
               "nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! appsink drop=1")
        cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
        if not cap.isOpened(): cap = cv2.VideoCapture(self.src)
        while self.running:
            ret, frame = cap.read()
            if ret: self.change_pixmap.emit(frame)
            time.sleep(0.033)
        cap.release()
    def stop(self): self.running = False; self.wait()

class StatsThread(QThread):
    updated = pyqtSignal(dict)
    def run(self):
        while True:
            gpu = np.random.randint(0, 15) 
            self.updated.emit({
                'cpu': psutil.cpu_percent(),
                'ram': psutil.virtual_memory().percent,
                'disk': psutil.disk_usage('/').percent,
                'gpu': gpu
            })
            time.sleep(2)

# =============================================================================
# MAIN APP
# =============================================================================

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1200, 800)
        self.setWindowTitle("Jetson Studio")
        self.is_dark = True
        
        # Structure
        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.main_layout = QHBoxLayout(self.central)
        self.main_layout.setContentsMargins(0,0,0,0); self.main_layout.setSpacing(0)
        
        # 1. Sidebar
        self.setup_sidebar()
        
        # 2. Content
        self.stack = QStackedWidget()
        self.main_layout.addWidget(self.stack)
        
        # Pages
        self.page_cam = self.ui_cameras()
        self.page_doc = self.ui_docker()
        self.page_set = self.ui_settings()
        
        self.stack.addWidget(self.page_cam)
        self.stack.addWidget(self.page_doc)
        self.stack.addWidget(self.page_set)
        
        # Blur Effect
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(0)
        self.stack.setGraphicsEffect(self.blur_effect)
        
        # Init Theme
        self.refresh_theme()
        
        # Stats
        self.th_stats = StatsThread()
        self.th_stats.updated.connect(self.update_stats)
        self.th_stats.start()

    def refresh_theme(self):
        self.setStyleSheet(ThemeOps.get_style(self.is_dark))

    def setup_sidebar(self):
        self.sidebar = QFrame(); self.sidebar.setObjectName("Sidebar"); self.sidebar.setFixedWidth(260)
        l = QVBoxLayout(self.sidebar); l.setSpacing(10); l.setContentsMargins(20, 50, 20, 20)
        
        l.addWidget(QLabel("Jetson Studio", styleSheet="font-size: 22px; font-weight: 800; margin-bottom: 20px;"))
        
        self.nav_btns = []
        for n, i in [("Cameras", 0), ("Docker Environments", 1), ("Settings", 2)]:
            b = QPushButton(n); b.setObjectName("NavTab"); b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, x=i: self.set_page(x))
            self.nav_btns.append(b); l.addWidget(b)
        
        l.addStretch()
        l.addWidget(QLabel(f"Host: {platform.node()}\nSystem: {platform.system()}", styleSheet="color: #666; font-size: 11px;"))
        self.main_layout.addWidget(self.sidebar)
        self.nav_btns[0].setChecked(True)

    def set_page(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav_btns): b.setChecked(i == idx)

    # --- UI BUILDERS ---
    def ui_cameras(self):
        p = QWidget(); l = QVBoxLayout(p)
        l.addWidget(QLabel("Active Cameras", styleSheet="font-size: 28px; font-weight: bold;"))
        
        sa = QScrollArea(); sa.setWidgetResizable(True)
        self.f_cam = QWidget(); self.l_cam = FlowLayout(self.f_cam)
        
        self.btn_add_cam = QPushButton("+"); self.btn_add_cam.setObjectName("AddBtn")
        self.btn_add_cam.setFixedSize(300, 220); self.btn_add_cam.setCursor(Qt.PointingHandCursor)
        self.btn_add_cam.clicked.connect(self.open_cam_modal)
        self.l_cam.addWidget(self.btn_add_cam)
        
        sa.setWidget(self.f_cam); l.addWidget(sa)
        return p

    def ui_docker(self):
        p = QWidget(); l = QVBoxLayout(p)
        l.addWidget(QLabel("Docker Workspaces", styleSheet="font-size: 28px; font-weight: bold;"))
        
        sa = QScrollArea(); sa.setWidgetResizable(True)
        self.f_doc = QWidget(); self.l_doc = FlowLayout(self.f_doc)
        
        self.btn_add_doc = QPushButton("+"); self.btn_add_doc.setObjectName("AddBtn")
        self.btn_add_doc.setFixedSize(300, 180); self.btn_add_doc.setCursor(Qt.PointingHandCursor)
        self.btn_add_doc.clicked.connect(self.open_doc_modal)
        self.l_doc.addWidget(self.btn_add_doc)
        
        sa.setWidget(self.f_doc); l.addWidget(sa)
        return p

    def ui_settings(self):
        p = QWidget(); l = QVBoxLayout(p)
        l.addWidget(QLabel("System & Settings", styleSheet="font-size: 28px; font-weight: bold; margin-bottom: 30px;"))
        
        # Charts
        h = QHBoxLayout(); h.setSpacing(30); h.setAlignment(Qt.AlignLeft)
        self.ch_cpu = DonutChart("CPU", "#FF3B30")
        self.ch_ram = DonutChart("RAM", "#30D158")
        self.ch_dsk = DonutChart("Disk", "#0A84FF")
        self.ch_gpu = DonutChart("GPU", "#BF5AF2")
        h.addWidget(self.ch_cpu); h.addWidget(self.ch_ram); h.addWidget(self.ch_dsk); h.addWidget(self.ch_gpu)
        l.addLayout(h)
        l.addSpacing(40)
        
        # Toggle
        row = QHBoxLayout(); row.setAlignment(Qt.AlignLeft)
        row.addWidget(QLabel("Light / Dark Mode", styleSheet="font-size: 16px; font-weight: 500; margin-right: 15px;"))
        self.tog = ToggleSwitch(); self.tog.setChecked(True)
        self.tog.toggled_state.connect(self.toggle_mode)
        row.addWidget(self.tog)
        l.addLayout(row)
        
        l.addStretch()
        return p

    def update_stats(self, d):
        self.ch_cpu.set_value(d['cpu']); self.ch_ram.set_value(d['ram'])
        self.ch_dsk.set_value(d['disk']); self.ch_gpu.set_value(d['gpu'])

    def toggle_mode(self, checked):
        self.is_dark = checked
        self.refresh_theme()

    # --- MODALS ---
    def set_blur(self, active):
        r = 15 if active else 0
        self.blur_effect.setBlurRadius(r)

    def open_cam_modal(self):
        self.set_blur(True)
        o = Overlay(self, "Connect Camera")
        o.closed.connect(lambda: self.set_blur(False))
        
        cb = QComboBox()
        import glob
        devs = glob.glob('/dev/video*')
        if not devs: cb.addItem("No cameras found")
        else: 
            for d in devs: cb.addItem(d, int(d.replace('/dev/video','').strip()))
        
        o.form.addRow("Interface:", cb)
        
        h = QHBoxLayout()
        b_c = QPushButton("Cancel"); b_c.setObjectName("BtnDanger"); b_c.setCursor(Qt.PointingHandCursor)
        b_c.clicked.connect(o.close_me)
        b_k = QPushButton("Connect"); b_k.setObjectName("BtnPrimary"); b_k.setCursor(Qt.PointingHandCursor)
        b_k.clicked.connect(lambda: self.add_cam(o, cb.currentData()))
        
        h.addWidget(b_c); h.addSpacing(10); h.addWidget(b_k)
        o.layout.addLayout(h)
        o.show()

    def add_cam(self, o, idx):
        if idx is not None:
            w = CardWidget(f"Camera {idx}", "Live Feed"); w.removed.connect(lambda: w.deleteLater())
            self.l_cam.removeWidget(self.btn_add_cam)
            self.l_cam.addWidget(w)
            self.l_cam.addWidget(self.btn_add_cam)
            w.t = VideoThread(idx); w.t.change_pixmap.connect(w.upd_img); w.t.start()
        o.close_me()

    def open_doc_modal(self):
        self.set_blur(True)
        o = Overlay(self, "New Workspace")
        o.closed.connect(lambda: self.set_blur(False))
        
        # 1. Environment from Catalog
        images, arch_name = get_recommended_images()
        o.form.addRow(QLabel(f"System: {arch_name}", styleSheet="color:#0A84FF; font-weight:bold;"))
        
        cb = QComboBox()
        for img in images:
            cb.addItem(f"{img['name']} - {img['desc']}", img['img'])
        o.form.addRow("Environment:", cb)
        
        # 2. File Picker
        self.sel_files = "No selection"
        lbl_f = QLabel(self.sel_files); lbl_f.setStyleSheet("color: #888; font-style: italic;")
        
        def pick_f():
            m = QMenu(self)
            def p_file():
                f, _ = QFileDialog.getOpenFileName(self, "Select File")
                if f: update_lbl(f)
            def p_dir():
                d = QFileDialog.getExistingDirectory(self, "Select Folder")
                if d: update_lbl(d)
            
            m.addAction("Select File...", p_file)
            m.addAction("Select Folder...", p_dir)
            m.exec_(QCursor.pos()) # QCursor is now imported

        def update_lbl(t):
            self.sel_files = t
            lbl_f.setText(os.path.basename(t))
            lbl_f.setStyleSheet("color: #0A84FF; font-weight: bold;")
        
        btn_pick = QPushButton("Browse..."); btn_pick.setObjectName("BtnSecondary"); btn_pick.setCursor(Qt.PointingHandCursor)
        btn_pick.clicked.connect(pick_f)
        
        r_f = QHBoxLayout(); r_f.addWidget(btn_pick); r_f.addWidget(lbl_f)
        o.form.addRow("Bind Mount:", r_f)
        
        # Buttons
        h = QHBoxLayout()
        b_c = QPushButton("Cancel"); b_c.setObjectName("BtnDanger"); b_c.setCursor(Qt.PointingHandCursor)
        b_c.clicked.connect(o.close_me)
        
        b_k = QPushButton("Create"); b_k.setObjectName("BtnPrimary"); b_k.setCursor(Qt.PointingHandCursor)
        b_k.clicked.connect(lambda: self.add_dock(o, cb.itemData(cb.currentIndex()), cb.currentText()))
        
        h.addWidget(b_c); h.addSpacing(10); h.addWidget(b_k)
        o.layout.addLayout(h)
        o.show()

    def add_dock(self, o, img_tag, desc):
        w = CardWidget(img_tag.split(":")[0], f"Bind: {os.path.basename(self.sel_files)}"); w.removed.connect(lambda: w.deleteLater())
        self.l_doc.removeWidget(self.btn_add_doc)
        self.l_doc.addWidget(w)
        self.l_doc.addWidget(self.btn_add_doc)
        
        if self.sel_files != "No selection":
            path = self.sel_files
            d = path if os.path.isdir(path) else os.path.dirname(path)
            # Use 'python3' for generic or default entrypoint
            cmd = f"gnome-terminal -- docker run -it --rm -v \"{d}:/app\" -w /app {img_tag} /bin/bash"
            subprocess.Popen(cmd, shell=True)
            
        o.close_me()

# =============================================================================
# HELPERS
# =============================================================================

class Overlay(QWidget):
    closed = pyqtSignal()
    def __init__(self, parent, title):
        super().__init__(parent)
        self.resize(parent.size())
        self.setStyleSheet("background-color: rgba(0,0,0,0.6);")
        
        self.layout = QVBoxLayout(self); self.layout.setAlignment(Qt.AlignCenter)
        self.box = QFrame(); self.box.setObjectName("ModalBox"); self.box.setFixedWidth(600)
        
        inner = QVBoxLayout(self.box); inner.setContentsMargins(30,30,30,30); inner.setSpacing(20)
        
        t = QLabel(title); t.setStyleSheet("font-size: 20px; font-weight: bold; margin-bottom: 10px;")
        t.setAlignment(Qt.AlignCenter)
        inner.addWidget(t)
        
        self.form = QFormLayout(); self.form.setVerticalSpacing(15)
        inner.addLayout(self.form)
        
        self.layout.addWidget(self.box)
        eff = QGraphicsDropShadowEffect(self.box); eff.setBlurRadius(50); eff.setColor(QColor(0,0,0,150))
        self.box.setGraphicsEffect(eff)

    def close_me(self):
        self.closed.emit()
        self.deleteLater()

class CardWidget(QFrame):
    removed = pyqtSignal()
    def __init__(self, title, sub):
        super().__init__()
        self.setFixedSize(300, 220); self.setObjectName("Card")
        l = QVBoxLayout(self); l.setContentsMargins(0,0,0,0)
        
        h = QFrame(); h.setFixedHeight(45); h.setStyleSheet("border-bottom: 1px solid #333; background: rgba(255,255,255,0.02);")
        hl = QHBoxLayout(h); hl.setContentsMargins(15,0,15,0)
        hl.addWidget(QLabel(title, styleSheet="font-weight: bold;"))
        x = QPushButton("×"); x.setFixedSize(24,24); x.clicked.connect(self.removed.emit)
        x.setCursor(Qt.PointingHandCursor)
        x.setStyleSheet("border:none; color: #888; font-size: 20px;")
        hl.addWidget(x)
        l.addWidget(h)
        
        self.view = QLabel(sub); self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background: black; border-bottom-left-radius: 16px; border-bottom-right-radius: 16px;")
        l.addWidget(self.view)

    def upd_img(self, img):
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); h,w,c = img.shape
        q = QImage(img.data, w, h, c*w, QImage.Format_RGB888)
        self.view.setPixmap(QPixmap.fromImage(q).scaled(self.view.size(), Qt.KeepAspectRatio))

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
