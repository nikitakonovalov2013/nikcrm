#!/usr/bin/env bash
set -e

mkdir -p /var/log/app/web
echo "[entrypoint] running migrations..." | tee -a /var/log/app/web/migrate.log
alembic upgrade head 2>&1 | tee -a /var/log/app/web/migrate.log
echo "[entrypoint] starting web..." | tee -a /var/log/app/web/migrate.log
exec uvicorn web.app.main:app --host 0.0.0.0 --port 8000
