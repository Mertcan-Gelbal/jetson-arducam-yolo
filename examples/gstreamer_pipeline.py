#!/usr/bin/env python3
"""
Hardware-Accelerated GStreamer Pipeline for Jetson
Uses nvarguscamerasrc and hardware video decoding
"""

import cv2
import numpy as np
from ultralytics import YOLO
import argparse


def get_gst_compatibility():
    """Detect GStreamer compatibility mode from ENV or system"""
    compat = os.environ.get('GST_VERSION_COMPAT', 'modern')
    l4t_version = get_l4t_version()
    
    if l4t_version < 35:
        return 'legacy'
    return compat

def create_gstreamer_pipeline(
    sensor_id=0,
    capture_width=1920,
    capture_height=1080,
    display_width=1280,
    display_height=720,
    framerate=30,
    flip_method=0
):
    compat_mode = get_gst_compatibility()
    
    # Adapt format based on compatibility mode
    pixel_format = "NV12" # Standard for Jetson
    
    # Modern JetPack 6 (r36) / GStreamer 1.20 might require specific appsink settings
    appsink_extras = "drop=1"
    if compat_mode == 'modern':
        appsink_extras = "drop=1, max-buffers=1"
    
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM), "
        f"width=(int){capture_width}, height=(int){capture_height}, "
        f"format=(string){pixel_format}, framerate=(fraction){framerate}/1 ! "
        f"nvvidconv flip-method={flip_method} ! "
        f"video/x-raw, width=(int){display_width}, height=(int){display_height}, "
        f"format=(string)BGRx ! "
        f"videoconvert ! "
        f"video/x-raw, format=(string)BGR ! "
        f"appsink {appsink_extras}"
    )


def create_v4l2_pipeline(device="/dev/video0", width=1280, height=720):
    """
    Create V4L2 GStreamer pipeline (fallback if nvargus not available)
    
    Args:
        device: V4L2 device path
        width: Capture width
        height: Capture height
    
    Returns:
        GStreamer pipeline string
    """
    
    return (
        f"v4l2src device={device} ! "
        f"video/x-raw, width=(int){width}, height=(int){height} ! "
        f"videoconvert ! "
        f"video/x-raw, format=(string)BGR ! "
        f"appsink drop=1"
    )


def main():
    parser = argparse.ArgumentParser(description='GStreamer Hardware-Accelerated Detection')
    parser.add_argument('--sensor-id', type=int, default=0, help='Camera sensor ID')
    parser.add_argument('--capture-width', type=int, default=1920, help='Capture width')
    parser.add_argument('--capture-height', type=int, default=1080, help='Capture height')
    parser.add_argument('--display-width', type=int, default=1280, help='Display width')
    parser.add_argument('--display-height', type=int, default=720, help='Display height')
    parser.add_argument('--framerate', type=int, default=30, help='Camera framerate')
    parser.add_argument('--flip', type=int, default=0, help='Flip method (0-5)')
    parser.add_argument('--v4l2', action='store_true', help='Use V4L2 instead of nvargus')
    parser.add_argument('--device', type=str, default='/dev/video0', help='V4L2 device')
    parser.add_argument('--model', type=str, default='yolov8n.pt', help='Model path')
    parser.add_argument('--conf', type=float, default=0.25, help='Confidence threshold')
    parser.add_argument('--display', action='store_true', help='Display results')
    args = parser.parse_args()
    
    # Create GStreamer pipeline
    if args.v4l2:
        print(f"Using V4L2 device: {args.device}")
        pipeline = create_v4l2_pipeline(
            device=args.device,
            width=args.display_width,
            height=args.display_height
        )
    else:
        print(f"Using nvargus sensor ID: {args.sensor_id}")
        pipeline = create_gstreamer_pipeline(
            sensor_id=args.sensor_id,
            capture_width=args.capture_width,
            capture_height=args.capture_height,
            display_width=args.display_width,
            display_height=args.display_height,
            framerate=args.framerate,
            flip_method=args.flip
        )
    
    print("\nGStreamer Pipeline:")
    print(pipeline)
    print()
    
    # Load YOLOv8 model
    print(f"Loading model: {args.model}")
    model = YOLO(args.model)
    
    # Open camera with GStreamer
    print("Opening camera...")
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    
    if not cap.isOpened():
        print("Error: Could not open camera with GStreamer pipeline")
        print("\nTroubleshooting:")
        print("1. Check if camera is connected: ls /dev/video*")
        print("2. Test with gst-launch:")
        if not args.v4l2:
            print(f"   gst-launch-1.0 nvarguscamerasrc sensor-id={args.sensor_id} ! fakesink")
        else:
            print(f"   gst-launch-1.0 v4l2src device={args.device} ! fakesink")
        print("3. Check nvargus daemon: sudo systemctl status nvargus-daemon")
        return
    
    print("Camera opened successfully!")
    
    # Performance metrics
    import time
    frame_count = 0
    start_time = time.time()
    fps_update_interval = 30
    
    try:
        print("Starting detection... Press 'q' to quit\n")
        
        while True:
            ret, frame = cap.read()
            
            if not ret:
                print("Error: Failed to capture frame")
                break
            
            # Run inference
            results = model(frame, conf=args.conf, verbose=False)
            
            # Get annotated frame
            annotated_frame = results[0].plot()
            
            frame_count += 1
            
            # Calculate and display FPS
            if frame_count % fps_update_interval == 0:
                elapsed_time = time.time() - start_time
                fps = fps_update_interval / elapsed_time
                detections = len(results[0].boxes)
                
                print(f"Frame {frame_count}: {fps:.1f} FPS | {detections} detections")
                
                start_time = time.time()
            
            if args.display:
                # Add FPS overlay
                elapsed = time.time() - start_time + 0.001
                instant_fps = 1.0 / elapsed if elapsed > 0 else 0
                
                cv2.putText(
                    annotated_frame,
                    f"FPS: {instant_fps:.1f}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 255, 0),
                    2
                )
                
                cv2.imshow('GStreamer + YOLOv8', annotated_frame)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    
    except KeyboardInterrupt:
        print("\nStopping...")
    
    finally:
        total_time = time.time() - start_time
        avg_fps = frame_count / total_time if total_time > 0 else 0
        
        cap.release()
        cv2.destroyAllWindows()
        
        print(f"\nStatistics:")
        print(f"  Total frames: {frame_count}")
        print(f"  Average FPS: {avg_fps:.2f}")
        print(f"  Total time: {total_time:.2f}s")


if __name__ == '__main__':
    main()
