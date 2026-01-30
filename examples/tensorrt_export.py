#!/usr/bin/env python3
"""
YOLOv8 TensorRT Export and Benchmark
Export YOLOv8 to TensorRT and compare performance
"""

import time
import argparse
from ultralytics import YOLO
import torch
import cv2
import numpy as np


def export_to_tensorrt(model_path, half=True, workspace=4, verbose=True):
    """
    Export YOLOv8 model to TensorRT engine
    
    Args:
        model_path: Path to PyTorch model (.pt)
        half: Use FP16 precision (faster on Jetson)
        workspace: GPU workspace size in GB
        verbose: Print detailed export info
    
    Returns:
        Path to TensorRT engine file
    """
    
    print(f"Loading model: {model_path}")
    model = YOLO(model_path)
    
    print("\nExporting to TensorRT...")
    print(f"  Precision: {'FP16' if half else 'FP32'}")
    print(f"  Workspace: {workspace}GB")
    
    # Export to TensorRT
    engine_path = model.export(
        format='engine',
        device=0,
        half=half,
        workspace=workspace,
        verbose=verbose,
        simplify=True
    )
    
    print(f"\nTensorRT engine saved to: {engine_path}")
    return engine_path


def benchmark_model(model_path, num_frames=100, warmup=10, imgsz=640):
    """
    Benchmark model inference speed
    
    Args:
        model_path: Path to model file
        num_frames: Number of frames to benchmark
        warmup: Number of warmup iterations
        imgsz: Input image size
    
    Returns:
        Dictionary with benchmark results
    """
    
    print(f"\nBenchmarking: {model_path}")
    print(f"  Frames: {num_frames}")
    print(f"  Warmup: {warmup}")
    print(f"  Image size: {imgsz}")
    
    # Load model
    model = YOLO(model_path)
    
    # Create dummy input
    dummy_frame = np.random.randint(0, 255, (imgsz, imgsz, 3), dtype=np.uint8)
    
    # Warmup
    print("\nWarming up...")
    for _ in range(warmup):
        _ = model(dummy_frame, verbose=False)
    
    # Benchmark
    print("Running benchmark...")
    times = []
    
    for i in range(num_frames):
        start = time.time()
        results = model(dummy_frame, verbose=False)
        end = time.time()
        
        inference_time = (end - start) * 1000  # Convert to ms
        times.append(inference_time)
        
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{num_frames}")
    
    # Calculate statistics
    times = np.array(times)
    results = {
        'mean_ms': np.mean(times),
        'std_ms': np.std(times),
        'min_ms': np.min(times),
        'max_ms': np.max(times),
        'median_ms': np.median(times),
        'fps': 1000.0 / np.mean(times)
    }
    
    return results


def compare_models(pytorch_model, tensorrt_engine, num_frames=100):
    """Compare PyTorch and TensorRT performance"""
    
    print("\n" + "="*60)
    print("PERFORMANCE COMPARISON")
    print("="*60)
    
    # Benchmark PyTorch model
    print("\n[1/2] PyTorch Model (FP32)")
    pt_results = benchmark_model(pytorch_model, num_frames)
    
    # Benchmark TensorRT engine
    print("\n[2/2] TensorRT Engine (FP16)")
    trt_results = benchmark_model(tensorrt_engine, num_frames)
    
    # Print comparison
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    
    print("\nPyTorch Model:")
    print(f"  Mean inference: {pt_results['mean_ms']:.2f} ms")
    print(f"  Std deviation:  {pt_results['std_ms']:.2f} ms")
    print(f"  Min/Max:        {pt_results['min_ms']:.2f} / {pt_results['max_ms']:.2f} ms")
    print(f"  Average FPS:    {pt_results['fps']:.2f}")
    
    print("\nTensorRT Engine:")
    print(f"  Mean inference: {trt_results['mean_ms']:.2f} ms")
    print(f"  Std deviation:  {trt_results['std_ms']:.2f} ms")
    print(f"  Min/Max:        {trt_results['min_ms']:.2f} / {trt_results['max_ms']:.2f} ms")
    print(f"  Average FPS:    {trt_results['fps']:.2f}")
    
    # Calculate speedup
    speedup = pt_results['mean_ms'] / trt_results['mean_ms']
    fps_improvement = trt_results['fps'] / pt_results['fps']
    
    print("\nSpeedup:")
    print(f"  Inference time: {speedup:.2f}x faster")
    print(f"  FPS increase:   {fps_improvement:.2f}x")
    print(f"  Time saved:     {pt_results['mean_ms'] - trt_results['mean_ms']:.2f} ms per frame")
    
    print("\n" + "="*60)


def test_accuracy(pytorch_model, tensorrt_engine, test_image=None):
    """
    Test if TensorRT engine produces similar results to PyTorch model
    """
    
    print("\n" + "="*60)
    print("ACCURACY TEST")
    print("="*60)
    
    # Load models
    pt_model = YOLO(pytorch_model)
    trt_model = YOLO(tensorrt_engine)
    
    # Create or load test image
    if test_image:
        frame = cv2.imread(test_image)
        if frame is None:
            print(f"Warning: Could not load {test_image}, using random image")
            frame = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
    else:
        frame = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
    
    # Run inference
    print("\nRunning inference on both models...")
    pt_results = pt_model(frame, verbose=False)[0]
    trt_results = trt_model(frame, verbose=False)[0]
    
    # Compare detections
    pt_boxes = len(pt_results.boxes)
    trt_boxes = len(trt_results.boxes)
    
    print(f"\nPyTorch detections:  {pt_boxes}")
    print(f"TensorRT detections: {trt_boxes}")
    print(f"Difference:          {abs(pt_boxes - trt_boxes)}")
    
    if abs(pt_boxes - trt_boxes) <= 2:
        print("\n✓ Results are similar (within 2 detection difference)")
    else:
        print("\n⚠ Warning: Results differ significantly")
        print("  This is normal for some images due to FP16 precision")
    
    print("="*60)


def main():
    parser = argparse.ArgumentParser(description='YOLOv8 TensorRT Export and Benchmark')
    parser.add_argument('--model', type=str, default='yolov8n.pt', help='PyTorch model path')
    parser.add_argument('--export', action='store_true', help='Export to TensorRT')
    parser.add_argument('--benchmark', action='store_true', help='Run benchmark')
    parser.add_argument('--compare', action='store_true', help='Compare PyTorch vs TensorRT')
    parser.add_argument('--test-accuracy', action='store_true', help='Test accuracy')
    parser.add_argument('--test-image', type=str, help='Test image path')
    parser.add_argument('--engine', type=str, help='TensorRT engine path (auto-detected if not provided)')
    parser.add_argument('--frames', type=int, default=100, help='Benchmark frames')
    parser.add_argument('--fp32', action='store_true', help='Use FP32 instead of FP16')
    parser.add_argument('--workspace', type=int, default=4, help='GPU workspace in GB')
    args = parser.parse_args()
    
    # Check CUDA availability
    if not torch.cuda.is_available():
        print("Error: CUDA not available")
        return
    
    print("System Information:")
    print(f"  PyTorch: {torch.__version__}")
    print(f"  CUDA: {torch.version.cuda}")
    print(f"  Device: {torch.cuda.get_device_name(0)}")
    print()
    
    # Auto-detect engine path
    if not args.engine:
        args.engine = args.model.replace('.pt', '.engine')
    
    # Export to TensorRT
    if args.export:
        engine_path = export_to_tensorrt(
            args.model,
            half=not args.fp32,
            workspace=args.workspace
        )
        args.engine = engine_path
    
    # Benchmark individual model
    if args.benchmark and not args.compare:
        model_to_benchmark = args.engine if args.engine.endswith('.engine') else args.model
        results = benchmark_model(model_to_benchmark, args.frames)
        
        print("\nBenchmark Results:")
        print(f"  Mean:   {results['mean_ms']:.2f} ms")
        print(f"  Median: {results['median_ms']:.2f} ms")
        print(f"  FPS:    {results['fps']:.2f}")
    
    # Compare PyTorch vs TensorRT
    if args.compare:
        compare_models(args.model, args.engine, args.frames)
    
    # Test accuracy
    if args.test_accuracy:
        test_accuracy(args.model, args.engine, args.test_image)


if __name__ == '__main__':
    main()
