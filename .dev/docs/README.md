# Документация BERVEL

Актуальная карта 11.11.2026. [Устройство приложения](architecture/00-overview.md) отделяет установленный продукт от клиентских источников и отложенного кода.

## Работа с приложением

- [Docker: запуск, разработка, данные и проверки](operations/docker-local.md).
- [Файлы: оригиналы, просмотр и удаление](operations/files-and-pdf.md), [подготовка для ИИ](operations/shared-knowledge.md).
- [Ассистент, чаты и обращения](operations/workspace-assistant-radar.md), [доступ через веб-сервер](operations/frontend-server-access.md).
- [Радар ИИ: профили и обновление](operations/ai-radar-profile.md), [симулятор Контур API](operations/kontur-simulator.md).
- [Пресейл-конвейер](operations/presales-pipeline.md).
- [Проверка: native Codex CLI](operations/inspection-chat.md).
- [Временный доступ Cloudflare и ограничения](operations/cloudflare-sharing.md).

## Текущие контракты

[Ядро](architecture/01-core.md), [общие возможности](architecture/02-shared-capabilities.md), [данные](architecture/05-data-model.md), [HTTP/команды](architecture/06-contracts.md), [стек](architecture/08-stack-deployment.md), [границы ядра](architecture/12-core-v0.1-spec.md), [Python](architecture/19-python-layout.md), [workers](architecture/22-independent-workers.md).

[Радар ИИ](architecture/03-tender-radar.md), [пресейл](architecture/04-presales.md), [рабочее пространство](architecture/24-workspace-assistant-radar.md), [один ID файла](architecture/25-independent-files.md), [знания](architecture/21-shared-knowledge.md), [ассистент и native web](architecture/26-assistant-data-and-native-web.md), [CLI Проверки](architecture/32-inspection-engineer.md), [спецификация пресейла](planning/2026-11-10-presales-pipeline-spec.md).

Принятые ADR: [модульный монолит](adr/0001-modular-core.md), [проверяемые результаты](adr/0003-structured-decisions.md), [хранение](adr/0004-hybrid-standardized-storage.md), [Python/PostgreSQL](adr/0005-python-background-processes.md), [отдельные файлы](adr/0006-independent-files.md).

Учебный document_review, n8n/Redis и обычный tender_radar выведены. Их старые команды/планы/эксперименты не входят в текущую документацию. История миграций, источники и резервные копии сохраняются.

## Установленный результат и незавершённое

- [Пресейл: установленный выпуск](quality/2026-11-10-presales-pipeline.md).
- [Радар: завершение с 23 предупреждениями](quality/2026-11-10-radar-completion.md), [manual/auto повторный анализ](quality/2026-11-10-radar-reanalysis-option.md).
- [Адресный ремонт 23](planning/2026-11-10-radar-document-repair.md) — не завершён.
- [Досылка в Проверку](planning/2026-11-06-inspection-steering-spec.md), [план](planning/2026-11-06-inspection-steering-plan.md), [отложенная установка](quality/2026-11-06-inspection-steering.md).
- [Условия перехода файлов](planning/2026-10-28-files-without-revisions-design.md), [план](planning/2026-10-28-files-without-revisions-plan.md), [проверки/ограничения перехода](quality/files-without-revisions-2026-10-28.md).
- [Cloudflare: сетевой блокер](quality/cloudflare-sharing-2026-11-10.md).
- [Docker: очистка и проверки 11 ноября](quality/docker-cleanup-2026-11-11.md).

Quality-отчёт относится к конкретному выпуску. Старый helper/образ нельзя применять по одному историческому отчёту; факт наличия исходников не подтверждает установку. Попап пресейла и часть изменений Проверки остаются отложенными.

## Источники и ограничения

[Discovery](discovery/README.md), [разбор 12 файлов](discovery/customer-package-2026-10-06.md), [покрытие R01–R22](discovery/customer-package-request-coverage.csv), [происхождение](sources/README.md), [источники входов](architecture/11-data-sources-and-intake.md), [открытые решения](architecture/09-open-decisions.md), [словарь](../GLOSSARY.md), [текущие задачи](planning/README.md).

КП/опросник и наличие образца не означают согласованный объём, реальные возможности производства или полномочия отправки/подписи. Исходные запросы сохраняются независимо от удаления устаревшей агентской декомпозиции.
