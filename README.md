# tg_userbot — swarm userbot с Gemini AI

Проект запускает `swarm` из нескольких Telegram userbot-аккаунтов в одном процессе.

Основной режим:
- все боты постоянно онлайн;
- если пользователь делает `reply` на сообщение конкретного бота, отвечает только этот бот;
- orchestrator по расписанию выбирает случайную пару ботов и запускает `A -> B` обмен;
- все сообщения сохраняются в SQLite;
- orchestrator использует persisted history, чтобы не повторять один и тот же вопрос изо дня в день;
- в ключевых потоках есть подробное логирование.

## Быстрый старт

### 1. Установка

```bash
git clone <repo-url>
cd usbttg
uv sync
```

### 2. Настроить `.env`

```bash
cp .env.example .env
```

Обязательные переменные:
- `API_ID`
- `API_HASH`
- `GEMINI_API_KEY`
- `SESSION_STRING_<BOT_ID>` для каждого бота из `settings.toml`
- `SETTINGS_PATH`

Пример:

```dotenv
API_ID=12345678
API_HASH=...
GEMINI_API_KEY=...
SESSION_STRING_ANNA=...
SESSION_STRING_MIKE=...
SETTINGS_PATH=config/settings.toml
```

### 3. Настроить `settings.toml`

Скопируй шаблон:

```bash
cp config/settings.example.toml config/settings.toml
```

Важные секции:
- `[app]` — режим `swarm`
- `[target]` — целевая группа
- `[storage]` — путь к SQLite
- `[prompts]` — базовые промты и директория persona-файлов
- `[swarm]`, `[swarm.schedule]`, `[swarm.orchestrator]`
- `[[swarm.bots]]` — список ботов

### 4. Получить `SESSION_STRING`

Для каждого аккаунта:

```bash
uv run python -c "
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input('API_ID: '))
api_hash = input('API_HASH: ')

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print(client.session.save())
"
```

Сохрани строку в `.env` как `SESSION_STRING_<BOT_ID>`.

### 5. Запуск

```bash
uv run python run.py
```

## Конфигурация swarm

Минимальный пример:

```toml
[app]
mode = "swarm"

[target]
group_chat_id = -1001234567890
group_target = "@my_group"

[storage]
db_path = "data/history.db"

[prompts]
base_dir = "ai/prompts"
topics_path = "ai/prompts/topics.md"
bot_profiles_dir = "ai/prompts/bots"

[swarm]
enabled = true
max_parallel_bots = 20
ignore_messages_from_swarm = true
reply_only_to_addressed_bot = true

[swarm.schedule]
active_windows_utc = ["10-11", "16-18"]
initiator_offset_minutes = [0, 30]
responder_delay_minutes = [3, 10]
max_turns_per_exchange = 2
pair_cooldown_slots = 1

[swarm.orchestrator]
tick_seconds = 30
silence_timeout_minutes = 60
skip_if_recent_human_activity = true

[[swarm.bots]]
id = "anna"
session_env = "SESSION_STRING_ANNA"
persona_file = "anna.md"
enabled = true
temperature = 0.9

[[swarm.bots]]
id = "mike"
session_env = "SESSION_STRING_MIKE"
persona_file = "mike.md"
enabled = true
temperature = 0.8
```

## Как это работает

### Addressed reply

1. Боты постоянно слушают группу.
2. Если пользователь отвечает на сообщение `bot_X`, отвечает только `bot_X`.
3. Сообщения от других swarm-ботов игнорируются.
4. Human reply имеет приоритет над scheduled send через per-bot coordinator.

### Scheduled exchange

1. Orchestrator работает по UTC-окнам.
2. Выбирается случайная пара `initiator -> responder`.
3. Persisted state используется для anti-repeat по парам, темам и recent questions.
4. Вопрос и ответ сохраняются в SQLite с `exchange_id`.

## Логирование

В логах видно:
- запуск и reconnect каждого бота;
- причины ignore/handle во `reply_router`;
- выбор пары и темы в orchestrator;
- причины `skip`;
- записи exchange и сообщений в SQLite.

Локально:

```bash
uv run python run.py
```

Systemd/Coolify:
- приложение рассчитано на постоянную работу как background worker;
- SQLite должен лежать на persistent volume.

## Структура проекта

```text
ai/
  gemini.py
  history.py
  prompt_composer.py
  prompts/
core/
  config.py
  runtime_models.py
userbot/
  client.py
  swarm_manager.py
  reply_router.py
  orchestrator.py
  exchange_store.py
  scheduler.py
run.py
```

## Тесты

```bash
uv run pytest
```

## Важно

- `SESSION_STRING_*` не публиковать и не логировать.
- Проект использует реальные Telegram-аккаунты.
- Основной режим теперь только `swarm`.
