"""Platform-owned catalog and versioned derived document indexes."""
from sqlalchemy import DateTime, and_, cast, select
from sqlalchemy.engine import Engine
from uuid import UUID
from bervel.core.infrastructure.database import artifacts, document_indexes

INDEX_VERSION = 5

class PostgresDocumentRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def catalog(self, connection, scope: str) -> list[dict]:
        statement = (select(artifacts.c.metadata)
            .where(artifacts.c.data_scope == scope, artifacts.c.deleted_at.is_(None))
            .order_by(cast(artifacts.c.metadata['created_at'].astext, DateTime(timezone=True)).desc(), artifacts.c.artifact_id.desc()))
        return [{key: item[key] for key in ('artifact_id', 'filename', 'created_at', 'media_type', 'size')}
                for item in connection.execute(statement).scalars()]

    def document_index(self, snapshot: dict) -> dict | None:
        with self.engine.connect() as connection:
            return connection.execute(select(document_indexes.c.result).where(document_indexes.c.artifact_id == UUID(snapshot['artifact_id']),
                document_indexes.c.version == INDEX_VERSION)).scalar_one_or_none()

    def save_document_index(self, snapshot: dict, result: dict) -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        with self.engine.begin() as connection:
            connection.execute(pg_insert(document_indexes).values(artifact_id=UUID(snapshot['artifact_id']),
                version=INDEX_VERSION, result=result).on_conflict_do_update(index_elements=['artifact_id'], set_={'version': INDEX_VERSION, 'result': result}))
