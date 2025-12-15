#!/usr/bin/env bash
set -e

mkdir -p /var/log/app/bot
echo "[entrypoint] starting bot..." | tee -a /var/log/app/bot/migrate.log
python -m bot.app.main
