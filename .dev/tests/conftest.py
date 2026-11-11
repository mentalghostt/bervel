import os
from uuid import uuid4

import pytest


@pytest.fixture
def pg_engine():
    from sqlalchemy import create_engine, text
    from bervel.core.infrastructure.database import migrate
    url = os.environ.get('BERVEL_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Set BERVEL_TEST_DATABASE_URL to the dedicated PostgreSQL test database')
    admin = create_engine(url)
    assert admin.url.database == 'bervel_core_stage31_test', 'Refuse testing in another database'
    schema = 't_' + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(admin.url.update_query_dict({'options': f'-csearch_path={schema}'}))
    migrate(engine)
    yield engine
    engine.dispose()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    admin.dispose()


@pytest.fixture
def services(pg_engine, tmp_path):
    from bervel.core.models import ActorContext
    from bervel.core.services import ArtifactService
    from bervel.core.infrastructure.repository import PostgresRepository
    from bervel.core.infrastructure.storage import FileObjectStore
    repository = PostgresRepository(pg_engine)
    store = FileObjectStore(tmp_path / 'objects')
    permissions = frozenset({'artifact:write', 'artifact:read', 'dataset:publish', 'dataset:read', 'module:read'})
    context = ActorContext('test-user-a', 'test-area-a', permissions)
    return ArtifactService(repository, store), repository, store, context
