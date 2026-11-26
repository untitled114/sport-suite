#!/bin/bash
# Sport-Suite EC2 Bootstrap
# Runs once on first launch via cloud-init.
# Sets up the base environment - code is deployed separately.
set -euo pipefail
exec > >(tee /var/log/user-data.log | logger -t user-data) 2>&1

echo "[bootstrap] Starting Sport-Suite bootstrap at $(date)"

# ============================================================================
# System packages
# ============================================================================
apt-get update -qq
apt-get install -y -qq \
  curl wget git vim tmux htop \
  build-essential libssl-dev libffi-dev \
  python3.11 python3.11-venv python3.11-dev python3-pip \
  postgresql-client \
  jq unzip \
  ca-certificates gnupg lsb-release \
  awscli

# ============================================================================
# Docker
# ============================================================================
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker

# ============================================================================
# Application user
# ============================================================================
APP_USER="app"
APP_HOME="/home/$APP_USER"
if ! id "$APP_USER" &>/dev/null; then
  useradd -m -s /bin/bash -G docker "$APP_USER"
fi

# The application user logs in with the deploy key and has no sudo; administration goes through
# the image's default user.
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "$APP_HOME/.ssh"
IMAGE_USER_HOME=$(getent passwd ubuntu | cut -d: -f6)
install -m 600 -o "$APP_USER" -g "$APP_USER" "$IMAGE_USER_HOME/.ssh/authorized_keys" "$APP_HOME/.ssh/authorized_keys"

# ============================================================================
# Data volume mount (/dev/xvdf -> /data)
# PostgreSQL data, ML models, and predictions live here.
# EBS persists independently of the instance.
# ============================================================================
DATA_DEVICE="/dev/xvdf"
DATA_MOUNT="/data"

# Wait for EBS volume to attach (up to 60s)
for i in $(seq 1 12); do
  if [ -b "$DATA_DEVICE" ]; then break; fi
  echo "[bootstrap] Waiting for $DATA_DEVICE to attach... ($i/12)"
  sleep 5
done

if [ -b "$DATA_DEVICE" ]; then
  # Format only if no filesystem present
  if ! blkid "$DATA_DEVICE" | grep -q "TYPE"; then
    echo "[bootstrap] Formatting $DATA_DEVICE as ext4"
    mkfs.ext4 -F "$DATA_DEVICE"
  fi

  mkdir -p "$DATA_MOUNT"
  mount "$DATA_DEVICE" "$DATA_MOUNT"

  # Persist mount across reboots
  DEVICE_UUID=$(blkid -s UUID -o value "$DATA_DEVICE")
  echo "UUID=$DEVICE_UUID $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab

  # Create directory structure on data volume
  mkdir -p \
    "$DATA_MOUNT/postgres" \
    "$DATA_MOUNT/models" \
    "$DATA_MOUNT/predictions" \
    "$DATA_MOUNT/logs" \
    "$DATA_MOUNT/backups"

  chown -R "$APP_USER:$APP_USER" "$DATA_MOUNT"
  echo "[bootstrap] Data volume mounted at $DATA_MOUNT"
else
  echo "[bootstrap] WARNING: $DATA_DEVICE not found - data volume may not have attached yet"
fi

# ============================================================================
# Project directories
# ============================================================================
PROJECT_DIR="$APP_HOME/sport-suite"
mkdir -p "$PROJECT_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_HOME"

# The data volume's log directory, linked into the project. Model files are linked by the
# deploy once the code is in place.
sudo -u "$APP_USER" ln -sfn "$DATA_MOUNT/logs" "$PROJECT_DIR/logs"

# ============================================================================
# Python tooling
# ============================================================================
# The system python3 stays as the image ships it (apt and cloud-init depend on it); the
# application gets its own virtual environment on Python 3.11.
sudo -u "$APP_USER" python3.11 -m venv "$APP_HOME/venv"
sudo -u "$APP_USER" "$APP_HOME/venv/bin/pip" install --upgrade pip uv

# ============================================================================
# AWS CLI profile for S3 (uses instance role - no keys needed)
# ============================================================================
IMDS_TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
AWS_REGION=$(curl -s -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" \
  "http://169.254.169.254/latest/meta-data/placement/region")
mkdir -p "$APP_HOME/.aws"
cat > "$APP_HOME/.aws/config" <<EOF
[default]
region = $AWS_REGION
output = json
EOF
chown -R "$APP_USER:$APP_USER" "$APP_HOME/.aws"

# Backups are scheduled by Airflow (airflow/dags/ops_db_backup.py runs
# scripts/ops/backup_databases.sh nightly), so no cron entry is installed here.

# ============================================================================
# SSH hardening
# ============================================================================
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl reload sshd

# ============================================================================
# Done
# ============================================================================
echo "[bootstrap] Sport-Suite bootstrap complete at $(date)"
echo "[bootstrap] Next: deploy the code, then start services"
