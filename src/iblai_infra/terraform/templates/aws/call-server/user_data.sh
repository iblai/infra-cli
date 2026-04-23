#!/bin/bash
# ibl.ai Infrastructure — Call Server (LiveKit) EC2 Bootstrap
# Ubuntu 22.04 LTS
#
# Rendered via Terraform templatefile() — ${enable_sip} is interpolated.

set -euo pipefail

exec > >(tee /var/log/user-data.log) 2>&1
echo "=== Call-server user data started at $(date) ==="

# System updates
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

# Essential packages
apt-get install -y \
    curl wget git vim htop net-tools unzip jq \
    software-properties-common apt-transport-https \
    ca-certificates gnupg lsb-release

# Docker
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# Docker daemon configuration
cat > /etc/docker/daemon.json <<'DOCKER_EOF'
{
  "exec-opts": ["native.cgroupdriver=systemd"],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  },
  "storage-driver": "overlay2"
}
DOCKER_EOF
systemctl restart docker

# AWS CLI v2
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip -q awscliv2.zip
./aws/install
rm -rf awscliv2.zip aws/

# Application directory
mkdir -p /opt/ibl/{logs,data,config,backups}
chown -R ubuntu:ubuntu /opt/ibl

# Firewall — mirrors the AWS security group (defence-in-depth).
# Core LiveKit ports always open; SIP ports conditional on enable_sip.
ufw allow 22/tcp          comment 'SSH'
ufw allow 80/tcp          comment 'HTTP (ACME)'
ufw allow 443/tcp         comment 'HTTPS / TURN-TLS fallback'
ufw allow 7880/tcp        comment 'LiveKit API/WebSocket'
ufw allow 7881/tcp        comment 'LiveKit ICE/TCP'
ufw allow 7882/udp        comment 'LiveKit ICE/UDP mux'
ufw allow 50000:60000/udp comment 'LiveKit ICE/UDP host'
ufw allow 5349/tcp        comment 'LiveKit TURN/TLS'
ufw allow 3478/udp        comment 'LiveKit TURN/UDP + STUN'

%{ if enable_sip ~}
ufw allow 5060/udp        comment 'LiveKit SIP UDP'
ufw allow 5060/tcp        comment 'LiveKit SIP TCP'
ufw allow 5061/tcp        comment 'LiveKit SIP TLS'
ufw allow 10000:20000/udp comment 'LiveKit SIP RTP'
%{ endif ~}

ufw --force enable

# Kernel tunings for many concurrent UDP streams
cat > /etc/sysctl.d/99-livekit.conf <<'SYSCTL_EOF'
# LiveKit handles many concurrent UDP flows; raise buffer ceilings.
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.core.netdev_max_backlog = 5000
# Expand ephemeral port range (50k–60k is taken by LiveKit ICE)
net.ipv4.ip_local_port_range = 20000 49999
SYSCTL_EOF
sysctl --system >/dev/null

# Timezone
timedatectl set-timezone UTC

# Systemd service placeholder (Ansible will configure the real call stack)
cat > /etc/systemd/system/ibl-call.service <<'SERVICE_EOF'
[Unit]
Description=IBL Call Server (LiveKit)
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/ibl
ExecStart=/bin/echo "IBL Call Server - not yet configured"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE_EOF

systemctl daemon-reload
systemctl enable ibl-call.service

echo "=== Call-server user data completed at $(date) ==="
