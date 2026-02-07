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

    # ... (ThemeOps remains same) ...

# =============================================================================
#  CUSTOM WIDGETS (FIXED TOGGLE)
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
        self.anim.setEasingCurve(QEasingCurve.InOutBack) # Bouncy effect

    @pyqtProperty(float)
    def thumbPos(self): return self._thumb_pos
    @thumbPos.setter
    def thumbPos(self, pos): self._thumb_pos = pos; self.update()

    def checkStateSet(self):
        start = self._thumb_pos
        # If checked (Dark), thumb goes right. If unchecked (Light), left.
        end = 31.0 if self.isChecked() else 3.0 
        self.anim.setStartValue(start); self.anim.setEndValue(end); self.anim.start()
        self.toggled_state.emit(self.isChecked()); super().checkStateSet()

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        
        # Track
        # Dark Mode (Checked) -> Black track. Light Mode -> Light Blue track
        track_col = QColor("#000000") if self.isChecked() else QColor("#4CA1AF") 
        p.setBrush(track_col); p.setPen(Qt.NoPen)
        p.drawRoundedRect(0, 0, self.width(), self.height(), 16, 16)
        
        # Icons
        font = p.font(); font.setPixelSize(14); p.setFont(font); p.setPen(QColor("white"))
        if self.isChecked(): p.drawText(8, 22, "🌙") # Moon visible on left when thumb is right
        else: p.drawText(35, 22, "☀️") # Sun visible on right when thumb is left

        # Thumb
        p.setBrush(QColor("white"))
        p.drawEllipse(int(self._thumb_pos), 3, 26, 26)

# ... (DonutChart remains same) ...

    # --- MODALS (FIXED ALIGNMENT) ---
    def set_blur(self, active): self.blur_effect.setBlurRadius(15 if active else 0)

    def open_cam_modal(self):
        self.set_blur(True)
        o = Overlay(self, "Add Camera Source"); o.closed.connect(lambda: self.set_blur(False))
        
        # CONTENT
        tabs = QTabWidget()
        tabs.setMinimumHeight(150) # Auto expand but min size
        
        # Tab 1: Local
        t1 = QWidget(); f1 = QFormLayout(t1); f1.setContentsMargins(10,20,10,10)
        cb_loc = QComboBox()
        import glob
        devs = glob.glob('/dev/video*')
        if not devs: cb_loc.addItem("No cameras found")
        else: 
            for d in devs: cb_loc.addItem(d, int(d.replace('/dev/video','').strip()))
        f1.addRow("Device:", cb_loc)
        tabs.addTab(t1, "Local Device")
        
        # Tab 2: Network / File
        t2 = QWidget(); f2 = QFormLayout(t2); f2.setContentsMargins(10,20,10,10)
        le_url = QLineEdit(); le_url.setPlaceholderText("rtsp://..., http://..., or /video.mp4")
        f2.addRow("Stream URL:", le_url)
        tabs.addTab(t2, "Stream / Link")
        
        # Tab 3: Docker AI Project
        t3 = QWidget(); f3 = QFormLayout(t3); f3.setContentsMargins(10,20,10,10)
        cb_img = QComboBox(); imgs, _ = get_recommended_images()
        for i in imgs: cb_img.addItem(i['name'], i['img'])
        f3.addRow("AI Image:", cb_img)
        le_stream = QLineEdit("http://localhost:5000/video_feed"); f3.addRow("Stream:", le_stream)
        tabs.addTab(t3, "Docker AI")
        
        o.content_layout.addWidget(tabs)
        
        # Spacer to push buttons down if needed, or keep compact
        o.content_layout.addSpacing(10)

        # Actions
        h = QHBoxLayout()
        b_c = QPushButton("Cancel"); b_c.setObjectName("BtnDanger"); b_c.setCursor(Qt.PointingHandCursor)
        b_c.clicked.connect(o.close_me)
        
        b_k = QPushButton("Connect"); b_k.setObjectName("BtnPrimary"); b_k.setCursor(Qt.PointingHandCursor)
        b_k.clicked.connect(lambda: self.connect_cam_logic(o, tabs, cb_loc, le_url, cb_img, le_stream))
        
        h.addWidget(b_c); h.addSpacing(10); h.addWidget(b_k)
        o.content_layout.addLayout(h)
        o.show()


    def connect_cam_logic(self, o, tabs, cb_loc, le_url, cb_img, le_stream):
        idx = tabs.currentIndex()
        src = None
        label = "Camera"
        
        if idx == 0: 
            src = cb_loc.currentData()
            label = cb_loc.currentText()
        elif idx == 1: 
            src = le_url.text()
            label = "Stream Source"
        elif idx == 2:
            img = cb_img.currentData()
            src = le_stream.text()
            label = f"AI Feed ({cb_img.currentText()})"
            
        if src is not None:
            w = CardWidget(label, "Connecting..."); w.removed.connect(lambda: w.deleteLater())
            self.l_cam.removeWidget(self.btn_add_cam)
            self.l_cam.addWidget(w); self.l_cam.addWidget(self.btn_add_cam)
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
        
        self.sel_file = "No selection"
        lbl_f = QLabel(self.sel_file, styleSheet="color:#888; font-style:italic;")
        
        def pick_f():
            m = QMenu(self)
            m.addAction("Select File...", lambda: self.set_f(QFileDialog.getOpenFileName(self)[0], lbl_f))
            m.addAction("Select Folder...", lambda: self.set_f(QFileDialog.getExistingDirectory(self), lbl_f))
            m.exec_(QCursor.pos())

        btn_pick = QPushButton("Browse..."); btn_pick.setObjectName("BtnSecondary"); btn_pick.setCursor(Qt.PointingHandCursor)
        btn_pick.clicked.connect(pick_f)
        o.form.addRow("Bind Mount:", QHBoxLayout())
        o.form.itemAt(2, QFormLayout.FieldRole).layout().addWidget(btn_pick)
        o.form.itemAt(2, QFormLayout.FieldRole).layout().addWidget(lbl_f)
        
        h = QHBoxLayout()
        b_c = QPushButton("Cancel"); b_c.setObjectName("BtnDanger"); b_c.setCursor(Qt.PointingHandCursor); b_c.clicked.connect(o.close_me)
        b_k = QPushButton("Create"); b_k.setObjectName("BtnPrimary"); b_k.setCursor(Qt.PointingHandCursor)
        b_k.clicked.connect(lambda: self.create_dock(o, cb.currentData(), self.sel_file))
        
        h.addWidget(b_c); h.addSpacing(10); h.addWidget(b_k)
        o.content_layout.addLayout(h)
        o.show()

    def set_f(self, path, lbl):
        if path: self.sel_file = path; lbl.setText(os.path.basename(path)); lbl.setStyleSheet("color:#0A84FF; font-weight:bold;")

    def create_dock(self, o, img, path):
        w = CardWidget(img.split(":")[0], f"Bind: {os.path.basename(path)}"); w.removed.connect(lambda: w.deleteLater())
        self.l_doc.removeWidget(self.btn_doc_add)
        self.l_doc.addWidget(w); self.l_doc.addWidget(self.btn_doc_add)
        if path != "No selection":
            d = path if os.path.isdir(path) else os.path.dirname(path)
            cmd = f"gnome-terminal -- docker run -it --rm -v \"{d}:/app\" -w /app {img} /bin/bash"
            subprocess.Popen(cmd, shell=True)
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
