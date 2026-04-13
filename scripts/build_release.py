import os
import platform
import subprocess
import sys


def _env_truthy(name: str) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    return v in {"1", "true", "yes", "on"}


def _run_refactor_guards(repo_root: str) -> bool:
    """Run page-refactor guards in best-effort mode.

    Default: warning only (does not break build flow).
    Strict mode: set VISIONDOCK_RELEASE_STRICT_GUARDS=1 to fail fast.
    """
    guard_script = os.path.join(repo_root, "scripts", "check_page_refactor_guards.py")
    if not os.path.exists(guard_script):
        return True
    strict = _env_truthy("VISIONDOCK_RELEASE_STRICT_GUARDS")
    print("Running static page-refactor guards...")
    r = subprocess.run(["python3", guard_script], cwd=repo_root)
    if r.returncode == 0:
        print("Page-refactor guards: OK")
        return True
    msg = "Page-refactor guards failed."
    if strict:
        print(msg + " Strict mode enabled; aborting release build.")
        return False
    print(msg + " Continuing build (strict mode disabled).")
    print("Tip: export VISIONDOCK_RELEASE_STRICT_GUARDS=1 for fail-fast behavior.")
    return True


def build(skip_guards: bool = False):
    print("Starting VisionDock Production Build (PySide6)...")
    repo_root = os.path.join(os.path.dirname(__file__), "..")
    if skip_guards:
        print("Skipping page-refactor guards (--skip-guards).")
    else:
        if not _run_refactor_guards(repo_root):
            return

    system = platform.system()
    app_name = "VisionDock"

    cmd = [
        "pyinstaller",
        "--name", app_name,
        "--noconfirm",
        "--clean",
        "--add-data", "visiondock.svg:.",
        "--hidden-import", "PySide6",
        "--hidden-import", "PySide6.QtCore",
        "--hidden-import", "PySide6.QtGui",
        "--hidden-import", "PySide6.QtWidgets",
        "--hidden-import", "shiboken6",
        "--hidden-import", "cv2",
        "--hidden-import", "psutil",
        "--collect-all", "PySide6",
    ]

    if system == "Darwin":
        print("Target: macOS (Apple Silicon/Intel)")
        cmd.extend([
            "--windowed",
            "--icon", "visiondock.icns",
            "--osx-bundle-identifier", "com.visiondock.app",
        ])
    elif system == "Linux":
        print("Target: Linux (Jetson/Ubuntu)")
        cmd.append("--windowed")
    elif system == "Windows":
        print("Target: Windows")
        cmd.append("--windowed")

    gui_dir = os.path.join(os.path.dirname(__file__), "..", "gui")
    main_py = os.path.join(gui_dir, "main.py")
    cmd.append(main_py)

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=repo_root)

    print("\n" + "=" * 50)
    if system == "Darwin":
        print(f"Build output: dist/{app_name}.app")
    else:
        print(f"Build output: dist/{app_name}/")
    print("=" * 50)


if __name__ == "__main__":
    args = set(sys.argv[1:])
    build(skip_guards="--skip-guards" in args)
