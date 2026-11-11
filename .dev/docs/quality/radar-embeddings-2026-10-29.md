# Радар1.4: результаты проверки

Корень C:/OSPanel/home/bervel.local, Personal/core. Пользователь29.10.2026 выбрал АВТОНОМНО и два прохода ИИ, без изменений фильтров/арифметики. База60836e9298bd1d722902e0cd589b2583c369c1e4; Git только чтение. [Контракт](../architecture/29-radar-semantic-matching.md).

## Код и тесты

- Новые контракты сначала дали17RED, затем17GREEN. Оркестрация и квота:6RED, затем исправлены. Отдельные review-кейсы overlapping quotes и известный oversized matching answer воспроизведены RED до исправления.
- Финальный полный Python suite:675passed,3предупреждения (Starlette/httpx и распознавание public.vector в двух schema checks),292.91s. Файл local/radar-embeddings-2026-10-29/python-final.txt.
- Существующие фильтры/расчёты full.py,preselection.py,stock.py,economics.py byte-for-byte совпадают с исходной версией. Проверка image-proof.json подтверждает ровно7изменённых Python-файлов, включая новый semantic.py; зависимости прежние.
- Независимое read-only ревью: P1 повторное использование одной количественной строки и P2 бюджет ответа/изоляция большого тендера исправлены; UI/PDF распознают1.4. Незакрытых actionable findings не осталось.
- Frontend собран и прошёл TypeScript/build/oxlint. Целевые браузерные проверки1.4,legacy и not_analysed:3/3. Общий выбранный browser suite27/44; все17падений воспроизведены на исходном работающем образе (там26/44, дополнительно падает новый тест1.4). Новых падений нет; общий browser suite не зелёный. Полные имена и сравнение находятся в task-local frontend-*.txt и ui-baseline-comparison.json. Правка UI ограничена распознаванием версии1.4.

## Реальная модель и эмбеддинги

Непишущий контроль одного тендера №0317400001026000107 по точному ценовому фильтру из1994. Оригинальные XLSX/склад/экономика из существующего приложения, без новых файлов/тендеров в БД. gpt-6.1-sol; OpenAI text-embedding-3-small:1536; extraction_requests1,matching_requests1,embedding_queries1,portion_errors[]. Извлечена исходная общая фраза, количество/единицаnull, полнотаfalse. Найдены10складских кандидатов. Итогuncertain/needs_review: в XLSX нет состава/размеров, выдуманных характеристик не появилось. Это проверка интеграции, не измерение прироста precision/recall.

Первый QA-контейнер не был подключён к закрытой Docker-сети codex: DNS codex не разрешался, запрос до исполнителя не дошёл (MODEL_UNAVAILABLE). Диагностика установила API в bervel_default+bervel_codex, Codex только в bervel_codex. Новый ограниченный контроль подключён к обеим существующим сетям; исходная неудача сохранена отдельно, неизвестный запрос не переигрывался. Сети основных контейнеров/VPN/OSP не менялись.

Материалы: local/radar-embeddings-2026-10-29/probe-connected-result.json,probe-connected-state.json. Идентификатор пробы dda8899b-2dbd-4306-8e5b-0c77d522e22f не является сохранённым radar_run.

## Установка

Установлено29.10.2026, около16:36МСК. Python image da8a77e14a0f3f3a1da7f2a5ec638e135dee005d80201ebcacfc440899f03d67; frontend77a9cf644fee5d01c4f9be215db24d2af062b781ce5ae19f1aebffe070bf97b0. Все9служб работают. PostgreSQL/Codex сохранили container ID; миграции не запускались. Настройки сверены по значениям с неизменённым Compose и defaults прежнего образа (первый hash env был чувствителен к перестановке ключей). Все mounts/команды сохранены. Compose при recreation inspection-reader заменил Docker Desktop source сокета на /var/run/docker.sock; до приёмки восстановлен точный прежний /run/host-services/docker.proxy.sock через task-local preserve-inspection-mount.compose.yaml. Новых прав читателю не добавлялось.

Cold backup БД65525693байт, SHA256 b6ac44fbe556e556b1b1ccbc66af8a2b38365f68e4fa67186cbe5c803f420032. pg_restore --list содержит все23таблицы; в tar независимо проверены хеши11оригиналов. Полное восстановление этой копии не выполнялось: схема не менялась. Backup/image tags оставлены внутри разрешённого рабочего окружения.

После установки создан ровно1контрольный запуск через настоящий API/worker: e8508db2-82c8-4247-9f1b-3d1e33050601, тендер0317400001026000107, pipeline1.4, completed,2запросаИИ/1embedding-query, portion_errors[]. API сохраняет и отдаёт semantic_analysis. Результатneeds_review по отсутствующему составу. Из1994записей ценовой фильтр оставил1; полный повтор анализа1994не запускался. [Открыть сохранённый отчёт](http://127.0.0.1:5173/#/radar?run=e8508db2-82c8-4247-9f1b-3d1e33050601).

verification-final.json: все8370прежнихстрокБД и11оригиналов неизменны; добавлены radar_runs1,radar_batches1,audit2,command_receipts2. Git index F90247865C153A1F51A2594374EECA49D1E4ABA8B603B37C85F6A3AE594C4E0A прежний. Основной UI реально проверен на1440/390px: сохранённый результат,2модельныхзапроса,нетpage errors/overflow,никакихAPI mutations; live-ui.json и PNG. Собственный QAfrontend-контейнер удалён после проверки точного ID/image; чужие orphan-контейнеры не затронуты.
