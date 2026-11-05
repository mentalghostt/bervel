# Radar reliability implementation plan

> **For agentic workers:** Use scoped subagents for independent research and implementation, with task review and a final whole-change review. Preserve the user's autonomous mode and Git restrictions.

**Goal:** Запуск радара по полному CSV без ожидания общегоRAG, с автоматическим поиском и подтверждённым покрытием склада.
**Architecture:** Существующий модульный монолит; radar-only локальный гибридный товарный индекс, обработка структурированныхObjects по позициям, прежняя очередь. Общий RAG не переключается.
**Tech Stack:** Python, существующие FastEmbed/E5/ONNX, PostgreSQL/SQLAlchemy, React/TypeScript, текущий Контур API/Codex gateway. Новых сервисов нет.
**Spec:** .dev/docs/planning/2026-11-02-radar-reliability-design.md

## Global constraints

- Корень C:/OSPanel/home/bervel.local, Personal/core; код править на месте, Git только read-only.
- АВТОНОМНО: решения и переходы выполнять самостоятельно; VPN/OSP/чужие изменения не трогать.
- Старые команды/результаты не переписывать. Новые результатыpipeline1.7.
- Все непустые технические свойства и весь состав потребности сохраняются.
- Платные вызовы не повторять при неизвестном исходе; контрольные команды сохранять заранее.
- Доказательства и временные материалы только внутри рабочего корня.

## Review focus

- ПравильныйSKU не теряется из-за синонима, позиции вCSV, соседнего шага/покрытия или ограничения выдачи.
- CSVpending/failedобщейиндексации доступен радару; повреждённый/удалённый/чужой оригинал отвергается доAPIквоты.
-4432объекта и повторные позиции дают тот же состав/количества после порционирования.
- Несколько поисков не превышают общий лимит деталей, не дублируютID/суммарныеTotalCount.
-429/невалидныйответ/перезапуск не теряет успешные проверки и не публикует ложныйполныйотчёт.

## Task1 — local hybrid stock search

Files: new tender_radar/stock_search.py, infrastructure/stock_search.py; stock importer/model metadata; minimal LocalEmbeddings offline option. Tests: new test_radar_stock_search.py and quality benchmark in outputs.

Interface: `LocalStockSearch(embeddings).prepare(stock, fingerprint: str)` returns index with `query(position: dict, limit: int=24, offset: int=0) -> dict`: items(list of original StockItem), total(int), has_more(bool), mode('hybrid_local'). Position has name, characteristics and optional quantity/unit/quote. Cache key includes fingerprint and embedding space. Helpers `compact_item(item)` retains every nonempty property and group/subtype. Search uses allSKU, semantic+lexical/structured ranking; no hard claim of conformity.

- [x] Write meaningful failing tests for multilingual/synonym candidates, dimensions/pitch/coating, repeated queries/cache, lastSKU, allnonemptyfacts, group metadata.
- [x] Implement with existing E5 model offline; split search texts within model budget; bounded cache.
- [x] Benchmark realCSV3076 and earlier35SKU confirmed cases; record recall and difficult cases, fix misses before acceptance.
- [x] Task review: spec compliance and quality; no fullCSVLLM payload and no global index switch.

## Task2 — automatic API terms and confirmed search semantics

Files: kontur_source.py, infrastructure/kontur.py; new focused query helper if justified; related tests. API DTO changes coordinated byroot.

Interface: `automatic_query(stock, user_query) -> dict` yields validated query with effective keywords plus auto groups; `KonturClient.fetch` supports that saved automatic plan while historical manual query works unchanged. Sourceinfo records actual requests, effective terms, limitedpool. No assumption Text[] isOR.

- [x] Live read-only search probes on stable date/stage for singular/plural/stem, at most12initialsearchcalls; save sanitized counts/IDs. Credentials remain inside configured client.
- [x] Red tests automatic terms dedup/groups, selectedstages, round-robinsharedcardbudget, duplicates and unknowncounts.
- [x] Implement automatic profile, independent searches and ranking by Objects; keep chosen max_tenders as totaluniqueGETlimit. Validate CSVbeforefetch.
- [x] Task review against probe evidence; no manufactured OR/morphology support.

## Task3 — structured pipeline1.7 and durable progress

Files: new positions.py/structured.py for pure object contracts; orchestration/service.py; repository.py/tables.py/migrationonlyifneeded for existingqueue retry/checkpoint; ports.py; API/models; full.py only additive1.7 support. Runner error metadata only if required.

- [x] Red tests APIObjects copied with fullcharacteristics and stableIDs,4432objects, duplicateSKU consumption, no oldfullmemorycontext, pendingstockCSV, responsevalidation and one repair.
- [x] Implement directObject needs and fallback extraction for missingObjects; position/candidate batching under realpayload limits.
- [x] Integrate LocalStockSearch and compact evidence. Server quantities/prices/requirements override model suggestions; exact conformity validated with all known object requirements.
- [x] Save canonical validated assessments in checkpoint, retry eligible explicit refusals without replay; hardquota/auth/unknownoutcome remain specific.
- [x] Test restart/resume/checkpoint/duewaiting and completefalse semantics. Preserve legacy contracts and pricing paths.

## Task4 — UI, stock preparation and integration

Files: RadarPage/RadarReport/radarApi helpers; scopedstockCSV readiness; coreknowledgequeue skip of stockCSV automaticgenericindexing without pretendingvectorsready; associated frontend/API tests. Root owns these files to avoid clashes with other chats.

- [x] Red tests no keywordfield, automaticbody, stockpendingselectable, exactrepeat oldbody, coveredpartial/full primarylist, zero/unknown separate, filters/sorts.
- [x] Implement usercopy and scopedstockreadiness; no globalartifactReadyweakening.
- [x] Integrate autosearch/coreprofiles/APIversion1.7; run relevant backend/browserchecks and inspect390/1440.

## Task5 — validation, review and safe installation

- [x] Replay fullrealCSV and saved100tenderbatch offline for identity/quantity/fullness and faultinjection. Run quality benchmark and scopedregressions.
- [x] Fresh independent wholechange review, fix confirmed issues and rereview.
- [x] Snapshot installedfiles/environment/data; testedrestore backup before migration/deployment. Build only selectedchanges, preserve otherchat/inspection runtime changes.
- [x] Install API/radar/knowledgeworker/frontend asrequired, noOSP/VPN/Gitmutation. Run realkontursearch and LLMcontrol against chosenwarehouse; verify confirmedpositivecoverageorhonestlogicalzero.
- [x] Record versions/tests/limits and final artifact/runIDs in repo memory; show final userresults, not just testpass.

Итог и границы проверок: docs/quality/radar-reliability-2026-11-02.md. Последний независимый технический признак обязателен, обязательных наборов длина/ГОСТ/прочность нет. «Болт М12» допускается при соблюдении всех раскрытых требований; общее название без конкретизирующего параметра остаётся неподтверждённым. Свежая заключительная проверка — 248 passed, установленный контроль completed/71 из 71/error=null. Автоматический поиск 20 карточек дал честный ноль подтверждённых совпадений; отдельная полная свежая карточка подтвердила 34 кг болтов.
