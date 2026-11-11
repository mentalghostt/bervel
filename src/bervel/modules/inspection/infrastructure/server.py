"""Private diagnostic process. Only this service owns the Docker socket."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
import secrets
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, text

from bervel.core.errors import CoreError
from bervel.modules.inspection import InspectionReader
from bervel.modules.inspection.infrastructure.database import InspectionDatabase
from bervel.modules.inspection.infrastructure.docker import DockerReads, InspectionDocker
from bervel.modules.inspection.infrastructure.files import InspectionFiles
from bervel.modules.inspection.redaction import SecretRedactor


def build_reader():
    redactor = SecretRedactor()
    redactor.observe(dict(os.environ))
    engine = create_engine(os.environ['BERVEL_INSPECTION_DATABASE_URL'], connect_args={'options': '-cstatement_timeout=10000'}, pool_pre_ping=True)
    # Misconfiguration must fail closed, even though callers cannot supply SQL.
    with engine.connect().execution_options(postgresql_readonly=True) as connection:
        dangerous = connection.execute(text("SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one()
        writable = connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND (has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') OR c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)))")).scalar_one()
        if dangerous or writable: raise ValueError('Inspection database role must only read')
    files = InspectionFiles({name: Path('/sources') / name for name in ('project', 'objects', 'codex', 'embeddings', 'postgres')}, redactor)
    docker = InspectionDocker(DockerReads(), redactor)
    # Inventory credentials before reading logs that might echo an unlabelled token.
    for item in docker.list()['containers']:
        raw = docker.transport.get('/containers/' + item['id'] + '/json')
        redactor.observe(raw)
        if raw.get('Config', {}).get('Labels', {}).get('com.docker.compose.service') == 'codex':
            for name in ('/data/codex/provider-settings.json', '/data/codex/voice-settings.json', '/run/mox/auth.json', '/run/secrets/model-credentials'):
                try: docker.files(container=item['id'], path=name)
                except CoreError as error:
                    if error.code != 'NOT_FOUND': raise
    return InspectionReader(files=files, database=InspectionDatabase(engine, redactor), docker=docker, redactor=redactor), engine


class ReadBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: str = Field(max_length=64)
    arguments: dict


class RedactBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: Any


def create_reader_app(reader, engine, token: str) -> FastAPI:
    if len(token) < 32: raise ValueError('Private inspection token is required')
    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()
    app = FastAPI(title='BERVEL inspection reader', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    def check(authorization):
        if not authorization or not secrets.compare_digest(authorization, 'Bearer ' + token): raise HTTPException(401)
    @app.exception_handler(CoreError)
    async def failure(request, error):
        status = {'FORBIDDEN': 403, 'NOT_FOUND': 404, 'QUOTA_EXCEEDED': 413, 'VALIDATION_ERROR': 422}.get(error.code, 503)
        return JSONResponse({'error': {'code': error.code, 'message': 'Источник не прочитан.'}}, status_code=status)
    @app.exception_handler(Exception)
    async def unexpected(request, error):
        return JSONResponse({'error': {'code': 'INSPECTION_UNAVAILABLE', 'message': 'Источник не прочитан.'}}, status_code=503)
    @app.get('/health/live')
    def health(): return {'status': 'ok'}
    @app.post('/read')
    def read(body: ReadBody, authorization: Annotated[str | None, Header()] = None):
        check(authorization)
        return reader.read(body.operation, body.arguments)
    @app.post('/redact')
    def redact(body: RedactBody, authorization: Annotated[str | None, Header()] = None):
        check(authorization)
        return {'value': reader.redactor.clean(body.value)}
    return app


if __name__ == '__main__':
    import uvicorn
    instance, database_engine = build_reader()
    uvicorn.run(create_reader_app(instance, database_engine, os.environ['BERVEL_INSPECTION_TOKEN']), host='0.0.0.0', port=8091, access_log=False)
