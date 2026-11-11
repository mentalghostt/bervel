"""Run the module's Python handlers using the durable PostgreSQL queue."""
import signal
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event
from typing import Literal

from sqlalchemy.exc import SQLAlchemyError

from bervel.ai_tender_radar_setup import build_ai_tender_radar_worker
from bervel.core.config import Settings
from bervel.core.errors import CoreError
from bervel.presales_setup import build_presales_worker


def build_services(settings: Settings, *, embedding_token_budget: int):
    from bervel.app_setup import build_services as build_application_services
    return build_application_services(settings, embedding_token_budget=embedding_token_budget)


def _run_radar_lanes(handler, stop: Event) -> None:
    def poll(lane):
        while not stop.is_set():
            try:
                profiles = getattr(getattr(handler, 'run_pipeline', None), 'profiles', None)
                if lane == 'analysis' and profiles is not None:
                    profiles.enqueue_due()
                processed = handler.run_once(lane=lane)
            except (CoreError, SQLAlchemyError):
                print(f'AI radar {lane} storage is unavailable; polling will resume.', file=sys.stderr, flush=True)
                stop.wait(3)
                continue
            if not processed:
                stop.wait(1)

    # Both lanes drain before the caller disposes their shared connection pool.
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='radar-lane') as workers:
        futures = [workers.submit(poll, lane) for lane in ('analysis', 'question')]
        try:
            while not stop.is_set():
                finished, _ = wait(futures, timeout=.1, return_when=FIRST_COMPLETED)
                for future in finished:
                    future.result()
        finally:
            stop.set()
        for future in futures:
            future.result()


def run_worker(settings: Settings, *, role: Literal['chat','knowledge','inspection','ai_tender_radar','presales'], once: bool = False) -> None:
    budgets={'chat':8000,'knowledge':8000,'inspection':8000}
    if role == 'ai_tender_radar':
        engine, handler = build_ai_tender_radar_worker(settings)
    elif role == 'presales':
        engine, handler = build_presales_worker(settings)
    elif role in budgets:
        services = build_services(settings,embedding_token_budget=budgets[role])
        engine = services.engine
        handler = getattr(services,role)
    else:
        raise ValueError('Worker requires chat, knowledge, inspection, ai_tender_radar or presales role')
    stop = Event()
    previous = {name: signal.signal(name, lambda *_: stop.set()) for name in (signal.SIGINT, signal.SIGTERM)}
    try:
        if role == 'ai_tender_radar' and not once:
            _run_radar_lanes(handler, stop)
            return
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
