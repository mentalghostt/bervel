# Завершение обновления радара при локальных ошибках

Режим: продолжение исходной АВТОНОМНО-задачи устойчивого радара. Корень C:/OSPanel/home/bervel.local, Personal/core;293 dirty paths, Git только чтение, незавершённых операций нет. Архитектура и .dev/AGENTS.md прочитаны. Не менять VPN/OSP/другие разделы. Все материалы внутри рабочего корня.

## Цель и подтверждённая причина

Пользователь: обновление повторно падает после ручного resume; должно доходить до конца без такого вмешательства. Последние два job6ea298... и8f88b... завершились MODEL_INVALID_OUTPUT «Позиция документа отсутствует в подтверждённом составе закупки». Saved run61e2...:377/400 analyses+ratings,400full,2774cards, search56/detail200 уже завершены, active0. Доказательство .dev/local/radar-completion-2026-11-10/diagnosis.json.

Локальный остаток analysis_failures после3попыток превращается в global raise; тот же список прекращает независимые повторы рейтингов. Primary ranking тоже поднимает локальный исчерпанный остаток до global. Это исправление существующей обработки очереди, без нового планировщика/сервиса, без ослабления document validators.

## Поведение и контракт

- Дойти до каждого элемента конечной очереди. Ошибки модели/валидности данных конкретного тендера сохраняются, ограниченно повторяются по прежним правилам (максимум3), после исчерпания остаются явными per-card ошибками и не прерывают остальные карточки или независимые rating retries.
- Готовые source inputs, первичные оценки, analyses/reviews/ratings и durable checkpoints сохраняются. Лимит10 полных lifecycles, ограниченные retries/cooldown, manual/auto независимость и v2/v3 выбор cohort не меняются. Ремонт terminal error propagation применяется и к продолжению текущегоv2 без подмены frozen inputs/версии числового анализа.
- Завершённый обход имеет state=complete/phase=complete; при остаточных локальных ошибках public data.completion_summary = {version:'profile-completion-v1',failed_tenders,primary_failed,detail_failed,analysis_failed,rating_failed,metrics_failed}. Все counts — целые>=0, failed_tenders — уникальные тендеры с неустранёнными ошибками именно текущего logical refresh. Старые ошибки вне cohort не считаются. Никаких выдуманных0score или невалидных извлечённых позиций.
- Card DTO получает optional refresh_warnings:[{stage:'primary'|'detail'|'analysis'|'rating'|'metrics',code:string,message:string}] — только актуальные сохранённые ошибки текущего прохода; frontend не читает private model envelopes и не создаёт клиентский POST/resume loop.
- UI: «Обновление завершено с замечаниями: N тендеров» при failed_tenders>0; видимые причины в деталях карточки. Старые DTO без summary/warnings по-прежнему допустимы; если optional поле передано, проверка формы строгая.
- Не превращать global storage/DB, повреждённый frozen snapshot/lease fence, USER_STOPPED и глобальные auth/source ошибки в успешный итог. В частности, убрать существующее случайное проглатывание ошибок сохранения внутри metrics/rating catch-all, чтобы новый warning-path не маскировал их. Не добавлять бесконечные платные повторы или слепой повтор неизвестного платного source request.
- Полное отсутствие внешнего доступа не может породить достоверный анализ: такие глобальные причины остаются видимыми. Текущая подтверждённая проблема — локальные validation errors; исправление должно завершать именно такие проходы без ручного resume.

## Вертикали

1. Backend — completion_backend_audit: before/SHA, RED тесты для преждевременной остановки, bounded primary/analysis/rating/metrics outcomes, honest summary/card warnings, persistence exception boundaries. Product scope: brief_ranking.py, profile_refresh.py, metrics_refresh.py, individual_rating.py и минимально необходимые named_runs.py/presentation.py/runs.py. Никаких migrations/app_setup/dependencies; расширение файлов предварительно сообщить root. Tests только own new test file; старые ожидания global failure менять только с проверкой strict non-profile legacy contract.
2. Frontend — completion_ui_audit: before/SHA; AiTenderRadarPage.tsx, aiRadarRunsApi.ts, radarRefreshStatus.ts; tests реального finish-with-warnings, malformed DTO, old complete/failed/active, popup warning, independent manual/auto. CSS менять только если требуется существующему предупреждению; новых интерфейсных сценариев не добавлять.
3. Независимый review — отдельный агент: собственные RED/GREEN контрпримеры, fail-one-first/late/unrelated-rating, all-fail, resume/crash, MAX3/10, storage failure propagation, exact current source/artifact binding. Product read-only.
4. Root: обеспечить интеграцию и release. Baseline scoped build должен совпасть с установленными316assets; не захватывать deferred inspection/chat и остальные dirty изменения. Отдельные новые образы поверх текущего option runtime. Approval связан с exact manifest+compose+actual3imageIDs. Установка только active0; никаких migrations/неявных source calls. Все32table hashes до/после установки должны совпасть.

## Проверки и приёмка

- [x] RED на currentкоде: локальный MODEL_INVALID_OUTPUT в одной ранней карточке не даёт завершить остальные; после исправления весьcohort settled, каждый failure<=3, валидные results сохранены.
- [x] Primary failure не блокирует details; analysis failure не отменяет отдельные rating retries; final summary не включает historical/out-of-cohort errors; all-fail не выдаётся за успешный анализ без предупреждения.
- [x] Currentv2 resume использует прежние frozen inputs/cohort/refreshid и не вызывает повторно search/details/готовые model stages. Прерывание после failed-card-save не теряет terminal outcome и не зацикливается.
- [x] SQLAlchemy/unexpected persistence errors/Stop/lease/global auth не подавляются per-card warning; modelunknown не повторяется безусловно.
- [x] Frontend strict optional DTO/formatter/popup, finished-with-warnings и прежние manual/auto/metrics/chat/resume regressions.
- [x] Independent review, сборка/установка только exact approved package; live GET-only UI и32tables unchanged до resume.
- [x] После установки штатно продолжить сохранённое обновление один раз с persistent command key; следить до terminal complete/complete-with-warnings, подтвердить сохранность ранее готовых377карточек и прежнихsource56/200. Реальный user-authorized model completion допускается, новые full refresh/search/details ради smoke не запускать.

Git commit/staging/push и session end не входят в задачу. Тестовая БД только bervel_core_stage31_test на уже работающем Docker PostgreSQL; DB тесты последовательно по lock. Tests via existing test image; без host Python и без включения OSP modules.

Итог: установлен finalpackage e1a9c430...; one resume8f558... завершился17:28:38МСК complete с23 analysis warnings после3попыток,377ready full-byte-equal/source56/200, active0. Подробности ../quality/2026-11-10-radar-completion.md.
