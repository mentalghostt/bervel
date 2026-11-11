import os
import subprocess
import sys


def test_migration_connection_failure_has_safe_cli_error(tmp_path):
    env = os.environ.copy()
    env['BERVEL_DATABASE_URL'] = 'postgresql+psycopg://missing@127.0.0.1:1/absent?connect_timeout=1'
    result = subprocess.run([sys.executable, '-m', 'bervel', 'migrate'], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert 'Traceback' not in result.stderr
    assert '127.0.0.1' not in result.stderr
