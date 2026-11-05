# «Проверка» как встроенный Codex CLI — окончательное уточнение 30.10.2026

Режим АВТОНОМНО и делегирование сохраняются. Корень C:/OSPanel/home/bervel.local, Personal, основная ветка core, Git только read-only. Это продолжение четырёх тикетов; последующие прямые указания пользователя имеют приоритет над прежним проектом инспектора.

## Последнее явное решение пользователя

Пользователь требует использовать помощника как Codex через встроенный CLI. Текст запроса поступает непосредственно в CLI, без собственного JSON-планировщика, подбора/нарезки контекста и посредника, решающего действия модели. Полномочия полные: чтения, команды и изменения, без выдачи разрешений/предложений патча/подтверждений. Native CLI profile: danger-full-access, approval_policy=never. Это явно отменяет прежнюю схему code.propose→human confirmation и закрытые диагностические операции для нового инспектора.

Нормальный технический транспорт UI/API/CLI остаётся; он доставляет текст, события и файлы, не планирует задачу и не подменяет/обрезает её контекст. История CLI хранится самим CLI; один BERVEL chat соответствует одному native thread_id, следующие сообщения используют resumeThread. Вопрос передаётся runStreamed без префиксов/JSON/history envelopes/outputSchema. Не добавлять application round/time/token budget. Нативное управление окном/сжатием контекста остаётся у CLI.

Native CLI видит настоящий рабочий корень RW и BERVEL Docker socket, может управлять контейнерами/данными как обычный CLI. Никаких custom tool permission gates/classifier/proposal для его команд. Отдельные пользовательские правила VPN/OSP/основной Git/путей материалов остаются инструкциями агенту. Секреты не печатаются в ответы/вложения; прежний фильтр UI может скрывать значения ПОСЛЕ CLI, но не вырезает пользовательский запрос/инструментальный контекст модели. Среда и реальные полномочия должны быть описаны честно.

## Native engine

Используется уже установленный @openai/codex-sdk0.163.0, который запускает настоящий встроенный CLI. Стабильные startThread/resumeThread/runStreamed достаточны; отдельный самописный MCP/JSON planner/AppServer protocol не нужен для approval-never. Официальные источники прочитаны: https://learn.chatgpt.com/docs/codex-sdk и https://learn.chatgpt.com/docs/app-server. Проверены локальные SDK .d.ts и findCodexPath. Native mode не меняет старый /v1/generate для чата/радара/voice.

Node job API: POST /v1/inspection/jobs {request_id,session_id,actor_id,data_scope,question,api_key}; GET /v1/inspection/jobs/{id}; POST /v1/inspection/jobs/{id}/cancel. Private transport uses existing inspection token; это обычная защита service endpoint, не выдача полномочий модели. Job starts async202, returns state queued/running/completed/failed/cancelled/unknown, thread_id,answer,model,items,exports. Native session mapping and interrupted job markers durable in codex_state; no automatic restart/replay of unknown job after service restart. Actual question/api_key not duplicated into job receipts; CLI stores its own conversation. api_key supplied only as standard app client credential env for export/API, not injected into conversation.

Native profile enables standard CLI shell/file tools, approval never, danger-full-access, network available. Source CWD /workspace/project RW. Native CLI gets Python3.12/runtime libraries, Node24/npm, git,curl and Docker CLI. Existing provider/MOX auth/config/CA and selection remain unchanged. Separate CODEX_HOME profile may isolate native rollouts from legacy generate; stable native thread_id still persisted and resumed.

## BERVEL adaptation

Inspection default workflow becomes NativeInspectionWorkflow; never calls old planner, generate(output_schema), authorization classifier or ConversationContext.select. No fallback to old planner if CLI fails. Current input unmodified. Old BERVEL messages remain in DB; old pre-native sessions may expose their archive via ordinary app API/history helper, not automatic massive prompt injection. New CLI turns/history are native.

Native events drive readable progress/actions; their display can be redacted/limited AFTER CLI (not model input). Standard CLI file change events report actual writes, no confirm controls. Session response identifies engine=codex-cli and native thread ID when known. Public assistant-mode DTO/limits unchanged. Inspection input removes artificial4000char limit; transport may reject physically oversized request explicitly, never silently cut.

Requested generated documents/images can be made with ordinary CLI programs. Standard bervel-export <path> helper uploads to existing ArtifactService, prints canonical download/preview metadata, records confirmed export IDs. Native workflow validates registered artifacts and returns existing file cards. No claim of created attachment without API success. SVG/images/PDF display uses completed frontend implementation.

## Work ownership

- A: codex-runner/native-inspection.mjs + server.mjs native routes; Node tests. No Python/shared source or root Dockerfile changes.
- B: NativeInspectionWorkflow, infrastructure CLI client, shared chat native injection/input/progress/thread metadata as needed, tests/migration0019 ONLY if required. No Node/compose/app_setup/frontend changes.
- C: bervel-export helper and UI native engine/progress/input/legacy-proposal read-only presentation, tests. No other server/Node runtime/config files.
- Root: compose/main Dockerfile full CLI environment, app_setup/settings assembly, execution/deploy coordination, docs, real verification and preservation. Codex service may now be recreated with new image/mounts because user explicitly requested native full CLI; external MOX files and VPN stay unchanged.

## Acceptance

Prove actual native executable start/resume same ID across messages and process restart; question exact passthrough; no outputSchema/planner/classifier/context.select; standard command_execution and file_change events; actual code fixture changed without confirmation; no fixed step/budget; raw requested file available in native tool context; cancellation; no automatic unknown replay. Confirm normal chat/radar/voice behavior unchanged via existing79Node + backend/browser regression appropriate to changes. Native live source read+followup, current Kontur limits via existing KonturClient.limits() (GET /external/v1/limitGroups, not configured-only status), created file/PDF/SVG export and rendering, direct controlled code edit, CLI restart resume. Preserve all prior data including new user sessions; do not clean user query/history. Own QA fixture and artifacts only cleaned after exact checks. Tests and reports inside .dev/local/inspection-engineer-2026-10-30/native-cli.
