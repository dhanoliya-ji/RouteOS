#!/bin/bash
# EC2 user-data for RouteOS on Ubuntu 24.04 LTS (t3.micro, AWS free tier).
# Paste this into "Advanced details -> User data" when launching the instance.
# It runs ONCE as root on first boot. Logs: /var/log/cloud-init-output.log
set -euxo pipefail

# --- Swap -------------------------------------------------------------------
# t3.micro has 1 GB of RAM. Postgres + Redis + a Python image holding OR-Tools
# will not fit reliably without swap; the build in particular gets OOM-killed.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  # Prefer RAM, but allow swap under pressure rather than OOM-killing a build.
  sysctl -w vm.swappiness=20
  echo 'vm.swappiness=20' > /etc/sysctl.d/99-swap.conf
fi

# --- Docker -----------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker ubuntu   # so you can run docker without sudo after re-login

echo "[user-data] done. SSH in and follow aws/AWS-DEPLOY.md step 6."
