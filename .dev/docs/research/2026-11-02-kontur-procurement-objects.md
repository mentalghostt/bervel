# Объекты закупки в Контур API

Проверено 2 ноября 2026 по сохранённой официальной OpenAPI-схеме от 29 октября 2026. Новые авторизованные запросы к API не выполнялись. [Официальная документация](https://developer.kontur.ru/doc/zakupki.external_api?about=0) при открытии инструментом чтения требует JavaScript; актуальность схемы после даты её сохранения отдельно не подтверждена.

## Какие данные предусмотрены

`GET /external/v1/purchases/{id}` возвращает `ExternalApiPurchaseModel` с массивом `Lots`. Объекты находятся в `Lots[].Customers[].Objects[]`, а совместные объекты — в `Lots[].JointObjects[]`. Оба массива используют `ExternalApiPurchaseObjectModel`. Источник: [сохранённая официальная схема](../../local/kontur-api-2026-10-29/openapi.json), модели `ExternalApiPurchaseModel`, `ExternalApiLotModel`, `ExternalApiOrganizationModel`.

У объекта предусмотрены `Name`, `Okpd2s[]` с `Code` и `Name`, `Quantity`, `Okei`, `ItemPrice`, `PositionPrice`, `Characteristics[]`. Обратите внимание: имя поля классификатора объекта — именно `Okpd2s`, а не `Okpd2`. Источник: та же схема, модели `ExternalApiPurchaseObjectModel`, `ApiReferenceModel`.

| Колонка в карточке | Поле API внутри объекта |
|---|---|
| Наименование характеристики | `Characteristics[].Name` |
| Значение характеристики | `Characteristics[].Values[].Value` |
| Единица измерения характеристики | `Characteristics[].Values[].Okei` |
| Инструкция по заполнению | `Characteristics[].FillingInstruction` |

Источник сопоставления: та же схема, модели `CharacteristicsModel` и `CharacteristicValuesModel`. `Okei` непосредственно у объекта означает единицу количества товара; `Okei` внутри `Values` — единицу характеристики.

`POST /external/v1/search` возвращает список кратких карточек `SearchResultItem` (либо карточек с фрагментами совпадений), без массивов объектов и полного набора их характеристик. Для состава требуется отдельный запрос полной карточки по `Id`. Источник: методы `/external/v1/search`, `/external/v1/purchases/{id}` и модели `SearchResultItemPagingModel`, `SearchResultItem`, `SearchResultItemWithSnippets` в той же схеме.

## Ограничения

Наличие поля в схеме не гарантирует заполнение во всякой закупке: указанные массивы и поля допускают `null`. Официальное описание различает тестовый ключ с закрашенными данными и боевой ключ с доступом согласно тарифу; `BlurredApiSchema` описывает скрытое значение, например `#####`. Поэтому полный состав и читаемость характеристик проверяют по конкретному ответу API. Источник: `info.description`, `BlurredApiSchema` и определения перечисленных моделей в сохранённой схеме.

Эта проверка подтверждает контракт API. Фактические значения закупки `0360200048626000130` следует сверять с её сохранённым либо новым ответом; видимая веб-карточка сама по себе не доказывает состав ответа используемого ключа.
