# Самостоятельная проверка этапа 3.1

Рабочий корень: `C:/OSPanel/home/bervel.local`. Python 3.12+, PostgreSQL 17. API слушает `127.0.0.1:8011`. PostgreSQL уже включён пользователем; его модуль, VPN, Redis и n8n помощник не меняет.

## Быстрая проверка

Откройте PowerShell в корне проекта и выполните:

```powershell
Set-Location C:/OSPanel/home/bervel.local
.dev/scripts/core_stage_3_1_local.ps1 -Action verify
```

Команда применяет миграции к отдельной БД приложения, запускает свой API, загружает оба [учебных CSV](../../fixtures/core-stage-3.1/requirements-v1.csv), проверяет повторы, конфликты и доступ, перезапускает только свой процесс, читает сохранённые данные и останавливает API. Порт 8011 должен быть свободен. Каждый прогон создаёт новое учебное дело; прежние данные сохраняются.

Успех: `ok: true`, `values_v1: ["10", null]`, `values_v2: ["10", "4"]`, `restart_persistence: true`. При чужой области — `404`, при неизвестном ключе — `401`, при изменении команды под прежним ключом — `409`. Протокол без секретов: `.dev/local/core-stage-3.1/live-verify.json`.

## Ручной сценарий в браузере

В одном терминале сначала настройте окружение и скопируйте ключ области A в буфер обмена. Команды не записывают секреты в файл и не печатают их:

```powershell
.dev/scripts/core_stage_3_1_local.ps1 -Action configure
($env:BERVEL_ACCESS_KEYS | ConvertFrom-Json)[0].key | Set-Clipboard
.dev/scripts/core_stage_3_1_local.ps1 -Action serve
```

Откройте [интерактивное API](http://127.0.0.1:8011/docs). Нажмите **Authorize**, вставьте ключ из буфера, подтвердите **Authorize**, затем **Close**. В поле вводится сам ключ: интерфейс добавит `Bearer`. Для вызова маршрута раскройте его, нажмите **Try it out**, заполните поля и нажмите **Execute**.

1. `GET /health/ready`: ожидается `200` и `{"status":"ok"}`.
2. `POST /api/v0.1/artifacts`: выберите `C:/OSPanel/home/bervel.local/.dev/fixtures/core-stage-3.1/requirements-v1.csv`; `command_key = manual-upload-v1`, `origin_system = manual-test`, `source_role = test_fixture`. Необязательные внешние ID и дату оставьте пустыми. Сохраните возвращённый `artifact_id` и `revision = 1`.
3. `POST /api/v0.1/demo/document-review/extractions`: вставьте тело ниже, заменив `ARTIFACT_ID` возвращённым UUID. Параметр `dataset_id` при первой обработке отсутствует. Сохраните возвращённый `dataset_id`.

```json
{
  "artifact_id": "ARTIFACT_ID",
  "artifact_revision": 1,
  "command_key": "manual-extract-v1",
  "module_version": "1.0.0",
  "expected_revision": 0
}
```

4. `GET /datasets/{dataset_id}/revisions/1`: ожидается `quality_state = PARTIAL`; в конверте видны версия модуля и схемы, ID обработки и точный снимок входа. `GET .../records` возвращает две строки: количество `"10"` и `UNKNOWN`/`null`. У второй строки есть `MISSING_QUANTITY`, а `evidence_refs` указывает на строку 3 оригинального CSV и нужную колонку. `limit = 1`, затем `cursor = 1` позволяют прочитать строки отдельными страницами; в последней странице `next_cursor = null`.
5. Повторите **точно тот же JSON** из шага 3: ссылка и редакция остаются прежними. Измените `artifact_revision` на 2 под тем же ключом после загрузки второй редакции: ожидается `409 / IDEMPOTENCY_CONFLICT`.
6. `POST /artifacts/{artifact_id}/revisions`: тот же `artifact_id`, файл `requirements-v2.csv`, `expected_revision = 1`, `command_key = manual-upload-v2`, `origin_system = manual-test`. Вернётся редакция 2. Выполните обработку с новым ключом:

```json
{
  "artifact_id": "ARTIFACT_ID",
  "artifact_revision": 2,
  "command_key": "manual-extract-v2",
  "module_version": "1.0.0",
  "dataset_id": "DATASET_ID",
  "expected_revision": 1
}
```

7. `GET /datasets/{dataset_id}/revisions/2/records`: количества `"10"` и `"4"`; конверт второй редакции — `COMPLETE`. Повторное чтение `/revisions/1/records` по-прежнему показывает `null`. `GET /artifacts/{artifact_id}/history` содержит обе редакции оригинала и оба результата. `GET .../revisions/1/content` скачивает прежние байты. Для истории нужны оба права: чтение оригиналов и чтение результатов.
8. Остановите собственный API клавишами **Ctrl+C**. В том же терминале скопируйте ключ области B, затем снова запустите API:

```powershell
($env:BERVEL_ACCESS_KEYS | ConvertFrom-Json)[1].key | Set-Clipboard
.dev/scripts/core_stage_3_1_local.ps1 -Action serve
```

В `/docs` замените авторизацию (**Logout**, затем **Authorize**) ключом B. Чтение прежних метаданных, оригинала, набора, строк и истории даёт `404`; сведения о чужом деле отсутствуют. Любая произвольная строка вместо ключа даёт `401`. В конце верните ключ A и убедитесь, что старые данные сохранились после перезапуска. Ключи каждого терминала живут только в его окружении; новый терминал создаёт новую пару, связанную с теми же учебными субъектами и областями.

При повторении всего ручного сценария используйте новые ключи команд (например, `manual2-upload-v1`), иначе ядро правильно вернёт сохранённую квитанцию прежнего запуска.

## Что находится под капотом

`src/bervel/core` определяет правила и сценарии; учебный модуль разбирает CSV стандартной библиотекой и `Decimal`; локальный реестр проверяет результат JSON Schema Draft 2020-12. HTTP лишь принимает команды и переводит ошибки. Файловый адаптер сохраняет байты по SHA-256 без использования исходного имени как пути. PostgreSQL хранит ссылки, неизменяемые редакции, записи, снимки входа, факт обработки, аудит и квитанции команд.

Повтор команды защищён блокировкой и уникальным ключом внутри области, субъекта и операции. Новая редакция проверяет ожидаемый номер под блокировкой строки. Результат публикуется одной SQL-транзакцией: ошибка отменяет все её части. Файлы и SQL не объявляются одной транзакцией: сохранённый без ссылки объект может остаться для сверки, но через API он недоступен; автоматического удаления нет.

## Проверки кода и пакета

После `configure`:

```powershell
$pythonPath = 'C:/OSPanel/home/bervel.local/.dev/local/core-stage-3.1/venv/Scripts/python.exe'
& $pythonPath -m pytest -c .dev/pytest.ini --rootdir=. -q
& $pythonPath -m pip check
& $pythonPath .dev/scripts/check_project_materials.py
```

PostgreSQL-тесты используют только `bervel_core_stage31_test`, создают отдельную схему для каждого теста и удаляют только её. Без `BERVEL_TEST_DATABASE_URL` эти проверки пропускаются; такой запуск не подтверждает хранение. Ни БД приложения, ни БД n8n тестами не очищаются.

Повтор сборки и проверки поставляемого пакета (порт 8012 свободен):

```powershell
& $pythonPath -m build --no-isolation --outdir .dev/local/core-stage-3.1/dist
$packagePython = 'C:/OSPanel/home/bervel.local/.dev/local/core-stage-3.1/package-venv/Scripts/python.exe'
& $packagePython -m pip install --force-reinstall --no-deps .dev/local/core-stage-3.1/dist/bervel-0.1.0-py3-none-any.whl
& $packagePython -I .dev/scripts/core_stage_3_1_package.py
```

Ожидается `wheel_only`, `source_dev_not_required`, `two_revisions`, `repeat`, `migration_repeat` со значением `true`. Сценарий использует встроенные искусственные байты и HTTP-клиент стандартной библиотеки; само приложение запускается из установленного wheel через `python -I -m bervel` в пустом рабочем каталоге. Скрипт проверки относится к разработке и в пакет не входит.

## Настройка другого окружения

Для приложения необходимы `BERVEL_DATABASE_URL` (профиль `postgresql+psycopg://...`), абсолютный `BERVEL_OBJECT_ROOT` и JSON-массив `BERVEL_ACCESS_KEYS`. Каждый элемент содержит `key`, `actor_id`, `data_scope`, `permissions`. Поддерживаемые права: `module:read`, `artifact:read`, `artifact:write`, `dataset:read`, `dataset:publish`. Приложение не содержит адреса локального PostgreSQL, пути Open Server или обязательной папки `.dev`. `BERVEL_HOST` должен быть loopback; `BERVEL_PORT` по умолчанию 8011.

На этой машине уже созданы отдельные базы `bervel_core_stage31` и `bervel_core_stage31_test`, роль `bervel_core_stage31` без административных полномочий. Подключение использует существующую локальную политику PostgreSQL; она не менялась. Для другой машины администратор создаёт базы и учётные данные и передаёт строку подключения через окружение. Помощник не устанавливает Python, не создаёт роли, не включает модули и не переписывает политику БД.

Если локальное Python-окружение удалено, создайте его установленным Python 3.12+; сначала задайте `TEMP`, `TMP` и `PIP_CACHE_DIR` каталогами внутри `.dev/local/core-stage-3.1`, затем:

```powershell
python -m venv .dev/local/core-stage-3.1/venv
& .dev/local/core-stage-3.1/venv/Scripts/python.exe -m pip install -r .dev/requirements-core-stage-3.1.lock
& .dev/local/core-stage-3.1/venv/Scripts/python.exe -m pip install --no-build-isolation -e .
python -m venv .dev/local/core-stage-3.1/package-venv
& .dev/local/core-stage-3.1/package-venv/Scripts/python.exe -m pip install .dev/local/core-stage-3.1/dist/bervel-0.1.0-py3-none-any.whl
```

Это учебный локальный API. OCR, модели, реальные документы, действующие остатки, промышленный вход, долговременные процессы, n8n, согласования и второй модуль в этот этап не входят. Полный запуск на отдельной площадке без Open Server, восстановление резервных копий и промышленная нагрузка остаются будущими проверками. [Отчёт и покрытие CV01–CV20](../quality/core-stage-3.1-report.md).
