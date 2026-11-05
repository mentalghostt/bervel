# Проверка: переход на настоящий Codex CLI — 30 октября 2026

Корень C:/OSPanel/home/bervel.local, Personal, основная ветка core. Режим АВТОНОМНО, параллельные агенты по прямому запросу пользователя. Git только для чтения; существующая незакоммиченная работа радара сохранена. VPN/OSP и папка проекта не менялись.

Последнее требование заменяет прежние read-only ограничения и подтверждения патчей. В действующей сборке инспектора используется NativeInspectionWorkflow → private job API → официальный SDK0.163.0 → настоящий Codex CLI. Исходная строка запроса без прикладного отбора/сокращения истории; native persisted thread и resume; sandbox danger-full-access, approval_policy never. Прежние planner/engineering classes не собираются и не являются fallback. Подробнее: [архитектура32](../architecture/32-inspection-engineer.md).

Проверено до установки:

- Независимое ревью:0открытых Critical/Important. Закрыты transport orphan/скрытая ошибка и partialsecret в обрезанном progress/title.
- Node:100/100. Python native/общий chat/границы модулей:76targeted, дополнительный display/integration92. Это пересекающиеся наборы, не суммировать.
- UI28focused+33adjacent до финальных точечных правок, затем4N1 и2N2. Экспорт8Node. Native UI live после установки ещё не проверен.
- В новом образе реально исполняются codex-cli0.163.0, Node24.22.0, Python3.12.14/pytest9.1.1, Docker29.9.2/Compose5.6.1, Git/ripgrep; pip check без конфликтов.
- Свежие backup/restore сохранили8911исходных строк/24файла. КодAPI125hashes/Codex134hashes соответствует source manifest, лишних файлов0. Внешние MOX auth/config/CA и codex_state сохранены отдельно; значения секретов в отчёт не выведены.

Полный серверный прогон: **868passed,3прежних warnings**. Native установлен; postinstall сохранил все8911строк/24объекта и6254исходных файлов codex_state byte-for-byte; MOX auth/config/CA/provider state и оба PG container IDs сохранены. Installed API/worker/reader6×125 и Codex134source hashes совпали. Подтверждены native assembly, отсутствие confirmation routes, inspection input без4000ceiling и сохранённый assistant4000.

Реальный CLI, собственный QAчат58448e42-8a75-48ff-8c55-fcb95b9a39d5/native thread01a18d88-018d-7680-af58-dd81f4c29465:

| Проверка | Результат | Секунды | Native actions |
|---|---|---:|---:|
| Только запрошенный fixture | Прочитан реальный файл, Markdown heading/table |10.2|1|
| Изменение исходника | QA_AFTER→QA_NATIVE в настоящем host-файле, без proposal/approval |10.1|1|
| SVG/PDF | Созданы обычными командами, два реальных artifact uploads и verified cards |18.3|1|
| Контур | Реальный KonturClient.limits/GET limitGroups, текущие числа без вывода ключа |22.7|2|

Все4запроса completed в одном native thread; старых system.list/conversation.read вызовов нет. Вложенный PDF отдельно отрендерен Poppler и визуально проверен: заголовок/таблица читаемы, обрезки нет. Native frontend13/13 пройден на installed UI с mocked API.

Реальный desktop/mobile UI проверил working attachment cards, SVG780×180, PDFcanvas/скачивание/хеши, отсутствие console/page/request errors. Обнаруженный дефект inlineMarkdown (модель выбрала advertised preview_url, renderer принимал только/content) исправлен нормализацией точной sameorigin artifact/preview ссылки к проверенной metadata+/content. После установки r2 **strict live UI2/2passed**: именно inlineMarkdown image780×180/no placeholder, отдельные filecards, реальные download filenames/SHA256,0page/console/requesterrors/неожиданных mutations/pageoverflow. Installed r2 browser14/14passed; scoped delta34/34passed. Root визуально проверил реальный mobile screenshot и PDF.

Из native provider config удалены ошибочно перенесённые legacy retry0 overrides, чтобы повторы запросов/потока велись штатным CLI; native13regressionsgreen. Запрет автоматического повторного job start при неизвестном исходе сохранён. Независимое r2 delta review одобрено:0открытыхCritical/Important.

Финальный r2 установлен, заменены только Codex+frontend; API/workers/reader/оба PG IDs неизменны. Native CLI image521bd709e24f…, frontend5cfbc334792f…, API038d68d583b0…. Перезапуск сохранил **все11765текущих statefiles** (включая native jobs/threadmap/rollouts) byte-for-byte; snapshot8937rows/26objects до/после также совпал. MOX bindings/settings/CA и прочие env/mounts сохранены.

**Actual resume после замены контейнера passed**: тот же native thread, контрольный маркер первого сообщения возвращён за4.2s, toolactions0. **Actual Stop passed**: действительно запущенная python sleep120 команда отменена, turn terminal blocked/cancel_requested true; последующая проверка /proc не нашла оставшегося собственного процесса. Дополнительно непосредственно в native контейнере доказаны Docker daemon access, SELECT1 основной проектной БД и подключение к isolated testDB. Никаких gated broker/proposal инструментов для этого не использовано.

Собственный native QAчат, два upload IDs и временный fixture удалены только после проверки точных IDs, всех6turns, имени/role/origin/SHA256 и содержимого исходника. Local SVG/PDF, screenshots, receipts/CLI context и доказательства сохранены. Пользовательские чаты не менялись. **Финальная повторная сверка после QA/cleanup сохранила все8911исходных строк и24исходных объекта** (`final-preservation.json`). В live очередь незавершённых native тестов не оставлена.

Ограничения: pre-native история остаётся архивом, автоматически в новый CLI prompt не переносится; физическое окно/compaction ведёт сам CLI. Attachment upload имеет существующий явный5MiB предел; это не ограничение чтения/создания файла или модельного контекста. Неизвестные необозначенные секреты не гарантируются эвристическим display mask; export bytes не фильтруются, поэтому native инструкция запрещает экспорт credentials. Существующий mobile PDF toolbar тесен внутри узкой карточки, документ читаем/скачивается и внешнего pageoverflow нет. Результаты промежуточного confirmed-patch выпуска не использованы как доказательство native.

Доказательства: `.dev/local/inspection-engineer-2026-10-30/native-cli/`, native plan и task-a/b/c/d reports. Известные5старых browserradar failures предыдущего выпуска воспроизведены на старом установленном образе; несвязанные radar изменения в рамках этой задачи не выполнялись.
