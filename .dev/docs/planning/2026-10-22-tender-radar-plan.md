# Тендерный радар — Implementation Plan

> Исполнение: executing-plans inline, TDD, одно независимое ревью. АВТОНОМНО разрешает этапы; запрет Git-изменений в core, VPN/MOX/OSP и внешних путей сохраняется.

**Goal:** Реальный отбор тендеров из XLSX/CSV по явно выбранным пользователем RAG-файлам с сохранённым проверяемым отчётом.
**Architecture:** Модульный монолит, прежний worker/модельный контейнер, отдельные адаптеры импорта/хранения, deterministic eligibility + bounded structured LLM.
**Stack:** Python/FastAPI/PostgreSQL/SQLAlchemy, openpyxl+defusedxml, React/TypeScript, существующий SDK/CLI.
**Spec:** ../architecture/18-tender-radar.md.

## Ограничения и фокус ревью

Только текущий корень и .dev для инструментов; никаких auth/VPN/Git мутаций. Новые таблицы additive. Scope/actor/revision и выбор RAG не обходить; закрытые/устаревшие закупки не выдавать за текущие; исторические даты/leading zeros/Excel hyperlinks сохранять. Проверять враждебные архивы, formula, конфликт дублей, model JSON/injection, crash и stale отчёт. Платное/недоступное API не изображать работающим.

### 1. Источники и содержательные файлы
- [x] Перечитать12 оригиналов, проверить hashes, роли/ограничения/ADR.
- [x] Завершить официальное исследование API/export и публичных текущих карточек.
- [x] Через artifact-tool создать profileCSV по реальным изделиям и currentCSV по проверенным карточкам; доказательства/даты отдельно, без фиктивного производства.

### 2. Импорт и правила
- [x] RED: native XLSX row4/leading zeros/dates/hyperlinks/footer; CSV bad dates/cancelled/conflicting IDs; selected-file scope/pinning; no unselected text; model validation.
- [x] Реализовать infrastructure/tender_importer.py, modules/tender_radar/{analysis,service}.py; DocumentReader OOXML text; openpyxl/defusedxml pinned.
- [x] GREEN на реальном F02 и независимых malicious/edge fixtures.

### 3. Сохранение и фон
- [x] Additive migration0007/radar tables, infrastructure/radar_repository.py, API DTO/router, worker wiring; существующие permissions.
- [x] RED/GREEN PostgreSQL: команда/повтор/конкурентность/авария/изоляция и сохранённый отчёт.

### 4. Пользовательский сценарий
- [x] frontend RadarPage/radarApi/report, вкладка/пагинация файлов/выбор batch/focus/restore/download.
- [x] Mock browser tests, build/lint, desktop/mobile screenshot QA.

### 5. Доказательства
- [x] Развёртывание без потери данных. Загрузить substantive profile+real currentCSV+originalF02, живой MOX анализ и смена RAG, проверить SQL/reload/export.
- [x] Полные подходящие regression suites, независимое ревью/fixes, preservation/auth hashes, документация/инструкция/ограничения.
