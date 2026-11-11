"""Structured SELECT over base tables; no SQL strings accepted from a model."""
from sqlalchemy import MetaData, Table, func, inspect, select
from sqlalchemy.engine import Engine

from bervel.core.errors import CoreError
from bervel.modules.inspection.redaction import sensitive, HIDDEN


class InspectionDatabase:
    def __init__(self, engine: Engine, redactor, *, schema: str = 'public'):
        self.engine, self.redactor, self.schema_name = engine, redactor, schema

    def tables(self) -> dict:
        with self.engine.connect().execution_options(postgresql_readonly=True) as connection:
            return {'schema': self.schema_name, 'tables': [{'name': name} for name in sorted(inspect(connection).get_table_names(schema=self.schema_name))]}

    def _table(self, connection, name: str):
        if name not in inspect(connection).get_table_names(schema=self.schema_name):
            raise CoreError('NOT_FOUND', 'Таблица BERVEL не найдена; произвольный SQL недоступен.')
        return Table(name, MetaData(), schema=self.schema_name, autoload_with=connection, resolve_fks=False)

    def schema(self, *, table: str) -> dict:
        with self.engine.connect().execution_options(postgresql_readonly=True) as connection:
            value = self._table(connection, table)
            return {'schema': self.schema_name, 'table': table, 'columns': [
                {'name': c.name, 'type': str(c.type), 'nullable': c.nullable, 'primary_key': c.primary_key,
                 'default': HIDDEN if sensitive(c.name) and c.server_default else str(c.server_default.arg) if c.server_default else None} for c in value.columns]}

    def rows(self, *, table: str, columns: list[str] | None = None, where: dict | None = None, offset: int = 0, limit: int = 20) -> dict:
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
            raise CoreError('VALIDATION_ERROR', 'Неверная страница таблицы.')
        with self.engine.connect().execution_options(postgresql_readonly=True) as connection:
            value = self._table(connection, table)
            names = columns or list(value.columns.keys())
            filters = where or {}
            if not names or any(name not in value.c for name in [*names, *filters]):
                raise CoreError('VALIDATION_ERROR', 'Столбец таблицы не найден.')
            if len(filters) > 10 or any(not isinstance(item, (str, int, float, bool, type(None))) for item in filters.values()):
                raise CoreError('VALIDATION_ERROR', 'Допустимы только точные скалярные фильтры.')
            conditions = [value.c[name] == item for name, item in filters.items()]
            ordering = list(value.primary_key.columns)
            statement = select(*(value.c[name] for name in names)).where(*conditions)
            if ordering: statement = statement.order_by(*ordering)
            rows = [dict(row) for row in connection.execute(statement.offset(offset).limit(limit + 1)).mappings()]
            total = connection.execute(select(func.count()).select_from(value).where(*conditions)).scalar_one()
        from fastapi.encoders import jsonable_encoder
        cleaned = self.redactor.clean(rows[:limit])
        return {'schema': self.schema_name, 'table': table, 'rows': jsonable_encoder(cleaned, custom_encoder={bytes: lambda b: {'binary_bytes': len(b)}}),
            'total': total, 'offset': offset, 'next_offset': offset + limit if len(rows) > limit else None,
            'stable_order': bool(ordering)}
