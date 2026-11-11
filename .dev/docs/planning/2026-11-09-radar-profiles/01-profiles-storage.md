# Вертикаль 1 — профиль и хранилище

Два фиксированных источника: `test_api` — «Симуляция контур.закупки», `kontur_api` — «контур.закупки». Нет CRUD произвольных профилей. Область данных определяется actor.data_scope. Раздельные ключевые слова, расписание и база тендеров. Компания/склад общие, читаются из последнего сохранённого профиля предприятия при обновлении.

## Данные

`ai_radar_profiles`: PK(data_scope,source), unique(run_id); UUID постоянного контейнера анализа, JSONB конфигурации/расписания/квитанции, timestamp. Настройки ключевых слов: `keyword_mode: auto|manual`, `keywords: string[]` (в авто пустой массив). Ручные фразы непустые, каждая до 500 знаков, без управляющих символов; удалить дубли. Расписание: `{enabled:boolean,every_days:positive integer,time:'HH:MM',timezone:'Europe/Moscow'}`. По умолчанию выключено, 1 день, 09:00 МСК.

Контейнер — существующая `ai_radar_runs` с `data.profile_source=source`, именем профиля, `current_job_id`, обновляемым snapshot склада. Не создавать второй контейнер при refresh. Карточка — `ai_radar_analyses` с unique(run_id,candidate_id) и постоянным analysis_id: insert новых, update существующих. Отчёты вопросов остаются адресными по analysis_id. Не смешивать карточки двух источников при одинаковом Id. Отдельные технические jobs/receipts нужны для очереди, но не образуют пользовательскую историю.

Новая миграция `0027b_radar_profiles` — отдельная ветвь от 0027a (соседняя inspection0028 не требуется). Если нужны два head в полном дереве тестов, `migrate` уже использует heads либо root согласует узкую корректную адаптацию тестового запуска. Установку выполнять целевой ревизией. От старых данных не мигрировать мусор в профили: отдельная явная purge-команда root перед запуском профилей.

## Публичный API

Корень `/api/v0.1/ai-tender-radar/profiles`.

- GET `` → `{profiles: Profile[]}`. Ровно два профиля, чтение не пишет БД.
- GET `/{source}` → `{profile: Profile, run: Run|null}`. При ещё не созданном контейнере run=null.
- PUT `/{source}/keywords` body `{keyword_mode,keywords}` → Profile. Требуются settings:write и process:read; нет изменения настроек предприятия.
- POST `/{source}/refresh` body `{command_key,detail_max:number|null,schedule?:Schedule}` → `{job_id,state,run_id}`. `detail_max=0` допустим; null означает весь доступный максимум деталей. `schedule` сохраняется атомарно с принятием команды, не при отклонённой команде. Требуются process:start, artifact:read, settings:read. Квоты в UI берутся из последнего известного состояния; повтор POST идентичен по ключу.

Profile = `{source,name,run_id:string|null,keyword_mode,keywords,schedule,available:boolean,last_refresh_at:string|null,next_refresh_at:string|null,quotas?:{search:number,detail:number}}`. available показывает наличие настроенного адаптера, не скрывая профиль. Источник выбирается из разрешённого enum, чужой data_scope/actor_id из тела не принимаются. Конфигурация расписания строго валидируется сервером.

Operation очереди = `{kind:'profile_refresh',profile_source,run_id,settings_snapshot_id,keyword_mode,keywords,detail_max,search_fingerprint,...}`. Все входы конкретной команды зафиксированы; повтор ключа проверяется ДО чтения нового snapshot. `run_id` постоянен, job_id меняется. Поля operation не зависят от момента replay.

## Сигнатуры для соседних вертикалей

`RadarProfiles(pipeline, repository)` назначается `pipeline.profiles`.

- `.list(actor) -> dict`, `.read(actor,source) -> dict`, `.save_keywords(actor,source,keyword_mode,keywords) -> dict`.
- `.refresh(actor,source,command_key,detail_max,schedule=None) -> job`.
- `.enqueue_due(now:datetime|None=None) -> int`: сколько принято jobs; не создавать отдельный сервис/cron.

`PostgresProfileRepository(engine)`:

- `.upsert_card(run_id:UUID,candidate_id:str,data:dict)->dict`: полная актуальная запись, stable analysis_id, уникальность candidate внутри run; инфраструктурный слой не принимает решения актуальности.
- `.set_run_snapshot(scope:str,run_id:UUID,snapshot_id:UUID)->None`.
- `.finish_refresh(scope:str,source:str,job_id:UUID,error:dict|None=None)->None`.
- Остальные методы создания/блокировки профиля/расписания выбирает storage, но внешние контракты выше не меняет.

## Расписание

Серверное время — aware UTC, выбор времени/календарный день — Europe/Moscow. Каждые n дней в выбранное время, не чаще одного автоматического старта на профиль за московский день; пропущенные интервалы не проигрывать пачкой. Атомарная блокировка строки + command_key, производный от профиля и due-date, защищают от двух tick/worker. Занятая analysis-очередь — отложить до следующего tick, не потерять запуск. После фактического принятия перенести next_due минимум на n дней и не давать изменение времени включить второй автостарт в тот же день. Ручные обновления разрешены отдельно. Авто передаёт detail_max=null, поиск — до всего доступного лимита (не более 200 запросов). При отсутствии склада/доступа ошибка видима, нет тихого успеха и бесконечного внешнего retry.

## Приёмка

Два профиля из чистой БД, изоляция scope/источника, replay после смены snapshot, валидация 0/null/отрицательного, двойной запрос, scheduler при busy/restart/two ticks/изменении расписания/МСК/n>1. При любом отказе enqueue предыдущие данные профиля читаемы.
