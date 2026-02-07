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
    QScrollArea, QGridLayout, QTextEdit, QMessageBox, QComboBox
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize, QPropertyAnimation, QEasingCurve
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
        # Low latency + ISP processing
        self.gst_pipeline = (
            f"nvarguscamerasrc sensor-id={sensor_id} ! "
            "video/x-raw(memory:NVMM), width=(int)1280, height=(int)720, format=(string)NV12, framerate=(fraction)30/1 ! "
            "nvvidconv ! video/x-raw, format=(string)BGRx ! "
            "videoconvert ! video/x-raw, format=(string)BGR ! appsink"
        )

    def run(self):
        # Optimized capture
        cap = cv2.VideoCapture(self.gst_pipeline, cv2.CAP_GSTREAMER)
        
        # Fallback to standard V4L2 if GStreamer fails (e.g. USB Cam)
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
            # Memory
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disk = psutil.disk_usage('/')
            
            # CPU
            cpu_percent = psutil.cpu_percent(interval=None)
            
            # Emit data
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
            self.command,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=True,
            text=True,
            bufsize=1
        )
        
        for line in iter(process.stdout.readline, ''):
            self.output_signal.emit(line.strip())
            
        process.stdout.close()
        ret = process.wait()
        self.finished_signal.emit(ret)

# =============================================================================
# UI COMPONENTS
# =============================================================================

class SidebarButton(QPushButton):
    def __init__(self, text, icon_name=None, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setObjectName("NavButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(50)

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Jetson Arducam AI Kit - Professional Dashboard")
        self.setGeometry(100, 100, 1280, 800)
        
        # Determine paths
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.root_dir = os.path.dirname(self.script_dir)
        
        # Load Stylesheet
        style_path = os.path.join(self.script_dir, "styles.qss")
        if os.path.exists(style_path):
            with open(style_path, "r") as f:
                self.setStyleSheet(f.read())
        
        # Main Layout
        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.main_layout = QHBoxLayout(self.main_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # 1. Sidebar
        self.setup_sidebar()
        
        # 2. Key Content Area
        self.content_area = QStackedWidget()
        self.content_area.setObjectName("ContentArea")
        self.main_layout.addWidget(self.content_area)
        
        # Define Pages
        self.page_dashboard = self.create_dashboard_page()
        self.page_cameras = self.create_cameras_page()
        self.page_install = self.create_install_page()
        self.page_docker = self.create_docker_page()
        
        self.content_area.addWidget(self.page_dashboard)
        self.content_area.addWidget(self.page_cameras)
        self.content_area.addWidget(self.page_install)
        self.content_area.addWidget(self.page_docker)
        
        # Start Monitors
        self.system_monitor = SystemMonitorThread()
        self.system_monitor.update_signal.connect(self.update_system_stats)
        self.system_monitor.start()
        
        # Video Thread placeholder
        self.video_thread = None

    def setup_sidebar(self):
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar_layout = QVBoxLayout(self.sidebar)
        self.sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_layout.setSpacing(8)
        
        # Logo Area
        logo_label = QLabel("   Jetson AI Kit")
        logo_label.setObjectName("HeaderTitle")
        logo_label.setFixedHeight(80)
        self.sidebar_layout.addWidget(logo_label)
        
        # Navigation Buttons
        self.btn_dashboard = SidebarButton("  Dashboard")
        self.btn_cameras = SidebarButton("  Cameras")
        self.btn_install = SidebarButton("  Installation")
        self.btn_docker = SidebarButton("  Docker Manager")
        
        # Connect signals
        self.btn_dashboard.clicked.connect(lambda: self.switch_page(0))
        self.btn_cameras.clicked.connect(lambda: self.switch_page(1))
        self.btn_install.clicked.connect(lambda: self.switch_page(2))
        self.btn_docker.clicked.connect(lambda: self.switch_page(3))
        
        # Add to layout
        self.sidebar_layout.addWidget(self.btn_dashboard)
        self.sidebar_layout.addWidget(self.btn_cameras)
        self.sidebar_layout.addWidget(self.btn_install)
        self.sidebar_layout.addWidget(self.btn_docker)
        self.sidebar_layout.addStretch()
        
        # Footer
        footer = QLabel("  v2.1.0 • Connected")
        footer.setStyleSheet("color: #71717a; font-size: 11px; padding: 16px;")
        self.sidebar_layout.addWidget(footer)
        
        self.main_layout.addWidget(self.sidebar)
        
        # Set Default Active
        self.btn_dashboard.setChecked(True)

    def switch_page(self, index):
        self.content_area.setCurrentIndex(index)
        
        # Update button states
        buttons = [self.btn_dashboard, self.btn_cameras, self.btn_install, self.btn_docker]
        for i, btn in enumerate(buttons):
            btn.setChecked(i == index)
            
        # Stop video if leaving camera page
        if index != 1 and self.video_thread:
            self.stop_video()

    # -------------------------------------------------------------------------
    # DASHBOARD PAGE
    # -------------------------------------------------------------------------
    def create_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        # Header
        header = QLabel("System Dashboard")
        header.setStyleSheet("color: white; font-size: 24px; font-weight: bold; margin-bottom: 20px;")
        layout.addWidget(header)
        
        # Grid for Stats
        grid = QGridLayout()
        layout.addLayout(grid)
        
        # RAM Card
        self.ram_val = QLabel("0 / 0 GB")
        self.ram_val.setObjectName("CardValue")
        self.ram_bar = QProgressBar()
        self.ram_bar.setObjectName("RamBar")
        grid.addWidget(self.create_stat_card("Memory Usage", self.ram_val, self.ram_bar), 0, 0)
        
        # Swap Card
        self.swap_val = QLabel("0 GB")
        self.swap_val.setObjectName("CardValue")
        self.swap_bar = QProgressBar()
        self.swap_bar.setObjectName("SwapBar")
        grid.addWidget(self.create_stat_card("Swap Usage", self.swap_val, self.swap_bar), 0, 1)
        
        # Disk Card
        self.disk_val = QLabel("0 %")
        self.disk_val.setObjectName("CardValue")
        self.disk_bar = QProgressBar()
        self.disk_bar.setObjectName("DiskBar")
        grid.addWidget(self.create_stat_card("Disk Usage (/)", self.disk_val, self.disk_bar), 1, 0)
        
        # CPU Card
        self.cpu_val = QLabel("0 %")
        self.cpu_val.setObjectName("CardValue")
        self.cpu_bar = QProgressBar()
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
        # RAM
        self.ram_val.setText(f"{stats['ram_used']:.1f} / {stats['ram_total']:.1f} GB")
        self.ram_bar.setValue(int(stats['ram_percent']))
        
        # Swap
        self.swap_val.setText(f"{stats['swap_percent']}%")
        self.swap_bar.setValue(int(stats['swap_percent']))
        
        # Disk
        self.disk_val.setText(f"{stats['disk_percent']}%")
        self.disk_bar.setValue(int(stats['disk_percent']))
        
        # CPU
        self.cpu_val.setText(f"{stats['cpu_percent']}%")
        self.cpu_bar.setValue(int(stats['cpu_percent']))

    # -------------------------------------------------------------------------
    # CAMERAS PAGE (Live Streaming)
    # -------------------------------------------------------------------------
    def create_cameras_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Live Camera Preview")
        title.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        
        # Camera Selection
        self.camera_selector = QComboBox()
        self.camera_selector.setMinimumWidth(250)
        self.camera_selector.setStyleSheet("""
            QComboBox {
                background-color: #2d323b;
                color: white;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 6px;
                min-height: 20px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #2d323b;
                color: white;
                selection-background-color: #76b900;
            }
        """)
        
        # Refresh Button
        btn_refresh = QPushButton("⟳")
        btn_refresh.setFixedSize(36, 36)
        btn_refresh.setToolTip("Refresh Camera List")
        btn_refresh.clicked.connect(self.refresh_cameras)
        
        self.btn_start_cam = QPushButton("Start Stream")
        self.btn_start_cam.setObjectName("PrimaryButton")
        self.btn_start_cam.clicked.connect(self.toggle_video)
        
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(QLabel("Select Camera:"))
        header_layout.addWidget(self.camera_selector)
        header_layout.addWidget(btn_refresh)
        header_layout.addWidget(self.btn_start_cam)
        layout.addLayout(header_layout)
        
        # Video Area
        self.video_label = QLabel("Camera Offline")
        self.video_label.setObjectName("CameraPreview")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setMinimumSize(640, 480)
        
        layout.addWidget(self.video_label)
        layout.addStretch()
        
        # Initial scan
        QTimer.singleShot(500, self.refresh_cameras)
        
        return page

    def get_available_cameras(self):
        """Scan for available /dev/video* devices."""
        cameras = []
        try:
            import glob
            devs = glob.glob('/dev/video*')
            devs.sort()
            
            for dev in devs:
                idx_str = dev.replace('/dev/video', '')
                if idx_str.isdigit():
                    idx = int(idx_str)
                    cam_name = f"Camera {idx} ({dev})"
                    cameras.append((cam_name, idx))
        except Exception as e:
            print(f"Error scanning cameras: {e}")
        return cameras

    def refresh_cameras(self):
        """Update the combo box with available cameras."""
        self.camera_selector.clear()
        cameras = self.get_available_cameras()
        
        if not cameras:
            self.camera_selector.addItem("No cameras found", -1)
            self.btn_start_cam.setEnabled(False)
        else:
            for name, idx in cameras:
                self.camera_selector.addItem(name, idx)
            self.btn_start_cam.setEnabled(True)

    def toggle_video(self):
        if self.video_thread:
            self.stop_video()
        else:
            self.start_video()

    def start_video(self):
        # Get selected camera index
        sensor_id = self.camera_selector.currentData()
        if sensor_id is None or sensor_id < 0:
            QMessageBox.warning(self, "Camera Error", "No valid camera selected.")
            return

        self.video_thread = VideoThread(sensor_id=sensor_id)
        self.video_thread.change_pixmap_signal.connect(self.update_video_image)
        self.video_thread.start()
        
        self.btn_start_cam.setText("Stop Stream")
        self.btn_start_cam.setObjectName("DangerButton")
        self.camera_selector.setEnabled(False) # Lock selection while streaming
        
        # Force styles update
        self.btn_start_cam.style().unpolish(self.btn_start_cam)
        self.btn_start_cam.style().polish(self.btn_start_cam)

    def stop_video(self):
        if self.video_thread:
            self.video_thread.stop()
            self.video_thread = None
            
        self.video_label.setText("Camera Offline")
        self.video_label.setPixmap(QPixmap()) # Clear
        
        self.btn_start_cam.setText("Start Stream")
        self.btn_start_cam.setObjectName("PrimaryButton")
        self.camera_selector.setEnabled(True) # Unlock selection
        
        self.btn_start_cam.style().unpolish(self.btn_start_cam)
        self.btn_start_cam.style().polish(self.btn_start_cam)

    def update_video_image(self, cv_img):
        qt_img = self.convert_cv_qt(cv_img)
        self.video_label.setPixmap(qt_img)

    def convert_cv_qt(self, cv_img):
        rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        convert_to_Qt_format = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        p = convert_to_Qt_format.scaled(self.video_label.width(), self.video_label.height(), Qt.KeepAspectRatio)
        return QPixmap.fromImage(p)

    # -------------------------------------------------------------------------
    # INSTALLATION PAGE
    # -------------------------------------------------------------------------
    def create_install_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        
        header = QLabel("Installation Wizard")
        header.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        layout.addWidget(header)
        
        # Options
        opts_layout = QHBoxLayout()
        self.btn_install_drivers = QPushButton("1. Install Drivers")
        self.btn_install_drivers.clicked.connect(lambda: self.run_script("scripts/setup_cameras.sh"))
        
        self.btn_verify = QPushButton("2. Verify System")
        self.btn_verify.clicked.connect(lambda: self.run_script("scripts/test_installation.sh"))
        
        self.btn_build = QPushButton("3. Build Docker")
        self.btn_build.clicked.connect(lambda: self.run_script("scripts/build_docker.sh"))
        
        opts_layout.addWidget(self.btn_install_drivers)
        opts_layout.addWidget(self.btn_verify)
        opts_layout.addWidget(self.btn_build)
        layout.addLayout(opts_layout)
        
        # Log Output
        self.log_output = QTextEdit()
        self.log_output.setObjectName("LogOutput")
        self.log_output.setReadOnly(True)
        layout.addWidget(self.log_output)
        
        return page

    def run_script(self, script_path):
        full_path = os.path.join(self.root_dir, script_path)
        self.log_output.append(f"\n>>> Running: {script_path}...\n")
        
        self.runner = CommandRunnerThread(f"bash {full_path}", cwd=self.root_dir)
        self.runner.output_signal.connect(self.log_output.append)
        self.runner.finished_signal.connect(self.on_script_finished)
        self.runner.start()
        
        # Disable buttons
        self.btn_install_drivers.setEnabled(False)
        self.btn_verify.setEnabled(False)
        self.btn_build.setEnabled(False)

    def on_script_finished(self, code):
        self.log_output.append(f"\n>>> Process finished with code {code}\n")
        # Enable buttons
        self.btn_install_drivers.setEnabled(True)
        self.btn_verify.setEnabled(True)
        self.btn_build.setEnabled(True)
        
        if code == 0:
            QMessageBox.information(self, "Success", "Task completed successfully!")
        else:
            QMessageBox.warning(self, "Warning", "Task completed with errors. Check logs.")

    # -------------------------------------------------------------------------
    # DOCKER PAGE
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
        
        # Docker Logs placeholder
        self.docker_logs = QTextEdit()
        self.docker_logs.setObjectName("LogOutput")
        self.docker_logs.setPlaceholderText("Container logs will appear here...")
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
