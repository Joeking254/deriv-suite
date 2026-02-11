#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  echo "Please run as root: sudo $0"
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/deriv-suite}"
RUN_USER="${RUN_USER:-ubuntu}"
SRC_DIR="${SRC_DIR:-$(pwd)}"

echo "Installing dependencies..."
apt update
apt install -y python3-venv rsync

echo "Preparing application directory: $APP_DIR"
mkdir -p "$APP_DIR"

if [ "$SRC_DIR" != "$APP_DIR" ]; then
  echo "Copying project from $SRC_DIR to $APP_DIR"
  rsync -a --exclude ".venv" --exclude "logs" "$SRC_DIR"/ "$APP_DIR"/
fi

mkdir -p "$APP_DIR/logs"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

if [ ! -f "$APP_DIR/bot/.env" ]; then
  echo "Missing $APP_DIR/bot/.env"
  echo "Create it from .env.example and set APP_ID and API_TOKEN."
  exit 1
fi

chmod 600 "$APP_DIR/bot/.env"

echo "Creating Python virtual environment..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/bot/requirements.txt"

echo "Installing systemd services..."
cat > /etc/systemd/system/deriv-bot.service <<EOF
[Unit]
Description=Deriv Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR/bot
EnvironmentFile=$APP_DIR/bot/.env
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/deriv-web.service <<EOF
[Unit]
Description=Deriv Signal Desk (Web)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR/backend
EnvironmentFile=$APP_DIR/bot/.env
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/backend/serve.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now deriv-bot
systemctl enable --now deriv-web
systemctl status --no-pager deriv-bot
systemctl status --no-pager deriv-web

echo "Done."
echo "Logs: $APP_DIR/logs/bot.log"
