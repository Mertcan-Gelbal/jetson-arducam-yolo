#!/usr/bin/env python3
"""
Jetson Arducam AI Kit - Professional Desktop Application
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
    QSizePolicy, QLayout, QInputDialog, QStyle
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize, QPoint, QRect, QPropertyAnimation, QEasingCurve
from PyQt5.QtGui import QFont, QIcon, QImage, QPixmap, QColor

# =============================================================================
# WORKER THREADS (Non-Blocking Operations)
# =============================================================================

class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(np.ndarray)
    
    def __init__(self, sensor_id=0):
        super().__init__()
        self._run_flag = True
        self.sensor_id = sensor_id
        
        # GStreamer Pipeline for Jetson Hardware Acceleration (NVARGUS)
        self.gst_pipeline = (
            f"nvarguscamerasrc sensor-id={sensor_id} ! "
            "video/x-raw(memory:NVMM), width=(int)1280, height=(int)720, format=(string)NV12, framerate=(fraction)30/1 ! "
            "nvvidconv ! video/x-raw, format=(string)BGRx ! "
            "videoconvert ! video/x-raw, format=(string)BGR ! appsink"
        )

    def run(self):
        # Optimized capture
        cap = cv2.VideoCapture(self.gst_pipeline, cv2.CAP_GSTREAMER)
        
        # Fallback to standard V4L2 if GStreamer fails
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.sensor_id)
        
        while self._run_flag:
            ret, cv_img = cap.read()
            if ret:
                self.change_pixmap_signal.emit(cv_img)
            time.sleep(0.016)  # ~60 FPS cap
            
        cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()


class SystemMonitorThread(QThread):
    update_signal = pyqtSignal(dict)
    
    def run(self):
        while True:
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disk = psutil.disk_usage('/')
            cpu_percent = psutil.cpu_percent(interval=None)
            
            self.update_signal.emit({
                'ram_used': mem.used / (1024**3),
                'ram_total': mem.total / (1024**3),
                'ram_percent': mem.percent,
                'swap_percent': swap.percent,
                'disk_percent': disk.percent,
                'cpu_percent': cpu_percent
            })
            time.sleep(2)


class CommandRunnerThread(QThread):
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)
    
    def __init__(self, command, cwd=None):
        super().__init__()
        self.command = command
        self.cwd = cwd
        
    def run(self):
        process = subprocess.Popen(
            self.command, cwd=self.cwd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            shell=True, text=True, bufsize=1
        )
        for line in iter(process.stdout.readline, ''):
            self.output_signal.emit(line.strip())
        process.stdout.close()
        ret = process.wait()
        self.finished_signal.emit(ret)

# =============================================================================
# CUSTOM LAYOUTS & WIDGETS
# =============================================================================

class FlowLayout(QLayout):
    """Standard Qt Flow Layout to arrange widgets in a grid that wraps."""
    def __init__(self, parent=None, margin=0, spacing=-1):
        super(FlowLayout, self).__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self.itemList = []

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item):
        self.itemList.append(item)

    def count(self):
        return len(self.itemList)

    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        height = self.doLayout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        super(FlowLayout, self).setGeometry(rect)
        self.doLayout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.itemList:
            size = size.expandedTo(item.minimumSize())
        size += QSize(2 * self.contentsMargins().top(), 2 * self.contentsMargins().top())
        return size

    def doLayout(self, rect, testOnly):
        x, y = rect.x(), rect.y()
        lineHeight = 0
        spacing = self.spacing()
        
        for item in self.itemList:
            wid = item.widget()
            spaceX = spacing + wid.style().layoutSpacing(QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Horizontal)
            spaceY = spacing + wid.style().layoutSpacing(QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Vertical)
            
            nextX = x + item.sizeHint().width() + spaceX
            if nextX - spaceX > rect.right() and lineHeight > 0:
                x = rect.x()
                y = y + lineHeight + spaceY
                nextX = x + item.sizeHint().width() + spaceX
                lineHeight = 0

            if not testOnly:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = nextX
            lineHeight = max(lineHeight, item.sizeHint().height())

        return y + lineHeight - rect.y()

class CameraWidget(QFrame):
    """Square widget indicating a single camera feed."""
    removed = pyqtSignal(int)
    
    def __init__(self, sensor_id, name, parent=None):
        super().__init__(parent)
        self.sensor_id = sensor_id
        self.name = name
        self.setFixedSize(320, 320)
        
        self.setStyleSheet("""
            CameraWidget {
                background-color: #000;
                border: 1px solid #3f3f46;
                border-radius: 8px;
            }
            QLabel { color: white; }
        """)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        # Top Bar
        top_bar = QWidget()
        top_bar.setStyleSheet("background-color: rgba(0,0,0,0.6); border-top-left-radius: 8px; border-top-right-radius: 8px;")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(8, 4, 8, 4)
        
        lbl_name = QLabel(self.name)
        lbl_name.setStyleSheet("font-weight: bold; background: transparent;")
        
        btn_close = QPushButton("×")
        btn_close.setFixedSize(24, 24)
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.close_camera)
        btn_close.setStyleSheet("QPushButton { background: transparent; color: #aaa; border: none; font-size: 18px; font-weight: bold; } QPushButton:hover { color: #f44; }")
        
        top_layout.addWidget(lbl_name)
        top_layout.addStretch()
        top_layout.addWidget(btn_close)
        
        # Display
        self.video_label = QLabel("Connecting...")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet("background: transparent;")
        
        self.layout.addWidget(top_bar)
        self.layout.addWidget(self.video_label)
        self.layout.setStretch(1, 1)
        
        self.thread = VideoThread(sensor_id)
        self.thread.change_pixmap_signal.connect(self.update_image)
        self.thread.start()

    def update_image(self, cv_img):
        rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        qt_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888)
        scaled_pixmap = QPixmap.fromImage(qt_img).scaled(
            self.video_label.width(), self.video_label.height(), 
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.video_label.setPixmap(scaled_pixmap)

    def close_camera(self):
        self.thread.stop()
        self.removed.emit(self.sensor_id)
        self.deleteLater()

class AddCameraWidget(QFrame):
    """Square widget with a '+' button."""
    clicked = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(320, 320)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            AddCameraWidget {
                background-color: #22262b;
                border: 2px dashed #3f3f46;
                border-radius: 8px;
            }
            AddCameraWidget:hover {
                border-color: #76b900;
                background-color: #282c34;
            }
            QLabel { color: #555; background: transparent;}
        """)
        
        layout = QVBoxLayout(self)
        label = QLabel("+")
        label.setStyleSheet("font-size: 64px; font-weight: 300;")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        
        label_text = QLabel("Add Camera")
        label_text.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 20px;")
        label_text.setAlignment(Qt.AlignCenter)
        layout.addWidget(label_text)

    def mousePressEvent(self, event):
        self.clicked.emit()

class SidebarButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setObjectName("NavButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(50)

# =============================================================================
# MAIN WINDOW
# =============================================================================

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jetson Arducam AI Kit - Professional Dashboard")
        self.setGeometry(100, 100, 1280, 800)
        
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.root_dir = os.path.dirname(self.script_dir)
        
        style_path = os.path.join(self.script_dir, "styles.qss")
        if os.path.exists(style_path):
            with open(style_path, "r") as f:
                self.setStyleSheet(f.read())
        
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.main_layout = QHBoxLayout(self.main_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        self.setup_sidebar()
        
        self.content_area = QStackedWidget()
        self.content_area.setObjectName("ContentArea")
        self.main_layout.addWidget(self.content_area)
        
        self.page_dashboard = self.create_dashboard_page()
        self.page_cameras = self.create_cameras_page()
        self.page_install = self.create_install_page()
        self.page_docker = self.create_docker_page()
        
        self.content_area.addWidget(self.page_dashboard)
        self.content_area.addWidget(self.page_cameras)
        self.content_area.addWidget(self.page_install)
        self.content_area.addWidget(self.page_docker)
        
        self.system_monitor = SystemMonitorThread()
        self.system_monitor.update_signal.connect(self.update_system_stats)
        self.system_monitor.start()

    def setup_sidebar(self):
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_layout.setSpacing(8)
        
        logo_label = QLabel("   Jetson AI Kit")
        logo_label.setObjectName("HeaderTitle")
        logo_label.setFixedHeight(80)
        self.sidebar_layout.addWidget(logo_label)
        
        self.btn_dashboard = SidebarButton("  Dashboard")
        self.btn_cameras = SidebarButton("  Cameras")
        self.btn_install = SidebarButton("  Installation")
        self.btn_docker = SidebarButton("  Docker Manager")
        
        self.btn_dashboard.clicked.connect(lambda: self.switch_page(0))
        self.btn_cameras.clicked.connect(lambda: self.switch_page(1))
        self.btn_install.clicked.connect(lambda: self.switch_page(2))
        self.btn_docker.clicked.connect(lambda: self.switch_page(3))
        
        self.sidebar_layout.addWidget(self.btn_dashboard)
        self.sidebar_layout.addWidget(self.btn_cameras)
        self.sidebar_layout.addWidget(self.btn_install)
        self.sidebar_layout.addWidget(self.btn_docker)
        self.sidebar_layout.addStretch()
        
        footer = QLabel("  v2.2.0 • Connected")
        footer.setStyleSheet("color: #71717a; font-size: 11px; padding: 16px;")
        self.sidebar_layout.addWidget(footer)
        
        self.main_layout.addWidget(self.sidebar)
        self.btn_dashboard.setChecked(True)

    def switch_page(self, index):
        self.content_area.setCurrentIndex(index)
        buttons = [self.btn_dashboard, self.btn_cameras, self.btn_install, self.btn_docker]
        for i, btn in enumerate(buttons):
            btn.setChecked(i == index)

    # -------------------------------------------------------------------------
    # DASHBOARD
    # -------------------------------------------------------------------------
    def create_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QLabel("System Dashboard")
        header.setStyleSheet("color: white; font-size: 24px; font-weight: bold; margin-bottom: 20px;")
        layout.addWidget(header)
        
        grid = QGridLayout()
        layout.addLayout(grid)
        
        self.ram_val = QLabel("0 / 0 GB"); self.ram_bar = QProgressBar()
        self.ram_val.setObjectName("CardValue"); self.ram_bar.setObjectName("RamBar")
        grid.addWidget(self.create_stat_card("Memory Usage", self.ram_val, self.ram_bar), 0, 0)
        
        self.swap_val = QLabel("0 GB"); self.swap_bar = QProgressBar()
        self.swap_val.setObjectName("CardValue"); self.swap_bar.setObjectName("SwapBar")
        grid.addWidget(self.create_stat_card("Swap Usage", self.swap_val, self.swap_bar), 0, 1)
        
        self.disk_val = QLabel("0 %"); self.disk_bar = QProgressBar()
        self.disk_val.setObjectName("CardValue"); self.disk_bar.setObjectName("DiskBar")
        grid.addWidget(self.create_stat_card("Disk Usage (/)", self.disk_val, self.disk_bar), 1, 0)
        
        self.cpu_val = QLabel("0 %"); self.cpu_bar = QProgressBar()
        self.cpu_val.setObjectName("CardValue")
        grid.addWidget(self.create_stat_card("CPU Load", self.cpu_val, self.cpu_bar), 1, 1)
        
        layout.addStretch()
        return page

    def create_stat_card(self, title, value_label, progress_bar):
        card = QFrame()
        card.setObjectName("Card")
        l = QVBoxLayout(card)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        l.addWidget(t)
        l.addWidget(value_label)
        l.addWidget(progress_bar)
        return card

    def update_system_stats(self, stats):
        self.ram_val.setText(f"{stats['ram_used']:.1f} / {stats['ram_total']:.1f} GB")
        self.ram_bar.setValue(int(stats['ram_percent']))
        self.swap_val.setText(f"{stats['swap_percent']}%")
        self.swap_bar.setValue(int(stats['swap_percent']))
        self.disk_val.setText(f"{stats['disk_percent']}%")
        self.disk_bar.setValue(int(stats['disk_percent']))
        self.cpu_val.setText(f"{stats['cpu_percent']}%")
        self.cpu_bar.setValue(int(stats['cpu_percent']))

    # -------------------------------------------------------------------------
    # CAMERAS (GRID)
    # -------------------------------------------------------------------------
    def create_cameras_page(self):
        page = QWidget()
        main_layout = QVBoxLayout(page)
        
        header = QLabel("Multi-Camera Grid")
        header.setStyleSheet("color: white; font-size: 24px; font-weight: bold; margin-bottom: 10px;")
        main_layout.addWidget(header)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")
        
        self.flow_container = QWidget()
        self.flow_layout = FlowLayout(self.flow_container, margin=10, spacing=20)
        
        self.add_cam_widget = AddCameraWidget()
        self.add_cam_widget.clicked.connect(self.show_add_camera_dialog)
        self.flow_layout.addWidget(self.add_cam_widget)
        
        self.flow_container.setLayout(self.flow_layout)
        scroll.setWidget(self.flow_container)
        
        main_layout.addWidget(scroll)
        self.active_cameras = set()
        return page

    def show_add_camera_dialog(self):
        cameras = self.get_available_cameras()
        available = [c for c in cameras if c[1] not in self.active_cameras]
        
        if not available:
            QMessageBox.information(self, "Info", "No new cameras detected.")
            return
            
        items = [x[0] for x in available]
        item, ok = QInputDialog.getItem(self, "Select Camera", "Available Devices:", items, 0, False)
        
        if ok and item:
            selected = next(x for x in available if x[0] == item)
            self.add_camera_to_grid(selected[1], selected[0])

    def add_camera_to_grid(self, sensor_id, name):
        cam_widget = CameraWidget(sensor_id, name)
        cam_widget.removed.connect(self.remove_camera_from_grid)
        
        self.flow_layout.removeWidget(self.add_cam_widget)
        self.add_cam_widget.setParent(None)
        
        self.flow_layout.addWidget(cam_widget)
        self.flow_layout.addWidget(self.add_cam_widget)
        self.active_cameras.add(sensor_id)

    def remove_camera_from_grid(self, sensor_id):
        self.active_cameras.discard(sensor_id)

    def get_available_cameras(self):
        cameras = []
        try:
            import glob
            devs = glob.glob('/dev/video*')
            devs.sort()
            for dev in devs:
                idx = int(dev.replace('/dev/video', ''))
                cameras.append((f"Camera {idx} ({dev})", idx))
        except:
            pass
        return cameras

    # -------------------------------------------------------------------------
    # INSTALLATION
    # -------------------------------------------------------------------------
    def create_install_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QLabel("Installation Wizard")
        header.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        layout.addWidget(header)
        
        opts = QHBoxLayout()
        self.btn_install_drivers = QPushButton("1. Install Drivers")
        self.btn_install_drivers.clicked.connect(lambda: self.run_script("scripts/setup_cameras.sh"))
        
        self.btn_verify = QPushButton("2. Verify System")
        self.btn_verify.clicked.connect(lambda: self.run_script("scripts/test_installation.sh"))
        
        self.btn_build = QPushButton("3. Build Docker")
        self.btn_build.clicked.connect(lambda: self.run_script("scripts/build_docker.sh"))
        
        opts.addWidget(self.btn_install_drivers)
        opts.addWidget(self.btn_verify)
        opts.addWidget(self.btn_build)
        layout.addLayout(opts)
        
        self.log_output = QTextEdit()
        self.log_output.setObjectName("LogOutput")
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)
        return page

    def run_script(self, script_path):
        self.log_output.append(f"\n>>> Running: {script_path}...\n")
        self.runner = CommandRunnerThread(f"bash {os.path.join(self.root_dir, script_path)}", self.root_dir)
        self.runner.output_signal.connect(self.log_output.append)
        self.runner.finished_signal.connect(self.on_script_finished)
        self.runner.start()
        self.btn_install_drivers.setEnabled(False)
        self.btn_verify.setEnabled(False)
        self.btn_build.setEnabled(False)

    def on_script_finished(self, code):
        self.log_output.append(f"\n>>> Process finished with code {code}\n")
        self.btn_install_drivers.setEnabled(True)
        self.btn_verify.setEnabled(True)
        self.btn_build.setEnabled(True)

    # -------------------------------------------------------------------------
    # DOCKER
    # -------------------------------------------------------------------------
    def create_docker_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        header = QLabel("Docker Management")
        header.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        layout.addWidget(header)
        
        actions = QHBoxLayout()
        btn_start = QPushButton("Start Container")
        btn_start.setObjectName("PrimaryButton")
        btn_start.clicked.connect(lambda: self.run_script("scripts/run_docker.sh"))
        
        btn_stop = QPushButton("Stop Container")
        btn_stop.setObjectName("DangerButton")
        btn_stop.clicked.connect(self.stop_docker)
        
        actions.addWidget(btn_start)
        actions.addWidget(btn_stop)
        actions.addStretch()
        layout.addLayout(actions)
        
        self.docker_logs = QTextEdit()
        self.docker_logs.setObjectName("LogOutput")
        self.docker_logs.setPlaceholderText("Container logs...")
        layout.addWidget(self.docker_logs)
        return page

    def stop_docker(self):
        self.log_output.append("Stopping container...")
        subprocess.run("docker stop jetson-arducam-ctr", shell=True)
        QMessageBox.information(self, "Docker", "Container stop command sent.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec_())
