"""Throwaway measurement tool. Its deduplication is NOT an n8n guarantee."""
import argparse
import json
import sqlite3
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class ObserverStore:
    def __init__(self, path: Path):
        self.path = path
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run_id TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY, run_id TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS effects (
                    key TEXT PRIMARY KEY, run_id TEXT, payload TEXT);
            """)

    def record(self, payload: dict) -> int:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO events(run_id, payload) VALUES (?, ?)",
                (payload["run_id"], json.dumps(payload, sort_keys=True)),
            )
            return cursor.lastrowid

    def publish(self, payload: dict) -> dict:
        encoded = json.dumps(payload, sort_keys=True)
        conflict = False
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO attempts(run_id, payload) VALUES (?, ?)",
                (payload["run_id"], encoded),
            )
            previous = connection.execute(
                "SELECT payload FROM effects WHERE key = ?", (payload["key"],)
            ).fetchone()
            if previous:
                conflict = previous[0] != encoded
            else:
                connection.execute(
                    "INSERT INTO effects(key, run_id, payload) VALUES (?, ?, ?)",
                    (payload["key"], payload["run_id"], encoded),
                )
        if conflict:
            raise ValueError("Idempotency key reused with different payload")
        return {"created": previous is None}

    def snapshot(self, run_id: str) -> dict:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            return {
                table: [json.loads(row[0]) for row in connection.execute(
                    f"SELECT payload FROM {table} WHERE run_id = ?", (run_id,)
                )]
                for table in ("events", "attempts", "effects")
            }


def serve(store: ObserverStore, host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # An experiment deliberately kills a caller before its reply.

        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path == "/state":
                self.reply(200, store.snapshot(query["run_id"][0]))
            else:
                self.reply(200, {"measurement_tool": "ready"})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/record":
                self.reply(200, {"event_id": store.record(payload)})
            elif self.path == "/effect":
                hold_seconds = payload.pop("hold_seconds", 0)
                try:
                    result = store.publish(payload)
                except ValueError as error:
                    self.reply(409, {"error": str(error)})
                    return
                if hold_seconds:
                    time.sleep(hold_seconds)
                self.reply(200, result)
            else:
                self.reply(404, {"error": "Unknown measurement endpoint"})

    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5689)
    args = parser.parse_args()
    args.database.parent.mkdir(parents=True, exist_ok=True)
    serve(ObserverStore(args.database), args.host, args.port)
