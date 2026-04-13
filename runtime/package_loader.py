import json
import os
import re
import shutil
from datetime import datetime


MODEL_EXTENSIONS = (".engine", ".onnx", ".pt", ".pth", ".trt", ".torchscript", ".bin")
CONFIG_EXTENSIONS = (".yaml", ".yml", ".json", ".txt")
RUNTIME_EXTENSIONS = CONFIG_EXTENSIONS + (".py",)


def _visiondock_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), ".visiondock")
    os.makedirs(path, exist_ok=True)
    return path


def packages_root_dir() -> str:
    path = os.path.join(_visiondock_dir(), "model_packages")
    os.makedirs(path, exist_ok=True)
    return path


def package_metadata_path(package_dir: str) -> str:
    return os.path.join(package_dir, "metadata.json")


def sanitize_identifier(value: str, fallback="package") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = fallback
    clean = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    return clean or fallback


def load_package_metadata(package_dir: str):
    meta_path = package_metadata_path(package_dir)
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data["package_dir"] = package_dir
            data["metadata_path"] = meta_path
            return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def list_packages():
    rows = []
    root = packages_root_dir()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        package_dir = os.path.join(root, name)
        if not os.path.isdir(package_dir):
            continue
        metadata = load_package_metadata(package_dir)
        if metadata:
            rows.append(metadata)
    rows.sort(key=lambda item: str(item.get("exported_at") or ""), reverse=True)
    return rows


def get_package(package_id: str):
    target = str(package_id or "").strip()
    if not target:
        return None
    for package in list_packages():
        if str(package.get("package_id") or "") == target:
            return package
    return None


def _walk_workspace_files(workspace_dir: str):
    files = []
    root = os.path.abspath(workspace_dir)
    if not os.path.isdir(root):
        return files
    for base, _dirs, names in os.walk(root):
        for name in names:
            if name.startswith("."):
                continue
            abs_path = os.path.join(base, name)
            rel_path = os.path.relpath(abs_path, root)
            files.append(rel_path)
    files.sort()
    return files


def scan_workspace_candidates(workspace_dir: str):
    files = _walk_workspace_files(workspace_dir)
    model_artifacts = []
    label_files = []
    recipe_files = []
    runtime_files = []

    for rel_path in files:
        lower = rel_path.lower()
        ext = os.path.splitext(lower)[1]
        if ext in MODEL_EXTENSIONS:
            model_artifacts.append(rel_path)
        if ext in CONFIG_EXTENSIONS:
            if any(token in lower for token in ("label", "labels", "class", "classes", "names")):
                label_files.append(rel_path)
            if "recipe" in lower:
                recipe_files.append(rel_path)
        if ext in RUNTIME_EXTENSIONS:
            if any(token in lower for token in ("runtime", "deploy", "config", "infer", "inspection", "entry")):
                runtime_files.append(rel_path)

    return {
        "workspace_dir": os.path.abspath(workspace_dir),
        "files": files,
        "model_artifacts": model_artifacts,
        "label_files": label_files,
        "recipe_files": recipe_files,
        "runtime_files": runtime_files,
    }


def resolve_workspace_file(workspace_dir: str, relative_path: str) -> str:
    root = os.path.abspath(workspace_dir)
    rel = str(relative_path or "").strip()
    if not rel:
        return ""
    abs_path = os.path.abspath(os.path.join(root, rel))
    if not abs_path.startswith(root):
        raise ValueError("Selected file is outside the workspace directory.")
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(abs_path)
    return abs_path


def _copy_optional_file(src_path: str, dest_dir: str, dest_name: str):
    if not src_path:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    ext = os.path.splitext(src_path)[1]
    dest_path = os.path.join(dest_dir, dest_name + ext)
    shutil.copy2(src_path, dest_path)
    return dest_path


def _copy_runtime_artifact(src_path: str, export_root: str):
    if not src_path:
        return None
    ext = os.path.splitext(src_path)[1].lower()
    if ext == ".py":
        dest_dir = os.path.join(export_root, "runtime")
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, "runtime_entry.py")
    else:
        dest_dir = os.path.join(export_root, "config")
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, "runtime" + ext)
    shutil.copy2(src_path, dest_path)
    return dest_path


def build_package_from_workspace(
    workspace_name: str,
    workspace_dir: str,
    version: str,
    artifact_relpath: str,
    package_name=None,
    label_relpath=None,
    recipe_relpath=None,
    runtime_relpath=None,
    source_image=None,
    container_id=None,
):
    root = os.path.abspath(workspace_dir)
    if not os.path.isdir(root):
        raise FileNotFoundError(f"Workspace directory not found: {root}")
    if not str(version or "").strip():
        raise ValueError("Package version is required.")

    artifact_abs = resolve_workspace_file(root, artifact_relpath)
    label_abs = resolve_workspace_file(root, label_relpath) if label_relpath else ""
    recipe_abs = resolve_workspace_file(root, recipe_relpath) if recipe_relpath else ""
    runtime_abs = resolve_workspace_file(root, runtime_relpath) if runtime_relpath else ""

    package_name = package_name or workspace_name or "model-package"
    package_id = sanitize_identifier(f"{package_name}-{version}", fallback="model-package")
    export_root = os.path.join(packages_root_dir(), package_id)
    if os.path.exists(export_root):
        raise FileExistsError(f"Package already exists: {package_id}")

    artifact_dir = os.path.join(export_root, "artifacts")
    config_dir = os.path.join(export_root, "config")
    os.makedirs(artifact_dir, exist_ok=True)
    os.makedirs(config_dir, exist_ok=True)

    model_ext = os.path.splitext(artifact_abs)[1]
    model_dest = os.path.join(artifact_dir, "model" + model_ext)
    shutil.copy2(artifact_abs, model_dest)

    labels_dest = _copy_optional_file(label_abs, config_dir, "labels")
    recipe_dest = _copy_optional_file(recipe_abs, config_dir, "recipe")
    runtime_dest = _copy_runtime_artifact(runtime_abs, export_root) if runtime_abs else None

    exported_at = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata = {
        "package_id": package_id,
        "package_name": str(package_name),
        "version": str(version),
        "workspace_name": str(workspace_name or ""),
        "workspace_dir": root,
        "exported_at": exported_at,
        "model_name": os.path.basename(artifact_abs),
        "source_image": source_image or "",
        "container_id": (container_id or "")[:12],
        "files": {
            "model": os.path.relpath(model_dest, export_root),
            "labels": os.path.relpath(labels_dest, export_root) if labels_dest else None,
            "recipe": os.path.relpath(recipe_dest, export_root) if recipe_dest else None,
            "runtime": os.path.relpath(runtime_dest, export_root) if runtime_dest else None,
        },
        "runtime_kind": (
            "python_hook"
            if runtime_dest and str(runtime_dest).lower().endswith(".py")
            else "config"
            if runtime_dest
            else ""
        ),
        "source_files": {
            "model": artifact_relpath,
            "labels": label_relpath or None,
            "recipe": recipe_relpath or None,
            "runtime": runtime_relpath or None,
        },
    }
    meta_path = package_metadata_path(export_root)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    metadata["package_dir"] = export_root
    metadata["metadata_path"] = meta_path
    return metadata
