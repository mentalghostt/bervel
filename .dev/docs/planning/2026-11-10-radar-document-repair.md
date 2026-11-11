# Исправление документальных расхождений и адресный повтор23

Продолжение исходной АВТОНОМНО-задачи радара. Пользователь требует исправить три диагностированных класса и заново проанализировать ТОЛЬКО23 проблемные карточки с объяснением особенностей в AI-ревью. Root C:/OSPanel/home/bervel.local, Personal/core, HEAD12ebcdb0, dirty309 исходно; Git только чтение, незавершённых операций нет.

## Неизменяемые границы

- Текущий API/frontend — bervel-presales:20261110-*, schema0029_presales_pipeline. Сохранить пресейл/егоworker/настройки/0029; не возвращать старые radar-only images/compose или upgradeheads.
- Остальные377 ready карточек должны совпасть целиком по JSON SHA. Склад, companysnapshot67a/3076товаров, originals, настройки, searchpages, quotas/counters56/200 и source raw23 не меняются.
- Не менять VPN/OSP, не включать модули OSP. Уже работающий PostgreSQL допускает read-only baseline; новые файлы только taskroot .dev/local/radar-document-repair-2026-11-10. Разработанное приложение/адресный worker можно запустить для прямо запрошенного анализа23.
- Исходные23 берутся из .dev/local/radar-document-diagnosis-2026-11-10/inputs.private.json failures; фактическое текущее состояние проверяется перед командой. Диагноз findings.md и offlineprobes обязательны к чтению.

## Решение

1. Новая documents.RECONCILED_TEXT_PROMPT_VERSION=document-extraction-v5. Старые frozenv1–v4 не переписываются. В v5 обработка завершается с явными discrepancies/verification_notes/rejected_requirements; отсутствие полного состава не называется malformed model response.
2. API известные positions/цены остаются подтверждённой числовой основой. Недостающие строки документа сохраняются как document_position_unassigned с name/quantity/unit/evidence, без выдуманного lot или price и без подмены source raw. Анализ подтверждённой части выполняется, неизвестные общий объём/прибыль/выполнимость остаются неизвестными. composition_confirmed=false и limitations ограничивают выводы.
3. Короткая цитата принимается лишь когда name/value/unit доказаны в общем собственном блоке выбранной позиции; повтор такой же цитаты в другом блоке не делает правильный факт ошибкой. Смешивание разных SKU/документов/характеристик запрещено; blindall→any не применять.
4. Вводная сводка, противоречащая поимённым строкам/API, учитывается как source_summary_conflict. Её параметры нельзя переносить на похожий SKU. Неподтверждённые предложения модели остаются отклонёнными фактами/замечаниями, не изменяют канонические позиции и не обрывают весь анализ.
5. AI-ревью получает структурированные особенности и обязательный фактический абзац. Для17 явно «Состав закупки в карточке тендера и документах различается…», указать отсутствующий APIлот/неподтверждённую часть. Для4 указать противоречие вводной сводки и актуального поимённого состава. Для2 указать подтверждённую контекстом привязку повторяющихся характеристик (без ложного утверждения о различии состава). Индивидуальная оценка видит неопределённость в product_match при прежних четырёх критериях; нет фиктивных0/100.
6. Simulator header строится по FINALcoveragepositions после unique assortment. IDs/RNGstream/quotas/pagination/цены/лотструктура и намеренные empty_lot сценарии прежние. Исторические raw23 не перезаписываются; новые документы перестают противоречить сами себе.

## Адресная операция

Существующий /runs/.../reanalyze создаёт дочерний selection-v1 run и не подходит. Нужна одна durable операция в исходном profile run: jobkind profile_reanalyze_selected, строгий allowlist analysis_ids, command_key, закреплённый существующий settingssnapshot67a и локальные v5docs/reconciledreview версии. Current_job_id используется для существующих lease/Stop fences; отдельный selected_reanalysis_checkpoint для прогресса/resume. Run-wide старые stage_versions не менять как будто все377 пересчитаны.

POST /profiles/{source}/reanalyze-selected {command_key,analysis_ids}. Strict UUID list, непустой, без дублей; все карточки должны принадлежать текущему scopedprofile run и иметь сохранённые fullinputs. Canonical selection входит в idempotency identity; другойscope/другиеIDs/другойsnapshot с темжеключом конфликтует. Enqueue атомарен под profile/run lock и analysislane guard; permissions как process:start/read/artifact:read. Никаких source/search/detail/providerquota calls или общего metrics prelude.

Write fence проверяет allowlist на сохранении, не только в цикле. Считаются только23. Успешный target очищает только собственный старый completionerror; по завершении parentcheckpoint analysed/ratedids пополняются успешными23 и пересчитывается общий summary. Готовые этапы selectedbatch не повторяются при resume/lease recovery. MAX10lifecycles/MAX3model retries/cooldown, неизвестный оплаченный outcome не повторяется слепо. ЯвныйStop/globalstorage/auth остаются видимыми. Не менять last_refresh_at/next_due/manualauto настройки как будто это новый поиск.

## Вертикали и владельцы

- repair_documents: documents.py, completeness.py, review.py, individual_rating.py; own contracttests и offline23 harness. Before/SHA, RED→GREEN, frozenmanifest. Константы/поля сообщить targetowner до интеграции. No named_runs/profile/simulator/frontend edits.
- document_inventory_diagnosis: .dev/scripts/kontur_simulator.py +ownnew consistencytest, before/SHA, structural-IDdigest/emptylot invariants. Четыре строки переноса summary после окончательныхpositions; no new RNGcalls.
- document_quote_diagnosis: адресный API/service/coordinator/fencedrepository и named_runs stageversion/reviewfingerprint wiring. Own tests. Допустим новый selected_reanalysis.py как узкий coordinator; без migrations/dependencies/frontend. Scope точныхфайлов сообщить root до правок. Newnormalrefresh получает v5, alreadyacceptedv4 операции не подменяются.
- Root: интеграция, независимый review после freeze через освободившегося владельца simulator, baseline/final package actualSHA; preserve currentpresalesbase. Тестовые серверы не включать через OSP. Повтор не должен запустить общийworker/scheduler и посторонние queuedjobs: предпочтителен адресный one-shot analysisworker после проверки очереди, так как постоянный radarworker был остановлен внешне.

## Проверки и фактическая приёмка

- [ ] Все17partialcases дают analysis/review/individualrating с честно ограниченным scope; unassigned rows retained, no guessedlot/price, sourceAPI поля unchanged.
- [ ] Correctquote oracle2 принимается, crossproduct/crossdocument/name-value-unit mixing отклоняется. FourcapturedwrongAIanswers не меняют размеры/марки фактических SKU; конфликт виден в review.
- [ ] Future simulator summary содержит только итоговые позиции; закреплённые5000IDs/quotas/emptylot/весьwire состав unchanged.
- [ ] Batch isolation, strictAPI/auth/idempotency, foreignIDs/377write forbidden, crashcheckpointgap/Stop/lease/resume, bounded10/3, no source/wholeprofilemetrics calls.
- [ ] Финальные frozen source/package/image review, appropriate regression gates иactualimage offline tests. API базе0029 не выполнять migrations.
- [ ] Непосредственно до команды сохранены все377cardJSONSHA ипрочие данные; one persistentcommand ровно23target IDs. Дождаться23/23 analysis+review+individualrating, не просто скрытых ошибок. Проверить reviewnotes иpendingunknown fields всех23.
- [ ] После live повтора377ready cardJSON ивсе rawinputs/searchpages/settings/warehouse unchanged; source56/200; APIread/browser showsactualnewreviewnotes. Записать точный итог/ограничения вqualityreport.

Если есть неразрешимая недостающая информация, не выдумывать её ради статуса: анализ выполняется по подтверждённой части с объяснением и корректной неопределённостью.
