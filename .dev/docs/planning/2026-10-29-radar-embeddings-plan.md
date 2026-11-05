# Radar Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. User selected autonomous execution; no intermediate approval gates.

**Goal:** Два прохода ИИ с гибридным поиском складских кандидатов после прежних фильтров.

**Architecture:** Чистые контракты извлечения/сопоставления в tender_radar/semantic.py. Общая память выполняет scoped hybrid retrieval. RadarService связывает этапы, существующие фильтры и расчёты остаются прежними.

**Tech Stack:** Existing Python, PostgreSQL/pgvector, OpenAI embeddings, Codex runner. No new dependencies/migrations.

**Spec:** [Design](2026-10-29-radar-embeddings-design.md).

## Global Constraints

- C:/OSPanel/home/bervel.local; core; Git only read; user VPN/OSP unchanged.
- filter_rows, preselect, stock_check, economics_check unchanged.
- Exact quotes/quantities and all mandatory positions survive both passes; no numerical hallucination.
- Failed semantic retrieval cannot silently become lexical success; no automatic model replay.

## Review Focus

- Repeated quantity lines and incomplete extraction cannot become a complete match.
- A candidate belonging to another tender/position cannot be selected.
- Empty retrieval cannot justify a hard absence-of-stock claim.
- Provider429 during extraction or matching stops later portions.
- Existing files, old runs and source indexes survive installation unchanged.

### Task1: Contracts and scoped evidence

Files: new semantic.py, tests/test_radar_semantic.py; core/document_memory.py, tender_radar/ports.py.

Interfaces: extraction_prompt(rows), parse_extractions(rows,answer); matching_prompt(rows,sources,focus,extractions,candidates,catalogue), parse_matches(...); memory.matching_context(actor,snapshot,question) returns RetrievalResult.

- [x] Write tests for quotation/quantity identity, mixed positions, SKU locality, strict hybrid mode and row-to-SKU mapping; observe failures.
- [x] Implement pure contracts and scoped retrieval using existing KnowledgeService; run focused tests.

### Task2: Orchestration and quota

Files: tender_radar/service.py, worker.py, tests/test_radar_semantic_run.py, relevant existing launch/worker tests.

Consumes Task1 interfaces; produces new pipeline1.4, additive report trace and phase counters.

- [x] Write service tests: actual two-stage prompt transport, correct arithmetic, mixed lot, extraction/matching429, missing embeddings, prefiltered rows never reaching AI; observe failures.
- [x] Implement1.4 branch and checkpoint/counters, cache identical retrievals per run, adjust four role budgets8000 each.
- [x] Run focused and complete Python tests; record any unrelated failures by name.

### Task3: Review, live proof and installation

Files: quality/radar-embeddings-2026-10-29.md; local/radar-embeddings-2026-10-29/ evidence.

- [x] Independent read-only final review; address actionable findings with failing tests first.
- [x] Build and inspect wheel/image; restricted real model/embedding control using current selected files; no full1994-tender rerun.
- [x] Backup, verify idle, install compatible Python image without migrations; check prior data/object fingerprints and active services.
- [x] Document exact outcome, limitations and user-visible verification. Preserve Git index.
