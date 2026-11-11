# Audit: full refresh, metrics-only и parity источников

Статус: исследование для единого плана, не разрешение реализации/выпуска. Корень Personal `C:/OSPanel/home/bervel.local`, ветка `core`; Git только чтение. Прочитаны `C:/Users/Evan/AI/ARCHITECTURE.md` и `.dev/AGENTS.md`. Продукт/runtime/БД не изменялись; production анализ, reset квот и миграции не запускались. Новые тесты в этом исследовании не запускались: ниже указано существующее покрытие и рекомендуемые проверки.

## Что фактически работает сейчас

`profiles.py:_refresh` закрепляет текущий сохранённый company snapshot и выбранный им stock artifact/hash при принятии команды. Resume использует те же входы. «Последний склад» здесь означает склад в сохранённом профиле, не последний произвольно загруженный CSV.

`profile_refresh.py` выполняет limits → keywords/search → AI priority → details → `_analyze` для всех сохранённых full cards нового logical refresh → individual score. Пересчёта покрытия/маржи до source I/O нет. Если source недоступен, локальные показатели не обновляются. Новый logical refresh повторно оценивает первичный приоритет старых карточек без деталей: reuse требует `priority_refresh_id == refresh_id`. Это отличается от требования «AI priority только новым».

Отдельной metrics-only команды нет. GET профиля пересчитывает актуальность и отображает сохранённый анализ; latest stock не читает. `_analyze` не подходит для metrics-only: внутри документы, matching и review модели. Есть локальные `restore_matching → refresh_saved_analysis → calculation/production/scoring`, но их текущий контракт — исторические проверенные данные.

## Parity test_api / kontur_api по исходникам

| Область | Общее | Установленное отличие / граница вывода |
|---|---|---|
| Composition | Один `KonturClient`, `normalize_tender`, общий profile scenario | `test_api` — локальный HTTP с test key; `kontur_api` — явный configured adapter, реальный ключ. Нет автоматического fallback источника. |
| Search | `Text`, `PurchaseStatuses=[1,4]`, фиксированный временной диапазон, page 0..199, проверка 50 items/page | Это контракт нашего клиента. Проверки simulator/mock transport не доказывают семантику и полноту настоящего API. |
| Budgets | Раздельные search/purchase; reservations до I/O | Unlimited simulator возвращает `[]` и получает локальный fallback 200/200; real пустые limits отвергаются. Mirrored simulator учитывает provider remaining и локальный spent; reset запрещён. |
| Detail | Нормализация ID, Objects/JointObjects, характеристики, цены, ограничения | Raw details повторно не запрашиваются для уже loaded карты. Изменившийся brief обновляет summary/deadline, не технические строки или документы. |
| Documents | Общий `documents.enrich_tender`, evidence/quote/schema checks, затем matching/calculation/review | Только simulated fetch скачивает до пяти локальных UTF-8 Docs (same-origin, 256 KiB/document) и предварительно парсит явные labelled facts. Real fetch оставляет Docs links без текста; общий этап возвращает `DOCUMENTS_NOT_FULLY_READ`. |
| Request rate | Один `KonturRequestGate` для configured clients внутри процесса | API и worker — разные процессы; общей распределённой rate reservation нет. Фактическая provider квота может изменяться внешними потребителями. |
| Errors | Shared transport codes, input validation, durable source unknown markers | Таймаут после отправки не доказывает отсутствие оплаты. Runtime API behaviour в этом audit не проверялось. |

### Документальное отличие — не обход полного анализа

`completeness.enrich_from_documents()` в simulator adapter извлекает явно размеченные даты/расходы с цитатами и сохраняет полные тексты. Это не объявляет соответствие продукции, не заменяет matching и не выставляет балл. `_analyze` затем всё равно проходит общий `documents.enrich_tender`, проверку требований, расчёт и review. Однако это дополнительный simulator-only preparse, поэтому равное качество результата источников не следует из общей функции анализа.

Минимум для данного плана: одинаковый сохранённый normalized input с текстами должен проходить одну общую pipeline; отсутствие текста должно одинаково оставаться unknown. Не добавлять в metrics-only скачивание реальных вложений, OCR или произвольный сетевой fetch. Если требуется убрать именно preparse-асимметрию, согласовать малое versioned изменение: либо общий deterministic parser для уже имеющихся текстов, либо отсутствие simulator-only terms prefill в новом режиме. Старые сохранённые документы и версии не переписывать. Получение настоящих вложений — отдельная задача с проверенным транспортным контрактом.

## Реальные gaps и решения до кода

1. **Локальные цифры и техсоответствие — разные основания.** `restore_matching()` не ищет новые SKU; прежнее `NO_COMPATIBLE_SKU` сохраняется как подтверждённое отсутствие. При смене каталога это уже не доказательство. Старый положительный match тоже покрывает только проверенные SKU, не все новые альтернативы. Рекомендуется metrics-only на сохранённом доказательстве с повторной проверкой его полей; новые/изменённые товары отмечать непроверенными, старое отрицание инвалидировать при изменении catalog fingerprint. Не выдавать такой процент за полное покрытие актуальным каталогом. Детерминированное подтверждение новых SKU — отдельный согласуемый алгоритм.
2. **Время и provenance.** `present_card()` намеренно использует прежний `evaluated_at` и production version. Это историческая projection, не актуальный пересчёт. Нужны отдельные время/версия входа текущих метрик, при сохранении исходных analysis ID, frozen settings/stock, raw, review и scores/history. Новая дата расчёта не должна притворяться новой LLM-проверкой.
3. **Оценка ИИ после metrics-only.** При изменении покрытия/маржи/сроков сохранённый score не соответствует новым facts. Рекомендуется явно stale с датой прежней оценки; 0 модельных вызовов, никакого фиктивного нового балла. Что отображать из старого балла рядом со stale — решение интерфейса до реализации.
4. **Что означает «новая карточка».** Нужно выбрать: впервые увиденный source ID либо карточка без законченного primary score. Рекомендуется сохранять успешный старый priority, продолжать незавершённые по явной retry/resume политике; новый ID получает priority. Изменение brief/company само по себе не должно скрыто запускать массовый rerank. Если профиль изменился, старый priority должен иметь понятную версию/дату.
5. **Кого полноценно анализирует full refresh после локального этапа.** Только новые/неразобранные карты либо также старые карты с новыми matching inputs? Это влияет на платные вызовы и актуальность новых SKU. Нельзя одновременно обещать отсутствие повторного LLM и полную проверку нового каталога.
6. **Снимок latest.** Рекомендуется current saved company+selected stock фиксировать один раз при enqueue. Изменение настроек во время выполнения относится к следующей команде; resume не подменяет входы. Stock artifact hash проверяется до локальных расчётов. Чтение не переключает snapshot.
7. **Параллельные команды.** Рекомендуется прежняя analysis queue/один активный job на scope; metrics-only и full не пишут одни карты параллельно. Нужны отдельный kind/checkpoint/receipt и resume нужного сценария, без перезаписи source reservations. Новые таблицы для этого не требуются по текущей модели JSON.
8. **Источник не является стабильным snapshot по доказанному контракту.** Сейчас constant TotalCount + unique IDs + полный обход <10000 позволяют local `scope_complete`; при изменяющейся выдаче с тем же количеством это не доказательство отсутствия старой карты. Source missing должно означать наблюдение в конкретной области/времени поиска, а не доказанную отмену. Политика снятия старых карт при таком отсутствии требует явного решения.

Дополнительные границы: отсутствие expiry flags в production v3/v4 не равно доказанной свежести — `_fresh` применяется к legacy rules, а новый режим использует сохранённые факты профиля. Не менять это молча. Готовый full analysis/rating переживает resume; внутрикартовые matching/review model stages до полного commit не имеют отдельного durable результата. Повтор одинакового request_id runner не гарантирует дедупликацию: общий `/v1/generate` исполняет запрос заново.

## Матрица edge cases и проверок

| Случай | Критерий нового сценария | Существующее покрытие / необходимое дополнение |
|---|---|---|
| Full source недоступен сразу | Локальный этап закончен и виден; source error не откатывает его | Новая regression: throw на первом `read_limits/search`, проверить новые metrics и прежний анализ |
| Metrics-only, source и model запрещены | 0 source/LLM/reset calls, включая keywords/review/rating | Новый fail-on-call fake для каждого внешнего метода |
| Stock quantity/cost changed | Новые coverage/margin по подтверждённым соответствиям | calculation/presentation tests + новый latest snapshot case |
| Добавлен/удалён/изменён SKU | Старый proof перепроверен, новый не объявляется подтверждённым | Новый matrix: positive proof invalidation, old negative → unknown, incomplete scope |
| Stock file отсутствует/не тот hash | Явная ошибка, старые данные сохранены | Добавить metrics-only integrity/access/NOT_FOUND tests |
| Settings меняются после enqueue | Одна закреплённая версия на весь command/resume | Существующий profile command replay + новый metrics checkpoint case |
| Старый primary с готовым score | Не повторять AI priority на full refresh | Новый regression с двумя logical refresh и counter model calls |
| Старый primary без score / изменённый brief | Поведение соответствует выбранному определению new | Новые cases после решения fork 4 |
| New ID совпал в разных источниках | Не смешивать test/kontur bases | Разные profile run ID; добавить одинаковые IDs для обеих баз |
| 0 detail quota при search quota >0 | Briefs обновляются, детали не оплачиваются | `test_provider_zero_details_does_not_restrict_available_search_budget` |
| Unlimited / finite / mirrored | Локальный fallback отдельно от provider quota; reset не участвует | simulator/connection/reset suites; проверить одинаковый command без режима reset |
| 9999/10000/10001, duplicate IDs, changing total | Нет ложного mass disappearance при явной неполноте | `test_result_cap_never_creates_false_disappearances`, incomplete traversal tests; новый same-count reorder fixture |
| Unknown search outcome | Нет автоматического повторного paid call; archived page восстанавливается | profile unknown search + `test_received_search_page_recovers_without_another_paid_http` |
| Unknown details, pending до crash | Не refetch после restart/new refresh | unknown detail + durable pending detail tests |
| 404/closed → новый открытый brief | Сохранить карту; повтор только по явному допустимому состоянию | profiles_review restore-open tests; cancelled-during-details test |
| Planned/expired/naive deadline, clock advance | MSK actuality, без source/LLM; не подменять delivery deadline | profile_policy + expired planned + individual cutoff tests |
| Crash после local card commit до checkpoint | Не терять цифры и не создавать новую версию команды | Новая local-stage crash/resume regression по существующему recovery шаблону |
| Stop / lost lease / owner replaced / wrong scope | Нет новых dispatch; stale writes отвергаются, принятую работу сохранить | progress_recovery + individual operations fencing; распространить на новый kind |
| Transient AI error / auth / quota / unknown | Durable 3-attempt policy, provider cooldown; terminal не retry | priority_retry / individual_rating suites; не переносить эти вызовы в metrics-only |
| Same command twice / unknown POST response | Тот же receipt и frozen input; изменённый mode → conflict | Новый replay key matrix full vs metrics-only, UI recovery storage |
| Docs text есть/нет/частично/с конфликтом | Одинаковый shared extraction; unread → unknown | ai_radar_documents/document_contract + новый source parity harness |
| Metrics changed, old individual score exists | Stale/history; не 0 и не выдавать старый score за новый | Новый GET/reload/browser test; existing saved-rating stale tests |

## Рекомендуемый release gate после утверждения плана

1. Pure stage gate: latest stock maths + proof invalidation + frozen time/provenance + 0 внешних вызовов metrics-only. Отдельно доказать порядок local commit до первого source call и отсутствие повторного primary scoring.
2. Isolated PostgreSQL/API: command kinds, permissions, idempotency, resume, Stop/lease/replaced owner, crash boundaries, сохранность analysis IDs/raw/review/scores/source reservations. Один последовательный DB gate, не рабочая БД.
3. Source contract parity: один набор synthetic HTTP payloads через test и explicit official mock adapter, одинаковая нормализация/ошибки/quota accounting. Дополнительно real HTTP только локального simulator. Не называть это проверкой настоящего API.
4. Existing regressions: profile refresh/storage/API/policy, progress recovery, priority retry, individual/comparative rating, production/calculation/presentation, documents, source normalization. Старые stage versions должны продолжать прежний сценарий.
5. Frontend: оба действия и их разные затраты/результаты понятны; первичные карточки не теряются; новый local результат виден после reload при source failure; stale score и source freshness объяснены; повтор unknown команды без нового key; desktop/mobile/0 score.
6. Перед отдельным выпуском: exact source/runtime baseline, узкий manifest, сохранность чужих файлов и данных. Без автоматического real API smoke/LLM/reset/миграций. Внешний smoke с конкретным бюджетом — только если отдельно выбран пользователем.

Основные файлы реализации, если план утверждён: `profile_refresh.py`, новый узкий local calculation scenario либо переиспользуемая pure функция, `matching.py`/`calculation.py` (контракт saved proof), `profiles.py`, `profile_api.py`, `infrastructure/profiles.py`, `runs.py` (read/provenance/resume), composition только при реальной необходимости, frontend profile command/status/metrics. Общие HTTP adapters изменять только по выбранному parity контракту; сетевой document loader не добавлять попутно.
