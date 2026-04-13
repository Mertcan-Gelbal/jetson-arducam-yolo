import importlib.util
import json
import logging
import os
import time

import cv2
import numpy as np

from runtime.model_registry import get_active_package


log = logging.getLogger("visiondock.runtime")


def _softmax(logits):
    arr = np.asarray(logits, dtype=np.float32)
    arr = arr - np.max(arr)
    exp = np.exp(arr)
    denom = np.sum(exp)
    if denom <= 0:
        return np.zeros_like(arr)
    return exp / denom


def _load_json_or_yaml(path: str):
    if not path or not os.path.isfile(path):
        return {}
    lower = path.lower()
    if lower.endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    if lower.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    return {}


def _load_labels(path: str):
    if not path or not os.path.isfile(path):
        return []
    lower = path.lower()
    if lower.endswith(".txt"):
        with open(path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    data = _load_json_or_yaml(path)
    if isinstance(data, dict):
        if isinstance(data.get("labels"), list):
            return [str(x) for x in data.get("labels") if str(x).strip()]
        if isinstance(data.get("names"), dict):
            return [str(data["names"][key]) for key in sorted(data["names"], key=lambda item: int(item))]
        if isinstance(data.get("names"), list):
            return [str(x) for x in data.get("names") if str(x).strip()]
    return []


def _resolve_package_path(package: dict, rel_path: str):
    if not package or not rel_path:
        return ""
    package_dir = package.get("package_dir") or ""
    if not package_dir:
        return ""
    return os.path.join(package_dir, rel_path)


class InferenceRunner:
    def __init__(self, profile: dict):
        self.profile = profile or {}
        self._loaded_package_id = None
        self._package = None
        self._runtime_config = {}
        self._labels = []
        self._adapter_name = "heuristic"
        self._model_session = None
        self._model_input_name = None
        self._model_input_hw = (224, 224)
        self._model_output_name = None
        self._python_hook_module = None
        self._ultralytics_model = None

    def update_profile(self, profile: dict):
        self.profile = profile or {}

    def adapter_name(self) -> str:
        return self._adapter_name

    def _clear_loaded_model(self):
        self._loaded_package_id = None
        self._package = None
        self._runtime_config = {}
        self._labels = []
        self._adapter_name = "heuristic"
        self._model_session = None
        self._model_input_name = None
        self._model_input_hw = (224, 224)
        self._model_output_name = None
        self._python_hook_module = None
        self._ultralytics_model = None

    def _load_active_package(self):
        package = get_active_package()
        package_id = str((package or {}).get("package_id") or "")
        if package_id == self._loaded_package_id:
            return

        self._clear_loaded_model()
        self._package = package
        self._loaded_package_id = package_id or None
        if not package:
            return

        files = package.get("files") or {}
        runtime_path = _resolve_package_path(package, files.get("runtime"))
        labels_path = _resolve_package_path(package, files.get("labels"))
        model_path = _resolve_package_path(package, files.get("model"))

        self._runtime_config = _load_json_or_yaml(runtime_path)
        self._labels = _load_labels(labels_path)
        if isinstance(self._runtime_config.get("labels"), list):
            self._labels = [str(x) for x in self._runtime_config.get("labels") if str(x).strip()]

        adapter = str(self._runtime_config.get("adapter") or "auto").strip().lower()
        if runtime_path.lower().endswith(".py"):
            self._load_python_hook(runtime_path)
            self._adapter_name = "python_hook"
            return
        if adapter in ("auto", "onnx_classification") and model_path.lower().endswith(".onnx"):
            self._load_onnx_session(model_path)
            self._adapter_name = "onnx_classification"
            return
        if adapter in ("auto", "ultralytics_detection") and model_path.lower().endswith((".pt", ".engine")):
            if self._load_ultralytics_model(model_path):
                self._adapter_name = "ultralytics_detection"
                return
        self._adapter_name = "heuristic"

    def _load_python_hook(self, runtime_path: str):
        module_name = f"visiondock_runtime_hook_{int(time.time() * 1000)}"
        spec = importlib.util.spec_from_file_location(module_name, runtime_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load runtime hook: {runtime_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "inspect_frames") and not hasattr(module, "inspect_frame"):
            raise RuntimeError("Runtime hook must expose inspect_frames() or inspect_frame().")
        self._python_hook_module = module

    def _load_onnx_session(self, model_path: str):
        import onnxruntime as ort

        providers = ["CPUExecutionProvider"]
        try:
            available = ort.get_available_providers()
        except Exception:
            available = []
        if "CUDAExecutionProvider" in available:
            providers.insert(0, "CUDAExecutionProvider")
        session = ort.InferenceSession(model_path, providers=providers)
        input_meta = session.get_inputs()[0]
        self._model_session = session
        self._model_input_name = input_meta.name
        shape = list(input_meta.shape or [])
        height = 224
        width = 224
        if len(shape) >= 4:
            if isinstance(shape[-2], int) and shape[-2] > 0:
                height = int(shape[-2])
            if isinstance(shape[-1], int) and shape[-1] > 0:
                width = int(shape[-1])
        input_size = self._runtime_config.get("input_size")
        if isinstance(input_size, int):
            height = width = int(input_size)
        elif isinstance(input_size, (list, tuple)) and len(input_size) >= 2:
            height = int(input_size[0])
            width = int(input_size[1])
        self._model_input_hw = (height, width)
        outputs = session.get_outputs()
        self._model_output_name = outputs[0].name if outputs else None

    def _load_ultralytics_model(self, model_path: str) -> bool:
        try:
            from ultralytics import YOLO
        except ImportError:
            return False
        self._ultralytics_model = YOLO(model_path)
        return True

    def _inspection_roi(self):
        roi = ((self.profile or {}).get("inspection") or {}).get("roi") or {}
        if not isinstance(roi, dict) or not roi.get("enabled"):
            return None
        return roi

    def _crop_roi(self, frame):
        roi = self._inspection_roi()
        if roi is None or frame is None or frame.size == 0:
            return frame
        height, width = frame.shape[:2]
        x = roi.get("x", 0.0)
        y = roi.get("y", 0.0)
        roi_w = roi.get("width", 1.0)
        roi_h = roi.get("height", 1.0)
        if 0 <= x <= 1 and 0 <= y <= 1 and 0 < roi_w <= 1 and 0 < roi_h <= 1:
            left = int(width * float(x))
            top = int(height * float(y))
            right = int(width * float(x + roi_w))
            bottom = int(height * float(y + roi_h))
        else:
            left = int(x)
            top = int(y)
            right = int(x + roi_w)
            bottom = int(y + roi_h)
        left = max(0, min(width - 1, left))
        top = max(0, min(height - 1, top))
        right = max(left + 1, min(width, right))
        bottom = max(top + 1, min(height, bottom))
        return frame[top:bottom, left:right]

    def _heuristic_thresholds(self):
        cfg = dict((((self.profile or {}).get("inspection") or {}).get("heuristic") or {}))
        cfg.setdefault("min_laplacian_var", 60.0)
        cfg.setdefault("min_mean_intensity", 55.0)
        cfg.setdefault("max_mean_intensity", 205.0)
        cfg.setdefault("min_edge_density", 0.01)
        cfg.setdefault("max_edge_density", 0.22)
        return cfg

    def _heuristic_infer(self, frame, frame_index: int):
        started = time.monotonic()
        crop = self._crop_roi(frame)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        mean_intensity = float(np.mean(gray))
        lap_var = float(cv2.Laplacian(gray, cv2.CV_32F).var())
        edges = cv2.Canny(gray, 80, 160)
        edge_density = float(np.mean(edges > 0))
        cfg = self._heuristic_thresholds()

        penalties = []
        if mean_intensity < float(cfg["min_mean_intensity"]):
            penalties.append(("underexposed_material", min(1.0, (cfg["min_mean_intensity"] - mean_intensity) / 80.0)))
        if mean_intensity > float(cfg["max_mean_intensity"]):
            penalties.append(("overexposed_material", min(1.0, (mean_intensity - cfg["max_mean_intensity"]) / 80.0)))
        if lap_var < float(cfg["min_laplacian_var"]):
            penalties.append(("blur_or_out_of_focus", min(1.0, (cfg["min_laplacian_var"] - lap_var) / 120.0)))
        if edge_density < float(cfg["min_edge_density"]):
            penalties.append(("low_texture_or_missing_sock", min(1.0, (cfg["min_edge_density"] - edge_density) * 12.0)))
        if edge_density > float(cfg["max_edge_density"]):
            penalties.append(("wrinkle_or_alignment_noise", min(1.0, (edge_density - cfg["max_edge_density"]) * 6.0)))

        defect_score = max([score for _name, score in penalties], default=0.12)
        defect_classes = [name for name, score in penalties if score >= 0.15]
        return {
            "frame_index": frame_index,
            "adapter": "heuristic",
            "defect_score": defect_score,
            "confidence": max(defect_score, 1.0 - defect_score),
            "defect_classes": defect_classes,
            "metrics": {
                "mean_intensity": round(mean_intensity, 3),
                "laplacian_var": round(lap_var, 3),
                "edge_density": round(edge_density, 5),
            },
            "inference_ms": int((time.monotonic() - started) * 1000),
        }

    def _onnx_preprocess(self, frame):
        crop = self._crop_roi(frame)
        target_h, target_w = self._model_input_hw
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (target_w, target_h), interpolation=cv2.INTER_AREA)
        tensor = resized.astype(np.float32) / 255.0
        mean = self._runtime_config.get("mean") or [0.485, 0.456, 0.406]
        std = self._runtime_config.get("std") or [0.229, 0.224, 0.225]
        if len(mean) == 3 and len(std) == 3:
            tensor = (tensor - np.asarray(mean, dtype=np.float32)) / np.asarray(std, dtype=np.float32)
        tensor = np.transpose(tensor, (2, 0, 1))[np.newaxis, ...]
        return tensor.astype(np.float32)

    def _onnx_infer(self, frame, frame_index: int):
        started = time.monotonic()
        tensor = self._onnx_preprocess(frame)
        outputs = self._model_session.run([self._model_output_name], {self._model_input_name: tensor})
        logits = np.asarray(outputs[0]).squeeze()
        if logits.ndim > 1:
            logits = logits.reshape(-1)
        probs = _softmax(logits)
        top_index = int(np.argmax(probs))
        labels = self._labels or [f"class_{idx}" for idx in range(len(probs))]
        top_label = labels[top_index] if top_index < len(labels) else f"class_{top_index}"

        fail_classes = {str(x) for x in (self._runtime_config.get("fail_classes") or []) if str(x).strip()}
        pass_classes = {str(x) for x in (self._runtime_config.get("pass_classes") or []) if str(x).strip()}
        pass_class = str(self._runtime_config.get("pass_class") or "").strip()
        if pass_class:
            pass_classes.add(pass_class)

        if fail_classes:
            defect_score = float(sum(float(probs[idx]) for idx, label in enumerate(labels[: len(probs)]) if label in fail_classes))
            defect_classes = [top_label] if top_label in fail_classes else []
        elif pass_classes:
            pass_score = float(sum(float(probs[idx]) for idx, label in enumerate(labels[: len(probs)]) if label in pass_classes))
            defect_score = max(0.0, min(1.0, 1.0 - pass_score))
            defect_classes = [] if top_label in pass_classes else [top_label]
        else:
            defect_score = max(0.0, min(1.0, 1.0 - float(probs[top_index])))
            defect_classes = [top_label] if defect_score >= 0.5 else []

        return {
            "frame_index": frame_index,
            "adapter": "onnx_classification",
            "defect_score": defect_score,
            "confidence": float(probs[top_index]),
            "defect_classes": defect_classes,
            "top_label": top_label,
            "scores": {
                labels[idx] if idx < len(labels) else f"class_{idx}": round(float(value), 6)
                for idx, value in enumerate(probs[: min(len(probs), 8)])
            },
            "inference_ms": int((time.monotonic() - started) * 1000),
        }

    def _ultralytics_infer(self, frame, frame_index: int):
        started = time.monotonic()
        imgsz = int(self._runtime_config.get("input_size") or 640)
        conf = float(self._runtime_config.get("confidence_threshold") or 0.25)
        result = self._ultralytics_model(frame, imgsz=imgsz, conf=conf, verbose=False)[0]
        names = result.names if hasattr(result, "names") else {}
        fail_classes = {str(x) for x in (self._runtime_config.get("fail_classes") or []) if str(x).strip()}
        detections = []
        defect_classes = []
        defect_score = 0.0
        boxes = getattr(result, "boxes", None)
        if boxes is not None:
            for cls_tensor, conf_tensor in zip(boxes.cls.tolist(), boxes.conf.tolist()):
                label = names.get(int(cls_tensor), str(int(cls_tensor))) if isinstance(names, dict) else str(int(cls_tensor))
                conf_value = float(conf_tensor)
                detections.append({"label": label, "confidence": round(conf_value, 6)})
                if not fail_classes or label in fail_classes:
                    defect_classes.append(label)
                    defect_score = max(defect_score, conf_value)
        return {
            "frame_index": frame_index,
            "adapter": "ultralytics_detection",
            "defect_score": defect_score,
            "confidence": defect_score if detections else 0.99,
            "defect_classes": sorted(set(defect_classes)),
            "detections": detections[:10],
            "inference_ms": int((time.monotonic() - started) * 1000),
        }

    def _python_hook_infer(self, frames):
        context = {
            "profile": self.profile,
            "package": self._package,
            "runtime_config": self._runtime_config,
            "labels": self._labels,
        }
        module = self._python_hook_module
        if hasattr(module, "inspect_frames"):
            payload = module.inspect_frames(frames=frames, context=context)
            if isinstance(payload, dict) and isinstance(payload.get("frame_results"), list):
                return [dict(item or {}) for item in payload.get("frame_results") or []]
            if isinstance(payload, list):
                return [dict(item or {}) for item in payload]
            raise RuntimeError("inspect_frames() must return a list or {'frame_results': [...]} payload.")
        results = []
        for index, frame in enumerate(frames):
            payload = module.inspect_frame(frame=frame, context=context)
            if not isinstance(payload, dict):
                raise RuntimeError("inspect_frame() must return a dict payload.")
            row = dict(payload)
            row.setdefault("frame_index", index)
            results.append(row)
        return results

    def inspect_burst(self, frames):
        self._load_active_package()
        if self._adapter_name == "python_hook":
            frame_results = self._python_hook_infer(frames)
            return {"adapter": self._adapter_name, "frame_results": frame_results}

        frame_results = []
        for index, frame in enumerate(frames or []):
            if self._adapter_name == "onnx_classification" and self._model_session is not None:
                frame_results.append(self._onnx_infer(frame, index))
            elif self._adapter_name == "ultralytics_detection" and self._ultralytics_model is not None:
                frame_results.append(self._ultralytics_infer(frame, index))
            else:
                frame_results.append(self._heuristic_infer(frame, index))
        return {"adapter": self._adapter_name, "frame_results": frame_results}
