#!/bin/bash
#
# init-vm-phase1.sh
#
# NNTPS course VM setup — Phase 1 (run once, ends in a reboot).
# After reboot, run init-vm-phase2.sh to finish (OVS + sanity checks).
#
# Run as the normal course user with sudo rights, NOT as root directly:
#   chmod +x init-vm-phase1.sh
#   ./init-vm-phase1.sh

set -euo pipefail

echo "=== NNTPS VM setup — phase 1 ==="

# --- VirtualBox Guest Additions -------------------------------------------
# Kernel build tools must be present BEFORE the guest additions package,
# since its DKMS module build is triggered at install time.
echo "--> Installing kernel build tools (needed for Guest Additions DKMS build)..."
sudo dnf install -y kernel-devel kernel-headers make gcc dkms

echo "--> Installing VirtualBox Guest Additions..."
sudo dnf install -y virtualbox-guest-additions
sudo systemctl restart vboxservice

# --- Remove bloatware -------------------------------------------------------
echo "--> Removing unneeded default XFCE apps..."
sudo dnf remove -y \
  libreoffice* \
  thunderbird \
  claws-mail \
  pidgin \
  hexchat \
  audacious \
  parole \
  gimp* \
  xsane* \
  shotwell \
  xfce4-dict \
  galculator \
  ristretto || true   # don't hard-fail if some of these aren't installed

sudo dnf autoremove -y
sudo dnf clean all

# --- Update everything -------------------------------------------------------
echo "--> Upgrading system packages..."
sudo dnf upgrade --refresh -y

# --- NNTPS toolchain ----------------------------------------------------------
echo "--> Installing NNTPS tools..."
sudo dnf install -y clang llvm elfutils-libelf-devel libbpf-devel \
  git net-tools wireshark mininet python3-tkinter xorg-x11-xbitmaps 
# Last two needed for MiniEdit

# Let the course user capture packets without sudo
sudo usermod -aG wireshark "$USER"

# --- SELinux -----------------------------------------------------------------
# Disabled because several labs (BPF loading, custom OVS flows, container
# networking) hit SELinux denials. 
# Takes effect after reboot.
echo "--> Disabling SELinux (takes effect after reboot)..."
sudo sed -i 's/^SELINUX=enforcing/SELINUX=disabled/' /etc/selinux/config
sudo setenforce 0 || true   # best-effort for the remainder of this session

# --- bpfman ------------------------------------------------------------------
echo "--> Installing and enabling bpfman..."
sudo dnf install -y bpfman

# Without this, `bpfman load file` (loading a plain local .o, no OCI image
# involved) crashes on startup trying to initialize Cosign/sigstore image
# verification — it fails to parse its embedded TUF root metadata. 
echo "--> Configuring bpfman to skip Cosign/sigstore image verification..."
sudo mkdir -p /etc/bpfman
sudo tee /etc/bpfman/bpfman.toml > /dev/null <<'BPFMAN_EOF'
[signing]
allow_unsigned = true
verify_enabled = false
BPFMAN_EOF

sudo systemctl daemon-reload
sudo systemctl enable --now bpfman.socket

# --- VS Code -------------------------------------------------------------------
echo "--> Adding Microsoft repo and installing VS Code..."
sudo rpm --import https://packages.microsoft.com/keys/microsoft.asc
sudo sh -c 'echo -e "[code]\nname=Visual Studio Code\nbaseurl=https://packages.microsoft.com/yumrepos/vscode\nenabled=1\ngpgcheck=1\ngpgkey=https://packages.microsoft.com/keys/microsoft.asc" > /etc/yum.repos.d/vscode.repo'
sudo dnf install -y code

# --- Docker --------------------------------------------------------------------
# docker-compose-plugin (not docker-compose) gives us `docker compose` v2,
# which is what every nntps compose.yml assumes.
echo "--> Installing Docker (moby-engine) + Compose v2 plugin..."
#sudo dnf install -y moby-engine docker-compose-plugin
#sudo systemctl enable --now docker
#sudo usermod -aG docker "$USER"
sudo dnf install -y moby-engine docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

echo ""
echo "=== Phase 1 complete. ==="
echo "A reboot is required now for SELinux + docker/wireshark group membership"
echo "+ VirtualBox kernel modules to fully take effect."
echo ""
echo "After reboot, log back in and run: ./init-vm-phase2.sh"
echo ""
read -p "Reboot now? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    sudo reboot
else
    echo "Remember to reboot manually before running phase 2."
fi
