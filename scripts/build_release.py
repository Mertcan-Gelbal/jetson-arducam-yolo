import os
import sys
import platform
import subprocess

def build():
    print("Starting VisionDock Production Build...")
    
    # OS Detection
    system = platform.system()
    app_name = "VisionDock"
    
    # Base PyInstaller command
    cmd = [
        "pyinstaller",
        "--name", app_name,
        "--noconfirm",
        "--clean",
        "--add-data", "visiondock.svg:.",
        # Common hidden imports to ensure no missing modules in bundle
        "--hidden-import", "PyQt5.sip",
        "--hidden-import", "PyQt5.QtSvg",
        "--hidden-import", "cv2",
        "--hidden-import", "psutil"
    ]
    
    if system == "Darwin":
        print("Target: macOS (Apple Silicon/Intel)")
        cmd.extend([
            "--windowed", # No console on Mac
            # "--onefile", # Try onefile if bundle fails, but start with folder bundle
            "--icon", "visiondock.icns",
            # Ensure the .app bundle is created correctly
            "--osx-bundle-identifier", "com.visiondock.app"
        ])

    elif system == "Linux":
        print("Target: Linux (Jetson/Ubuntu)")
        # On Linux, usually keep console for debug or use --windowed if stable
        cmd.append("--windowed")
    elif system == "Windows":
        print("Target: Windows")
        cmd.append("--windowed")
        
    cmd.append("gui/main.py")
    
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)
    
    print("\n" + "="*50)
    if system == "Darwin":
        print(f"BINGO! Your application is ready at: dist/{app_name}.app")
        print("To open it: open dist/VisionDock.app")
    else:
        print(f"DONE! Your binary is in: dist/{app_name}/")
    print("="*50)

if __name__ == "__main__":
    build()
