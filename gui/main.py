#!/usr/bin/env python3
"""
Jetson Arducam AI Kit - Modern Professional Application
"""
import sys
import os
import psutil
import subprocess
import cv2
import time
import numpy as np

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QStackedWidget, QFrame,
    QScrollArea, QGridLayout, QTextEdit, QMessageBox, QComboBox,
    QSizePolicy, QLayout, QInputDialog, QStyle, QSizeGrip, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize, QPoint, QRect, QEvent

# =============================================================================
# STYLES & ASSETS
# =============================================================================

GLOBAL_STYLES = """
/* Main Application */
QMainWindow { background-color: #121212; color: #E0E0E0; }

/* Scrollbars - Minimal/Hidden look */
QScrollBar:vertical {
    border: none;
    background: #1e1e1e;
    width: 8px;
    border-radius: 4px;
}
QScrollBar::handle:vertical {
    background: #333;
    min-height: 20px;
    border-radius: 4px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    border: none;
    background: none;
}

/* Cards */
QFrame#Card {
    background-color: #1e1e1e;
    border: 1px solid #333;
    border-radius: 12px;
}
QFrame#Card:hover {
    border-color: #76b900;
}

/* Buttons */
QPushButton {
    background-color: #2c2c2c;
    color: white;
    border: none;
    padding: 8px 16px;
    border-radius: 6px;
    font-weight: 600;
}
QPushButton:hover { background-color: #3d3d3d; }
QPushButton#PrimaryButton { background-color: #76b900; color: #000; }
QPushButton#PrimaryButton:hover { background-color: #6a9e00; }
QPushButton#DangerButton { background-color: #cf6679; color: #000; }

/* Add Card */
QFrame#AddCard {
    background-color: rgba(255, 255, 255, 0.05);
    border: 2px dashed #444;
    border-radius: 12px;
}
QFrame#AddCard:hover {
    background-color: rgba(255, 255, 255, 0.08);
    border-color: #76b900;
}
"""

# =============================================================================
# WORKER THREADS
# =============================================================================

class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(np.ndarray)
    
    def __init__(self, sensor_id=0):
        super().__init__()
        self._run_flag = True
        self.sensor_id = sensor_id
        # Optimized GStreamer Pipeline
        self.gst_pipeline = (
            f"nvarguscamerasrc sensor-id={sensor_id} ! "
            "video/x-raw(memory:NVMM), width=(int)1280, height=(int)720, format=(string)NV12, framerate=(fraction)30/1 ! "
            "nvvidconv ! video/x-raw, format=(string)BGRx ! "
            "videoconvert ! video/x-raw, format=(string)BGR ! appsink"
        )

    def run(self):
        cap = cv2.VideoCapture(self.gst_pipeline, cv2.CAP_GSTREAMER)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.sensor_id)
        
        while self._run_flag:
            ret, cv_img = cap.read()
            if ret:
                self.change_pixmap_signal.emit(cv_img)
            time.sleep(0.033) # ~30 FPS
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()

class SystemMonitorThread(QThread):
    update_signal = pyqtSignal(dict)
    def run(self):
        while True:
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent()
            self.update_signal.emit({'ram': mem.percent, 'cpu': cpu})
            time.sleep(2)

class CommandRunnerThread(QThread):
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    def __init__(self, cmd, cwd):
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
    def run(self):
        p = subprocess.Popen(self.cmd, cwd=self.cwd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            self.output_signal.emit(line.strip())
        self.finished_signal.emit(p.wait())

# =============================================================================
# CUSTOM UI COMPONENTS (MODAL, RESIZABLE, FLOW)
# =============================================================================

class OverlayDialog(QWidget):
    """
    A full-screen overlay modal that dims the background and centers content.
    """
    def __init__(self, parent=None, title="Select Option"):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        # Full coverage
        self.resize(parent.size())
        
        # Dimmed Background
        self.setStyleSheet("background-color: rgba(0, 0, 0, 200);")
        
        # Main Layout
        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignCenter)
        
        # Content Box
        self.content_frame = QFrame()
        self.content_frame.setStyleSheet("""
            QFrame {
                background-color: #1e1e1e;
                border: 1px solid #333;
                border-radius: 16px;
            }
            QLabel { color: white; background: transparent; }
            QPushButton { margin: 5px; }
        """)
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(20); shadow.setColor(QColor(0,0,0,150)); shadow.setOffset(0, 4)
        self.content_frame.setGraphicsEffect(shadow)
        self.content_frame.setFixedSize(500, 350)
        
        # Inner Layout
        self.inner_layout = QVBoxLayout(self.content_frame)
        self.inner_layout.setSpacing(15)
        self.inner_layout.setContentsMargins(30, 30, 30, 30)
        
        # Header
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet("font-size: 22px; font-weight: bold; color: #76b900;")
        lbl_title.setAlignment(Qt.AlignCenter)
        self.inner_layout.addWidget(lbl_title)
        
        # Dynamic Content Area
        self.dynamic_area = QVBoxLayout()
        self.inner_layout.addLayout(self.dynamic_area)
        self.inner_layout.addStretch()
        
        # Cancel Button logic handled by specific implementations or clicked outside (optional)
        
        self.layout.addWidget(self.content_frame)
        self.hide() # Hidden by default

    def close_modal(self):
        self.hide()
        self.deleteLater()

class ResizableCard(QFrame):
    """Base class for any card that needs to be resizable by the user."""
    def __init__(self, parent=None, min_w=300, min_h=300):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumSize(min_w, min_h)
        
        # Add resize grip to bottom-right
        self.grip = QSizeGrip(self)
        self.grip.resize(16, 16)
        # Position grip at bottom right
        self.grip.setStyleSheet("background: transparent;")
    
    def resizeEvent(self, event):
        rect = self.rect()
        self.grip.move(rect.right() - 16, rect.bottom() - 16)
        super().resizeEvent(event)

class AddButtonWidget(QFrame):
    clicked = pyqtSignal()
    def __init__(self):
        super().__init__()
        self.setObjectName("AddCard")
        self.setFixedSize(300, 300)
        self.setCursor(Qt.PointingHandCursor)
        
        layout = QVBoxLayout(self)
        lbl = QLabel("+")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("font-size: 80px; font-weight: 100; color: #555; background: transparent;")
        layout.addWidget(lbl)
    
    def mousePressEvent(self, event):
        self.clicked.emit()

class FlowLayout(QLayout):
    """Reflowing grid layout."""
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        if parent: self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.itemList = []
    def __del__(self):
        while self.itemList: self.takeAt(0)
    def addItem(self, item): self.itemList.append(item)
    def count(self): return len(self.itemList)
    def itemAt(self, i): return self.itemList[i] if 0 <= i < len(self.itemList) else None
    def takeAt(self, i): return self.itemList.pop(i) if 0 <= i < len(self.itemList) else None
    def expandingDirections(self): return Qt.Orientations(Qt.Orientation(0))
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self.doLayout(QRect(0,0,width,0), True)
    def setGeometry(self, rect): super().setGeometry(rect); self.doLayout(rect, False)
    def sizeHint(self): return self.minimumSize()
    def minimumSize(self):
        s = QSize()
        for i in self.itemList: s = s.expandedTo(i.minimumSize())
        m = self.contentsMargins()
        return s + QSize(2*m.top(), 2*m.top())
    def doLayout(self, rect, test):
        x, y, lh = rect.x(), rect.y(), 0
        sp = self.spacing()
        for item in self.itemList:
            w = item.widget()
            nextX = x + item.sizeHint().width() + sp
            if nextX - sp > rect.right() and lh > 0:
                x = rect.x()
                y = y + lh + sp
                nextX = x + item.sizeHint().width() + sp
                lh = 0
            if not test: item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = nextX
            lh = max(lh, item.sizeHint().height())
        return y + lh - rect.y()

# =============================================================================
# WIDGET IMPLS
# =============================================================================

class CameraWidget(ResizableCard):
    removed = pyqtSignal(int)
    def __init__(self, sensor_id, name):
        super().__init__(min_w=320, min_h=240) # Aspect 4:3 default
        self.sensor_id = sensor_id
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        
        # Absolute Pos Close Button
        self.btn_close = QPushButton("×", self)
        self.btn_close.clicked.connect(self.close)
        self.btn_close.setFixedSize(30, 30)
        self.btn_close.move(self.width()-35, 5)
        self.btn_close.setStyleSheet("background: rgba(0,0,0,0.5); border-radius: 15px; color: white; font-size: 20px; padding:0;")
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.raise_()
        
        # Video Label
        self.video = QLabel("Loading...")
        self.video.setAlignment(Qt.AlignCenter)
        self.video.setStyleSheet("background: black; border-radius: 12px;")
        layout.addWidget(self.video)
        
        self.thread = VideoThread(sensor_id)
        self.thread.change_pixmap_signal.connect(self.update_image)
        self.thread.start()
        
    def update_image(self, img):
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        q_img = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pix = QPixmap.fromImage(q_img).scaled(self.video.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.video.setPixmap(pix)
        
    def close(self):
        self.thread.stop()
        self.removed.emit(self.sensor_id)
        self.deleteLater()
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.btn_close.move(self.width()-35, 5)

class DockerWidget(ResizableCard):
    removed = pyqtSignal(str)
    def __init__(self, env_name, config):
        super().__init__(min_w=300, min_h=200)
        self.env_name = env_name
        
        layout = QVBoxLayout(self)
        
        # Header
        h_layout = QHBoxLayout()
        title = QLabel(env_name)
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #76b900; background: transparent;")
        btn_close = QPushButton("×")
        btn_close.setFixedSize(24, 24)
        btn_close.clicked.connect(lambda: self.removed.emit(env_name))
        btn_close.setStyleSheet("background: transparent; padding: 0;")
        h_layout.addWidget(title)
        h_layout.addStretch()
        h_layout.addWidget(btn_close)
        layout.addLayout(h_layout)
        
        # Details
        details = QLabel(f"Base: {config['base']}\nPyTorch: {config['torch']}\nCUDA: {config['cuda']}")
        details.setStyleSheet("color: #aaa; font-size: 13px; background: transparent;")
        details.setAlignment(Qt.AlignTop)
        layout.addWidget(details)
        
        # Controls
        ctrl = QHBoxLayout()
        btn_start = QPushButton("Start"); btn_start.setObjectName("PrimaryButton")
        btn_stop = QPushButton("Stop"); btn_stop.setObjectName("DangerButton")
        ctrl.addWidget(btn_start)
        ctrl.addWidget(btn_stop)
        layout.addLayout(ctrl)

# =============================================================================
# MAIN APP
# =============================================================================

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jetson AI Studio")
        self.resize(1300, 850)
        self.setStyleSheet(GLOBAL_STYLES)
        
        # Data
        self.active_cams = set()
        self.active_dockers = set()
        
        # Layouts
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(0); main_layout.setContentsMargins(0,0,0,0)
        
        self.setup_sidebar(main_layout)
        
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(20,20,20,20)
        main_layout.addWidget(self.stack)
        
        # Pages
        self.page_cams = self.create_camera_page()
        self.page_docker = self.create_docker_page()
        
        self.stack.addWidget(self.page_cams)
        self.stack.addWidget(self.page_docker)
        
    def setup_sidebar(self, layout):
        sidebar = QFrame()
        sidebar.setStyleSheet("background: #181818; border-right: 1px solid #333;")
        sidebar.setFixedWidth(240)
        l = QVBoxLayout(sidebar)
        l.setSpacing(10)
        
        lbl = QLabel("Jetson Studio")
        lbl.setStyleSheet("font-size: 24px; font-weight: bold; color: white; padding: 20px 0;")
        lbl.setAlignment(Qt.AlignCenter)
        l.addWidget(lbl)
        
        btn_cam = QPushButton("Cameras"); btn_cam.setCheckable(True); btn_cam.setChecked(True)
        btn_docker = QPushButton("Docker Environments"); btn_docker.setCheckable(True)
        
        btn_cam.clicked.connect(lambda: self.switch(0, [btn_cam, btn_docker]))
        btn_docker.clicked.connect(lambda: self.switch(1, [btn_cam, btn_docker]))
        
        l.addWidget(btn_cam)
        l.addWidget(btn_docker)
        l.addStretch()
        layout.addWidget(sidebar)
        
    def switch(self, idx, btns):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate(btns): b.setChecked(i == idx)

    # -------------------------------------------------------------------------
    # CAMERA PAGE
    # -------------------------------------------------------------------------
    def create_camera_page(self):
        page = QWidget()
        l = QVBoxLayout(page)
        
        header = QLabel("Active Cameras")
        header.setStyleSheet("font-size: 28px; font-weight: bold; margin-bottom: 20px;")
        l.addWidget(header)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        
        self.cam_container = QWidget()
        self.cam_flow = FlowLayout(self.cam_container, margin=0, spacing=20)
        
        # Add Button
        self.btn_add_cam = AddButtonWidget()
        self.btn_add_cam.clicked.connect(self.show_cam_modal)
        self.cam_flow.addWidget(self.btn_add_cam)
        
        self.cam_container.setLayout(self.cam_flow)
        scroll.setWidget(self.cam_container)
        l.addWidget(scroll)
        return page

    def show_cam_modal(self):
        # Create Overlay Modal
        self.modal = OverlayDialog(self, "Add New Camera")
        
        # Detect Cams
        import glob
        cams = glob.glob('/dev/video*')
        
        combo = QComboBox()
        combo.setStyleSheet("padding: 10px; background: #333; color: white; border-radius: 6px;")
        if not cams: combo.addItem("No cameras detected")
        else:
            for c in cams: 
                idx = int(c.replace('/dev/video', ''))
                if idx not in self.active_cams:
                    combo.addItem(f"Camera {idx} ({c})", idx)
        
        btn_add = QPushButton("Add to Grid")
        btn_add.setObjectName("PrimaryButton")
        btn_add.clicked.connect(lambda: self.add_cam(combo.currentData()))
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.modal.close_modal)
        
        self.modal.dynamic_area.addWidget(QLabel("Select Device:"))
        self.modal.dynamic_area.addWidget(combo)
        self.modal.dynamic_area.addSpacing(20)
        self.modal.dynamic_area.addWidget(btn_add)
        self.modal.dynamic_area.addWidget(btn_cancel)
        
        self.modal.show()

    def add_cam(self, idx):
        if idx is None: return
        self.modal.close_modal()
        
        w = CameraWidget(idx, f"Cam {idx}")
        w.removed.connect(self.remove_cam)
        
        self.cam_flow.removeWidget(self.btn_add_cam)
        self.btn_add_cam.setParent(None)
        self.cam_flow.addWidget(w)
        self.cam_flow.addWidget(self.btn_add_cam)
        self.active_cams.add(idx)

    def remove_cam(self, idx):
        self.active_cams.discard(idx)

    # -------------------------------------------------------------------------
    # DOCKER PAGE
    # -------------------------------------------------------------------------
    def create_docker_page(self):
        page = QWidget()
        l = QVBoxLayout(page)
        
        header = QLabel("Docker Workspaces")
        header.setStyleSheet("font-size: 28px; font-weight: bold; margin-bottom: 20px;")
        l.addWidget(header)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        
        self.docker_container = QWidget()
        self.docker_flow = FlowLayout(self.docker_container, margin=0, spacing=20)
        
        self.btn_add_docker = AddButtonWidget()
        self.btn_add_docker.clicked.connect(self.show_docker_modal)
        self.docker_flow.addWidget(self.btn_add_docker)
        
        self.docker_container.setLayout(self.docker_flow)
        scroll.setWidget(self.docker_container)
        l.addWidget(scroll)
        return page

    def show_docker_modal(self):
        self.dock_modal = OverlayDialog(self, "Create Environment")
        
        # Options
        envs = {
            "YOLOv8 Inference": {"base": "l4t-pytorch", "torch": "2.1", "cuda": "11.4"},
            "Data Collection": {"base": "l4t-base", "torch": "N/A", "cuda": "11.4"},
            "Training Suite": {"base": "l4t-ml", "torch": "1.13", "cuda": "11.4"}
        }
        
        self.dock_combo = QComboBox()
        self.dock_combo.setStyleSheet("padding: 10px; background: #333; color: white; border-radius: 6px;")
        for k in envs.keys(): self.dock_combo.addItem(k, envs[k])
        
        btn_create = QPushButton("Create Workspace")
        btn_create.setObjectName("PrimaryButton")
        btn_create.clicked.connect(lambda: self.add_docker(self.dock_combo.currentText(), self.dock_combo.currentData()))
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.dock_modal.close_modal)
        
        self.dock_modal.dynamic_area.addWidget(QLabel("Environment Template:"))
        self.dock_modal.dynamic_area.addWidget(self.dock_combo)
        self.dock_modal.dynamic_area.addSpacing(20)
        self.dock_modal.dynamic_area.addWidget(btn_create)
        self.dock_modal.dynamic_area.addWidget(btn_cancel)
        
        self.dock_modal.show()

    def add_docker(self, name, config):
        self.dock_modal.close_modal()
        
        w = DockerWidget(name, config)
        w.removed.connect(self.remove_docker)
        
        self.docker_flow.removeWidget(self.btn_add_docker)
        self.btn_add_docker.setParent(None)
        
        self.docker_flow.addWidget(w)
        self.docker_flow.addWidget(self.btn_add_docker)
        self.active_dockers.add(name)

    def remove_docker(self, name): # Logic handled by deleteLater in signal
        if name in self.active_dockers: self.active_dockers.discard(name)
    
    # Resize Overlay on Window Resize
    def resizeEvent(self, event):
        for child in self.children():
            if isinstance(child, OverlayDialog) and child.isVisible():
                child.resize(self.size())
        super().resizeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    from PyQt5.QtGui import QColor # Ensure availability
    window = App()
    window.show()
    sys.exit(app.exec_())
