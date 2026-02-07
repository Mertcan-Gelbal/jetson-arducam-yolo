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
    QGraphicsBlurEffect, QMenu, QAction, QListView, QCompleter, QTabWidget, QLineEdit
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
        {"name": "L4T PyTorch", "img": "nvcr.io/nvidia/l4t-pytorch:r35.2.1-pth2.0-py3", "desc": "PyTorch 2.0 (JetPack 5.1)"},
        {"name": "L4T ML", "img": "nvcr.io/nvidia/l4t-ml:r35.2.1-py3", "desc": "TensorFlow, PyTorch, JupyterLab"},
        {"name": "DeepStream L4T", "img": "nvcr.io/nvidia/deepstream-l4t:6.2-base", "desc": "DeepStream 6.2 IVA"}
    ],
    "desktop": [
        {"name": "PyTorch (CPU)", "img": "pytorch/pytorch:latest", "desc": "Standard PyTorch"},
        {"name": "TensorFlow", "img": "tensorflow/tensorflow:latest", "desc": "Standard TensorFlow"},
        {"name": "Python 3.9 Slim", "img": "python:3.9-slim", "desc": "Lightweight Python Env"},
    ]
}

def get_recommended_images():
    arch = platform.machine()
    system = platform.system()
    if arch == "aarch64" and system == "Linux":
        return NGC_CATALOG["jetson"], "Jetson (NVIDIA NGC)"
    else:
        return NGC_CATALOG["desktop"], f"{system} ({arch})"

# =============================================================================
#  CUSTOM WIDGETS
# =============================================================================

class ToggleSwitch(QAbstractButton):
    toggled_state = pyqtSignal(bool)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(60, 32)
        self.setCursor(Qt.PointingHandCursor)
        self._thumb_pos = 3.0
        
        self.anim = QPropertyAnimation(self, b"thumbPos")
        self.anim.setDuration(300)
        self.anim.setEasingCurve(QEasingCurve.InOutBack)

    @pyqtProperty(float)
    def thumbPos(self): return self._thumb_pos
    @thumbPos.setter
    def thumbPos(self, pos): self._thumb_pos = pos; self.update()

    def checkStateSet(self):
        start = self._thumb_pos
        end = 31.0 if self.isChecked() else 3.0 
        self.anim.setStartValue(start); self.anim.setEndValue(end); self.anim.start()
        self.toggled_state.emit(self.isChecked()); super().checkStateSet()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        track_col = QColor("#000000") if self.isChecked() else QColor("#4CA1AF") 
        p.setBrush(track_col); p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 16, 16)
        
        font = p.font(); font.setPixelSize(14); p.setFont(font); p.setPen(QColor("white"))
        if self.isChecked(): p.drawText(8, 22, "🌙") 
        else: p.drawText(35, 22, "☀️") 

        p.setBrush(QColor("white"))
        p.drawEllipse(int(self._thumb_pos), 3, 26, 26)

class DonutChart(QWidget):
    def __init__(self, title, color_hex, parent=None):
        super().__init__(parent)
        self.title = title; self.base_color = QColor(color_hex); self.percent = 0
        self.setFixedSize(160, 180)

    def set_value(self, p): self.percent = p; self.update()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() // 2, (self.height() - 20) // 2
        rect = QRect(cx - 50, cy - 50, 100, 100)
        
        p.setPen(QPen(QColor(128, 128, 128, 40), 10, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 0, 360 * 16)
        
        p.setPen(QPen(self.base_color, 10, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 90 * 16, int(-self.percent * 3.6 * 16))
        
        p.setPen(self.parent().palette().text().color())
        font = QFont(); font.setPixelSize(20); font.setBold(True); p.setFont(font)
        p.drawText(rect, Qt.AlignCenter, f"{int(self.percent)}%")
        
        font.setPixelSize(11); font.setBold(False); p.setPen(QColor(128,128,128)); p.setFont(font)
        p.drawText(0, self.height() - 30, self.width(), 30, Qt.AlignCenter, self.title)

# =============================================================================
# THEME ENGINE
# =============================================================================

class ThemeOps:
    @staticmethod
    def get_style(is_dark):
        if is_dark:
            bg = "#0f0f12"; sidebar = "#161618"; card = "#1c1c1e"; text = "#ffffff"
            subtext = "#8e8e93"; border = "#2c2c2e"; input_bg = "#252528"; hover = "rgba(255,255,255,0.05)"
        else:
            bg = "#f5f5f7"; sidebar = "#ffffff"; card = "#ffffff"; text = "#000000"
            subtext = "#6e6e73"; border = "#d1d1d6"; input_bg = "#e9e9eb"; hover = "rgba(0,0,0,0.05)"
        
        return f"""
        QMainWindow {{ background-color: {bg}; }}
        QWidget {{ font-family: '-apple-system', 'Segoe UI', 'Roboto', sans-serif; color: {text}; }}
        QFrame#Sidebar {{ background-color: {sidebar}; border-right: 1px solid {border}; }}
        QPushButton#NavTab {{ border: none; border-radius: 12px; text-align: left; padding: 12px 20px; color: {subtext}; font-weight: 600; font-size: 14px; }}
        QPushButton#NavTab:hover {{ background-color: {hover}; color: {text}; }}
        QPushButton#NavTab:checked {{ background-color: #0A84FF; color: white; }}
        QFrame#Card {{ background-color: {card}; border: 1px solid {border}; border-radius: 18px; }}
        QPushButton#AddBtn {{ border: 2px dashed {border}; border-radius: 18px; background-color: transparent; color: {subtext}; font-size: 40px; }}
        QPushButton#AddBtn:hover {{ border-color: #0A84FF; color: #0A84FF; background-color: rgba(10,132,255, 0.05); }}
        QFrame#ModalBox {{ background-color: {card}; border-radius: 20px; border: 1px solid {border}; }}
        QLabel {{ color: {text}; }}
        QLineEdit, QComboBox {{ background-color: {input_bg}; border: 1px solid {border}; border-radius: 10px; padding: 10px; color: {text}; selection-background-color: #0A84FF; }}
        QComboBox QAbstractItemView {{ background-color: {card}; color: {text}; selection-background-color: #0A84FF; border: 1px solid {border}; }}
        QTabWidget::pane {{ border: none; }}
        QTabWidget::tab-bar {{ left: 5px; }}
        QTabBar::tab {{ background: {input_bg}; color: {subtext}; padding: 8px 16px; border-radius: 16px; margin-right: 8px; font-weight: 600; border: none; }}
        QTabBar::tab:selected {{ background: #0A84FF; color: white; }}
        QPushButton#BtnPrimary {{ background-color: #0A84FF; color: white; border-radius: 10px; padding: 12px; font-weight: bold; border: none; }}
        QPushButton#BtnPrimary:hover {{ background-color: #0071e3; }}
        QPushButton#BtnDanger {{ background-color: rgba(255,69,58,0.1); color: #FF453A; border-radius: 10px; padding: 12px; font-weight: bold; border: 1px solid rgba(255,69,58,0.3); }}
        QPushButton#BtnDanger:hover {{ background-color: #FF453A; color: white; }}
        QPushButton#BtnSecondary {{ background-color: {input_bg}; color: {text}; border-radius: 10px; padding: 12px; border: 1px solid {border}; }}
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
        cap = cv2.VideoCapture(self.src)
        if isinstance(self.src, int) and not cap.isOpened():
            gst = (f"nvarguscamerasrc sensor-id={self.src} ! video/x-raw(memory:NVMM), width=640, height=480, framerate=30/1 ! "
                   "nvvidconv ! video/x-raw, format=BGRx ! videoconvert ! video/x-raw, format=BGR ! appsink drop=1")
            cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
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
            self.updated.emit({
                'cpu': psutil.cpu_percent(),
                'ram': psutil.virtual_memory().percent,
                'disk': psutil.disk_usage('/').percent,
                'gpu': np.random.randint(0, 30) # Sim
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
        
        self.central = QWidget(); self.setCentralWidget(self.central)
        self.main_layout = QHBoxLayout(self.central); self.main_layout.setContentsMargins(0,0,0,0); self.main_layout.setSpacing(0)
        
        self.setup_sidebar()
        self.stack = QStackedWidget(); self.main_layout.addWidget(self.stack)
        
        self.page_cam = self.ui_cameras()
        self.page_doc = self.ui_docker()
        self.page_set = self.ui_settings()
        
        self.stack.addWidget(self.page_cam); self.stack.addWidget(self.page_doc); self.stack.addWidget(self.page_set)
        
        self.blur_effect = QGraphicsBlurEffect(); self.blur_effect.setBlurRadius(0); self.stack.setGraphicsEffect(self.blur_effect)
        self.refresh_theme(); self.th_stats = StatsThread(); self.th_stats.updated.connect(self.update_stats); self.th_stats.start()

    def refresh_theme(self): self.setStyleSheet(ThemeOps.get_style(self.is_dark))

    def setup_sidebar(self):
        self.sidebar = QFrame(); self.sidebar.setObjectName("Sidebar"); self.sidebar.setFixedWidth(260)
        l = QVBoxLayout(self.sidebar); l.setSpacing(10); l.setContentsMargins(20, 50, 20, 20)
        l.addWidget(QLabel("Jetson Studio", styleSheet="font-size: 22px; font-weight: 800; margin-bottom: 20px;"))
        self.nav_btns = []
        for n, i in [("Cameras", 0), ("Docker Environments", 1), ("Settings", 2)]:
            b = QPushButton(n); b.setObjectName("NavTab"); b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor); b.clicked.connect(lambda _, x=i: self.set_page(x))
            self.nav_btns.append(b); l.addWidget(b)
        l.addStretch()
        l.addWidget(QLabel(f"Host: {platform.node()}", styleSheet="color: #888; font-size: 11px;"))
        self.main_layout.addWidget(self.sidebar); self.nav_btns[0].setChecked(True)

    def set_page(self, idx):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(self.nav_btns): b.setChecked(i == idx)

    def ui_cameras(self):
        p = QWidget(); l = QVBoxLayout(p)
        l.addWidget(QLabel("Active Cameras", styleSheet="font-size: 28px; font-weight: bold;"))
        sa = QScrollArea(); sa.setWidgetResizable(True)
        self.f_cam = QWidget(); self.l_cam = FlowLayout(self.f_cam)
        self.btn_add_cam = QPushButton("+"); self.btn_add_cam.setObjectName("AddBtn"); self.btn_add_cam.setFixedSize(300, 220)
        self.btn_add_cam.setCursor(Qt.PointingHandCursor); self.btn_add_cam.clicked.connect(self.open_cam_modal)
        self.l_cam.addWidget(self.btn_add_cam)
        sa.setWidget(self.f_cam); l.addWidget(sa); return p

    def ui_docker(self):
        p = QWidget(); l = QVBoxLayout(p)
        l.addWidget(QLabel("Docker Workspaces", styleSheet="font-size: 28px; font-weight: bold;"))
        sa = QScrollArea(); sa.setWidgetResizable(True)
        self.f_doc = QWidget(); self.l_doc = FlowLayout(self.f_doc)
        self.btn_doc_add = QPushButton("+"); self.btn_doc_add.setObjectName("AddBtn"); self.btn_doc_add.setFixedSize(300, 180)
        self.btn_doc_add.setCursor(Qt.PointingHandCursor); self.btn_doc_add.clicked.connect(self.open_doc_modal)
        self.l_doc.addWidget(self.btn_doc_add)
        sa.setWidget(self.f_doc); l.addWidget(sa); return p

    def ui_settings(self):
        p = QWidget(); l = QVBoxLayout(p)
        l.addWidget(QLabel("System & Settings", styleSheet="font-size: 28px; font-weight: bold; margin-bottom: 30px;"))
        
        # Stats
        h = QHBoxLayout(); h.setSpacing(30); h.setAlignment(Qt.AlignLeft)
        self.ch_cpu = DonutChart("CPU", "#FF3B30"); self.ch_ram = DonutChart("RAM", "#30D158")
        self.ch_dsk = DonutChart("Disk", "#0A84FF"); self.ch_gpu = DonutChart("GPU", "#BF5AF2")
        h.addWidget(self.ch_cpu); h.addWidget(self.ch_ram); h.addWidget(self.ch_dsk); h.addWidget(self.ch_gpu)
        l.addLayout(h); l.addSpacing(40)
        
        # System Info Card
        info_card = QFrame(); info_card.setObjectName("Card"); info_card.setFixedHeight(120)
        il = QGridLayout(info_card); il.setContentsMargins(20,20,20,20)
        
        infos = [
            ("OS System", f"{platform.system()} {platform.release()}"),
            ("Architecture", platform.machine()),
            ("Processor", platform.processor() or "Unknown"),
            ("Python Version", platform.python_version())
        ]
        
        for i, (k, v) in enumerate(infos):
            il.addWidget(QLabel(k, styleSheet="color: #888; font-size: 12px;"), 0, i)
            il.addWidget(QLabel(v, styleSheet="font-weight: bold; font-size: 14px;"), 1, i)
            
        l.addWidget(QLabel("Device Information", styleSheet="font-size: 18px; font-weight: bold; margin-bottom: 10px;"))
        l.addWidget(info_card)
        l.addSpacing(30)

        # Toggle
        row = QHBoxLayout(); row.setAlignment(Qt.AlignLeft)
        l_mode = QLabel("Appearance Mode", styleSheet="font-size: 16px; font-weight: 600; margin-right: 15px;")
        self.tog = ToggleSwitch(); self.tog.setChecked(True); self.tog.toggled_state.connect(self.toggle_mode)
        row.addWidget(l_mode); row.addWidget(self.tog)
        l.addLayout(row); l.addStretch()
        return p

    def update_stats(self, d):
        self.ch_cpu.set_value(d['cpu']); self.ch_ram.set_value(d['ram'])
        self.ch_dsk.set_value(d['disk']); self.ch_gpu.set_value(d['gpu'])
    
    def toggle_mode(self, checked):
        self.is_dark = checked; self.refresh_theme()

    # --- MODALS ---
    def set_blur(self, active): self.blur_effect.setBlurRadius(15 if active else 0)

    def open_cam_modal(self):
        self.set_blur(True)
        o = Overlay(self, "Add Camera Source"); o.closed.connect(lambda: self.set_blur(False))
        tabs = QTabWidget(); tabs.setMinimumHeight(150)
        
        t1 = QWidget(); f1 = QFormLayout(t1); f1.setContentsMargins(10,20,10,10)
        cb_loc = QComboBox()
        import glob; devs = glob.glob('/dev/video*')
        if not devs: cb_loc.addItem("No cameras found")
        else: 
            for d in devs: cb_loc.addItem(d, int(d.replace('/dev/video','').strip()))
        f1.addRow("Device:", cb_loc); tabs.addTab(t1, "Local Device")
        
        t2 = QWidget(); f2 = QFormLayout(t2); f2.setContentsMargins(10,20,10,10)
        le_url = QLineEdit(); le_url.setPlaceholderText("rtsp://..., http://...")
        f2.addRow("Stream URL:", le_url); tabs.addTab(t2, "Stream / Link")
        
        t3 = QWidget(); f3 = QFormLayout(t3); f3.setContentsMargins(10,20,10,10)
        cb_img = QComboBox(); imgs, _ = get_recommended_images()
        for i in imgs: cb_img.addItem(i['name'], i['img'])
        f3.addRow("AI Image:", cb_img)
        le_stream = QLineEdit("http://localhost:5000/video_feed"); f3.addRow("Stream:", le_stream)
        tabs.addTab(t3, "Docker AI")
        
        o.content_layout.addWidget(tabs); o.content_layout.addSpacing(10)
        h = QHBoxLayout()
        b_c = QPushButton("Cancel"); b_c.setObjectName("BtnDanger"); b_c.setCursor(Qt.PointingHandCursor); b_c.clicked.connect(o.close_me)
        b_k = QPushButton("Connect"); b_k.setObjectName("BtnPrimary"); b_k.setCursor(Qt.PointingHandCursor)
        b_k.clicked.connect(lambda: self.connect_cam_logic(o, tabs, cb_loc, le_url, cb_img, le_stream))
        h.addWidget(b_c); h.addSpacing(10); h.addWidget(b_k); o.content_layout.addLayout(h); o.show()

    def connect_cam_logic(self, o, tabs, cb_loc, le_url, cb_img, le_stream):
        idx = tabs.currentIndex(); src = None; label = "Camera"
        if idx == 0: src = cb_loc.currentData(); label = cb_loc.currentText()
        elif idx == 1: src = le_url.text(); label = "Stream Source"
        elif idx == 2: src = le_stream.text(); label = f"AI Feed ({cb_img.currentText()})"
        if src is not None:
            w = CardWidget(label, "Connecting..."); w.removed.connect(lambda: w.deleteLater())
            self.l_cam.removeWidget(self.btn_add_cam); self.l_cam.addWidget(w); self.l_cam.addWidget(self.btn_add_cam)
            w.t = VideoThread(src); w.t.change_pixmap.connect(w.upd_img); w.t.start()
        o.close_me()

    def open_doc_modal(self):
        self.set_blur(True)
        o = Overlay(self, "New Workspace"); o.closed.connect(lambda: self.set_blur(False))
        
        images, sys_name = get_recommended_images()
        o.form.addRow(QLabel(f"System: {sys_name}", styleSheet="color:#0A84FF; font-weight:bold;"))
        cb = QComboBox()
        for i in images: cb.addItem(f"{i['name']}", i['img'])
        o.form.addRow("Environment:", cb)
        
        self.sel_file = "No selection"; lbl_f = QLabel(self.sel_file, styleSheet="color:#888; font-style:italic;")
        def pick_f():
            m = QMenu(self)
            m.addAction("Select File...", lambda: self.set_f(QFileDialog.getOpenFileName(self)[0], lbl_f))
            m.addAction("Select Folder...", lambda: self.set_f(QFileDialog.getExistingDirectory(self), lbl_f))
            m.exec_(QCursor.pos())
        btn_pick = QPushButton("Browse..."); btn_pick.setObjectName("BtnSecondary"); btn_pick.setCursor(Qt.PointingHandCursor); btn_pick.clicked.connect(pick_f)
        o.form.addRow("Bind Mount:", QHBoxLayout()); o.form.itemAt(2, QFormLayout.FieldRole).layout().addWidget(btn_pick); o.form.itemAt(2, QFormLayout.FieldRole).layout().addWidget(lbl_f)
        
        h = QHBoxLayout()
        b_c = QPushButton("Cancel"); b_c.setObjectName("BtnDanger"); b_c.setCursor(Qt.PointingHandCursor); b_c.clicked.connect(o.close_me)
        b_k = QPushButton("Create"); b_k.setObjectName("BtnPrimary"); b_k.setCursor(Qt.PointingHandCursor)
        b_k.clicked.connect(lambda: self.create_dock(o, cb.currentData(), self.sel_file))
        h.addWidget(b_c); h.addSpacing(10); h.addWidget(b_k); o.content_layout.addLayout(h); o.show()

    def set_f(self, path, lbl):
        if path: self.sel_file = path; lbl.setText(os.path.basename(path)); lbl.setStyleSheet("color:#0A84FF; font-weight:bold;")

    def create_dock(self, o, img, path):
        w = CardWidget(img.split(":")[0], f"Bind: {os.path.basename(path)}"); w.removed.connect(lambda: w.deleteLater())
        self.l_doc.removeWidget(self.btn_doc_add); self.l_doc.addWidget(w); self.l_doc.addWidget(self.btn_doc_add)
        o.close_me()

# =============================================================================
# HELPERS
# =============================================================================

class Overlay(QWidget):
    closed = pyqtSignal()
    def __init__(self, parent, title):
        super().__init__(parent)
        self.resize(parent.size()); self.setStyleSheet("background-color: rgba(0,0,0,0.6);")
        self.layout = QVBoxLayout(self); self.layout.setAlignment(Qt.AlignCenter)
        self.box = QFrame(); self.box.setObjectName("ModalBox"); self.box.setFixedWidth(450)
        self.layout.addWidget(self.box)
        self.content_layout = QVBoxLayout(self.box); self.content_layout.setContentsMargins(25,25,25,25); self.content_layout.setSpacing(15)
        t = QLabel(title); t.setStyleSheet("font-size: 18px; font-weight: bold; margin-bottom: 5px;"); t.setAlignment(Qt.AlignCenter)
        self.content_layout.addWidget(t)
        self.form = QFormLayout(); self.form.setVerticalSpacing(15); self.content_layout.addLayout(self.form)
        eff = QGraphicsDropShadowEffect(self.box); eff.setBlurRadius(50); eff.setColor(QColor(0,0,0,150)); self.box.setGraphicsEffect(eff)
    def close_me(self): self.closed.emit(); self.deleteLater()

class CardWidget(QFrame):
    removed = pyqtSignal()
    def __init__(self, title, sub):
        super().__init__()
        self.setFixedSize(300, 220); self.setObjectName("Card")
        l = QVBoxLayout(self); l.setContentsMargins(0,0,0,0)
        h = QFrame(); h.setFixedHeight(45); h.setStyleSheet("border-bottom: 1px solid #ddd; background: rgba(127,127,127,0.1);")
        hl = QHBoxLayout(h); hl.setContentsMargins(15,0,15,0)
        hl.addWidget(QLabel(title, styleSheet="font-weight: bold;"))
        x = QPushButton("×"); x.setFixedSize(24,24); x.clicked.connect(self.removed.emit); x.setStyleSheet("border:none; color:#888; font-size:20px;")
        hl.addWidget(x); l.addWidget(h)
        self.view = QLabel(sub); self.view.setAlignment(Qt.AlignCenter); self.view.setStyleSheet("background: black; border-bottom-left-radius: 16px; border-bottom-right-radius: 16px; color: white;")
        l.addWidget(self.view)
    def upd_img(self, img):
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB); h,w,c = img.shape
        self.view.setPixmap(QPixmap.fromImage(QImage(img.data, w, h, c*w, QImage.Format_RGB888)).scaled(self.view.size(), Qt.KeepAspectRatio))

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
            if next_x - sp > r.right() and lh > 0: x, y, lh = r.x(), y + lh + sp, 0; next_x = x + it.sizeHint().width() + sp
            if not t: it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x, lh = next_x, max(lh, it.sizeHint().height())
        return y + lh - r.y()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec_())
