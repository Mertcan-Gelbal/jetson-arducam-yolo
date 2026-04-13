from datetime import datetime


def _clamp_score(value, default=0.5):
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = float(default)
    return max(0.0, min(1.0, score))


def _decision_from_score(score: float, pass_threshold: float, fail_threshold: float) -> str:
    if score >= fail_threshold:
        return "fail"
    if score <= pass_threshold:
        return "pass"
    return "uncertain"


def aggregate_inspection(
    frame_results,
    profile=None,
    inspection_id=None,
    camera_name=None,
    trigger_source="manual",
    active_model=None,
    camera_meta=None,
    adapter_name=None,
):
    profile = profile or {}
    inspection_cfg = (profile or {}).get("inspection") or {}
    active_model = active_model or {}
    camera_meta = camera_meta or {}

    pass_threshold = _clamp_score(inspection_cfg.get("pass_threshold"), 0.35)
    fail_threshold = _clamp_score(inspection_cfg.get("fail_threshold"), 0.65)
    hard_fail_threshold = _clamp_score(inspection_cfg.get("hard_fail_threshold"), 0.9)
    if pass_threshold > fail_threshold:
        pass_threshold, fail_threshold = fail_threshold, pass_threshold

    normalized = []
    for index, item in enumerate(frame_results or []):
        row = dict(item or {})
        score = _clamp_score(row.get("defect_score"), 0.5)
        row["frame_index"] = int(row.get("frame_index", index))
        row["defect_score"] = score
        row["decision_hint"] = str(
            row.get("decision_hint") or _decision_from_score(score, pass_threshold, fail_threshold)
        ).strip().lower()
        row["confidence"] = _clamp_score(row.get("confidence"), max(score, 1.0 - score))
        row["defect_classes"] = [str(x) for x in (row.get("defect_classes") or []) if str(x).strip()]
        normalized.append(row)

    if not normalized:
        normalized = [{
            "frame_index": 0,
            "defect_score": 1.0,
            "decision_hint": "fault",
            "confidence": 1.0,
            "defect_classes": ["no_frames_captured"],
        }]

    fail_votes = sum(1 for row in normalized if row.get("decision_hint") == "fail")
    pass_votes = sum(1 for row in normalized if row.get("decision_hint") == "pass")
    uncertain_votes = len(normalized) - fail_votes - pass_votes
    top_row = max(normalized, key=lambda row: row.get("defect_score", 0.0))
    avg_fail_score = sum(row.get("defect_score", 0.0) for row in normalized) / max(1, len(normalized))

    if any(row.get("decision_hint") == "fault" for row in normalized):
        decision = "fault"
    elif top_row.get("defect_score", 0.0) >= hard_fail_threshold:
        decision = "fail"
    elif fail_votes > len(normalized) / 2:
        decision = "fail"
    elif pass_votes == len(normalized):
        decision = "pass"
    elif fail_votes == 0 and uncertain_votes > 0:
        decision = "uncertain"
    elif pass_votes > fail_votes and avg_fail_score < fail_threshold:
        decision = "pass"
    else:
        decision = "uncertain"

    defect_classes = []
    seen = set()
    for row in normalized:
        for defect in row.get("defect_classes") or []:
            if defect not in seen:
                seen.add(defect)
                defect_classes.append(defect)

    if decision == "pass":
        defect_classes = []
    elif not defect_classes:
        defect_classes = ["manual_review_required" if decision == "uncertain" else "inspection_defect"]

    confidence_summary = {
        "pass": round(max(0.0, min(1.0, 1.0 - avg_fail_score)), 4),
        "fail": round(avg_fail_score, 4),
    }

    duration_ms = int(camera_meta.get("capture_duration_ms") or 0)
    duration_ms += int(sum(int(row.get("inference_ms") or 0) for row in normalized))

    return {
        "inspection_id": inspection_id or datetime.now().strftime("inspection-%Y%m%d-%H%M%S"),
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "camera_name": camera_name or profile.get("camera_name") or profile.get("station_name") or "Camera System",
        "station_name": camera_name or profile.get("camera_name") or profile.get("station_name") or "Camera System",
        "profile_name": inspection_cfg.get("profile_name") or "default_project",
        "recipe_name": inspection_cfg.get("recipe_name") or "default_recipe",
        "product_label": inspection_cfg.get("product_label") or "Inspection target",
        "decision": decision,
        "defect_classes": defect_classes,
        "confidence_summary": confidence_summary,
        "duration_ms": duration_ms,
        "model_version": active_model.get("version") or "unassigned",
        "model_name": active_model.get("name") or "No model deployed",
        "package_id": active_model.get("package_id") or "",
        "adapter": adapter_name or "unknown",
        "source": trigger_source or "manual",
        "camera_backend": camera_meta.get("backend") or "",
        "frame_count": len(normalized),
        "frame_results": normalized,
        "representative_frame_index": int(top_row.get("frame_index", 0)),
        "vote_summary": {
            "pass": pass_votes,
            "fail": fail_votes,
            "uncertain": uncertain_votes,
        },
    }
