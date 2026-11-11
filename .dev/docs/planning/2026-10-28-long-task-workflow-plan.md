# Long task workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Independent storage/API and UI work may use dispatching-parallel-agents. User authorizes implementation of the presented scheme; no repeated stage approval. All product changes follow proven RED tests.

**Goal:** Длинное исследование завершается проверенным запрошенным файлом или явным partial/blocked.

**Architecture:** Серверная машина состояний в существующем chat module, durable JSONB checkpoint, ограниченный research и отдельные synthesis/generation/verification. Прежние сервисы доступа/авторизации/эффектов сохраняются.

**Tech Stack:** Существующие Python/FastAPI/SQLAlchemy/PostgreSQL, React/TypeScript, Codex SDK. Новых зависимостей нет.

**Spec:** .dev/docs/planning/2026-10-28-long-task-workflow-design.md

## Global Constraints

- C:/OSPanel/home/bervel.local, Personal/core; Git только чтение, чужие изменения сохранять.
- tests first; новый код только после соответствующего RED.
- VPN/OSP/внешняя авторизация неизменны; xAI только голос; все материалы внутри root.
- Не повторять completed/unknown эффекты или failed paid web под новым запросом.
- Исторические строки workflow=NULL не переписываются в выдуманный успех/провал.

## Review Focus

- Research исчерпан при успешных действиях: переход к синтезу, файл либо явный blocked.
- Сжатие контекста: сохранённые факты/ссылки не исчезают с ростом истории.
- Потеря подтверждения создания: unknown/blocked, ровно один эффект.
- Reload и polling: видимый серверный этап без повторного POST.
- Обновление: миграция сохраняет прежние строки/оригиналы; пакет установлен согласованно.

### Task 1: Контракт и хранение

Files: modules/chat/models.py, api.py, infrastructure/tables.py, repository.py; migration0014; tests/test_chat_workflow_storage.py.

- [ ] RED: новые запросы содержат workflow; checkpoint не откатывает фазу/версию, не обновляет finished turn; HTTP отдаёт фазу; partial/blocked терминальны; legacy rows не изменены; fresh/migrated schema совпадают.
- [ ] После GO реализовать контракт из spec, запустить новые и существующие chat/storage/migration тесты.

### Task 2: Планировщик, доказательства, deadlines

Files: modules/chat/workflow.py, service.py, planner.py, context.py; core/model_gateway.py, infrastructure/codex.py; codex-runner/server.mjs; tests/test_chat_long_tasks.py, workflow unit tests and runner deadline tests.

- [ ] RED на настоящих PostgreSQL/actions/renderer/HTTP: многократные поиски упираются в research budget, но PDF физически сохраняется/скачивается/проверяется; переходы видны в другом соединении.
- [ ] RED для устойчивой записной книжки, достаточных/нулевых данных, failed web, unknown create, format mismatch, законченного файла при падении модели, ограничений по фазе.
- [ ] Реализовать чистую машину состояний, notebook и минимальные additions typed плану. Передавать remaining timeout в runner с общим прежним верхним пределом180сек.
- [ ] Пройти новые тесты, обновить только намеренно изменённые ожидания старого state/outcome, выполнить полный регресс.

### Task 3: Видимые этапы

Files: frontend/src/chatApi.ts, TestChat.tsx, AssistantResults.tsx; tests/frontend-chat-workflow.cjs.

- [ ] RED: research→generating→verifying отображается из API; partial/blocked не путаются с успехом; reload только GET; legacy сохранён; длинные источники компактны и доступны.
- [ ] После GO реализовать интерфейс и валидацию контракта, browser tests, build/lint.

### Task 4: Проверка и установка

- [ ] Независимое review и соответствующие проверки; package hashes/dependencies.
- [ ] Idle/backup текущего приложения; отдельное восстановление0014; сверка прежних данных.
- [ ] Изолированный реальный исходный сценарий со штатным Codex, фазами и скачанным PDF; никаких прежних эффектов не переигрывать.
- [ ] Согласованно установить schema/API/workers/frontend, сохранив Codex текущего соседнего чата; итоговый контроль и проверка сохранности, убрать только собственный QA.
- [ ] Итоговый отчёт с фактическими результатами/ограничениями. Git операции переданы человеку по правилам.
