#!/usr/bin/env bash
# Week 1 server hardening — run with sudo on a fresh Ubuntu 24.04 box
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# Firewall: deny in, allow out, web ports only
ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# SSH: no passwords, no root
install -m 0644 "$HERE/sshd-99-hardening.conf" /etc/ssh/sshd_config.d/99-hardening.conf
install -d -m 0755 /run/sshd
sshd -t

# Brute-force protection + automatic security updates
apt-get install -y fail2ban unattended-upgrades
systemctl enable --now fail2ban

# Services a cloud VM does not need
systemctl disable --now ModemManager fwupd udisks2 || true
systemctl mask packagekit
echo "hardening applied"
