FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 1) Метаданные и миграции (для кеша слоёв)
COPY pyproject.toml ./
COPY alembic.ini ./ 
COPY migrations ./migrations

# 2) Исходники пакетов ДО установки editable
COPY shared ./shared
COPY web ./web
# Если в pyproject указан пакет bot — добавь:
COPY bot ./bot

# 3) Установка проекта
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -e .

# 4) Entrypoint
COPY web/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
