"""The large profile response must be fully encoded off the ASGI event loop."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

from bervel.core.models import ActorContext
from bervel.modules.ai_tender_radar import profile_api


def app_with_payload(payload):
    service = SimpleNamespace(read=lambda actor, source: payload, list=lambda actor: {'profiles': []})
    app = FastAPI()
    app.include_router(profile_api.profiles_router(service, lambda: ActorContext('reader', 'private', frozenset())))
    @app.get('/probe')
    async def probe():
        return {'ok': True}
    return app


def test_profile_read_preserves_wire_types_and_uses_the_prepared_response(monkeypatch):
    payload = {'id': UUID('61e2b011-d82f-4cdb-bef0-c584bb56890b'),
               'when': datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc),
               'price': Decimal('123.45'), 'name': 'Тендер', 'cards': [{'score': 0}, {'score': 100}]}
    calls = []
    def encode(value):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            calls.append('worker')
        else:
            calls.append('event-loop')
        return jsonable_encoder(value)
    monkeypatch.setattr(profile_api, 'jsonable_encoder', encode, raising=False)
    with TestClient(app_with_payload(payload)) as client:
        response = client.get('/api/v0.1/ai-tender-radar/profiles/test_api')
    assert response.status_code == 200
    assert response.json() == jsonable_encoder(payload)
    assert calls == ['worker']
    assert response.headers['content-type'] == 'application/json'
    assert payload['price'] == Decimal('123.45')


def test_profile_encoding_does_not_prevent_another_request_from_finishing(monkeypatch):
    entered, release, finished = Event(), Event(), Event()
    def encode(value):
        entered.set()
        try:
            assert release.wait(3), 'Encoder occupied the event loop; probe could not release it'
            return jsonable_encoder(value)
        finally:
            finished.set()
    monkeypatch.setattr(profile_api, 'jsonable_encoder', encode, raising=False)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_with_payload({'cards': []})),
                                     base_url='http://test') as client:
            profile = asyncio.create_task(client.get('/api/v0.1/ai-tender-radar/profiles/test_api'))
            try:
                assert await asyncio.to_thread(entered.wait, 1), 'Response was not prepared inside the endpoint'
                assert not finished.is_set()
                response = await client.get('/probe')
                assert response.json() == {'ok': True}
                assert not finished.is_set(), 'Probe waited for profile serialization'
            finally:
                release.set()
                await profile
    asyncio.run(scenario())
