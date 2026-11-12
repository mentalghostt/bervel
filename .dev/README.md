# BERVEL: материалы разработки

Рабочий корень — `C:/OSPanel/home/bervel.local`. Приложение и исходники остаются в этой папке. В `.dev` находятся документация, проверки, инструменты и приватные рабочие материалы; установленное приложение от них не зависит.

Начать с [карты документации](docs/README.md), [текущего устройства](docs/architecture/00-overview.md) и [команд Docker](docs/operations/docker-local.md). Основной локальный адрес — [BERVEL](http://127.0.0.1:5173/).

| Часть | Описание |
|---|---|
| Файлы и знания | [Один ID файла](docs/architecture/25-independent-files.md), [просмотр/удаление](docs/operations/files-and-pdf.md), [подготовка для ИИ](docs/operations/shared-knowledge.md) |
| Ассистент и обращения | [Рабочее пространство](docs/operations/workspace-assistant-radar.md), [данные и native web](docs/architecture/26-assistant-data-and-native-web.md) |
| Проверка | [Встроенный Codex CLI](docs/operations/inspection-chat.md) |
| Радар ИИ | [Профили и обновление](docs/operations/ai-radar-profile.md) |
| Пресейл | [Порядок работы](docs/operations/presales-pipeline.md), [спецификация](docs/planning/2026-11-10-presales-pipeline-spec.md) |
| Python | [Платформа, модули и сборка](docs/architecture/19-python-layout.md) |

API и presales-worker установлены из выпуска `bervel-presales:20261110-*`, схема — `0029_presales_pipeline`; frontend — `bervel-ngrok-radar:20261110-frontend`. Это установленная основа, а не обещание, что все workers сейчас запущены. Перед операциями проверить `docker.ps1 -Action status`.

[Ремонт 23 предупреждений радара](docs/planning/2026-11-10-radar-document-repair.md) не завершён. Последний подтверждённый итог: 377 готовых карточек и 23 предупреждения. [Досылка в «Проверку»](docs/planning/2026-11-06-inspection-steering-spec.md) и попап настроек пресейла подготовлены, но установка отложена. Другие отложенные изменения также не становятся установленными из-за наличия кода.

[Discovery](docs/discovery/README.md), [первичные источники](docs/sources/README.md) и [реестры](docs/planning/README.md) сохраняют происхождение требований. Это не список обещанных возможностей. Оригиналы/резервные копии и незавершённые материалы сохраняются в `.dev/data/private` и защищённых частях `.dev/local`.

`python .dev/scripts/check_project_materials.py` проверяет ссылки, реестры и источники; это не тест приложения. Команды проверок — в Docker-инструкции. Перед изменениями читать `AGENTS.md`/`CLAUDE.md`, журналы и уроки.
