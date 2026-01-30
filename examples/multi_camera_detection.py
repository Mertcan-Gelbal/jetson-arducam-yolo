#!/usr/bin/env python3
"""
Multi-Camera YOLOv8 Object Detection
Processes multiple camera feeds simultaneously
"""

import cv2
import numpy as np
from ultralytics import YOLO
import argparse
import threading
from queue import Queue


class CameraThread(threading.Thread):
    """Thread to capture frames from a camera"""
    
    def __init__(self, camera_id, queue):
        threading.Thread.__init__(self)
        self.camera_id = camera_id
        self.queue = queue
        self.cap = cv2.VideoCapture(camera_id)
        self.running = False
        
    def run(self):
        self.running = True
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                # Keep only latest frame
                if not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                    except:
                        pass
                self.queue.put((self.camera_id, frame))
    
    def stop(self):
        self.running = False
        self.cap.release()


def process_cameras(camera_ids, model_path, conf_thresh=0.25, display=False):
    """Process multiple cameras with YOLOv8"""
    
    # Load model
    print(f"Loading model: {model_path}")
    model = YOLO(model_path)
    
    # Create queues and threads for each camera
    queues = {}
    threads = {}
    
    for cam_id in camera_ids:
        print(f"Initializing camera {cam_id}")
        queues[cam_id] = Queue(maxsize=2)
        threads[cam_id] = CameraThread(cam_id, queues[cam_id])
        threads[cam_id].start()
    
    frame_counts = {cam_id: 0 for cam_id in camera_ids}
    
    try:
        print("Processing cameras... Press 'q' to quit")
        
        while True:
            frames = {}
            
            # Get latest frame from each camera
            for cam_id in camera_ids:
                if not queues[cam_id].empty():
                    _, frame = queues[cam_id].get()
                    frames[cam_id] = frame
            
            if not frames:
                continue
            
            # Run inference on all frames
            results = {}
            for cam_id, frame in frames.items():
                result = model(frame, conf=conf_thresh, verbose=False)
                results[cam_id] = result[0]
                frame_counts[cam_id] += 1
            
            # Display results
            if display and results:
                display_frames = []
                
                for cam_id in sorted(results.keys()):
                    annotated = results[cam_id].plot()
                    
                    # Add camera label
                    cv2.putText(
                        annotated,
                        f"Camera {cam_id} - Frame {frame_counts[cam_id]}",
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2
                    )
                    
                    # Add detection count
                    det_count = len(results[cam_id].boxes)
                    cv2.putText(
                        annotated,
                        f"Detections: {det_count}",
                        (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2
                    )
                    
                    display_frames.append(annotated)
                
                # Create grid layout
                if len(display_frames) == 2:
                    combined = np.hstack(display_frames)
                elif len(display_frames) == 3:
                    # 2 on top, 1 on bottom
                    top = np.hstack(display_frames[:2])
                    bottom = display_frames[2]
                    # Resize bottom to match width
                    bottom = cv2.resize(bottom, (top.shape[1], bottom.shape[0]))
                    combined = np.vstack([top, bottom])
                elif len(display_frames) == 4:
                    # 2x2 grid
                    top = np.hstack(display_frames[:2])
                    bottom = np.hstack(display_frames[2:])
                    combined = np.vstack([top, bottom])
                else:
                    combined = display_frames[0]
                
                # Resize if too large
                max_width = 1920
                if combined.shape[1] > max_width:
                    scale = max_width / combined.shape[1]
                    new_width = int(combined.shape[1] * scale)
                    new_height = int(combined.shape[0] * scale)
                    combined = cv2.resize(combined, (new_width, new_height))
                
                cv2.imshow('Multi-Camera Detection', combined)
                
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            
            # Print stats every 30 frames
            if any(count % 30 == 0 for count in frame_counts.values()):
                stats = " | ".join([
                    f"Cam{cam_id}: {frame_counts[cam_id]} frames"
                    for cam_id in camera_ids
                ])
                print(stats)
    
    except KeyboardInterrupt:
        print("\nStopping...")
    
    finally:
        # Stop all threads
        for thread in threads.values():
            thread.stop()
        for thread in threads.values():
            thread.join()
        
        cv2.destroyAllWindows()
        
        print("\nFinal statistics:")
        for cam_id in camera_ids:
            print(f"  Camera {cam_id}: {frame_counts[cam_id]} frames processed")


def main():
    parser = argparse.ArgumentParser(description='Multi-Camera YOLOv8 Detection')
    parser.add_argument(
        '--cameras',
        type=int,
        nargs='+',
        default=[0, 1],
        help='Camera device IDs (e.g., --cameras 0 1 2 3)'
    )
    parser.add_argument('--model', type=str, default='yolov8n.pt', help='Model path')
    parser.add_argument('--conf', type=float, default=0.25, help='Confidence threshold')
    parser.add_argument('--display', action='store_true', help='Display results')
    args = parser.parse_args()
    
    print(f"Multi-Camera Detection")
    print(f"Cameras: {args.cameras}")
    print(f"Model: {args.model}")
    print(f"Confidence: {args.conf}")
    
    process_cameras(
        camera_ids=args.cameras,
        model_path=args.model,
        conf_thresh=args.conf,
        display=args.display
    )


if __name__ == '__main__':
    main()
