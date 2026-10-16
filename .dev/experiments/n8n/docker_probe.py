"""Synthetic Docker measurements, not application recovery or delivery guarantees.

Run from the repository root with Python 3.12+ and Docker Compose on PATH:
  python .dev/experiments/n8n/docker_probe.py
Only n8n-main/n8n-worker in project bervel are restarted or killed. PostgreSQL,
the API, frontend, Open Server Panel and VPN are never stopped by this tool.
The authenticated editor REST API is an experimental, version-specific contract.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from workflows import build

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / ".dev/local/docker/orchestration"
ENGINE_URL = "http://127.0.0.1:5688"
OBSERVER_URL = "http://127.0.0.1:5689"
COOKIES = http.cookiejar.CookieJar()
CLIENT = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIES))
WORKFLOW_ID = "bvd" + uuid.uuid4().hex[:12]
RESULTS = {"engine": "n8n", "expected_engine_version": "2.43.5",
           "profile": "docker_queue_manual_recovery", "synthetic_data_only": True,
           "product_implementation": False, "workflow_id": WORKFLOW_ID,
           "effect_deduplication_owner": "external observer SQLite unique key; NOT n8n",
           "checks": []}


def compose(*arguments: str, timeout: int = 300) -> str:
    command = [ARGS.docker, "compose", "--project-name", "bervel",
               "-f", "compose.yaml", "-f", "compose.orchestration.yaml",
               "-f", ".dev/docker/compose.orchestration-test.yaml",
               "--profile", "orchestration", *arguments]
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    with (RUNTIME / "compose.log").open("a", encoding="utf-8") as log:
        # Commands contain no secret values; do not log rendered Compose configuration.
        log.write("\n" + " ".join(arguments) + "\n" + process.stdout + process.stderr)
    if process.returncode:
        raise RuntimeError("Compose command failed; see orchestration/compose.log: " + arguments[0])
    return process.stdout.strip()


def http(url: str, payload=None, authenticated: bool = False):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    open_request = CLIENT.open if authenticated else urllib.request.urlopen
    with open_request(request, timeout=90) as response:
        body = response.read()
        return response.status, json.loads(body) if body else None


def api(path: str, payload=None):
    return http(ENGINE_URL + "/rest/" + path, payload, authenticated=True)[1]["data"]


def wait_for(condition, seconds: int = 120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = condition()
            if value:
                return value
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.3)
    raise TimeoutError("Expected measurement not reached within " + str(seconds) + " seconds")


def login():
    password_file = ARGS.owner_password_file
    fresh = not password_file.exists()
    if fresh:
        settings = http(ENGINE_URL + "/rest/settings")[1]["data"]
        if not settings.get("userManagement", {}).get("showSetupOnFirstLoad"):
            raise RuntimeError("Existing n8n owner requires --owner-password-file; owner was not changed")
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_file.write_text("SPIKE_OWNER_PASSWORD=" + secrets.token_urlsafe(24) + "Aa1!", encoding="utf-8")
    password = password_file.read_text(encoding="utf-8").strip().split("=", 1)[1]
    if fresh:
        api("owner/setup", {"email": ARGS.owner_email, "firstName": "Docker",
                            "lastName": "Measurement", "password": password})
    else:
        api("login", {"emailOrLdapLoginId": ARGS.owner_email, "password": password})


def sql_value(sql: str) -> str:
    return compose("exec", "-T", "postgres", "sh", "-ec",
                   'PGPASSWORD="$N8N_DB_PASSWORD" exec psql -h 127.0.0.1 -U bervel_n8n -d bervel_n8n -At -c "$1"',
                   "sh", sql)


def import_versions():
    for version in ("1", "2"):
        workflow = build(version, observer_url="http://observer:5689")
        workflow.update(id=WORKFLOW_ID, name="Docker synthetic " + WORKFLOW_ID + " v" + version)
        workflow["nodes"][0]["parameters"]["path"] += "-" + WORKFLOW_ID
        workflow["nodes"][0]["webhookId"] += "-" + WORKFLOW_ID
        workflow["nodes"][4]["webhookId"] += "-" + WORKFLOW_ID
        path = RUNTIME / (WORKFLOW_ID + "-v" + version + ".json")
        path.write_text(json.dumps([workflow], indent=2), encoding="utf-8")
        compose("exec", "-T", "n8n-main", "n8n", "import:workflow", "--input=/probe/" + path.name)


def activate(version: str):
    # Only our generated alphanumeric ID is interpolated; no supplied SQL fragments.
    rows = json.loads(sql_value('''SELECT json_agg(json_build_object(
        'version_id', h."versionId", 'body', n->'parameters'->>'jsonBody'))
        FROM workflow_history h, json_array_elements(h.nodes) n
        WHERE h."workflowId"=''' + "'" + WORKFLOW_ID + "'" + ''' AND n->>'name'='Completed';'''))
    expected = build(version, observer_url="http://observer:5689")["nodes"][-1]["parameters"]["jsonBody"]
    version_id = next(row["version_id"] for row in rows if row["body"] == expected)
    api("workflows/" + WORKFLOW_ID + "/activate", {"versionId": version_id})
    wait_for(lambda: sql_value('SELECT "publishedVersionId" FROM workflow_published_version WHERE "workflowId"=' +
                               "'" + WORKFLOW_ID + "';") == version_id)
    return version_id


def state(run_id: str) -> dict:
    return http(OBSERVER_URL + "/state?" + urllib.parse.urlencode({"run_id": run_id}))[1]


def events(run_id: str, phase: str) -> list:
    return [event for event in state(run_id)["events"] if event.get("phase") == phase]


def begin(hold_seconds: int = 0, run_id: str | None = None) -> str:
    run_id = run_id or "docker-" + uuid.uuid4().hex
    # Do not retry this mutating command after an ambiguous transport failure.
    status, _ = http(ENGINE_URL + "/webhook/bervel-core-stage-2-" + WORKFLOW_ID,
                     {"run_id": run_id, "revision": "1", "hold_seconds": hold_seconds})
    assert status == 200
    return run_id


def resume(waiting: dict):
    parsed = urllib.parse.urlparse(waiting["resume_url"])
    assert parsed.hostname == "127.0.0.1" and parsed.port == 5688, "Unexpected resume host"
    # Observer records its event immediately before the Wait node. The engine,
    # rather than that earlier event, determines when the resume can be sent.
    wait_for(lambda: execution(waiting["execution_id"])["status"] == "waiting")
    assert http(waiting["resume_url"], {"decision": "approved", "synthetic": True})[0] == 200


def execution(execution_id: str) -> dict:
    data = api("executions/" + execution_id)
    return {key: data.get(key) for key in ("id", "status", "finished", "retryOf", "retrySuccessId", "workflowId")}


def engine_ready():
    wait_for(lambda: http(ENGINE_URL + "/healthz/readiness")[0] == 200)
    compose("up", "-d", "--no-deps", "--wait", "n8n-main", "n8n-worker")


def waiting_restart():
    activate("1")
    run_id = begin()
    waiting = wait_for(lambda: events(run_id, "waiting"))[0]
    compose("restart", "n8n-main", "n8n-worker")
    engine_ready()
    login()
    assert not events(run_id, "completed"), "Restart advanced the waiting execution"
    resume(waiting)
    completed = wait_for(lambda: events(run_id, "completed"))
    assert len(completed) == 1 and completed[0]["execution_id"] == waiting["execution_id"]
    return {"run_id": run_id, "state": state(run_id), "execution": execution(waiting["execution_id"])}


def duplicate_start():
    activate("1")
    run_id = begin()
    first = wait_for(lambda: events(run_id, "waiting"))[0]
    resume(first)
    wait_for(lambda: events(run_id, "completed"))
    begin(run_id=run_id)
    waiting = wait_for(lambda: events(run_id, "waiting") if len(events(run_id, "waiting")) == 2 else None)
    resume(waiting[1])
    wait_for(lambda: len(events(run_id, "completed")) == 2)
    snapshot = state(run_id)
    assert len(snapshot["effects"]) == 1 and len(snapshot["attempts"]) == 2
    return {"run_id": run_id, "state": snapshot, "engine_deduplicated_start": len(events(run_id, "started")) == 1}


def manual_original_version():
    activate("1")
    run_id = begin(hold_seconds=45)
    wait_for(lambda: state(run_id)["effects"])
    parent_id = events(run_id, "started")[0]["execution_id"]
    compose("kill", "--signal", "SIGKILL", "n8n-worker")
    compose("up", "-d", "--no-deps", "--wait", "n8n-worker")
    wait_for(lambda: execution(parent_id)["status"] == "crashed")
    baseline = {"execution": execution(parent_id), "state": state(run_id)}
    assert not events(run_id, "waiting"), "Crash automatically advanced; manual recovery baseline differs"
    published = activate("2")
    response = api("executions/" + parent_id + "/retry", {"loadWorkflow": False})
    child_id = response["id"]
    waiting = wait_for(lambda: [event for event in events(run_id, "waiting") if event["execution_id"] == child_id])[0]
    resume(waiting)
    wait_for(lambda: execution(child_id)["status"] == "success")
    snapshot = state(run_id)
    assert execution(child_id)["retryOf"] == parent_id
    assert [event["version"] for event in events(run_id, "completed")] == ["1"]
    assert len(snapshot["attempts"]) == 2 and len(snapshot["effects"]) == 1
    return {"run_id": run_id, "baseline": baseline, "child": execution(child_id),
            "published_v2": published, "state": snapshot, "automatic_recovery": False}


def version_change():
    activate("1")
    old_run = begin()
    old_waiting = wait_for(lambda: events(old_run, "waiting"))[0]
    published = activate("2")
    resume(old_waiting)
    old_completed = wait_for(lambda: events(old_run, "completed"))
    new_run = begin()
    new_waiting = wait_for(lambda: events(new_run, "waiting"))[0]
    resume(new_waiting)
    new_completed = wait_for(lambda: events(new_run, "completed"))
    assert old_completed[0]["version"] == "1" and new_completed[0]["version"] == "2"
    return {"old_run": old_run, "new_run": new_run, "published_v2": published,
            "old_state": state(old_run), "new_state": state(new_run)}


def save():
    (RUNTIME / "results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")


def check(name: str, operation):
    print("Running " + name, flush=True)
    started = time.monotonic()
    try:
        details = operation()
        result = {"id": name, "status": "pass", "details": details}
    except (AssertionError, TimeoutError, RuntimeError, urllib.error.URLError) as error:
        result = {"id": name, "status": "fail", "error": str(error) or type(error).__name__}
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    RESULTS["checks"].append(result)
    save()
    print(name + ": " + result["status"], flush=True)


def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    RESULTS["tested_on"] = time.strftime("%Y-%m-%d")
    workflow_imported = False
    try:
        compose("config", "--quiet")
        # Do not recreate PostgreSQL or other parent services as dependencies.
        compose("up", "-d", "--no-deps", "--wait", "redis", "observer")
        compose("up", "-d", "--no-deps", "--wait", "n8n-main")
        compose("up", "-d", "--no-deps", "--wait", "n8n-worker")
        version = compose("exec", "-T", "n8n-main", "n8n", "--version")
        RESULTS["actual_engine_version"] = version
        RESULTS["actual_redis_version"] = compose("exec", "-T", "redis", "redis-server", "--version")
        assert version == "2.43.5", "Unexpected n8n version"
        login()
        import_versions()
        workflow_imported = True
        check("DKOR01_wait_survives_main_worker_restart", waiting_restart)
        check("DKOR02_external_observer_deduplicates_effect", duplicate_start)
        check("DKOR03_manual_retry_uses_original_version", manual_original_version)
        check("DKOR04_old_and_new_versions", version_change)
        RESULTS["status"] = "pass" if all(item["status"] == "pass" for item in RESULTS["checks"]) else "fail"
    except (AssertionError, TimeoutError, RuntimeError, urllib.error.URLError) as error:
        RESULTS.update(status="blocked", error=str(error) or type(error).__name__)
    finally:
        try:
            save()
            (RUNTIME / "services.log").write_text(
                compose("logs", "--no-color", "--tail", "250", "n8n-main", "n8n-worker", "redis", "observer"),
                encoding="utf-8",
            )
        finally:
            if workflow_imported:
                try:
                    api("workflows/" + WORKFLOW_ID + "/deactivate", {})
                    RESULTS["cleanup"] = {"workflow_deactivated": True, "execution_history_preserved": True}
                except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError) as error:
                    RESULTS["cleanup"] = {"workflow_deactivated": False, "error": str(error)}
                    RESULTS["status"] = "fail"
                save()
    return 0 if RESULTS["status"] == "pass" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default=os.environ.get("BERVEL_DOCKER", "docker"))
    historical_owner = ROOT / ".dev/local/core-stage-2/owner.env"
    parser.add_argument("--owner-password-file", type=Path,
                        default=historical_owner if historical_owner.exists() else RUNTIME / "owner.env")
    parser.add_argument("--owner-email", default="core-spike@example.invalid")
    ARGS = parser.parse_args()
    raise SystemExit(main())
