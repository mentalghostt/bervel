"""Synchronous test facade over the supported HTTPX ASGI transport."""
from contextlib import ExitStack
from functools import partial

from anyio.from_thread import start_blocking_portal
import httpx


class ASGITestClient:
    def __init__(self, app):
        self.app = app

    def __enter__(self):
        self.stack = ExitStack()
        self.portal = self.stack.enter_context(start_blocking_portal())
        self.stack.enter_context(self.portal.wrap_async_context_manager(self.app.router.lifespan_context(self.app)))
        self.client = self.stack.enter_context(self.portal.wrap_async_context_manager(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url='http://testserver')))
        return self

    def get(self, url, **kwargs):
        return self.portal.call(partial(self.client.get, url, **kwargs))

    def post(self, url, **kwargs):
        return self.portal.call(partial(self.client.post, url, **kwargs))

    def delete(self, url, **kwargs):
        return self.portal.call(partial(self.client.delete, url, **kwargs))

    def put(self, url, **kwargs):
        return self.portal.call(partial(self.client.put, url, **kwargs))

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)
