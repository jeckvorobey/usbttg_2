# tg_userbot — Telegram Userbot с Gemini AI

Userbot на базе Telethon, который отвечает на сообщения в группе через Gemini AI и самостоятельно инициирует разговоры по расписанию.

---

## Быстрый старт

### 1. Клонировать и создать окружение

```bash
git clone <repo-url>
cd usbttg

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Настроить `.env`

```bash
cp .env.example .env
nano .env
```

Заполнить обязательные поля:

| Переменная       | Где взять                                              |
|------------------|--------------------------------------------------------|
| `API_ID`         | [my.telegram.org](https://my.telegram.org) → API development tools |
| `API_HASH`       | Там же                                                 |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `SESSION_NAME`   | Имя файла сессии в `data/sessions/` (без `.session`)  |
| `PROXY_URL`      | Необязательно. Пример: `http://user:pass@host:port`   |

### 3. Добавить пользователей в whitelist

```bash
nano data/whitelist.md
```

Добавить Telegram `user_id` (один на строку):

```
123456789
7092621358
```

Узнать свой `user_id` можно написав боту `@userinfobot`.

### 4. Запустить

```bash
python run.py
```

---

## Запуск в фоне (постоянная работа)

### Вариант 1 — systemd (рекомендуется)

Создать файл службы:

```bash
sudo nano /etc/systemd/system/tg-userbot.service
```

Вставить (заменить `/home/serg/Develop/usbttg` и `serg` на свои):

```ini
[Unit]
Description=Telegram Userbot с Gemini AI
After=network.target

[Service]
Type=simple
User=serg
WorkingDirectory=/home/serg/Develop/usbttg
ExecStart=/home/serg/Develop/usbttg/.venv/bin/python run.py
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
source .venv/bin/activate
python run.py
```

Отключиться от сессии (бот продолжит работать): `Ctrl+A`, затем `D`

Вернуться к сессии:

```bash
screen -r userbot
```

---

### Вариант 3 — nohup (минимальный)

```bash
source .venv/bin/activate
nohup python run.py > logs/userbot.log 2>&1 &
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

python send_hello.py
```

---

## Структура проекта

```
├── run.py              — Точка входа, запускает userbot
├── send_hello.py       — Разовая отправка сообщения
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
    └── sessions/       — Файлы сессий Telethon (не удалять!)
```

---

## Тесты

```bash
pytest
```

---

## Важно

- Файл `data/sessions/*.session` — это авторизованная сессия Telegram. **Не удалять и не публиковать.**
- `.env` с секретами добавлен в `.gitignore`.
- Userbot работает от лица реального аккаунта Telegram — используй осторожно, не нарушая ToS Telegram.
