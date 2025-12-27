NikCRM: Telegram bot + Admin Web (FastAPI)

# Overview
- Telegram bot (aiogram 3) for employee registration via FSM.
- Admin moderation in Telegram (approve/reject with inline buttons).
- Admin web interface (FastAPI + Jinja2 + HTMX + Alpine) to manage users, edit, blacklist/delete, send messages, and broadcast.
- Shared async SQLAlchemy 2.0 models/migrations with Alembic.
- Dockerized with docker-compose: db (Postgres), bot, web.

# Tech stack
- Python 3.11/3.12
- aiogram 3.x
- FastAPI, Jinja2, HTMX, Alpine.js
- SQLAlchemy 2.0 (async) + asyncpg
- Alembic
- Pydantic v2 (pydantic-settings)
- Docker + docker-compose

# Structure
```
.
├─ bot/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ config.py
│  │  ├─ handlers/
│  │  ├─ keyboards/
│  │  ├─ states/
│  │  ├─ services/
│  │  ├─ repository/
│  │  └─ utils/
│  ├─ Dockerfile
│  └─ entrypoint.sh
├─ web/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ config.py
│  │  ├─ services/
│  │  ├─ repository.py
│  │  ├─ templates/
│  │  └─ static/
│  ├─ Dockerfile
│  └─ entrypoint.sh
├─ shared/           # shared config/db/models/enums/schemas
├─ migrations/       # Alembic (async engine)
├─ pyproject.toml
├─ alembic.ini
├─ docker-compose.yml
├─ .env.example
└─ README.md
```

# Configuration
Copy .env.example to .env and fill values:
```
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_IDS=123456789,987654321

POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=app
POSTGRES_USER=app
POSTGRES_PASSWORD=app

DATABASE_URL=postgresql+asyncpg://app:app@db:5432/app

WEB_BASE_URL=http://localhost:8000
WEB_JWT_SECRET=change_me
JWT_TTL_MINUTES=10

LOG_LEVEL=INFO
```
Notes:
- ADMIN_IDS is a comma-separated list of Telegram user IDs with admin rights.
- WEB_JWT_SECRET is used for short-lived admin login links.

# Run
Prerequisites: Docker and docker-compose.

Build and start:
```
docker compose up --build
```
Services:
- db: Postgres 16 (exposes 5432)
- bot: aiogram bot process (polling)
- web: FastAPI admin at http://localhost:8000

Alembic migrations are applied automatically by both bot and web entrypoints before start.

# Bot flows
- /start shows greeting.
- Reply menu:
  - For new users: "Зарегистрироваться" starts FSM.
  - For approved users: "Профиль" shows saved data.
  - For admins: "Сотрудники" sends a short-lived login link to the web admin.

Registration FSM steps:
- Имя
- Фамилия
- Дата рождения (ДД.ММ.ГГГГ)
- Ставка (в тысячах)
- График: 2/2, 5/2, 4/3
- Должность: Руководитель, Сборщик заказов, Упаковщик, Мастер

After submit:
- User saved with status PENDING.
- All admins receive a detailed application with inline buttons: Подтвердить/Отклонить.
- Approve → user becomes APPROVED, user notified, "Профиль" available.
- Reject → user becomes BLACKLISTED, user notified, bot blocks functionality.

# Web admin
Auth:
- Admin presses "Сотрудники" in the bot → receives one-time URL /auth?token=<JWT> (valid 5–10 minutes).
- Opening the link sets an auth cookie and redirects to the dashboard.

UI (http://localhost:8000):
- Table of users: TG ID, names, birth date, rate, schedule, position, status, registered date.
- Row action "Открыть" → modal to edit fields, save, blacklist, delete, and send a message.
- Broadcast: selectable users (default all APPROVED). Sends message only to APPROVED users.

Actions logged:
- EDIT, BLACKLIST, DELETE, MESSAGE, BROADCAST into admin_actions table.

# Development tips
- Models are shared from shared/models.py. Keep DB changes in Alembic migrations (migrations/versions/...).
- Async session manager in shared/db.py; import and use get_async_session.
- Pydantic settings in shared/config.py load from .env.

# Common issues
- Bot does not start: ensure BOT_TOKEN set in .env and reachable from container.
- Admin link invalid: ensure WEB_BASE_URL and WEB_JWT_SECRET in .env; token expires after JWT_TTL_MINUTES.
- Database connection: containers depend_on healthcheck; check logs with `docker compose logs db`.

# Timezone (Docker)
Containers are expected to run in Europe/Moscow timezone so scheduled reminders/reports match local time.

Check inside a running container:
```
docker exec -it <container_name> date
```
Expected output should reflect Moscow time (MSK, +0300) and correct date/time.

# License
MIT

# Полная документация (RU)

## Назначение
Проект для регистрации сотрудников через Telegram‑бота и управления ими через веб‑интерфейс администратора. Запуск одним docker‑compose. Авторизация админов в вебе по одноразовой JWT‑ссылке, которую генерирует бот.

## Фичи
- Регистрация в боте (FSM): Имя → Фамилия → Дата рождения (ДД.ММ.ГГГГ) → Ставка (к) → График (2/2, 5/2, 4/3) → Должность.
- Модерация админом: заявка отправляется всем админам, подтверждение/отклонение через inline‑кнопки.
- Статусы: PENDING, APPROVED, REJECTED/BLACKLISTED. Черный список блокирует функционал.
- Профиль: у утвержденных доступна кнопка «Профиль».
- Веб‑админка: таблица, модалки редактирования/черный список/удаление/сообщение, массовая рассылка (только APPROVED).
- Логи действий: `admin_actions` фиксирует EDIT/BLACKLIST/DELETE/MESSAGE/BROADCAST.

## Технологический стек
- Python 3.11+
- aiogram 3.x
- FastAPI
- Jinja2 + HTMX + Alpine.js (SSR‑фронтенд)
- SQLAlchemy 2.0 (async) + asyncpg
- Alembic (async окружение)
- PostgreSQL 16
- Docker / Docker Compose
- Pydantic v2 (pydantic‑settings)

## Архитектура
- bot ↔ db: бот пишет/читает пользователей и статусы.
- web ↔ db: веб управляет пользователями и логами.
- web → bot API: отправка сообщений/рассылок.
- bot → web: одноразовая JWT‑ссылка `/auth?token=...`.

```
┌────────┐       write/read       ┌──────────────┐
│  bot   │ ─────────────────────► │   Postgres   │
│(aiogram)│ ◄──────────────────── │   (db)       │
└───▲────┘                        └─────▲────────┘
    │  Telegram Bot API                 │
    │  (sendMessage)                    │
    ▼                                   │
┌────────┐   JWT /auth?token=...        │ SQLAlchemy async
│  web   │ ─────────────────────────────┘
│(FastAPI│  + Jinja2/HTMX)
└────────┘  http://localhost:8000
```

## Структура репозитория
```
.
├─ bot/
│  ├─ app/
│  │  ├─ main.py           # запуск aiogram 3
│  │  ├─ config.py
│  │  ├─ handlers/         # FSM регистрации, админ‑коллбеки, профиль
│  │  ├─ keyboards/        # reply/inline клавиатуры
│  │  ├─ states/           # состояния FSM
│  │  ├─ services/         # JWT‑ссылки и пр.
│  │  ├─ repository/       # доступ к БД
│  │  └─ utils/
│  ├─ Dockerfile
│  └─ entrypoint.sh        # alembic upgrade + запуск бота
├─ web/
│  ├─ app/
│  │  ├─ main.py           # FastAPI + роуты админки
│  │  ├─ config.py
│  │  ├─ services/
│  │  │  └─ messenger.py   # отправка сообщений в Telegram
│  │  ├─ repository.py     # логирование действий админа
│  │  ├─ templates/        # Jinja2 (base, index, partials/*)
│  │  └─ static/           # CSS
│  ├─ Dockerfile
│  └─ entrypoint.sh        # alembic upgrade + uvicorn
├─ shared/                 # единые config/db/models/enums/schemas
│  ├─ config.py
│  ├─ db.py
│  ├─ enums.py
│  ├─ models.py
│  └─ schemas.py
├─ migrations/             # Alembic (async env.py, versions/*)
├─ alembic.ini
├─ pyproject.toml
├─ docker-compose.yml
├─ .env.example
└─ README.md
```

## Переменные окружения (.env)
См. `.env.example`:
```
BOT_TOKEN=
ADMIN_IDS=123456789,987654321

POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=app
POSTGRES_USER=app
POSTGRES_PASSWORD=app

DATABASE_URL=postgresql+asyncpg://app:app@db:5432/app

WEB_BASE_URL=http://localhost:8000
WEB_JWT_SECRET=change_me
JWT_TTL_MINUTES=10

LOG_LEVEL=INFO
```
- BOT_TOKEN — токен бота.
- ADMIN_IDS — Telegram ID админов через запятую.
- DATABASE_URL — строка подключения SQLAlchemy (async) к Postgres.
- WEB_BASE_URL — базовый URL веб‑админки (используется ботом в ссылке).
- WEB_JWT_SECRET — секрет подписи JWT‑ссылок.
- JWT_TTL_MINUTES — время жизни ссылки (мин).

## Локальный запуск
Требования: Docker, Docker Compose.

1) Подготовка окружения:
```
cp .env.example .env
nano .env
```
2) Сборка и запуск:
```
docker compose up --build
```
3) Логи:
```
docker compose logs -f bot
docker compose logs -f web
```
4) Перезапуск сервиса:
```
docker compose restart web
```
5) Проверка:
- В Telegram: `/start` → «Зарегистрироваться» → пройти шаги.
- Админ (из ADMIN_IDS) получает inline‑кнопки Подтвердить/Отклонить.
- После подтверждения у пользователя доступен «Профиль».
- Кнопка «Сотрудники» у админа генерирует ссылку в админку → открыть в браузере.
- На вебе: таблица пользователей, модалки «Открыть», массовая рассылка.

## Миграции
Автозапуск через entrypoint обоих сервисов (`alembic upgrade head`).

Вручную из контейнера:
```
docker compose exec web alembic upgrade head
docker compose exec web alembic revision -m "change" --autogenerate
```
Локально (при установленном окружении):
```
alembic upgrade head
```

## Полезные команды
Полная очистка и пересборка:
```
docker compose down -v
docker compose up --build
```
Подключение к БД:
```
docker compose exec db psql -U app -d app
```
Логи последними 200 строками:
```
docker compose logs -f --tail=200 web
```

## Логирование
- Файлы логов в контейнерах:
  - bot: `/var/log/app/bot/app.log` (ротация по дням, хранится 7 копий), `/var/log/app/bot/migrate.log`
  - web: `/var/log/app/web/app.log` (ротация по дням, хранится 7 копий), `/var/log/app/web/migrate.log`
- Логи вынесены в volume и сохраняются при перезапусках:
  - compose volumes: `logs_bot` → монтируется в `/var/log/app/bot`, `logs_web` → в `/var/log/app/web`
- Уровень логирования управляется `LOG_LEVEL` (DEBUG для dev, INFO для prod).
- Как смотреть логи:
  - Через Docker: `docker compose logs -f bot` / `docker compose logs -f web`
  - Напрямую: открыть файлы в volume (`logs_bot`, `logs_web`).


## Деплой (Ubuntu 22.04/24.04)
1) Установка Docker/Compose:
```
sudo apt update
sudo apt install -y ca-certificates curl gnupg ufw
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```
2) Клонирование и конфиг:
```
git clone <repo-url> && cd <repo>
cp .env.example .env && nano .env
```
3) Подъем в фоне:
```
sudo docker compose up -d --build
```
4) UFW:
```
sudo ufw allow OpenSSH
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```
5) Nginx → web:8000 (пример на хосте):
```
server {
    listen 80;
    server_name example.com;

    proxy_read_timeout 60s;
    proxy_connect_timeout 60s;
    proxy_send_timeout 60s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```
Альтернатива: Nginx в Docker, проксируйте на `http://web:8000`, наружу откройте 80/443.

6) HTTPS (Let’s Encrypt):
```
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d example.com --email admin@example.com --agree-tos --redirect
sudo certbot renew --dry-run
```

## Troubleshooting
- Бот не стартует: проверьте `BOT_TOKEN`.
- Нет подключения к БД: `DATABASE_URL`, `docker compose logs db`.
- Миграции: `docker compose exec web alembic upgrade head`.
- Nginx: `nginx -t`, корректный `proxy_pass`, порт 8000 доступен локально.
- ADMIN_IDS: должны быть числа, разделенные запятой.
- JWT‑ссылка: `WEB_JWT_SECRET`, `WEB_BASE_URL`, TTL (`JWT_TTL_MINUTES`).
- Рассылка: только пользователи в статусе APPROVED и не BLACKLISTED.
