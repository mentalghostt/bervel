"""Apply the shared browser-result SQL checks to the dedicated Docker test database."""
import json
import os
from pathlib import Path

from sqlalchemy import create_engine
from frontend_csv_verify import verify_database_results

engine = create_engine(os.environ['BERVEL_TEST_DATABASE_URL'], hide_parameters=True)
if engine.url.database != 'bervel_core_stage31_test':
    raise ValueError('Browser checks require the dedicated test database')
try:
    checks = verify_database_results(engine, Path('/browser-results/live-results.json'))
finally:
    engine.dispose()
report = {'ok': True, 'database': 'bervel_core_stage31_test', 'checks': checks}
Path('/workspace/.dev/local/core-stage-3.1/e2e-sql.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
