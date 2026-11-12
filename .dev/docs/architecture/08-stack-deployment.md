# Стек и локальная установка

Backend — Python/FastAPI, SQLAlchemy, PostgreSQL/pgvector; frontend — React/TypeScript/Vite и Nginx. Оригиналы находятся в отдельном volume. Codex-runner обслуживает модельные запросы/native inspection. Версии зависимостей закреплены manifest/lock-файлами приложения.

Очереди — PostgreSQL, workers — knowledge/chat/inspection/ai_tender_radar/presales. n8n, Redis, Temporal и Qdrant не используются текущей реализацией.

Рабочие Dockerfile/Compose находятся в корне, dev/test-профили — в `.dev/docker`. Установленное приложение не требует `.dev` или Open Server Panel. При отдельной задаче модуль OSP включает/переключает пользователь.

[Docker-инструкция](../operations/docker-local.md) содержит правильные команды. Обычный start запускает сайт/API/PostgreSQL; роли workers включаются отдельно по задаче. Dev меняет среду разработки и не является проверкой сохранения установленного выпуска.

Установленная основа: API/пресейл `bervel-presales:20261110-*`, frontend `bervel-ngrok-radar:20261110-frontend`, схема 0029. Это локальная установка; промышленный SLA/RPO/RTO, хранение и полномочия заказчика остаются отдельными требованиями. [Открытые решения](09-open-decisions.md), [безопасность](07-reliability-security.md).
