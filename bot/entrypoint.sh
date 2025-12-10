#!/usr/bin/env bash
set -e

mkdir -p /var/log/app/bot
echo "[entrypoint] running migrations..." | tee -a /var/log/app/bot/migrate.log
alembic upgrade head 2>&1 | tee -a /var/log/app/bot/migrate.log
echo "[entrypoint] starting bot..." | tee -a /var/log/app/bot/migrate.log
python -m bot.app.main
