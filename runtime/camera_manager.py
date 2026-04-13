import logging
import os
import threading
import time

import cv2
import numpy as np

from runtime.camera_profiles import csi_sensor_label


log = logging.getLogger("visiondock.runtime")


def _read_latest(cap, max_flush: int = 24):
    if cap is None or not cap.isOpened():
        return False, None
    if not cap.grab():
        return False, None
    flushed = 0
    while flushed < max_flush and cap.grab():
        flushed += 1
    return cap.retrieve()


def build_argus_pipeline(
    sensor_id=0,
    capture_width=1920,
    capture_height=1080,
    display_width=1280,
    display_height=720,
    framerate=30,
    flip_method=0,
    aelock=False,
):
    aelock_token = " aelock=1" if aelock else ""
    return (
        f"nvarguscamerasrc sensor-id={int(sensor_id)}{aelock_token} ! "
        f"video/x-raw(memory:NVMM), width=(int){int(capture_width)}, height=(int){int(capture_height)}, "
        f"format=(string)NV12, framerate=(fraction){int(framerate)}/1 ! "
        f"nvvidconv flip-method={int(flip_method)} ! "
        f"video/x-raw, width=(int){int(display_width)}, height=(int){int(display_height)}, format=(string)BGRx ! "
        "videoconvert ! video/x-raw, format=(string)BGR ! "
        "appsink drop=1 max-buffers=1 sync=false"
    )


class CameraManager:
    def __init__(self, profile: dict):
        self._lock = threading.Lock()
        self._cap = None
        self._opened_backend = None
        self.capture_info = {}
        self.update_profile(profile)

    def update_profile(self, profile: dict):
        self.profile = profile or {}
        self.camera_cfg = dict((self.profile or {}).get("camera") or {})
        self.capture_info = {}
        self.close()

    def close(self):
        with self._lock:
            if self._cap is not None:
                try:
                    self._cap.release()
                except cv2.error as exc:
                    log.warning("Camera release failed: %s", exc)
            self._cap = None
            self._opened_backend = None

    def backend(self) -> str:
        return str(self.camera_cfg.get("backend") or "mock_frame").strip().lower()

    def preview_label(self) -> str:
        backend = self.backend()
        custom = str(self.camera_cfg.get("preview_source_label") or "").strip()
        if custom:
            return custom
        if backend == "jetson_csi_argus":
            sensor_model = csi_sensor_label(self.camera_cfg.get("sensor_model") or "GENERIC_CSI")
            return (
                f"Jetson CSI {sensor_model} sensor {int(self.camera_cfg.get('sensor_id') or 0)} "
                f"via Argus ({int(self.camera_cfg.get('capture_width') or 1920)}x"
                f"{int(self.camera_cfg.get('capture_height') or 1080)} @ "
                f"{int(self.camera_cfg.get('framerate') or 30)} fps)"
            )
        if backend == "opencv_device":
            return f"OpenCV device {int(self.camera_cfg.get('device_index') or 0)}"
        if backend == "image_file":
            return str(self.camera_cfg.get("source_path") or "Image file").strip() or "Image file"
        if backend == "video_file":
            return str(self.camera_cfg.get("source_path") or "Video file").strip() or "Video file"
        return "Development mock frame"

    def _open_live_capture_locked(self):
        backend = self.backend()
        if backend == "jetson_csi_argus":
            pipeline = build_argus_pipeline(
                sensor_id=self.camera_cfg.get("sensor_id") or 0,
                capture_width=self.camera_cfg.get("capture_width") or 1920,
                capture_height=self.camera_cfg.get("capture_height") or 1080,
                display_width=self.camera_cfg.get("display_width") or self.camera_cfg.get("capture_width") or 1280,
                display_height=self.camera_cfg.get("display_height") or self.camera_cfg.get("capture_height") or 720,
                framerate=self.camera_cfg.get("framerate") or 30,
                flip_method=self.camera_cfg.get("flip_method") or 0,
                aelock=bool(self.camera_cfg.get("aelock")),
            )
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            source_label = pipeline
        elif backend == "opencv_device":
            device_index = int(self.camera_cfg.get("device_index") or 0)
            cap = cv2.VideoCapture(device_index)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.camera_cfg.get("display_width") or 1280))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.camera_cfg.get("display_height") or 720))
            cap.set(cv2.CAP_PROP_FPS, float(self.camera_cfg.get("framerate") or 30))
            source_label = str(device_index)
        elif backend == "video_file":
            source_label = str(self.camera_cfg.get("source_path") or "").strip()
            if not source_label:
                raise RuntimeError("camera.source_path is required for video_file backend.")
            cap = cv2.VideoCapture(source_label)
        else:
            raise RuntimeError(f"Unsupported live camera backend: {backend}")
        if cap is None or not cap.isOpened():
            raise RuntimeError(f"Could not open camera backend '{backend}'.")
        self._cap = cap
        self._opened_backend = backend
        self.capture_info = {
            "backend": backend,
            "source": source_label,
            "opened_at": time.time(),
        }
        return cap

    def _ensure_live_capture_locked(self):
        backend = self.backend()
        if self._cap is not None and self._opened_backend == backend and self._cap.isOpened():
            return self._cap, False
        self.close()
        return self._open_live_capture_locked(), True

    def _warmup_locked(self, cap):
        warmup = max(0, int(self.camera_cfg.get("warmup_frames") or 0))
        for _ in range(warmup):
            ok, _frame = _read_latest(cap, max_flush=6)
            if not ok:
                cap.read()

    def _read_file_frame(self):
        path = str(self.camera_cfg.get("source_path") or "").strip()
        if not path:
            raise RuntimeError("camera.source_path is required for image_file backend.")
        frame = cv2.imread(path, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(f"Could not read image_file source: {path}")
        return frame

    def _mock_frame(self):
        width = int(self.camera_cfg.get("display_width") or 1280)
        height = int(self.camera_cfg.get("display_height") or 720)
        width = max(320, width)
        height = max(240, height)
        gradient = np.linspace(32, 224, width, dtype=np.uint8)
        frame = np.repeat(gradient[np.newaxis, :], height, axis=0)
        frame = cv2.merge([frame, np.flipud(frame), frame])
        cv2.rectangle(frame, (width // 5, height // 4), (width * 4 // 5, height * 3 // 4), (210, 210, 210), -1)
        cv2.putText(
            frame,
            "VisionDock Mock Inspection",
            (40, height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (20, 20, 20),
            2,
            cv2.LINE_AA,
        )
        return frame

    def capture_burst(self, burst_count=None):
        backend = self.backend()
        burst = int(burst_count or self.camera_cfg.get("burst_count") or 1)
        burst = max(1, burst)
        frames = []
        started = time.monotonic()

        if backend == "image_file":
            frame = self._read_file_frame()
            frames = [frame.copy() for _ in range(burst)]
        elif backend == "mock_frame":
            frame = self._mock_frame()
            frames = [frame.copy() for _ in range(burst)]
        else:
            with self._lock:
                cap, just_opened = self._ensure_live_capture_locked()
                if just_opened:
                    self._warmup_locked(cap)
                frame_skip = max(0, int(self.camera_cfg.get("frame_skip") or 0))
                for _index in range(burst):
                    ok, frame = _read_latest(cap)
                    if not ok or frame is None or frame.size == 0:
                        ok, frame = cap.read()
                    if not ok or frame is None or frame.size == 0:
                        self.close()
                        cap, _ = self._ensure_live_capture_locked()
                        self._warmup_locked(cap)
                        ok, frame = _read_latest(cap)
                        if not ok or frame is None or frame.size == 0:
                            raise RuntimeError(f"Camera backend '{backend}' returned empty frames.")
                    frames.append(frame.copy())
                    for _ in range(frame_skip):
                        cap.grab()

        duration_ms = int((time.monotonic() - started) * 1000)
        capture_meta = {
            "backend": backend,
            "preview_source": self.preview_label(),
            "sensor_model": self.camera_cfg.get("sensor_model") or "GENERIC_CSI",
            "frame_count": len(frames),
            "capture_duration_ms": duration_ms,
        }
        if frames:
            capture_meta["frame_shape"] = {
                "height": int(frames[0].shape[0]),
                "width": int(frames[0].shape[1]),
            }
        self.capture_info = capture_meta
        return frames, capture_meta
