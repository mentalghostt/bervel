import argparse
import os
import sys
from sqlalchemy.exc import SQLAlchemyError

from bervel.config import Settings
from bervel.core.errors import CoreError


def main() -> int:
    parser = argparse.ArgumentParser(prog='bervel')
    parser.add_argument('command', choices=['migrate', 'serve'])
    args = parser.parse_args()
    try:
        if args.command == 'migrate':
            from bervel.infrastructure.database import make_engine, migrate
            engine = make_engine(os.environ['BERVEL_DATABASE_URL'])
            try:
                migrate(engine)
            finally:
                engine.dispose()
            print('Database migrations applied.')
        else:
            import uvicorn
            from bervel.api import create_app
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
