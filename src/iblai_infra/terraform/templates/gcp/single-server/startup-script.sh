#!/bin/bash
# ibl.ai Infrastructure - GCP Compute Engine Startup Script
# Ubuntu 22.04 LTS
#
# Mirrors the AWS user_data.sh. One GCP-specific difference: the login user is
# created by the guest agent from metadata ssh-keys and can lag this script, so
# we wait for (and fall back to creating) the `ubuntu` user before touching it.

set -euo pipefail

exec > >(tee /var/log/startup-script.log) 2>&1
echo "=== Startup script started at $(date) ==="

# Ensure the login user exists before we add it to groups / chown its files.
# The GCP guest agent provisions it from metadata ssh-keys asynchronously.
for _ in $(seq 1 30); do
    id ubuntu >/dev/null 2>&1 && break
    sleep 2
done
id ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu

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

# Add ubuntu user to docker group
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

# AWS CLI v2 (the platform stores media/static/backups on AWS S3)
curl -s "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip -q awscliv2.zip
./aws/install
rm -rf awscliv2.zip aws/

# Application directory
mkdir -p /opt/ibl/{logs,data,config,backups}
chown -R ubuntu:ubuntu /opt/ibl

# Log rotation
cat > /etc/logrotate.d/ibl <<'LOGROTATE_EOF'
/opt/ibl/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0640 ubuntu ubuntu
}
LOGROTATE_EOF

# Firewall (host-level, in addition to the VPC firewall rules)
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# Timezone
timedatectl set-timezone UTC

# Systemd service placeholder
cat > /etc/systemd/system/ibl-app.service <<'SERVICE_EOF'
[Unit]
Description=IBL Application
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/ibl
ExecStart=/bin/echo "IBL Application - not yet configured"
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE_EOF

systemctl daemon-reload
systemctl enable ibl-app.service

echo "=== Startup script completed at $(date) ==="
