/**
 * Jetson Arducam AI Kit - Dashboard JavaScript
 */

// Socket.IO Connection
const socket = io();

// State
let systemInfo = null;
let installationRunning = false;

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
    const navItems = document.querySelectorAll('.nav-item');

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const section = item.dataset.section;

            // Update nav active state
            navItems.forEach(nav => nav.classList.remove('active'));
            item.classList.add('active');

            // Show corresponding section
            document.querySelectorAll('.section').forEach(sec => sec.classList.remove('active'));
            document.getElementById(section).classList.add('active');
        });
    });
}

// =============================================================================
// SOCKET.IO EVENTS
// =============================================================================

function initSocketEvents() {
    socket.on('connect', () => {
        updateConnectionStatus(true);
    });

    socket.on('disconnect', () => {
        updateConnectionStatus(false);
    });

    socket.on('connected', (data) => {
        console.log('Connected to server:', data);
    });

    socket.on('installation_started', (data) => {
        console.log('Installation started:', data);
        installationRunning = true;
        updateInstallationUI(true);
    });

    socket.on('installation_step', (data) => {
        updateStepStatus(data.step, data.status);
    });

    socket.on('installation_log', (data) => {
        appendLog(data.line);
    });

    socket.on('installation_complete', (data) => {
        installationRunning = false;
        updateInstallationUI(false);
        if (data.success) {
            showNotification('Installation completed successfully!', 'success');
        }
    });
}

function updateConnectionStatus(connected) {
    const dot = document.getElementById('connectionDot');
    const text = document.getElementById('connectionText');

    if (connected) {
        dot.classList.add('connected');
        text.textContent = 'Connected';
    } else {
        dot.classList.remove('connected');
        text.textContent = 'Disconnected';
    }
}

// =============================================================================
// API CALLS
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
    } catch (error) {
        console.error('Failed to refresh cameras:', error);
    }
}

// =============================================================================
// UI UPDATES
// =============================================================================

function updateDashboard(info) {
    // Jetson Info
    if (info.jetson) {
        document.getElementById('jetsonModel').textContent = info.jetson.model || 'Unknown';
        document.getElementById('jetpackVersion').textContent = info.jetson.jetpack_version || '--';
        document.getElementById('l4tVersion').textContent = info.jetson.l4t_version || '--';
        document.getElementById('ubuntuVersion').textContent = info.jetson.ubuntu_version || '--';

        // Memory
        const memTotal = info.jetson.memory_total;
        const memAvail = info.jetson.memory_available;
        const memUsed = memTotal - memAvail;
        const memPercent = memTotal > 0 ? (memUsed / memTotal) * 100 : 0;

        document.getElementById('ramUsage').textContent = `${memUsed} / ${memTotal} MB`;
        document.getElementById('ramProgress').style.width = `${memPercent}%`;

        // Swap
        document.getElementById('swapUsage').textContent = `${info.jetson.swap_total} MB`;
        document.getElementById('swapProgress').style.width = info.jetson.swap_total > 0 ? '100%' : '0%';

        // Disk
        const diskTotal = Math.round(info.jetson.disk_total / 1024);
        const diskAvail = Math.round(info.jetson.disk_available / 1024);
        const diskUsed = diskTotal - diskAvail;
        const diskPercent = diskTotal > 0 ? (diskUsed / diskTotal) * 100 : 0;

        document.getElementById('diskUsage').textContent = `${diskUsed} / ${diskTotal} GB`;
        document.getElementById('diskProgress').style.width = `${diskPercent}%`;
    }

    // Cameras
    if (info.cameras) {
        updateCameraList(info.cameras);
    }

    // Docker
    if (info.docker) {
        updateDockerStatus(info.docker);
    }

    // GStreamer
    if (info.gstreamer) {
        updateGstreamerStatus(info.gstreamer);
    }
}

function updateCameraList(cameras) {
    const listEl = document.getElementById('cameraList');
    const detailEl = document.getElementById('detectedCameras');

    if (cameras.length === 0) {
        listEl.innerHTML = '<p class="muted">No cameras detected</p>';
        if (detailEl) detailEl.innerHTML = '<p class="muted">No cameras detected. Check connections.</p>';
        return;
    }

    let html = '';
    cameras.forEach(cam => {
        const typeClass = cam.type === 'CSI' ? 'csi' : 'usb';
        html += `
            <div class="camera-item">
                <div class="camera-icon">📷</div>
                <div class="camera-info">
                    <div class="camera-name">${cam.name}</div>
                    <div class="camera-device">${cam.device}</div>
                </div>
                <span class="camera-type ${typeClass}">${cam.type}</span>
            </div>
        `;
    });

    listEl.innerHTML = html;
    if (detailEl) detailEl.innerHTML = html;
}

function updateDockerStatus(docker) {
    // Badges
    const installed = document.getElementById('dockerInstalled');
    const running = document.getElementById('dockerRunning');
    const nvidia = document.getElementById('nvidiaRuntime');

    installed.textContent = `Docker: ${docker.installed ? 'Installed' : 'Not Found'}`;
    installed.className = `badge ${docker.installed ? 'success' : 'error'}`;

    running.textContent = `Service: ${docker.running ? 'Running' : 'Stopped'}`;
    running.className = `badge ${docker.running ? 'success' : 'warning'}`;

    nvidia.textContent = `NVIDIA: ${docker.nvidia_runtime ? 'Enabled' : 'Disabled'}`;
    nvidia.className = `badge ${docker.nvidia_runtime ? 'success' : 'warning'}`;

    // Images
    const imagesEl = document.getElementById('dockerImages');
    if (docker.images && docker.images.length > 0) {
        imagesEl.innerHTML = docker.images.map(img =>
            `<div class="info-row"><span class="value">${img}</span></div>`
        ).join('');
    } else {
        imagesEl.innerHTML = '<p class="muted">No images found</p>';
    }

    // Container list (Docker section)
    const containerList = document.getElementById('containerList');
    if (containerList) {
        if (docker.containers && docker.containers.length > 0) {
            containerList.innerHTML = docker.containers.map(c =>
                `<div class="info-row"><span class="value">${c}</span></div>`
            ).join('');
        } else {
            containerList.innerHTML = '<p class="muted">No containers found</p>';
        }
    }

    // Image list (Docker section)
    const imageList = document.getElementById('imageList');
    if (imageList) {
        if (docker.images && docker.images.length > 0) {
            imageList.innerHTML = docker.images.map(img =>
                `<div class="info-row"><span class="value">${img}</span></div>`
            ).join('');
        } else {
            imageList.innerHTML = '<p class="muted">No images found</p>';
        }
    }
}

function updateGstreamerStatus(gst) {
    document.getElementById('gstVersion').textContent = gst.version || '--';

    const nvargus = document.getElementById('pluginNvargus');
    const nvvidconv = document.getElementById('pluginNvvidconv');
    const opencv = document.getElementById('pluginOpenCV');

    nvargus.className = `plugin-badge ${gst.nvarguscamerasrc ? 'active' : 'inactive'}`;
    nvvidconv.className = `plugin-badge ${gst.nvvidconv ? 'active' : 'inactive'}`;
    opencv.className = `plugin-badge ${gst.opencv_gst ? 'active' : 'inactive'}`;
}

// =============================================================================
// INSTALLATION
// =============================================================================

function startInstallation() {
    if (installationRunning) return;

    const steps = [];
    if (document.getElementById('checkDrivers').checked) steps.push('drivers');
    if (document.getElementById('checkVerify').checked) steps.push('verify');
    if (document.getElementById('checkBuild').checked) steps.push('build');
    if (document.getElementById('checkRun').checked) steps.push('run');

    if (steps.length === 0) {
        showNotification('Please select at least one step', 'warning');
        return;
    }

    // Reset step UI
    ['drivers', 'verify', 'build', 'run'].forEach(step => {
        const el = document.getElementById(`step-${step}`);
        el.classList.remove('running', 'success', 'error');
        el.querySelector('.status-icon').textContent = '⏳';
    });

    // Clear logs
    document.getElementById('logOutput').textContent = '';

    // Start installation
    socket.emit('start_installation', { steps });
}

function updateInstallationUI(running) {
    const btn = document.getElementById('startInstallBtn');
    const progress = document.getElementById('installProgress');

    if (running) {
        btn.disabled = true;
        btn.innerHTML = '<span>⏳</span> Installing...';
        progress.style.display = 'block';
    } else {
        btn.disabled = false;
        btn.innerHTML = '<span>🚀</span> Start Installation';
        progress.style.display = 'none';
    }
}

function updateStepStatus(step, status) {
    const el = document.getElementById(`step-${step}`);
    if (!el) return;

    el.classList.remove('running', 'success', 'error');

    const statusIcon = el.querySelector('.status-icon');

    switch (status) {
        case 'running':
            el.classList.add('running');
            statusIcon.textContent = '⏳';
            break;
        case 'success':
            el.classList.add('success');
            statusIcon.textContent = '✅';
            break;
        case 'error':
            el.classList.add('error');
            statusIcon.textContent = '❌';
            break;
        case 'skipped':
            statusIcon.textContent = '⏭️';
            break;
    }

    // Update progress bar
    updateProgressBar();
}

function updateProgressBar() {
    const steps = ['drivers', 'verify', 'build', 'run'];
    let completed = 0;

    steps.forEach(step => {
        const el = document.getElementById(`step-${step}`);
        if (el.classList.contains('success') || el.classList.contains('error')) {
            completed++;
        }
    });

    const percent = (completed / steps.length) * 100;
    document.getElementById('installProgressBar').style.width = `${percent}%`;
    document.getElementById('progressPercent').textContent = `${Math.round(percent)}%`;
}

// =============================================================================
// LOGS
// =============================================================================

function appendLog(line) {
    const logEl = document.getElementById('logOutput');
    logEl.textContent += line + '\n';
    logEl.scrollTop = logEl.scrollHeight;
}

function clearLogs() {
    document.getElementById('logOutput').textContent = 'Logs cleared.\n';
}

// =============================================================================
// DOCKER ACTIONS
// =============================================================================

function runScript(script) {
    socket.emit('start_installation', { steps: [script] });
}

function stopContainer() {
    // This would need a backend endpoint
    showNotification('Stopping container...', 'info');
}

// =============================================================================
// NOTIFICATIONS
// =============================================================================

function showNotification(message, type = 'info') {
    // Simple console log for now
    console.log(`[${type.toUpperCase()}] ${message}`);

    // Could be enhanced with toast notifications
    alert(message);
}
