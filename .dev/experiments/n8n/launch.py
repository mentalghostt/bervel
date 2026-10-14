"""Launch only the experimental n8n process; never start Open Server modules."""
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parent
RUNTIME = EXPERIMENT.parents[1] / "local" / "core-stage-2"


def environment(role: str = "cli") -> dict:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    key_file = RUNTIME / "encryption.env"
    if not key_file.exists():
        key_file.write_text("N8N_ENCRYPTION_KEY=" + secrets.token_hex(32), encoding="utf-8")
    key = key_file.read_text(encoding="utf-8").split("=", 1)[1].strip()
    env = os.environ.copy()
    env.update({
        "N8N_ENCRYPTION_KEY": key,
        "N8N_USER_FOLDER": str(RUNTIME / "n8n"),
        "DB_TYPE": "postgresdb",
        "DB_POSTGRESDB_HOST": os.environ["SPIKE_PG_HOST"],
        "DB_POSTGRESDB_PORT": os.environ.get("SPIKE_PG_PORT", "5432"),
        "DB_POSTGRESDB_DATABASE": "bervel_core_spike_n8n",
        "DB_POSTGRESDB_USER": "bervel_n8n_spike",
        "DB_POSTGRESDB_PASSWORD": "",
        "EXECUTIONS_MODE": "queue",
        "QUEUE_BULL_REDIS_HOST": os.environ["SPIKE_REDIS_HOST"],
        "QUEUE_BULL_REDIS_PORT": os.environ.get("SPIKE_REDIS_PORT", "6379"),
        "QUEUE_BULL_REDIS_DB": "15",
        "QUEUE_BULL_PREFIX": "bervel-core-stage-2-bull",
        "N8N_REDIS_KEY_PREFIX": "bervel-core-stage-2",
        "QUEUE_WORKER_LOCK_DURATION": "30000",
        "QUEUE_WORKER_LOCK_RENEW_TIME": "5000",
        "QUEUE_WORKER_STALLED_INTERVAL": "5000",
        "N8N_PORT": "5688",
        "N8N_HOST": "127.0.0.1",
        "N8N_LISTEN_ADDRESS": "127.0.0.1",
        "N8N_PROTOCOL": "http",
        "N8N_WEBHOOK_URL": "http://127.0.0.1:5688/",
        "N8N_SECURE_COOKIE": "false",
        "N8N_DIAGNOSTICS_ENABLED": "false",
        "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
        "N8N_PERSONALIZATION_ENABLED": "false",
        "OFFLOAD_MANUAL_EXECUTIONS_TO_WORKERS": "true",
        "EXECUTIONS_DATA_SAVE_ON_ERROR": "all",
        "EXECUTIONS_DATA_SAVE_ON_SUCCESS": "all",
        "N8N_GRACEFUL_SHUTDOWN_TIMEOUT": "2",
        "N8N_EVENTBUS_LOGWRITER_LOGBASE": str(RUNTIME / (role + "-events")),
        "N8N_RUNNERS_BROKER_PORT": "5680" if role == "worker" else "5679",
    })
    return env


def command(*arguments: str) -> list[str]:
    node = os.environ.get("SPIKE_NODE_PATH") or shutil.which("node")
    if not node:
        raise RuntimeError("Set SPIKE_NODE_PATH to a supported Node.js executable")
    return [node, str(EXPERIMENT / "node_modules" / "n8n" / "bin" / "n8n"), *arguments]


if __name__ == "__main__":
    raise SystemExit(subprocess.call(command(*sys.argv[1:]), env=environment()))
