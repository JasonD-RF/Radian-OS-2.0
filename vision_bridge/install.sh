#!/usr/bin/env bash
# install.sh — Jetson Orin Nano setup for jetson_cam_bridge
#
# Run once after cloning the repo:
#   chmod +x install.sh
#   ./install.sh
#
# Prerequisites:
#   * JetPack 6.2.2 installed (L4T 36.5, Ubuntu 22.04 aarch64)
#   * Basler pylon 7.5.0 ARM64 deb downloaded to ~/pylon_7.5.0_aarch64.deb
#     (download from https://www.baslerweb.com/en/downloads/software-downloads/)
#   * This script is run from the project root (/home/radianjetson1/jetson_cam_bridge/)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "==> Project dir: $PROJECT_DIR"

# ── 1. Jumbo frames for GigE Vision ──────────────────────────────────────────
# GigE Vision performs best with 8192-byte packet payloads, which requires an
# interface MTU of at least 9000.  The Jetson's Realtek NIC supports jumbo frames.

echo ""
echo "==> Configuring jumbo frames on eth0 (MTU 9000)…"

# Immediate effect (lost on reboot without the next step)
sudo ip link set eth0 mtu 9000 || echo "    WARN: could not set MTU — check interface name"

# Persist across reboots via systemd-networkd
NETWORK_CONF=/etc/systemd/network/10-eth0.network
if [ ! -f "$NETWORK_CONF" ]; then
    sudo tee "$NETWORK_CONF" > /dev/null <<'EOF'
[Match]
Name=eth0

[Link]
MTUBytes=9000
EOF
    echo "    Wrote $NETWORK_CONF"
else
    echo "    $NETWORK_CONF already exists — skipping (verify MTU=9000 is set inside it)."
fi

# ── 2. Basler pylon SDK ───────────────────────────────────────────────────────
echo ""
echo "==> Installing Basler pylon SDK…"

PYLON_DEB=~/pylon_7.5.0_aarch64.deb
if [ -f "$PYLON_DEB" ]; then
    sudo dpkg -i "$PYLON_DEB"
    echo "    pylon SDK installed."
else
    echo "    WARNING: $PYLON_DEB not found."
    echo "    Download it from https://www.baslerweb.com/en/downloads/software-downloads/"
    echo "    and re-run this script, or install pypylon without the SDK for non-Basler cameras."
fi

# ── 3. TensorRT Python bindings ───────────────────────────────────────────────
# TensorRT ships with JetPack 6.  The Python bindings are in apt, not pip.
echo ""
echo "==> Installing TensorRT Python bindings (from JetPack apt repo)…"
sudo apt-get install -y --no-install-recommends \
    python3-libnvinfer \
    python3-libnvinfer-dev \
    python3-pycuda || echo "    WARN: some TRT packages may not be available — inference will be disabled."

# ── 4. Python virtualenv ──────────────────────────────────────────────────────
echo ""
echo "==> Creating Python virtual environment…"
cd "$PROJECT_DIR"

if [ ! -d venv ]; then
    python3 -m venv venv --system-site-packages
    # --system-site-packages lets the venv see the system-level tensorrt, pycuda,
    # and pypylon packages installed above without duplicating them.
    echo "    venv created."
else
    echo "    venv already exists — skipping."
fi

source venv/bin/activate

# ── 5. pip dependencies ───────────────────────────────────────────────────────
echo ""
echo "==> Installing pip dependencies…"
pip install --upgrade pip wheel setuptools
pip install -r requirements.txt

# Install pypylon if pylon SDK was installed
if dpkg -l | grep -q pylon; then
    echo ""
    echo "==> Installing pypylon (Basler Python binding)…"
    pip install pypylon || echo "    WARN: pypylon install failed — Basler SDK required."
fi

# ── 6. Copy .env ──────────────────────────────────────────────────────────────
echo ""
if [ ! -f .env ]; then
    cp .env.example .env
    echo "==> Created .env from .env.example — edit it now before starting the bridge."
else
    echo "==> .env already exists — not overwriting."
fi

# ── 7. systemd service ────────────────────────────────────────────────────────
echo ""
echo "==> Installing systemd service…"
sudo cp jetson_cam_bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable jetson_cam_bridge
echo "    Service installed.  Start with:  sudo systemctl start jetson_cam_bridge"
echo "    View logs with:                  journalctl -u jetson_cam_bridge -f"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "==> Install complete."
echo ""
echo "Next steps:"
echo "  1. Edit .env — set CAMERA_SERIAL (or leave empty for first found)"
echo "  2. Verify jumbo frames: ip link show eth0 | grep mtu"
echo "  3. Start the bridge: sudo systemctl start jetson_cam_bridge"
echo "  4. Check health:     curl http://localhost:8765/health"
echo "  5. View stream:      http://$(hostname -I | awk '{print \$1}'):8765/stream"
