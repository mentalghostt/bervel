# Радар: завершение обновления при локальных ошибках

Установлено 10 ноября 2026 в17:22МСК, C:/OSPanel/home/bervel.local (Personal/core). Текущий контракт: ../architecture/03-tender-radar.md. Git только read-only, HEAD12ebcdb0 прежний. VPN, Open Server Panel, склад и другие разделы не менялись.

## Причина и исправление

Последние два фактических запуска завершались MODEL_INVALID_OUTPUT «Позиция документа отсутствует в подтверждённом составе закупки». Были готовы377 из400 текущих подробных анализов. После3попыток локальные ошибки попадали в общий fatal-список, и обновление падало; этот же список прекращал независимые повторы оценки ИИ других карточек.

Теперь первичная оценка, получение деталей, подробный анализ, индивидуальный рейтинг и показатели сохраняют отдельные локальные исходы. Конечная очередь обрабатывается полностью; неустранённые ожидаемые ошибки карточки после ограниченных попыток становятся явными замечаниями, не глобальным падением. Максимум10 подробных lifecycles и максимум3попытки сохранены. Durable успешные стадии, готовые результаты и исходные source inputs переиспользуются.

Валидация документов не ослаблена, неизвестные оценки не заменяются нулём. Ошибки авторизации, целостности снимка, остановка/lease fence и storage/DB остаются глобальными; catch-all больше не выдаёт ошибку записи за неизвестный ответ модели. При известном auth failure рейтинг сохраняет failed-envelope перед пробросом ошибки.

Общие карты checkpoint/attempts/errors изменяются под тем же RLock, под которым рабочие потоки сохраняют состояние. Ожидания future вынесены из lock; первичный ranking использует локальную карту и переносит копии под lock. Детерминированные GuardedMap тесты воспроизводили прежние гонки, затем прошли.

## Публичное поведение

Состояния API остались queued/running/complete/failed. При завершённом обходе с локальными проблемами state=complete и completion_summary version=profile-completion-v1 содержит уникальное число проблемных тендеров и counts по стадиям. Интерфейс пишет «Обновление завершено с замечаниями: N тендеров»; кнопка Resume больше не требуется для такого итога. Card.refresh_warnings содержит актуальные причины; сохранённый прежний анализ остаётся виден с явной подписью. Historical/out-of-cohort errors не примешиваются к итогам текущего refresh. Клиентского auto-POST/resume цикла нет.

Независимые manual/auto настройки, флажок выбора новых/всех подробных тендеров, metrics-only и чат сохранены. Currentv2 продолжен с прежним logical refresh и frozen inputs; numerical cache/stage version не переводились на новую политику.

## Проверки

- TDD: exact document-validator error, ранняя/поздняя проблемная карточка, all-fail, независимые retries рейтинга, primary failure, crash-after-save, stale warnings и shared-map mutations воспроизводились до исправлений.
- Финальный own+independent gate82 PASS/1DBskip; отдельный frozen independent44 PASS/1DBdeselected. Готовые377 карточек в синтетическом400v2 проверяются целиком, включая cache/projection/markers.
- Проверки именно установочного image:82 PASS/1DBdeselected,10.67с. Test-only образ добавил pytest dependencies поверх final API; bervel импортировался из site-packages, не из рабочего src. Внешняя сеть отключена, проект read-only, source/model подменены.
- SQL/API:52PASS в общем target gate, единственный auth regression исправлен и прошёл отдельно вместе с global failure target (2PASS). Итого53уникальных target-case, а не новый полный53run. Независимый persisted SQL/HTTP GET warning-contract1PASS, без мутаций от чтения и без source/model calls.
- Frontend:52/52 общий gate; fresh completion8/8 после мобильной проверки прокрутки; scoped TypeScript/oxlint exit0.1440/390 screenshots просмотрены. Старый неудачный вызов TypeScript сохранён в первом логе, финальный результат — frontend-finish-tsc.txt.
- Release preflight18PASS (штатная конфигурация и17 отклонённых опасных изменений); независимый exact image/layers/config/source binding PASS, P0–P2 нет.

Isolated test DB однажды не имела public.vector; восстановлена штатная extension после проверки точного Docker container ID/current_database=bervel_core_stage31_test. Production, migrations и conftest не менялись.

## Сборка и установка

Runtime bervel-radar-completion:20261110-api/worker/frontend,7 backend modules и3 frontend source,316assets. Schema0027b_radar_profiles прежняя. Source/dependency/migration других вертикалей не захвачены. API/worker/frontend заменены узко через compose --no-deps; остальные8 контейнеров, env/cmd/ports/mount Source/network aliases остались прежними.

В параллельной задаче менялись App/ProviderSettings/Presales и автоматически сканируемые CSS-классы. Только build overlay исключил эти ещё не установленные inputs, не меняя чужие файлы. Исходная сборка после изоляции совпала с установленной316/316SHA. Для исключений сканирования использована официальная [инструкция Tailwind](https://tailwindcss.com/docs/detecting-classes-in-source-files). Deferred inspection TestChat/chatApi также не попали в релиз.

Final manifest e1a9c430a1d493003126d7bbafc0d4cc73d2fd05e4bed0821b587a91f2741af5; compose a6360036353ab50603a82036c4d7a489a5f3633eaabb5bb6de55692b2f5aba33. Actual API b57b5365e5f8dcf21b3a7f7ec8dc1057449613c9e462f47174bf3df87731245b; worker40822eb89a6be7c282f924c3d88765c855125526abe7d6fc5d3cf5f796f63c28; frontendeb20329355d21e6ade2230731284161c72f14a1d44eeb0c01621896ac54b1d88. Approval привязан к ним; установленный SHA7+7+316 проверен. Первоначальный пакет был заменён до approval после self-review гонки, не устанавливался.

Установка/GET-only UI1440/390 не изменили ни одну из32таблиц — совпали и initial task, и непосредственно install-boundary snapshots. JS errors0, write requests0.

## Фактическое завершение

Одна штатная команда resume с сохранённым ключом создала job8f558d28-7297-4bf1-9d84-a97004f58374 для прежнего run61e2b011-d82f-4cdb-bef0-c584bb56890b/logical5f8.../v2. Далее автоматический worker выполнил остаток без дополнительных resume/full refresh.

В17:28:38МСК jobloaded, runcomplete/phasecomplete, globalerror=null. Остаточные23 анализа получили ровно по3попытки; summary.failed_tenders=23/analysis_failed=23, остальные stage-failure counts0. Проблемы разбора документов не превратились в выдуманные успешные анализы/баллы. Остались377 готовых текущих анализов и377 оценок. Исторические386 сохранённых analysis envelopes не удалялись.

Все исходные377 готовых карточек совпали целиком по JSON SHA; version/logical refresh/immutable operation digest и source56search/200details сохранились. Остались2774IDs и400fullcards; activejobs0. После разрешённого resume изменились только4radarтаблицы; остальные28 не изменились.

Финальный live GET-only browser gate1440/390: statecomplete,23 публичных карточки с причинами, точный итог с замечаниями, кнопки «Возобновить» нет, JS errors0/write requests0. Новый платный поиск/получение подробностей для smoke не запускались; модель использовалась только для разрешённого продолжения23незавершённых карточек.

## Доказательства

.dev/local/radar-completion-2026-11-10/: diagnosis.json, backend/frontend handoffs/manifests, review/final-review.md/release-approved.json, installed-image-tests.log, release-artifact-binding.json, runtime-verified.json, install-data-proof.json, terminal-progress.json, resume-validation.json, live-finished-qa.json. Private runtime/compose содержат секретные значения окружения; не публиковать их и не выводить в ответы.
