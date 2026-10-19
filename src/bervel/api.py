from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Path, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import Headers

from bervel.config import Settings
from bervel.api_models import (ArtifactRefResponse, ArtifactMetadataResponse, DatasetRefResponse,
                               DatasetResponse, RecordsResponse, HistoryResponse, HealthResponse, ErrorResponse, ExtractionBody)
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRevisionRef, DatasetRevisionRef, ExtractArtifact, RegisterArtifact
from bervel.core.registry import default_registry
from bervel.core.services import ArtifactService, ExtractionService, authorize
from bervel.infrastructure.database import make_engine
from bervel.infrastructure.repository import PostgresRepository
from bervel.infrastructure.storage import FileObjectStore

MAX_FILE_BYTES = 5 * 1024 * 1024
STATUS = {'VALIDATION_ERROR': 422, 'FORBIDDEN': 403, 'NOT_FOUND': 404, 'UNSUPPORTED_SCHEMA': 422,
          'INCOMPATIBLE_MODULE': 422, 'STALE_REVISION': 409, 'IDEMPOTENCY_CONFLICT': 409,
          'TRANSIENT_DEPENDENCY_ERROR': 503, 'QUOTA_EXCEEDED': 413,
          'STALE_ATTEMPT': 409, 'PROCESS_ACTIVE': 409, 'WORKFLOW_MISMATCH': 409, 'ENGINE_RESPONSE_UNKNOWN': 409}


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


class RequestLimits:
    """Reject unauthenticated and excessive bodies before multipart parsing."""
    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        authorization = headers.get('authorization', '').split(' ', 1)
        internal = scope['path'].startswith('/api/internal/')
        valid = (len(authorization) == 2 and authorization[0].lower() == 'bearer' and
                 (self.settings.authenticate_worker(authorization[1]) if internal else self.settings.authenticate(authorization[1]) is not None))
        if not valid:
            response = error_response('UNAUTHORIZED', 'A valid Bearer key is required', 401)
            response.headers['WWW-Authenticate'] = 'Bearer'
            await response(scope, receive, send)
            return
        limit = 16 * 1024 if internal else MAX_FILE_BYTES + 64 * 1024
        body = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                return
            chunk = event.get('body', b'')
            if len(body) + len(chunk) > limit:
                await error_response('QUOTA_EXCEEDED', 'Request exceeds upload limit', 413)(scope, receive, send)
                return
            body.extend(chunk)
            if not event.get('more_body', False):
                break
        delivered = False
        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        await self.app(scope, buffered_receive, send)


def create_app(settings: Settings, *, workflow_engine=None) -> FastAPI:
    engine = make_engine(settings.database_url)
    registry = default_registry()
    repository = PostgresRepository(engine)
    repository.register_schemas(registry.schemas.definitions())
    artifacts = ArtifactService(repository, FileObjectStore(settings.object_root))
    extraction = ExtractionService(artifacts, repository, registry)
    if workflow_engine is None and settings.n8n is not None and settings.n8n.workflow_id:
        from bervel.infrastructure.n8n import N8nEngine
        config = settings.n8n
        workflow_engine = N8nEngine(config.base_url, config.api_key, config.webhook_key,
            config.workflow_id, config.webhook_path, config.timeout)
    process_service = None
    if workflow_engine is not None:
        from bervel.core.processes import ProcessService
        from bervel.infrastructure.process_repository import PostgresProcessRepository
        process_service = ProcessService(PostgresProcessRepository(engine), extraction, workflow_engine)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title='BERVEL Core — учебный CSV', version='0.1.0', lifespan=lifespan)
    app.add_middleware(RequestLimits, settings=settings)
    security = HTTPBearer(auto_error=False)

    def actor(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]) -> ActorContext:
        context = settings.authenticate(credentials.credentials) if credentials else None
        if context is None:
            raise HTTPException(401, 'A valid Bearer key is required', headers={'WWW-Authenticate': 'Bearer'})
        return context

    def worker(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]):
        if credentials is None or not settings.authenticate_worker(credentials.credentials):
            raise HTTPException(401, 'A valid worker Bearer key is required', headers={'WWW-Authenticate': 'Bearer'})

    @app.exception_handler(CoreError)
    async def core_error(request, error: CoreError):
        return error_response(error.code, error.message, STATUS.get(error.code, 500))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return error_response('VALIDATION_ERROR', 'Check required fields, types and supported values', 422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        return error_response('INTERNAL_ERROR', 'Operation failed', 500)

    @app.get('/health/live', tags=['Здоровье'], response_model=HealthResponse)
    def live():
        return {'status': 'ok'}

    @app.get('/health/ready', tags=['Здоровье'], response_model=HealthResponse)
    def ready():
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1 FROM schema_definitions LIMIT 1'))
            return {'status': 'ok'}
        except SQLAlchemyError:
            return error_response('TRANSIENT_DEPENDENCY_ERROR', 'Database is unavailable', 503)

    router = APIRouter(prefix='/api/v0.1', dependencies=[Depends(actor)],
        responses={status: {'model': ErrorResponse} for status in (401, 403, 404, 409, 413, 422, 500, 503)})

    @router.get('/modules', tags=['Модули'])
    def modules(context: Annotated[ActorContext, Depends(actor)]):
        authorize(context, 'module:read')
        return registry.list()

    def receive_original(file: UploadFile, key: str, context: ActorContext, artifact_id: UUID | None,
                         expected: int, origin: str, external_id: str | None, external_revision: str | None,
                         received_at: str | None, role: str):
        if received_at:
            try:
                received = datetime.fromisoformat(received_at.replace('Z', '+00:00'))
                if received.tzinfo is None:
                    raise ValueError('Timezone required')
            except ValueError as error:
                raise CoreError('VALIDATION_ERROR', 'received_at requires an ISO 8601 date-time and timezone') from error
        content = file.file.read(MAX_FILE_BYTES + 1)
        ref = artifacts.register(context, RegisterArtifact(content, file.filename or '', file.content_type or 'application/octet-stream',
            {'origin_system': origin, 'external_id': external_id, 'external_revision': external_revision, 'received_at': received_at},
            key, artifact_id, expected, role))
        return {'artifact_id': str(ref.artifact_id), 'revision': ref.revision}

    @router.post('/artifacts', tags=['Оригиналы'], response_model=ArtifactRefResponse)
    def upload(file: UploadFile, context: Annotated[ActorContext, Depends(actor)],
               command_key: Annotated[str, Form(min_length=1, max_length=128)],
               origin_system: Annotated[str, Form(max_length=2000)] = 'manual-upload',
               external_id: Annotated[str | None, Form(max_length=2000)] = None,
               external_revision: Annotated[str | None, Form(max_length=2000)] = None,
               received_at: Annotated[str | None, Form()] = None,
               source_role: Annotated[str, Form()] = 'test_fixture'):
        return receive_original(file, command_key, context, None, 0, origin_system, external_id, external_revision, received_at, source_role)

    @router.post('/artifacts/{artifact_id}/revisions', tags=['Оригиналы'], response_model=ArtifactRefResponse)
    def upload_revision(artifact_id: UUID, file: UploadFile, context: Annotated[ActorContext, Depends(actor)],
               command_key: Annotated[str, Form(min_length=1, max_length=128)], expected_revision: Annotated[int, Form(ge=1)],
               origin_system: Annotated[str, Form(max_length=2000)] = 'manual-upload',
               external_id: Annotated[str | None, Form(max_length=2000)] = None,
               external_revision: Annotated[str | None, Form(max_length=2000)] = None,
               received_at: Annotated[str | None, Form()] = None,
               source_role: Annotated[str, Form()] = 'test_fixture'):
        return receive_original(file, command_key, context, artifact_id, expected_revision, origin_system, external_id, external_revision, received_at, source_role)

    @router.get('/artifacts/{artifact_id}/revisions/{revision}', tags=['Оригиналы'], response_model=ArtifactMetadataResponse)
    def artifact_metadata(artifact_id: UUID, revision: Annotated[int, Path(ge=1)], context: Annotated[ActorContext, Depends(actor)]):
        return artifacts.metadata(context, ArtifactRevisionRef(artifact_id, revision))

    @router.get('/artifacts/{artifact_id}/revisions/{revision}/content', tags=['Оригиналы'])
    def content(artifact_id: UUID, revision: Annotated[int, Path(ge=1)], context: Annotated[ActorContext, Depends(actor)]):
        original = artifacts.read(context, ArtifactRevisionRef(artifact_id, revision))
        return Response(original.content, media_type='application/octet-stream', headers={
            'Content-Disposition': 'attachment; filename="original.bin"', 'X-Content-Type-Options': 'nosniff'})

    @router.get('/artifacts/{artifact_id}/history', tags=['Оригиналы'], response_model=HistoryResponse)
    def history(artifact_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        return artifacts.history(context, artifact_id)

    @router.post('/demo/document-review/extractions', tags=['Извлечение'], response_model=DatasetRefResponse)
    def extract(body: ExtractionBody, context: Annotated[ActorContext, Depends(actor)]):
        ref = extraction.extract(context, ExtractArtifact(ArtifactRevisionRef(body.artifact_id, body.artifact_revision),
            body.command_key, body.module_version, body.dataset_id, body.expected_revision))
        return {'dataset_id': str(ref.dataset_id), 'revision': ref.revision}

    @router.get('/datasets/{dataset_id}/revisions/{revision}', tags=['Результаты'], response_model=DatasetResponse)
    def dataset(dataset_id: UUID, revision: Annotated[int, Path(ge=1)], context: Annotated[ActorContext, Depends(actor)]):
        return extraction.read(context, DatasetRevisionRef(dataset_id, revision))

    @router.get('/datasets/{dataset_id}/revisions/{revision}/records', tags=['Результаты'], response_model=RecordsResponse)
    def records(dataset_id: UUID, revision: Annotated[int, Path(ge=1)], context: Annotated[ActorContext, Depends(actor)],
                cursor: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 100):
        return extraction.records(context, DatasetRevisionRef(dataset_id, revision), cursor, limit)

    app.include_router(router)
    from bervel.process_api import process_routers
    for process_router in process_routers(process_service, actor, worker):
        app.include_router(process_router)
    return app
