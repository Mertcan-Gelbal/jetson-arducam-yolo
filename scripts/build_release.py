import os
import platform
import subprocess

def build():
    print("Starting VisionDock Production Build (PySide6)...")

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
    subprocess.run(cmd, cwd=os.path.join(os.path.dirname(__file__), ".."))

    print("\n" + "=" * 50)
    if system == "Darwin":
        print(f"Build output: dist/{app_name}.app")
    else:
        print(f"Build output: dist/{app_name}/")
    print("=" * 50)


if __name__ == "__main__":
    build()
