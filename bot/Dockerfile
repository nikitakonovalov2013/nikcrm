FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 1) Копируем метаданные (для кеша)
COPY pyproject.toml ./
COPY alembic.ini ./ 
COPY migrations ./migrations

# 2) Копируем исходники пакетов ДО установки editable
COPY shared ./shared
COPY bot ./bot
# Если в pyproject есть пакет web — обязательно:
COPY web ./web

# 3) Теперь можно ставить проект
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -e .

# 4) Entrypoint
COPY bot/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
