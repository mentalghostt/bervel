import argparse
import os
import sys
from sqlalchemy.exc import SQLAlchemyError

from bervel.core.config import Settings
from bervel.core.errors import CoreError


def main() -> int:
    parser = argparse.ArgumentParser(prog='bervel')
    parser.add_argument('command', choices=['migrate', 'serve', 'worker'])
    parser.add_argument('--once', action='store_true', help='Process at most one queued job and exit (worker only)')
    parser.add_argument('--module', choices=['chat','knowledge','inspection','ai_tender_radar','presales'], help='Queue owned by this worker process')
    args = parser.parse_args()
    if args.once and args.command != 'worker':
        parser.error('--once is only supported by worker')
    if (args.command=='worker') != (args.module is not None):
        parser.error('--module is required for worker and only supported by worker')
    try:
        if args.command == 'migrate':
            from bervel.core.infrastructure.database import make_engine, migrate
            engine = make_engine(os.environ['BERVEL_DATABASE_URL'])
            try:
                migrate(engine)
            finally:
                engine.dispose()
            print('Database migrations applied.')
        elif args.command == 'worker':
            from bervel.worker import run_worker
            run_worker(Settings.from_environment(),role=args.module,once=args.once)
        else:
            import uvicorn
            from bervel.app_setup import create_app
            settings = Settings.from_environment()
            uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
        return 0
    except (KeyError, ValueError, CoreError) as error:
        print(error.message if isinstance(error, CoreError) else 'Configuration is invalid; check BERVEL environment variables.', file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print('Database is unavailable or its structure is incompatible; check connection and migrations.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
