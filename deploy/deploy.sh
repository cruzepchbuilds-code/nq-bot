#!/bin/bash
# Deploy update — pull latest code and restart services
# Run from your local machine: ssh nqbot@YOUR_VPS_IP 'bash nq_bot/deploy/deploy.sh'
# Or run directly on the VPS

set -e
cd /home/nqbot/nq_bot

echo "Pulling latest code ..."
git pull origin main

echo "Restarting trading bot ..."
sudo systemctl restart nqbot.service

echo "Done. Status:"
sudo systemctl status nqbot.service --no-pager -l
