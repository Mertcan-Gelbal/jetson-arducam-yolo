/**
 * Jetson Arducam AI Kit - Dashboard JavaScript
 * Clean, minimal implementation for Ubuntu/Jetson
 */

const socket = io();

let systemInfo = null;
let installationRunning = false;
let videoStream = null;

// =============================================================================
// INITIALIZATION
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initSocketEvents();
    refreshSystemInfo();
});

// =============================================================================
// NAVIGATION
// =============================================================================

function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const section = item.dataset.section;

            document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
            item.classList.add('active');

            document.querySelectorAll('.section').forEach(sec => sec.classList.remove('active'));
            document.getElementById(section).classList.add('active');

            // Stop video when leaving cameras section
            if (section !== 'cameras' && videoStream) {
                stopVideoPreview();
            }
        });
    });
}

// =============================================================================
// SOCKET EVENTS
// =============================================================================

function initSocketEvents() {
    socket.on('connect', () => updateConnectionStatus(true));
    socket.on('disconnect', () => updateConnectionStatus(false));

    socket.on('installation_started', (data) => {
        installationRunning = true;
        updateInstallationUI(true);
    });

    socket.on('installation_step', (data) => updateStepStatus(data.step, data.status));
    socket.on('installation_log', (data) => appendLog(data.line));

    socket.on('installation_complete', (data) => {
        installationRunning = false;
        updateInstallationUI(false);
    });
}

function updateConnectionStatus(connected) {
    const dot = document.getElementById('connectionDot');
    const text = document.getElementById('connectionText');

    dot.classList.toggle('connected', connected);
    text.textContent = connected ? 'Connected' : 'Disconnected';
}

// =============================================================================
// API
// =============================================================================

async function refreshSystemInfo() {
    try {
        const response = await fetch('/api/system-info');
        systemInfo = await response.json();
        updateDashboard(systemInfo);
    } catch (error) {
        console.error('Failed to fetch system info:', error);
    }
}

async function refreshCameras() {
    try {
        const response = await fetch('/api/refresh-cameras');
        const data = await response.json();
        updateCameraList(data.cameras);
        updateCameraGrid(data.cameras);
    } catch (error) {
        console.error('Failed to refresh cameras:', error);
    }
}

// =============================================================================
// DASHBOARD UPDATE
// =============================================================================

function updateDashboard(info) {
    if (info.jetson) {
        const j = info.jetson;
        setText('jetsonModel', j.model || '--');
        setText('jetpackVersion', j.jetpack_version || '--');
        setText('l4tVersion', j.l4t_version || '--');
        setText('ubuntuVersion', j.ubuntu_version || '--');
        setText('cudaVersion', j.cuda_version || '--');

        // Memory
        const memUsed = j.memory_total - j.memory_available;
        const memPercent = j.memory_total > 0 ? (memUsed / j.memory_total) * 100 : 0;
        setText('ramUsage', `${memUsed} / ${j.memory_total} MB`);
        setProgress('ramProgress', memPercent);

        setText('swapUsage', `${j.swap_total} MB`);
        setProgress('swapProgress', j.swap_total > 0 ? 50 : 0);

        const diskTotal = Math.round(j.disk_total / 1024);
        const diskAvail = Math.round(j.disk_available / 1024);
        const diskUsed = diskTotal - diskAvail;
        const diskPercent = diskTotal > 0 ? (diskUsed / diskTotal) * 100 : 0;
        setText('diskUsage', `${diskUsed} / ${diskTotal} GB`);
        setProgress('diskProgress', diskPercent);
    }

    if (info.cameras) updateCameraList(info.cameras);
    if (info.docker) updateDockerStatus(info.docker);
    if (info.gstreamer) updateGstreamerStatus(info.gstreamer);
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function setProgress(id, percent) {
    const el = document.getElementById(id);
    if (el) el.style.width = `${Math.min(100, percent)}%`;
}

// =============================================================================
// CAMERA LIST
// =============================================================================

function updateCameraList(cameras) {
    const listEl = document.getElementById('cameraList');
    if (!listEl) return;

    if (!cameras || cameras.length === 0) {
        listEl.innerHTML = '<p class="muted">No cameras detected</p>';
        return;
    }

    listEl.innerHTML = cameras.map(cam => `
        <div class="list-item">
            <div>
                <div class="name">${cam.name}</div>
                <div class="device">${cam.device}</div>
            </div>
            <span class="tag ${cam.type.toLowerCase()}">${cam.type}</span>
        </div>
    `).join('');
}

function updateCameraGrid(cameras) {
    const gridEl = document.getElementById('detectedCameras');
    if (!gridEl) return;

    if (!cameras || cameras.length === 0) {
        gridEl.innerHTML = '<p class="muted">No cameras detected. Check cable connections.</p>';
        return;
    }

    gridEl.innerHTML = cameras.map((cam, idx) => `
        <div class="camera-card">
            <div class="camera-card-header">
                <span class="camera-name">${cam.name}</span>
                <span class="tag ${cam.type.toLowerCase()}">${cam.type}</span>
            </div>
            <div class="camera-card-body">
                <div class="camera-info-row">
                    <span>Device</span>
                    <code>${cam.device}</code>
                </div>
                ${cam.i2c_address ? `
                <div class="camera-info-row">
                    <span>I2C Address</span>
                    <code>0x${cam.i2c_address}</code>
                </div>
                ` : ''}
            </div>
            <div class="camera-card-actions">
                <button class="btn btn-primary btn-small" onclick="startVideoPreview('${cam.device}', ${idx})">
                    Preview
                </button>
                <button class="btn btn-secondary btn-small" onclick="testCamera('${cam.device}')">
                    Test
                </button>
            </div>
        </div>
    `).join('');
}

// =============================================================================
// VIDEO PREVIEW
// =============================================================================

function startVideoPreview(device, sensorId) {
    const previewPanel = document.getElementById('videoPreviewPanel');
    const previewImage = document.getElementById('videoPreview');
    const deviceLabel = document.getElementById('previewDevice');

    if (previewPanel && previewImage) {
        previewPanel.style.display = 'block';
        deviceLabel.textContent = device;

        // Use MJPEG stream from backend
        previewImage.src = `/api/video-feed?device=${encodeURIComponent(device)}&sensor=${sensorId}`;
        videoStream = true;
    }
}

function stopVideoPreview() {
    const previewPanel = document.getElementById('videoPreviewPanel');
    const previewImage = document.getElementById('videoPreview');

    if (previewPanel) {
        previewPanel.style.display = 'none';
    }
    if (previewImage) {
        previewImage.src = '';
    }

    // Notify backend to stop stream
    fetch('/api/stop-video').catch(() => { });
    videoStream = null;
}

function testCamera(device) {
    appendLog(`Testing camera: ${device}`);
    socket.emit('test_camera', { device });
}

// =============================================================================
// DOCKER STATUS
// =============================================================================

function updateDockerStatus(docker) {
    setBadge('dockerInstalled', docker.installed ? 'Installed' : 'Not Found', docker.installed);
    setBadge('dockerRunning', docker.running ? 'Running' : 'Stopped', docker.running);
    setBadge('nvidiaRuntime', docker.nvidia_runtime ? 'Enabled' : 'Disabled', docker.nvidia_runtime);

    const containerList = document.getElementById('containerList');
    const imageList = document.getElementById('imageList');

    if (containerList) {
        containerList.innerHTML = docker.containers?.length > 0
            ? docker.containers.map(c => `<div class="list-item"><span class="name">${c}</span></div>`).join('')
            : '<p class="muted">No containers</p>';
    }

    if (imageList) {
        imageList.innerHTML = docker.images?.length > 0
            ? docker.images.map(i => `<div class="list-item"><span class="name">${i}</span></div>`).join('')
            : '<p class="muted">No images</p>';
    }
}

function setBadge(id, text, isSuccess) {
    const el = document.getElementById(id);
    if (el) {
        el.textContent = text;
        el.className = `status-badge ${isSuccess ? 'success' : 'error'}`;
    }
}

// =============================================================================
// GSTREAMER STATUS
// =============================================================================

function updateGstreamerStatus(gst) {
    setText('gstVersion', gst.version || '--');

    setPluginStatus('pluginNvargus', gst.nvarguscamerasrc);
    setPluginStatus('pluginNvvidconv', gst.nvvidconv);
    setPluginStatus('pluginOpenCV', gst.opencv_gst);
}

function setPluginStatus(id, active) {
    const el = document.getElementById(id);
    if (el) {
        el.className = `plugin ${active ? 'active' : 'inactive'}`;
    }
}

// =============================================================================
// INSTALLATION
// =============================================================================

function startInstallation() {
    if (installationRunning) return;

    const steps = [];
    if (document.getElementById('checkDrivers')?.checked) steps.push('drivers');
    if (document.getElementById('checkVerify')?.checked) steps.push('verify');
    if (document.getElementById('checkBuild')?.checked) steps.push('build');
    if (document.getElementById('checkRun')?.checked) steps.push('run');

    if (steps.length === 0) {
        alert('Please select at least one step');
        return;
    }

    // Reset UI
    ['drivers', 'verify', 'build', 'run'].forEach(step => {
        const el = document.getElementById(`step-${step}`);
        if (el) {
            el.classList.remove('running', 'success', 'error');
            const status = document.getElementById(`status-${step}`);
            if (status) status.textContent = 'Pending';
        }
    });

    document.getElementById('logOutput').textContent = '';
    socket.emit('start_installation', { steps });
}

function updateInstallationUI(running) {
    const btn = document.getElementById('startInstallBtn');
    const progress = document.getElementById('installProgress');

    if (btn) {
        btn.disabled = running;
        btn.textContent = running ? 'Installing...' : 'Start Installation';
    }
    if (progress) {
        progress.style.display = running ? 'block' : 'none';
    }
}

function updateStepStatus(step, status) {
    const el = document.getElementById(`step-${step}`);
    const statusEl = document.getElementById(`status-${step}`);

    if (el) {
        el.classList.remove('running', 'success', 'error');
        if (status !== 'pending') el.classList.add(status);
    }

    if (statusEl) {
        const labels = { running: 'Running...', success: 'Complete', error: 'Failed', pending: 'Pending' };
        statusEl.textContent = labels[status] || status;
    }

    updateProgressBar();
}

function updateProgressBar() {
    const steps = ['drivers', 'verify', 'build', 'run'];
    let completed = 0;

    steps.forEach(step => {
        const el = document.getElementById(`step-${step}`);
        if (el?.classList.contains('success') || el?.classList.contains('error')) {
            completed++;
        }
    });

    const percent = (completed / steps.length) * 100;
    setProgress('installProgressBar', percent);
    setText('progressPercent', `${Math.round(percent)}%`);
}

// =============================================================================
// LOGS
// =============================================================================

function appendLog(line) {
    const logEl = document.getElementById('logOutput');
    if (logEl) {
        logEl.textContent += line + '\n';
        logEl.scrollTop = logEl.scrollHeight;
    }
}

function clearLogs() {
    const logEl = document.getElementById('logOutput');
    if (logEl) logEl.textContent = '';
}

// =============================================================================
// DOCKER ACTIONS
// =============================================================================

function runScript(script) {
    socket.emit('start_installation', { steps: [script] });
}

function stopContainer() {
    appendLog('Stopping container...');
    fetch('/api/stop-container', { method: 'POST' }).catch(() => { });
}
