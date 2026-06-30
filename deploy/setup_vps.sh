#!/bin/bash
# VPS setup script — run once as root on a fresh Ubuntu 22.04 droplet
# Usage: bash deploy/setup_vps.sh

set -e

echo "=== CruzCapital NQ Bot — VPS Setup ==="

# System deps
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip git

# Create user
useradd -m -s /bin/bash nqbot 2>/dev/null || echo "User nqbot already exists"
mkdir -p /home/nqbot/logs
chown -R nqbot:nqbot /home/nqbot/logs

# Clone repo (update URL to your actual repo)
if [ ! -d /home/nqbot/nq_bot ]; then
    sudo -u nqbot git clone https://github.com/YOUR_USERNAME/nq_bot.git /home/nqbot/nq_bot
fi

# Python venv
sudo -u nqbot python3 -m venv /home/nqbot/nq_bot/venv
sudo -u nqbot /home/nqbot/nq_bot/venv/bin/pip install -q -r /home/nqbot/nq_bot/requirements.txt

# .env file (edit with your keys)
if [ ! -f /home/nqbot/nq_bot/.env ]; then
    cat > /home/nqbot/nq_bot/.env << 'EOF'
DATABENTO_API_KEY=PASTE_YOUR_KEY_HERE
# Add Tradovate creds here when ready for live orders:
# TRADOVATE_USERNAME=
# TRADOVATE_PASSWORD=
# TRADOVATE_APP_ID=
# TRADOVATE_APP_VERSION=
# TRADOVATE_DEMO=1
EOF
    chown nqbot:nqbot /home/nqbot/nq_bot/.env
    chmod 600 /home/nqbot/nq_bot/.env
    echo ">>> Edit /home/nqbot/nq_bot/.env with your keys <<<"
fi

# Enable timezone
timedatectl set-timezone America/New_York

# Install systemd services
cp /home/nqbot/nq_bot/deploy/nqbot.service    /etc/systemd/system/
cp /home/nqbot/nq_bot/deploy/research.service  /etc/systemd/system/
cp /home/nqbot/nq_bot/deploy/research.timer    /etc/systemd/system/

systemctl daemon-reload
systemctl enable nqbot.service
systemctl enable research.timer
systemctl start  nqbot.service
systemctl start  research.timer

echo ""
echo "=== Setup complete ==="
echo "Trading bot:  systemctl status nqbot"
echo "Research:     systemctl status research.timer"
echo "Logs:         tail -f /home/nqbot/logs/trading.log"
