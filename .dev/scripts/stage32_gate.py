"""Synthetic integration gate; no credentials or document bodies are recorded."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Lock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

released, mutex = Event(), Lock()
released.set()
events = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with mutex:
            self.respond({'events': list(events)})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        if self.path == '/control':
            if data.get('hold'):
                released.clear()
            else:
                released.set()
            self.respond({'ok': True})
            return
        response = {'ok': True}
        if self.path.startswith('/api/internal/v0.1/processes/'):
            request = Request('http://api:8011' + self.path, data=json.dumps(data).encode(),
                headers={'Authorization': self.headers.get('Authorization', ''), 'Content-Type': 'application/json'})
            try:
                with urlopen(request, timeout=30) as forwarded:
                    response = json.loads(forwarded.read())
            except HTTPError:
                self.send_error(502)
                return
            data['process_run_id'] = self.path.split('/')[-2]
        with mutex:
            events.append({key: data.get(key) for key in ('process_run_id', 'execution_id')})
        released.wait(120)
        self.respond(response)

    def respond(self, value):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        try:
            self.wfile.write(json.dumps(value).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 5699), Handler).serve_forever()
