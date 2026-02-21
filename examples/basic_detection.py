#!/usr/bin/env python3
"""
General AI Object Detection Example
Single camera detection with display
Supports: YOLOv8, YOLOv9, YOLOv11, RT-DETR, etc.
"""

import cv2
from ultralytics import YOLO
import argparse
import time
import logging

# Configure Industrial Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("arducam_ai.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("JetsonAI")

def get_camera(source_type, camera_id, width, height):
    """Helper to initialize and return a cv2.VideoCapture object"""
    if source_type == 'usb':
        cap = cv2.VideoCapture(camera_id)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    else:
        gst_pipeline = (
            f"nvarguscamerasrc sensor-id={camera_id} ! "
            f"video/x-raw(memory:NVMM), width={width}, height={height}, framerate=30/1 ! "
            f"nvvidconv flip-method=0 ! "
            f"video/x-raw, width={width}, height={height}, format=BGRx ! "
            f"videoconvert ! "
            f"video/x-raw, format=BGR ! appsink"
        )
        logger.debug(f"GStreamer Pipeline: {gst_pipeline}")
        cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
    return cap


def main():
    parser = argparse.ArgumentParser(description='Jetson Arducam AI Detection')
    parser.add_argument('--source-type', type=str, default='csi', choices=['csi', 'usb'], help='Camera type')
    parser.add_argument('--model', type=str, default='yolo11n.pt', help='Model path (.pt or .engine)')
    parser.add_argument('--camera', type=int, default=0, help='Camera ID')
    parser.add_argument('--width', type=int, default=1280, help='Camera width')
    parser.add_argument('--height', type=int, default=720, help='Camera height')
    parser.add_argument('--conf', type=float, default=0.25, help='Confidence threshold')
    parser.add_argument('--iou', type=float, default=0.45, help='IOU threshold')
    parser.add_argument('--imgsz', type=int, default=640, help='Inference image size')
    parser.add_argument('--display', action='store_true', help='Show display window')
    args = parser.parse_args()

    # Load YOLO Model
    logger.info(f"Loading model: {args.model}")
    model = YOLO(args.model)
    
    logger.info(f"Opening {args.source_type.upper()} camera {args.camera}...")
    cap = get_camera(args.source_type, args.camera, args.width, args.height)

    if not cap.isOpened():
        logger.error(f"Could not open {args.source_type} camera {args.camera} at startup.")
        if args.source_type == 'csi':
            logger.info("Tip: Check ribbon cable? Try running scripts/setup_cameras.sh")
        return
    
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    logger.info(f"Camera resolution established: {width}x{height} @ {fps}fps")
    
    frame_count = 0
    logger.info("Starting detection loop. Press 'q' to quit (if display is open).")
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Camera hardware connection lost! Attempting to auto-heal in 3 seconds...")
                cap.release()
                time.sleep(3)
                cap = get_camera(args.source_type, args.camera, args.width, args.height)
                if cap.isOpened():
                    logger.info("Camera successfully reconnected!")
                continue
            
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
                logger.info(f"Frame {frame_count} processed: {detections} objects detected")
            
            if args.display:
                cv2.putText(
                    annotated_frame,
                    f"Frame: {frame_count}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 255, 0),
                    2
                )
                cv2.imshow('Jetson Advanced Inference', annotated_frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    logger.info("Quit signal received from display.")
                    break
    
    except KeyboardInterrupt:
        logger.info("Stopping detection via KeyboardInterrupt (Ctrl+C)...")
    
    finally:
        if 'cap' in locals() and cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        logger.info(f"Shutdown complete. Total operational frames: {frame_count}")


if __name__ == '__main__':
    main()
