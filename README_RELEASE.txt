# VisionDock Production Release

Your application has been successfully packaged into a native macOS Application Bundle.

## Location
You can find your ready-to-use application here:
`/Users/mertcangelbal/Documents/jetson-arducam/dist/VisionDock.app`

## How to use:
1. Open Finder and go to the `dist` folder.
2. Double-click `VisionDock.app`.
3. (Optional) Drag and drop it into your 'Applications' folder.

## Troubleshooting:
If the application shows a "damaged" warning because it's unsigned, run this command in terminal:
`xattr -cr /Users/mertcangelbal/Documents/jetson-arducam/dist/VisionDock.app`

## For Linux (Jetson):
Run the installer script on your Jetson device:
`./scripts/create_app_installer.sh`
This will register VisionDock with the system menu and your custom logo.
