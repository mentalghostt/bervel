"""Run the module's Python handlers using the durable PostgreSQL queue."""
import signal
import sys
from threading import Event
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from bervel.core.config import Settings
from bervel.core.errors import CoreError
from bervel.app_setup import build_services


def run_worker(settings: Settings, *, role: Literal['chat','knowledge','radar','inspection'], once: bool = False) -> None:
    budgets={'chat':8000,'knowledge':24000,'radar':8000,'inspection':8000}
    if role not in budgets:
        raise ValueError('Worker requires chat, knowledge, radar or inspection role')
    services = build_services(settings,embedding_token_budget=budgets[role])
    engine = services.engine
    handler = getattr(services,role)
    stop = Event()
    previous = {name: signal.signal(name, lambda *_: stop.set()) for name in (signal.SIGINT, signal.SIGTERM)}
    try:
        while not stop.is_set():
            if role == 'knowledge':
                try:
                    services.artifacts.cleanup_deleted()
                except (CoreError, SQLAlchemyError):
                    # A failed unlink must not stop preparation of unrelated files.
                    print('Original cleanup is pending; it will be retried.', file=sys.stderr, flush=True)
            try:
                processed = handler.run_once()
            except (CoreError, SQLAlchemyError):
                if once:
                    raise
                print('Background storage is unavailable; polling will resume.', file=sys.stderr, flush=True)
                stop.wait(3)
                continue
            if once:
                return
            if not processed:
                stop.wait(1)
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)
        engine.dispose()
