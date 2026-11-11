from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException, Path, Query, UploadFile, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Engine
from starlette.datastructures import Headers, QueryParams

from bervel.core.config import Settings
from bervel.core.api.models import ArtifactRefResponse, ArtifactMetadataResponse, ArtifactListResponse, HealthResponse, ErrorResponse
from bervel.core.api.models import ArtifactUsageBody, ArtifactUsageResponse
from bervel.core.errors import CoreError
from bervel.core.services import ArtifactService
from bervel.core.models import ActorContext, ArtifactRef, RegisterArtifact

STATUS = {'VALIDATION_ERROR': 422, 'FORBIDDEN': 403, 'NOT_FOUND': 404, 'UNSUPPORTED_SCHEMA': 422,
          'INCOMPATIBLE_MODULE': 422, 'STALE_REVISION': 409, 'IDEMPOTENCY_CONFLICT': 409, 'CONFLICT': 409,
          'TRANSIENT_DEPENDENCY_ERROR': 503, 'QUOTA_EXCEEDED': 413,
          'STALE_ATTEMPT': 409, 'PROCESS_ACTIVE': 409, 'WORKFLOW_MISMATCH': 409, 'ENGINE_RESPONSE_UNKNOWN': 409}
STATUS.update({code: 502 for code in ('MODEL_TLS_ERROR', 'MODEL_AUTH_ERROR', 'MODEL_FAILED')})
STATUS.update({'MODEL_UNCONFIGURED': 503, 'MODEL_UNAVAILABLE': 503, 'MODEL_INVALID_OUTPUT': 502,
               'MODEL_BUSY': 429, 'MODEL_RATE_LIMITED': 429, 'MODEL_QUOTA_EXCEEDED': 429, 'MODEL_TIMEOUT': 504,
               'INVALID_SETTINGS': 422, 'SETTINGS_UNAVAILABLE': 503})
STATUS.update({'ARTIFACT_DELETED': 410, 'ARTIFACT_BUSY': 409, 'ARTIFACT_CLEANUP_PENDING': 503})
STATUS.update({'KONTUR_AUTH_ERROR': 502, 'KONTUR_ACCESS_DENIED': 502, 'KONTUR_INVALID_RESPONSE': 502,
               'KONTUR_UNAVAILABLE': 503, 'KONTUR_UNCONFIGURED': 503, 'KONTUR_RATE_LIMIT': 429})
STATUS.update({'SETTINGS_CONFLICT':409,'SIMULATION_REQUIRED':409,'FILE_NOT_READY':409,'VOICE_UNCONFIGURED':503,'VOICE_UNSUPPORTED_AUDIO':415,
               'VOICE_TOO_LARGE':413,'VOICE_AUTH_ERROR':502,'VOICE_BUSY':429,'VOICE_TIMEOUT':504,'VOICE_FAILED':502,
               'VOICE_CANCELLED':499,'INVALID_VOICE_SETTINGS':422,'WEB_SEARCH_UNCONFIGURED':503,'WEB_SEARCH_FAILED':502})


def reject_obsolete_fields(fields):
    if any(key in fields for key in ('revision', 'expected_revision', 'artifact_id')):
        raise CoreError('VALIDATION_ERROR', 'Файл адресуется только по собственному ID; редакции не поддерживаются.')


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


class RequestLimits:
    """Authenticate before multipart parsing; bound non-file request bodies."""
    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        authorization = headers.get('authorization', '').split(' ', 1)
        binding = (self.settings.authenticate(authorization[1])
                   if len(authorization) == 2 and authorization[0].lower() == 'bearer' else None)
        if binding is None:
            response = error_response('UNAUTHORIZED', 'A valid Bearer key is required', 401)
            response.headers['WWW-Authenticate'] = 'Bearer'
            await response(scope, receive, send)
            return
        if scope['path'].startswith('/api/v0.1/artifacts'):
            query = QueryParams(scope.get('query_string', b''))
            if any(key in query for key in ('revision', 'expected_revision')):
                await error_response('VALIDATION_ERROR', 'Редакции файлов не поддерживаются.', 422)(scope, receive, send)
                return
        if scope['path'] == '/api/v0.1/artifacts' and scope['method'] == 'POST':
            if 'artifact:write' not in binding.permissions:
                await error_response('FORBIDDEN', 'Operation is not permitted', 403)(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        if scope['path'] == '/api/v0.1/settings/company' and scope['method'] == 'PUT':
            if 'settings:write' not in binding.permissions:
                await error_response('FORBIDDEN', 'Operation is not permitted', 403)(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        limit = 5 * 1024 * 1024 + 64 * 1024  # JSON/audio transport; originals stream separately.
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


def create_platform_app(settings: Settings, engine: Engine, artifacts: ArtifactService) -> tuple[FastAPI, Callable[..., ActorContext]]:
    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title='BERVEL', version='0.1.0', lifespan=lifespan)
    app.add_middleware(RequestLimits, settings=settings)
    security = HTTPBearer(auto_error=False)

    def actor(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]) -> ActorContext:
        context = settings.authenticate(credentials.credentials) if credentials else None
        if context is None:
            raise HTTPException(401, 'A valid Bearer key is required', headers={'WWW-Authenticate': 'Bearer'})
        return context

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
                connection.execute(text('SELECT 1 FROM artifacts LIMIT 1'))
            return {'status': 'ok'}
        except SQLAlchemyError:
            return error_response('TRANSIENT_DEPENDENCY_ERROR', 'Database is unavailable', 503)

    router = APIRouter(prefix='/api/v0.1', dependencies=[Depends(actor)],
        responses={status: {'model': ErrorResponse} for status in (401, 403, 404, 409, 410, 413, 422, 500, 503)})


    @router.get('/artifacts', tags=['Оригиналы'], response_model=ArtifactListResponse)
    def list_artifacts(context: Annotated[ActorContext, Depends(actor)],
                       cursor: Annotated[int, Query(ge=0)] = 0,
                       limit: Annotated[int, Query(ge=1, le=100)] = 20):
        return artifacts.list(context, cursor, limit)

    @router.delete('/artifacts/{artifact_id}', tags=['Оригиналы'], status_code=204, response_class=Response)
    def delete_artifact(artifact_id: UUID, request: Request,
                        context: Annotated[ActorContext, Depends(actor)]):
        reject_obsolete_fields(request.query_params)
        artifacts.delete(context, artifact_id)
        return Response(status_code=204)

    @router.put('/artifacts/{artifact_id}/usage-role', tags=['Оригиналы'], response_model=ArtifactUsageResponse)
    def set_artifact_usage(artifact_id: UUID, body: ArtifactUsageBody, context: Annotated[ActorContext, Depends(actor)]):
        return artifacts.set_usage_role(context, artifact_id, body.usage_role)

    def receive_original(file: UploadFile, key: str, context: ActorContext, origin: str, external_id: str | None, external_revision: str | None,
                         received_at: str | None, role: str):
        if received_at:
            try:
                received = datetime.fromisoformat(received_at.replace('Z', '+00:00'))
                if received.tzinfo is None:
                    raise ValueError('Timezone required')
            except ValueError as error:
                raise CoreError('VALIDATION_ERROR', 'received_at requires an ISO 8601 date-time and timezone') from error
        ref = artifacts.register(context, RegisterArtifact(file.file, file.filename or '', file.content_type or 'application/octet-stream',
            {'origin_system': origin, 'external_id': external_id, 'external_revision': external_revision, 'received_at': received_at},
            key, role))
        return {'artifact_id': str(ref.artifact_id)}

    @router.post('/artifacts', tags=['Оригиналы'], response_model=ArtifactRefResponse)
    async def upload(request: Request, file: UploadFile, context: Annotated[ActorContext, Depends(actor)],
               command_key: Annotated[str, Form(min_length=1, max_length=128)],
               origin_system: Annotated[str, Form(max_length=2000)] = 'manual-upload',
               external_id: Annotated[str | None, Form(max_length=2000)] = None,
               external_revision: Annotated[str | None, Form(max_length=2000)] = None,
               received_at: Annotated[str | None, Form()] = None,
               source_role: Annotated[str, Form()] = 'test_fixture'):
        reject_obsolete_fields(await request.form())
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(receive_original, file, command_key, context, origin_system, external_id, external_revision, received_at, source_role)

    @router.get('/artifacts/{artifact_id}', tags=['Оригиналы'], response_model=ArtifactMetadataResponse)
    def artifact_metadata(artifact_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        return artifacts.metadata(context, ArtifactRef(artifact_id))

    @router.get('/artifacts/{artifact_id}/content', tags=['Оригиналы'])
    def content(artifact_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        from fastapi.responses import StreamingResponse
        from starlette.background import BackgroundTask
        from bervel.core.originals import CHUNK_BYTES
        metadata, stream = artifacts.open(context, ArtifactRef(artifact_id))
        def body():
            try:
                while chunk := stream.read(CHUNK_BYTES):
                    yield chunk
            finally:
                stream.close()
        return StreamingResponse(body(), media_type='application/octet-stream', background=BackgroundTask(stream.close), headers={
            'Content-Length': str(metadata['size']),
            'Content-Disposition': 'attachment; filename="original.bin"', 'X-Content-Type-Options': 'nosniff'})

    app.include_router(router)
    return app, actor
