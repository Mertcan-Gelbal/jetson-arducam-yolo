# Medical Sock Camera Architecture

This product should use one camera execution method in production:

- `Jetson CSI (IMX519) -> libargus / nvarguscamerasrc -> OpenCV/GStreamer consumer`

The decision is based on the supported Jetson camera stack and the IMX519 integration path used by Arducam.

## Production Rule

- For the sock inspection station, keep the IMX519 camera owned by the Jetson inspection runtime.
- Use `nvarguscamerasrc` for capture, not a USB-style `v4l2src + ffmpeg` chain.
- Treat manual RTSP/HTTP URLs as an advanced fallback for debugging only.

## Why This Is The Correct Path

NVIDIA documents the Jetson CSI capture path around Argus and `nvarguscamerasrc`. The Jetson guide also separates USB camera support around `v4l2src`, which is a different device class and not the preferred production route for CSI Bayer sensors.

Arducam's Jetson IMX519 documentation and example focus repository also assume the native Jetson CSI camera stack and the IMX519 motor-focus control flow.

## Product Consequences

- Remove USB/YUYV/FFmpeg capture from the main production flow.
- Keep the runtime as the single owner of the CSI device.
- Run trigger-based burst capture inside the runtime, then execute inference and LED/GPIO output from the same process.
- Leave preview streaming as optional and secondary to inspection reliability.

## Sources

- NVIDIA Jetson Linux Developer Guide, Accelerated GStreamer:
  [docs.nvidia.com](https://docs.nvidia.com/jetson/archives/r38.2/DeveloperGuide/SD/Multimedia/AcceleratedGstreamer.html)
- Arducam IMX519 Jetson documentation:
  [docs.arducam.com](https://docs.arducam.com/Nvidia-Jetson-Camera/Native-Camera/imx519/)
- Arducam IMX519 focus example for Jetson:
  [github.com/ArduCAM/Jetson_IMX519_Focus_Example](https://github.com/ArduCAM/Jetson_IMX519_Focus_Example)
