#!/usr/bin/env bash
set -e

mkdir -p /var/log/app/finance_bot
echo "[entrypoint] starting finance_bot..." | tee -a /var/log/app/finance_bot/run.log
python -m finance_bot.app.main
