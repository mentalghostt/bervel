# Workspace polish implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans task by task.

**Goal:** Исправить перечисленные пользователем интерфейсные проблемы и ложный отказ ассистента, проверить единую цепочку запуска радара.

**Architecture:** Existing React UI and Python modular monolith. Plan-first assistant with lazy public file.read, existing public RadarService, immutable history and receipts. No new subsystem.

**Tech Stack:** Existing React/TypeScript/CSS, Python/FastAPI/PostgreSQL, Node runner, Docker.

**Spec:** 2026-10-28-workspace-polish-design.md.

## Global constraints

- C:/OSPanel/home/bervel.local, Personal/core, dirty existing tree. Git read-only; no copy/worktree/move.
- All evidence under .dev/local/workspace-polish-2026-10-28. No external auth/VPN/OSP changes.
- Preserve actor/scope, effect/unknown dedup, six planning rounds, model192/240KiB budgets, roles/readiness, original data, full deletion contract and voices/settings.
- Autonomous choices and continuation authorized. No extra feature/dependency/migration.

## Review focus

- Clicking delete on unselected file must delete exactly that file, preserve the selected preview/others, no nested buttons or duplicate dialog IDs.
- Modal close/open/in-flight and unknown upload outcomes preserve command/ref and don't reupload confirmed original.
- Historical/deleted file links open exact source or honest410; stale role responses don't alter another selected file.
- Repeated reads/history selection never reorder by viewing; long PDF/text/CSV/report grow down, page/mobile no horizontal overflow.
- Lazy reads still supply genuine file facts/images/citations; commands get no unrelated content. Internal ticket draft allowed but injection/unauthorized effects denied.

## Task1: Frontend all requested polish (independent implementer)

Files: frontend/src/{App,index.css,FilesPage,UploadFile,DeleteFile,FilePreview,PdfPreview,RadarPage,RadarReport,TestChat,AssistantResults,api}. Existing frontend tests; new frontend-workspace-polish.cjs. Own frontend only; no backend edits.

Produces: current API-compatible UI, exact-source hash route (#/files?artifact_id=...&revision=N); unique source links. No ChatSource schema change.

- [x] RED behavior tests for vertical page growth/narrow list/modal controls/card deletion/compact metadata/deep links; history sort and button placement/source links/copy.
- [x] Implement minimal component/CSS changes preserving all prior protections; update tests only for explicitly requested new behavior.
- [x] GREEN affected and full browser tests, build/TypeScript/lint, screenshots1440/390 inspected. Build own tagged test image/container; do not install main.

## Task2: Assistant command routing and ticket intent (independent implementer)

Files: src/bervel/modules/chat/{service,planner,actions,context}.py as necessary, owned Python tests. No frontend/core/migrations/Node/app_setup edits without coordination.

Consumes: existing actions.file.read and memory.context/images, public services. Produces unchanged ChatTurn/results/sources schema, lazy sources based on executed reads, clarified internal draft intent policy.

- [x] RED: CSV metadata request never calls content search; ordinary ticket request no file retrieval and accepts internal draft; document read still gets sources/images, errors retain only actual read sources; legacy mode stays.
- [x] Implement plan-first behavior using existing public operation; never blanket bypass authorization or duplicate guards.
- [x] RED/GREEN authorization native case and quoted/injected negative cases; tests must prove actions actually persisted/denied, not only assert a prompt string.
- [x] GREEN targeted/full Python. Native no-effects classifier/planner probe using exact user text with no invented facts. No main effects/deployment.

## Task3: Parent integration and live evaluation

Files: own task helpers/evidence, narrow contract tests when required; docs/paired memory.

- [x] Prove router and assistant use same RadarService.analyse/normalized refs+filters, including defaults; no alternate pipeline.
- [x] Independent review of Task1/2, repair/recheck findings, preserve previous workflows.
- [x] Backup/fingerprint current DB/object data, install tested images only with existing readonly MOX mounts. No migrations expected.
- [x] Public real CSV-list, ordinary ticket request, file-content read/source link, operator action smoke. No external ticket delivery.
- [x] Via assistant launch full2290 normal current-date radar with latest READY3roles and explicit normal filters, await completed/allrowsaccounted, inspect PDF/link. Don't repeat legacy/artificial1trillion run as standard test.
- [x] Actual UI1440/390 screenshot/console/scroll/focus tests; final data preservation/image-source identities/docs check; update plan/ledger/report. Stop only own QA container, no broad cleanup/Git.
