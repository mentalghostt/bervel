# BERVEL Workspace Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development or executing-plans. Шаги отмечаются в ledger; Git-операции запрещены правилами пользователя для core.

**Goal:** Полный радар по складу/экономике, действующий универсальный ассистент, голос, тикеты и единый рабочий интерфейс.
**Architecture:** Существующие core-сервисы и модульные границы; typed service operations, SQL/SDK adapters, явная сборка app_setup. Durable queues/results и точные snapshots; общий ArtifactService для всех файлов.
**Tech Stack:** Python3.12/FastAPI/SQLAlchemy/PostgreSQL17/pgvector, React19/TypeScript/Vite/Tailwind, существующий Codex SDK0.163, xAI REST STT, ReportLab и безопасные документы/SVG.
**Spec:** .dev/docs/planning/2026-10-27-workspace-upgrade-design.md

## Global Constraints

- Корень C:\OSPanel\home\bervel.local; Personal; core; файлы редактируются на месте, Git только read-only. Не переносить папки/чаты, не создавать клоны.
- VPN/OSP/MOX auth неизменны; все материалы внутри корня; секреты только server-side.
- Исходники документов не изменять; mock явно отделять от реальных фактов; старые данные/историю/файлы не терять.
- Режим АВТОНОМНО: после интервью все этапы и внутренние переходы разрешены, без промежуточной приёмки. Ключ xAI вводит человек через готовое поле.

## Review Focus

- Дубли/конфликтующие строки/отсутствующие количества и срок поставки дают явные причины, не ложное соответствие.
- Ноль склада/ненулевая нехватка/валюта/округление/равенство порога маржи.
- Повтор команды/остановка worker/длинный radar-run не дублирует эффекты и не теряет результат.
- Документная prompt injection/чужой scope/удалённый источник/секрет в настройках или истории.
- Микрофон denied/неподдерживаемый MIME/переключение страницы, мобильная компоновка и независимый scroll.

## Task 1 — Core file roles and readiness (parent)

Files: core/models.py, ports.py, services.py, infrastructure/repository.py/database.py, api/models.py/app.py; migration0011_artifact_usage; tests/test_artifact_usage.py.
Produces: usage_role general/procurement/stock/economics; list readiness fields; `prepared_metadata(actor,ref)`, `set_usage_role(actor,id,role)`; PUT artifacts/{id}/usage-role.
- [x] RED: роль/готовность, права/область/старые файлы/удаление.
- [x] Реализовать минимальный контракт, старые API совместимы.
- [x] GREEN: новые и прежние оригиналы/индекс/удаление.

## Task 2 — Radar full pipeline (radar implementer)

Files: modules/tender_radar/** and its Python tests only. No core/frontend/app_setup edits. No schema migration needed: extended selection/report JSONB.
Consumes: ArtifactService.prepared_metadata; public artifact/memory/model contracts.
Produces: `RadarService.analyse(actor,procurement_ref,stock_ref,economics_ref,filters,command_key)` and POST radar/analyse; report1.3, экономический CSV-template/adapter, complete filters and coverage metadata. Public integration details written in ledger before frontend integration.
- [x] RED: цена/дни до LLM, focus, все строки/дубли, Decimal склад/маржа, любые нехватки, неизвестные характеристики/даты/валюта, цены из экономики, бюджет/мощности.
- [x] Implement importer/model/economics/preselection/service/report/API/PDF, legacy reports readable.
- [x] GREEN: targeted and full radar tests, no hidden candidate truncation.

## Task 3 — Assistant actions, documents, tickets (assistant implementer)

Files: modules/chat/**, new modules/tickets/**, core/infrastructure/codex.py and model_gateway.py, migration0012_assistant_workflows (down0011_artifact_usage), Python tests. No core file API/frontend/Node/app_setup edits.
Consumes: public ArtifactService/RadarService/model; ready role-based latest selection; existing settings adapters injected. Coordinates `generate(..., output_schema=None, web_search=False)` with runner implementer.
Produces: action dispatcher/receipts, typed message results (radar_run/file/ticket/web), durable result references, documents/SVG, ticket service/router/table. ChatService optional injected actions preserves direct legacy use. Public builder signatures/DTO documented promptly.
- [x] RED: explicit requested actions, actor/scope, no tools authorized by document text, idempotency/restart/errors, latest role-based ready files, long radar result card, ticket content/status, safe documents and citations.
- [x] Implement typed planner/action execution to public services, save authoritative result before claiming success; preserve history/legacy API.
- [x] GREEN: assistant/ticket and existing chat tests, generated files real and valid.

## Task 4 — Runner structured output/web and voice backend (runner implementer)

Files: codex-runner/**, new core/api/voice.py and infrastructure/voice.py, Python/Node tests. No other Python core/app_setup/frontend/migrations edits.
Produces: generate optional output_schema and web_search request fields; verified native web search with citations or explicit error; GET/PUT /v1/voice/settings server-only key, POST /v1/transcribe JSON audio transport; public Python voice router + adapter using settings.codex_url. xAI model fixed grok-voice-transcribe-2.0; MIME validation/short audio<=5MiB, timeouts/errors/redaction. No external MOX config changes.
- [x] RED: schemas/flags not silently ignored; default shell/apps/network stays blocked; web only per requested search; secret write-only and retained across restart; xAI multipart format/MIME/limits/403/429/timeout.
- [x] Implement and verify using official docs/local SDK. Coordinate output_schema/web_search with assistant implementer.
- [x] GREEN: Node/Python tests; isolated real native web capability probe when safe, no switching provider.

## Task 5 — Frontend and composition (parent)

Files: app_setup.py; frontend App/index.css/FilesPage/UploadFile/api, RadarPage/radarApi/RadarReport, TestChat/chatApi/ProviderSettings, new TicketsPage/VoiceInput/result cards; browser tests.
- [x] Apply frontend-design + humanizer, derive visual tokens from source; preserve accessible controls.
- [x] Files: obvious persistent dropzone, drag/drop, usage role editor, per-row readiness/status/diagnostics, existing hard deletion preserved.
- [x] Radar: three equal single dropdowns, new filters, separate CTA, result/history columns; all legacy/current results and safe downloads.
- [x] Assistant: rename, actions/results/PDF/deep links, docs/diagrams/web, microphone draft; tickets page and voice key setting. Bind all routers/services via app_setup without cross-core module imports.
- [x] RED→GREEN browser/API integration; 390/1440 viewport and keyboard checks.

## Task 6 — Data and live evaluation (parent)

- [x] Build clearly labelled economic/production mock CSV from source nomenclature/constraints plus explicit assumed numbers; never invent real plant facts.
- [x] Upload mock to Files, set economics role, wait actual complete index; assign exact existing procurement/stock roles after integrity checks.
- [x] Backup/fingerprint; install new wheel/runtime/frontend while preserving user data/provider/VPN/volumes.
- [x] Current-date radar on full selected source + historic20.01.2026 controlled run, track every input row; add deterministic mock boundary cases independently.
- [x] Real assistant actions/radar result PDF/link/document/diagram/ticket and explicit no-key web error. The user deferred xAI key entry and requested verification without it: protocol, browser lifecycle and public unconfigured response passed; actual xAI STT/web responses remain unverified.

## Task 7 — Independent reviews and completion

- [x] Fresh independent subagents «Дебаггер» and «Архитектор» inspect all implementation, errors, architecture, permissions, data preservation. Reproduce issues RED→GREEN and re-review.
- [x] Full backend/Node/browser/build/lint; compare installed hashes and pre-existing row/object fingerprints; inspect app desktop/mobile and actual generated documents.
- [x] Update paired .dev/AGENTS.md/CLAUDE.md and operations/quality report; keep exact evidence in .dev/local/platform-upgrade-2026-10-27/. No commits, push, PR or cleanup of others' work.

Final verification:401Python,41Node,67browser+3narrowchecks; final live receipt feedback, exact3-nodeSVG, full2290radarPDF/latest-role run and no-key negative flow. Current/historical all2290accounted; final fingerprints7680originalrows/19objects preserved. Evidence and limitations: ../quality/workspace-upgrade-2026-10-27.md.
