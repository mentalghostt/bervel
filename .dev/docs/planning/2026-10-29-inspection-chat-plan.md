# Проверка BERVEL — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Добавить изолированные чаты полного диагностического чтения BERVEL с единственным эффектом ticket.create.

**Architecture:** Общие очередь, история и UI чатов; неизменяемый режим в БД; отдельные inspection tools/workflow и внутренний reader с ro-источниками. Секреты скрываются до сохранения и передачи модели.

**Tech Stack:** Существующие Python 3.12/FastAPI/SQLAlchemy/PostgreSQL, React/TypeScript, Docker Engine API; stdlib HTTP Unix socket.

**Spec:** .dev/docs/architecture/28-inspection-chat.md

## Global Constraints

- Рабочий корень C:/OSPanel/home/bervel.local; Personal/core; Git только чтение, worktree/копии не создаются.
- Материалы разработки только .dev; никаких изменений VPN, OSP и внешних MOX auth/config.
- Остальные страницы сохраняют поведение; новые зависимости не добавляются.
- Старые чаты assistant; mode не выбирается моделью или телом запроса.
- Все инструменты ИИ read-only, кроме создания нового внутреннего обращения; служебное хранение чата разрешено.

## Review Focus

- Подмена ID/namespace/replay key не переводит inspection в assistant и не смешивает историю.
- Секреты в ENV, вложенных JSON, логах, URL, частях страниц и восстановленных квитанциях не доходят до модели/тикета.
- Symlink/path traversal и подмена контейнера не открывают чужие источники, special files не блокируют worker.
- Потеря ответа после ticket.create не создаёт второй тикет; сбой reader не превращается в пустой успешный ответ.
- Отсутствие .dev или необязательного источника не ломает общую платформу и не выдаётся за прочитанный источник.

### Task 1: История и серверный режим

**Files:** chat/infrastructure/{tables,repository}.py, chat/api.py, core/migrations/versions/0015_inspection_sessions.py; tests/test_inspection_chat.py.
**Interfaces:** PostgresChatRepository(engine, documents, *, mode='assistant'); chat_router(service, actor, *, prefix='/api/v0.1/chat'); режим задаёт composition.
- [x] RED: создать два режима одним actor/key, проверить отдельные списки/read/send/delete/claim и replay.
- [x] GREEN: additive mode, namespace квитанций и фильтры; старые записи остаются assistant.
- [x] Проверить test_inspection_chat.py и прежние test_chat*.py в отдельной PostgreSQL test DB.

### Task 2: Безопасные источники

**Files:** modules/inspection/{contracts,redaction,service}.py; infrastructure/{files,database,docker,client}.py; infrastructure/server.py; tests/test_inspection_reader.py.
**Interfaces:** InspectionReader.read(operation: str, arguments: dict) -> dict; SecretRedactor.clean(value) -> value; закрытые схемы аргументов в contracts.py.
- [x] RED: чтение/пагинация, маскирование до paging, traversal/symlink/special file, SQL/чужой контейнер/записывающий HTTP запрещены.
- [x] GREEN: ro-files, структурированный SELECT из базовых таблиц, Docker GET allowlist и project labels, явные ошибки/coverage.
- [x] Проверить test_inspection_reader.py, реальные PostgreSQL права отдельной роли и Docker diagnostics без exec.

### Task 3: Исполнитель и подключение

**Files:** inspection/{actions,workflow}.py; chat/service.py; app_setup.py; worker.py; core/config.py; compose.yaml; tests/test_inspection_workflow.py.
**Interfaces:** общий ChatService получает workflow factory; inspection actions принимает только схемы contracts; публичный TicketService — единственный effect.
- [x] RED: запрещённый file.create/settings.update/ticket.edit/exec никогда не вызывается; прочитанный секрет не попадает в model/receipts; ticket.create/replay/unknown защищены.
- [x] GREEN: ограниченный цикл исследования/ответа, цитирование локаторов, редактированные квитанции; независимая авторизация ticket.create; отдельная очередь в прежнем chat worker.
- [x] Проверить inspection workflow и полный Python/Node набор; Compose config проверять без печати секретов.

### Task 4: Интерфейс

**Files:** frontend/src/{App,TestChat,AssistantResults}.tsx, chatApi.ts; .dev/tests/frontend-inspection.cjs.
**Interfaces:** TestChat(active, mode='assistant'); API factory/неизменяемый namespace; storage keys разделены по mode.
- [x] RED: меню, отдельные история/черновики/unknown command, переходы, создание и ссылка тикета; desktop1440/mobile390.
- [x] GREEN: переиспользовать существующий чат, маркировать чтение понятными именами, сохранить ввод и прокрутку.
- [x] Проверить build/lint, все browser tests и изображения desktop/mobile.

### Task 5: Проверка и установка

**Files:** .dev/local/inspection-2026-10-29/, .dev/docs/quality/inspection-chat-2026-10-29.md, operations/inspection-chat.md.
- [x] Проверить весь diff/получить независимое ревью и исправить замечания.
- [x] Снять fingerprints/backup, проверить восстановление и отсутствие активных задач.
- [x] Установить additive migration, reader role и согласованные образы без потери прежних строк/байтов.
- [x] Живые model read/code/DB/container, отказ записи и ticket.create; проверить сохранение прежних данных, службы, UI.
- [x] Записать фактические результаты, ограничения и команды запуска. Коммит/push в core не выполнять.

## Ledger

- Pre-flight: Task1 mode/namespace используется Task3 composition и Task4 API; default assistant сохраняет старый контракт.
- Pre-flight: Task2 contracts/redaction используются Task3 tools; reader всегда возвращает маскированные данные до квитанции.
- Pre-flight: Task3 общий ChatService/UI контракт сохраняется для Task4; собственный workflow отделяет разрешения inspection.
- Ruling: выполняем Native в существующем корне: незакоммиченный проект содержит необходимые изменения многих прежних задач; worktree от HEAD потерял бы их. Git-операции не выполняем.
- Ruling: skill helpers task-start/task-done не запускаются без адаптации: предполагают commit-range и запись вне .dev. Шаги и команды фиксируются здесь/в каталоге доказательств, без Git mutation.

- Ruling: для inspection выделен четвёртый Python worker того же механизма очереди: длительное исследование не блокирует обычные чаты. API/worker используют один согласованный образ.
- Ruling: живой native DENY допустимого тикета воспроизведён на общем classifier без фактических read receipts. Узкий inspection classifier получает server read context, но только текущая прямая просьба разрешает создание;4живых положительных/отрицательных случая прошли.
- Ruling: redaction выполняется до paging/model projection и при всех public replay/recovery outputs; exact [СКРЫТО] разрешён в тикете только без credential suffix.

- Final verification:650Python/79Node/109общих browser +5inspection, native read/refusal/ticket/quotedrefusal и4classifier cases PASS;8308прежнихстрок/11оригиналов сохранены. Собственный QA удалён. Режимы и служебные квитанции сохранены.
