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
    parser.add_argument('--camera', type=int, default=0, help='Camera device ID')
    parser.add_argument('--model', type=str, default='yolov8n.pt', help='Model path')
    parser.add_argument('--conf', type=float, default=0.25, help='Confidence threshold')
    parser.add_argument('--iou', type=float, default=0.45, help='IOU threshold')
    parser.add_argument('--imgsz', type=int, default=640, help='Input size')
    parser.add_argument('--display', action='store_true', help='Display results')
    args = parser.parse_args()

    # Load YOLOv8 model
    print(f"Loading model: {args.model}")
    model = YOLO(args.model)
    
    # Open camera
    print(f"Opening camera {args.camera}")
    cap = cv2.VideoCapture(args.camera)
    
    if not cap.isOpened():
        print(f"Error: Could not open camera {args.camera}")
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
