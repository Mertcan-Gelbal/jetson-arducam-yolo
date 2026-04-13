#!/usr/bin/env python3
"""
Autofocus for Jetson CSI + OpenCV (sharpness search via Laplacian variance).
Uses focus_imx519.py between tries — works with IMX519-style I2C focusers; not for fixed-lens modules.

Best results: stop other nvarguscamerasrc pipelines first. On failure, may fall back to mid position.

Usage: python autofocus_imx519.py [--bus 10] [--sensor-id 0] [--steps 9]
"""
import argparse
import os
import sys
import subprocess
import time

def set_focus(bus: int, position: int) -> bool:
    script = os.path.join(os.path.dirname(__file__), "focus_imx519.py")
    if not os.path.exists(script):
        return False
    r = subprocess.run([sys.executable, script, "--bus", str(bus), "--position", str(position)],
                       capture_output=True, timeout=5, cwd=os.path.dirname(os.path.dirname(__file__)))
    return r.returncode == 0

def main():
    ap = argparse.ArgumentParser(description="CSI autofocus by sharpness (IMX519-style I2C focuser)")
    ap.add_argument("--bus", type=int, default=10, help="I2C bus")
    ap.add_argument("--sensor-id", type=int, default=0, help="GStreamer sensor-id for capture")
    ap.add_argument("--steps", type=int, default=9, help="Number of positions to try (spread 0-1023)")
    args = ap.parse_args()
    bus, sensor_id, steps = args.bus, args.sensor_id, args.steps
    steps = max(2, min(20, steps))
    positions = [int(i * 1023 / (steps - 1)) for i in range(steps)]

    try:
        import cv2
        import numpy as np
    except ImportError:
        print("OpenCV required for autofocus. Run: pip install opencv-python", file=sys.stderr)
        print("Falling back: setting focus to middle position.", file=sys.stderr)
        set_focus(bus, 512)
        sys.exit(0)

    # Short pipeline: grab one frame (Linux / Jetson)
    if sys.platform != "linux":
        print("Autofocus capture supported on Linux (Jetson). Setting middle position.", file=sys.stderr)
        set_focus(bus, 512)
        sys.exit(0)

    gst = (
        f"nvarguscamerasrc sensor-id={sensor_id} num-buffers=1 ! "
        "video/x-raw(memory:NVMM), width=640, height=480, format=NV12 ! "
        "nvvidconv ! video/x-raw, format=BGR ! appsink"
    )
    best_pos, best_var = 512, 0.0

    for pos in positions:
        if not set_focus(bus, pos):
            continue
        time.sleep(0.3)  # motor settle
        cap = cv2.VideoCapture(gst, cv2.CAP_GSTREAMER)
        if not cap.isOpened():
            continue
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if var > best_var:
            best_var = var
            best_pos = pos

    set_focus(bus, best_pos)
    print(f"Autofocus done: best position {best_pos} (sharpness {best_var:.0f})")

if __name__ == "__main__":
    main()
