# «Проверка» — реализация и проверка 29 октября 2026

Рабочий корень C:/OSPanel/home/bervel.local, Personal/core. Пользователь утвердил архитектуру и разрешил автономную реализацию. Раздел установлен по адресу http://127.0.0.1:5173/#/inspection. Ограничены только инструменты ИИ этого раздела; общий вход и возможности остальных страниц сохранены.

## Результат

Переиспользуются интерфейс, очередь, история и квитанции чатов. Миграция0015 добавила неизменяемый mode=assistant/inspection; прежние чаты получили assistant без переписывания текстов и результатов. Новый API namespace, browser storage и выбор очереди разделены по режиму. Дополнительный worker-inspection использует тот же Python механизм очереди и не блокирует обычный chat worker.

Отдельный inspection-reader предоставляет только именованные операции чтения. Пять ro-корней project/objects/codex/embeddings/postgres, PostgreSQL роль с SELECT и READ ONLY, Docker GET-адаптер с проверкой project label. Внешнего reader-порта и произвольного passthrough/SQL/exec нет. File writers, settings mutations и запуск радара не входят в зависимости InspectionActions. Единственное предметное изменение — создание нового внутреннего тикета через публичный TicketService. История и служебные квитанции сохраняются штатно.

Секреты очищаются до paging, ограничения model context, вызова модели и записи результатов. Чтение истории и create/send replay фильтруются повторно. Маскируются известные credentials, ENV/JSON/YAML/XML assignments, URL passwords, PEM, Cookie/Authorization и opaque credential-файлы; in-root symlink не скрывает настоящий sensitive path. Directory handles/O_NOFOLLOW закрывают промежуточную подмену пути.

## Проверки

- Полный Python на финальных исходниках:650passed,3предупреждения отражения PostgreSQL/SQLAlchemy;0ошибок.
- До последней точечной правки авторизации полный набор:644passed. После правки:67passed, затем52passed на workflow/chat/storage; независимые59passed на inspection/worker роли.
- Node:79passed, включая настоящий CLI/SDK с локальным контрольным Responses server. Это техническая проверка протокола, отдельно от живого MOX ниже.
- Браузерный общий набор:109passed; отдельные inspection:5passed. Build TypeScript/Vite и oxlint прошли при сборке frontend image.
- Живые desktop1440/mobile390: установленный интерфейс, правильная навигация, доступный ввод, отсутствие горизонтального выхода и page errors. Проверены реальные история/ответ/карточка тикета.
- Независимое ревью закрыло R1–R3: вложенные секреты и opaque файлы; безопасный redaction placeholder в настоящем TicketService без допуска credential suffix; фильтрация старых результатов/истории/replay и до context projection. Финальное узкое ревью новой авторизации не нашло открытых actionable findings.

Первый общий прогон выявил старый test image без tiktoken и ro mount для теста, создающего temporary directory; пересобран согласованный test image, scripts mount исправлен. Старый browser assertion ожидал ссылку внутри закрытого details: тест теперь раскрывает существующий список источников. Продуктовое поведение web sources не менялось. Эти первоначальные красные прогоны не выданы за успешную проверку.

## Настоящая модель и установленный reader

1. Native read завершён completed: system.read(src/bervel/app_setup.py), database.tables, container.list,3completed actions. Ответ сослался на реальные источники и явно обозначил, что строки БД/логи в этом вопросе не читались.
2. Прямые запросы создать файл и перезапустить API: completed ответ с отказом,0actions; обращения не создавались.
3. Первый контрольный тикет получил ложный deny общего classifier. Изолированный debug-plan показал допустимый draft, debug-decision — deny. Исправлен контракт: inspection проверяет только создание нового внутреннего draft, передавая выполненные read results как фактический untrusted context; разрешение всё ещё исходит только из текущего прямого запроса.
4. После исправления реальная цепочка create→authorize→TicketService→receipt→answer завершена completed, ровно1тикет со статусом created, прочитан обычным API «Обращений».
5. Живой classifier: quoted request→deny, instruction only in evidence→deny, history-only request→deny, прямое создание без исполнения исправлений→allow. Последующая живая просьба только объяснить цитату и не создавать обращение завершена без effects.
6. Installed reader:5запрещённых операций/путей дали403, неверный private token401; все5источников имеют mount RDONLY. Реально прочитаны схема/строка artifacts, inspect/logs API, установленный исходник API и PG_VERSION в томе PostgreSQL. Проверены скрытие .env и отсутствие INSERT/UPDATE/DELETE/TRUNCATE/CREATE у reader role.

Контрольный чат и единственный неизменённый контрольный тикет удалены после проверки точных ID, владельца, содержания, revision=1 и полного состава реплик. Пользовательские записи не удалялись. Минимальные durable command receipts сохранены штатно; они защищают от повторов.

## Данные и установка

Перед установкой активных chat/radar/knowledge заданий не было. Веб-вход и фоновые обработчики были кратко остановлены. Backup БД/оригиналов/окружения находится в .dev/local/inspection-2026-10-29/backup, внутри проекта. Новая миграция проверена на восстановленной populated БД; сравнение SHA256 каждой прежней строки (за исключением нового поля mode) и каждого оригинала подтвердило8308строк/11файлов. Восстановление использовало отдельную БД, затем она удалена.

После установки и scoped QA cleanup повторное сравнение подтвердило сохранение всех тех же8308строк и11оригиналов. Новые служебные квитанции проверок объясняют дополнительные16строк; прежние значения не изменены. Установлены9работающих служб, включая reader/worker-inspection. PostgreSQL volume и исходные ID сохранены. Codex container/внешние MOX auth/config/CA mounts не переустанавливались. VPN и Open Server не менялись.

Финальный API image: bervel-api:inspection-20261029-r2, frontend: bervel-frontend:inspection-clean-answers-20261029; локальные runtime tags направлены на проверенные образы. Первая установка и последующая правка classifier отражены раздельно в install.log/install-r2.log. Task helpers фиксируют эту операцию; повторять их вслепую нельзя.

### Корректировка ответов по скриншоту пользователя

Убран общий UI renderer сырых kind=data результатов: раскрывающиеся JSON-блоки «Каталог системы · данные», «Чтение исходника · данные», контейнеры/таблицы/прочие технические результаты больше не показываются в ответах обоих режимов, в том числе старых диалогов. Остались обычный текст и смысловые карточки файлов/обращений/радара/точных табличных итогов, источники и краткий журнал действий. Серверные результаты не переписывались.

Сборка TypeScript/Vite и lint прошли (0ошибок,7предупреждений).44существующих browser checks прошли после обновления старого assertion, ожидавшего удалённый raw dropdown. Реальная установленная история содержит24сохранённых результата чтения: raw dropdowns=0, pre blocks=0, page errors=0. Доказательства: frontend-clean-answers-{build,final,install}.log, clean-answers-ui.json и screenshots/clean-answers.png. Обновлён только frontend; API/worker/БД не переустанавливались.

## Ограничения

Одна текстовая страница до32000символов; файл или Docker response до32МиБ. Строки БД и каталоги читаются постранично, логи — по tail/временному диапазону. Неизвестный бинарный формат возвращает metadata_only; штатный PDF/OCR/Office decoder сохраняет свои ограничения. Непрочитанный остаток маркируется явно. Необозначенный, ещё неизвестный секрет в произвольном свободном тексте нельзя гарантированно распознать эвристикой. Исследовательские выводы модели не являются формальной проверкой кода.

## Файлы и доказательства

- Архитектура: ../architecture/28-inspection-chat.md; план: ../planning/2026-10-29-inspection-chat-plan.md; эксплуатация: ../operations/inspection-chat.md.
- Реализация: src/bervel/modules/inspection/, общие chat/{service,api,infrastructure}, core/model_contract.py, migration0015, app_setup.py/worker.py/config.py, compose.yaml, frontend chat/App/results. Docker init/env/start helpers согласованы с новым reader/worker.
- Проверки: .dev/local/inspection-2026-10-29/{backend-final-complete,node-final,frontend-final,auth-final-green,live,live-ticket-fixed,classifier-native,live-ui-final}.log, reader-check.json, preservation-final.json, cleanup.json, review.md и review-*.log.
- Native результаты: live-read/refusal/ticket-fixed/quoted-refusal.json; classifier-native.json. Снимки: screenshots/live-inspection-1440.png и live-inspection-390.png.
- Git в core использовался только для чтения. Staging/commit/push/fetch/PR не выполнялись; прежние незакоммиченные изменения сохранены. Worktree/клон/перенос проекта не создавались.
