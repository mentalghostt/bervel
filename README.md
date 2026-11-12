# BERVEL

Локальное приложение: файлы и база знаний, чат, тендерный радар и пресейл.
Нужны Docker Desktop с Compose и PowerShell. Команды выполняются из корня
репозитория; Python, Node и модули Open Server Panel для запуска не нужны.

```powershell
.\.dev\scripts\docker.ps1 -Action init
.\.dev\scripts\docker.ps1 -Action start
```

Сайт: http://127.0.0.1:5173. `init` создаёт локальный `.env` с секретами;
при повторном запуске сохраняет существующие значения. Модель и внешние
API требуют настройки собственных доступов: [инструкция запуска](.dev/docs/operations/docker-local.md).

Обычный запуск создаёт **9 сервисов**: PostgreSQL, одноразовую миграцию,
API, frontend, Codex и четыре фоновых исполнителя — knowledge, chat,
ai-tender-radar, presales. Тесты, инженерная инспекция, симулятор и туннели
не входят в обычный запуск. n8n и Redis больше не используются.

```powershell
# Работа с обновлением исходников
.\.dev\scripts\docker.ps1 -Action dev
# Дополнительно включить инженерную инспекцию (ещё 3 сервиса)
.\.dev\scripts\docker.ps1 -Action start -Inspection
# Тесты запускаются отдельно; E2E-контейнеры удаляются после прогона
.\.dev\scripts\docker.ps1 -Action tests
.\.dev\scripts\docker.ps1 -Action e2e
.\.dev\scripts\docker.ps1 -Action status
.\.dev\scripts\docker.ps1 -Action stop
```

База и файлы сохраняются в именованных томах Docker и не входят в Git.
Не используйте `docker compose down -v` для очистки контейнеров — эта команда
удаляет данные. Одноразовые проверки запускайте через `docker run --rm` или
`docker compose run --rm`; для завершившегося preview удаляйте конкретный
контейнер по имени. `--rm` удаляет только запускаемый контейнер, а не его
Compose-зависимости; сценарий `e2e` отдельно убирает свои зависимости.

[Документация проекта](.dev/README.md).
