# Устройство Python

Рабочий корень — `C:/OSPanel/home/bervel.local`, приложение — `src/bervel`. Карта 11.11.2026:

```text
src/bervel/
  __main__.py                  CLI: migrate / serve / worker
  app_setup.py                 явная сборка API и общих зависимостей
  ai_tender_radar_setup.py      сборка AIрадара/worker
  presales_setup.py             сборка пресейла/worker
  worker.py                    обязательная роль процесса
  core/
    api/                       HTTP платформы, знания, preview, настройки, голос
    infrastructure/            PostgreSQL, оригиналы, документы, embeddings, модель
    migrations/versions/        история от 0001
    services.py                ArtifactService
    knowledge.py               база знаний
    document_memory.py         чтение/поиск документов
    config.py                  настройки установки
  modules/
    chat/                      ассистент/действия/история
    inspection/                native Codex CLI
    company_settings/          каталог/склад/производство
    ai_tender_radar/            профили/документы/анализ/оценка/вопросы
    presales/                  обращения/редакции/цены/экспорт
    tickets/                   внутренние обращения
```

HTTP проверяет вход/доступ и вызывает операцию; она использует явно переданные зависимости. SQL/HTTP/SDK/файлы находятся в инфраструктуре. Ядро не импортирует модули, сосед вызывается только через публичный пакет.

API выполняет короткую операцию и принимает команду; длительная работа идёт в PostgreSQL-очередь [отдельной роли](22-independent-workers.md). Универсального ProcessService, учебного document_review, registry/datasets и обычного tender_radar нет. CSV-декодирование остаётся общим чтением документов.

Файл адресуется одним ID. Оригиналы/права/знания/модель не дублируются по модулям; предметные таблицы находятся у владельцев. Историю миграций не перемещают за Python-пакетами.

Новый модуль начинается с [минимальной заготовки](../../templates/module/README.md): типизированные публичные операции, нужные порты, реальные адаптеры. Добавить только необходимые API/таблицы/настройки и явную сборку. Манифесты, `default_schemas`, `/modules` и пустые слои не нужны.

Приложение не импортирует `.dev`. Проверки границ — `.dev/tests/test_python_boundaries.py`, далее проверки сценария. [Команды Docker](../operations/docker-local.md).
