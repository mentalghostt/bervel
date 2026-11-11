# Вертикаль 4 — интеграция, очистка и выпуск

Сохранить техническую очередь и постоянный run_id профиля; `RadarRuns.execute` dispatch profile_refresh до legacy-кода. Подключить `service.run_pipeline.profiles = RadarProfiles(...)` в composition root, HTTP router в app_setup, schedule tick внутри существующего analysis worker loop. Чат и обновление имеют разные queue lanes; текущие guards/политику unknown не удалять. GET профиля использует сохранённый актуальный анализ, не переписывает его историческим stock snapshot и не вызывает модель.

## Удаление старых данных

Прямой ответ владельца: «Резервную копию старых запусков не создавай, удаляй». Это разрешение только на старые данные радара. Не создавать dump/backup этих запусков. Таблицы: ai_radar_reports, ai_radar_search_pages, ai_radar_analyses, ai_radar_legacy_groups, ai_radar_runs, ai_tender_radar_jobs. До удаления проверить отсутствие active queued/running/waiting. Транзакция DELETE в правильном порядке, без CASCADE, без удаления оригинальных файлов/objects и без изменения общих company_settings/artifacts/datasets/chat/inspection/audit. Сохранить только counts/hashes доказательств, не содержимое старых тендеров. Сравнить counts и детерминированные хэши всех остальных публичных таблиц до/после; все совпадают. Изначальный read-only evidence taskroot не является резервной копией запусков и не содержит полных тендеров.

## Проверки и окружение

Host Python не найден, использовать имеющийся test image `bervel-tender-decision:tests-20261106`; тестовая БД `bervel_core_stage31_test` контейнера `bervel-inspection-test-postgres-1`, отдельная сеть `bervel_inspection_tests`. Не использовать рабочую БД для pytest. Локальные temp/cache/tokenizer/JUnit, browser profiles и screenshots — `.dev/local/radar-profiles-2026-11-09`. Изолированные схемы тестов удаляет fixture только в тестовой БД. Node/Playwright уже установлены в `.dev/node_modules`, Chrome системный.

Context7 resolve-library-id оказался недоступен (явный tool error). Fallback — актуальные официальные SQLAlchemy/PostgreSQL, React input и FastAPI body docs; ссылки сохраняются в отчёте. Не ставить новые библиотеки.

## Узкий выпуск

Текущие api/worker/frontend: `bervel-radar-three-fixes:*20261108-18a3d011`; schema0027a. Inspection0028 и часть chat/native runner подготовлены другими задачами и пользователем отложены. Не выпускать весь dirty tree. Подготовить Docker overlay FROM установленного образа с COPY только проверенных файлов радара + composition diff; app_setup/worker проверять относительно установленного baseline. Frontend build подставляет установленный baseline для TestChat/chatApi (предыдущая проверенная схема selective source), остальные соседние файлы сравнить с установленным dist/source и не включать без необходимости.

Снять текущий runtime shape в приватный taskroot, сохранить окружение/volumes/network/ports/security/user/healthcheck. Команда Docker Compose ограничена api/worker-ai-tender-radar/frontend `--no-deps`; не перезапускать codex/БД/симулятор/прочие worker. Миграцию применять target0027b, не heads в production. Перед применением проверить перечень таблиц и код миграции: только новые структуры радара/unique index, не изменение других таблиц. Backup старых тендеров запрещён и не нужен: по решению пользователя они удаляются.

После установки: readiness, правильные image IDs/target revision, нет рестартов и изменений container IDs остальных сервисов; профили доступны, старой истории нет. Источник реальных тендеров проверяется контрактными тестами без расхода paid details. Симуляционный refresh ограниченной подробной квоты после успешной проверки чистоты; на финале различать старые удалённые и новые QA-карточки. Не включать автоматическое расписание ради QA в пользовательском профиле. Кнопки/слайдеры/окно проверять реальным браузером, реальные запросы контролировать. Финальный отчёт `.dev/docs/quality/2026-11-09-radar-profiles.md` с матрицей 11 требований, командами/результатами, ограничениями и ссылками на screenshots.
