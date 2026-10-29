# MOX TLS и живой RAG — проверка 22 октября 2026

## Результат

Ошибка TLS устранена в контейнере. Реальный POST /api/v0.1/settings/model/test через сайт вернул HTTP 200, answer **OK**, model **gpt-6.1-sol**. Реальный браузерный сценарий загрузки двух CSV и трёх вопросов завершился успешно. auth.json и config.toml MOX не изменялись; login/logout, исправления настроек MOX, VPN и Open Server не выполнялись. Git только чтение.

## Причина и исправление

Сертификат MOX самоподписанный и уже доверен Windows Root, но отсутствовал в Linux-контейнере. SHA256 сертификата сервера и Windows Root совпал: `5635D0726498F455CF55EF0B8E411457CFEFD7092218C0335CF83356D5339110`. Он действителен до 15 сентября 2036, SAN содержит localhost, 127.0.0.1 и ::1. Прежняя замена имени на host.docker.internal также нарушала hostname validation.

Optional compose.mox.yaml подключает только публичный PEM в /run/mox/ca.pem read-only. Node получает NODE_EXTRA_CA_CERTS, дочерний CLI — CODEX_CA_CERTIFICATE. Helper -Mox экспортирует публичный сертификат из существующего доверенного Windows Root в ignored docker/mox/ca.pem; private key не экспортируется. Исходный URL localhost сохраняется. Внутри контейнера TCP bridge слушает только loopback и пересылает неизменённые зашифрованные байты к host.docker.internal:8000. TLS завершается на исходном MOX; цепочка и имя проверяются клиентом. Insecure-флаги не используются.

Полная TLS-проверка после исправления: authorized=true, error=null, fingerprint совпал. После успешной проверки выполнен настоящий модельный запрос, не тестовый Responses server.

## Живой сценарий без подмены ответов

Через рабочий интерфейс http://127.0.0.1:5173/ загружены:

| Файл | artifact_id | Позиции/количества, возвращённые моделью |
|---|---|---|
| rag-proof-alpha-mvjfgn3d.csv | b44ae983-4017-4e43-ab59-3621939a2d91 | RAG_ALPHA_72913 — 17 pcs; RESERVE_RAG_ALPHA_72913 — 29 pcs |
| rag-proof-beta-mvjfgn3d.csv | 6d53d9c5-622b-435b-8887-fb446d7c4d3f | RAG_BETA_48261 — 83 pcs; RESERVE_RAG_BETA_48261 — 47 pcs |

В одном диалоге дважды задан вопрос о самом последнем CSV. После второго upload ответ использовал beta и не включал маркер alpha. Все source artifact_id/revision/filename совпали с реальными оригиналами, total_rows=2, truncated=false. После reload история из двух ответов восстановилась; третий вопрос вернул beta и 83 pcs. Диалог `2def39e9-51cc-4f5c-8ed2-737ead03fcac`.

SQL независимо подтвердил 3 completed turns с model=gpt-6.1-sol и правильными источниками A/B/B, а также 2 сохранённых CSV chunks. Исключив новые связанные IDs, fingerprint всех прежних строк 11 таблиц совпал с до-тестовым состоянием. SHA256 auth.json/config.toml до/после совпал. Ошибок браузера нет. Контрольные CSV оставлены в каталоге загрузок; beta сейчас последний.

## Автоматические проверки и ревью

15 Node-тестов прошли: runner, настройки, byte bridge, genuine CLI против явно тестового Responses API. Новые регрессии первоначально падали на отсутствующем bridge и изменённом localhost. Production runner собран и развёрнут. Независимое ревью TLS-правки не обнаружило существенных дефектов; дополнительно выполнено 8 локальных тестов без новых модельных запросов. Скриншот chat-beta.png просмотрен: ответы и фрагменты A/B видны в интерфейсе.

Доказательства внутри `.dev/local/codex-chat/tls-live/`: connection-test.json, rag-live.json, chat-alpha.png, chat-beta.png, chat-restored.png, sql-verification.json, mox-before.json, node-tests.log, review.md, deploy.log. Скрипты: `.dev/scripts/codex_chat_rag_live.cjs` и `codex_rag_sql_verify.py`. Запуск live script создаёт новые контрольные uploads и реальные модельные запросы; это не unit test.

Ограничения прежние: RAG по последнему оригиналу CSV, ограниченный текстовый контекст, общий серверный доступ без личного входа. Перенос на другую машину, ротация сертификата во время работы и промышленная нагрузка не проверялись. При обновлении публичного CA контейнер codex нужно пересоздать/перезапустить с тем же optional MOX подключением, чтобы Node перечитал trust.
