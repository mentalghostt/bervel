# Карта предпроектной документации

## Архитектура

- [Удаление 28 файлов и переход без редакций: код проверен, установка не выполнена](quality/files-without-revisions-2026-10-28.md)

- [Файлы без редакций: контракт, миграция0013 и статус установки](architecture/25-independent-files.md)
- [ADR-0006: каждый файл — отдельный неизменяемый оригинал](adr/0006-independent-files.md)

- [Чаты ассистента: список, удаление и компоновка — 28 октября](quality/chat-workspace-2026-10-28.md)

- [Действующее рабочее пространство: файлы, радар, универсальный ассистент и обращения](architecture/24-workspace-assistant-radar.md)
- [Текущее устройство после удаления учебного CSV](architecture/20-retired-educational-csv.md)
- [Рабочий тендерный радар: импорт, полный профиль и прямая оценка всех закупок](architecture/18-tender-radar.md)
- [Прямая оценка: проверки механики и границы проверки качества](quality/radar-direct-evaluation-report.md)
- [История фонового CSV: Python и PostgreSQL](architecture/15-python-background-processes.md)
- [ADR-0005: замена n8n и Redis](adr/0005-python-background-processes.md)

- [Принятая спецификация ядра v0.1 — этап 1](architecture/12-core-v0.1-spec.md)
- [Этап 3.1: принятый профиль хранилища и учебного CSV](architecture/13-core-stage-3.1-storage-csv.md)
- [Исторический этап 3.2: прежний движок](architecture/14-core-stage-3.2-orchestration.md)
- [Этап 2 ядра: результаты первого эксперимента с n8n](quality/core-stage-2-n8n-report.md)
- [Этап 2 ядра: ручное восстановление n8n](quality/core-stage-2-n8n-manual-report.md)
- [Как ИИ входит в веб-приложение](architecture/10-ai-walkthrough.md)
- [Источники данных и первый контур обработки по полученным файлам](architecture/11-data-sources-and-intake.md)
- [Общая схема и границы](architecture/00-overview.md)
- [Общее ядро](architecture/01-core.md)
- [Общие прикладные возможности](architecture/02-shared-capabilities.md)
- [Тендерный радар](architecture/03-tender-radar.md)
- [Пресейл-конвейер](architecture/04-presales.md)
- [Модель данных и владение](architecture/05-data-model.md)
- [Контракты и события](architecture/06-contracts.md)
- [Отказоустойчивость и безопасность](architecture/07-reliability-security.md)
- [Стек и размещение](architecture/08-stack-deployment.md)
- [Открытые решения](architecture/09-open-decisions.md)

## Работа над проектом

- [Исправление интерфейса и универсального ассистента28.10](quality/workspace-polish-2026-10-28.md)
- [Файлы, три источника радара, ассистент, голос и обращения](operations/workspace-assistant-radar.md)
- [Файлы: просмотр оригиналов, две колонки и единый PDF-шаблон](operations/files-and-pdf.md)
- [Проверки обновления файлов и PDF](quality/files-pdf-report.md)
- [Тендерный радар: открыть отчёт, загрузить XLSX/CSV и выбрать профиль](operations/tender-radar.md)
- [Радар: настоящая проверка MOX, смена RAG, результаты и ограничения](quality/tender-radar-report.md)
- [Результаты замены n8n/Redis на Python и PostgreSQL](quality/python-background-report.md)
- [Историческая инструкция фонового CSV, состояния и восстановление](operations/python-background.md)

- [Docker: устройство, запуск, разработка и резервные копии](operations/docker-local.md)
- [Результаты Docker-переноса и ограничения проверок](quality/docker-environment-report.md)
- [Как читать декомпозицию](planning/README.md)
- [Порядок реализации](planning/roadmap.md)
- [Каталог функций](planning/function-catalog.csv)
- [Предварительный backlog](planning/backlog.csv)
- [Зависимости](planning/dependency-map.md)
- [Приёмка](quality/acceptance.md)
- [Оценка ИИ](quality/ai-evaluation.md)
- [Сценарии проверки](quality/test-scenarios.csv)
- [Первый проверочный сценарий ядра v0.1](quality/core-v0.1-scenario.md)
- [Проверка реализованного этапа 3.1](operations/core-stage-3.1-local.md)
- [Запуск и проверка процессов этапа 3.2](operations/core-stage-3.2-local.md)
- [Проверки и ограничения этапа 3.2](quality/core-stage-3.2-report.md)
- [Страница выбора CSV: запуск и проверка этапа 1](operations/frontend-csv-selection.md)
- [Загрузка CSV в PostgreSQL через браузер: этап 2](operations/frontend-csv-upload.md)
- [Список загруженных файлов и просмотр сохранённых строк](operations/frontend-file-browser.md)
- [Чат по всем файлам: загрузка, источники и настройки доступа](operations/codex-chat.md)
- [Архитектура общей памяти файлов, PDF и изображений](architecture/17-file-memory.md)
- [Реальная проверка общей памяти через MOX](quality/file-memory-report.md)
- [Доступ через веб-сервер без ручного ввода API-ключа](operations/frontend-server-access.md)
- [Отчёт и покрытие CV01–CV20 этапом 3.1](quality/core-stage-3.1-report.md)
- [Подготовка эксплуатации](operations/runbook-outline.md)
- [Предлагаемый порядок ручного восстановления n8n](operations/n8n-manual-recovery.md)

## Данные и доказательства

- [Повторное чтение пакета перед реализацией радара](research/2026-10-22-radar-customer-package.md)
- [Контур: официальные условия API/Excel и шесть реальных карточек](research/2026-10-22-kontur-tenders.md)
- [Как работать с запросами заказчику](discovery/README.md)
- [Что прояснили 12 файлов заказчика](discovery/customer-package-2026-10-06.md)
- [53 подзадачи первоначальной таблицы](discovery/preliminary-customer-requirements.csv)
- [Текущее покрытие R01–R22](discovery/customer-package-request-coverage.csv)
- [Реестр запросов](discovery/requests.csv)
- [Допущения](discovery/assumptions.csv)
- [Риски](discovery/risks.csv)
- [Источники и ограничения проверки](sources/README.md)
- [Протокол независимого аудита запросов](sources/brief-supplement-final-audit.txt)

## Статусы

«В КП» означает содержание предложения, а не подтверждённую техническую реализуемость.
«В опроснике» означает заданный вопрос, а не полученный ответ.
«Предлагается» означает предварительное решение команды.
«Подтверждено заказчиком» допустимо только со ссылкой на фактический ответ/протокол и дату.

«Получен образец» означает доступный файл и проверяемое наблюдение. Это не утверждение производственных правил, не экспертная разметка и не согласование объёма. Пакет от 6 октября 2026 частично уточняет запросы, но не закрывает их целиком.

14 октября 2026 владелец разработки принял спецификацию ядра v0.1 с модульным монолитом, гибридным стандартизированным хранилищем и дополнением о независимости от Open Server Panel. Этап 1 завершён; эти решения разработки не закрывают вопросы заказчику DEC-01–DEC-12.

Независимый аудит относится к дополнительным запросам R01-R22. Он не является аудитом готовности всей архитектуры или работающего ПО.


Действующее устройство Python: [карта платформы и модулей](architecture/19-python-layout.md).
