#!/usr/bin/env python3
import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from runtime.camera_manager import CameraManager
from runtime.camera_profiles import detect_local_jetson_sensor_model, software_control_capabilities
from runtime.decision_engine import aggregate_inspection
from runtime.inference_runner import InferenceRunner
from runtime.output_driver import JetsonGPIOOutputDriver, MockOutputDriver
from runtime.input_driver import JetsonGPIOInputDriver
from runtime.model_registry import (
    activate_package,
    active_model_payload,
    get_active_package,
    get_previous_package,
    list_available_packages,
    rollback_active_package,
)
from runtime.profile_utils import load_inspection_profile
from runtime.storage import count_results, load_latest_result, load_result_records, persist_frame_image, persist_result


log = logging.getLogger("visiondock.runtime")


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RuntimeState:
    def __init__(self):
        self._lock = threading.Lock()
        self.profile = load_inspection_profile()
        self.driver = None
        self.camera_manager = None
        self.inference_runner = None
        self.input_driver = None
        self.runtime_status = "starting"
        self.service_started_at = _iso_now()
        self.inspection_count = count_results()
        self.last_result = load_latest_result()
        self.last_trigger_at = (self.last_result or {}).get("captured_at")
        self.last_error = None
        self.driver_backend = None
        self.camera_backend = None
        self.model_adapter = None
        self.active_model = {"name": "No model deployed", "version": "unassigned"}
        self.preview_source = "Not configured"
        self.detected_sensor_model = ""
        self._setup_driver()

    def _build_driver(self):
        backend = ((self.profile or {}).get("output_backend") or "mock").strip().lower()
        if backend == "jetson_gpio":
            return JetsonGPIOOutputDriver(self.profile)
        return MockOutputDriver(self.profile)

    def _update_profile_state(self):
        model_cfg = ((self.profile or {}).get("model") or {})
        self.active_model = {
            "name": model_cfg.get("name") or "No model deployed",
            "version": model_cfg.get("version") or "unassigned",
        }
        registry_model = active_model_payload()
        if registry_model:
            self.active_model = dict(registry_model)
        camera_cfg = ((self.profile or {}).get("camera") or {})
        self.preview_source = camera_cfg.get("preview_source_label") or "Not configured"

    def package_inventory(self):
        packages = list_available_packages()
        return {
            "packages": packages,
            "active_package": get_active_package(),
            "previous_package": get_previous_package(),
        }

    def _setup_driver(self):
        self._update_profile_state()
        self.camera_manager = CameraManager(self.profile)
        self.preview_source = self.camera_manager.preview_label()
        self.camera_backend = self.camera_manager.backend()
        self.detected_sensor_model = detect_local_jetson_sensor_model() if self.camera_backend == "jetson_csi_argus" else ""
        self.inference_runner = InferenceRunner(self.profile)
        self.model_adapter = self.inference_runner.adapter_name()
        requested_backend = ((self.profile or {}).get("output_backend") or "mock").strip().lower()
        try:
            driver = self._build_driver()
            driver.setup()
            self.driver = driver
            self.driver_backend = requested_backend
            if self.runtime_status == "starting":
                self.runtime_status = "idle"
            self.last_error = None
            
            # Setup hardware trigger if enabled
            if ((self.profile or {}).get("gpio") or {}).get("enabled"):
                self.input_driver = JetsonGPIOInputDriver(self.profile, self._hardware_trigger_callback)
                self.input_driver.setup()
            else:
                self.input_driver = None

        except Exception as exc:
            log.exception("Failed to initialize %s output backend", requested_backend)
            fallback = MockOutputDriver(self.profile)
            fallback.setup()
            fallback.apply_decision("fault")
            self.driver = fallback
            self.driver_backend = f"{requested_backend} -> mock"
            self.runtime_status = "fault"
            self.last_error = str(exc)

    def _hardware_trigger_callback(self, source="gpio_trigger"):
        """Called asynchronously by the hardware input driver."""
        with self._lock:
            if self.runtime_status == "busy":
                log.info("Hardware trigger ignored: runtime is busy.")
                return
        try:
            self.trigger(payload={"source": source})
        except Exception as e:
            log.error(f"Hardware trigger execution failed: {e}")

    def _safe_cleanup(self, resource, name: str):
        if resource is None:
            return
        try:
            resource.cleanup()
        except (AttributeError, RuntimeError, OSError) as exc:
            log.warning("Failed to cleanup %s: %s", name, exc)

    def reload(self):
        self._safe_cleanup(self.driver, "output driver")
        self._safe_cleanup(self.input_driver, "input driver")
        if self.camera_manager is not None:
            self.camera_manager.close()
        with self._lock:
            self.profile = load_inspection_profile()
            self._setup_driver()
            if self.runtime_status != "fault":
                self.runtime_status = "idle"
        return self.snapshot()

    def _next_inspection_id(self):
        return f"inspection-{self.inspection_count + 1:06d}"

    def _fault_payload(self, inspection_id: str, trigger_source: str, message: str):
        camera_name = (self.profile or {}).get("camera_name") or (self.profile or {}).get("station_name") or "Camera System"
        return {
            "inspection_id": inspection_id,
            "captured_at": _iso_now(),
            "camera_name": camera_name,
            "station_name": camera_name,
            "decision": "fault",
            "defect_classes": ["runtime_fault"],
            "confidence_summary": {"pass": 0.0, "fail": 1.0},
            "duration_ms": 0,
            "model_version": self.active_model.get("version") or "unassigned",
            "model_name": self.active_model.get("name") or "No model deployed",
            "package_id": self.active_model.get("package_id") or "",
            "adapter": self.model_adapter or "unconfigured",
            "source": trigger_source or "manual",
            "camera_backend": self.camera_backend or "",
            "frame_count": 0,
            "vote_summary": {"pass": 0, "fail": 0, "uncertain": 0},
            "frame_results": [{"frame_index": 0, "decision_hint": "fault", "defect_classes": ["runtime_fault"], "note": message}],
        }

    def trigger(self, payload=None):
        payload = payload or {}
        trigger_source = str(payload.get("source") or "manual").strip().lower() or "manual"
        inspection_cfg = ((self.profile or {}).get("inspection") or {})
        inspection_id = self._next_inspection_id()

        with self._lock:
            self.last_trigger_at = _iso_now()
            self.runtime_status = "busy"
            self.driver.apply_decision("busy")

        image_path = None
        try:
            burst_count = inspection_cfg.get("frame_vote_count") or ((self.profile or {}).get("camera") or {}).get("burst_count") or 1
            frames, camera_meta = self.camera_manager.capture_burst(burst_count=burst_count)
            self.preview_source = camera_meta.get("preview_source") or self.preview_source
            self.camera_backend = camera_meta.get("backend") or self.camera_backend
            inspection = self.inference_runner.inspect_burst(frames)
            self.model_adapter = inspection.get("adapter") or self.inference_runner.adapter_name()
            result = aggregate_inspection(
                inspection.get("frame_results") or [],
                profile=self.profile,
                inspection_id=inspection_id,
                camera_name=(self.profile or {}).get("camera_name") or (self.profile or {}).get("station_name"),
                trigger_source=trigger_source,
                active_model=self.active_model,
                camera_meta=camera_meta,
                adapter_name=self.model_adapter,
            )
            representative_index = int(result.get("representative_frame_index") or 0)
            should_save = (
                result.get("decision") == "fail" and bool(inspection_cfg.get("save_fail_frames", True))
            ) or (
                result.get("decision") == "uncertain" and bool(inspection_cfg.get("save_uncertain_frames", True))
            )
            if should_save and frames:
                representative_index = max(0, min(representative_index, len(frames) - 1))
                image_path = persist_frame_image(frames[representative_index], inspection_id, result.get("decision"))
            self.driver.apply_decision(result.get("decision") if result.get("decision") != "fault" else "fault")
            stored = persist_result(result, profile=self.profile, image_path=image_path)
        except Exception as exc:
            log.exception("Inspection trigger failed")
            self.driver.apply_decision("fault")
            stored = persist_result(
                self._fault_payload(inspection_id, trigger_source, str(exc)),
                profile=self.profile,
            )
            with self._lock:
                self.inspection_count += 1
                self.last_result = stored
                self.runtime_status = "fault"
                self.last_error = str(exc)
            return stored

        with self._lock:
            self.inspection_count += 1
            self.last_result = stored
            self.runtime_status = "fault" if stored.get("decision") == "fault" else "idle"
            self.last_error = "Inspection reported a runtime fault." if stored.get("decision") == "fault" else None
        log.info("Inspection trigger complete: decision=%s source=%s", stored.get("decision"), trigger_source)
        return stored

    def snapshot(self):
        profile = self.profile or {}
        gpio_cfg = profile.get("gpio") or {}
        camera_cfg = profile.get("camera") or {}
        configured_sensor = str(camera_cfg.get("sensor_model") or "GENERIC_CSI").strip().upper() or "GENERIC_CSI"
        detected_sensor = str(self.detected_sensor_model or "").strip().upper()
        sensor_match = None
        if detected_sensor and configured_sensor not in ("", "GENERIC_CSI"):
            sensor_match = detected_sensor == configured_sensor
        return {
            "status": "ok",
            "runtime_status": self.runtime_status,
            "camera_name": profile.get("camera_name"),
            "station_name": profile.get("camera_name") or profile.get("station_name"),
            "board_model": profile.get("board_model"),
            "output_backend": profile.get("output_backend"),
            "effective_output_backend": self.driver_backend,
            "trigger_mode": profile.get("trigger_mode"),
            "gpio_enabled": bool(gpio_cfg.get("enabled")),
            "service_started_at": self.service_started_at,
            "last_trigger_at": self.last_trigger_at,
            "inspection_count": self.inspection_count,
            "driver_state": getattr(self.driver, "state", {}),
            "last_result": self.last_result,
            "last_error": self.last_error,
            "active_model": dict(self.active_model),
            "preview_source": self.preview_source,
            "camera_backend": self.camera_backend,
            "camera_sensor_model": configured_sensor,
            "detected_sensor_model": detected_sensor,
            "camera_sensor_match": sensor_match,
            "camera_focuser_type": camera_cfg.get("focuser_type") or "none",
            "camera_controls": software_control_capabilities(camera_cfg.get("focuser_type") or "none"),
            "model_adapter": self.model_adapter,
        }


class InspectionRequestHandler(BaseHTTPRequestHandler):
    state = None

    def _reply(self, payload, code=200):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return None

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query or "")
        if path == "/health":
            state = self.state.snapshot()
            payload = {
                "status": state.get("status"),
                "runtime_status": state.get("runtime_status"),
                "camera_name": state.get("camera_name"),
                "board_model": state.get("board_model"),
                "output_backend": state.get("output_backend"),
                "effective_output_backend": state.get("effective_output_backend"),
                "gpio_enabled": state.get("gpio_enabled"),
                "camera_backend": state.get("camera_backend"),
                "camera_sensor_model": state.get("camera_sensor_model"),
                "detected_sensor_model": state.get("detected_sensor_model"),
                "camera_sensor_match": state.get("camera_sensor_match"),
                "model_adapter": state.get("model_adapter"),
                "driver_state": state.get("driver_state"),
                "last_error": state.get("last_error"),
                "service_started_at": state.get("service_started_at"),
                "inspection_count": state.get("inspection_count"),
                "last_trigger_at": state.get("last_trigger_at"),
            }
            self._reply(payload)
            return
        if path == "/state":
            self._reply(self.state.snapshot())
            return
        if path == "/config":
            self._reply(self.state.profile or {})
            return
        if path == "/models":
            inventory = self.state.package_inventory()
            self._reply({"status": "ok", **inventory})
            return
        if path == "/results":
            try:
                limit = int((query.get("limit") or ["60"])[0])
            except (TypeError, ValueError):
                limit = 60
            self._reply({"status": "ok", "records": load_result_records(limit=max(1, min(limit, 500)))})
            return
        self._reply({"error": "not_found"}, code=404)

    def do_DELETE(self):
        if self.path.startswith("/result/"):
            inspection_id = self.path.split("/")[-1]
            try:
                from runtime.storage import delete_result
                if delete_result(inspection_id):
                    self._reply({"status": "deleted", "inspection_id": inspection_id})
                else:
                    self._reply({"error": "not_found"}, code=404)
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                log.exception("Result deletion failed for inspection_id=%s", inspection_id)
                self._reply({"error": str(exc)}, code=500)
            return
        self._reply({"error": "not_found"}, code=404)

    def do_POST(self):
        if self.path == "/reload":
            snapshot = self.state.reload()
            self._reply({"status": "reloaded", "state": snapshot})
            return

        data = self._read_json()
        if data is None:
            self._reply({"error": "invalid_json"}, code=400)
            return

        if self.path == "/trigger":
            try:
                result = self.state.trigger(data)
            except Exception as exc:
                self._reply({"error": str(exc)}, code=400)
                return
            self._reply({"status": "ok", "result": result, "state": self.state.snapshot()})
            return

        if self.path == "/decision":
            decision = (data.get("decision") or "").strip().lower()
            try:
                result = self.state.driver.apply_decision(decision)
            except Exception as exc:
                self._reply({"error": str(exc)}, code=400)
                return
            self._reply(result)
            return

        if self.path == "/activate":
            package_id = str(data.get("package_id") or "").strip()
            if not package_id:
                self._reply({"error": "package_id_required"}, code=400)
                return
            try:
                package = activate_package(package_id)
            except Exception as exc:
                self._reply({"error": str(exc)}, code=400)
                return
            self.state._update_profile_state()
            self._reply({"status": "activated", "package": package, "state": self.state.snapshot()})
            return

        if self.path == "/rollback":
            try:
                package = rollback_active_package()
            except Exception as exc:
                self._reply({"error": str(exc)}, code=400)
                return
            self.state._update_profile_state()
            self._reply({"status": "rolled_back", "package": package, "state": self.state.snapshot()})
            return

        if self.path == "/signal":
            signal_name = (data.get("name") or "").strip().lower()
            enabled = bool(data.get("enabled"))
            try:
                self.state.driver.set_signal(signal_name, enabled)
            except Exception as exc:
                self._reply({"error": str(exc)}, code=400)
                return
            self._reply({"status": "ok", "state": getattr(self.state.driver, "state", {})})
            return

        self._reply({"error": "not_found"}, code=404)


def main():
    parser = argparse.ArgumentParser(description="VisionDock inspection runtime")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8787, help="Bind port")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    state = RuntimeState()
    InspectionRequestHandler.state = state
    server = ThreadingHTTPServer((args.host, int(args.port)), InspectionRequestHandler)
    log.info("Inspection runtime listening on http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Inspection runtime shutting down")
    finally:
        state._safe_cleanup(state.driver, "output driver")
        state._safe_cleanup(state.input_driver, "input driver")
        if state.camera_manager is not None:
            state.camera_manager.close()
        server.server_close()


if __name__ == "__main__":
    main()
