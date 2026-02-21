#!/bin/bash
# Universal Jetson AI Application Builder
# Wraps the core python logic into a unified executable via PyInstaller
# and registers a desktop shortcut.

echo -e "\033[1;36m====================================================\033[0m"
echo -e "\033[1;36m       JETSON AI STUDIO - APP COMPILER\033[0m"
echo -e "\033[1;36m====================================================\033[0m"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="JetsonAIStudio"
DESKTOP_ENTRY="/usr/share/applications/jetson-ai.desktop"

cd "$SCRIPT_DIR"

echo "[1/3] Ensuring PyInstaller is available..."
if ! command -v pyinstaller &> /dev/null; then
    sudo pip3 install pyinstaller
fi

echo "[2/3] Compiling App into a standalone binary..."
# Note: PyInstaller wraps the python dependencies, but relies on system cv2/PyQt5
# --windowed removes console terminal popups on launch
pyinstaller --name "$APP_NAME" \
            --windowed \
            --noconfirm \
            --clean \
            gui/main.py

echo "[3/3] Registering App on System Launcher..."
# Create custom .desktop entry
cat <<EOF | sudo tee $DESKTOP_ENTRY > /dev/null
[Desktop Entry]
Version=1.0
Name=Jetson AI Studio
Comment=Industrial Edge AI & Vision Dashboard
Exec=$SCRIPT_DIR/dist/$APP_NAME/$APP_NAME
Icon=camera-video
Terminal=false
Type=Application
Categories=Development;Engineering;Video;
Keywords=AI;YOLO;GStreamer;Vision;
EOF

sudo chmod +x $DESKTOP_ENTRY
sudo update-desktop-database > /dev/null 2>&1 || true

echo -e "\033[1;32m====================================================\033[0m"
echo -e "\033[1;32m BUILD SUCCESSFUL! \033[0m"
echo " You can now search for 'Jetson AI Studio' in your app launcher,"
echo " or run it via: $SCRIPT_DIR/dist/$APP_NAME/$APP_NAME"
echo ""
echo " Note: We recommend pinning it to your favorites dock!"
echo -e "\033[1;32m====================================================\033[0m"
