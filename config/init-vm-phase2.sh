#!/bin/bash
#
# init-vm-phase2.sh
#
# NNTPS course VM setup — Phase 2 (run once, after rebooting from phase 1).
#
#   chmod +x init-vm-phase2.sh
#   ./init-vm-phase2.sh

set -euo pipefail

echo "=== NNTPS VM setup — phase 2 ==="

# Resolve the repo root as one level up from this script's own folder
# (this script is expected to live in <repo>/config/init-vm-phase2.sh)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Open vSwitch --------------------------------------------------------------
echo "--> Starting and enabling Open vSwitch..."
sudo systemctl start openvswitch
sudo systemctl enable openvswitch

# --- Sanity checks ---------------------------------------------------------------
echo ""
echo "=== Sanity checks ==="

echo -n "SELinux status: "
getenforce

echo -n "Docker group membership: "
groups "$USER" | grep -q docker && echo "OK" || echo "MISSING — did you reboot after phase 1?"

echo -n "Wireshark group membership: "
groups "$USER" | grep -q wireshark && echo "OK" || echo "MISSING — did you reboot after phase 1?"

echo -n "Docker service: "
systemctl is-active docker

echo -n "docker compose (v2 plugin): "
docker compose version || echo "MISSING — check docker-compose-plugin install"

echo -n "Open vSwitch service: "
systemctl is-active openvswitch

echo -n "bpfman socket: "
systemctl is-active bpfman.socket

echo -n "Mininet: "
mn --version 2>/dev/null || echo "MISSING"

# --- Make repo scripts executable ------------------------------------------------
echo ""
echo "=== Making repo scripts executable ==="
find "$REPO_ROOT" -maxdepth 1 -iname "*.sh" -exec chmod +x {} \;
find "$REPO_ROOT" -mindepth 2 -iname "*.sh" -exec chmod +x {} \; 2>/dev/null || true
echo "Done (repo root: $REPO_ROOT)"

echo ""
echo "=== Setup complete ==="
echo "To start the tools:"
echo "  cd $REPO_ROOT"
echo "  ./floodlight-start.sh   # Floodlight controller + web UI"
echo "  ./ryu-start.sh          # Ryu controller"
echo "  ./mininet-start.sh      # Mininet network emulation"
