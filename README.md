# tg_userbot — Telegram Userbot с Gemini AI

Userbot на базе Telethon, который отвечает на сообщения в группе через Gemini AI и самостоятельно инициирует разговоры по расписанию.

---

## Быстрый старт

### 0. Установить uv (если ещё нет)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

После установки перезапусти терминал или выполни `source ~/.bashrc` (или `~/.zshrc`).

### 1. Клонировать и установить зависимости

```bash
git clone <repo-url>
cd usbttg
uv sync
```

`uv sync` сам создаст виртуальное окружение `.venv` и установит все зависимости из `uv.lock`. Python 3.11+ устанавливать отдельно не нужно — если нужной версии нет, `uv` скачает её сам.

### 2. Настроить `.env`

```bash
cp .env.example .env
nano .env
```

Заполнить обязательные поля:

| Переменная       | Где взять                                                           |
|------------------|---------------------------------------------------------------------|
| `API_ID`         | [my.telegram.org](https://my.telegram.org) → API development tools |
| `API_HASH`       | Там же                                                              |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/app/apikey)      |
| `SESSION_STRING` | Строковая Telethon-сессия (см. раздел ниже)                        |
| `PROXY_URL`      | Необязательно. Пример: `http://user:pass@host:port`                |
| `GROUP_CHAT_ID`  | Необязательно. `chat_id` группы для фильтрации входящих сообщений  |
| `GROUP_TARGET`   | Необязательно. `@username` или ссылка группы для исходящих постов  |

Дополнительно можно настроить устойчивость Gemini при перегрузке:

- `GEMINI_FALLBACK_MODEL` — резервная модель, на которую бот переключится после неудачных повторов основной.
- `GEMINI_MAX_RETRIES` — число повторов на одну модель.
- `GEMINI_RETRY_BACKOFF_SECONDS` — базовая задержка для экспоненциального backoff.
- `GEMINI_RETRY_JITTER_SECONDS` — случайная добавка к задержке, чтобы не бить в API синхронно.

### 3. Получить SESSION_STRING

`SESSION_STRING` — это строковая Telethon-сессия, которая заменяет логин/пароль. Получить её можно один раз:

```bash
uv run python -c "
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import os

api_id = int(input('API_ID: '))
api_hash = input('API_HASH: ')

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print('SESSION_STRING:', client.session.save())
"
```

Скопируй выведенную строку и вставь в `.env` в поле `SESSION_STRING`.

**Внимание:** это авторизованная сессия твоего аккаунта. Не публикуй её и не передавай третьим лицам.

### 4. Добавить пользователей в whitelist

```bash
nano data/whitelist.md
```

Добавить Telegram `user_id` (один на строку):

```
123456789
7092621358
```

Узнать свой `user_id` можно написав боту `@userinfobot`.

### 5. Запустить

```bash
uv run python run.py
```

---

## Запуск в фоне (постоянная работа)

### Вариант 0 — Coolify

В репозитории есть `Dockerfile` для деплоя как background worker.

Что настроить в Coolify:

- Тип сервиса: worker/background service, без публичного порта.
- Build Pack: Dockerfile.
- Persistent Volume: примонтировать в `/data`.
- Environment Variables: передать `API_ID`, `API_HASH`, `GEMINI_API_KEY`, `SESSION_STRING`, `SETTINGS_PATH` и при необходимости `GROUP_CHAT_ID`, `GROUP_TARGET`, `PROXY_URL`. Whitelist остаётся в `config/settings.toml`.

Для автопоста по расписанию в каналы и супергруппы лучше задавать `GROUP_TARGET` (`@username` или `https://t.me/...`). Одного `GROUP_CHAT_ID=-100...` часто недостаточно: Telethon нужен резолвнутый `entity` с `access_hash`.

Важно:

- В контейнере по умолчанию используется `DB_PATH=/data/history.db`, чтобы SQLite переживал рестарты и перевыкатки.
- `ai/prompts/topics.md` и `ai/prompts/*.md` входят в образ, отдельный volume для них не нужен.
- `.env` в образ не копируется, секреты нужно задавать через интерфейс Coolify.

### Вариант 1 — systemd (рекомендуется)

Создать файл службы:

```bash
sudo nano /etc/systemd/system/tg-userbot.service
```

Вставить (заменить пути и имя пользователя на свои):

```ini
[Unit]
Description=Telegram Userbot с Gemini AI
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/usbttg
ExecStart=/path/to/usbttg/.venv/bin/python run.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Активировать и запустить:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tg-userbot
sudo systemctl start tg-userbot
```

Проверить статус:

```bash
sudo systemctl status tg-userbot
```

Смотреть логи:

```bash
sudo journalctl -u tg-userbot -f
```

---

### Вариант 2 — screen (проще, без прав sudo)

```bash
screen -S userbot
uv run python run.py
```

Отключиться от сессии (бот продолжит работать): `Ctrl+A`, затем `D`

Вернуться к сессии:

```bash
screen -r userbot
```

---

### Вариант 3 — nohup (минимальный)

```bash
nohup uv run python run.py > logs/userbot.log 2>&1 &
echo $! > userbot.pid
```

Остановить:

```bash
kill $(cat userbot.pid)
```

---

## Разовая отправка сообщения

Скрипт `send_hello.py` позволяет отправить сообщение вручную:

```bash
# Отредактировать TARGET в send_hello.py
nano send_hello.py  # TARGET = "@username" или "+79XXXXXXXXXX"

uv run python send_hello.py
```

---

## Структура проекта

```
├── run.py              — Точка входа, запускает userbot
├── send_hello.py       — Разовая отправка сообщения
├── pyproject.toml      — Зависимости проекта (управляется uv)
├── uv.lock             — Зафиксированные версии зависимостей
├── .env                — Секреты (не в git)
├── .env.example        — Шаблон настроек
├── ai/
│   ├── gemini.py       — Клиент Gemini API
│   ├── history.py      — История диалогов в SQLite
│   └── prompts/        — Промты в формате .md
├── userbot/
│   ├── client.py       — Telethon клиент
│   ├── handlers.py     — Обработчики сообщений
│   └── scheduler.py    — Планировщик разговоров
├── core/
│   └── config.py       — Настройки через pydantic-settings
└── data/
    ├── whitelist.md    — Список разрешённых user_id
    ├── topics.md       — Темы для инициирования разговора
```

---

## Тесты

```bash
uv run pytest
```

---

## Важно

- `SESSION_STRING` — это авторизованная сессия Telegram. **Не публиковать и не логировать.**
- `.env` с секретами добавлен в `.gitignore`.
- Userbot работает от лица реального аккаунта Telegram — используй осторожно, не нарушая ToS Telegram.
