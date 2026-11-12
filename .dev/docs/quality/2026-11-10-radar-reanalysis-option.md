# Радар: выбор группы анализа и независимое автообновление

Установлено 10 ноября 2026 в C:/OSPanel/home/bervel.local. Работа в существующей Personal/core; Git использовался только для чтения. Текущий контракт: ../architecture/03-tender-radar.md.

## Результат

- «Обновить»: ручная квота и флажок «Проанализировать на основе измененных показателей». false — только подробности, полученные в данном logical refresh; true — все полные карточки, включая неизменившиеся и неактуальные. Показатели пересчитываются первыми в обоих случаях. Флажок не запрашивает повторно уже раскрытые source details.
- «Автообновление»: отдельные enabled/дни/время МСК и свой флажок. Сохранение не создаёт job, не вызывает источник/модель и доступно во время анализа. Ручная команда не меняет расписание, next_due или auto policy. Изначально оба флажка false, настройки независимы по источникам.
- Отдельные pending-команды, хранилища и восстановление; поздняя квитанция расписания не подменяет ручные/run поля. Старый ключ после нового сохранения не откатывает серверное состояние. Legacy refresh body replay сохраняется дословно.
- Новые операции v3; принятые v1/v2 не меняют алгоритм при продолжении. Атомарный details_loaded_refresh_id защищает свежую группу при сбое до checkpoint; готовые стадии того же logical refresh повторно не запускаются.

## Проверки

Backend: 235 PASS/3 ожидаемых skip в offline processing gate; актуальные API/storage33 PASS. Независимо: pure30 PASS, DB schedule9 PASS и retry2 PASS. Только отдельная существующая тестовая БД; pgvector prerequisite восстановлен в ней, не в production.

Frontend: TypeScript/Vite PASS, новых lint-замечаний в двух файлах нет. Основной gate45 PASS, дополнительный resume9 PASS после адаптации одного устаревшего combined-popup ожидания; точный legacy schedule replay сохранён. Независимые6 UI и2 visual PASS на1440/390. Preflight18 PASS: правильная конфигурация и17 отклонённых мутаций до установки.

Случай200старых+200новых проверен для обоих источников: false выбирает200, true400; false+0новых не вызывает подробную модель. TRUE не ограничивается только изменившимися fingerprint. Проверены active job, strict boolean, permissions, duplicate/concurrent key, A→B→replayA, source separation, marker gap и lease/explicit resume.

## Установка и данные

Runtime: bervel-radar-option:20261110-api/worker/frontend. Изменены только6 backend modules и2 frontend source; собраны316 frontend assets с исключением отложенного inspection/chat кода. Manifest0a47e3b326e63c86b4b3875621cd72f2301ba3c889bf8d9bb120f16375a05b26, composeee91c17b33bcf77f5c89efd54b4285ce87fecc2b968089106bfcdf8c21318921. Три фактических image IDs связаны approval; offline image hash/import и installed SHA проверены. Нет миграций; schema0027b прежняя. Остальные8 контейнеров и весь runtime shape/env/mount Source/ports сохранены. VPN/OSP не менялись.

Первый install guard корректно остановил установку: пользователь во время реализации возобновил старый v2 job857c2ee0-40cf-47de-b864-e23302d7faf7. Чтобы установить исправление и продолжить работу согласно прежнему разрешению пользователя, применены штатный Stop→полный drain→установка→explicit Resume. Terminal USER_STOPPED10:59:55Z наступил до lease expiry, все149 analyses+149 scores сохранены. После drain worker штатно вышел с ExitCode0 без SIGKILL.

Все32 рабочие таблицы побайтно по JSON-хешам совпали между послеостановочной и послеустановочной границами: changed_tables=[], active0. Исходный снимок задачи нельзя выдавать за неподвижный: до остановки пользовательский job закономерно менял radar-таблицы. Склад/настройки предприятия и остальные разделы не изменялись установкой.

Installed live GET-only QA:1440/390, два раздельных окна,0 JS errors/0 write requests. После resume отдельный live gate1440/390 подтвердил доступность автонастроек при активном анализе; ручная кнопка штатно недоступна. Новые платные full-refresh команды или реальные autosaves для тестирования флажков не запускались; продолжен уже авторизованный пользовательский анализ.

Продолжение: job6ea298fd-5fdb-4747-be30-f00e066e428e, тот же run61e2b011-d82f-4cdb-bef0-c584bb56890b/logical5f8..., v2 и frozen inputs. Изменились только resume_of и retry cycle; source attempts остались56/200. Все149 готовых карточек сохранили analysis/review/rating/cache/raw/tender SHA. После продолжения счётчик анализа вырос до150/400; выполнение идёт фоном. Новые флажки применяются к новым обновлениям, не переписывают параметры этого прохода.

Финальный resume-validation.json повторно подтвердил149 сохранённых карточек, прежние source attempts и running169/400 analyses+169/400 scores. Собственные временные QA-серверы5191/5192 закрыты с проверкой PID/commandline; рабочий анализ оставлен выполняться. Scoped git diff --check прошёл, Git index/history не изменялись.

## Артефакты

Корень .dev/local/radar-reanalysis-option-2026-11-10/: backend/frontend handoff и manifests; review/final-review.md, release-approved.json, stop-resume-addendum.md; release-artifact-binding.json, runtime-verified.json, install-data-proof.json, live-readonly-qa.json, live-active-qa.json, after-pause-proof.json, after-resume-proof.json, resumed-operation-proof.json. Private inspect/compose содержат конфигурацию окружения; их значения не выводились в ответы.
