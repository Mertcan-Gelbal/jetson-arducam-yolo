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
    
    def __init__(self, camera_id, queue, source_type='csi', width=1280, height=720):
        threading.Thread.__init__(self)
        self.camera_id = camera_id
        self.queue = queue
        self.running = False
        
        if source_type == 'usb':
            self.cap = cv2.VideoCapture(camera_id)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        else:
            # CSI Camera: GStreamer Pipeline (Hardware Accelerated)
            gst_pipeline = (
                f"nvarguscamerasrc sensor-id={camera_id} ! "
                f"video/x-raw(memory:NVMM), width={width}, height={height}, framerate=30/1 ! "
                f"nvvidconv flip-method=0 ! "
                f"video/x-raw, width={width}, height={height}, format=BGRx ! "
                f"videoconvert ! "
                f"video/x-raw, format=BGR ! appsink drop=True"
            )
            self.cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
        
    def run(self):
        self.running = True
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                # Keep only latest frame (clear previous if not processed)
                if not self.queue.empty():
                    try:
                        self.queue.get_nowait()
                    except:
                        pass
                self.queue.put(frame)
    
    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()


def process_cameras(camera_ids, model_path, source_type='csi', conf_thresh=0.25, display=False):
    """Process multiple cameras with YOLOv8"""
    
    # Load model
    print(f"Loading model: {model_path}")
    model = YOLO(model_path)
    
    # Create queues and threads for each camera
    queues = {}
    threads = {}
    
    for cam_id in camera_ids:
        print(f"Initializing camera {cam_id} ({source_type})")
        queues[cam_id] = Queue(maxsize=1)
        threads[cam_id] = CameraThread(cam_id, queues[cam_id], source_type=source_type)
        threads[cam_id].start()
    
    frame_counts = {cam_id: 0 for cam_id in camera_ids}
    
    try:
        print("Processing cameras... Press 'q' to quit")
        
        while True:
            # Run inference on each camera sequentially (Jetson shared GPU memory)
            # Alternatively, batch the frames together for better GPU utilization
            batch_frames = []
            valid_cam_ids = []
            
            for cam_id in camera_ids:
                if not queues[cam_id].empty():
                    frame = queues[cam_id].get()
                    batch_frames.append(frame)
                    valid_cam_ids.append(cam_id)
            
            if not batch_frames:
                continue
            
            # Run inference on the batch
            results = model(batch_frames, conf=conf_thresh, verbose=False)
            
            # Map results back to cameras
            results_dict = {}
            for i, cam_id in enumerate(valid_cam_ids):
                results_dict[cam_id] = results[i]
                frame_counts[cam_id] += 1
            
            # Display results
            if display:
                display_frames = []
                
                for cam_id in sorted(results_dict.keys()):
                    annotated = results_dict[cam_id].plot()
                    
                    # Add camera label and stats
                    label = f"Cam {cam_id} - {frame_counts[cam_id]}"
                    cv2.putText(annotated, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    
                    det_count = len(results_dict[cam_id].boxes)
                    cv2.putText(annotated, f"Detections: {det_count}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                    
                    display_frames.append(annotated)
                
                if display_frames:
                    # Create grid layout
                    if len(display_frames) == 2:
                        combined = np.hstack(display_frames)
                    elif len(display_frames) == 3:
                        top = np.hstack(display_frames[:2])
                        bottom = cv2.resize(display_frames[2], (top.shape[1], display_frames[2].shape[0]))
                        combined = np.vstack([top, bottom])
                    elif len(display_frames) == 4:
                        top = np.hstack(display_frames[:2])
                        bottom = np.hstack(display_frames[2:])
                        combined = np.vstack([top, bottom])
                    else:
                        combined = display_frames[0]
                    
                    # Resize if too large
                    max_width = 1920
                    if combined.shape[1] > max_width:
                        scale = max_width / combined.shape[1]
                        combined = cv2.resize(combined, (int(combined.shape[1] * scale), int(combined.shape[0] * scale)))
                    
                    cv2.imshow('Multi-Camera Detection', combined)
                    
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
            
            # Stats logging
            if any(count % 30 == 0 for count in frame_counts.values()):
                stats = " | ".join([f"Cam{cid}: {cnt} frames" for cid, cnt in frame_counts.items()])
                print(stats)
    
    except KeyboardInterrupt:
        print("\nStopping...")
    
    finally:
        for thread in threads.values():
            thread.stop()
        for thread in threads.values():
            thread.join()
        cv2.destroyAllWindows()
        print("\nFinal statistics:")
        for cam_id in camera_ids:
            print(f"  Camera {cam_id}: {frame_counts[cam_id]} frames processed")


def main():
    parser = argparse.ArgumentParser(description='Multi-Camera AI Detection')
    parser.add_argument('--cameras', type=int, nargs='+', default=[0, 1], help='Camera device IDs')
    parser.add_argument('--source-type', type=str, default='csi', choices=['csi', 'usb'], help='Camera type')
    parser.add_argument('--model', type=str, default='yolo11n.engine', help='Model path (.pt or .engine)')
    parser.add_argument('--conf', type=float, default=0.25, help='Confidence threshold')
    parser.add_argument('--display', action='store_true', help='Display results (Disable for headless)')
    args = parser.parse_args()
    
    process_cameras(
        camera_ids=args.cameras,
        model_path=args.model,
        source_type=args.source_type,
        conf_thresh=args.conf,
        display=args.display
    )


if __name__ == '__main__':
    main()
