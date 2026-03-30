#!/usr/bin/env bash
# VisionDock: verify repo tree before copying to Jetson (or on Jetson after copy).
# Run from repo root:  bash scripts/check_jetson_deploy.sh
# Exit 0 = ready; 1 = missing critical items.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FAIL=0
WARN=0

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ok() { echo -e "  ${GREEN}OK${NC} $1"; }
bad() { echo -e "  ${RED}MISSING${NC} $1"; FAIL=1; }
warn() { echo -e "  ${YELLOW}WARN${NC} $1"; WARN=1; }
info() { echo -e "${CYAN}==${NC} $1"; }

echo ""
info "1) Zorunlu dosyalar (GUI + başlatıcı)"
for f in \
  gui/main.py \
  gui/requirements.txt \
  gui/catalog.json \
  start_gui.sh \
  install.sh \
  visiondock.svg \
  scripts/focus_imx519.py \
  scripts/factory_smoke_test.sh \
  scripts/prepare_docker_and_permissions.sh \
  docker-compose.yml \
  Dockerfile
do
  if [[ -f "$f" ]]; then ok "$f"; else bad "$f"; fi
done

echo ""
info "2) Python ve pip"
if command -v python3 >/dev/null 2>&1; then
  ok "python3 $(python3 --version 2>&1)"
else
  bad "python3 not in PATH"
fi
if python3 -m pip --version >/dev/null 2>&1; then
  ok "pip (python3 -m pip)"
else
  bad "python3 -m pip not available"
fi

echo ""
info "3) GUI bağımlılıkları (import testi)"
REQ=(PySide6 psutil cv2 numpy paramiko)
for m in "${REQ[@]}"; do
  if python3 -c "import ${m//-/_}" 2>/dev/null; then
    ok "import $m"
  else
    warn "import $m — run: python3 -m pip install -r gui/requirements.txt"
  fi
done

echo ""
info "4) Sözdizimi (gui/main.py)"
if python3 -m py_compile gui/main.py 2>/dev/null; then
  ok "py_compile gui/main.py"
else
  bad "py_compile gui/main.py"
fi

echo ""
info "5) Jetson / Linux ortam ipuçları"
if [[ -f /etc/nv_tegra_release ]]; then
  ok "Jetson detected: $(head -1 /etc/nv_tegra_release)"
else
  warn "Not a Jetson image (no /etc/nv_tegra_release) — still OK for dev PC checks"
fi
if command -v v4l2-ctl >/dev/null 2>&1; then ok "v4l2-ctl"; else warn "v4l2-ctl not found (USB/V4L2 tuning)"; fi
if [[ -x /usr/bin/nvgstcapture ]]; then ok "nvgstcapture (CSI path)"; else warn "nvgstcapture missing (CSI pipeline may still work via OpenCV)"; fi
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then ok "docker info"; else warn "docker installed but engine not reachable — ./scripts/prepare_docker_and_permissions.sh"; fi
else
  warn "docker CLI not found (optional for VisionDock Studio workspaces)"
fi

echo ""
info "6) Dizin kopyası (rsync örneği, çalıştırmadan önce hedefi düzenleyin)"
echo "  rsync -av --progress \\"
echo "    --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' \\"
echo "    --exclude='.venv/' --exclude='venv/' --exclude='dist/' --exclude='build/' \\"
echo "    \"$ROOT/\" jetson@JETSON_ZT_IP:~/jetson-arducam-yolo/"
echo ""
echo -e "  Jetson'da: ${YELLOW}cd ~/jetson-arducam-yolo && chmod +x start_gui.sh install.sh scripts/*.sh${NC}"
echo -e "  Bağımlılık: ${YELLOW}python3 -m pip install -r gui/requirements.txt${NC}"
echo -e "  GUI:       ${YELLOW}./start_gui.sh${NC}   veya   ${YELLOW}python3 gui/main.py${NC}"
echo -e "  Docker:    ${YELLOW}./install.sh --docker-host${NC} (gruplar + servis)"

echo ""
if [[ "$FAIL" -ne 0 ]]; then
  echo -e "${RED}Sonuç: EKSİK dosya veya kritik hata — Jetson'a kopyalamadan düzeltin.${NC}"
  exit 1
fi
if [[ "$WARN" -ne 0 ]]; then
  echo -e "${YELLOW}Sonuç: Çalışabilir; yukarıdaki WARN maddelerini Jetson'da gözden geçirin.${NC}"
  exit 0
fi
echo -e "${GREEN}Sonuç: Temel kontroller tamam.${NC}"
exit 0
