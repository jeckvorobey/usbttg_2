# План архитектуры: Multi-agent Userbot Swarm (6 ботов в одной группе)

> Документ собран в роли Senior Application Architect.
> Опирается на существующий код `tg_userbot` (Telethon + Gemini + aiosqlite + APScheduler).

---

## 1. Idea Analysis

### Постановка
В одной Telegram-группе присутствуют **6 userbot-аккаунтов**. Требуется механизм:
- случайный бот **A** начинает разговор;
- случайный бот **B** (≠ A) отвечает;
- разговор может длиться несколько реплик;
- у каждого бота — **свой промт** (подставляется по `bot_id`);
- есть управляющий процесс, запускающий всё;
- **все 6 ботов постоянно онлайн**, потому что любой человек в группе может обратиться к любому из них через `reply`.

### Тип системы
Multi-agent event-driven userbot swarm в рамках единого процесса на одном сервере.

### Требования

| Тип | Требование | Приоритет |
|-----|------------|-----------|
| Функц. | 6 Telethon-клиентов в одной группе | P0 |
| Функц. | Orchestrator выбирает случайную пару (initiator, responder) | P0 |
| Функц. | Индивидуальные промты на каждого бота | P0 |
| Функц. | Каждый бот отвечает людям через reply (только на reply к самому себе) | P0 |
| Функц. | Переиспользование текущих модулей `ai/`, `userbot/`, `core/` | P0 |
| Функц. | Общая история диалога (контекст виден всем) | P1 |
| Нефункц. | Без рисков flood-wait / бана | P0 |
| Нефункц. | Горячая перезагрузка промтов | P2 |

---

## 2. Decomposition

Новые и изменяемые компоненты поверх существующей flat-структуры:

| Компонент | Ответственность | State |
|-----------|-----------------|-------|
| **`userbot/swarm.py`** (новый) | Пул из N `TelegramClient`, запуск параллельно в одном event loop, supervisor при крэшах | stateful |
| **`userbot/orchestrator.py`** (новый) | Выбор случайной пары, планирование реплик, таймеры между сообщениями, `max_turns` | stateful |
| **`userbot/handlers.py`** (изменить) | Обобщить `reply_guard` с параметром `self_user_id`, подключать ко всем 6 клиентам | stateless |
| **`ai/gemini.py`** (изменить) | `generate(bot_id, context) -> str` — промт грузится по `bot_id` | stateless |
| **`ai/history.py`** (изменить) | Поле `bot_id` в таблице истории | stateful (SQLite) |
| **`core/config.py`** (изменить) | `BOT_PROFILES: list[BotProfile]` (session, name, prompt_file, temperature) | stateless |
| **`ai/prompts/bots/{bot_id}.md`** (новый) | Персональные промты 6 ботов | статичные файлы |

---

## 3. Architectural Options

### Option 1 — Single-process swarm + central orchestrator ⭐ РЕКОМЕНДОВАНО
Один Python-процесс держит 6 `TelegramClient`. Orchestrator — `asyncio.Task`, решающий «кто говорит». Общая SQLite-БД.

- **Pros:** минимум инфраструктуры; переиспользует текущий стек 1-в-1; синхронизация через `asyncio.Lock`; одна точка логов.
- **Cons:** single point of failure; крэш одного клиента может зацепить остальных (митигируется try/except + supervisor-таск).
- **Trade-off:** для 6 ботов идеально, для 60+ — нет.
- **Оценка:** scalability 6/10, cost 10/10, complexity 9/10, TTM 10/10.

### Option 2 — Multi-process, broker-based (Redis pub/sub)
Каждый userbot — отдельный процесс, координатор через Redis.

- **Pros:** изоляция крэшей; горизонтальное масштабирование.
- **Cons:** нарушает ограничение «только SQLite» из CLAUDE.md; сложнее деплой; дороже DevOps.
- **Trade-off:** оправдано при >15 ботах или uptime 99.9%.
- **Оценка:** scalability 10/10, cost 5/10, complexity 4/10, TTM 5/10.

### Option 3 — Лидер + ведомые (оркестрация через служебный TG-канал)
Первый бот — leader, шлёт команды остальным через закрытый канал.

- **Pros:** всё в стеке Telegram.
- **Cons:** Telegram — плохой transport для координации (latency, flood-wait); отладка — кошмар.
- **Оценка:** scalability 3/10, cost 9/10, complexity 5/10, TTM 7/10.

**Выбор:** **Option 1**.

---

## 4. Technology Stack (дельта к текущему)

- **Без изменений:** Python 3.11+, Telethon, Gemini (google-generativeai), aiosqlite, APScheduler, pydantic-settings, pytest/pytest-asyncio.
- **Новое (встроенное):** `asyncio.TaskGroup` (Py 3.11+) для управления N клиентами; `random.Random(seed)` для воспроизводимости в тестах.
- **Ничего нового в инфраструктуру** — сохраняется ограничение «только SQLite».

---

## 5. Data Flow & Interactions

```
┌────────────────── один сервер, один python-процесс ──────────────────┐
│                                                                       │
│  ┌─────────────── SWARM (6 TelegramClient) ──────────────────┐        │
│  │                                                            │        │
│  │  bot_1  bot_2  bot_3  bot_4  bot_5  bot_6                  │        │
│  │    │      │      │      │      │      │                   │        │
│  │    └──────┴──────┼──────┴──────┴──────┘                   │        │
│  │                  │                                          │        │
│  │     У каждого: @client.on(NewMessage) handler              │        │
│  │                  │                                          │        │
│  │                  ▼                                          │        │
│  │     ┌────────── REPLY_GUARD ─────────┐                     │        │
│  │     │ event.is_reply?                │                     │        │
│  │     │ reply_to.sender_id == self?    │ ◄── reply адресован ИМЕННО     │
│  │     │ sender not in OUR_BOTS?        │     этому боту?                │
│  │     └────────────────────────────────┘                     │        │
│  │                  │ yes                                      │        │
│  │                  ▼                                          │        │
│  │           gemini.generate(bot_id=self)                      │        │
│  │                  │                                          │        │
│  │                  ▼                                          │        │
│  │           client.send_message(reply_to=event.id)            │        │
│  └────────────────────────────────────────────────────────────┘        │
│                                                                       │
│  ┌────────────── ORCHESTRATOR (asyncio.Task) ─────────────────┐        │
│  │  каждые N минут (или при тишине >T минут):                  │        │
│  │    A, B = random_pair(BOTS)                                 │        │
│  │    clients[A].send_message(group, topic_opener)             │        │
│  │    sleep(20..60)                                            │        │
│  │    clients[B].send_message(group, reply_to=prev_msg_id)     │        │
│  │    # до max_turns ходов                                      │        │
│  └─────────────────────────────────────────────────────────────┘        │
│                                                                       │
│  ┌────────────── SQLite (общая история) ──────────────────────┐        │
│  │  messages(id, group_id, bot_id, sender_id, role, text, ts) │        │
│  └─────────────────────────────────────────────────────────────┘        │
└───────────────────────────────────────────────────────────────────────┘
```

### Два канала входа у каждого бота
1. **Passive listener** — `@client.on(events.NewMessage)` ловит reply от людей → отвечает.
2. **Active executor** — Orchestrator дёргает `clients[A].send_message(...)` для автономного диалога.

### Разграничение каналов
- Приоритет у **handler'а**: если человек сделал reply, Orchestrator пропускает ход этого бота.
- `asyncio.Lock` per-bot (один замок на `bot_id`) предотвращает одновременные send от одного аккаунта.

### Ключевые фильтры в handler'е

```python
@client.on(events.NewMessage(chats=config.TARGET_GROUP))
async def on_reply(event):
    # 1. Если отправитель — один из наших 6 ботов: игнор (нет эха)
    if event.sender_id in SWARM_USER_IDS:
        return
    # 2. Если это не reply: игнор (инициатива — только у Orchestrator)
    if not event.is_reply:
        return
    # 3. Если reply указывает НЕ на сообщение этого бота: игнор
    replied = await event.get_reply_message()
    if replied.sender_id != self_user_id:
        return
    # 4. Только теперь — обращение к ЭТОМУ конкретному боту
    history = await history_repo.load(event.chat_id, limit=20)
    prompt  = prompts.load_for(bot_id=self.bot_id)
    answer  = await gemini.generate(prompt, history, event.message.text)
    await event.reply(answer)
    await history_repo.save(bot_id=self.bot_id, text=answer, ...)
```

---

## 6. Risks

| Риск | Почему | Митигация |
|------|--------|-----------|
| **Flood-wait от Telegram** | 6 аккаунтов шлют в одну группу → подозрительно | Задержки 10–60с между репликами, `client.action()` «печатает…», лимит X сообщений/час |
| **Шаблонность ответов** | Gemini на похожих промтах даёт похожий стиль | Разные `temperature` per bot, разные «характеры» в промтах, примеры речи |
| **Эхо-петля бот↔бот** | A отвечает B, тот снова A, до бесконечности | Orchestrator контролирует `max_turns`; handler игнорит отправителей из `SWARM_USER_IDS` |
| **Хор на одного человека** | 6 ботов одновременно отвечают на одно сообщение | Проверка `replied.sender_id == self_user_id` |
| **Крэш одного клиента** | Сетевая ошибка / revoke session | Supervisor-таск перезапускает только упавшего клиента |
| **Гонка handler vs orchestrator** | Оба шлют через один `bot_id` одновременно | `asyncio.Lock` per-bot |
| **Утечка `SESSION_STRING`** | 6 секретов в `.env` | `.env` в `.gitignore`, маскировать в логах, secret-менеджер на проде |
| **Ban аккаунта** | Telegram ML детектит bot-like поведение | «Человечный» ритм, случайные паузы, иногда тишина, не мгновенные ответы |

---

## 7. Implementation Plan (для Codex CLI)

### MVP (фаза 1)

1. **`core/config.py`:** добавить `BotProfile` и `BOT_PROFILES: list[BotProfile]` с полями `name`, `session_string`, `prompt_path`, `temperature`.
2. **`userbot/swarm.py`:** `class Swarm` с `start_all()` / `stop_all()`; держит `dict[bot_id, TelegramClient]`.
3. **`ai/history.py`:** поле `bot_id` в таблице истории + миграция схемы.
4. **`ai/gemini.py`:** `generate(bot_id, context)` — промт грузится по `bot_id` из `ai/prompts/bots/{bot_id}.md`.
5. **`userbot/handlers.py`:** обобщить `reply_guard` (параметр `self_user_id`), подключить ко всем 6 клиентам.
6. **`userbot/orchestrator.py`:** цикл `pick_pair → generate → send → delay` с `max_turns`.
7. **`ai/prompts/bots/bot_1.md … bot_6.md`:** заглушки с разными «характерами».
8. **`main.py`:** `asyncio.gather(swarm.run(), orchestrator.run())`.

### TDD-план (порядок тестов)

| # | Тест | Модуль |
|---|------|--------|
| 1 | `test_bot_profiles_loaded` — 6 профилей валидны | `core/config.py` |
| 2 | `test_pick_pair_never_same` — A ≠ B всегда | `userbot/orchestrator.py` |
| 3 | `test_prompt_loaded_by_bot_id` — правильный промт по bot_id | `ai/gemini.py` |
| 4 | `test_history_saves_bot_id` — bot_id сохраняется в SQLite | `ai/history.py` |
| 5 | `test_reply_guard_self_only` — бот отвечает только на reply к самому себе | `userbot/handlers.py` |
| 6 | `test_reply_guard_ignores_swarm_senders` — игнор reply от других ботов | `userbot/handlers.py` |
| 7 | `test_orchestrator_respects_max_turns` — цепочка не бесконечная | `userbot/orchestrator.py` |
| 8 | `test_swarm_starts_all_clients` — 6 клиентов подключены | `userbot/swarm.py` |
| 9 | `test_per_bot_lock_prevents_concurrent_send` — гонка заблокирована | `userbot/swarm.py` |

### Что можно отдать Codex CLI безопасно
- `core/config.py` расширение (чистый pydantic)
- `userbot/swarm.py` (boilerplate вокруг Telethon)
- миграция `ai/history.py`
- unit-тесты 1–9
- заглушки персональных промтов

### Что требует ручной валидации
- Telethon multi-client event loop (реальные задержки, flood-wait на живом аккаунте)
- Антиспам-эвристики (подбор задержек, распределение сообщений во времени)
- Тюнинг промтов «характеров»

### Iteration (фаза 2)
- Orchestrator триггерит диалог только при тишине в группе >T минут (адаптивный режим).
- Боты иногда «молчат», иногда «уходят из разговора».
- Реакции (не только текст), цитирование.

### Scaling (фаза 3, по необходимости)
- Миграция на Option 2 (Redis broker), если N ботов вырастет >15 или нужна гео-распределённость / HA.

### Предлагаемая структура файлов (дельта)

```
ai/prompts/bots/
  bot_1.md … bot_6.md
userbot/swarm.py
userbot/orchestrator.py
tests/test_swarm.py
tests/test_orchestrator.py
tests/test_history_bot_id.py
tests/test_reply_guard_multi.py
```

---

## 8. Facts / Assumptions / Unknowns

### Facts (подтверждено)
- Текущий стек: Telethon, Gemini, aiosqlite, APScheduler, pydantic-settings.
- Есть `userbot/scheduler.py` с 30-минутными сессиями.
- В коммите `d418b86` уже добавлен контекст `reply_guard` — готовая основа.
- Промты загружаются из `ai/prompts/*.md` в runtime.
- Ограничения из CLAUDE.md: только SQLite, только async, без FastAPI.
- **Один сервер, один процесс, все 6 ботов онлайн постоянно** (подтверждено пользователем).
- **Люди могут обращаться к любому боту через reply** (подтверждено пользователем).

### Assumptions (помечено явно)
- **A1:** 6 userbot'ов = 6 разных Telegram-аккаунтов = 6 разных `SESSION_STRING`.
- **A2:** Все 6 ботов уже вступили в целевую группу вручную.
- **A3:** Orchestrator — asyncio-таск внутри того же процесса, что и Swarm.
- **A4:** Если человек пишет в группу **без reply** — никто не отвечает.
- **A5:** Если человек сделал reply конкретному боту — отвечает **только этот бот** (хор запрещён).
- **A6:** Handler имеет приоритет над Orchestrator (при конфликте Orchestrator пропускает ход).
- **A7:** Реальные пользователи из `whitelist.md` сохраняются (текущая фича не ломается).

### Unknowns (требуют решения)
1. Должен ли бот отвечать, если его **упомянули через @username** (не reply)?
2. **Частота инициирования** автономных диалогов: фиксированный интервал vs «при тишине >T минут»?
3. Сколько реплик длится один разговор бот-бот? (2 / 5–10 / случайно?)
4. Возможны ли **параллельные** диалоги разных пар, или строго один разговор за раз?
5. Интеграция со `scheduler.py`: заменяем текущую 30-мин логику или расширяем?
6. Формат хранения 6 сессий в `.env`: `SESSION_STRING_1…6` vs JSON-массив?
7. Уровень параноии по flood-wait: агрессивное общение vs максимально «человечное»?

---

## 9. Questions (требуют ответа перед имплементацией)

1. Должен ли бот реагировать на **@username-упоминание** (не только reply)?
2. **Триггер автономного диалога:** расписание (каждые N минут) или «при тишине >T минут»?
3. **Длина бот-бот диалога:** фиксированная, случайная в диапазоне, адаптивная?
4. **Параллельные разговоры:** разрешены (две разные пары одновременно) или строго один?
5. **Интеграция с текущим `scheduler.py`:** расширяем или заменяем?
6. **Хранение 6 сессий:** `SESSION_STRING_1..6` или JSON в одном ключе?
7. **Flood-wait политика:** агрессивная или консервативная?
8. **Персональности 6 ботов:** задаёт пользователь вручную, или сгенерировать шаблонно?

---

## Приложение A. Ресурсные оценки

Для 6 Telethon-клиентов на одном сервере:
- **RAM:** ~50–80 MB на клиента × 6 ≈ 300–500 MB.
- **CPU:** I/O-bound, ~0% в idle.
- **Сокеты:** 6 постоянных TCP-соединений к DC Telegram.
- **Минимальная VPS:** 1 vCPU, 1 GB RAM — с запасом.

Bottleneck — **не ресурсы**, а **rate-limit Telegram** (flood-wait). Решается задержками в Orchestrator.

---

## Приложение B. Почему не «запускать клиентов on-demand»

Альтернатива: поднимать только того бота, кто сейчас говорит, `disconnect()` после. **Отклонено**, потому что:
- Частые логины повышают риск бана.
- Задержка на подключение (2–5с) на каждый ход.
- Теряются апдейты группы между подключениями → бот не увидит reply от человека.

Стандартный паттерн — **все 6 клиентов всегда подключены**, поведением управляет Orchestrator.
