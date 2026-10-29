# Shared knowledge implementation plan

> Исполнитель: текущий агент, executing-plans. Режим АВТОНОМНО заранее разрешает выбор решений и переходы; коммиты в core запрещены. Артефакты только в.dev/local/knowledge-2026-10-27.

**Goal:** Original → extraction → chunks → OpenAI embeddings → shared retrieval, with real-data verification and safe re-upload.
**Architecture:** Existing core DocumentMemory + PostgresDocumentRepository + worker. Business modules consume text evidence, not database or embedding SDKs.
**Tech Stack:** Python stdlib HTTP, SQLAlchemy, PostgreSQL17 + pgvector, existing React UI.
**Spec:** .dev/docs/planning/2026-10-27-shared-knowledge-design.md

## Global constraints

No Git mutations, VPN/OSP changes, external MOX credential changes or project moves. Backup before file removal. No automatic retries. No forged vectors or fake-model quality claims. Keep complete radar profile and existing histories.

## Review focus

- Wrong-scope/revision evidence must never be returned.
- Partial/unreadable sources must remain visibly incomplete.
- Crashes/repeated uploads must not silently duplicate embeddings or lose completed portions.
- Retrieval must retain table headers, lot scope and source locators.
- Restore/re-upload must reproduce every backed-up original byte-for-byte.

## Task1 — Baseline and backup

- [x] Snapshot runtime source hashes and current SQL/file inventory; capture lexical retrieval baseline before code changes.
- [x] Save and verify pg_dump and originals in task folder; keep original services/config.

## Task2 — Chunking, embedding adapter and database

Files: core/chunking.py, core/knowledge.py, core/infrastructure/embeddings.py, core/infrastructure/knowledge.py, migration0008, Docker postgres build, tests/test_knowledge*.py.

- [x] Write failing tests for deterministic chunking/locators and real adapter validation/failures.
- [x] Implement minimal extraction/chunk preparation and EmbeddingClient.embed(texts)->list[list[float]].
- [x] Add vector/job schema and real PostgreSQL tests for revision/scope/persistence/state transitions.
- [x] GREEN component tests, including fresh migration in isolated test schema.

## Task3 — Upload, worker and shared retrieval

Files: core/services.py, core/infrastructure/repository.py/documents.py, core/document_memory.py, core/api, app_setup.py, worker.py, config.py, compose.yaml, frontend/src/FilesPage.tsx/api.ts.

- [x] RED tests for atomic enqueue on upload, idempotency, status and shared retrieval.
- [x] Implement bounded worker portions; persist vectors and expose status/manual resume.
- [x] Connect DocumentMemory semantic+lexical retrieval and complete-profile path; preserve integrity checks.
- [x] Show indexing state in Files UI; verify backend and browser suites.

## Task4 — Install, re-upload and quality evaluation

- [x] Verify real OpenAI key and connection; install tested image/migration without resetting volumes.
- [x] Verify backup, remove previous uploads from active catalog, upload copies through normal API; verify hashes/statuses.
- [x] Run fixed lexical-vs-embedding retrieval evaluation and real test-chat questions. Repair representation/chunking if no improvement.
- [x] Fresh reviewer (requesting-code-review); address important findings, run final checks; update architecture/operations/quality/AGENTS/CLAUDE.
