#!/usr/bin/env python3
"""
Jetson AI Studio - Ultimate Edition (Refined)
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
    QSizePolicy, QLayout, QFileDialog, QGraphicsDropShadowEffect, QSizeGrip,
    QGraphicsOpacityEffect
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize, QPoint, QRect, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PyQt5.QtGui import QColor, QFont, QIcon, QImage, QPixmap, QCursor

# =============================================================================
# THEMES & STYLES
# =============================================================================

class Theme:
    DARK = {
        "bg": "#0f0f10", 
        "sidebar": "#161618", 
        "card": "#1c1c1e",
        "text": "#ffffff", 
        "text_sec": "#8a8a8e", 
        "accent": "#0a84ff", # Vivid Blue
        "border": "#2c2c2e", 
        "input": "#2c2c2e",
        "danger": "#ff453a",
        "success": "#32d74b"
    }

def get_stylesheet(theme):
    return f"""
    QMainWindow {{ background-color: {theme['bg']}; color: {theme['text']}; }}
    QWidget {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    
    /* Sidebar */
    QFrame#Sidebar {{ 
        background-color: {theme['sidebar']}; 
        border-right: 1px solid {theme['border']}; 
    }}
    
    QPushButton#NavButton {{
        text-align: left; padding: 14px 24px; border: none; border-radius: 12px;
        color: {theme['text_sec']}; font-weight: 500; font-size: 15px; background: transparent; margin: 4px 12px;
    }}
    QPushButton#NavButton:hover {{ background-color: rgba(255, 255, 255, 0.05); color: {theme['text']}; }}
    QPushButton#NavButton:checked {{ 
        background-color: {theme['accent']}; 
        color: #ffffff; 
        font-weight: 600; 
    }}

    /* Modern Cards */
    QFrame#Card, QFrame#AddCard {{
        background-color: {theme['card']};
        border: 1px solid {theme['border']};
        border-radius: 18px;
    }}
    
    QLabel {{ border: none; background: transparent; color: {theme['text']}; }}
    QLabel#Header {{ font-size: 32px; font-weight: 700; color: {theme['text']}; letter-spacing: -0.5px; }}
    QLabel#SubHeader {{ font-size: 18px; font-weight: 600; color: {theme['text']}; margin-bottom: 8px; }}
    QLabel#SecondaryText {{ color: {theme['text_sec']}; font-size: 13px; line-height: 1.4; }}
    
    /* Buttons */
    QPushButton {{
        background-color: {theme['input']}; color: {theme['text']};
        border: 1px solid {theme['border']}; padding: 10px 20px; border-radius: 10px; font-weight: 600; font-size: 13px;
    }}
    QPushButton:hover {{ 
        background-color: {theme['border']}; 
        border-color: {theme['text_sec']};
    }}
    QPushButton#PrimaryButton {{ 
        background-color: {theme['accent']}; 
        color: #fff; border: none; 
    }}
    QPushButton#PrimaryButton:hover {{ background-color: #0077e6; }}
    
    QPushButton#DangerButton {{ 
        background-color: rgba(255, 69, 58, 0.15); 
        color: {theme['danger']}; 
        border: 1px solid rgba(255, 69, 58, 0.3);
    }}
    QPushButton#DangerButton:hover {{ background-color: {theme['danger']}; color: #fff; }}

    /* Inputs */
    QLineEdit, QComboBox {{
        background-color: {theme['input']}; 
        border: 1px solid {theme['border']};
        border-radius: 10px; padding: 12px; 
        color: {theme['text']}; font-size: 14px;
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {theme['accent']}; }}
    
    /* Scrollbar */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ border: none; background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {theme['border']}; min-height: 20px; border-radius: 4px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
    """

# =============================================================================
# UI ANIMATIONS & EFFECTS
# =============================================================================

class AnimatedWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim = None

    def enterEvent(self, event):
        self.animate_scale(1.02)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animate_scale(1.0)
        super().leaveEvent(event)

    def animate_scale(self, end_scale):
        # Note: True scale animation via transform is complex in Qt Widgets without OpenGL.
        # We simulate 'focus' effect via border/color or slight geometry shift.
        # For stability, we'll use a subtle opacity/brightness effect instead of geometry to avoid layout jitters.
        pass

# =============================================================================
# WORKER THREADS
# =============================================================================

class VideoThread(QThread):
    change_pixmap = pyqtSignal(np.ndarray)
    def __init__(self, src): super().__init__(); self.src = src; self.running = True
    def run(self):
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
                'ram_p': mem.percent, 'ram_u': mem.used/(1024**3), 'ram_t': mem.total/(1024**3),
                'disk': disk.percent
            })
            time.sleep(2)

# =============================================================================
# MODAL SYSTEM
# =============================================================================

class OverlayDialog(QWidget):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.resize(parent.size())
        self.theme = parent.current_theme
        
        # Blur/Dim Effect
        self.setStyleSheet("background-color: rgba(0, 0, 0, 160);")
        
        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignCenter)
        
        self.container = QFrame()
        self.container.setFixedWidth(520)
        self.container.setStyleSheet(f"""
            QFrame {{ 
                background-color: {self.theme['card']}; 
                border-radius: 20px; 
                border: 1px solid {self.theme['border']};
            }}
        """)
        
        # Shadow
        shadow = QGraphicsDropShadowEffect(self.container)
        shadow.setBlurRadius(40); shadow.setColor(QColor(0,0,0,100)); shadow.setOffset(0, 8)
        self.container.setGraphicsEffect(shadow)
        
        self.inner = QVBoxLayout(self.container)
        self.inner.setContentsMargins(40, 40, 40, 40)
        self.inner.setSpacing(20)
        
        # Header
        lbl = QLabel(title)
        lbl.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {self.theme['text']}; border: none;")
        lbl.setAlignment(Qt.AlignCenter)
        self.inner.addWidget(lbl)
        
        self.layout.addWidget(self.container)
        
        # Entrance Animation
        self.opacity_eff = QGraphicsOpacityEffect(self.container)
        self.container.setGraphicsEffect(self.opacity_eff)
        self.anim = QPropertyAnimation(self.opacity_eff, b"opacity")
        self.anim.setDuration(300)
        self.anim.setStartValue(0); self.anim.setEndValue(1)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.start()
        
        self.show()

    def close_modal(self):
        self.anim.setDirection(QPropertyAnimation.Backward)
        self.anim.finished.connect(self.deleteLater)
        self.anim.start()

# =============================================================================
# CARDS & WIDGETS
# =============================================================================

class CameraWidget(QFrame):
    removed = pyqtSignal(int)
    def __init__(self, idx, theme):
        super().__init__()
        self.setObjectName("Card")
        self.setFixedSize(340, 280)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        
        # Top Bar
        bar = QWidget()
        bar.setFixedHeight(45)
        bar.setStyleSheet("background: transparent; border-bottom: 1px solid rgba(255,255,255,0.05);")
        bh = QHBoxLayout(bar); bh.setContentsMargins(15,0,15,0)
        
        lbl = QLabel(f"Camera {idx}"); lbl.setStyleSheet("font-weight: 600; font-size: 13px;")
        
        btn_close = QPushButton("×")
        btn_close.setFixedSize(28,28)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.close)
        btn_close.setStyleSheet(f"background: transparent; color: {theme['text_sec']}; font-size: 22px; border: none; padding: 0;")
        
        bh.addWidget(lbl); bh.addStretch(); bh.addWidget(btn_close)
        layout.addWidget(bar)
        
        # View
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setStyleSheet("background: black; border-bottom-left-radius: 18px; border-bottom-right-radius: 18px;")
        layout.addWidget(self.view)
        
        self.t = VideoThread(idx)
        self.t.change_pixmap.connect(self.update_img)
        self.t.start()

    def update_img(self, img):
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch*w, QImage.Format_RGB888)
        self.view.setPixmap(QPixmap.fromImage(qimg).scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def close(self): self.t.stop(); self.removed.emit(0); self.deleteLater() # Idx not strictly needed for emit

class DockerWidget(QFrame):
    removed = pyqtSignal(str)
    def __init__(self, name, script, theme):
        super().__init__()
        self.setObjectName("Card")
        self.setFixedSize(340, 200)
        
        l = QVBoxLayout(self); l.setContentsMargins(25,25,25,25); l.setSpacing(15)
        
        # Header
        h = QHBoxLayout()
        icon = QLabel("🐍"); icon.setStyleSheet("font-size: 24px;")
        t = QLabel(name); t.setStyleSheet("font-size: 16px; font-weight: 700;")
        
        btn = QPushButton("×"); btn.setFixedSize(24,24); btn.clicked.connect(lambda: self.removed.emit(name))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"background: transparent; color: {theme['text_sec']}; border: none; font-size: 20px;")
        
        h.addWidget(icon); h.addWidget(t); h.addStretch(); h.addWidget(btn)
        l.addLayout(h)
        
        # Content
        file_n = os.path.basename(script)
        desc = QLabel(f"Bind Mount: {file_n}\nStatus: Ready")
        desc.setObjectName("SecondaryText")
        l.addWidget(desc)
        
        # Action
        act = QPushButton("Run Container"); act.setObjectName("PrimaryButton")
        act.setCursor(Qt.PointingHandCursor)
        act.clicked.connect(lambda: self.run(script))
        l.addWidget(act)

    def run(self, script):
        d = os.path.dirname(script); f = os.path.basename(script)
        cmd = f"gnome-terminal -- docker run -it --rm --runtime nvidia -v \"{d}:/app\" -w /app l4t-pytorch python3 {f}"
        subprocess.Popen(cmd, shell=True)

class AddCard(QFrame):
    clicked = pyqtSignal()
    def __init__(self, theme):
        super().__init__()
        self.setObjectName("AddCard")
        self.setFixedSize(340, 200) # Consistent size
        self.setCursor(Qt.PointingHandCursor)
        
        self.setStyleSheet(f"""
            QFrame#AddCard {{
                background-color: transparent;
                border: 2px dashed {theme['border']};
                border-radius: 18px;
            }}
            QFrame#AddCard:hover {{ border-color: {theme['accent']}; background-color: rgba(255,255,255,0.03); }}
        """)
        
        l = QVBoxLayout(self); l.setAlignment(Qt.AlignCenter)
        icon = QLabel("+"); icon.setStyleSheet(f"font-size: 40px; color: {theme['text_sec']}; font-weight: 300;")
        txt = QLabel("New Item"); txt.setStyleSheet(f"color: {theme['text_sec']}; font-weight: 600; margin-top: 10px;")
        l.addWidget(icon, 0, Qt.AlignCenter); l.addWidget(txt, 0, Qt.AlignCenter)

    def mousePressEvent(self, e): self.clicked.emit()

# =============================================================================
# MAIN WINDOW
# =============================================================================

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1280, 800)
        self.current_theme = Theme.DARK
        self.setStyleSheet(get_stylesheet(self.current_theme))
        
        w = QWidget(); self.setCentralWidget(w)
        main_h = QHBoxLayout(w); main_h.setContentsMargins(0,0,0,0); main_h.setSpacing(0)
        
        # Sidebar
        self.sidebar = QFrame(); self.sidebar.setObjectName("Sidebar"); self.sidebar.setFixedWidth(280)
        sl = QVBoxLayout(self.sidebar); sl.setContentsMargins(25, 45, 25, 25); sl.setSpacing(8)
        
        self.sidebar_header = QLabel("Jetson Studio")
        self.sidebar_header.setStyleSheet(f"font-size: 26px; font-weight: 800; color: {self.current_theme['text']}; margin-bottom: 30px;")
        sl.addWidget(self.sidebar_header)
        
        self.nav_btns = []
        for t, i in [("Cameras", 0), ("Docker Environments", 1), ("Settings", 2)]:
            b = QPushButton(t); b.setObjectName("NavButton"); b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda c, x=i: self.switch_tab(x))
            sl.addWidget(b); self.nav_btns.append(b)
        
        sl.addStretch()
        
        # Sys Info Footer
        foot = QLabel(f"Host: {platform.node()}\nOS: {platform.system()}")
        foot.setStyleSheet(f"color: {self.current_theme['text_sec']}; font-size: 12px;")
        sl.addWidget(foot)
        
        main_h.addWidget(self.sidebar)
        
        # Content
        self.stack = QStackedWidget()
        main_h.addWidget(self.stack)
        
        self.init_pages()
        self.switch_tab(0)

    def init_pages(self):
        # 1. Cameras
        p1 = QWidget(); l1 = QVBoxLayout(p1); l1.setContentsMargins(50,50,50,50)
        l1.addWidget(QLabel("Active Cameras", objectName="Header"))
        l1.addWidget(QLabel("Manage your connected CSI and USB cameras.", objectName="SecondaryText"))
        l1.addSpacing(20)
        
        self.flow_cam = self.create_flow(l1)
        self.add_btn_cam = AddCard(self.current_theme)
        self.add_btn_cam.clicked.connect(self.modal_cam)
        self.flow_cam.addWidget(self.add_btn_cam)
        
        # 2. Docker
        p2 = QWidget(); l2 = QVBoxLayout(p2); l2.setContentsMargins(50,50,50,50)
        l2.addWidget(QLabel("Docker Workspaces", objectName="Header"))
        l2.addWidget(QLabel("Containerized environments with mapped local files.", objectName="SecondaryText"))
        l2.addSpacing(20)
        
        self.flow_dock = self.create_flow(l2)
        self.add_btn_dock = AddCard(self.current_theme)
        self.add_btn_dock.clicked.connect(self.modal_docker)
        self.flow_dock.addWidget(self.add_btn_dock)

        # 3. Settings
        p3 = QWidget(); l3 = QVBoxLayout(p3); l3.setContentsMargins(50,50,50,50); l3.setAlignment(Qt.AlignTop)
        l3.addWidget(QLabel("System & Settings", objectName="Header"))
        l3.addSpacing(30)
        
        # Stats Grid
        sg = QGridLayout(); sg.setSpacing(20)
        self.lbl_cpu = self.stat_card(sg, "CPU Load", "0%", 0)
        self.lbl_ram = self.stat_card(sg, "Memory", "0 GB", 1)
        self.lbl_disk = self.stat_card(sg, "Disk", "0%", 2)
        l3.addLayout(sg)
        
        l3.addSpacing(30)
        l3.addWidget(QLabel("Appearance", objectName="SubHeader"))
        theme_btn = QPushButton("Toggle Dark/Light Mode"); theme_btn.setFixedSize(200, 45); theme_btn.setCursor(Qt.PointingHandCursor)
        # Placeholder for theme logic (restart required for full effect usually in Qt or full stylesheet reload)
        l3.addWidget(theme_btn)
        
        self.stack.addWidget(p1); self.stack.addWidget(p2); self.stack.addWidget(p3)
        
        # Monitor
        self.mon = MonitorThread()
        self.mon.stats_updated.connect(self.update_stats)
        self.mon.start()

    def stat_card(self, grid, title, val, col):
        f = QFrame(); f.setObjectName("Card"); f.setFixedHeight(120)
        l = QVBoxLayout(f); l.setContentsMargins(20,20,20,20)
        l.addWidget(QLabel(title, objectName="SecondaryText"))
        v = QLabel(val); v.setStyleSheet("font-size: 28px; font-weight: 700;")
        l.addWidget(v)
        grid.addWidget(f, 0, col)
        return v

    def update_stats(self, d):
        self.lbl_cpu.setText(f"{d['cpu']}%")
        self.lbl_ram.setText(f"{d['ram_u']:.1f} / {d['ram_t']:.1f} GB")
        self.lbl_disk.setText(f"{d['disk']}%")

    def create_flow(self, layout):
        s = QScrollArea(); s.setWidgetResizable(True)
        w = QWidget(); fl = FlowLayout(w) # Custom flow
        s.setWidget(w); layout.addWidget(s)
        return fl

    def switch_tab(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav_btns): b.setChecked(i==idx)

    # MODALS
    def modal_cam(self):
        m = OverlayDialog(self, "Add Camera")
        
        cb = QComboBox()
        import glob
        devs = glob.glob('/dev/video*')
        if not devs: cb.addItem("No cameras found")
        else: 
            for d in devs: cb.addItem(d, int(d.replace('/dev/video','').strip()))
        
        btn = QPushButton("Connect Camera"); btn.setObjectName("PrimaryButton"); btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.add_camera(m, cb.currentData()))
        
        m.inner.addWidget(QLabel("Select Interface:", objectName="SubHeader"))
        m.inner.addWidget(cb)
        m.inner.addSpacing(10)
        m.inner.addWidget(btn)
        m.inner.addWidget(QPushButton("Cancel", cursor=Qt.PointingHandCursor, clicked=m.close_modal))

    def add_camera(self, m, idx):
        if idx is None: return
        w = CameraWidget(idx, self.current_theme)
        w.removed.connect(lambda: w.deleteLater())
        
        # Insert before add button
        layout = self.flow_cam
        layout.removeWidget(self.add_btn_cam)
        layout.addWidget(w)
        layout.addWidget(self.add_btn_cam)
        m.close_modal()

    def modal_docker(self):
        m = OverlayDialog(self, "New Docker Workspace")
        
        self.sel_file = None
        lbl_f = QLabel("No file selected", styleSheet="color: #666; font-style: italic;")
        
        def pick():
            # FIX: Use DontUseNativeDialog to prevent crashes on some Linux WMs / threads
            f, _ = QFileDialog.getOpenFileName(self, "Select Script", os.path.expanduser("~"), 
                                             "Python (*.py);;All (*)", options=QFileDialog.DontUseNativeDialog)
            if f:
                self.sel_file = f
                lbl_f.setText(os.path.basename(f))
                lbl_f.setStyleSheet(f"color: {self.current_theme['accent']}; font-weight: bold;")
        
        btn_f = QPushButton("Browse Files..."); btn_f.clicked.connect(pick); btn_f.setCursor(Qt.PointingHandCursor)
        
        btn_create = QPushButton("Create Environment"); btn_create.setObjectName("PrimaryButton"); btn_create.setCursor(Qt.PointingHandCursor)
        btn_create.clicked.connect(lambda: self.add_docker(m))
        
        m.inner.addWidget(QLabel("Python Script to Bind:", objectName="SubHeader"))
        
        # File Box
        fbox = QFrame(); fbox.setObjectName("Card"); fl = QHBoxLayout(fbox)
        fl.addWidget(btn_f); fl.addWidget(lbl_f)
        m.inner.addWidget(fbox)
        
        m.inner.addSpacing(10)
        m.inner.addWidget(btn_create)
        m.inner.addWidget(QPushButton("Cancel", cursor=Qt.PointingHandCursor, clicked=m.close_modal))

    def add_docker(self, m):
        if not self.sel_file: return
        w = DockerWidget("Python Env", self.sel_file, self.current_theme)
        w.removed.connect(lambda: w.deleteLater())
        
        layout = self.flow_dock
        layout.removeWidget(self.add_btn_dock)
        layout.addWidget(w)
        layout.addWidget(self.add_btn_dock)
        m.close_modal()

# Simple Flow Layout Implementation
class FlowLayout(QLayout):
    def __init__(self, parent=None): super().__init__(parent); self._items = []
    def __del__(self): pass
    def addItem(self, item): self._items.append(item)
    def count(self): return len(self._items)
    def itemAt(self, index): return self._items[index] if 0 <= index < len(self._items) else None
    def takeAt(self, index): return self._items.pop(index) if 0 <= index < len(self._items) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self.do_layout(QRect(0, 0, width, 0), True)
    def setGeometry(self, rect): super().setGeometry(rect); self.do_layout(rect, False)
    def sizeHint(self): return self.minimumSize()
    def minimumSize(self):
        size = QSize()
        for item in self._items: size = size.expandedTo(item.minimumSize())
        return size + QSize(20, 20)
    def do_layout(self, rect, test):
        x, y, lh, sp = rect.x(), rect.y(), 0, 20
        for item in self._items:
            w_op = item.widget()
            next_x = x + item.sizeHint().width() + sp
            if next_x - sp > rect.right() and lh > 0:
                x = rect.x(); y = y + lh + sp; next_x = x + item.sizeHint().width() + sp; lh = 0
            if not test: item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x; lh = max(lh, item.sizeHint().height())
        return y + lh - rect.y()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())
