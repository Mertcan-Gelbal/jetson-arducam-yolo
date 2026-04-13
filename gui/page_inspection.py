from types import SimpleNamespace

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def build_inspection_page(self, helpers):
    h = SimpleNamespace(**helpers)
    FlowLayout = h.FlowLayout
    polish_scroll_area = h.polish_scroll_area

    w = QWidget()
    l = QVBoxLayout(w)
    l.setContentsMargins(40, 40, 40, 40)
    l.setSpacing(0)

    title_block = QVBoxLayout()
    title_block.setSpacing(6)
    pt = QLabel("Inspection")
    pt.setObjectName("PageTitle")
    title_block.addWidget(pt)
    ps = QLabel(
        "Monitor the active project runtime, trigger inspections manually, and manage operator-visible camera sources."
    )
    ps.setObjectName("PageSubtitle")
    ps.setWordWrap(True)
    title_block.addWidget(ps)
    l.addLayout(title_block)
    l.addSpacing(16)

    runtime_card = QFrame()
    runtime_card.setObjectName("InfoCard")
    runtime_l = QVBoxLayout(runtime_card)
    runtime_l.setContentsMargins(24, 22, 24, 22)
    runtime_l.setSpacing(16)

    hdr = QHBoxLayout()
    copy = QVBoxLayout()
    copy.setSpacing(4)
    title = QLabel("Inspection Runtime")
    title.setObjectName("SectionHeading")
    copy.addWidget(title)
    self._camera_runtime_name = QLabel("VisionDock Runtime Node")
    self._camera_runtime_name.setObjectName("RuntimeNameLabel")
    copy.addWidget(self._camera_runtime_name)
    self._camera_runtime_endpoint = QLabel("Runtime endpoint will appear here")
    self._camera_runtime_endpoint.setObjectName("CaptionMuted")
    self._camera_runtime_endpoint.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
    )
    copy.addWidget(self._camera_runtime_endpoint)
    hdr.addLayout(copy, 1)

    self._camera_runtime_status_badge = QLabel("Checking…")
    self._camera_runtime_status_badge.setObjectName("StatusPill")
    self._camera_runtime_status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    self._camera_runtime_status_badge.setMinimumWidth(130)
    self._camera_runtime_status_badge.setMinimumHeight(34)
    hdr.addWidget(self._camera_runtime_status_badge, 0, Qt.AlignmentFlag.AlignTop)
    runtime_l.addLayout(hdr)

    self._camera_runtime_detail = QLabel(
        "Runtime state will appear here once the edge service responds."
    )
    self._camera_runtime_detail.setObjectName("CaptionMuted")
    self._camera_runtime_detail.setWordWrap(True)
    runtime_l.addWidget(self._camera_runtime_detail)

    metrics = QGridLayout()
    metrics.setHorizontalSpacing(16)
    metrics.setVerticalSpacing(14)

    def _make_metric(title_text, attr_name):
        cell = QFrame()
        cell.setObjectName("SurfaceRow")
        cl = QVBoxLayout(cell)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(6)
        cap = QLabel(title_text.upper())
        cap.setObjectName("CaptionMutedSm")
        val = QLabel("—")
        val.setWordWrap(True)
        val.setObjectName("RuntimeMetricValue")
        cl.addWidget(cap)
        cl.addWidget(val)
        setattr(self, attr_name, val)
        return cell

    metrics.addWidget(_make_metric("Active model", "_camera_runtime_model_value"), 0, 0)
    metrics.addWidget(_make_metric("Last decision", "_camera_runtime_decision_value"), 0, 1)
    metrics.addWidget(_make_metric("Trigger mode", "_camera_runtime_trigger_value"), 0, 2)
    metrics.addWidget(_make_metric("GPIO backend", "_camera_runtime_gpio_value"), 0, 3)
    metrics.addWidget(
        _make_metric("Preview source", "_camera_runtime_preview_value"), 1, 0, 1, 2
    )
    metrics.addWidget(_make_metric("Inspection count", "_camera_runtime_count_value"), 1, 2)
    metrics.addWidget(
        _make_metric("Last trigger", "_camera_runtime_last_trigger_value"), 1, 3
    )
    runtime_l.addLayout(metrics)

    btn_row = QHBoxLayout()
    btn_row.setSpacing(10)
    refresh_btn = QPushButton("Refresh Runtime")
    refresh_btn.setObjectName("BtnSecondary")
    refresh_btn.setFixedHeight(34)
    refresh_btn.clicked.connect(lambda: self._refresh_camera_runtime_card(force=True))
    btn_row.addWidget(refresh_btn)

    self._camera_runtime_reload_btn = QPushButton("Reload Profile")
    self._camera_runtime_reload_btn.setObjectName("BtnSecondary")
    self._camera_runtime_reload_btn.setFixedHeight(34)
    self._camera_runtime_reload_btn.clicked.connect(self._reload_inspection_runtime_profile)
    btn_row.addWidget(self._camera_runtime_reload_btn)

    self._camera_runtime_trigger_btn = QPushButton("Manual Trigger")
    self._camera_runtime_trigger_btn.setObjectName("BtnPrimary")
    self._camera_runtime_trigger_btn.setFixedHeight(36)
    self._camera_runtime_trigger_btn.clicked.connect(self._trigger_inspection_runtime)
    btn_row.addWidget(self._camera_runtime_trigger_btn)
    btn_row.addStretch(1)

    open_settings_btn = QPushButton("Open Settings")
    open_settings_btn.setObjectName("ShellBtn")
    open_settings_btn.setFixedHeight(34)
    open_settings_btn.clicked.connect(lambda: self.switch(5))
    btn_row.addWidget(open_settings_btn)
    runtime_l.addLayout(btn_row)
    l.addWidget(runtime_card)
    l.addSpacing(18)

    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setStyleSheet("background:transparent; border:none;")
    self.cam_widget = QWidget()
    self.cf = FlowLayout(self.cam_widget)
    self.cf.setContentsMargins(0, 0, 0, 0)
    self.cf.setSpacing(20)
    self.abc = self.create_add_btn(
        "Add Camera Source",
        self.modal_cam,
        "Add USB, CSI, RTSP, HTTP, file, or runtime preview sources for operator visibility.",
    )
    self.cf.addWidget(self.abc)
    sa.setWidget(self.cam_widget)
    polish_scroll_area(sa)
    l.addWidget(sa, 1)
    QTimer.singleShot(100, self.refresh_cameras)
    QTimer.singleShot(250, self._refresh_camera_runtime_card)
    return w
