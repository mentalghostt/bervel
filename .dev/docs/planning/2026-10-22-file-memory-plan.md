# Общая память файлов: план

Режим АВТОНОМНО. Inline исполнение, TDD, одно независимое итоговое ревью. Спецификация ../architecture/17-file-memory.md. Прежние dirty-файлы сохраняются; baseline внутри .dev/local/file-memory, Git не изменять. Runtime вне .dev. MOX/auth/VPN/OSP не менять.

1. Каталог и retrieval: snapshot всех актуальных файлов с scope, обратная совместимость старых snapshots; generic DocumentMemory, индексы, миграция0006. Тесты RED/GREEN: старый CSV найден, два файла в одном ответе, named unknown file metadata, latestCSV, scope/revision/pinning, повреждение, partial. Реализация core/document_memory.py, infrastructure/document_reader.py, chat_repository.py, database.py, chat.py/api/worker.
2. Форматы: Poppler/Tesseract в runtime без Python-пакетов; ограниченные PDF/OCR адаптеры и безопасные ошибки. Верифицировать CSV, TXT, PDF text, PNG OCR и неизвестный binary. Изображения выбранных источников — optional images в /v1/generate, local_image SDK, strict MIME/base64/size/signature/cleanup. Node RED/GREEN и реальный MOX vision.
3. UI: upload любого оригинала в FilesPage, download/read-only источники, locator/status по типу в TestChat/chatApi. Сборка/линтер, browser mocked и live.
4. Развёртывание additive без потери volumes/данных. Живые запросы через MOX по старому/новому CSV, обоим CSV вместе, TXT, PDF, PNG, неизвестному формату; источники ID/revision/name/locator проверять независимо. Перезапуск/reload, ревью и отчёт. Не выдавать metadata_only за прочитанное содержимое. Проверка auth/config hashes.

Все четыре пункта выполнены 22 октября 2026. Итог: 131 backend, 35 browser, 18 Node; четыре проверки реальных декодеров, включая scanned PDF; шесть настоящих MOX вопросов, reload и download; независимое ревью замечаний закрыто. SQL и hashes подтверждают сохранность. [Отчёт](../quality/file-memory-report.md). Переход на отдельную семантическую/векторную систему и дополнительные парсеры не включены.
