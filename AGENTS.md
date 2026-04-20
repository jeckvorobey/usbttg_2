# tg_userbot — инструкции для Codex

## Назначение проекта

Telegram userbot на базе Telethon, который работает в режиме `swarm`:
- в одном процессе запущено несколько userbot-аккаунтов;
- каждый bot постоянно онлайн и слушает целевую группу;
- если пользователь делает `reply` на сообщение конкретного bot, отвечает только этот bot;
- orchestrator по расписанию запускает случайные `A -> B` обмены;
- все сообщения сохраняются в SQLite;
- persisted history используется для anti-repeat поведения, чтобы боты не задавали один и тот же вопрос каждый день;
- в ключевых потоках есть подробное логирование.

## Технологии

| Компонент | Библиотека | Назначение |
|---|---|---|
| MTProto | `telethon` | Подключение к Telegram как user |
| AI | `google-generativeai` | Генерация ответов через Gemini |
| Scheduler | `apscheduler` | Планирование orchestrator tick |
| Database | `aiosqlite` | История сообщений и persisted state |
| Config | `pydantic-settings` | Секреты из `.env`, несекретные настройки из TOML |
| Testing | `pytest`, `pytest-asyncio` | TDD и async unit/integration tests |
| Python | `3.11+` | Целевая версия |

## Архитектура

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

## Поток данных

```text
Human reply в группе
  → reply_router.py
  → per-bot coordinator
  → prompt_composer.py
  → gemini.py
  → history.py
  → ответ адресованным bot

Scheduled tick
  → orchestrator.py
  → exchange_store.py (persisted anti-repeat state)
  → prompt_composer.py
  → gemini.py
  → history.py
  → A задаёт вопрос, B отвечает
```

## Правила разработки

1. Сначала изучай существующий код и тесты, затем меняй реализацию.
2. Предпочитай TDD: тест до реализации, затем минимальное изменение кода.
3. Не завязывай тесты на внешние сервисы:
   - Gemini API мокировать;
   - SQLite подменять на `":memory:"`;
   - Telethon-клиенты подменять fake/stub-объектами.
4. Для async-логики использовать только асинхронные интерфейсы.
5. Комментарии и docstrings держать на русском языке.
6. Не хардкодить промты в коде.
7. Все новые ключевые ветки поведения должны сопровождаться логированием.

## Ограничения

- `async` везде для БД и сетевых операций;
- только `aiosqlite`, без синхронного SQLite API;
- без FastAPI и HTTP-сервера;
- только SQLite, без PostgreSQL и Redis;
- persona загружать строго из `persona_file` в конфиге;
- все сообщения сохранять в БД;
- `SESSION_STRING_*` не логировать и не коммитить.

## Рабочие команды

- Установка зависимостей: `uv sync`
- Запуск тестов: `uv run pytest`
- Проверка сбора тестов: `uv run pytest --collect-only`
- Локальный запуск: `uv run python run.py`

## Важные пути

| Путь | Описание |
|---|---|
| `config/settings.example.toml` | Пример swarm-конфигурации |
| `ai/prompts/topics.md` | Темы для scheduled exchange |
| `ai/prompts/system.md` | Базовый системный промт |
| `ai/prompts/reply.md` | Базовый промт ответа |
| `ai/prompts/start_topic.md` | Базовый промт старта темы |
| `ai/prompts/bots/` | Persona-файлы ботов |
| `.env.example` | Шаблон секретов |

## Ожидания от Codex

- Перед правками сверяй описание выше с реальной структурой проекта.
- При изменении поведения сначала обновляй или добавляй тесты, затем код.
- Не трогай пользовательские данные и не раскрывай `SESSION_STRING_*`.
- В финальном отчёте указывай, что изменено и чем это проверено.
