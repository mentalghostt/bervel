# Kontur API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. User authorized autonomous implementation, checks and installation; do not wait for intermediate stage approvals. No Git mutations.

**Goal:** Прямой источник Контур.Закупки с выбором API/выгрузки и запуском существующего радара.

**Architecture:** Новый внешний Kontur-адаптер возвращает нормализованные TenderRecord и неизменяемый снимок ответов. Прежняя очередь получает API-selection; worker подготавливает batch и вызывает прежний1.4. Nullable batch_id нужен только до получения источника.

**Tech Stack:** Existing Python stdlib, PostgreSQL/SQLAlchemy/Alembic, React/TypeScript. No new dependencies.

**Spec:** [Design](2026-10-29-kontur-api-design.md).

## Global Constraints

- Existing root/core; preserve previous dirty changes; no Git writes, VPN/OSP changes.
- KONTUR_API_KEY server-only, repr=False, only API/radar containers receive it.
- Existing filters/stock/economics unchanged. API JSON replaces manual XLSX/CSV, preserves original facts.
- Real API success must be proved; mocks and schema examples are not a real response.

## Review Focus

- Pagination starts and terminates explicitly; duplicate IDs/inconsistent TotalCount do not silently lose coverage.
- Joint/customer purchase objects do not double quantity or erase distinct customer orders.
- Blurred/missing numbers never become zero or confirmed conformity.
- Frozen API commands survive reload/uncertain enqueue without refetch/replay or mode drift.
- Existing populated DB/files/vectors and file-based reports survive migration/install.

### Task1: Vendor adapter and data evidence

Files: new modules/tender_radar/infrastructure/kontur.py; tests/test_kontur_api.py; core/config.py; app_setup.py; compose.yaml; .env.example.

Interfaces: KonturClient.configured, fetch(query)-> snapshot/normalized TenderImport/source metadata; validate_query(query)->canonical.

- [x] Red tests for HTTP contract, pagination, exact decimal facts, characteristics, repeated/summarized objects, redaction/error classification.
- [x] Implement adapter with pinned vendor host, bounded serial requests and no credential disclosure; focused tests green.
- [x] Obtain a bounded real response or explicitly record network/access blocker without inventing results.

### Task2: Durable API acquisition and compatibility

Files: tender_radar/service.py,ports.py,models.py,infrastructure/repository.py,tables.py; core/migrations/versions/0016_radar_api_source.py; tender_radar/api.py; tests/test_radar_kontur.py.

Consumes Task1; produces enqueue_api/pin_api_batch and /radar/kontur/status,/radar/analyse-kontur.

- [x] Red integration tests: no provider call in HTTP, null batch while queued, snapshot pinned before first model request, receipt/scope checks, acquisition failure, legacy file flow.
- [x] Implement queue/source preparation and typed public DTOs; migration only relaxes radar_runs.batch_id nullability.
- [x] Verify clean schema and migration over populated restore; run focused tests.

### Task3: Source selector and browser proof

Files: frontend/src/RadarPage.tsx,radarApi.ts,radarAttempt.ts,RadarReport.tsx,index.css; frontend tests.

- [x] Red browser tests for API selection/query, immutable uncertain retry, source counts, missing key and old file mode.
- [x] Implement in current visual style using frontend-design; build/lint and targeted desktop/mobile QA.

### Task4: Review and installation

Files: quality/kontur-api-2026-10-29.md; local/kontur-api-2026-10-29 evidence/helpers.

- [x] Complete backend suite and browser comparison; independent read-only review; fix actionable findings with RED→GREEN.
- [x] Backup + restore + preservation fingerprints; build/verify only task-specific image changes; migrate0016/install compatible services without credential/mount changes beyond intended API key.
- [x] Bounded real API/model control when connectivity exists; verify installed error handling and report any remaining external blocker honestly.
- [x] Record sources, actual accessible data/limits, tests, previous-row/object preservation and user-facing result. Git remains read-only.


Фактический итог: реализация, установка,701Python/9browser,restore/migration и сохранность подтверждены. Полный живой цикл API→2ИИ остаётся непроверенным: контролю47позиций мешает прежний RADAR_CONTEXT_LIMIT; контролю13позиций после успешного первого ИИ — EMBEDDING_REGION_UNAVAILABLE. Ограничения не обходились и данные не обрезались. Подробности quality/kontur-api-2026-10-29.md.
