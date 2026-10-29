# Radar stock selection implementation plan

> Исполнитель: текущий агент, executing-plans inline; финальное независимое ревью. Автономный запрос разрешает внутренние переходы; никаких Git-коммитов/веток/публикаций в core. Материалы только .dev/local/radar-stock-2026-10-27.

**Goal:** all tenders → deterministic eligibility/name+description → grounded LLM meaning/positions → Decimal free-stock threshold.
**Architecture:** existing radar module/worker + shared originals/DocumentMemory; narrow StockImporter adapter, no new dependencies/services.
**Tech Stack:** existing Python/FastAPI/SQLAlchemy/PostgreSQL/React; stdlib Decimal and CSV.
**Spec:** .dev/docs/planning/2026-10-27-radar-stock-design.md

## Global constraints

Read-only Git, preserve existing history/queued commands, no VPN/OSP/MOX changes. Do not infer real stock from request quantities; derived mock explicitly labelled. No automatic retries, partial reports or silent row loss.

## Review focus

- Ambiguous names and relevant descriptions survive preselection; unknown profiles never hard-exclude by a fixed unrelated-products dictionary.
- SKU/quantity evidence belongs to the same tender; repeats aggregate without inventing availability or unit conversions.
- Missing stock/mixed mandatory positions never become accepted supply; mock remains visibly mock.
- Source revisions, scope and option fingerprints prevent cross-scope data and changed-command replay.
- Every input row has an auditable final stage; old reports still load.

## Task1 — Pure stock contract and arithmetic

Files: models.py, ports.py, new stock.py and infrastructure/stock.py; tests/test_radar_stock.py.
- [x] Back up touched source/runtime/DB metadata under task folder.
- [x] RED stock CSV validation and Decimal quantity/threshold/unknown/repeated-SKU tests.
- [x] Implement StockImporter.parse and stock_check(positions,complete,stock,percent); GREEN.

## Task2 — Funnel and grounded model positions

Files: analysis.py, new preselection.py, service.py, app_setup.py, importer.py; tests/test_radar*.py.
- [x] RED early expired exclusion, title+description, generic/mixed names and stage coverage.
- [x] Implement cautious preselection and grounded position schema/validation; preserve full profile and input order.
- [x] Integrate stock through injected port, exact descriptor checks and end-time eligibility; add report1.2. GREEN intended changes to old tests.

## Task3 — Persisted contracts and API

Files: migration0009, tables.py, repository.py, api.py; API/PG tests.
- [x] RED options/snapshot/scoping/fingerprints/legacy report tests.
- [x] Add selection column with additive migration; expose typed stock assessments/filters/funnel. GREEN fresh migration and historical reading.

## Task4 — User controls and report

Files: frontend/src/radarApi.ts, RadarPage.tsx, RadarReport.tsx; infrastructure/pdf.py; browser/PDF tests.
- [x] RED tolerance/source submission and quantity/mock reporting.
- [x] Implement controls and shared report values in UI/PDF, retain current design; GREEN build/lint/browser/PDF layout.

## Task5 — Real checks, review and install

- [x] Create clearly labelled normalized mock from user-selected profile, preserve original and all current data.
- [x] Full backend/runner/browser checks, one small real LLM evaluation; measure prefiltered rows/model calls and quantity decisions.
- [x] Independent reviewer, fix findings and required rechecks.
- [x] Install candidate API/worker images + additive migration + frontend after checking active jobs; leave Codex/MOX/other data intact.
- [x] Verify live pipeline and report/source hashes, shared indexing and independent workers. Update architecture/quality/operations/AGENTS/CLAUDE/ledger. No Git writes.
