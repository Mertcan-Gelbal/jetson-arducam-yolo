#!/usr/bin/env python3
"""
General AI Object Detection Example
Single camera detection with display
Supports: YOLOv8, YOLOv9, YOLOv11, RT-DETR, etc.
"""

import cv2
from ultralytics import YOLO
import argparse


def main():
    parser = argparse.ArgumentParser(description='YOLOv8 Basic Detection')
    parser.add_argument('--source-type', type=str, default='csi', choices=['csi', 'usb'], help='Camera type: csi or usb')
    parser.add_argument('--width', type=int, default=1280, help='Camera width')
    parser.add_argument('--height', type=int, default=720, help='Camera height')
    args = parser.parse_args()

    # Load YOLO Model
    print(f"Loading model: {args.model}")
    model = YOLO(args.model)
    
    # Initialize Camera
    print(f"Opening {args.source_type.upper()} camera {args.camera}...")
    
    if args.source_type == 'usb':
        # USB Camera: Standard V4L2
        cap = cv2.VideoCapture(args.camera)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    else:
        # CSI Camera: GStreamer Pipeline (Hardware Accelerated)
        gst_pipeline = (
            f"nvarguscamerasrc sensor-id={args.camera} ! "
            f"video/x-raw(memory:NVMM), width={args.width}, height={args.height}, framerate=30/1 ! "
            f"nvvidconv flip-method=0 ! "
            f"video/x-raw, width={args.width}, height={args.height}, format=BGRx ! "
            f"videoconvert ! "
            f"video/x-raw, format=BGR ! appsink"
        )
        print(f"Pipeline: {gst_pipeline}")
        cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)

    if not cap.isOpened():
        print(f"Error: Could not open {args.source_type} camera {args.camera}")
        if args.source_type == 'csi':
            print("Tip: Check ribbon cable? Try running scripts/setup_cameras.sh")
        else:
            print("Tip: Check USB connection? Try lsusage")
        return
    
    # Get camera properties
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    print(f"Camera resolution: {width}x{height} @ {fps}fps")
    
    frame_count = 0
    
    print("Starting detection... Press 'q' to quit")
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error: Failed to capture frame")
                break
            
            # Run inference
            results = model(
                frame,
                conf=args.conf,
                iou=args.iou,
                imgsz=args.imgsz,
                verbose=False
            )
            
            # Get annotated frame
            annotated_frame = results[0].plot()
            
            # Display FPS
            frame_count += 1
            if frame_count % 30 == 0:
                detections = len(results[0].boxes)
                print(f"Frame {frame_count}: {detections} objects detected")
            
            if args.display:
                # Add frame counter
                cv2.putText(
                    annotated_frame,
                    f"Frame: {frame_count}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 255, 0),
                    2
                )
                
                cv2.imshow('YOLOv8 Detection', annotated_frame)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
    
    except KeyboardInterrupt:
        print("\nStopping detection...")
    
    finally:
        cap.release()
        cv2.destroyAllWindows()
        print(f"Total frames processed: {frame_count}")


if __name__ == '__main__':
    main()
