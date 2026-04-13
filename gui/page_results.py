from types import SimpleNamespace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget


def build_results_page(self, helpers):
    h = SimpleNamespace(**helpers)
    FlowLayout = h.FlowLayout
    polish_scroll_area = h.polish_scroll_area
    make_icon_refresh_button = h.make_icon_refresh_button

    w = QWidget()
    l = QVBoxLayout(w)
    l.setContentsMargins(40, 40, 40, 40)
    l.setSpacing(0)

    head = QVBoxLayout()
    head.setSpacing(8)
    th = QHBoxLayout()
    pt = QLabel("Results")
    pt.setObjectName("PageTitle")
    th.addWidget(pt)
    th.addStretch()
    th.addWidget(make_icon_refresh_button(self.refresh_library, "Refresh inspection results"))
    head.addLayout(th)

    ps = QLabel(
        "Review project inspection results, detected classes, saved images, and runtime outputs."
    )
    ps.setObjectName("PageSubtitle")
    ps.setWordWrap(True)
    head.addWidget(ps)
    l.addLayout(head)
    l.addSpacing(25)

    sa = QScrollArea()
    sa.setWidgetResizable(True)
    cnt = QWidget()
    self.media_hub_layout = FlowLayout(cnt)
    self.media_hub_layout.setContentsMargins(0, 0, 0, 0)
    self.media_hub_layout.setSpacing(20)

    sa.setWidget(cnt)
    polish_scroll_area(sa)
    l.addWidget(sa, 1)
    QTimer.singleShot(100, self.refresh_library)
    return w
