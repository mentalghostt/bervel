# Files without revisions — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans. User explicitly authorized autonomous execution of every stage.

**Goal:** Уничтожить ровно 28 старых скрытых файлов и убрать механизм редакций файлов, сохранив активные данные и работу приложения.

**Architecture:** Один неизменяемый файл — один UUID; индекс, источник и результат адресуют этот UUID. Файловые метаданные находятся в artifacts, tombstone удалённого файла имеет пустые метаданные. Однократная миграция преобразует прежние технические ссылки; рабочая совместимость с редакциями не сохраняется.

**Tech Stack:** Existing Python/FastAPI/SQLAlchemy/PostgreSQL/pgvector, React/TypeScript, Docker; no new dependencies/services.

**Spec:** 2026-10-28-files-without-revisions-design.md, accepted with explicit deletion amendment.

## Global constraints

- Root C:/OSPanel/home/bervel.local, Personal/core, existing dirty tree. Git read-only, no worktree/copy/move. Task material only .dev/local/files-without-revisions-2026-10-28.
- Preserve VPN, OSP modules, provider, credentials, readonly MOX mounts and existing application data outside authorized deletion.
- The 28 target IDs are frozen before mutation. Use installed public deletion service before new schema; never broad DELETE by deleted_at after manifest creation.
- No fake revision=1, alias ArtifactRevisionRef, revision endpoint or file.revise/history operation in runtime. Historical migrations remain necessary deployment history.
- Preserve versions belonging to other entities: tickets/state, schemas/algorithms, datasets. Only file revision fields are removed.
- Preserve raw historical answer text/PDF/DOCX bytes. Convert structured app-controlled source links without changing numeric tender outcomes or invoking models.
- Main migration only after isolated verification, backup after authorized deletion, and active jobs drained. API and all Python workers must change together for new schema.

## Fixed shared interfaces

- core.models.ArtifactRef(artifact_id:UUID); core.models.Artifact(ref,metadata,content), replacing ArtifactRevisionRef/ArtifactRevision entirely.
- RegisterArtifact(content,filename,media_type,source,command_key,source_role='test_fixture') creates a new independent file; no artifact_id or expected_revision fields.
- ArtifactService.register(actor,command)->ArtifactRef; read/metadata/prepared_metadata(actor,ArtifactRef); delete(actor,artifact_id:UUID) with no expected_revision; set_usage_role/list unchanged semantically. history removed.
- DocumentSnapshot/DocumentSource keep artifact_id,filename,created_at,media_type,size/locators/status; no revision.
- API POST /artifacts -> {artifact_id}; GET /artifacts/{id}; GET /artifacts/{id}/content; GET /artifacts/{id}/preview?cursor&limit; DELETE /artifacts/{id}; GET /artifacts/{id}/knowledge; POST /artifacts/{id}/knowledge/resume with existing empty {} body. Usage-role route unchanged. No /revisions or /history endpoints.
- Every radar file ref is {artifact_id}; file results/cards and URLs omit revision. Chat sessions/ticket contracts unchanged except embedded file refs.
- artifacts adds metadata JSONB nullable, drops current_revision; artifact_revisions removed. knowledge/csv/document indices key only artifact_id (plus ordinal for chunks), FKs to artifacts. dataset_revisions drops artifact_revision while keeping its own revision. radar_batches drops artifact_revision, owned by consumer metadata; migration owned by core task.
- file.revise/file.history removed from assistant catalog; document edits produce a new file.create and preserve original unless user explicitly requested deletion. Keep intent/permissions/scope/idempotency/unknown guards and 192/240KiB/six-round context limits.

## Review focus

- Same filename or identical bytes are independent files, while retrying one command returns its original ID; deletion never removes shared bytes still referenced.
- Deleted old sources return410, foreign sources404; old revision bookmarks cannot silently resolve to another content.
- Migration must preserve exact sources, embeddings and numeric report outcomes; transform only technical refs, not user tables/content that happen to contain keys named revision.
- Older create/delete/action receipts must not resurrect a deleted file or repeat effects. New documents are separate originals, role changes do not create content copies.
- No API/worker uses old schema during migration; fresh database and upgrade from populated0012 both match final owned metadata.

## Task1 — Parent: exact authorized purge

- [x] Freeze 28 IDs/32 variants and active6 into task manifest; assert hidden date28Oct cutoff unchanged and byte/object checks.
- [x] Use installed DELETE endpoint with each pinned expected_revision, record individual outcomes; retry only explicit incomplete cleanup with same target.
- [x] Verify no target revisions/search data remain; unshared object bytes gone, shared originals protected; all active files and chat/radar/ticket records preserved.
- [x] Take verified DB/object backup and preservation baseline after purge, excluding purged file bytes from fresh backup.

## Task2 — Core storage and migration implementer

Own src/bervel/core/**, including models/ports/services, document models/readers/memory, storage repositories, APIs, metadata and new0013 migration; root CLI only if it imports file revision types. Own non-module core Python tests and scripts/tests requiring new metadata. Do not edit modules/chat or modules/tender_radar or frontend.

- [x] RED tests for ID-only registration/read/knowledge/delete with same-name/shared bytes/retry/scope; old-file ref/expected_revision rejected rather than ignored.
- [x] Implement fixed interfaces, no compatibility aliases or fixed revision. Update file/index/data ownership and SQL.
- [x] RED populated0012→0013 migration test: multiple original variants, tombstones, ready embeddings, reports/chat/action refs, dataset own revision retained; then implement one-time scoped conversion. Preserve user content fields and execution receipts.
- [x] Verify fresh DDL, populated upgrade, current core tests and new API OpenAPI shape. Save focused log/source hashes and exact migration details.

## Task3 — Backend consumers implementer

Own src/bervel/modules/chat/** and src/bervel/modules/tender_radar/** plus their Python tests. Consume fixed core interfaces. Coordinate radar metadata column removal with core migration owner; no core edits.

- [x] RED assistant file create/read/delete, editing produces distinct newfile, no revise/history tools; receipt/scope/intent/unknown defenses preserved.
- [x] Convert chat service/context/planner/actions/models and results to ID-only sources/links, preserving lazy reads and metadata-only commands.
- [x] RED radar latest/selection/import/run/PDF round-trip for ID-only sources, all rows/filters/numeric pipeline unchanged; update code and owned tables.
- [x] Verify focused assistant/radar suites, saved reports through new DTOs/PDF, file deletion sources410. Save report/hashes.

## Task4 — Frontend implementer

Own frontend/**, .dev/tests/frontend*.cjs and .dev/package.json only. No backend edits; consume pinned API and public DTOs.

- [x] RED tests ID-only upload/list/preview/download/readiness/delete and exact-source navigation; no revision text/parameters. Old bookmarks give honest obsolete message.
- [x] Convert all file types and routes in Files/Upload/Delete/preview/chat/results/radar; preserve chat sidebar/fixed composer/voice and all current UI copy/layout.
- [x] Update browser fixtures/assertions only for approved contract removal; preserve security, unknown outcomes, races, deleted source and pagination checks.
- [x] GREEN build/TS/lint/full browser +1440/390 screenshots; freeze immutable tested image. Do not install main.

## Task5 — Parent integration/review/deploy

- [x] Independent spec/quality/architecture review of core and consumers, then final feature review; fix findings via owner with reproductions.
- [x] Full Python/browser and isolated restore of post-purge real DB, migration verification and logical preservation comparison. Backup and secrets remain within task area/Docker authorized storage.
- [ ] Drain jobs; stop only API/Python workers during schema transition; migrate transactionally and install frozen API/frontend/workers together, preserving Codex/Postgres and mounts.
- [ ] Real public upload→ready→read/search→new document→download/delete, chat/tickets, saved radar report/source/PDF and full coverage launch appropriate to selected real source. Assert no revision contract remains in runtime, six original active files preserved,28targets gone.
- [ ] Final fingerprints/logs, docs/paired memory, stop only owned QA container. No commits/push/PR or broad cleanup.


End Full checkpoint: source/tests and isolated upgrade complete; main0013/API/workers/frontend installation and successful live LLM radar remain unfinished. See quality/files-without-revisions-2026-10-28.md and task progress.md. Owned QA removed; main healthy/unchanged after authorized purge.
