# Реструктуризация в `swarm`-архитектуру для постоянного пула userbot

## Кратко

Целевая модель: в одном процессе постоянно запущен `swarm` из `N` userbot-аккаунтов (`N >= 6`).
Каждый bot всегда онлайн и слушает группу. Если пользователь делает `reply` на сообщение конкретного bot, отвечает только этот bot.
По расписанию orchestrator выбирает случайную пару `A -> B`, где `A != B`: `A` задаёт вопрос из списка тем, `B` отвечает по своим настройкам и persona.
`whitelist_user_ids` в новой архитектуре удаляется.
Все сообщения должны сохраняться в БД, чтобы orchestrator не поднимал один и тот же вопрос каждый день и поведение bot оставалось естественным, а не циклично-шаблонным.
Во всех ключевых потоках должно быть подробное логирование, чтобы по логам было понятно, что делает приложение, почему bot ответил или не ответил, какую пару выбрал orchestrator и что записалось в БД.

Рекомендуемый подход: **single-process swarm manager + central orchestrator + shared SQLite**, с расчётом на текущую потребность 6..20 bot без новой инфраструктуры.

---

## 1. Целевая архитектура

### 1.1 Основная схема

```text
┌────────────────────────────── app process ──────────────────────────────┐
│                                                                         │
│  ┌──────────────────── SwarmManager ────────────────────┐               │
│  │  bot_1   bot_2   bot_3   ...   bot_N                │               │
│  │   │       │       │               │                 │               │
│  │   └───────┴───────┴───────────────┴──► Telethon      │               │
│  └──────────────────────────────────────────────────────┘               │
│                     │                                                   │
│                     ├──► AddressedReplyRouter                           │
│                     │      - reply только к самому себе                 │
│                     │      - ignore messages from swarm bots            │
│                     │      - per-bot prompt/profile                     │
│                     │                                                   │
│                     └──► SwarmOrchestrator                              │
│                            - расписание окон                            │
│                            - random pair A/B                            │
│                            - random delays                              │
│                            - anti-self / anti-repeat                    │
│                                                                         │
│  ┌──────────────────────── Shared SQLite ────────────────────────────┐   │
│  │ messages / bot_registry / scheduled_exchanges / orchestrator_state│   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌──────────────────────── Prompt Composer ──────────────────────────┐   │
│  │ base prompts + per-bot persona/profile overlay                   │   │
│  └───────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Каналы поведения

1. **Addressed reply flow**
   - любой bot постоянно слушает группу;
   - если пользователь сделал `reply` на сообщение bot_X, отвечает только `bot_X`;
   - остальные bot игнорируют событие.

2. **Scheduled swarm flow**
   - orchestrator по расписанию выбирает случайную пару;
   - `bot_A` публикует вопрос;
   - `bot_B` отвечает с задержкой и своим persona;
   - выбор пары делается заново на каждом слоте.

---

## 2. Почему именно так

### Вариант A: один process, много Telethon clients
**Выбор по умолчанию.**

Плюсы:
- максимально использует текущую кодовую базу;
- один deploy process;
- проще логирование, планировщик и shared state;
- без Redis, без брокеров, без новых infra-компонентов.

Минусы:
- один process остаётся точкой отказа;
- нужен аккуратный supervision внутри процесса.

Почему подходит:
- для 6 bot и даже для 10-20 bot это разумный баланс;
- соответствует ограничению проекта: `async`, SQLite, без лишней инфраструктуры;
- соответствует пользовательской цели: “все боты постоянно запущены”.

### Вариант B: supervisor + subprocess per bot
Оставить как future path, если later станет много bot или появятся нестабильные сессии.
Сейчас не нужен: усложняет систему раньше времени.

---

## 3. Что переиспользуем из текущей разработки

### Оставляем и переиспользуем
- `UserBotClient` как обёртку над Telethon.
- `GeminiClient`.
- `PromptLoader` как базу для prompt composition.
- `TopicSelector`.
- `WindowSchedule` и общую scheduler-логику времени.
- `MessageHistory` и SQLite как единый state store.
- `_resolve_group_target` и текущую Telethon-инициализацию.

### Что перестраиваем
- `run.py` сейчас монолитный bootstrap одного инстанса. Его нужно разрезать на reusable runtime-компоненты.
- `windowed_qa` как модель фиксированных ролей `initiator/responder` больше не подходит.
- `whitelist_user_ids` убрать полностью из нового режима.
- `reply_guard` не выбрасывать сразу, а использовать как основу для адресного reply-routing; затем упростить и переименовать.

---

## 4. Новая модель поведения

### 4.1 Правило ответа пользователю
Жёсткое правило:

```text
Если message.sender_id принадлежит одному из swarm bot -> ignore
Если сообщение не является reply -> ignore
Если reply адресован не этому bot -> ignore
Иначе отвечает только этот bot
```

### 4.2 Правило автодиалога
На каждом активном слоте:
- выбирается `initiator_bot_id`;
- выбирается `responder_bot_id`, обязательно `!= initiator_bot_id`;
- при наличии альтернатив не повторяется та же пара, что в прошлом слоте;
- orchestrator ставит jitter/delay между сообщениями;
- один и тот же bot не должен параллельно:
  - отвечать человеку,
  - и в тот же момент публиковать scheduled message.

Для этого: **per-bot async lock**.

### 4.3 Правило естественного поведения
- каждый bot должен вести себя как живой участник чата, а не как cron-скрипт;
- orchestrator перед новым scheduled question смотрит историю предыдущих exchange и recent conversational context;
- нельзя повторять один и тот же вопрос или почти тот же вопрос изо дня в день, если в истории уже есть близкий недавний аналог;
- все сообщения, включая scheduled initiator messages, scheduled responder messages, human prompts и ответы bot людям, должны сохраняться в БД как единый conversational log;
- prompt generation должна учитывать recent history, чтобы формулировки, заходы в разговор и общий стиль выглядели вариативно и по-человечески.

### 4.4 Правило наблюдаемости
- логирование должно быть во всех ключевых слоях: bootstrap, config loading, client lifecycle, routing, orchestrator, SQLite, prompt composition, Gemini generation;
- по логам должно быть видно:
  - какой bot запущен или переподключается;
  - почему входящее сообщение было обработано или проигнорировано;
  - какую пару выбрал orchestrator и почему exchange был пропущен;
  - какая тема была выбрана или отклонена anti-repeat логикой;
  - какие сообщения и exchange были сохранены в БД;
- логирование должно помогать разбирать поведение приложения в runtime, а не быть формальным “started/stopped” шумом.

---

## 5. Как упростить настройки

### 5.1 Что убрать
Удалить из новой конфигурации:
- `telegram.whitelist_user_ids`
- `bot.role`
- `mode.active = "windowed_qa"`
- дублирующие single-bot настройки, связанные с парой initiator/responder

### 5.2 Новая конфигурация
Сделать **один основной `settings.toml`** + секреты в `.env`.

#### Предлагаемая структура `settings.toml`

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

#### Что остаётся в `.env`
```dotenv
API_ID=...
API_HASH=...
GEMINI_API_KEY=...
SESSION_STRING_ANNA=...
SESSION_STRING_MIKE=...
...
```

### 5.3 Почему это проще
- один режим вместо набора legacy/windowed_qa/single-role;
- список bot описан явно и масштабируется до `N`;
- секреты не попадают в TOML;
- per-bot persona и temperature видны сразу;
- нет whitelist, потому что routing строится по `reply-to-self`, а не по списку user_id.

---

## 6. Предлагаемая структура кода после реструктуризации

```text
core/
  config.py                # новая swarm-конфигурация
  runtime_models.py        # BotProfile, SwarmSettings, ExchangePlan

ai/
  gemini.py                # per-bot generation options
  history.py               # bot_id + exchange_id
  prompt_composer.py       # base + persona overlay

userbot/
  client.py                # оставить
  swarm_manager.py         # запуск и supervision N clients
  reply_router.py          # addressed-reply logic
  orchestrator.py          # random pair scheduling
  scheduler.py             # time windows / silence / random offsets
  exchange_store.py        # SQLite state for planned/completed exchanges

run.py                     # thin bootstrap: load config -> start swarm
```

### Что убрать или вывести из hot path позже
- `windowed_qa.py` после миграции можно удалить.
- `reply_guard/*` либо:
  - временно адаптировать,
  - затем свернуть в новый `reply_router.py`.

---

## 7. Пошаговый план реструктуризации

## Шаг 1. Разрезать монолитный bootstrap
Цель: перестать собирать всё внутри `run.py`.

Сделать:
- выделить `RuntimeContext`;
- вынести создание `GeminiClient`, `MessageHistory`, `PromptLoader`, `TopicSelector`;
- сделать bootstrap, пригодный и для single client, и для swarm.

Результат:
- код запуска перестаёт быть одноразовым и становится composable.

## Шаг 2. Ввести новую swarm-конфигурацию
Цель: перейти от single-bot model к `N` bots.

Сделать:
- добавить `mode = "swarm"`;
- добавить `swarm.bots[]`;
- убрать `whitelist_user_ids` из активной конфигурации swarm;
- оставить старые поля временно только для migration compatibility.

Результат:
- приложение умеет описывать 6+ bot без role-based костылей.

## Шаг 3. Собрать `SwarmManager`
Цель: держать все bot постоянно онлайн.

Сделать:
- запускать `N` `UserBotClient` внутри одного event loop;
- на старте получать `me.id` для каждого bot;
- собирать `swarm_user_ids`;
- добавить health-state и supervised reconnect.
- добавить понятные lifecycle-логи по каждому bot.

Результат:
- все bot всегда подключены и готовы к reply.

## Шаг 4. Реализовать `AddressedReplyRouter`
Цель: правильная маршрутизация reply от человека.

Сделать:
- отдельный handler на каждый client;
- проверять:
  - sender не из swarm;
  - сообщение это `reply`;
  - reply адресован именно этому bot;
- генерировать ответ с persona этого bot;
- писать историю с `bot_id`.
- логировать причины ignore/handle для каждого входящего события.

Результат:
- пользователь получает ответ именно от того bot, к которому обратился.

## Шаг 5. Реализовать `SwarmOrchestrator`
Цель: scheduled random dialog.

Сделать:
- использовать окна активности;
- на каждом окне выбирать случайную пару;
- исключать `A == B`;
- учитывать cooldown прошлой пары;
- учитывать историю прошлых вопросов и recent exchanges, чтобы не повторять одну и ту же тему/формулировку слишком часто;
- инициатор публикует вопрос из `topics.md`;
- responder отвечает по своему prompt;
- ограничить `max_turns_per_exchange`, default `2`.
- логировать выбор пары, тему, причины skip и итог exchange.

Результат:
- боты периодически общаются между собой предсказуемо и контролируемо.

## Шаг 6. Расширить SQLite state
Цель: хранить не только текст, но и swarm-контекст.

Добавить:
- `bot_id`
- `exchange_id`
- `message_origin` (`human_reply`, `scheduled_initiator`, `scheduled_responder`)
- `reply_to_message_id`
- `topic_key` / `topic_signature` для anti-repeat дедупликации тем и формулировок
- timestamp-поля, позволяющие понять, когда тема уже использовалась в прошлых днях
- `scheduled_exchanges` table
- `swarm_bots` or in-memory registry with optional persisted snapshot

Результат:
- история и orchestration становятся наблюдаемыми, тестируемыми и пригодными для anti-repeat логики.

## Шаг 6.1. Добавить сквозное логирование
Цель: сделать поведение swarm понятным по runtime-логам.

Сделать:
- логирование решений orchestrator;
- логирование адресной маршрутизации reply;
- логирование сохранения сообщений и exchange в SQLite;
- логирование reconnect/lifecycle каждого bot;
- логирование anti-repeat решений и причин skip.

Результат:
- приложение можно отлаживать и сопровождать по логам без ручного угадывания внутреннего состояния.

## Шаг 7. Ввести `PromptComposer`
Цель: не плодить по 3 отдельные папки промтов на каждого bot.

Сделать композицию:
- base `system.md`
- base `reply.md`
- base `start_topic.md`
- persona overlay from `ai/prompts/bots/<bot_id>.md`

Итоговый prompt:
```text
base system
+ base action prompt
+ bot persona/profile
+ optional exchange context
```

Результат:
- один общий behavioral baseline, но разные характеры bot.

## Шаг 8. Удалить устаревшие сущности
После стабилизации:
- удалить `whitelist_user_ids`;
- удалить `windowed_qa`;
- почистить README и `settings.example.toml`;
- упростить тесты и доки под новый `swarm`-режим.

---

## 8. С чего начать прямо в коде

### Первая итерация
1. Рефактор `run.py` в reusable bootstrap.
2. Новые config-модели для `swarm`.
3. `SwarmManager` с fake clients в тестах.
4. `AddressedReplyRouter` и его тесты.
5. Только потом orchestrator.

### Почему именно так
Если начать с orchestrator, а runtime и routing ещё не стабилизированы, получится много ложной сложности.
Сначала нужен надёжный always-on swarm и правильный self-reply routing. Автодиалог поверх этого добавляется отдельно и проще.

---

## 9. Тестовый план

### Конфиг
- загружается `swarm.bots[]` с `N >= 1`
- приложение падает, если bot `id` дублируется
- приложение падает, если `session_env` не найден в env
- `whitelist_user_ids` не используется в `swarm` mode

### Swarm runtime
- запускаются все `enabled` bots
- при падении одного client остальные продолжают работать
- `swarm_user_ids` собирается корректно
- per-bot lock не допускает concurrent send одним bot
- в логах видны lifecycle-события bot и reconnect-попытки

### Reply routing
- bot отвечает только на `reply` к своему сообщению
- bot игнорирует не-reply сообщения
- bot игнорирует reply к другому bot
- bot игнорирует сообщения от другого swarm bot
- в логах видны причины ignore/handle

### Orchestrator
- `initiator != responder`
- при наличии альтернатив пара не повторяется два слота подряд
- orchestrator уважает `silence_timeout_minutes`
- один exchange не превышает `max_turns_per_exchange`
- orchestrator не повторяет один и тот же вопрос ежедневно, если в БД уже есть recent usage той же темы/формулировки
- человек reply-нул bot во время scheduled slot: bot отвечает человеку без гонки
- в логах видны причины выбора, skip и завершения exchange

### History / SQLite
- сохраняется `bot_id`
- сохраняется `exchange_id`
- сохраняется origin сообщения
- сохраняются все сообщения без пропусков, включая scheduled и human flows
- по БД можно определить, какие вопросы уже звучали недавно
- exchange можно восстановить после рестарта
- операции записи/чтения сопровождаются достаточными логами для диагностики

---

## 10. Важные решения по умолчанию

- Новый основной режим: `swarm`.
- Все bot постоянно онлайн.
- `whitelist_user_ids` убрать.
- Без `reply` от человека bot не отвечает.
- Отвечает только адресованный bot.
- Scheduled exchange по умолчанию: **один вопрос + один ответ**.
- Scheduled exchange не должен повторять одну и ту же тему/формулировку каждый день; по умолчанию нужен anti-repeat по истории.
- Логирование обязательно во всех ключевых потоках и должно объяснять решения приложения.
- Архитектура по умолчанию: **single-process multi-client swarm**.
- Масштабирование beyond low dozens bot: отдельный следующий этап, не часть MVP.

---

## 11. Основные риски и защита

- **Flood-wait / антиспам Telegram**
  - jitter delays
  - ограничение частоты scheduled exchanges
  - skip on recent human activity

- **Гонки orchestrator vs human reply**
  - per-bot lock
  - приоритет human reply над scheduled send

- **Похожие ответы у всех bot**
  - base prompt + persona overlay + per-bot temperature

- **Повторяющиеся вопросы и неестественное поведение**
  - full message persistence в SQLite
  - topic deduplication по recent history
  - prompt composition с учётом прошлых exchange
  - вариативные формулировки и anti-repeat окно

- **Непрозрачное поведение приложения**
  - сквозное структурированное логирование
  - логирование причин skip/route/select/save
  - lifecycle-логи по каждому bot и exchange

- **Рост числа bot**
  - текущий дизайн параметризован по `N`, но без premature subprocess/broker split

---

## 12. Предлагаемый MVP-результат

После первой полной миграции система должна уметь:
- держать 6+ userbot постоянно запущенными;
- правильно маршрутизировать пользовательские `reply`;
- по расписанию запускать случайный `A -> B` обмен;
- использовать разные persona/prompts для каждого bot;
- работать без `whitelist_user_ids`;
- сохранять все сообщения в БД и использовать историю для anti-repeat поведения;
- не задавать изо дня в день один и тот же вопрос;
- давать понятную картину работы через логи на всех ключевых этапах;
- управляться через один понятный `settings.toml` и `.env`.

## Assumptions
- Все Telegram-аккаунты уже вручную добавлены в нужную группу.
- В MVP один process на один host достаточно.
- Старые режимы можно оставить на время миграции, но целевым режимом считается только `swarm`.
