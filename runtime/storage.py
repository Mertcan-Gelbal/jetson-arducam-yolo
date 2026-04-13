import json
import os
from datetime import datetime


def _visiondock_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), ".visiondock")
    os.makedirs(path, exist_ok=True)
    return path


def results_root_dir() -> str:
    path = os.path.join(_visiondock_dir(), "results")
    os.makedirs(path, exist_ok=True)
    return path


def results_records_dir() -> str:
    path = os.path.join(results_root_dir(), "records")
    os.makedirs(path, exist_ok=True)
    return path


def results_images_dir() -> str:
    path = os.path.join(results_root_dir(), "images")
    os.makedirs(path, exist_ok=True)
    return path


def results_index_path() -> str:
    return os.path.join(results_root_dir(), "index.jsonl")


def latest_result_path() -> str:
    return os.path.join(results_root_dir(), "latest.json")


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if hasattr(value, "tolist"):
        try:
            return value.tolist()
        except Exception:
            pass
    return value


def _normalize_record(result: dict, profile=None, image_path=None) -> dict:
    result = dict(result or {})
    profile = profile or {}
    inspection_cfg = profile.get("inspection") or {}
    model_cfg = profile.get("model") or {}
    inspection_id = str(result.get("inspection_id") or _now_id()).strip() or _now_id()
    record = {
        "inspection_id": inspection_id,
        "station_name": result.get("station_name") or profile.get("station_name") or "Inspection Station",
        "captured_at": result.get("captured_at") or datetime.now().astimezone().isoformat(timespec="seconds"),
        "decision": str(result.get("decision") or "unknown").strip().lower(),
        "defect_classes": _json_safe(list(result.get("defect_classes") or [])),
        "confidence_summary": _json_safe(dict(result.get("confidence_summary") or {})),
        "model_version": result.get("model_version") or model_cfg.get("version") or "unassigned",
        "model_name": result.get("model_name") or model_cfg.get("name") or "No model deployed",
        "recipe_name": result.get("recipe_name") or inspection_cfg.get("recipe_name") or "default",
        "duration_ms": int(result.get("duration_ms") or 0),
        "image_path": os.path.abspath(image_path) if image_path else result.get("image_path"),
        "source": result.get("source") or "manual",
        "camera_backend": result.get("camera_backend") or "",
        "package_id": result.get("package_id") or "",
        "adapter": result.get("adapter") or "",
        "frame_count": int(result.get("frame_count") or 0),
        "vote_summary": _json_safe(dict(result.get("vote_summary") or {})),
        "frame_results": _json_safe(list(result.get("frame_results") or [])),
    }
    return record


def persist_result(result: dict, profile=None, image_path=None) -> dict:
    record = _normalize_record(result, profile=profile, image_path=image_path)
    record_path = os.path.join(results_records_dir(), f"{record['inspection_id']}.json")
    record["record_path"] = record_path

    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)

    with open(results_index_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    with open(latest_result_path(), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)

    # Prune old results proactively to prevent disk exhaustion
    try:
        prune_old_results(max_count=1500)
    except Exception as e:
        import logging
        logging.getLogger("visiondock.runtime").warning(f"Failed to prune old results: {e}")

    return record

def prune_old_results(max_count=1500):
    """Deletes old records and their associated images if disk limit exceeded."""
    rows = load_result_records()
    if len(rows) <= max_count:
        return
    old_rows = rows[max_count:]
    for row in old_rows:
        img_path = row.get("image_path")
        if img_path and os.path.exists(img_path):
            try:
                os.remove(img_path)
            except OSError:
                pass
        delete_result(row.get("inspection_id"))


def load_latest_result():
    path = latest_result_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_result_records(limit=None):
    """Load inspection result records newest-first.

    Primary source: ``index.jsonl`` (one JSON line per record, appended on
    each inspection cycle).  If the index is missing or yields zero rows the
    function falls back to scanning the ``records/`` directory directly so
    that records written by external tools are still visible.

    Deduplication: only the *last* occurrence of each inspection_id is kept
    so that re-triggered or rewritten records do not appear twice.
    """
    rows = _load_from_index()
    if not rows:
        rows = _load_from_records_dir()
    # Deduplicate: keep last entry per inspection_id (most recent write wins)
    seen: dict = {}
    for row in rows:
        iid = str(row.get("inspection_id") or "").strip()
        seen[iid or id(row)] = row
    deduped = list(seen.values())
    # Sort newest-first by captured_at, fall back to inspection_id string order
    deduped.sort(
        key=lambda r: str(r.get("captured_at") or r.get("inspection_id") or ""),
        reverse=True,
    )
    if limit is not None:
        try:
            deduped = deduped[: max(0, int(limit))]
        except (TypeError, ValueError):
            pass
    return deduped


def _load_from_index() -> list:
    """Read index.jsonl in file order (oldest-first).

    Returning oldest-first is intentional: the `load_result_records` dedup
    loop iterates this list and overwrites ``seen[id]`` with each row, so the
    *last* row wins — which is the newest entry for that inspection_id.
    Final sorting happens in `load_result_records`.
    """
    path = results_index_path()
    if not os.path.exists(path):
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        return []
    return rows  # oldest-first; do NOT reverse here


def _load_from_records_dir() -> list:
    rdir = results_records_dir()
    rows = []
    try:
        names = sorted(os.listdir(rdir), reverse=True)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        fpath = os.path.join(rdir, name)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                rows.append(data)
        except Exception:
            continue
    return rows

def delete_result(inspection_id: str) -> bool:
    """Remove a single result record and its per-record JSON file.

    The index.jsonl is rebuilt from the records/ directory after deletion so
    it stays consistent.  Returns True if a record was removed.
    """
    target = str(inspection_id or "").strip()
    if not target:
        return False
    removed = False
    record_path = os.path.join(results_records_dir(), f"{target}.json")
    if os.path.isfile(record_path):
        try:
            os.remove(record_path)
            removed = True
        except OSError:
            pass
    # Rebuild index from remaining records so it stays consistent
    _rebuild_index()
    # Remove latest.json if it pointed to the deleted record
    lpath = latest_result_path()
    try:
        with open(lpath, "r", encoding="utf-8") as f:
            latest = json.load(f)
        if str(latest.get("inspection_id") or "") == target:
            os.remove(lpath)
    except Exception:
        pass
    return removed


def _rebuild_index():
    """Rewrite index.jsonl from the records/ directory (used after deletion)."""
    rows = _load_from_records_dir()
    rows.sort(
        key=lambda r: str(r.get("captured_at") or r.get("inspection_id") or ""),
    )
    try:
        with open(results_index_path(), "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def count_results() -> int:
    """Return total number of inspection results stored in the index."""
    path = results_index_path()
    if not os.path.exists(path):
        return 0
    count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
    except Exception:
        return 0
    return count


def persist_frame_image(frame, inspection_id: str, tag="inspection"):
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required to persist inspection images.") from exc

    safe_tag = "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in str(tag or "inspection"))
    filename = f"{str(inspection_id or _now_id()).strip() or _now_id()}-{safe_tag}.jpg"
    path = os.path.join(results_images_dir(), filename)
    ok = cv2.imwrite(path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise RuntimeError(f"Could not write inspection image: {path}")
    return path
