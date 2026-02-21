#!/usr/bin/env python3
"""
Advanced Industrial AI Analytics for Jetson
Features:
1. Object Tracking (ByteTrack)
2. Spatial Analytics (Region of Interest / Zone Counting)
3. Headless Processing with Hardware-Accelerated UDP Streaming (H.264)
"""

import cv2
import numpy as np
from ultralytics import YOLO
import argparse
import time

def create_gstreamer_source(sensor_id=0, width=1280, height=720, fps=30):
    """CSI Camera Input Pipeline (NVMM Memory)"""
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM), width={width}, height={height}, framerate={fps}/1 ! "
        f"nvvidconv flip-method=0 ! "
        f"video/x-raw, width={width}, height={height}, format=BGRx ! "
        f"videoconvert ! "
        f"video/x-raw, format=BGR ! appsink drop=True"
    )

def create_gstreamer_sink(host="127.0.0.1", port=5000, width=1280, height=720, fps=30):
    """
    Hardware-Accelerated H.264 UDP Streaming Output Pipeline.
    Takes BGR image from OpenCV -> Converts to NV12 -> Encodes to H.264 via NVENC -> Streams over UDP.
    """
    return (
        f"appsrc ! "
        f"video/x-raw, format=BGR, width={width}, height={height}, framerate={fps}/1 ! "
        f"nvvidconv ! video/x-raw(memory:NVMM), format=NV12 ! "
        f"nvv4l2h264enc insert-sps-pps=true bitrate=4000000 ! "
        f"h264parse ! rtph264pay pt=96 config-interval=1 ! "
        f"udpsink host={host} port={port} sync=false async=false"
    )

def is_inside_polygon(point, polygon):
    """Check if (x, y) center point is inside the given polygon"""
    return cv2.pointPolygonTest(polygon, point, False) >= 0

def main():
    parser = argparse.ArgumentParser(description='Jetson Industrial Analytics')
    parser.add_argument('--model', type=str, default='yolo11n.pt', help='Model path')
    parser.add_argument('--source-type', type=str, default='csi', choices=['csi', 'usb'])
    parser.add_argument('--camera', type=int, default=0, help='Camera ID')
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--conf', type=float, default=0.3)
    parser.add_argument('--stream-out', action='store_true', help='Enable UDP H.264 Streaming')
    parser.add_argument('--stream-ip', type=str, default='127.0.0.1', help='Destination IP for UDP stream')
    parser.add_argument('--stream-port', type=int, default=5000, help='Destination port for UDP stream')
    parser.add_argument('--display', action='store_true', help='Show local display window')
    args = parser.parse_args()

    print(f"Loading Model: {args.model} for Tracking...")
    model = YOLO(args.model)

    # 1. Initialize Camera Input
    if args.source_type == 'csi':
        pipe_in = create_gstreamer_source(args.camera, args.width, args.height)
        cap = cv2.VideoCapture(pipe_in, cv2.CAP_GSTREAMER)
    else:
        cap = cv2.VideoCapture(args.camera)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    if not cap.isOpened():
        print(f"Error: Cannot open {args.source_type} camera {args.camera}")
        return

    # 2. Initialize Streaming Output
    video_writer = None
    if args.stream_out:
        pipe_out = create_gstreamer_sink(args.stream_ip, args.stream_port, args.width, args.height)
        print(f"\nStarting Hardware UDP Stream at: udp://@{args.stream_ip}:{args.stream_port}")
        print("To view remotely, open VLC Network Stream: udp://@<jetson_ip>:5000\n")
        video_writer = cv2.VideoWriter(pipe_out, cv2.CAP_GSTREAMER, 0, 30, (args.width, args.height))

    # 3. Define the Analytics Zone (Region of Interest Polygon)
    # Automatically scales based on camera resolution (e.g. a trapezoid in the middle)
    zone_pts = np.array([
        [int(args.width * 0.2), int(args.height * 0.9)],
        [int(args.width * 0.4), int(args.height * 0.4)],
        [int(args.width * 0.6), int(args.height * 0.4)],
        [int(args.width * 0.8), int(args.height * 0.9)]
    ], np.int32)
    zone_pts = zone_pts.reshape((-1, 1, 2))

    frame_count = 0
    start_time = time.time()
    
    # State tracking for objects currently in the zone
    objects_in_zone = set()
    total_entered_zone = 0

    print("Industrial Analytics Running... Press 'q' to stop.")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to grab frame.")
                break

            # Run inference WITH Object Tracking (ByteTrack)
            results = model.track(frame, persist=True, conf=args.conf, verbose=False, tracker="bytetrack.yaml")
            
            # Create a clean canvas for our analytics drawing
            annotated_frame = frame.copy()
            current_frame_objects = set()

            # Process tracked objects
            if results[0].boxes.id is not None:
                boxes = results[0].boxes.xyxy.cpu().numpy()
                track_ids = results[0].boxes.id.int().cpu().numpy()
                classes = results[0].boxes.cls.int().cpu().numpy()

                for box, track_id, cls in zip(boxes, track_ids, classes):
                    x1, y1, x2, y2 = map(int, box)
                    cx, cy = int((x1 + x2) / 2), int(y2)  # Use bottom-center for foot placement inside zone
                    
                    # Highlight the tracked object
                    color = (0, 255, 0)
                    
                    # Check Zone Intrusion
                    is_in_zone = is_inside_polygon((cx, cy), zone_pts)
                    if is_in_zone:
                        color = (0, 0, 255) # Red if inside restricted zone
                        current_frame_objects.add(track_id)
                        
                        # Count unique entries
                        if track_id not in objects_in_zone:
                            total_entered_zone += 1
                            print(f"[ALERT] Object ID {track_id} (Class {cls}) entered the restricted zone!")

                    # Draw Object Bounding Box and ID
                    cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(annotated_frame, f"ID:{track_id} C:{cls}", (x1, y1 - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
                    cv2.circle(annotated_frame, (cx, cy), 5, color, -1)

            # Update zone state memory (to track when they leave)
            objects_in_zone = current_frame_objects

            # Draw the Analytics Zone
            zone_color = (0, 0, 255) if len(objects_in_zone) > 0 else (255, 0, 0)
            cv2.polylines(annotated_frame, [zone_pts], isClosed=True, color=zone_color, thickness=2)
            
            # Overlay Statistics
            overlay = annotated_frame.copy()
            cv2.rectangle(overlay, (10, 10), (450, 110), (0, 0, 0), -1)
            cv2.addWeighted(overlay, 0.6, annotated_frame, 0.4, 0, annotated_frame)
            
            fps = frame_count / (time.time() - start_time) if frame_count > 0 else 0
            cv2.putText(annotated_frame, f"System FPS:   {fps:.1f}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.putText(annotated_frame, f"Zone Entries: {total_entered_zone}", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.putText(annotated_frame, f"Active In Zone: {len(objects_in_zone)}", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255) if len(objects_in_zone)>0 else (0, 255, 0), 2)

            # Handle Outputs
            if video_writer is not None:
                video_writer.write(annotated_frame)
            
            if args.display:
                cv2.imshow('Jetson Advanced Analytics', annotated_frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

            frame_count += 1

    except KeyboardInterrupt:
        print("\nAnalytics Stopped.")
    
    finally:
        cap.release()
        if video_writer is not None:
            video_writer.release()
        cv2.destroyAllWindows()
        print(f"Total Unique Zone Entries: {total_entered_zone}")


if __name__ == "__main__":
    main()
