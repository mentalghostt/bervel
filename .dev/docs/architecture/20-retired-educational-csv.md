# Выведенные сценарии

Учебный CSV/document_review выведен 26.10. Удалены extraction/process services, статический registry, datasets/process-runs и учебные HTTP-маршруты. n8n/Redis исключены решением [ADR-0005](../adr/0005-python-background-processes.md). Обычный tender_radar выведен 05.11; действует [Радар ИИ](03-tender-radar.md).

Чтение CSV, preview, знания и ассистент сохраняются. Историческое имя fixture не означает мусор: `requirements-v1.csv`/`requirements-v2.csv` нужны текущему test_api для отдельных неизменяемых загрузок.

История миграций, PostgreSQL-данные, оригиналы, архивы и бэкапы не являются выведенным исполняемым кодом. Поздние установленные модули и текущая схема сохраняются.

[Обзор](00-overview.md), [Python](19-python-layout.md), [файлы](25-independent-files.md).
