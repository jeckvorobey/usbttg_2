# tg_userbot

`tg_userbot` это Telegram userbot-проект, в котором несколько аккаунтов работают как один `swarm`.

Проще говоря:
- несколько Telegram-аккаунтов запускаются в одном процессе;
- каждый аккаунт сидит в одной целевой группе и остаётся онлайн;
- если человек отвечает `reply` на сообщение конкретного аккаунта, отвечает только этот аккаунт;
- по расписанию приложение само запускает небольшие диалоги между ботами;
- история сообщений и история таких обменов сохраняется в SQLite;
- проект старается не повторять одни и те же вопросы слишком часто;
- ключевые действия подробно пишутся в лог.

## Что приложение делает сейчас

В проекте есть два основных сценария работы.

### 1. Ответ на адресный `reply`

Если человек в группе отвечает на сообщение бота через Telegram `reply`, приложение:
- понимает, какому именно аккаунту адресован ответ;
- игнорирует остальные аккаунты swarm;
- собирает историю диалога из SQLite;
- подставляет persona нужного бота и базовые промты;
- запрашивает текст ответа у Gemini;
- отправляет ответ в группу;
- сохраняет и сообщение пользователя, и ответ бота в БД.

### 2. Плановый обмен между ботами

По расписанию orchestrator:
- проверяет, что текущее время входит в разрешённое UTC-окно;
- может пропустить запуск, если недавно была активность от человека;
- выбирает пару `бот A -> бот B`;
- выбирает тему;
- сверяется с persisted history, чтобы не повторять недавние темы и вопросы;
- генерирует стартовое сообщение от `A`;
- затем генерирует ответ от `B`;
- сохраняет весь exchange в SQLite.

## Что нужно для запуска

Для работы нужны:
- Python `3.11+`;
- `uv` для установки зависимостей и запуска команд;
- `API_ID` и `API_HASH` Telegram;
- `GEMINI_API_KEY`;
- `SESSION_STRING_*` для каждого Telegram-аккаунта из swarm-конфига;
- файл настроек TOML;
- persona-файлы для ботов.

## Быстрый запуск

### 1. Установить зависимости

```bash
uv sync
```

### 2. Создать `.env`

Скопируй шаблон:

```bash
cp .env.example .env
```

Минимально в `.env` должны быть:

```dotenv
API_ID=12345678
API_HASH=your_telegram_api_hash
GEMINI_API_KEY=your_gemini_api_key
SETTINGS_PATH=config/settings.toml
SESSION_STRING_ANNA=...
SESSION_STRING_MIKE=...
```

Важно:
- имя переменной `SESSION_STRING_*` должно совпадать с `session_env` у бота в TOML;
- `SESSION_STRING_*` нельзя коммитить и нельзя логировать.

### 3. Создать файл настроек

Скопируй пример:

```bash
cp config/settings.example.toml config/settings.toml
```

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

[gemini]
model = "gemini-2.5-flash"
fallback_model = "gemini-2.5-flash-lite"
temperature = 0.9

[logging]
level = "INFO"

[swarm]
enabled = true
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

На что обратить внимание:
- `group_chat_id` и `group_target` должны указывать на одну и ту же группу;
- `db_path` это путь к SQLite-файлу;
- `bot_profiles_dir` это папка с persona-файлами;
- каждый `persona_file` должен реально существовать;
- для каждого `session_env` должна быть переменная в `.env`;
- реальные файлы `ai/prompts/**/*.md` локальные для инстанса и не коммитятся;
- в git лежат только шаблоны `ai/prompts/**/*.example.md`.

### 4. Подготовить промты

Runtime читает реальные файлы без `.example`:
- `ai/prompts/system.md`
- `ai/prompts/reply.md`
- `ai/prompts/start_topic.md`
- `ai/prompts/topics.md`
- `ai/prompts/reply_rules.md`
- `ai/prompts/wind_down_hint.md`

В репозитории лежат только примеры с суффиксом `.example.md`. Для нового инстанса скопируй нужные шаблоны в такие же имена без `.example` и заполни их под конкретную группу.

### 5. Подготовить persona-файлы

Проект ожидает persona-файлы в директории, указанной в `bot_profiles_dir`.

Если у тебя в конфиге:

```toml
[prompts]
bot_profiles_dir = "ai/prompts/bots"
```

то должны существовать файлы вроде:
- `ai/prompts/bots/anna.md`
- `ai/prompts/bots/mike.md`

Шаблон persona-файла находится в `ai/prompts/bots/persona.example.md`. Реальные persona-файлы тоже локальные и не коммитятся.

### 6. Получить `SESSION_STRING` для каждого аккаунта

Для каждого Telegram-аккаунта нужно один раз получить строку сессии.

Пример команды:

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

Дальше:
- входишь в нужный Telegram-аккаунт;
- копируешь выведенную строку;
- сохраняешь её в `.env` как `SESSION_STRING_<ИМЯ>`.

## Как запустить приложение

Основной запуск:

```bash
uv run python run.py
```

Что произойдёт после запуска:
- загрузится `.env` и TOML-конфиг;
- инициализируется SQLite;
- загрузятся темы и промты;
- поднимутся все включённые swarm-аккаунты;
- зарегистрируются обработчики входящих `reply`;
- запустится scheduler для плановых обменов;
- в логах появится информация о целевой группе и активных ботах.

Если всё настроено правильно, приложение будет работать как постоянный фоновый worker.

## Как понять, что запуск прошёл нормально

Нормальные признаки:
- нет `Ошибка конфигурации` при старте;
- в логах видно, что таблицы SQLite созданы или готовы;
- в логах видно запуск каждого `bot_id`;
- в логах видно, что целевая группа успешно определена;
- процесс не завершается сразу после старта.

Если приложение не стартует, сначала проверь:
- заполнен ли `.env`;
- существует ли `config/settings.toml`;
- совпадают ли `session_env` и реальные имена переменных в `.env`;
- существуют ли persona-файлы;
- корректны ли `group_chat_id` и `group_target`.

## Как протестировать проект

### Полный прогон тестов

```bash
uv run pytest
```

Эта команда проверяет:
- конфигурацию;
- prompt composer;
- историю и SQLite-слой;
- exchange store;
- reply router;
- orchestrator;
- scheduler;
- runtime и bootstrap;
- вспомогательные скрипты.

### Быстрая проверка, что тесты вообще собираются

```bash
uv run pytest --collect-only
```

Это удобно, если хочешь быстро убедиться, что нет проблем с импортами и структурой тестов.

### Запуск одного тестового файла

Например:

```bash
uv run pytest tests/test_orchestrator.py
```

или:

```bash
uv run pytest tests/test_reply_router.py
```

## Как проверить руками после запуска

Самая простая ручная проверка такая:

1. Запусти приложение.
2. Убедись, что все нужные аккаунты вошли в группу.
3. Напиши сообщение одному из ботов или дождись его сообщения.
4. Ответь на это сообщение через `reply`.
5. Проверь, что отвечает именно тот аккаунт, которому был адресован `reply`.
6. Проверь, что в логах есть запись о маршрутизации и отправке ответа.
7. Проверь, что SQLite-файл появился и обновляется.

Для проверки планового режима:

1. Временно задай ближайшее `active_windows_utc`.
2. Запусти приложение.
3. Подожди несколько scheduler tick.
4. Проверь, что orchestrator выбрал пару ботов и тему.
5. Проверь, что в группе появился обмен `A -> B`.
6. Проверь, что exchange записался в SQLite.

## Вспомогательные скрипты

В проекте есть дополнительные утилиты.

### Посмотреть информацию о текущем Telegram-пользователе

```bash
uv run python scripts/get_info.py
```

Скрипт:
- логинится текущим аккаунтом;
- собирает публичные атрибуты пользователя;
- сохраняет отчёт в `tg_user_info/info.txt`.

### Обновить профиль аккаунта

```bash
uv run python scripts/update_profile.py
```

Скрипт в интерактивном режиме может:
- обновить имя;
- обновить фамилию;
- обновить username;
- обновить аватар.

## Важные ограничения

- проект рассчитан на `swarm`-режим;
- база данных только SQLite;
- сетевые и БД-операции сделаны асинхронно;
- persona каждого бота должна загружаться из `persona_file`;
- все сообщения должны сохраняться в БД;
- секреты из `.env` нельзя публиковать.

## Коротко: минимальный путь до первого запуска

Если совсем кратко, то порядок такой:

1. Установить зависимости: `uv sync`.
2. Скопировать `.env.example` в `.env`.
3. Скопировать `config/settings.example.toml` в `config/settings.toml`.
4. Заполнить `API_ID`, `API_HASH`, `GEMINI_API_KEY`.
5. Получить `SESSION_STRING_*` для каждого аккаунта и добавить их в `.env`.
6. Проверить, что persona-файлы существуют.
7. Запустить тесты: `uv run pytest`.
8. Запустить приложение: `uv run python run.py`.
