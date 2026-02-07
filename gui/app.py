#!/usr/bin/env python3
"""
Jetson Arducam AI Kit - Web GUI
A modern web interface for camera setup and system management.
"""

import os
import sys
import json
import subprocess
import threading
import queue
from datetime import datetime
from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'jetson-arducam-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global state
system_info = {}
installation_log = queue.Queue()
installation_running = False

# =============================================================================
# SYSTEM DETECTION FUNCTIONS
# =============================================================================

def get_jetson_info():
    """Detect Jetson device information."""
    info = {
        'model': 'Unknown',
        'l4t_version': 'Unknown',
        'jetpack_version': 'Unknown',
        'ubuntu_version': 'Unknown',
        'kernel_version': 'Unknown',
        'cuda_version': 'Unknown',
        'memory_total': 0,
        'memory_available': 0,
        'swap_total': 0,
        'disk_total': 0,
        'disk_available': 0,
    }
    
    try:
        # Jetson Model
        with open('/sys/firmware/devicetree/base/model', 'r') as f:
            info['model'] = f.read().strip().replace('\x00', '')
    except:
        pass
    
    try:
        # L4T Version
        result = subprocess.run(
            ['dpkg-query', '--showformat=${Version}', '--show', 'nvidia-l4t-kernel'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            info['l4t_version'] = result.stdout.strip()
            # Extract major version for JetPack mapping
            import re
            match = re.search(r'tegra-(\d+\.\d+\.\d+)', info['l4t_version'])
            if match:
                l4t_short = match.group(1)
                jp_map = {
                    '32.7': '4.6.x', '35.3': '5.1.1', '35.4': '5.1.2',
                    '35.5': '5.1.3', '35.6': '5.1.4+', '36.2': '6.0',
                    '36.3': '6.1', '36.4': '6.2+'
                }
                for k, v in jp_map.items():
                    if l4t_short.startswith(k):
                        info['jetpack_version'] = f"JetPack {v}"
                        break
    except:
        pass
    
    try:
        # Ubuntu Version
        result = subprocess.run(['lsb_release', '-rs'], capture_output=True, text=True)
        if result.returncode == 0:
            info['ubuntu_version'] = f"Ubuntu {result.stdout.strip()}"
    except:
        pass
    
    try:
        # Kernel Version
        result = subprocess.run(['uname', '-r'], capture_output=True, text=True)
        if result.returncode == 0:
            info['kernel_version'] = result.stdout.strip()
    except:
        pass
    
    try:
        # CUDA Version
        result = subprocess.run(['nvcc', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            import re
            match = re.search(r'release (\d+\.\d+)', result.stdout)
            if match:
                info['cuda_version'] = f"CUDA {match.group(1)}"
    except:
        pass
    
    try:
        # Memory Info
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if 'MemTotal' in line:
                    info['memory_total'] = int(line.split()[1]) // 1024  # MB
                elif 'MemAvailable' in line:
                    info['memory_available'] = int(line.split()[1]) // 1024
                elif 'SwapTotal' in line:
                    info['swap_total'] = int(line.split()[1]) // 1024
    except:
        pass
    
    try:
        # Disk Info
        result = subprocess.run(['df', '-m', '/'], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:
                parts = lines[1].split()
                info['disk_total'] = int(parts[1])
                info['disk_available'] = int(parts[3])
    except:
        pass
    
    return info


def get_camera_info():
    """Detect connected cameras."""
    cameras = []
    
    # Check for video devices
    try:
        result = subprocess.run(['v4l2-ctl', '--list-devices'], capture_output=True, text=True)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            current_name = ""
            for line in lines:
                if not line.startswith('\t') and line.strip():
                    current_name = line.strip().rstrip(':')
                elif line.strip().startswith('/dev/video'):
                    cameras.append({
                        'name': current_name,
                        'device': line.strip(),
                        'type': 'CSI' if 'argus' in current_name.lower() else 'USB'
                    })
    except:
        pass
    
    # Check for I2C devices (CSI cameras)
    try:
        for bus in [0, 1, 7, 8, 9, 10, 30, 31]:
            result = subprocess.run(
                ['i2cdetect', '-y', '-r', str(bus)],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                # Check for known camera addresses
                output = result.stdout
                camera_addrs = {'1a': 'IMX219', '10': 'IMX477/519', '3c': 'OV5647'}
                for addr, model in camera_addrs.items():
                    if addr in output.lower():
                        cameras.append({
                            'name': f'{model} (I2C Bus {bus})',
                            'device': f'/dev/i2c-{bus}',
                            'type': 'CSI',
                            'i2c_address': addr
                        })
    except:
        pass
    
    return cameras


def get_docker_info():
    """Get Docker status and images."""
    info = {
        'installed': False,
        'running': False,
        'nvidia_runtime': False,
        'images': [],
        'containers': []
    }
    
    try:
        # Check Docker installed
        result = subprocess.run(['docker', '--version'], capture_output=True, text=True)
        info['installed'] = result.returncode == 0
        
        if info['installed']:
            # Check Docker running
            result = subprocess.run(['systemctl', 'is-active', 'docker'], capture_output=True, text=True)
            info['running'] = result.stdout.strip() == 'active'
            
            # Check NVIDIA runtime
            result = subprocess.run(['docker', 'info'], capture_output=True, text=True)
            info['nvidia_runtime'] = 'nvidia' in result.stdout.lower()
            
            # List images
            result = subprocess.run(
                ['docker', 'images', '--format', '{{.Repository}}:{{.Tag}} ({{.Size}})'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                info['images'] = [img for img in result.stdout.strip().split('\n') if img]
            
            # List containers
            result = subprocess.run(
                ['docker', 'ps', '-a', '--format', '{{.Names}} - {{.Status}}'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                info['containers'] = [c for c in result.stdout.strip().split('\n') if c]
    except:
        pass
    
    return info


def get_gstreamer_info():
    """Get GStreamer version and plugins."""
    info = {
        'version': 'Unknown',
        'nvarguscamerasrc': False,
        'nvvidconv': False,
        'opencv_gst': False
    }
    
    try:
        result = subprocess.run(['gst-inspect-1.0', '--version'], capture_output=True, text=True)
        if result.returncode == 0:
            import re
            match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
            if match:
                info['version'] = match.group(1)
        
        # Check plugins
        result = subprocess.run(['gst-inspect-1.0', 'nvarguscamerasrc'], capture_output=True, text=True)
        info['nvarguscamerasrc'] = result.returncode == 0
        
        result = subprocess.run(['gst-inspect-1.0', 'nvvidconv'], capture_output=True, text=True)
        info['nvvidconv'] = result.returncode == 0
        
        # Check OpenCV GStreamer support
        try:
            import cv2
            info['opencv_gst'] = 'GStreamer' in cv2.getBuildInformation()
        except:
            pass
    except:
        pass
    
    return info


# =============================================================================
# ROUTES
# =============================================================================

@app.route('/')
def index():
    """Main dashboard page."""
    return render_template('index.html')


@app.route('/api/system-info')
def api_system_info():
    """Get all system information."""
    return jsonify({
        'jetson': get_jetson_info(),
        'cameras': get_camera_info(),
        'docker': get_docker_info(),
        'gstreamer': get_gstreamer_info(),
        'timestamp': datetime.now().isoformat()
    })


@app.route('/api/refresh-cameras')
def api_refresh_cameras():
    """Refresh camera detection."""
    return jsonify({'cameras': get_camera_info()})


# =============================================================================
# SOCKETIO EVENTS FOR REAL-TIME INSTALLATION
# =============================================================================

def run_installation(steps):
    """Run installation steps and emit progress."""
    global installation_running
    installation_running = True
    
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    for step in steps:
        socketio.emit('installation_step', {'step': step, 'status': 'running'})
        
        script_map = {
            'drivers': 'scripts/setup_cameras.sh',
            'verify': 'scripts/test_installation.sh',
            'build': 'scripts/build_docker.sh',
            'run': 'scripts/run_docker.sh'
        }
        
        if step in script_map:
            script_path = os.path.join(script_dir, script_map[step])
            try:
                process = subprocess.Popen(
                    ['bash', script_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=script_dir
                )
                
                for line in iter(process.stdout.readline, ''):
                    socketio.emit('installation_log', {'line': line.strip()})
                
                process.wait()
                status = 'success' if process.returncode == 0 else 'error'
            except Exception as e:
                socketio.emit('installation_log', {'line': f'Error: {str(e)}'})
                status = 'error'
        else:
            status = 'skipped'
        
        socketio.emit('installation_step', {'step': step, 'status': status})
    
    installation_running = False
    socketio.emit('installation_complete', {'success': True})


@socketio.on('start_installation')
def handle_start_installation(data):
    """Start installation process."""
    steps = data.get('steps', ['drivers', 'verify', 'build'])
    thread = threading.Thread(target=run_installation, args=(steps,))
    thread.daemon = True
    thread.start()
    emit('installation_started', {'steps': steps})


@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    emit('connected', {'status': 'ok'})


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  Jetson Arducam AI Kit - Web GUI")
    print("=" * 60)
    print("\n  Open in browser: http://localhost:5000\n")
    print("=" * 60)
    
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
