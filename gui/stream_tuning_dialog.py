from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QSpinBox, QVBoxLayout


def open_stream_tuning_dialog_for_card(
    card,
    app_win,
    parse_overrides,
    probe_native_geometry,
    preview_resolution_choices,
):
    if app_win is None or not hasattr(app_win, "_apply_stream_tuning"):
        return

    ov = parse_overrides(getattr(card, "stream_meta", "") or "")
    cur_wh = ov.get("stream_pv")
    cur_fps = int(ov.get("stream_max_fps") or 0)
    src = str(getattr(card, "sub_val", "") or "")

    cached = getattr(card, "_last_native_wh", None)
    nw = nh = None
    fps_hint = 0.0
    if cached and isinstance(cached, (tuple, list)) and len(cached) == 2:
        try:
            nw, nh = int(cached[0]), int(cached[1])
        except (TypeError, ValueError):
            nw = nh = None
    if nw is None or nh is None:
        nw, nh, fps_hint = probe_native_geometry(src)
    else:
        nw, nh, fps_hint = nw, nh, 0.0

    dlg = QDialog(app_win)
    dlg.setWindowTitle("Stream preview")
    dlg.setFixedWidth(448)
    lay = QVBoxLayout(dlg)
    lay.setSpacing(12)
    lay.setContentsMargins(18, 18, 18, 16)

    det = ""
    if nw and nh:
        det = f"<br><b>Detected source</b> — {nw}×{nh} (downsized options keep aspect ratio)."
        if fps_hint >= 5:
            det += f" Estimated source FPS: ~{fps_hint:.0f}."
    else:
        det = (
            "<br><i>Source resolution could not be measured</i> (network / short timeout). "
            "Generic 16:9 presets are shown; opening this menu while stream is active usually improves probing."
        )

    info = QLabel(
        "<b>Preview resolution</b> — scales on the <i>client side</i>; "
        "does not change Jetson encoder output.<br>"
        "<b>FPS limit</b> — 0 = maximum smoothness; limit when CPU usage is high."
        + det
    )
    info.setWordWrap(True)
    info.setObjectName("CaptionMuted")
    info.setTextFormat(Qt.TextFormat.RichText)
    lay.addWidget(info)

    combo = QComboBox()
    combo.setFixedHeight(36)
    if nw and nh:
        combo.addItem(f"Full source — {nw}×{nh} (no resampling)", None)
        for w, h in preview_resolution_choices(nw, nh):
            pct = max(1, min(99, int(round(100.0 * w * h / (nw * nh)))))
            combo.addItem(f"Preview {w}×{h}  (~%{pct})", (w, h))
    else:
        combo.addItem("Source — no resize", None)
        for label, wh in (
            ("1280 × 720", (1280, 720)),
            ("960 × 540", (960, 540)),
            ("854 × 480", (854, 480)),
            ("640 × 480", (640, 480)),
            ("640 × 360", (640, 360)),
        ):
            combo.addItem(label, wh)

    if cur_wh and len(cur_wh) == 2:
        found = False
        for i in range(combo.count()):
            d = combo.itemData(i)
            if d is not None and d[0] == cur_wh[0] and d[1] == cur_wh[1]:
                found = True
                break
        if not found:
            w, h = int(cur_wh[0]), int(cur_wh[1])
            combo.addItem(f"Saved setting {w}×{h}", (w, h))

    sel_ix = 0
    for i in range(combo.count()):
        d = combo.itemData(i)
        if d is None and cur_wh is None:
            sel_ix = i
            break
        if d is not None and cur_wh is not None and d[0] == cur_wh[0] and d[1] == cur_wh[1]:
            sel_ix = i
            break
    combo.setCurrentIndex(sel_ix)

    lay.addWidget(QLabel("Preview resolution"))
    lay.addWidget(combo)
    lay.addWidget(QLabel("Target FPS limit (0 = unlimited)"))
    spin = QSpinBox()
    spin.setRange(0, 120)
    if fps_hint >= 8:
        spin.setMaximum(max(120, int(fps_hint) + 20))
        spin.setToolTip(f"Source reported ~{fps_hint:.0f} fps; tune upper limit accordingly.")
    spin.setValue(cur_fps)
    spin.setFixedHeight(36)
    lay.addWidget(spin)

    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    ok_b = bb.button(QDialogButtonBox.StandardButton.Ok)
    ok_b.setText("Apply")
    ok_b.setObjectName("BtnPrimary")
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    app_win._apply_stream_tuning(card, combo.currentData(), spin.value())
