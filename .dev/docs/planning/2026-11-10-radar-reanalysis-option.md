# Выбор повторного анализа подробных карточек

Цель: флажок «Проанализировать на основе измененных показателей» с точной логикой пользователя: включён — анализировать все сохранённые подробные тендеры; выключен — только тендеры, подробности которых получены в данном обновлении. Дополнение пользователя: ручное обновление и настройки автообновления независимы и открываются разными кнопками.

## Контракт и границы

- Рабочий корень `C:/OSPanel/home/bervel.local`, Personal/core, общий dirty checkout. Git только чтение; VPN, OSP и посторонние модули не меняются. До правок проверены root/remote/branch/dirty и отсутствие незавершённых Git-операций.
- Продолжение исправления механики радара. Начальное значение обоих флажков false. Ручной режим хранится как profile.reanalyze_existing, автоматический — profile.auto_reanalyze_existing; они не наследуются друг от друга. Симуляция и боевой профиль также имеют независимые настройки.
- Новый optional strict boolean `reanalyze_existing` в POST `/profiles/{source}/refresh`; frontend новых команд всегда отправляет true/false. Публичный профиль возвращает сохранённое значение; старые ответы без поля читаются как default false.
- Значение закрепляется в operation при принятии команды. Resume/lease recovery не подменяют его текущими настройками профиля. Повтор command key с другим явно указанным режимом — конфликт, повтор с тем же — прежняя квитанция.
- Старые pending frontend-команды без поля не изменяются при replay. HTTP boundary различает отсутствие поля и явно переданный false, чтобы старые `profile_request` совпадали дословно.
- Новые операции получают `refresh_version='incremental-profile-v3'`; v1/v2 уже принятых операций сохраняют прежний алгоритм. Ни GET, ни установка не переводят старый запуск в новый режим.

## Независимое автообновление

- «Обновить» открывает ручной попап: квота подробных карточек, ручной флажок и кнопка запуска. Расписания/переключателя автообновления в нём больше нет. Новое manual body не содержит schedule. Manual меняет только свою настройку и запускает свою operation; schedule/next_due/auto policy/autoactor остаются прежними.
- «Автообновление» — отдельная кнопка и отдельный попап: enabled, каждые n дней, время МСК, свой флажок анализа и «Сохранить». Existing «Обновить показатели» сохраняется.
- `POST /profiles/{source}/schedule {command_key,schedule,reanalyze_existing}` возвращает RadarProfile. Это settings-only сохранение:0source/LLM/jobs, не запускает обновление и не меняет manual quota/pref. Доступно и во время анализа. Scheduler при следующем положенном запуске берёт auto_reanalyze_existing и доступный максимум квот, не чаще1auto/day.
- Авторизация settings:write/process:read; включение расписания сохраняет только разрешённого actor с process:start/settings:read/artifact:read. Применяется existing next_due и last_auto_day, без сброса дневной защиты при редактировании.
- Save+receipt атомарны с существующим core CommandReceipts в инфраструктуре, operation ai_radar.profile_schedule:<source>. Новая таблица не нужна. Старый ключ после более нового сохранения возвращает прежнюю квитанцию, но не откатывает конфигурацию. Different payload/same key — конфликт.
- Autosave имеет собственные busy/pending/localStorage/replay. Неизвестный autosave не запрещает manual/metrics, активный manual job не запрещает редактировать auto. Late receipt не подменяет unrelated manual/run поля; после него запрашивается текущий профиль.
- Legacy manual body.schedule допускается для точного replay прежнего profile_request, но новая v3 manual operation не применяет его как изменение расписания. Операции, принятые прежним кодом, остаются со своими исходными параметрами.

## Отбор карточек

Первый локальный пересчёт покрытия/маржи существующих анализов сохраняется при обоих значениях. Источник по-прежнему ищет briefs и получает только отсутствующие подробности в рамках квоты; флажок не расходует повторно source details уже раскрытых тендеров.

При успешном получении подробностей v3 атомарно с raw/tender/details_loaded сохраняет `details_loaded_refresh_id=logical_refresh_id`. По этому маркеру определяется свежая группа; crash после записи карточки, но до checkpoint не теряет её из группы. Resume продолжает тот же logical refresh, готовые расчёты/score не повторяются.

- false: подробные стадии и индивидуальная оценка только для сохранённых карточек с маркером текущего logical refresh. Изменение склада/даты не добавляет старые карточки в эту группу. Старые результаты не удаляются; при изменении показателей сохраняется их прежний score с существующей меткой устарелости.
- true: подробные стадии для всех карточек с полной информацией (включая ранее сохранённые), даже если их предыдущие input fingerprints совпадают. Успешные стадии данного logical refresh не повторяются при resume. Включённый флажок выбирает все подробные карточки, не только subset с изменёнными значениями; отдельного changed-only режима нет.
- Счётчики analysis/rating показывают выбранную группу: 200 при false для случая200старых+200новых,400 при true. Общее число карточек базы остаётся отдельным показателем.
- Документы и подтверждённое техническое соответствие могут использовать готовые неизменившиеся стадии; новый проход должен актуализировать расчёт/ревью/score выбранных карточек. Existing bounded10, durable stage cache, max3 retry/cooldown, Stop/lease fences и unknown paid source reservations сохраняются.

## Вертикали выполнения

1. Backend: `profile_api.py`, `profiles.py`, `profile_refresh.py`, узкая проверка incremental версии в `named_runs.py`/при необходимости `metrics_refresh.py`, инфраструктурный `profiles.py` для отдельной конфигурации расписания и receipts. Без миграций/новых зависимостей/сервисов. Before/SHA до первой правки в `.dev/local/radar-reanalysis-option-2026-11-10/backend-before/`.
2. Frontend: `AiTenderRadarPage.tsx`, `radarProfilesApi.ts`; существующие checkbox стили. Ровно одна новая подпись, без новых технических пояснений в пользовательском попапе. Before/SHA в том же task root `frontend-before/`.
3. Независимый review: контракт обоих значений, replay старой команды и fake-provider processing cohort. Product read-only; отдельные test/report files.
4. Root: интеграция, узкая сборка с сохранением deferred TestChat/chatApi baseline, проверка, установка в безопасной границе и браузерная проверка без реального платного full refresh.

## Проверки

- [x] false:200старых с прежним stock/day +200новых → только200новых model lifecycles; старыеanalysis/review/individual envelopes не изменены.
- [x] true:те же400, в том числе неизменившиеся full inputs →400 выбранных анализов; нет скрытого changed-only отбора.
- [x] false+0новых/квотаdetails0 →0подробных model calls, локальные показатели всё ещё обновляются.
- [x] marker сохранён с raw; resume после crash/checkpoint gap не теряет свежую карточку и не повторяет paid source; completed analysis/rating не повторяется.
- [x] legacyv2 продолжается по старым условиям; старый command replay без поля возвращает исходную operation/version/receipt.
- [x] strict boolean HTTP, permissions, idempotency conflict, профиль/auto сохранение, два источника.
- [x] Два независимых попапа; manual body без schedule и без изменения auto полей; autosave не запускает jobs/source/model и не меняет manual pref/quota, сохраняется при active job.
- [x] Разные manual/auto flags и два источника; scheduled operation берёт только auto policy; max1auto/day сохраняется при редактировании.
- [x] Autosave idempotency/late reply/old-key-after-new-save не откатывает серверную конфигурацию и не подменяет текущий manual/run state; неизвестная auto команда восстанавливается отдельно.
- [x] UI default false/true selection, payload/localStorage/reload/unknown exact retry, profile switching, disabled busy guards, quota/schedule и metrics-only остаются рабочими.
- [x] TypeScript/build/lint, существующие profile/progress/resume/metrics/chat и source/retry/individual regressions по затронутому scope.
- [x] Независимое review и exact manifest/installed SHA. Нет изменений склада/настроек предприятия/существующих analyses из-за самой установки.

Тесты с БД только на существующей `bervel_core_stage31_test`, строго последовательно с согласованным lock между исполнителем и reviewer. Все артефакты внутри task root. Runtime меняет только root. Запуск400 принят до нового параметра и не получает изменённые параметры. Runtime-уточнение при установке: пользователь самостоятельно возобновил его во время реализации; поэтому потребовались штатный Stop→drain→установка→explicitResume с сохранением149 готовых анализов/оценок и прежнейv2. Это продолжение того же logical refresh, не новый full refresh. Итог и доказательства: ../quality/2026-11-10-radar-reanalysis-option.md.

